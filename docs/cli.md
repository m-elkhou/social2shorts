# CLI reference

```bash
uv run social2shorts [-v] COMMAND [OPTIONS]
```

| Global option | Description |
|---|---|
| `-v`, `--verbose` | Debug logging (shows the ffmpeg command line, analysis details). Goes before the command. |
| `--install-completion` / `--show-completion` | Shell completion. |
| `--help` | Help for the program or a command. |

Every command takes `-c`, `--config PATH` (default `config.toml`).

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Done (also when a run stops early on quota, a full schedule, a systemic failure or another run holding the lock). |
| 1 | Config missing or invalid, unknown item, bad `--original` path, nothing found, or a `publish` change failed. |
| 2 | YouTube authorization failed (`run` records an alert; `publish` asks you to run `auth`). |

## `auth`

Link the YouTube channel (opens a browser once) and clear auth alerts.

```bash
uv run social2shorts auth
```

Fails with exit 1 if `[youtube] client_secrets` does not exist. Otherwise runs the OAuth
consent flow if needed, refreshes the token if possible, saves it to `token_path`, and resolves
all open alerts.

## `discover`

List posts from the TikTok/Instagram profiles into the catalog.

| Option | Default | Description |
|---|---|---|
| `--limit INT` | none | Max posts per platform. |

```bash
uv run social2shorts discover --limit 50
```

Exits 1 if no post was found on any profile.

## `curate`

Show the best catalog posts (deduped across platforms), in processing order.

| Option | Default | Description |
|---|---|---|
| `--top INT` | `30` | How many rows to show. |

Shows id, platform, duration, views, score and caption. Reads the database only.

## `ingest`

Copy new phone originals (skips files already copied) and cache their fingerprints.

| Option | Default | Description |
|---|---|---|
| `--limit INT` | none | Max phone videos to list and copy. |

Without a phone (or off Windows) it logs a warning and indexes what is already in
`originals_dir`.

## `run`

Process videos end to end (catalog posts and/or raw inbox footage).

| Option | Default | Description |
|---|---|---|
| `--source catalog\|raw\|all` | `all` | Which videos to process. `all` alternates catalog and raw items. |
| `--limit INT` | `3` | Max uploads (renders with `--dry-run`). |
| `--privacy public\|unlisted\|private\|scheduled` | `[youtube] default_privacy` | Upload visibility. `scheduled` uploads private with `publishAt` at the next free slot. |
| `--continuous` / `--no-continuous` | off | Ignore `--limit`: upload until today's quota is spent. |
| `--dry-run` / `--no-dry-run` | off | Render only; never upload, never check quota. |

```bash
uv run social2shorts run --dry-run --limit 3
uv run social2shorts run --source raw --privacy scheduled --continuous
```

Prints a report with these buckets: uploaded, rendered, needs review, waiting for original,
already uploaded, not a video (photo slideshow), failed (will retry), made private (claim).
Exits 2 on an auth alert.

Only one run works at a time (lock file `work_dir/social2shorts.lock`). A second run stops at
once with "another run (pid N) has been working for M min; this run did nothing".

## `review`

List items waiting for a decision, or approve/reject one.

| Option | Description |
|---|---|
| `--approve KEY` | Approve an item: status `ready`, attempts reset, processed first next run. |
| `--original PATH` | With `--approve`: the original to use for an ambiguous match. Must exist. |
| `--reject KEY` | Skip the item for good (status `rejected`). |

```bash
uv run social2shorts review
uv run social2shorts review --approve 7667654234271255828 --original originals/VID_123_bsl.mp4
uv run social2shorts review --reject raw-0f3a9c1d2b4e5f60
```

`--approve` wins if both `--approve` and `--reject` are given.

## `publish`

Change the visibility of uploaded video(s), e.g. unlisted → public after review.

| Argument / option | Default | Description |
|---|---|---|
| `TARGET` | required | An item key, or `all` for every recorded upload. |
| `--privacy public\|unlisted\|private` | required | `scheduled` is refused: scheduling happens at upload time. |
| `--include-claimed` / `--no-include-claimed` | off | Also change videos the Content-ID guard made private. |
| `--include-scheduled` / `--no-include-scheduled` | off | Also change videos still waiting for their publish time (clears their `publishAt`). |

```bash
uv run social2shorts publish all --privacy public
uv run social2shorts publish raw-0f3a9c1d2b4e5f60 --privacy public --include-claimed
```

Each change costs 50 quota units. Changing to public or unlisted marks the item `uploaded`
and clears its review row (this is how you release a claimed video). Skipped uploads are
listed as "left unchanged" with the reason. Exits 1 if any change failed.

## `status`

Item counts, today's quota and what is scheduled next.

Shows open alerts first, then counts by source and status, units used today (Pacific day) and
how many uploads still fit, the next 5 scheduled publications, and the size of the review
queue.

## `schedule`

List planned publication times and the next free slots.

| Option | Default | Description |
|---|---|---|
| `--count INT` | `6` | Free slots to preview. |

Times are shown in `[schedule] timezone`.
