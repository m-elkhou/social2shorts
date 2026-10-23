# Technical handoff: social2shorts

## Architecture
Ports & adapters. `ports.py` declares the `typing.Protocol` interfaces (catalog source, phone
originals, media probe, transcriber, LLM, renderer, video platform). Adapters live in
`sources/` (yt-dlp, MTP phone, inbox), `ai/` (faster-whisper, Gemini) and `publish/` (YouTube,
Content-ID check, quota, scheduler). `pipeline.py` has ONE item processor shared by the two source
strategies (`CatalogStrategy`, `RawStrategy`). `db.Store` is the SQLite repository; the schema is
versioned with `PRAGMA user_version` (add a function to `db.MIGRATIONS` to change it).

## Item lifecycle
`discovered -> (pending_original | ambiguous | review_audio) -> rendered (dry run) -> uploaded`
(`-> claimed` if the guard flags it). Errors move an item to `failed`, retried up to 3 times, then
to the review queue. `review --approve` sets `ready` (processed first next run), `--reject` sets
`rejected`.

## Operations
- Auth alert: run `uv run social2shorts auth`; it also clears the alert shown by `status`.
- Quota: `status` shows units used today (Pacific day). Tune `[youtube] daily_quota_units` if
  Google grants more.
- Phone not plugged in: the catalog path uses the cached originals in `originals_dir`.

## Known limitations
- Content ID claims are not exposed by the public Data API; the guard uses rejection, region
  blocking and `licensedContent` signals, re-checked for 3 days after upload.
- MTP ingestion is Windows-only (COM `Shell.Application`).
- Cross-platform dedupe relies on caption + rounded duration.
- Processing is sequential (by design: the YouTube quota is the bottleneck).
