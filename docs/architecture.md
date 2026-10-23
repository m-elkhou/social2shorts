# Architecture

social2shorts is a small hexagonal application. A pipeline core works on domain models and
talks to the outside world only through ports. Adapters implement those ports for yt-dlp,
the phone, the inbox folder, faster-whisper, Gemini, the montage engine and YouTube. State
lives in one SQLite file.

## Modules

| Module | Responsibility |
|---|---|
| `cli.py` | Typer commands. Thin wrappers that load config, build adapters and print reports. |
| `config.py` | `Settings`: TOML file plus `S2S_*` environment overrides, validated by pydantic. |
| `models.py` | Domain models and enums: `Item`, `ItemStatus`, `CatalogEntry`, `ShortMeta`, `Upload`... |
| `ports.py` | `typing.Protocol` interfaces the core needs. |
| `pipeline.py` | The run loop, the two source strategies and the shared `process_item`. |
| `operations.py` | Operator actions: approve, reject, publish, status summary. |
| `db.py` | `Store`, the SQLite repository, with versioned migrations. |
| `curate.py` | Eligibility filters, engagement score, cross-platform dedupe. |
| `match.py` | Matches a post to its phone original (duration + pHash), caches originals. |
| `phash.py` | Perceptual hashes of sampled frames (PyAV + imagehash), Hamming distance. |
| `media.py` | `LocalMedia` adapter: ffprobe duration and pHashes. |
| `renderer.py` | `MontageRenderer` adapter: calls the montage engine lazily. |
| `sources/tiktok_instagram.py` | `SocialCatalog` adapter: yt-dlp discovery and 540p reference download. |
| `sources/phone.py` | `PhoneOriginals` adapter and `MtpPhoneSource` (Windows COM). |
| `sources/inbox.py` | Inbox scanner: video files keyed by sha256. |
| `ai/transcribe.py` | `WhisperTranscriber` adapter: word-level faster-whisper. |
| `ai/metadata.py` | `GeminiLLM` adapter, prompt, `humanize()`, sanitizing, fallback metadata. |
| `publish/youtube.py` | `YouTubeClient` adapter: OAuth, resumable upload with retries, privacy. |
| `publish/contentid.py` | Post-upload rights signals from `videos.list`. |
| `publish/quota.py` | `QuotaBudget`: units and uploads per Pacific day. |
| `publish/scheduler.py` | Next free `publishAt` slot. |
| `montage/` | The montage engine: probe, analyze, plan, render. See [AI montage engine](montage.md). |

## Ports and adapters

```mermaid
classDiagram
    direction LR
    class CatalogSource {
        <<Protocol>>
        discover() list~CatalogEntry~
        download_reference(url, dest_dir) Path
    }
    class OriginalsSource {
        <<Protocol>>
        sync(dest_dir) list~Path~
    }
    class MediaProbe {
        <<Protocol>>
        duration(path) float
        phashes(path, n_frames) list~str~
    }
    class Transcriber {
        <<Protocol>>
        transcribe(path) list~Word~
    }
    class LLM {
        <<Protocol>>
        complete_json(prompt) str
    }
    class Renderer {
        <<Protocol>>
        render(src, dst, words, settings, hook) Path
    }
    class VideoPlatform {
        <<Protocol>>
        upload(path, meta, privacy, publish_at) UploadResult
        set_privacy(video_id, privacy)
        check_claims(video_ids) list~Claim~
    }
    class PipelineDeps {
        catalog
        originals
        media
        transcriber
        llm
        renderer
        platform
        clock
        default(settings)$
    }
    PipelineDeps --> CatalogSource
    PipelineDeps --> OriginalsSource
    PipelineDeps --> MediaProbe
    PipelineDeps --> Transcriber
    PipelineDeps --> LLM
    PipelineDeps --> Renderer
    PipelineDeps --> VideoPlatform
    CatalogSource <|.. SocialCatalog
    OriginalsSource <|.. PhoneOriginals
    MediaProbe <|.. LocalMedia
    Transcriber <|.. WhisperTranscriber
    LLM <|.. GeminiLLM
    Renderer <|.. MontageRenderer
    VideoPlatform <|.. YouTubeClient
```

