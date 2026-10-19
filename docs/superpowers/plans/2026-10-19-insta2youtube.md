# insta2youtube (motoride-mh) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a pipeline that republishes the *best* of @motoride.mh's TikTok/Instagram catalogue as clean, pro-quality, monetization-safe **YouTube Shorts** on @motoride-mh — curated, de-duplicated, sourced from the phone's 1080p+ originals, lightly AI-enriched, uploaded via the YouTube API.

**Architecture:** A staged pipeline over a SQLite store. **Discovery** reads the TikTok profile via `yt-dlp` (metadata + a small 540p reference clip). **Curation** filters/scores to a shortlist. **Ingestion** pulls the finished vertical originals off the phone (MTP/COM). **Matching** links each post to its original by duration + perceptual hash. An **audio gate** routes licensed-music videos to a review queue. **Enrichment** adds burned captions (faster-whisper) and LLM-written titles/descriptions. **Transcode** renders 1080×1920 H.264. **Upload** pushes Shorts (unlisted first) through the Data API. A **ledger** makes the whole thing idempotent. Pure logic is unit-tested; thin I/O adapters sit behind interfaces with fakes.

**Tech Stack:** Python 3.12, `uv` (env/deps), `typer`+`rich` (CLI), `pydantic` v2 (config/models), `yt-dlp`, `ffmpeg`/`ffprobe`, `PyAV`+`imagehash`+`Pillow` (pHash), `faster-whisper` (transcription), `google-generativeai` (Gemini, free key — public content only) for metadata, `google-api-python-client`+`google-auth-oauthlib` (YouTube), `pywin32` (MTP COM), `pytest`.

**Spec:** `C:\workdir\uw\scraper\insta2yorube\docs\DESIGN.md`

## Global Constraints

- **Python 3.12** only (the single interpreter on this machine).
- **Final media source = the phone's finished vertical originals** (`Movies/Edits`, `Movies/Instagram`), measured at **1440×2560 HEVC**; transcode target **1080×1920 H.264, AAC, +faststart**.
- **Never publish a 540p TikTok fallback without the user's explicit opt-in.** A post with no matched original is stored `pending_original`, never auto-uploaded.
- **First production run is `privacy=unlisted`.** Public/scheduled only after human review.
- **Ambiguous match → `review_queue`.** Never auto-pick among multiple candidates within threshold.
- **Audio gate:** `original sound`/empty `track` → eligible for auto; any licensed track → `review_queue`.
- **Idempotent:** a content fingerprint ledger guarantees each source video uploads at most once across TikTok+Instagram and across repeated runs.
- **Only public content leaves the machine** (to yt-dlp/Gemini/YouTube). No internal/sensitive data, ever, in any request. Gemini key used is the **free** `GEMINI_API_KEY`.
- **Git:** new local repo in the project folder; **local commits only, no push/remote without explicit user go-ahead**. Commit author `m.elkhou@hotmail.com`; **no `Co-Authored-By: Claude` / no Claude signature** in commit messages.
- **Secrets** (OAuth client, tokens, API keys) live outside the repo and are git-ignored.

## Review Focus

- **Duration collisions** (dozens of ~20 s clips): duration alone must never decide a match; when ≥2 originals fall within the duration tolerance, pHash must disambiguate and, failing a clear winner, the item goes to `review_queue` — covered in Task 6.
- **TikTok extractor breakage / empty response**: discovery returning 0 entries (or an "empty media response") must raise a loud error, not silently produce an empty catalogue that looks like success — covered in Task 2.
- **Licensed music tagged `original sound`** (ambient/background music): the pre-upload tag can't catch everything, so a post-upload Content-ID check must flag/unpublish — covered in Task 12; first batch is unlisted-then-reviewed as the Phase-1 backstop.
- **Phone disconnected / MTP item missing** mid-ingest: the ingest adapter must raise a specific error (not skip the file silently, which would strand a post as unmatched) — covered in Task 5.
- **OAuth refresh failure in an unattended run**: the uploader must surface an alert and pause the run, never stop silently or crash the scheduler loop — covered in Task 9 / Task 14.

