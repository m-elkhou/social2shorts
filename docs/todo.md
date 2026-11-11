# TODO

Short, ordered list of what is left. The longer view is in the [roadmap](roadmap.md); the state of the
project for the next person (or session) is in the [handoff](handoff.md).

## Now

- [ ] **Restart the continuous run** (`social2shorts run --continuous`) once it is idle: a process started before
  2026-11-11 keeps the old generator in memory and still writes the short descriptions.
- [ ] Check the first Shorts written by the new generator: teaser line, two paragraphs, a question, the channel
  line, the Instagram signature, hashtags last.

## Next

- [ ] Rewrite the descriptions of the Shorts already **published** with the short format (the 48 scheduled ones
  were rewritten on 2026-11-11 through the API, titles and schedule untouched).
- [ ] Measure: comments per Short and average view duration, before and after the new descriptions.
- [ ] Keep the anonymity rules of the montage engine in mind for raw footage: no visible face, no other person,
  no readable plate or sticker (see the motoride-montage privacy gate).

## Done

- [x] Strict description rule, enforced at upload: detailed text, signature with the Instagram, hashtags last
  (2026-11-10).
- [x] Viral description format in the generator: teaser, story, question, channel line, `#motovlog #bikelife`
  (2026-11-11).
