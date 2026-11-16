# Handoff

Where the project stands, for whoever picks it up next. Updated 2026-11-16.

## State

- The pipeline publishes the owner's catalog posts and raw clips as Shorts, on a schedule: the Windows task
  `\py\social2shorts` starts a fresh `uv run social2shorts run --continuous` every hour at hh:05 from the main
  checkout. Updating that checkout between two runs is how a change goes live; work in a separate git worktree
  and never stop a run in progress.
- **Descriptions.** Since 2026-11-11 the metadata generator (`ai/metadata.py`) writes every description in a
  fixed order: teaser line, two short paragraphs of factual story, a question for the comments, the channel
  line. At upload, `publish/youtube.py` adds the signature (`description_signature` in `config.toml`, it holds
  the owner's Instagram) and the hashtag line (`#motovlog #bikelife`, `#Shorts` last), and **refuses** any
  description that breaks the strict rule (`description_problems`). See
  [YouTube and monetization safety](youtube-safety.md).
- The 48 Shorts scheduled on 2026-11-11 had their descriptions rewritten in this format through the API (only
  `snippet.description`; titles, tags and `publishAt` unchanged, each checked after the update). The previous
  texts are kept locally in `logs/youtube_descriptions_avant_20261111.json` (git-ignored).
- **All thin descriptions rewritten on 2026-11-14** (owner's request). The channel held 152 videos; 122 Shorts
  carried one or two sentences about the video (they passed the rule only thanks to the channel line). Each got
  the full format (teaser, two paragraphs, question, channel line, signature, hashtags last; 676 to 854
  characters), written from what was already known of the video (title, the scene sentence of the old
  description, the original caption, the tags) with no new fact, checked by a script and by
  `description_problems`, then verified live. The generator's Gemini models could not be used: their free quota
  is about 20 requests a day each. Tools and backups are outside the repository, in
  `C:\workdir\_s2s_cb\channel\` (`chan_state.py` snapshot, `chan_apply.py` with a dry run and a
  before/after journal, `chan_verify.py`; `state_20261114_1530.json` holds every previous description).
  One description still breaks the rule: the long English video `JeFDoglF2SY` (not a Short, left as it is).
- **Schedule, 2026-11-14** (owner's request): one video every hour from 09:00 to 00:00. The local `config.toml`
  now has 16 hourly `publish_slots` (`00:00`, then `09:00` to `23:00`) and `max_per_day = 16`; the 9 videos that
  were scheduled for 18 and 19 November were moved to 16:00, 17:00 ... 00:00 (Casablanca time) of 14 November,
  and their `uploads.publish_at` rows updated. Nothing is scheduled after that: the pipeline has uploaded
  nothing since 11 November (71 raw items failed, about 100 wait in the audio review).

## Next: montage with trending music and a narration (owner, 2026-11-13)

The montage work happens in the sibling engine, motoride-montage; its
[next-session brief](https://m-elkhou.github.io/motoride-montage/next-session/) is the starting point. What this
repository brings to it:

- **The reference Short**: `X__udVaBnUQ`, the owner's own TikTok post `7483999753433550085` re-published from
  the catalog on 2026-11-11 (52 s). Its sound is a spoken French monologue over the ride ("Quand on est à 150 à
  l'heure..."); the source copy is `work/copies/7483999753433550085.mp4` and the transcript
  `work/subtitles/7483999753433550085.srt` (both local).
- **The owner's voice references** for cloning: `work/voiceover/ref_fr2_full.wav` (125 s), `ref_fr_full.wav`
  (60 s), `ref_fr3_full.wav` (26 s), `ref_fr2_expr.wav` (13 s), and English ones (`ref_en*.wav`).
- **faster-whisper** in this repository's `.venv`, used by motoride-montage to re-listen to every voice take
  (CPU only: the CUDA libraries of CTranslate2 are missing).
- The YouTube client, the OAuth token and the upload rules (descriptions, quota, schedule).

## Watch out

- **Memory.** The PC has 16 GB. The continuous run with ffmpeg can hold about 7 GB; heavy jobs started next to it
  (voice models, Whisper) fail with Windows error 1455 ("paging file too small").
- **Quota.** A `videos.update` costs 50 units; rewriting 48 descriptions used about 2,500 of the 10,000 daily
  units, which an upload day needs too (131 videos on 14 November: 6,550 units).
- **Bulk `videos.update`.** The uploads playlist lists some videos twice (179 entries for 152 videos):
  de-duplicate before writing. `defaultAudioLanguage` comes back as `und` on some videos and is refused when
  sent back (HTTP 400, "request metadata is invalid"). A snippet update on a video with no audio language sets
  it to `en-US`: 45 videos changed that way on 14 November (not corrected; the right value per video is not
  known).
- **Time zone.** `tzdata` 2026.5 gives `Africa/Casablanca` as UTC+0 in November 2026 (UTC+1 in summer), one hour
  behind Paris: a slot written `09:00` is 10:00 in Paris.
- **Token.** The scripts above copy `secrets/token.json` to a temporary folder and never rewrite it; they must
  not run between hh:04 and hh:12.
- **A video that is not in the ledger (2026-11-16).** One private Short (its id is kept in the owner's local
  notes, not in this public repository) was uploaded by hand through
  the signed-in Studio (the narrated pilot of the montage project, voice only), on the owner's request. It did not
  come from the queue and has no row in `uploads`: tools that compare the channel with the ledger will see one
  extra private video. Browser uploads count against the same daily channel limit as the pipeline's.
- **Music the channel may use (2026-11-16).** The whole YouTube Audio Library was listed from Studio: 7,160
  tracks, 6,251 with no attribution and 909 under Creative Commons Attribution 4.0 (credit text to paste in the
  description). Lists and tools: `C:/workdir/_s2s_cb/channel/musiques_youtube/`. Checked on YouTube's help the
  same day: crediting a commercial song gives no right to use it; Creator Music is for Partner Programme creators
  in the United States and not for Shorts; the audio of an uploaded video can no longer be changed in the mobile
  app.
- **Identity.** The repository is public. Since 2026-11-11 the live `config.toml` (channel handle, accounts,
  signature with the Instagram) is git-ignored, and the example config, docs, tests and code use neutral
  placeholders. The git **history** still holds the old texts; rewriting it is the owner's call.

## Open decisions (owner)

- Which audio language to declare on the Shorts (105 now say `en-US`; the speech is Moroccan Arabic or French,
  or there is none).
- Whether the hourly slots are meant in Casablanca time (as configured) or in Paris time.
- How trending music is licensed for the montages (claims through the API, in-app Shorts sounds, or a licensed
  library).

See the [TODO](todo.md) for the ordered list.