`PipelineDeps.default(settings)` builds the production adapters. Tests build `PipelineDeps`
from the fakes in `tests/fakes.py`. When `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) is not set,
`llm` is `None` and metadata falls back to captions.

Inside the core, a `SourceStrategy` per `SourceKind` decides how items are found and
prepared:

| Strategy | Kind | Montage mode | refresh | candidates | prepare |
|---|---|---|---|---|---|
| `CatalogStrategy` | `catalog` | `polish` | yt-dlp discovery | approved first, then the curated shortlist | audio gate, reference pHashes, match to an original |
| `RawStrategy` | `raw` | `highlight` | inbox scan | approved first, then inbox order | the inbox file itself |

## Item lifecycle

Every video is an `Item` with an `ItemStatus` (`models.py`). These are the transitions the
code makes:

```mermaid
stateDiagram-v2
    [*] --> discovered: discover or inbox scan
    discovered --> pending_original: no phone original yet
    pending_original --> pending_original: retried every run
    discovered --> review_audio: licensed music
    discovered --> ambiguous: several originals match
    pending_original --> ambiguous: several originals match
    discovered --> duplicate: fingerprint already uploaded
    discovered --> rendered: dry run
    discovered --> uploaded: upload OK
    pending_original --> uploaded: original found, upload OK
    rendered --> uploaded: next real run
    discovered --> failed: error, attempt counted
    failed --> uploaded: retry OK
    failed --> failed: third failure, queued for review
    review_audio --> ready: review approve
    ambiguous --> ready: review approve with original
    failed --> ready: review approve
    ready --> uploaded: processed first
    ready --> failed: third failure
    review_audio --> rejected: review reject
    ambiguous --> rejected: review reject
    failed --> rejected: review reject
    uploaded --> claimed: Content-ID guard
    claimed --> uploaded: publish public or unlisted
    claimed --> rejected: review reject
    duplicate --> [*]
    rejected --> [*]
```

Rules behind the diagram (`pipeline._eligible`):

- A run picks up items in `discovered`, `pending_original`, `matched`, `ready`, `failed` with
  fewer than 3 attempts, and `rendered` (real runs only).
- `matched` exists in the enum and is accepted as open, but the pipeline stores match results
  in the `matches` table and never sets it on an item.
- An error on a `ready` item keeps it `ready` until the third failure.
- `reject` works on any known item, and `publish` sets `uploaded` on any item whose privacy is
  changed to public or unlisted.

## One item, step by step

```mermaid
sequenceDiagram
    autonumber
    participant P as process_item
    participant DB as Store
    participant S as Strategy
    participant T as Transcriber
    participant L as LLM
    participant R as Renderer
    participant Q as QuotaBudget
    participant YT as VideoPlatform
    P->>DB: is_uploaded(fingerprint)?
    alt already uploaded
        DB-->>P: yes, mark duplicate, skip
    end
    P->>S: prepare(item)
    alt licensed, ambiguous or no original
        S->>DB: status + review row (or pending_original)
        S-->>P: None, stop here
    end
    S-->>P: source file
    P->>T: transcribe(source)
    T-->>P: words with timings
    P->>L: complete_json(prompt)
    L-->>P: title, description, tags, hook
    P->>R: render(source, words, montage settings, hook)
    R-->>P: work/renders/key.mp4
    alt dry run
        P->>DB: status rendered
    else real run
        P->>YT: upload(video, meta, privacy, publish_at)
        YT-->>P: video id, applied privacy
        P->>DB: record_upload (ledger, right away)
        P->>Q: spend(upload_cost_units)
        P->>DB: status uploaded
        P->>YT: check_claims([id])
        opt flagged
            P->>YT: set_privacy(private)
            P->>DB: status claimed + claim review
        end
    end
