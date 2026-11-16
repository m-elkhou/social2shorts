# TODO

Short, ordered list of what is left. The longer view is in the [roadmap](roadmap.md); the state of the
project for the next person (or session) is in the [handoff](handoff.md).

## Now

- [x] Rewrite every thin description of the channel (2026-11-14, 122 Shorts) and move the schedule to one video
  an hour from 09:00 to 00:00 ([handoff](handoff.md)).
- [ ] The queue is empty after 15 November 00:00: unblock the items waiting in the audio review and the failed
  raw items, or nothing more gets scheduled.
- [ ] Set the right audio language on the 105 Shorts that declare `en-US` (owner's choice of the value).
- [ ] Check the first Shorts written by the new generator: teaser line, two paragraphs, a question, the channel
  line, the Instagram signature, hashtags last.
- [ ] New montage direction (owner, 2026-11-13): trending energetic music and a narrated monologue in the owner's
  cloned voice, like the reference Short `X__udVaBnUQ`. The work is in motoride-montage (its next-session brief);
  this repository supplies the voice references, faster-whisper and the upload rules ([handoff](handoff.md)).

## Next

- [ ] Rewrite the descriptions of the Shorts already **published** with the short format (the 48 scheduled ones
  were rewritten on 2026-11-11 through the API, titles and schedule untouched).
- [ ] Measure: comments per Short and average view duration, before and after the new descriptions.
- [ ] Keep the anonymity rules of the montage engine in mind for raw footage: no visible face, no other person,
  no readable plate or sticker (see the motoride-montage privacy gate).

## Done

- [x] The hourly scheduled task (`\py\social2shorts`) runs a fresh `uv run` from the main checkout, which was
  updated on 2026-11-11: every run since uses the new generator.
- [x] Channel handle, accounts and name removed from tracked files; `config.toml` git-ignored (2026-11-11).
- [x] Strict description rule, enforced at upload: detailed text, signature with the Instagram, hashtags last
  (2026-11-10).
- [x] Viral description format in the generator: teaser, story, question, channel line, `#motovlog #bikelife`
  (2026-11-11).
