# 📝 social2shorts - Development Roadmap

## 🟢 Completed (Phase 1 & 2)
- [x] Project scaffold & SQLite store
- [x] TikTok discovery via yt-dlp
- [x] Engagement-based curation
- [x] Licensed-music audio gate
- [x] MTP phone ingestion (Windows COM)
- [x] pHash matching (Perceptual Hashing)
- [x] FFmpeg transcode to 1080x1920
- [x] Faster-Whisper captions + Gemini metadata
- [x] YouTube OAuth & resumable uploads
- [x] Idempotent ledger
- [x] Content-ID post-upload guard
- [x] Instagram discovery & cross-platform dedupe
- [x] Continuous mode & Windows Scheduler

## 🟢 Core rework (Oct 2026)
- [x] One shared item processor; `run --source catalog|raw|all`, `--dry-run`, `--continuous`
- [x] Raw inbox path (sha256 dedupe) wired to the montage engine
- [x] Quota tracking + scheduled publication (`publishAt`)
- [x] Instagram discovery wired; cross-platform dedupe
- [x] google-genai migration, humanized metadata, word-level whisper
- [x] Upload retry/backoff, DB migrations, approval flow

## 🔴 Future Enhancements (Phase 3+)
- [ ] **Advanced Curation**: Integrate LLM-based content analysis for better scoring.
- [ ] **Enhanced Transcoding**: Add dynamic overlays, brand watermarks, or B-roll insertion.
- [ ] **Multi-Profile Support**: Handle multiple source profiles in one run.
- [ ] **Web Dashboard**: A simple Flask/FastAPI UI to manage the review queue and track upload stats.
- [ ] **Cloud Deployment**: Containerize with Docker for headless server execution.
