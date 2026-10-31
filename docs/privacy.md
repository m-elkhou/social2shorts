# Privacy policy

*Last updated: 31 October 2026*

social2shorts is an open-source command-line program. Each person runs their own copy on their own
computer, with their own Google Cloud project, Google account and YouTube channel. **There is no
social2shorts server or hosted service.** The maintainers receive no data from any copy of the
program.

## What the program accesses

| Data | Why | How |
|---|---|---|
| Your YouTube channel, through the YouTube Data API v3 | Upload your videos, change the visibility of the videos it uploaded, and check them for Content ID claims after upload | OAuth scopes `youtube.upload` and `youtube.force-ssl`, granted by you in your browser |
| Your own public TikTok and Instagram posts (caption, duration, view and like counts, audio track name) | Choose which posts to turn into Shorts | Public pages read with yt-dlp |
| Your own video files (phone originals and the inbox folder) | Build the Short | Read from your computer |

## Where data is stored

Everything stays **on your computer**: the OAuth token (`secrets/token.json`, owner-only
permissions), the local SQLite database, the copied originals and the temporary renders. Nothing is
stored elsewhere by social2shorts.

## What is sent to third parties

- **Google, YouTube Data API:** the rendered video, its title, description and tags, and its
  visibility settings, so that it can be published on your channel.
- **Google, Gemini API**, only if you set `GEMINI_API_KEY`: the post's caption and the speech
  transcript of the video, so that Gemini can write the title, description, tags and on-screen
  hook. Without a key, nothing is sent to Gemini.

Nothing else is sent anywhere. There are no analytics, no advertising and no tracking.

## Google API Services User Data Policy

social2shorts' use and transfer of information received from Google APIs adheres to the
[Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy){ target=_blank },
including the Limited Use requirements. Google user data is used only to provide the features
described above, for the account that granted access. It is not sold, not used for advertising and
not shared with anyone else.

## How to revoke access and delete data

1. Remove the program's access at
   [myaccount.google.com/permissions](https://myaccount.google.com/permissions){ target=_blank }.
2. Delete `secrets/token.json`, the database file (`db_path` in `config.toml`) and the `work/`
   and `originals/` folders.

Videos already uploaded stay on your channel until you delete them in
[YouTube Studio](https://studio.youtube.com){ target=_blank }.

## Contact

Open an issue on [GitHub](https://github.com/m-elkhou/social2shorts/issues){ target=_blank }.
