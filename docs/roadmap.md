# Roadmap

Based on the code as it is today and on the recommendations in
[State of the art](research/state-of-the-art.md) that are not implemented yet.

## Done

**Catalog path**

- [x] TikTok and Instagram discovery with yt-dlp, cookies for Instagram
- [x] Engagement score, duration filters, cross-platform dedupe (`prefer_platform`)
- [x] Licensed-music gate on yt-dlp `track` metadata
- [x] Phone originals over Windows MTP (COM `Shell.Application`), local cache, offline fallback
- [x] Duration + pHash matching, ambiguous matches sent to a human, cached fingerprints

**Raw path and montage**

- [x] Inbox scanner with sha256 dedupe (hash cached by path, size, mtime)
- [x] Signal analysis: motion, loudness, speech, scene cuts, subject position, engine revs
- [x] Highlight planner: hook first (rev-led when possible), energy-based rhythm, snapping to
      revs / cuts / onsets, words never split, speed ramps, loop ending with crossfade
- [x] Polish mode for edited reels: dead head/tail trim only
- [x] Smart reframe (crop or blur) with per-cut stationary crops
- [x] Karaoke and minimal captions inside a 900×1400 safe box, hook title card
- [x] Per-video variation seeded from content, -14 LUFS loudness, music ducking

**Shared processor and publishing**

- [x] One item processor for both paths; `run --source catalog|raw|all`, `--dry-run`, `--continuous`
- [x] faster-whisper word timings, Gemini metadata (google-genai), `humanize()`, fallback titles
- [x] Resumable upload with retries and backoff; ledger written right after upload
- [x] Quota budget per Pacific day (units and insert calls); scheduled `publishAt` slots
- [x] Content-ID guard after upload and on a 3-day sweep
- [x] Review queue with approve / approve with original / reject, retry budget
- [x] Explicit `containsSyntheticMedia`, `selfDeclaredMadeForKids`, language fields
- [x] SQLite migrations, OAuth alerts with exit code 2, Windows scheduled task script

## Next

### Before the first bulk upload (P0)

- [ ] **Pass the YouTube API compliance audit.** Operator task. Until then, uploads are
      private only and scheduling does nothing.
- [ ] **Music and length guard.** If a render has music not from a claim-free whitelist, cap it
      at 59 s. Today `music_dir` is trusted and polish mode can keep up to 180 s.
- [ ] **Richer variation.** A registry of caption presets and hook layouts rotated per video,
      a rule that two consecutive uploads never share a combination, and a log of the choices
      per upload. Today variation covers colours, positions, rhythm, zoom and music within
      one style per mode.
- [ ] **Source-window dedupe.** Never upload two Shorts cut from the same moments.
- [ ] **Quota defaults.** Review `upload_cost_units` against the current Google pricing and
      the separate `videos.insert` bucket.

### Quality (P1)

- [ ] **Gemini as judge.** Send only candidate windows (with raised fps) to Gemini, rank them
      with a fixed rubric, snap the answers to our own boundaries.
- [ ] **Engine RPM tracking.** Harmonic ridge tracking on top of today's spectral-flux rev
      detector, for downshifts and full-throttle pulls.
- [ ] **GoPro GPMF telemetry**, if the camera records it: real speed and lean.
- [ ] **AutoFlip pan and track modes.** Today every cut uses a stationary crop.
- [ ] **Loop ending by visual similarity** to the opening frame, not only the energy peak.
- [ ] **Event-keyed SFX** (whoosh, impact) on revs and cuts, with minimum spacing.
- [ ] **Safe-zone debug overlay** render.

### Later (P2)

- [ ] Beat-synced cuts when music is used.
- [ ] A dead-space filter before candidate generation (today: static and dark penalties, and
      head/tail trim in polish mode).
- [ ] PySceneDetect `AdaptiveDetector` or TransNetV2 if scene cuts misfire.
- [ ] WhisperX alignment if karaoke timing drifts.
- [ ] Analytics feedback loop: viewed rate and engaged views per Short, used to reweight the
      scorer.
- [ ] open_clip prompt similarity as a scenic feature.
- [ ] Multi-profile runs, a small web UI for the review queue, a Docker image for headless runs.

## Known limitations

- Content ID claims are not exposed by the public Data API. The guard uses rejection, region
  blocking, `licensedContent` and removal.
- MTP phone ingestion is Windows-only.
- Cross-platform dedupe relies on caption and rounded duration.
- Processing is sequential by design: the YouTube quota is the bottleneck.

## Known issues

Found while writing these docs. Not fixed yet.

- `publish all` also changes videos the Content-ID guard made private, and clears `publishAt`
  on scheduled videos, publishing them at once.
- `download_reference` does not pass `instagram_cookies`, so reference downloads for Instagram
  posts may fail where discovery works.
- The `run --privacy` help text loses `[youtube]` (rich markup) and shows "Default:  default_privacy".
- `numpy` is imported by the montage engine but is not a direct dependency in
  `pyproject.toml` (it comes in through other packages).
- The `matched` item status is never set; match results live in the `matches` table.
