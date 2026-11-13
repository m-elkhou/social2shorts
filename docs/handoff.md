# Handoff

Where the project stands, for whoever picks it up next. Updated 2026-11-13.

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
  units, which an upload day needs too.
- **Identity.** The repository is public. Since 2026-11-11 the live `config.toml` (channel handle, accounts,
  signature with the Instagram) is git-ignored, and the example config, docs, tests and code use neutral
  placeholders. The git **history** still holds the old texts; rewriting it is the owner's call.

## Open decisions (owner)

- Whether to rewrite the descriptions of the Shorts already public.
- How trending music is licensed for the montages (claims through the API, in-app Shorts sounds, or a licensed
  library).

See the [TODO](todo.md) for the ordered list.
