# Handoff

Where the project stands, for whoever picks it up next. Updated 2026-11-11.

## State

- The pipeline publishes the owner's catalog posts and raw clips as Shorts, on a schedule, from a **continuous
  run** on the owner's PC. Nothing in this repository should be changed under a running process: work in a
  separate git worktree and let the owner restart the run.
- **Descriptions.** Since 2026-11-11 the metadata generator (`ai/metadata.py`) writes every description in a
  fixed order: teaser line, two short paragraphs of factual story, a question for the comments, the channel
  line. At upload, `publish/youtube.py` adds the signature (`description_signature` in `config.toml`, it holds
  the owner's Instagram) and the hashtag line (`#motovlog #bikelife`, `#Shorts` last), and **refuses** any
  description that breaks the strict rule (`description_problems`). See
  [YouTube and monetization safety](youtube-safety.md).
- The 48 Shorts scheduled on 2026-11-11 had their descriptions rewritten in this format through the API (only
  `snippet.description`; titles, tags and `publishAt` unchanged, each checked after the update). The previous
  texts are kept locally in `logs/youtube_descriptions_avant_20261111.json` (git-ignored).

## Watch out

- **Memory.** The PC has 16 GB. The continuous run with ffmpeg can hold about 7 GB; heavy jobs started next to it
  (voice models, Whisper) fail with Windows error 1455 ("paging file too small").
- **Quota.** A `videos.update` costs 50 units; rewriting 48 descriptions used about 2,500 of the 10,000 daily
  units, which an upload day needs too.
- **Identity.** The repository is public and tracked files still name the channel and its accounts
  (`config.toml`, `config.example.toml`, `docs/configuration.md`, `docs/research/original-design-fr.md`, test
  fixtures). New code reads the signature from the configuration only; cleaning the existing files (and
  possibly the history) is the owner's decision.

## Open decisions (owner)

- When to restart the continuous run so it uses the new generator.
- Whether to rewrite the descriptions of the Shorts already public.
- Whether to remove the channel's names from the tracked files of this public repository.

See the [TODO](todo.md) for the ordered list.