```

Before the loop, a real run re-checks uploads from the last 3 days that are not private (or
are scheduled). Claims can appear hours after upload.

Errors:

- `AuthRefreshError` stops the run and records an alert (exit code 2).
- `QuotaExceededError` and `ScheduleFull` stop the run with a reason.
- Anything else counts a failed attempt for that item and the loop continues.
- A failed upload still spends `upload_cost_units`: YouTube may have charged it.

## Database

One SQLite file (`db_path`). The schema version is `PRAGMA user_version`; `Store.init_schema()`
applies the pending functions of `db.MIGRATIONS`, one transaction each. Version 2 is current.
There are no declared foreign keys; the relations below are logical.

```mermaid
erDiagram
    ITEMS {
        TEXT key PK "post id or raw-sha prefix"
        TEXT kind "catalog or raw"
        TEXT fingerprint "dedupe key"
        TEXT status "ItemStatus"
        TEXT source_path "file to render"
        INTEGER attempts
        TEXT updated
    }
    CATALOG {
        TEXT source_id PK
        TEXT platform
        TEXT url
        TEXT caption
        REAL duration
        INTEGER view_count
        INTEGER like_count
        INTEGER comment_count
        TEXT upload_date
        TEXT track
        TEXT artist
        TEXT raw "yt-dlp JSON"
    }
    MATCHES {
        TEXT source_id PK
        TEXT original_path
        REAL confidence
        TEXT status
        TEXT candidates "JSON list"
    }
    REVIEW_QUEUE {
        INTEGER id PK
        TEXT source_id UK
        TEXT reason
        TEXT created
        TEXT kind "audio ambiguous error claim"
    }
    UPLOADS {
        TEXT youtube_id PK
        TEXT item_key
        TEXT fingerprint UK
        TEXT privacy
        TEXT publish_at
        TEXT uploaded_at
        INTEGER quota_units
    }
    QUOTA_USAGE {
        TEXT day PK "Pacific date"
        INTEGER units
        INTEGER uploads
    }
    REF_PHASHES {
        TEXT key PK
        TEXT phashes "JSON list"
    }
    ORIGINALS {
        TEXT path PK
        INTEGER size
        REAL duration
        TEXT phashes "JSON list"
    }
    FILE_HASHES {
        TEXT path PK
        INTEGER size
        INTEGER mtime_ns
        TEXT sha256
    }
    ALERTS {
        INTEGER id PK
        TEXT message
        TEXT created
        INTEGER resolved
    }
    ITEMS ||--o| CATALOG : "catalog items"
    ITEMS ||--o| MATCHES : "match result"
    ITEMS ||--o| REVIEW_QUEUE : "waiting for a human"
    ITEMS ||--o{ UPLOADS : "ledger"
    ITEMS ||--o| REF_PHASHES : "reference cache"
    MATCHES }o--o| ORIGINALS : "original_path"
```

| Table | Role |
|---|---|
| `items` | One row per video, whatever the source. |
| `catalog` | Post metadata from yt-dlp, joined to `items` on `key = source_id`. |
| `matches` | Last match result per post, with candidate paths for ambiguous ones. |
| `review_queue` | One row per item waiting for a decision (latest reason wins). |
| `uploads` | The anti-duplicate ledger. `fingerprint` is unique. |
| `quota_usage` | Units and `videos.insert` calls per Pacific day. |
| `originals`, `ref_phashes`, `file_hashes` | Caches: original fingerprints, reference pHashes, inbox sha256. |
| `alerts` | Operator alerts shown by `status` until `auth` resolves them. |

To change the schema, append a new `_migrate_vN(conn)` function to `MIGRATIONS`. Never edit
an existing one.

## Design patterns, and why

| Pattern | Where | Why |
|---|---|---|
| Ports and adapters | `ports.py`, `sources/`, `ai/`, `publish/`, `renderer.py` | The core is tested with fakes, no network or ffmpeg. Swapping yt-dlp or Gemini is local. |
| Strategy | `CatalogStrategy`, `RawStrategy`, `STRATEGIES` | Both paths share one processor; only "find and prepare" differs. |
| Repository | `db.Store` | All SQL in one place, one short-lived connection per operation. |
| Dependency injection | `PipelineDeps` with a `default()` factory, injectable `clock` and `sleep` | Deterministic tests for time, retries and quota days. |
| Write-ahead ledger | `_upload` records the upload before anything else | A crash after upload never causes a second upload. |
| Versioned migrations | `db.MIGRATIONS` + `PRAGMA user_version` | Old databases upgrade in place, atomically per step. |
| Retry with backoff and jitter | `publish.youtube.run_resumable` | Survives 5xx and dropped connections during long uploads. |
| Graceful fallback | `generate_metadata` | A Gemini outage never blocks an upload. |
| Lazy initialization | `YouTubeClient.service`, `functools.cache` on models, `Run.originals` | Heavy clients load only when needed; dry runs never touch OAuth. |
| Functional core | `montage/planner.py`, `montage/filters.py`, `montage/captions.py` | Pure, deterministic functions: same input, same plan and ffmpeg command. Easy to test. |
| Registry dispatch | `REFRAMES`, `CAPTION_STYLES` | Add a framing or caption style without touching callers. |
