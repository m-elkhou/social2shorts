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

## 🟡 In Progress (Sprint A)
- [ ] Full Google-style docstrings for all modules
- [ ] README.md, Handoff.md, and TODO.md creation
- [ ] Implementation of `run-top --limit 10` command

## 🔴 Future Enhancements (Phase 3+)
- [ ] **Advanced Curation**: Integrate LLM-based content analysis for better scoring.
- [ ] **Enhanced Transcoding**: Add dynamic overlays, brand watermarks, or B-roll insertion.
- [ ] **Better Error Recovery**: Implement a more granular retry system for transient YouTube API errors.
- [ ] **Multi-Profile Support**: Handle multiple source profiles in one run.
- [ ] **Web Dashboard**: A simple Flask/FastAPI UI to manage the review queue and track upload stats.
- [ ] **Cloud Deployment**: Containerize with Docker for headless server execution.
