# AGENTS.md — Wishlist Monitor

> **After any change, run `make lint-fix && make lint && make test` before committing.**
> `make lint-fix` auto-fixes ruff issues, `make lint` confirms formatting/lint/type
> checks are clean, and `make test` runs the pytest suite.

## Project Overview

A Python daemon that polls Amazon and Throne wishlists on a configurable schedule,
diffs items against a local SQLite database, and sends HTML email notifications for
additions, removals, and price changes.

## Language & Runtime

- Python 3.10+
- Virtual environment at `.venv/` — activate with `. .venv/bin/activate`
- Dependencies: `requirements.txt` (runtime), `requirements-dev.txt` (lint + type checking)

## Architecture

```
monitor.py          — entry point; daemon loop or single run (MODE=once|daemon)
core/
  models.py         — Item dataclass (shared across all fetchers)
  diff.py           — diff_items(): added / removed / price_changed logic
  storage.py        — SQLite persistence (items + events tables)
  emailer.py        — SMTP delivery via environment-variable config
  report_html.py    — Jinja2 HTML email builder
  logger.py         — root logger setup (idempotent, env-driven)
fetchers/
  amazon.py         — mobile HTML scraper with pagination + CAPTCHA handling
  throne.py         — three-strategy extractor: NEXT_DATA → JSON-LD → grid scan
templates/
  email_dark.html   — dark-mode email template
  email_light.html  — light-mode email template
```

## Lint Tooling

| Tool | Purpose | Command |
|------|---------|---------|
| ruff | style + import linting | `make ruff` |
| pylint | structural analysis | `make pylint` |
| mypy | type checking | `make mypy` |
| pytest | unit tests | `make test` |
| all lint | format + ruff + pylint + mypy | `make lint` |
| auto-fix | ruff --fix + format | `make lint-fix` |

Configuration lives in `pyproject.toml` (`[tool.mypy]`, `[tool.pylint.*]`).
Ruff uses its defaults (line length 88).

## Code Conventions

- **Docstrings**: every module, public function, and public class must have one.
- **Type hints**: all function parameters and return types must be annotated.
- **Exceptions**: never swallow silently. `broad-exception-caught` disables are
  permitted only on intentional catch-all handlers (defensive cleanup, retry loops);
  each must carry a `# pylint: disable=broad-exception-caught` comment.
- **Global state**: module-level mutable state must be `UPPER_CASE`. Use
  `# pylint: disable=global-statement` when mutating it.
- **Imports**: stdlib before third-party before local; enforced by ruff.
- **Logging**: use `get_logger(__name__)` from `core.logger`; never `print()`.
- **Verification**: after edits, run `make lint-fix && make lint && make test`.
- **Formatting**: ruff handles formatting; `make lint` includes
  `ruff format --check`.
- **Indentation preference**: use tabs over spaces for indentation in newly authored
  agent-facing instructions unless a file format or tool requires otherwise. Makefile
  recipe lines are the explicit exception and must use tabs.

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `MODE` | `daemon` | `once` for single run, `daemon` for continuous loop |
| `CONFIG_PATH` | `/data/config.json` | Wishlist config file |
| `DB_PATH` | `/data/wishlist_state.sqlite3` | SQLite database |
| `POLL_MINUTES` | `10` | Default poll interval |
| `REMOVAL_THRESHOLD` | `20` | Max removals before skipping diff (scrape guard) |
| `EMAIL_FROM` | — | Sender address |
| `SMTP_HOST` | — | SMTP server hostname |
| `SMTP_PORT` | `587` | SMTP port |
| `SMTP_USER` | — | SMTP username |
| `SMTP_PASS` | — | SMTP password |
| `SMTP_USE_SSL` | `false` | Use SMTP_SSL instead of STARTTLS |
| `EMAIL_TO` | — | Comma/semicolon-separated default recipients |
| `EMAIL_THEME` | `dark` | Email template theme (`dark` or `light`) |
| `LOG_LEVEL` | `INFO` | Root log level |
| `DEBUG_DIR` | `/data/debug_dumps` | Directory for HTML debug captures |

## Config File Format (`config.json`)

```json
{
  "wishlists": [
    {
      "platform": "amazon",
      "name": "My List",
      "identifier": "XXXXXXXXXX",
      "enabled": true,
      "poll_minutes": 30,
      "recipients": ["user@example.com"]
    },
    {
      "platform": "throne",
      "name": "Someone",
      "identifier": "username"
    }
  ]
}
```

## Adding a New Platform

1. Create `fetchers/<platform>.py` with a `fetch_items(identifier, wishlist_name)` function
   returning `tuple[list[Item], list[str]]`.
2. Register it in `fetchers/__init__.py` under `FETCHERS`.
3. Add a `_wishlist_url()` branch in `monitor.py` if applicable.
4. Ensure `make lint-fix && make lint && make test` passes before committing.

## Docker

- Image published to GHCR via `.github/workflows/docker-ghcr.yml`.
- The workflow runs `make lint` and `make test`; Docker publication only runs after
  both pass.
- Base image: `python:3.10-slim` (or newer slim).
- App lives at `/app`; data volume at `/data`.
