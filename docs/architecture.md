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
| `lock.py` | Single-instance lock (a PID file in `work_dir`) so two runs never overlap. |
| `operations.py` | Operator actions: approve, reject, publish, status summary. |
| `db.py` | `Store`, the SQLite repository, with versioned migrations. |
| `curate.py` | Eligibility filters, engagement score, cross-platform dedupe. |
| `match.py` | Matches a post to its phone original (duration + pHash), caches originals. |
| `phash.py` | Perceptual hashes of sampled frames (PyAV + imagehash), Hamming distance. |
| `media.py` | `LocalMedia` adapter (ffprobe duration, pHashes) and the cached sha256 of source files. |
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

| Strategy | Kind | refresh | candidates | prepare returns |
|---|---|---|---|---|
| `CatalogStrategy` | `catalog` | yt-dlp discovery | approved first, then the curated shortlist | the matched phone original, mode `polish` (after the audio gate and matching) |
| `RawStrategy` | `raw` | inbox scan | approved first, then inbox order | the inbox file: mode `highlight` if longer than `[montage] target_duration_s`, else `polish`. Clips under `min_duration_s` go to review. |

`prepare` returns a `Source(path, mode)`, or `None` after routing the item to review or
`pending_original`.

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
    discovered --> too_short: raw clip under min_duration_s
    discovered --> not_video: photo slideshow post
    discovered --> duplicate: item or source file already uploaded
    discovered --> rendered: dry run
    rendered --> uploading: next real run
    discovered --> uploading: upload call starts
    pending_original --> uploading: original found
    uploading --> uploaded: YouTube accepted it
    uploading --> failed: run crashed mid-upload, queued as interrupted
    discovered --> failed: error, attempt counted
    failed --> uploading: retry, fewer than 3 attempts
    failed --> failed: third failure, queued for review
    review_audio --> ready: review approve
    ambiguous --> ready: review approve with original
    too_short --> ready: review approve
    failed --> ready: review approve
    ready --> uploading: processed first
    ready --> failed: third failure
    review_audio --> rejected: review reject
    ambiguous --> rejected: review reject
    too_short --> rejected: review reject
    failed --> rejected: review reject
    uploaded --> claimed: Content-ID guard
    claimed --> uploaded: publish with include-claimed
    claimed --> rejected: review reject
    duplicate --> [*]
    not_video --> [*]
    rejected --> [*]
```

Rules behind the diagram (`pipeline._eligible`):

- A run picks up items in `discovered`, `pending_original`, `matched`, `ready`, `failed` with
  fewer than 3 attempts, and `rendered` (real runs only).
- `uploading` is set just before the `videos.insert` call. If the call fails it goes back to
  the previous status. If the process dies during the call, the next run finds it still
  `uploading`, marks it `failed` with no retries left and queues an `interrupted` review: the
  video may or may not be on YouTube, so a human checks Studio.
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
    P->>DB: item already uploaded? (key, raw sha256, legacy fingerprint)
    alt already uploaded
        DB-->>P: yes, mark duplicate, skip
    end
    P->>S: prepare(item)
    alt licensed, ambiguous, too short or no original
        S->>DB: status + review row (or pending_original)
        S-->>P: None, stop here
    end
    S-->>P: Source(path, mode)
    P->>DB: sha256 of the source file uploaded already?
    alt same file already on YouTube
        DB-->>P: yes, mark duplicate, skip
    end
    P->>T: transcribe(path)
    T-->>P: words with timings
    P->>L: complete_json(prompt)
    L-->>P: title, description, tags, hook
    P->>R: render(path, words, settings with mode, hook)
    R-->>P: work/renders/key.mp4 + words in the Short's time
    P->>P: save work/subtitles/key.srt
    alt dry run
        P->>DB: status rendered
    else real run
        P->>DB: status uploading
        P->>YT: upload(video, meta, privacy, publish_at)
        YT-->>P: video id, applied privacy
        P->>DB: record_upload (ledger, right away), status uploaded
        P->>Q: spend(upload_cost_units)
        opt upload_captions and a subtitle language
            P->>YT: add_captions(id, srt, language, name)
            P->>Q: spend(400)
        end
        P->>YT: check_claims([id])
        opt flagged
            P->>YT: set_privacy(private)
            P->>DB: status claimed + claim review
        end
    end
```