---

## File Structure

```text
insta2yorube/
  pyproject.toml                     # uv project, deps, pytest config
  config.example.toml                # sample config (paths, thresholds, api)
  .gitignore                         # secrets/, *.sqlite, originals/, work/, tokens
  src/insta2youtube/
    __init__.py
    config.py        # pydantic Settings; load_config(path) -> Settings
    models.py        # pydantic models: CatalogEntry, Original, MatchResult, ShortMeta, enums
    db.py            # Store: SQLite schema + typed CRUD (catalog, originals, matches, ledger, review_queue)
    discover.py      # TikTok discovery via yt-dlp (metadata + 540p reference)
    curate.py        # pure filtering + scoring -> shortlist
    audio_gate.py    # pure licensed-music classification
    phash.py         # frame sampling + perceptual hashing (PyAV/Pillow/imagehash)
    match.py         # pure matching logic over durations + phashes
    ingest.py        # MTP phone source (COM) behind PhoneSource interface + FakePhoneSource
    enrich.py        # faster-whisper transcription; ASS caption build; Gemini metadata
    transcode.py     # ffmpeg arg building (pure) + runner: to_short(...)
    youtube_upload.py# OAuth creds + resumable videos.insert + set_privacy
    contentid.py     # post-upload claim check
    pipeline.py      # orchestration: run_backlog(...), run_continuous(...)
    cli.py           # typer app: auth, discover, curate, ingest, run, review, publish, status
  tests/
    test_config.py test_models.py test_db.py test_curate.py test_audio_gate.py
    test_match.py test_phash.py test_ingest.py test_enrich.py test_transcode.py
    test_youtube_upload.py test_pipeline.py
    fixtures/        # tiny sample jsons, 2 short clips
```

---

## Task 1: Project scaffold, config, models, store

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `config.example.toml`, `src/insta2youtube/__init__.py`, `src/insta2youtube/config.py`, `src/insta2youtube/models.py`, `src/insta2youtube/db.py`
- Test: `tests/test_config.py`, `tests/test_models.py`, `tests/test_db.py`

**Interfaces:**
- Produces:
  - `models.Platform` = Enum(`tiktok`,`instagram`); `models.ItemStatus` = Enum(`discovered`,`curated`,`pending_original`,`matched`,`ambiguous`,`review_audio`,`ready`,`uploaded`,`skipped`).
  - `models.CatalogEntry(platform, source_id, url, caption, duration: float, view_count, like_count, comment_count, upload_date: str, track: str, artist: str, raw: dict)`.
  - `models.Original(path: str, duration: float, phashes: list[str])`.
  - `models.MatchResult(source_id, original_path: str|None, confidence: float, status: ItemStatus)`.
  - `models.ShortMeta(title: str, description: str, tags: list[str])`.
  - `config.Settings` (pydantic-settings) with `tiktok_profile_url, youtube_handle, min_duration_s=12.0, duration_tol_s=1.0, phash_threshold=10, phash_frames=6, originals_dir, work_dir, db_path, phone_name_match, phone_video_folders: list[str], gemini_model, default_privacy="unlisted"`; `config.load_config(path: str) -> Settings`.
  - `db.Store(db_path)` with `init_schema()`, `upsert_catalog(e: CatalogEntry)`, `all_catalog() -> list[CatalogEntry]`, `set_status(source_id, status)`, `set_match(m: MatchResult)`, `add_review(source_id, reason: str)`, `reviews() -> list[dict]`, `mark_uploaded(fingerprint: str, source_id, youtube_id)`, `is_uploaded(fingerprint: str) -> bool`, `fingerprint(entry) -> str`.

- [ ] **Step 1: Write failing tests**

