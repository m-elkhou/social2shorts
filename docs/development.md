# Development

## Repository layout

```text
social2shorts/
├── src/social2shorts/
│   ├── cli.py              # typer commands
│   ├── config.py           # Settings (TOML + S2S_* env)
│   ├── models.py           # domain models and enums
│   ├── ports.py            # Protocol interfaces
│   ├── pipeline.py         # run loop, strategies, process_item
│   ├── operations.py       # approve / reject / publish / status
│   ├── db.py               # SQLite Store + migrations
│   ├── curate.py  match.py  phash.py  media.py  renderer.py
│   ├── sources/            # yt-dlp catalog, phone (MTP), inbox
│   ├── ai/                 # whisper, Gemini metadata + humanize
│   ├── publish/            # YouTube, Content-ID guard, quota, scheduler
│   └── montage/            # probe, analyze, planner, style, captions, filters, render
├── tests/
│   ├── fakes.py            # fake adapters for every port
│   ├── conftest.py
│   ├── fixtures/           # yt-dlp JSON samples
│   ├── integration/        # end-to-end with real ffmpeg
│   └── montage/            # montage unit + integration tests
├── scripts/scheduled_run.ps1
├── docs/                   # this site (MkDocs Material)
├── config.example.toml
├── mkdocs.yml
└── pyproject.toml
```

## Setup

```bash
uv sync                 # runtime + dev group (pytest, pytest-cov, ruff)
uv sync --group docs    # add mkdocs-material and mkdocstrings
```

## Tests

Unit tests use the fakes in `tests/fakes.py`: no network, no YouTube, no ffmpeg. Tests that
run real ffmpeg are marked `integration`. `--strict-markers` is on, so a typo in a marker
fails the run.

```bash
uv run pytest -q                              # everything (needs ffmpeg)
uv run pytest -q -m "not integration"         # fast unit tests
uv run pytest -q -m integration               # ffmpeg end-to-end renders
uv run pytest -m "not integration" --cov=social2shorts --cov-report=term-missing
```

Any new test that calls ffmpeg (directly or through `montage.render`, `probe`, `analyze`,
`phash_file`) must carry `@pytest.mark.integration`. The Windows CI job runs only
`-m "not integration"`.

## Lint and format

```bash
uv run ruff check .
uv run ruff format --check .     # or `uv run ruff format .` to fix
```

Ruff runs rules `E, F, I, UP, B, SIM` with a line length of 100, targeting Python 3.12.
`# fmt: skip` is used on a few hand-aligned literals; keep it there.

## Docs

```bash
uv run --group docs mkdocs serve           # http://127.0.0.1:8000, live reload
uv run --group docs mkdocs build --strict  # what CI runs
```

The API pages are generated from docstrings by mkdocstrings (Google style). A broken
docstring reference fails the strict build.

## CI/CD

| Workflow | Trigger | Jobs |
|---|---|---|
| `.github/workflows/ci.yml` | push to `main`, any pull request, manual | `lint` (ruff check + format check), `test` (Ubuntu, ffmpeg, unit tests with coverage, `coverage.xml` artifact), `integration` (after `test`, ffmpeg end-to-end), `windows-smoke` (unit tests on Windows, checks the `pywin32` marker resolves), `docs` (strict MkDocs build) |
| `.github/workflows/docs.yml` | push to `main` touching `docs/**`, `mkdocs.yml` or `src/**`, manual | Builds the site and deploys it to GitHub Pages |

All jobs install with `uv sync --locked`, so a stale `uv.lock` fails CI. Run `uv lock` after
changing dependencies. Dependabot opens weekly updates for GitHub Actions and Python
dependencies.

To enable the docs site: *Settings → Pages → Build and deployment → Source: GitHub Actions*.
It is then served at <https://m-elkhou.github.io/social2shorts/>.

## Releases

There is no release automation yet. To cut a release:

1. Bump `version` in `pyproject.toml` and run `uv lock`.
2. Make sure CI is green on `main`.
3. Tag and push: `git tag v0.2.0 && git push origin v0.2.0`.
4. Create a GitHub release from the tag with the notable changes.

## Conventions

- **Commits**: Conventional Commits, e.g. `feat(montage): ...`, `fix(pipeline): ...`,
  `docs: ...`, `refactor(core): ...`.
- **Branches and PRs**: one topic per PR, using the PR template. CI must pass.
- **Architecture**: the core talks to the outside only through `ports.py`. New external
  systems get an adapter and a fake.
- **Docstrings**: Google style (`Args:`, `Returns:`, `Raises:`). Public functions have one.
- **Schema changes**: append a migration to `db.MIGRATIONS`; never edit an old one.
- **Determinism**: the montage planner and metadata picks must stay deterministic for the
  same input. Use seeds derived from content or item keys, never `random` without a seed.
- **Docs**: when you add a config key, a CLI option or a status, update
  [Configuration](configuration.md), [CLI](cli.md) or [Architecture](architecture.md) in the
  same PR.
