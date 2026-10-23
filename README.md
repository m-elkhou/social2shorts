# social2shorts

Turns the moto videos of **@motoride.mh** into high-quality, monetization-safe YouTube Shorts on
**@motoride-mh**. It works on two kinds of input:

- **Catalog**: the best TikTok/Instagram posts, re-rendered from the QHD phone original.
- **Raw**: footage that was never published, dropped into a local `inbox/` folder and cut into a
  Short by the AI montage engine (`social2shorts.montage`).

Uploads are spread over days to fit the YouTube API quota, never duplicated, and checked for
rights problems after upload.

## How it works

```
catalog: discover (yt-dlp) -> curate -> audio gate -> match phone original (duration + pHash) ┐
raw:     scan inbox (sha256 dedupe) ─────────────────────────────────────────────────────────┤
                                                                                             v
          transcribe (faster-whisper, word timings) -> metadata (Gemini, humanized) -> montage
          -> quota/schedule check -> upload (resumable, retried) -> ledger -> Content-ID guard
```

Both paths share one item processor (`pipeline.process_item`); a small strategy per source kind
decides how an item is found and prepared. External systems sit behind `typing.Protocol` ports
(`ports.py`) with adapters in `sources/`, `ai/` and `publish/`; tests use fakes.

| Package / module | Responsibility |
|---|---|
| `sources/` | yt-dlp catalog, phone (MTP) originals cache, inbox scanner |
| `ai/` | whisper transcription, metadata writer + `humanize()` |
| `publish/` | YouTube client, Content-ID check, quota budget, publish scheduler |
| `pipeline.py` | run loop and the shared item processor |
| `db.py` | SQLite repository with versioned migrations |
| `operations.py` | review / publish / status actions behind the CLI |

## Setup

Prerequisites: Python 3.12, [uv](https://docs.astral.sh/uv/), ffmpeg on PATH. For the catalog
path, a Windows PC with the phone connected in "File transfer" mode.

```bash
uv sync
cp config.example.toml config.toml        # then edit it
export GEMINI_API_KEY=...                  # optional: without it titles come from captions
uv run social2shorts auth                  # once: links the YouTube channel
```

Every setting can be overridden with an environment variable: `S2S_DB_PATH`,
`S2S_YOUTUBE__LANGUAGE=fr`, `S2S_SCHEDULE__MAX_PER_DAY=1`...

## Daily use

```bash
uv run social2shorts run --dry-run --limit 3      # render only, look at work/renders/
uv run social2shorts run --limit 3                # upload 3 (unlisted by default)
uv run social2shorts run --source raw --privacy scheduled --continuous
uv run social2shorts review                       # what needs a decision
uv run social2shorts review --approve 7667654234271255828
uv run social2shorts review --approve 76676... --original originals/VID_123_bsl.mp4
uv run social2shorts review --reject raw-0f3a...
uv run social2shorts publish all --privacy public  # after checking the unlisted uploads
uv run social2shorts status                       # counts, quota left today, next publications
uv run social2shorts schedule                     # planned publish times and free slots
```

Other commands: `discover` (refresh the catalog), `curate` (show the shortlist), `ingest` (copy new
phone originals and cache their fingerprints).

`--continuous` removes the `--limit` cap: the run uploads until today's quota is spent. Register
it as a Windows scheduled task with `scripts/scheduled_run.ps1`.

## Quota and scheduling

The Data API allows 10,000 units/day and `videos.insert` costs about 1,600, so roughly **6 uploads
per day** (a separate per-project cap on insert calls, `max_uploads_per_day`, is enforced too).
Usage is tracked per Pacific-time day in the database, and a run stops cleanly when the budget is
spent. With `--privacy scheduled`, each video is uploaded `private` with a `publishAt` set to the
next free slot of `[schedule]` (`publish_slots`, `timezone`, `max_per_day`), so 200 inbox videos
become a steady publishing calendar.

Unaudited API projects get every upload locked to `private`. The pipeline warns when YouTube
applied a different privacy than requested and stores what was actually applied.

## Safety

- First uploads default to `unlisted` for human review.
- Licensed music (catalog track metadata) is held for approval.
- Ambiguous original matches are never auto-picked: `review --approve ID --original PATH`.
- The ledger is written right after each upload; reruns never upload the same content twice
  (catalog: caption+duration fingerprint across platforms; raw: file sha256).
- Content-ID guard: right after upload and again on later runs (claims appear with a delay), any
  rejected, region-blocked or licensed-content video is made private and queued for review.
  The public Data API does not expose Content ID claims themselves, so YouTube Studio remains the
  source of truth.
- OAuth failures stop the run, exit with code 2 and leave an alert in `status`. Unattended runs
  never open a browser.

## Development

```bash
uv run pytest -q                      # unit + integration (needs ffmpeg)
uv run pytest -q -m "not integration"
uv run ruff check . && uv run ruff format --check .
```

## License
MIT