```python
# test_config.py
def test_load_config_defaults(tmp_path):
    p = tmp_path/"c.toml"; p.write_text('tiktok_profile_url="https://www.tiktok.com/@motoride.mh"\nyoutube_handle="@motoride-mh"\noriginals_dir="o"\nwork_dir="w"\ndb_path="db.sqlite"\n')
    s = load_config(str(p))
    assert s.min_duration_s == 12.0 and s.default_privacy == "unlisted" and s.phash_frames == 6

# test_models.py
def test_catalog_entry_is_original_audio():
    e = CatalogEntry(platform="tiktok", source_id="1", url="u", caption="c", duration=20, view_count=1, like_count=0, comment_count=0, upload_date="20260101", track="original sound", artist="Moto Ride Mh", raw={})
    assert e.is_original_audio is True

# test_db.py
def test_upsert_and_fingerprint_idempotent(tmp_path):
    s = Store(str(tmp_path/"db.sqlite")); s.init_schema()
    e = CatalogEntry(platform="tiktok", source_id="7", url="u", caption="road", duration=20.0, view_count=5, like_count=1, comment_count=0, upload_date="20260101", track="original sound", artist="x", raw={})
    s.upsert_catalog(e); s.upsert_catalog(e)
    assert len(s.all_catalog()) == 1
    fp = s.fingerprint(e); s.mark_uploaded(fp, "7", "yt123")
    assert s.is_uploaded(fp) is True
```

- [ ] **Step 2: Run tests, verify they fail** — `uv run pytest tests/test_config.py tests/test_models.py tests/test_db.py -q` → FAIL (imports missing).
- [ ] **Step 3: Scaffold the project.** `uv init`, add deps to `pyproject.toml` (list from Tech Stack; pin exact versions), write `.gitignore` (ignore `secrets/`, `*.sqlite`, `originals/`, `work/`, `__pycache__/`, `.venv/`, `tokens/`), `config.example.toml`. `git init`; set local `user.email=m.elkhou@hotmail.com`.
- [ ] **Step 4: Implement `config.py`, `models.py`, `db.py`** to the Interfaces above. `is_original_audio` = `track.strip().lower() in {"", "original sound"}`. `fingerprint(entry)` = stable hash of normalized caption + rounded duration (so the same video on TikTok and Instagram collides). SQLite schema: tables `catalog`, `matches`, `review_queue`, `ledger(fingerprint PK, source_id, youtube_id, created)`.
- [ ] **Step 5: Run tests, verify pass.** `uv run pytest -q` → PASS.
- [ ] **Step 6: Commit** — `feat: project scaffold, config, models, sqlite store`.

---

## Task 2: TikTok discovery

**Files:**
- Create: `src/insta2youtube/discover.py`
- Test: `tests/test_discover.py`, `tests/fixtures/tiktok_flat.json`, `tests/fixtures/tiktok_entry.json`

**Interfaces:**
- Consumes: `models.CatalogEntry`, `config.Settings`.
- Produces: `discover.parse_entry(info: dict) -> CatalogEntry` (pure); `discover.discover_tiktok(profile_url, limit: int|None, ytdlp=run_ytdlp) -> list[CatalogEntry]` (adapter, `ytdlp` injectable); `discover.download_reference(url, dest_dir, ytdlp=run_ytdlp) -> str` (downloads the 540p no-watermark clip, returns path).

- [ ] **Step 1: Write failing tests** (pure parse + empty-guard):

```python
def test_parse_entry_maps_fields():
    info = json.load(open("tests/fixtures/tiktok_entry.json"))
    e = parse_entry(info)
    assert e.platform == "tiktok" and e.source_id == "7667654234271255828" and e.duration == 20 and e.track == "original sound"

def test_discover_raises_on_empty():
    with pytest.raises(DiscoveryError):
        discover_tiktok("url", None, ytdlp=lambda *a, **k: [])   # empty = failure, not success
```

