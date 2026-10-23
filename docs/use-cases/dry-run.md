# Dry run previews

A dry run does everything except talk to YouTube: dedupe, prepare, transcribe, metadata,
render. Use it to check the look of the Shorts and tune `[montage]` before spending quota.

```bash
uv run social2shorts run --dry-run --limit 3                 # catalog and raw, alternating
uv run social2shorts run --source raw --dry-run --limit 10
uv run social2shorts -v run --source raw --dry-run --limit 1  # verbose: shows the ffmpeg command
```

Renders are written to `work/renders/<item key>.mp4`. With `--dry-run`, `--limit` counts
renders instead of uploads.

```text
             Dry run (nothing uploaded)
 result     count  items
 rendered       3  raw-0f3a9c1d2b4e5f60, raw-91c2..., 7667654234271255828
```

The log line for each render shows the title that would be used:

```text
Rendered raw-0f3a9c1d2b4e5f60 -> work/renders/raw-0f3a9c1d2b4e5f60.mp4 ('Took the long way home')
```

## Things to know

- **No quota check, no guard sweep.** A dry run never reads or spends quota.
- **Same lock.** A dry run cannot start while a real run is working, and the other way round.
- **Items move to `rendered`.** A second dry run skips them. The next real run processes them
  again from scratch (and re-renders), so changing `[montage]` between the dry run and the
  real run is safe.
- **Review routing still happens.** A licensed-music post still goes to the review queue
  during a dry run.
- **Gemini and Whisper are real.** A dry run spends Gemini calls and CPU time.

## A tuning loop

```bash
# try a different look on the same clips
rm work/renders/*.mp4
S2S_MONTAGE__CAPTIONS=minimal S2S_MONTAGE__TARGET_DURATION_S=25 \
  uv run social2shorts run --source raw --dry-run --limit 3
```

Because rendered items are skipped by later dry runs, use a throwaway database for repeated
experiments:

```bash
S2S_DB_PATH=work/preview.sqlite uv run social2shorts run --source raw --dry-run --limit 3
```
