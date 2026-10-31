# Getting started

This page takes you from a fresh machine to a first dry run, then to a first upload.

## 1. Install

You need **Python 3.12**, **[uv](https://docs.astral.sh/uv/)** and **ffmpeg** (with `ffprobe`)
on your `PATH`. uv installs Python for you if it is missing.

=== "Windows"

    ```powershell
    winget install astral-sh.uv
    winget install Gyan.FFmpeg
    git clone https://github.com/m-elkhou/social2shorts
    cd social2shorts
    uv sync
    ```

=== "macOS"

    ```bash
    brew install uv ffmpeg
    git clone https://github.com/m-elkhou/social2shorts
    cd social2shorts
    uv sync
    ```

=== "Linux"

    ```bash
    curl -LsSf https://astral.sh/uv/install.sh | sh
    sudo apt-get install -y ffmpeg
    git clone https://github.com/m-elkhou/social2shorts
    cd social2shorts
    uv sync
    ```

Check that ffmpeg has the filters the renderer uses (`ass` for captions, `loudnorm`,
`zoompan`, `xfade`) and the `libx264` encoder. `zscale` is optional: with it, HDR phone footage
is tonemapped with better quality.

```bash
ffmpeg -hide_banner -filters | grep -E " (ass|loudnorm|zoompan|xfade|zscale) "
ffmpeg -hide_banner -encoders | grep libx264
uv run social2shorts --help
```

!!! note "First transcription downloads a model"
    faster-whisper downloads the `whisper_model` (default `small`, about 500 MB) on first use
    and caches it. The first run is slower than the next ones.

### Phone originals on Windows (catalog path only)

The catalog path renders from the original files on your phone, not from the 540p copies that
TikTok serves. Copying uses Windows MTP through the `Shell.Application` COM object
(`pywin32`, installed automatically on Windows only).

1. Plug the phone in by USB, unlock it, and choose **File transfer** in the USB notification.
2. Open *This PC* and note the device name, for example `Galaxy S23 Ultra`.
3. Set `phone_name_match` to a part of that name (`"S23"`), and `phone_video_folders` to the
   folders that hold your finished reels, relative to the storage root:

    ```toml
    phone_name_match = "S23"
    phone_video_folders = ["Movies/Edits", "Movies/Instagram"]
    ```

4. Run `uv run social2shorts ingest`. Only files missing locally (same name and size) are
   copied to `originals_dir`. Each copy waits up to 300 s.

If the phone is not connected, or you are not on Windows, the sync is skipped with a warning
and the files already in `originals_dir` are used. On macOS or Linux you can copy the files
into `originals/` by hand.

## 2. Configure

```bash
cp config.example.toml config.toml
```

The minimum to edit:

```toml
youtube_handle = "@your-channel"

[sources]
tiktok_profile_url = "https://www.tiktok.com/@you"
instagram_profile_url = ""          # optional
inbox_dir = "inbox"                 # raw clips go here

[youtube]
default_privacy = "unlisted"        # start here, review, then publish
# language = "fr"                   # fr | en | ar: metadata + whisper language
```

Every key, with defaults, is in the [configuration reference](configuration.md). Any key can
also come from an environment variable such as `S2S_YOUTUBE__LANGUAGE=fr`.

## 3. Connect YouTube (OAuth)

The uploader uses the YouTube Data API v3 with an OAuth "Desktop app" client. You do this once.

!!! tip "Step-by-step guide with direct links"
    [Connect YouTube: create `client_secret.json`](youtube-oauth.md) walks through every console
    page and button, shows what you should see, and lists the common errors. The summary below is
    the short version.

1. **Create a project.** Open the [Google Cloud console](https://console.cloud.google.com/),
   create a project (for example `social2shorts`) and select it.
2. **Enable the API.** *APIs & Services → Library*, search **YouTube Data API v3**, click
   **Enable**.
3. **Configure the consent screen.** *APIs & Services → OAuth consent screen* (shown as
   *Google Auth Platform* in newer consoles):
    - User type **External**, app name, your support and developer email.
    - Under **Audience**, add the Google account that owns the channel as a **test user**.
    - Then click **Publish app** to move it to **In production**. In *Testing* mode Google
      expires refresh tokens after 7 days, which breaks unattended runs. An unverified app in
      production is fine for your own account: you will see a "Google hasn't verified this
      app" screen once; choose *Advanced → Go to ...*.
4. **Create the client.** *APIs & Services → Credentials → Create credentials → OAuth client
   ID*, application type **Desktop app**. Download the JSON and save it as
   `secrets/client_secret.json` (the path is `[youtube] client_secrets`).
5. **Authorize.**

    ```bash
    uv run social2shorts auth
    ```

    A browser opens. Pick the account, then the channel if it is a brand account. The app
    asks for two scopes: `youtube.upload` (uploads) and `youtube.force-ssl` (privacy changes
    and the post-upload check). The token is written to `secrets/token.json` with owner-only
    permissions. `secrets/` is in `.gitignore`.

!!! warning "Unaudited projects upload private only"
    YouTube locks every video uploaded through an **unaudited** API project created after
    28 July 2020 to **private**, and drops `publishAt`. Unlisted, public and scheduled
    uploads only work after your project passes the YouTube API compliance audit. Request it
    with the *YouTube API Services: Audit and Quota Extension Form*; it takes weeks.

    Until then social2shorts still uploads. It logs a warning when YouTube applied a different
    privacy than requested and stores what was really applied.

### Quota

The default allocation is 10,000 units per day, reset at midnight Pacific time. The config
assumes `videos.insert` costs 1,600 units, plus 400 for the subtitle track, so about **5 uploads
per day**. A separate cap on
`videos.insert` calls (`max_uploads_per_day`, 100) is enforced too. Check *APIs & Services →
YouTube Data API v3 → Quotas* in your project and adjust `[youtube] upload_cost_units` and
`daily_quota_units` to match. See [Publishing and quota](use-cases/publishing.md) for the math.

## 4. Gemini key (optional)

Titles, descriptions, tags and the on-screen hook line are written by Gemini.

1. Create a key in [Google AI Studio](https://aistudio.google.com/apikey).
2. Export it before running:

    === "Windows"

        ```powershell
        setx GEMINI_API_KEY "your-key"     # new terminals see it
        ```

    === "macOS / Linux"

        ```bash
        export GEMINI_API_KEY="your-key"
        ```

`GOOGLE_API_KEY` works too. The model is `gemini_model` (default `gemini-2.5-flash`). Only
your own public caption and the transcript are sent.

Without a key the run logs a warning and falls back to the caption, or to a title composed
per video from word pools (time of day from the file name or post date, keywords from the
transcript). No hook card is drawn in that case, because the hook line comes from the LLM.

## 5. First dry run

```bash
mkdir -p inbox
cp /path/to/a/raw/clip.mp4 inbox/      # at least 12 s long
uv run social2shorts run --source raw --dry-run --limit 1
```

The render lands in `work/renders/<item>.mp4`. Nothing touches YouTube. Watch it, tune the
[`[montage]`](configuration.md#montage) section if needed, then do a real upload:

```bash
uv run social2shorts run --source raw --limit 1     # unlisted by default
uv run social2shorts status
```

Next: pick your job in [Use cases](use-cases/index.md).