- [ ] **Step 2: Run tests, verify fail** — `uv run pytest tests/test_discover.py -q` → FAIL.
- [ ] **Step 3: Implement `discover.py`.** `discover_tiktok` calls the injected `ytdlp` runner with `--flat-playlist`/`--dump-json` then per-entry `--dump-json` (or a single `--dump-single-json`); maps via `parse_entry`; **raises `DiscoveryError` if zero entries**. `run_ytdlp` default uses `yt_dlp.YoutubeDL` with `impersonate` target when available; `download_reference` selects the no-watermark `h264_540p*` format into `work/refs/`.
- [ ] **Step 4: Run tests, verify pass** — PASS.
- [ ] **Step 5: Integration check (real network, manual).** `uv run python -m insta2youtube.cli discover --limit 0` against the real profile; expect **≥ 200** catalog rows and non-empty `track` on a sample. (Record count in output; do not assert in CI.)
- [ ] **Step 6: Commit** — `feat: tiktok discovery via yt-dlp with empty-response guard`.

---

## Task 3: Curation (pure)

**Files:**
- Create: `src/insta2youtube/curate.py`
- Test: `tests/test_curate.py`

**Interfaces:**
- Produces: `curate.score(e: CatalogEntry) -> float`; `curate.is_eligible(e, min_duration_s, exclude_no_duration=True) -> bool`; `curate.curate(entries, settings) -> list[CatalogEntry]` (eligible only, sorted by score desc).

- [ ] **Step 1: Write failing tests:**

```python
def test_excludes_microclips_and_slideshows():
    short = _entry(duration=6); ok = _entry(duration=30); slideshow = _entry(duration=None)
    assert is_eligible(short, 12.0) is False
    assert is_eligible(ok, 12.0) is True
    assert is_eligible(slideshow, 12.0) is False

def test_curate_sorts_by_engagement():
    a=_entry(duration=30, view_count=100); b=_entry(duration=30, view_count=1000)
    out = curate([a,b], _settings(min_duration_s=12))
    assert out[0].view_count == 1000
```

- [ ] **Step 2: Run tests, verify fail.** → FAIL.
- [ ] **Step 3: Implement `curate.py`.** `is_eligible` = has a real video duration and `duration >= min_duration_s`. `score` = weighted blend (log1p(views) dominant, plus likes/comments ratio, mild bonus for 15–60 s). `curate` filters then sorts desc.
- [ ] **Step 4: Run tests, verify pass.** → PASS.
- [ ] **Step 5: Commit** — `feat: curation filter + engagement score`.

---

## Task 4: Audio gate (pure)

**Files:**
- Create: `src/insta2youtube/audio_gate.py`
- Test: `tests/test_audio_gate.py`

**Interfaces:**
- Produces: `audio_gate.classify(e: CatalogEntry) -> Literal["original","licensed"]`.

- [ ] **Step 1: Write failing test:**

```python
def test_classify_audio():
    assert classify(_entry(track="original sound", artist="Moto Ride Mh")) == "original"
    assert classify(_entry(track="Blinding Lights", artist="The Weeknd")) == "licensed"
    assert classify(_entry(track="", artist="")) == "original"
```

- [ ] **Step 2: Run test, verify fail.** → FAIL.
- [ ] **Step 3: Implement `classify`** using `CatalogEntry.is_original_audio`; licensed otherwise.
- [ ] **Step 4: Run test, verify pass.** → PASS.
- [ ] **Step 5: Commit** — `feat: licensed-music audio gate`.

---

## Task 5: Phone ingestion (MTP) behind an interface

**Files:**
- Create: `src/insta2youtube/ingest.py`
- Test: `tests/test_ingest.py`

**Interfaces:**
- Produces:
  - `ingest.PhoneVideo(name: str, duration_s: float|None, folder: str)`.
  - Protocol `ingest.PhoneSource` with `list_videos(folders: list[str]) -> list[PhoneVideo]` and `copy(name: str, dest_dir: str) -> str`.
  - `ingest.MtpPhoneSource(phone_name_match, ...)` (real COM impl, Windows) and `ingest.FakePhoneSource(catalog: dict)` (tests).
  - `ingest.PhoneNotFound`, `ingest.FileNotOnPhone` exceptions.
  - `ingest.ingest_originals(source: PhoneSource, folders, dest_dir) -> list[Original]` (copies, then probes duration via ffprobe; leaves `phashes=[]` — pHashes are computed later during matching in Task 6, so this task does not depend on `phash.py`).

