# AGENTS.md — Wishlist Monitor

> **After any change, run `make lintfix && make lint && make test` before committing.**
> `make lintfix` auto-fixes ruff issues, `make lint` confirms formatting/lint/type
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
| auto-fix | ruff --fix + format | `make lintfix` |

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
- **Verification**: after edits, run `make lintfix && make lint && make test`.
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
| `OBSERVATION_PRUNE_ENABLED` | `true` | Auto-prune old `item_observations` rows |
| `OBSERVATION_RETENTION_DAYS` | `120` | Days of item observation history to retain |

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
4. Ensure `make lintfix && make lint && make test` passes before committing.

## Git Workflow

- Never push commits directly to `master`. Always open a pull request from a feature/fix branch.
- Use squash merge strategy when merging pull requests.
- After merging any pull request, monitor the GitHub Actions workflow runs to confirm both CI (lint-and-test) and the Docker image release (Build and Publish Docker image to GHCR) pass. Do not report the task complete until both succeed.
- After the Docker release publishes a new tag, deploy it to the TrueNAS host as a mandatory final step (see Deployment below). The PR is not considered complete until the new image is running on the production stack.

## Deployment

Production runs on the TrueNAS SCALE host `truenas.windsofstorm.net` as the Compose-YAML app `wishlist-monitor`. After every merged PR, once the `Build and Publish Docker image to GHCR` workflow succeeds and tags a new release (e.g. `v1.2.17`), deploy it using the `truenas-app` wrapper:

1. Fetch tags locally to discover the new version:
   ```bash
   git fetch --tags origin && git tag --sort=-creatordate | head -1
   ```
2. Run the deploy via SSH (the script lives on the host at `/mnt/myzmirror/myzdset/morgan/bin/truenas-app` and requires `sudo` for `midclt`). **Always deploy to `:latest`** — do not pin to a versioned tag, so that truenas-updater can keep the app current with future releases (e.g. Dependabot bumps):
   ```bash
   ssh truenas.windsofstorm.net \
     "sudo /mnt/myzmirror/myzdset/morgan/bin/truenas-app classify wishlist-monitor && \
      sudo docker pull ghcr.io/jasmeralia/wishlist-monitor:latest && \
      sudo /mnt/myzmirror/myzdset/morgan/bin/truenas-app update-image wishlist-monitor wishlist-monitor ghcr.io/jasmeralia/wishlist-monitor:latest"
   ```
   - `classify` must report `COMPOSE YAML — safe to update` before proceeding.
   - `docker pull` ensures the host has the freshly published image before the update.
   - `update-image <app> <service> <image>` performs the rolling update; the service name is also `wishlist-monitor`.
3. Verify the running image matches the newly published version by comparing digests:
   ```bash
   ssh truenas.windsofstorm.net \
     "sudo docker inspect ghcr.io/jasmeralia/wishlist-monitor:latest --format '{{index .RepoDigests 0}}' && \
      sudo docker inspect ghcr.io/jasmeralia/wishlist-monitor:<NEW_TAG> --format '{{index .RepoDigests 0}}'"
   ```
   The two digests must match. Only then confirm the post-update state and mark the task complete.
4. Confirm the post-update JSON reports `"state": "RUNNING"` and `ghcr.io/jasmeralia/wishlist-monitor:latest` in `images`.

Refer to `~/git/truenas/AGENTS.md` for general TrueNAS stack-management rules (classification, safety, raw `midclt` usage).

## Docker

- Image published to GHCR via `.github/workflows/docker-ghcr.yml`.
- The workflow runs `make lint` and `make test`; Docker publication only runs after
  both pass.
- Base image: `python:3.10-slim` (or newer slim).
- App lives at `/app`; data volume at `/data`.
