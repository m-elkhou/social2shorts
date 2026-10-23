# Review queue

Some decisions need a human. The pipeline never guesses on them: it parks the item, tells you
why, and moves on to the next one.

```bash
uv run social2shorts review
```

```text
                 Waiting for you (3)
 item                 kind       what to do                                     since
 7667654234271255828  audio      licensed music (Song - Artist). If you own ... 2026-10-23 09:12:01
 7667612345678901234  ambiguous  2 phone originals look like this post. Pick... 2026-10-23 09:14:40
 raw-0f3a9c1d2b4e5f60 error      gave up after 3 attempts: MontageError: ...    2026-10-23 10:02:13
```

## The four kinds

| Kind | Why it's here | What to do |
|---|---|---|
| `audio` | The catalog post uses a licensed track (yt-dlp `track` is not "original sound"). | If you own the rights or it is royalty-free: `review --approve KEY`. Otherwise `review --reject KEY`. |
| `ambiguous` | Several phone originals match the post. | Pick one: `review --approve KEY --original PATH`. The candidate paths are printed if you omit `--original`. |
| `error` | Processing failed 3 times. | Fix the cause (file moved, ffmpeg error...), then `review --approve KEY` for a fresh retry budget. |
| `claim` | The Content-ID guard made an uploaded video private. | Check YouTube Studio. If it's fine: `publish KEY`. If not: `review --reject KEY`. |

```mermaid
flowchart LR
    Q[(Review queue)] --> A{Decision}
    A -- "review --approve KEY" --> R[ready<br/>processed first next run]
    A -- "review --approve KEY --original PATH" --> R
    A -- "review --reject KEY" --> X[rejected<br/>never processed again]
    A -- "publish KEY<br/>(claims only)" --> U[uploaded]
```

## Commands

```bash
# Licensed music you have the rights to
uv run social2shorts review --approve 7667654234271255828

# Ambiguous match: choose the original
uv run social2shorts review --approve 7667612345678901234 --original originals/VID_20260412_bsl.mp4

# Failure fixed: retry
uv run social2shorts review --approve raw-0f3a9c1d2b4e5f60

# Never upload this one
uv run social2shorts review --reject raw-0f3a9c1d2b4e5f60
```

Approving an item:

- sets its status to `ready` and resets its attempt counter,
- removes it from the queue,
- and makes it the first candidate of the next run (approved catalog items skip the music
  gate).

Approving an item that is already on YouTube only clears the review row and reminds you to use
`publish` to change its visibility.

## Failures before the queue

A failure does not go to the queue at once. The item is counted, marked `failed` and retried on
the next runs. On the third failure (`MAX_ATTEMPTS = 3`) it is queued as `error`. The run
report shows retries under *failed (will retry)*.
