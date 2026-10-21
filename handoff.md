# 🤝 Technical Handoff: social2shorts

## 🏗️ System Architecture
The project follows a staged pipeline architecture:
`Discovery` $\rightarrow$ `Curation` $\rightarrow$ `Ingestion` $\rightarrow$ `Matching` $\rightarrow$ `Enrichment` $\rightarrow$ `Transcode` $\rightarrow$ `Upload`.

### Key Components
- **Storage**: SQLite is used for the `catalog`, `matches`, `review_queue`, and `ledger`.
- **Matching**: Uses `imagehash.phash` on sampled frames. A match is only confirmed if the duration is within `duration_tol_s` AND the pHash distance is below `phash_threshold`.
- **Safety**: The `audio_gate` and `contentid` modules act as filters. Any video flagged as "licensed" or "claimed" is routed to the `review_queue` and set to `private`.
- **Idempotency**: The `ledger` table stores a fingerprint of each source video. This prevents the same content from being uploaded twice, even if it exists on both TikTok and Instagram.

## 🛠️ Maintenance Guide

### Adding a New Source
To add a new platform (e.g., Facebook Reels):
1. Implement a new discovery function in `discover.py`.
2. Map the result to the `CatalogEntry` model.
3. Update `pipeline.py` to include the new source.

### Debugging MTP Ingestion
The `MtpPhoneSource` relies on Windows `Shell.Application`. If phone detection fails:
- Verify the `phone_name_match` in `config.toml`.
- Ensure the phone is in "File Transfer" mode.
- Check that the `Movies/Edits` or `Movies/Instagram` folders exist.

### API Key Management
- **Gemini**: Uses a free key. If quotas are hit, consider upgrading to a paid tier.
- **YouTube**: Uses OAuth 2.0. If the `token.json` expires or is corrupted, run `social2shorts auth` to re-authenticate.

## ⚠️ Known Limitations
- **Single-Threaded**: The pipeline processes videos sequentially.
- **Windows Bound**: The MTP ingestion is specific to the Windows COM API.
- **pHash Memory**: Currently decodes all frames for pHash; acceptable for short-form but might need seeking for longer clips.

## 🚀 Deployment
The project is designed to run as a local automation tool. Use the `scripts/scheduled_run.ps1` to register it as a Windows Scheduled Task for hands-off operation.