- [ ] **Step 1: Write failing tests (with the fake):**

```python
def test_fake_copy_and_missing(tmp_path):
    fake = FakePhoneSource({"Movies/Edits": [("VID_a.mp4", tmp_path/"a.mp4")]})
    assert any(v.name=="VID_a.mp4" for v in fake.list_videos(["Movies/Edits"]))
    with pytest.raises(FileNotOnPhone):
        fake.copy("VID_missing.mp4", str(tmp_path))
```

- [ ] **Step 2: Run tests, verify fail.** → FAIL.
- [ ] **Step 3: Implement `ingest.py`.** `MtpPhoneSource` uses `win32com.client.Dispatch("Shell.Application")`, finds the device by `phone_name_match`, walks `Movies/Edits` etc.; `list_videos` reads name + duration (details column discovered by header scan, as validated in the spike); `copy` uses `Folder.CopyHere` and polls for the destination file, **raising `FileNotOnPhone` if the item isn't present and `PhoneNotFound` if the device is absent** (never skip silently). `FakePhoneSource` satisfies the Protocol for tests.
- [ ] **Step 4: Run tests, verify pass.** → PASS.
- [ ] **Step 5: Integration check (real phone, manual).** `uv run python -m insta2youtube.cli ingest --limit 2`; expect 2 files copied into `originals/`, ffprobe shows **≥1080 height, vertical**.
- [ ] **Step 6: Commit** — `feat: MTP phone ingestion with fake for tests`.

---

## Task 6: Perceptual hashing + matching

**Files:**
- Create: `src/insta2youtube/phash.py`, `src/insta2youtube/match.py`
- Test: `tests/test_phash.py`, `tests/test_match.py`

**Interfaces:**
- Produces:
  - `phash.phash_file(path, n_frames) -> list[str]` (even-spaced frames via PyAV → `imagehash.phash` → hex strings).
  - `phash.min_distance(a: list[str], b: list[str]) -> int` (pure; min Hamming over frame pairs).
  - `match.match_one(entry, originals: list[Original], ref_phashes: list[str], dur_tol, phash_threshold) -> MatchResult` (pure).

- [ ] **Step 1: Write failing tests (pure logic, synthetic hashes):**

```python
def test_min_distance_zero_on_identical():
    assert min_distance(["ffff0000"], ["ffff0000"]) == 0

def test_match_ambiguous_goes_to_review():
    o1 = Original(path="A.mp4", duration=20.0, phashes=["ffff0000"])
    o2 = Original(path="B.mp4", duration=20.3, phashes=["ffff0001"])  # both near in duration AND phash
    m = match_one(_entry(duration=20.0), [o1,o2], ref_phashes=["ffff0000"], dur_tol=1.0, phash_threshold=10)
    assert m.status == "ambiguous"

def test_match_unique_winner():
    o1 = Original(path="A.mp4", duration=20.0, phashes=["ffff0000"])
    o2 = Original(path="B.mp4", duration=45.0, phashes=["00000000"])
    m = match_one(_entry(duration=20.0), [o1,o2], ref_phashes=["ffff0000"], dur_tol=1.0, phash_threshold=5)
    assert m.status=="matched" and m.original_path=="A.mp4" and m.confidence>0
```

- [ ] **Step 2: Run tests, verify fail.** → FAIL.
- [ ] **Step 3: Implement `phash.py` + `match.py`.** `match_one`: candidates = originals within `dur_tol`; score each by `min_distance(ref_phashes, cand.phashes)`; **unique** candidate below `phash_threshold` → `matched`; **≥2** below threshold with no clear separation → `ambiguous` (→ review); none → `pending_original`. Confidence = `1 - dist/threshold`. (`MatchResult.status` is always one of the `ItemStatus` enum values: `matched` | `ambiguous` | `pending_original`.)
- [ ] **Step 4: Run tests, verify pass.** → PASS.
- [ ] **Step 5: Commit** — `feat: perceptual-hash matching with ambiguity -> review`.

