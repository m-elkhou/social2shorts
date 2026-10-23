## Summary

<!-- What does this change and why? One or two sentences. -->

## Changes

-

## Tests

<!-- How did you check it? Paste the commands you ran. -->

- [ ] `uv run pytest -q -m "not integration"`
- [ ] `uv run pytest -q -m integration` (if rendering or ffmpeg code changed)

## Checklist

- [ ] `uv run ruff check . && uv run ruff format --check .` passes
- [ ] New ffmpeg-dependent tests are marked `@pytest.mark.integration`
- [ ] Docs updated (config keys, CLI options, statuses) and `uv run --group docs mkdocs build --strict` passes
- [ ] Schema changes go through a new migration in `db.MIGRATIONS`
- [ ] No secrets, tokens or personal media committed