Before the loop, a run takes the lock, recovers items left `uploading` by a crashed run, and
(real runs only) re-checks uploads from the last 3 days that are not private or are scheduled.
Claims can appear hours after upload.

Errors:

- `AuthRefreshError` stops the run and records an alert (exit code 2).
- `QuotaExceededError` and `ScheduleFull` stop the run with a reason. YouTube's daily quota
  (403) and the channel's upload limit (`uploadLimitExceeded`, 400) both raise
  `QuotaExceededError`; no item is charged an attempt. The stop reason says which one it was:
  the upload limit does not show in the API quota.
- The upload limit (about 10 uploads) counts the last 24 hours, not a Pacific day: on
  2026-11-01 it still refused uploads 4 and 8.5 hours after the previous batch, across a quota
  reset. So a refusal does not mark the quota day spent; it is stored in `upload_limit`, and
  later runs stop before any render or Gemini call until every upload of the 24 hours before
  the refusal is 24 hours old (`upload_limit_until`). `status` shows when that is.
- `LLMQuotaExceeded` (Gemini's per-day 429; the free tier allows 20 requests per model per
  day) stops the run too, rather than publish videos with template titles and uncorrected
  subtitles. Temporary Gemini errors (408, 500, 502, 503 "high demand", 504) are retried for
  about a minute first; after that, and for any other Gemini error, the item falls back
  (template metadata, speech-model draft).
- A catalog post whose reference has no video stream (a TikTok photo slideshow comes down as
  audio) becomes `not_video` and is never retried.
- Anything else is an item failure, with one exception: if **3 items in a row fail with the
  same error** (`SYSTEMIC_FAILURES`), it is treated as a system fault (no network, a model
  download failed...). The run stops, records an alert, and no item is charged an attempt.
  Otherwise each failure counts one attempt for its item and the loop continues.
- A failed upload call still spends `upload_cost_units`: YouTube may have charged it.
- Once YouTube accepted a video, nothing is retried. If the ledger write itself fails, the run
  logs a critical message and records an alert with the video link. If adding the subtitle
  track fails, the run records an alert that names the saved SRT to upload in YouTube Studio.
- A second run started while one holds `work_dir/social2shorts.lock` does nothing and reports
  "another run (pid N) has been working for M min". A lock older than 6 hours is treated as
  stale and replaced.

## Database

One SQLite file (`db_path`). The schema version is `PRAGMA user_version`; `Store.init_schema()`
applies the pending functions of `db.MIGRATIONS`, one transaction each. Version 3 is current:
it added `uploads.content_sha256` (the real dedupe key), stored timestamps as ISO UTC, and
mapped legacy Content-ID reviews to claims.
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
        TEXT fingerprint "caption hint or raw sha256"
        TEXT content_sha256 "source file sha256"
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
| `uploads` | The anti-duplicate ledger. Checked by item key, by source-file sha256, and by caption fingerprint for pre-v3 rows. |
| `quota_usage` | Units and `videos.insert` calls per Pacific day. |
| `upload_limit` | When YouTube last refused an upload with `uploadLimitExceeded` (one row). |
| `originals`, `ref_phashes`, `file_hashes` | Caches: original fingerprints, reference pHashes, sha256 of source files (by path, size and mtime). |
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
| Write-ahead ledger | `uploading` status before the call, ledger row right after it | A crash after upload never causes a second upload; a crash during it goes to a human. |
| Single-instance lock | `lock.single_instance` | A manual run and a scheduled run never upload at the same time. |
| Circuit breaker | `FailureStreak` | A system-wide fault stops the run instead of burning every item's retries. |
| Versioned migrations | `db.MIGRATIONS` + `PRAGMA user_version` | Old databases upgrade in place, atomically per step. |
| Retry with backoff and jitter | `publish.youtube.run_resumable` | Survives 5xx and dropped connections during long uploads. |
| Graceful fallback | `generate_metadata` | A Gemini outage never blocks an upload. |
| Lazy initialization | `YouTubeClient.service`, `functools.cache` on models, `Run.originals` | Heavy clients load only when needed; dry runs never touch OAuth. |
| Functional core | `montage/planner.py`, `montage/filters.py`, `montage/captions.py` | Pure, deterministic functions: same input, same plan and ffmpeg command. Easy to test. |
| Registry dispatch | `REFRAMES`, `CAPTION_STYLES` | Add a framing or caption style without touching callers. |