---

## Task 7: Transcode to 1080×1920 (ffmpeg)

**Files:**
- Create: `src/insta2youtube/transcode.py`
- Test: `tests/test_transcode.py`

**Interfaces:**
- Produces: `transcode.build_args(src, dst, target=(1080,1920), subtitle_ass: str|None) -> list[str]` (pure ffmpeg argv); `transcode.to_short(src, dst, subtitle_ass=None) -> str` (runner).

- [ ] **Step 1: Write failing tests (pure argv):**

```python
def test_build_args_scales_pads_and_burns():
    a = build_args("in.mp4","out.mp4", subtitle_ass="subs.ass")
    s = " ".join(a)
    assert "scale=" in s and "pad=" in s and "1080" in s and "1920" in s
    assert "ass=" in s and "libx264" in s and "+faststart" in s
```

- [ ] **Step 2: Run test, verify fail.** → FAIL.
- [ ] **Step 3: Implement `transcode.py`.** Build a filterchain: `scale` to fit within 1080×1920 preserving aspect, `pad` to exactly 1080×1920 (black bars only if not already 9:16), optional `ass=` subtitle burn; encode `libx264` CRF ~18, `aac`, `-movflags +faststart`. `to_short` runs ffmpeg and returns `dst`.
- [ ] **Step 4: Run test, verify pass.** → PASS.
- [ ] **Step 5: Integration check (manual).** Transcode one ingested original; ffprobe output = exactly **1080×1920, h264**.
- [ ] **Step 6: Commit** — `feat: ffmpeg transcode to 1080x1920 shorts`.

---

## Task 8: Enrichment — captions + metadata

**Files:**
- Create: `src/insta2youtube/enrich.py`
- Test: `tests/test_enrich.py`

**Interfaces:**
- Produces:
  - `enrich.Segment(start: float, end: float, text: str)`.
  - `enrich.transcribe(path, model=...) -> list[Segment]` (faster-whisper adapter; injectable).
  - `enrich.build_ass(segments, style) -> str` (pure; modern word-forward caption style; returns ASS path).
  - `enrich.generate_metadata(entry, transcript_text, llm=gemini_call) -> ShortMeta` (adapter; `llm` injectable); plus pure `enrich.sanitize_meta(raw: dict) -> ShortMeta` (title ≤100 chars, ≤15 tags, strips platform handles/hashtags-spam, appends a small fixed hashtag set).

- [ ] **Step 1: Write failing tests (pure parts):**

```python
def test_build_ass_contains_dialogue(tmp_path):
    p = build_ass([Segment(0,1,"salut"), Segment(1,2,"moto")], style=DEFAULT_STYLE)
    assert "Dialogue:" in open(p,encoding="utf-8").read()

def test_sanitize_meta_caps_title_and_tags():
    m = sanitize_meta({"title":"x"*200, "description":"d", "tags":[f"t{i}" for i in range(50)]})
    assert len(m.title) <= 100 and len(m.tags) <= 15
```

- [ ] **Step 2: Run tests, verify fail.** → FAIL.
- [ ] **Step 3: Implement `enrich.py`.** `transcribe` wraps `faster_whisper.WhisperModel` (auto language, segments; empty if no speech). `build_ass` emits styled captions (readable for night riding: bold, outline, lower-third, one short phrase at a time). `generate_metadata` prompts Gemini (free key) with the caption+transcript to produce a YouTube-native title/description/tags **reworded** (not copied), then `sanitize_meta` enforces limits and strips cross-platform noise. **Only public content is sent.**
- [ ] **Step 4: Run tests, verify pass.** → PASS.
- [ ] **Step 5: Integration check (manual).** Transcribe one real clip; confirm captions render after transcode; confirm Gemini returns a sane title.
- [ ] **Step 6: Commit** — `feat: whisper captions + llm youtube metadata`.

---

