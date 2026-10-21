# 🎬 social2shorts

**Republish the best of TikTok & Instagram as high-quality YouTube Shorts.**

`social2shorts` is a professional-grade pipeline designed to curate high-resolution vertical content from social media profiles, match them with original phone recordings, and automatically enrich and upload them to YouTube.

## 🚀 Features

- **Multi-Platform Discovery**: Scrapes TikTok and Instagram profiles for top-performing content.
- **Original Quality Matching**: Uses Perceptual Hashing (pHash) to link social media posts to 1440x2560 originals on a connected mobile device (MTP).
- **AI Enrichment**: 
  - **Faster-Whisper**: Automatic transcription and word-forward captioning.
  - **Gemini AI**: Generates SEO-optimized YouTube titles, descriptions, and tags.
- **Pro Transcoding**: FFmpeg pipeline ensuring a perfect 1080x1920 H.264 output with faststart.
- **Monetization Safety**:
  - Licensed music detection via audio gate.
  - Post-upload Content-ID check that flips claimed videos to `private` immediately.
  - All production uploads start as `unlisted` for human review.
- **Idempotent Ledger**: Content fingerprinting ensures no video is ever uploaded twice.

## 🛠️ Tech Stack

- **Language**: Python 3.12
- **Environment**: [uv](https://github.com/astral-sh/uv)
- **AI/ML**: `faster-whisper`, `google-generativeai`
- **Media**: `ffmpeg`, `PyAV`, `imagehash`
- **API**: YouTube Data API v3
- **DB**: SQLite

## 📦 Installation

### 1. Prerequisites
- Python 3.12
- FFmpeg installed and in your system PATH.
- A connected Android/iOS device with videos in `Movies/Edits` or `Movies/Instagram`.

### 2. Setup
```bash
# Clone the repository
git clone https://github.com/m-elkhou/social2shorts.git
cd social2shorts

# Install dependencies using uv
uv sync
```

### 3. Configuration
Copy the example config and fill in your details:
```bash
cp config.example.toml config.toml
```
Key settings include:
- `tiktok_profile_url`: The profile to scrape.
- `youtube_handle`: Your channel handle.
- `gemini_model`: Your Gemini API model (e.g., `gemini-1.5-flash`).
- `phone_name_match`: The name of your phone as seen by Windows.

### 4. Authentication
Run the auth command to link your YouTube account:
```bash
uv run social2shorts auth
```

## 📖 Usage

### Discovery & Curation
```bash
# Discover posts from TikTok
uv run social2shorts discover --limit 100

# Curate the best posts based on engagement
uv run social2shorts curate
```

### Ingestion & Matching
```bash
# Copy originals from phone to local storage
uv run social2shorts ingest --limit 20

# Match posts to originals using pHash
uv run social2shorts match
```

### The Full Pipeline
```bash
# Run the backlog (discover -> curate -> match -> enrich -> upload)
uv run social2shorts run --limit 5 --privacy unlisted
```

### Management
```bash
# Review audio claims or ambiguous matches
uv run social2shorts review

# Publish a specific video to public
uv run social2shorts publish <source_id> --privacy public
```

## ⚖️ License
MIT
