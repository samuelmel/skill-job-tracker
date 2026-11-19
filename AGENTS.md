# AGENTS.md: rules for agents implementing connectors

Read this file before starting. The task prompt only says **batch, slugs and data dir**; everything else is here.

## Setup
- Git Bash: `export UV_CACHE_DIR="$PWD/.uvcache"` before any `uv`.
- Checks (all must pass at the end): `uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q`.
- Real verify (embedded Postgres, applies the migrations):
  `uv run python scripts/verify_local.py --data-dir .pgdata-verify-<x> <slug> [<slug> ...]`

## Pattern to copy (don't reinvent)
- Connector: `src/vaya_indexer/connectors/ashby.py`, `lever_co.py`, `workday.py` + helpers in `_common.py`, `pagination.py`, `jsonld.py` (JobPosting), `feeds.py` (RSS/Atom/XML).
- Tests: `tests/mocked/test_connectors_batch1.py`, `tests/unit/test_connectors_batch1_normalize.py`.
- The contract test (`tests/contract/`) discovers `connectors/fixtures/<name>.json` on its own.
- Research per platform (endpoint, pagination, fields, instances, robots): `docs/research/r1..r4.json`. Use only what is there, and confirm with a real request before coding.

## Files per batch
| Type | Owner | Rule |
|---|---|---|
| `connectors/<name>.py`, `connectors/fixtures/<name>.json` | the batch | free |
| `tests/mocked/test_connectors_<batch>.py`, `tests/unit/test_connectors_<batch>_normalize.py` | the batch | free |
| `connectors/registry.py`, `coverage/platforms.json`, `docs/connectors.md` | shared | small Edit only on your own slugs; re-read before each Edit; never rewrite the file |
| `.pgdata-verify-<x>/` | the batch | exclusive verify DB, never share it |
| `docs/coverage.*`, `.gitignore`, `_common.py`, core/ingestion/database | orchestrator | don't touch (if you need a change, report it) |

Don't run `coverage-report`, don't commit, don't create files in the repo root (scratch goes in a temp dir).

## Mandatory rules
- No fake implementations, invented endpoints or invented instances. A platform only counts if verify runs SUCCEEDED with ≥1 job.
- Respect robots.txt; use the project's `HttpClient` (identifiable UA, rate limit). Don't bypass auth, CAPTCHA or Cloudflare. No token/secret in code.
- `complete=False` whenever the listing may be truncated (cap, partial feed, pagination error). Missing fields = `None`.
- If it can't be verified, record the real reason in `platforms.json` (limitations/notes) and move on.
- Near the turn limit: finish whole connectors (with verify) instead of starting new ones.

## Final report (short)
Table `slug | status | instances | jobs | note`, list of files, check results with numbers, pending items.