## Task 9: YouTube upload (OAuth + insert)

**Files:**
- Create: `src/insta2youtube/youtube_upload.py`
- Test: `tests/test_youtube_upload.py`

**Interfaces:**
- Produces:
  - `youtube_upload.get_credentials(client_secrets, token_path) -> Credentials` (InstalledAppFlow once; refresh thereafter; **raises `AuthRefreshError` on refresh failure**).
  - `youtube_upload.build_body(meta: ShortMeta, privacy) -> dict` (pure; snippet+status, categoryId set, `selfDeclaredMadeForKids=False`).
  - `youtube_upload.upload_short(video_path, meta, privacy, service=build_service) -> str` (resumable; returns video_id).
  - `youtube_upload.set_privacy(video_id, privacy, service=...) -> None`.

- [ ] **Step 1: Write failing tests (pure body + refresh-error surface):**

```python
def test_build_body_sets_privacy_and_kids():
    b = build_body(ShortMeta(title="t", description="d", tags=["moto"]), "unlisted")
    assert b["status"]["privacyStatus"]=="unlisted" and b["status"]["selfDeclaredMadeForKids"] is False
    assert b["snippet"]["title"]=="t"

def test_refresh_failure_raises(monkeypatch, tmp_path):
    # a stored token whose refresh raises -> AuthRefreshError, not a silent None
    with pytest.raises(AuthRefreshError):
        get_credentials("nope.json", str(_token_that_fails_refresh(tmp_path)))
```

- [ ] **Step 2: Run tests, verify fail.** → FAIL.
- [ ] **Step 3: Implement `youtube_upload.py`.** Scope `youtube.upload`; token JSON under `secrets/` (git-ignored). Resumable `MediaFileUpload`. On refresh exception → `AuthRefreshError` (callers alert+pause). `build_service` default via `googleapiclient.discovery.build`.
- [ ] **Step 4: Run tests, verify pass.** → PASS.
- [ ] **Step 5: Integration check (manual, user-gated).** After `cli auth`, upload one transcoded clip as `private`; confirm it appears in Studio; delete or keep as test.
- [ ] **Step 6: Commit** — `feat: youtube oauth + resumable shorts upload`.

---

## Task 10: Pipeline orchestration (backlog) + idempotent ledger

**Files:**
- Create: `src/insta2youtube/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: all prior modules (injected adapters for network/phone/llm/youtube).
- Produces: `pipeline.run_backlog(settings, phone: PhoneSource, deps, limit, privacy="unlisted") -> RunReport` where `deps` bundles injectable `discover/transcribe/llm/uploader` so the whole pipeline is testable with fakes. `RunReport(uploaded: list, review: list, pending: list, skipped: list)`.

- [ ] **Step 1: Write failing test (full pipeline with fakes, no network):**

```python
def test_backlog_routes_by_audio_and_match_and_is_idempotent(tmp_path):
    # 3 fake catalog entries: original-audio+match -> uploaded; licensed-audio -> review; no original -> pending
    report = run_backlog(settings, FakePhoneSource(...), fake_deps(...), limit=10)
    assert len(report.uploaded)==1 and len(report.review)==1 and len(report.pending)==1
    report2 = run_backlog(settings, FakePhoneSource(...), fake_deps(...), limit=10)
    assert len(report2.uploaded)==0   # ledger makes re-run upload nothing
