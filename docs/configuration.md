# Configuration reference

Settings come from a TOML file (`config.toml` by default, `-c` to change it), validated by
pydantic (`social2shorts.config.Settings`). An invalid value stops the command with a clear
message and exit code 1. Start from `config.example.toml`.

## Environment overrides

Every key can be set with an environment variable. Environment variables **win** over the
TOML file.

| Rule | Example |
|---|---|
| Prefix `S2S_` | `S2S_DB_PATH=work/test.sqlite` |
| Top-level key: `S2S_<KEY>` | `S2S_WHISPER_MODEL=medium` |
| Key in a section: `S2S_<SECTION>__<KEY>` (double underscore) | `S2S_YOUTUBE__LANGUAGE=fr` |
| Names are case-insensitive | `s2s_db_path` works too |
| Lists are JSON | `S2S_SCHEDULE__PUBLISH_SLOTS='["09:00","19:30"]'` |
| A nested override changes only that key; the rest of the section comes from the file | `S2S_SCHEDULE__MAX_PER_DAY=1` keeps your `publish_slots` |

`.env` files are not read. `GEMINI_API_KEY` / `GOOGLE_API_KEY` are read directly by the Gemini
client, not through `Settings`.

Older flat config files are upgraded on load: top-level `tiktok_profile_url`,
`instagram_profile_url`, `instagram_cookies`, `inbox_dir` move to `[sources]`, and
`default_privacy` moves to `[youtube]` (a value already in the section wins). An empty
`gemini_model = ""` falls back to the default.

## Top level

| Key | Type | Default | Description |
|---|---|---|---|
| `youtube_handle` | str | `""` | Your channel handle, for reference. Not used by the pipeline. |
| `originals_dir` | str | `originals` | Local cache of phone originals. |
| `work_dir` | str | `work` | References (`work/refs`) and renders (`work/renders`). Renders are deleted after upload. |
| `db_path` | str | `social2shorts.sqlite` | SQLite database. |
| `min_duration_s` | float | `12.0` | Catalog posts shorter than this are skipped (micro-clips). Posts without a duration (slideshows) are always skipped. |
| `duration_tol_s` | float | `1.0` | Matching: max difference between post and original durations. |
| `phash_threshold` | int | `10` | Matching: max Hamming distance between frame pHashes (64-bit). |
| `phash_frames` | int | `6` | Frames sampled per video for pHashes. |
| `phone_name_match` | str | `""` | Part of the phone's name in *This PC* (Windows MTP). |
| `phone_video_folders` | list[str] | `[]` | Folders on the phone to copy from, e.g. `["Movies/Edits"]`. Empty disables phone sync. |
| `gemini_model` | str | `gemini-2.5-flash` | Model for metadata. Needs `GEMINI_API_KEY`. |
| `whisper_model` | str | `small` | faster-whisper model size (`tiny`, `base`, `small`, `medium`, `large-v3`...). |

## `[sources]`

| Key | Type | Default | Description |
|---|---|---|---|
| `tiktok_profile_url` | str | `""` | TikTok profile to discover. Empty skips TikTok. |
| `instagram_profile_url` | str | `""` | Instagram profile to discover. Empty skips Instagram. |
| `instagram_cookies` | str \| null | `null` | Netscape cookies file for Instagram discovery. |
| `inbox_dir` | str | `inbox` | Folder scanned (recursively) for raw videos. |
| `prefer_platform` | `tiktok` \| `instagram` | `tiktok` | Which copy to keep when a post is on both platforms. |

## `[youtube]`

| Key | Type | Default | Description |
|---|---|---|---|
| `client_secrets` | str | `secrets/client_secret.json` | OAuth "Desktop app" client JSON. |
| `token_path` | str | `secrets/token.json` | Stored token (written with owner-only permissions). |
| `category_id` | str | `"2"` | YouTube category. `2` is Autos & Vehicles. |
| `default_privacy` | `public` \| `unlisted` \| `private` \| `scheduled` | `unlisted` | Used by `run` when `--privacy` is not given. |
| `daily_quota_units` | int > 0 | `10000` | Units per Pacific day. |
| `upload_cost_units` | int > 0 | `1600` | Units charged per upload. Check your Cloud console. |
| `max_uploads_per_day` | int > 0 | `100` | Cap on `videos.insert` calls per day. |
| `made_for_kids` | bool | `false` | `status.selfDeclaredMadeForKids` on every upload and privacy change. |
| `contains_synthetic_media` | bool | `false` | `status.containsSyntheticMedia`. Set `true` if renders add realistic AI-generated content. |
| `language` | str \| null | `null` | e.g. `fr`: sets `defaultLanguage` and `defaultAudioLanguage`, the metadata language and the Whisper language. Unset: Whisper auto-detects and metadata follows the caption. |

## `[schedule]`

Used with `--privacy scheduled`, and by `status` / `schedule` to show free slots.

| Key | Type | Default | Description |
|---|---|---|---|
| `timezone` | str (IANA) | `Africa/Casablanca` | Time zone of the slots. |
| `publish_slots` | list of `HH:MM` | `["12:00", "18:00"]` | Daily publication times. Must be non-empty; sorted and deduplicated on load. |
| `max_per_day` | int > 0 | `2` | Max publications per local day. |
| `min_lead_minutes` | int ≥ 15 | `60` | A slot must be at least this far in the future. |

## `[montage]`

Rendering knobs. The full table with explanations is on the
[montage page](montage.md#montage-settings).

| Key | Default | Key | Default |
|---|---|---|---|
| `mode` | `highlight` (overridden per source) | `caption_highlight` | `&H0000E5FF` |
| `width` | `1080` | `hook_text` | `true` |
| `height` | `1920` | `loudness_lufs` | `-14.0` |
| `fps` | `30` | `color_grade` | `true` |
| `target_duration_s` | `35.0` | `punch_in` | `true` |
| `min_duration_s` | `12.0` | `progress_bar` | `true` |
| `hook_s` | `2.0` | `fade_s` | `0.2` |
| `reframe` | `smart` | `music_dir` | `null` |
| `captions` | `karaoke` | `music_volume_db` | `-20.0` |
| `caption_font` | `Arial Black` | `crf` | `18` |
| `caption_size` | `88` | `preset` | `slow` |

## Full example

```toml
youtube_handle = "@motoride-mh"
originals_dir = "originals"
work_dir = "work"
db_path = "social2shorts.sqlite"
min_duration_s = 12.0
duration_tol_s = 1.0
phash_threshold = 10
phash_frames = 6
phone_name_match = "S23"
phone_video_folders = ["Movies/Edits", "Movies/Instagram"]
gemini_model = "gemini-2.5-flash"
whisper_model = "small"

[sources]
tiktok_profile_url = "https://www.tiktok.com/@motoride.mh"
instagram_profile_url = ""
# instagram_cookies = "cookies.txt"
inbox_dir = "inbox"
prefer_platform = "tiktok"

[youtube]
client_secrets = "secrets/client_secret.json"
token_path = "secrets/token.json"
category_id = "2"
default_privacy = "unlisted"
daily_quota_units = 10000
upload_cost_units = 1600
max_uploads_per_day = 100
made_for_kids = false
contains_synthetic_media = false
# language = "fr"

[schedule]
timezone = "Africa/Casablanca"
publish_slots = ["12:00", "18:00"]
max_per_day = 2
min_lead_minutes = 60

[montage]
target_duration_s = 35
captions = "karaoke"
```