```

- [ ] **Step 2: Run test, verify fail.** → FAIL.
- [ ] **Step 3: Implement `run_backlog`.** Order: discover→curate→(download refs + ingest originals + phash)→match→audio gate→ for `ready` items: enrich→transcode→upload→`mark_uploaded(fingerprint,...)`. Route `ambiguous`→review, `licensed`→review, `no_match`→pending. Skip any fingerprint already in ledger (idempotent). `limit` caps uploads (default MVP 3–5). On `AuthRefreshError`: stop the run with an alert in `RunReport`, no crash.
- [ ] **Step 4: Run test, verify pass.** → PASS.
- [ ] **Step 5: Commit** — `feat: backlog orchestration with idempotent ledger`.

---

## Task 11: CLI (Phase-1 complete)

**Files:**
- Create: `src/insta2youtube/cli.py`
- Modify: `pyproject.toml` (console script `insta2youtube`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces typer commands: `auth`, `discover [--limit]`, `curate`, `ingest [--limit]`, `run [--limit N --privacy unlisted]`, `review` (list audio/ambiguous queue; approve → `ready`), `publish <source_id|all> [--privacy public]` (flip unlisted→public via `set_privacy`), `status`.

- [ ] **Step 1: Write failing test (CLI wiring with a fake runner):**

```python
def test_run_invokes_pipeline(monkeypatch):
    called = {}
    monkeypatch.setattr(cli, "run_backlog", lambda *a, **k: called.setdefault("ok", True) or _empty_report())
    result = CliRunner().invoke(app, ["run","--limit","3"])
    assert result.exit_code==0 and called["ok"]
```

- [ ] **Step 2: Run test, verify fail.** → FAIL.
- [ ] **Step 3: Implement `cli.py`** with `typer`+`rich` tables; wire each command to its module; `review`/`publish` operate on the Store.
- [ ] **Step 4: Run test, verify pass.** → PASS.
- [ ] **Step 5: End-to-end dry run (manual, user-gated).** `insta2youtube run --limit 3 --privacy unlisted` with the phone connected and OAuth done → **3 unlisted Shorts** live for the user to review. This is the Phase-1 deliverable.
- [ ] **Step 6: Commit** — `feat: cli (auth/discover/ingest/run/review/publish/status)`.

---

## Task 12: Post-upload Content-ID check *(Phase 2)*

**Files:** Create `src/insta2youtube/contentid.py`; Test `tests/test_contentid.py`.

**Interfaces:** `contentid.check_claims(video_id, service=...) -> list[Claim]`; `contentid.guard_after_upload(video_id, on_claim) -> None` (if claims found: set `private` + flag review).

- [ ] Step 1: Failing test — a fake service returning a claim causes `on_claim` to fire and privacy set to `private`.
- [ ] Step 2: verify fail → Step 3: implement via `videos.list`/claims where available → Step 4: verify pass → Step 5: Commit `feat: post-upload content-id guard`.

---

## Task 13: Instagram discovery (secondary) + cross-platform dedupe *(Phase 2)*

**Files:** Modify `discover.py` (+ `discover_instagram(...)` using cookies); Test `tests/test_dedupe.py`.

**Interfaces:** `discover.discover_instagram(profile, cookies) -> list[CatalogEntry]`; dedupe relies on the Task-1 `fingerprint` so IG and TikTok copies collapse to one.

- [ ] Step 1: Failing test — a TikTok entry and an IG entry of the same video share one fingerprint → one ledger row. Step 2–5 as usual; commit `feat: instagram source + cross-platform dedupe`.

---

## Task 14: Continuous mode + scheduler *(Phase 2)*

**Files:** Modify `pipeline.py` (`run_continuous`); Create `scripts/scheduled_run.ps1`; Test in `tests/test_pipeline.py`.

**Interfaces:** `pipeline.run_continuous(settings, ...)` = discover diff vs ledger → only new posts → same pipeline; new-but-no-original → `pending_original` (no fallback without opt-in). PowerShell task registers a Windows Scheduled Task. On `AuthRefreshError`: alert + pause (don't reschedule-crash-loop).

- [ ] Step 1: Failing test — given a ledger with N fingerprints, `run_continuous` processes only the unseen ones. Step 2–5; commit `feat: continuous mode + windows scheduler`.

---

## Notes for the executor
- Keep secrets (`client_secrets.json`, `token.json`, `GEMINI_API_KEY`) in `secrets/` (git-ignored); never commit them, never log them.
- Network/phone/LLM/YouTube calls live only in adapters with injectable seams; unit tests use fakes and never hit the network.
- Local commits only. Do **not** push or add a remote without the user's explicit go-ahead.
