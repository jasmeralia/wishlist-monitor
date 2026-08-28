# AGENTS.md — Wishlist Monitor

> **After any change, run `make lintfix && make lint && make test` before committing.**
> `make lintfix` auto-fixes ruff issues, `make lint` confirms formatting/lint/type
> checks are clean, and `make test` runs the pytest suite.

## Project Overview

A Python daemon that polls Amazon and Throne wishlists, and Honey Birdette's live
US storefront for markdowns in configured categories/sizes, on a configurable
schedule. It diffs items against a local SQLite database and sends HTML email
notifications for additions, removals, and price changes.

## Language & Runtime

- Python 3.10+
- Virtual environment at `.venv/` — activate with `. .venv/bin/activate`
- Dependencies: `requirements.txt` (Docker image), `pyproject.toml` + `uv.lock` (native
  TrueNAS cron via `uv sync`); keep runtime deps in sync across both

## Architecture

```
monitor.py          — entry point; daemon loop or single run (MODE=once|daemon)
wishlist_monitor/   — `python -m wishlist_monitor` entry (native cron)
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
  honeybirdette.py  — Shopify /products.json catalog scan with category/size
                       markdown matching
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
| `PRICE_NOTIFY_THRESHOLD` | `20` | Percent price change required before notifying |
| `NOTIFY_ON_AVAILABILITY_CHANGE` | `true` | Notify when an item becomes unavailable or returns to availability |
| `NOTIFY_ON_PRICE_INCREASE` | `true` | Notify when a price increases (decreases always notify) |
| `HONEYBIRDETTE_MAX_PAGES` | `20` | Max `/products.json` pages fetched per Honey Birdette poll |
| `HONEYBIRDETTE_MIN_PRODUCTS` | `50` | Below this catalog size, the fetch fails closed (incomplete) |
| `HONEYBIRDETTE_PAGE_SLEEP_SECONDS` | `1` | Delay between successive catalog page fetches |
| `HONEYBIRDETTE_USER_AGENT` | Chrome-like UA string | User-Agent sent to the Honey Birdette storefront |

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
    },
    {
      "platform": "honeybirdette",
      "name": "Honey Birdette Sale Watch",
      "identifier": "us",
      "poll_minutes": 360,
      "options": {
        "sale_only": true,
        "matches": [
          {"type": "bra", "band": "34", "cup": "C"},
          {"type": "thong", "size": "M"},
          {"type": "sheers", "size": "M"}
        ]
      },
      "notifications": {
        "added": true,
        "removed": false,
        "price_decrease": true,
        "price_increase": false,
        "availability": true,
        "price_decrease_threshold_percent": 0
      }
    }
  ]
}
```

### Honey Birdette (`platform: "honeybirdette"`)

- `identifier`: a base storefront URL/domain (e.g. `"us.honeybirdette.com"`), or any
  other value (e.g. `"us"`) to use the default `https://us.honeybirdette.com`.
- `options.matches`: an array of category/size rules to watch. Each entry has a
  `type` of `"bra"`, `"thong"`, `"sheers"`, `"stockings"`, or `"hosiery"`
  (sheers/stockings/hosiery are all the same underlying storefront category).
  Bra entries require `band` and `cup` (e.g. `"34"` / `"C"`); all other types
  require `size` (e.g. `"M"`). Size/band values are matched case- and
  whitespace-insensitively; cup values additionally treat `-` and `/` as
  interchangeable separators (`"DD-E"` == `"DD/E"`).
- `options.sale_only` (default `true`): when true, only variants with a live
  markdown (`compare_at_price > price`) are tracked — full-price matches are
  ignored entirely. Set to `false` to track matching variants regardless of
  sale state.
- The fetcher discovers the entire live catalog via the storefront's public
  `/products.json` endpoint (there is no dependable sale-collection URL) and
  determines markdown state per-variant from `compare_at_price`. It never
  relies on search-engine results, which can surface stale products/pricing.
- Zero current matches is a normal state (no live sale in the configured
  sizes right now), not a scrape failure — the fetcher reports this via
  `FetchResult.allow_empty` so a sale ending is still recorded as a removal.

### Per-wishlist notification policy (`notifications`)

Any wishlist entry (not just Honey Birdette) may include a `notifications`
object to override which change types produce an email for that source,
without touching the global `NOTIFY_ON_*`/`PRICE_NOTIFY_THRESHOLD` env
defaults used elsewhere:

| Key | Default | Effect |
|-----|---------|--------|
| `added` | `true` | Include newly discovered items in the email |
| `removed` | `true` | Include items no longer present in the email |
| `price_decrease` | `true` | Include price decreases |
| `price_increase` | global `NOTIFY_ON_PRICE_INCREASE` | Include price increases |
| `availability` | global `NOTIFY_ON_AVAILABILITY_CHANGE` | Include availability-only flips |
| `price_decrease_threshold_percent` | global `PRICE_NOTIFY_THRESHOLD` | Percent decrease required to notify (increases still use the global threshold) |

`added`/`removed` are still saved to the database either way (so history and
readded-item diagnostics stay accurate); the toggles only affect what's
included in the notification email itself.

## Adding a New Platform

1. Create `fetchers/<platform>.py` with a
   `fetch_items(identifier, wishlist_name, options=None)` function
   returning a `core.models.FetchResult`.
2. Register it in `fetchers/__init__.py` under `FETCHERS`.
3. Add a `_wishlist_url()` branch in `monitor.py` if applicable.
4. Ensure `make lintfix && make lint && make test` passes before committing.

## Git Workflow

- Never push commits directly to `master`. Always open a pull request from a feature/fix branch.
- Use squash merge strategy when merging pull requests.
- After merging any pull request, monitor the GitHub Actions workflow runs to confirm both CI (lint-and-test) and the Docker image release (Build and Publish Docker image to GHCR) pass. Do not report the task complete until both succeed.
- After the Docker release publishes a new tag, deploy it to the TrueNAS host as a mandatory final step (see Deployment below). The PR is not considered complete until the new image is running on the production stack.

## Deployment

Production runs on the TrueNAS SCALE host `truenas.windsofstorm.net`. As of Odoo
#467 (epic #466), the primary deployment is a **native TrueNAS cron job** via the
shared `truenas-cron` wrapper — not the Compose YAML app. The Docker image remains
published for other environments and until the Compose app is decommissioned.

### Native cron (production)

Data, config, and secrets live on the pool at `/mnt/myzmirror/wishlist-monitor/`
(same paths the Compose app used on the host side). The wrapper auto-sources
`/mnt/myzmirror/wishlist-monitor/.env` when present — use `.env.native-cron.example`
as a template; **paths must be host `/mnt/...` values, not container `/data/...`**.

Cron entry (via `midclt cronjob.create`, user `morgan`, schedule `7 */3 * * *`):

```bash
/mnt/myzmirror/truenas-cron/bin/truenas-cron run --mode uv \
  --app jasmeralia/wishlist-monitor:master -- \
  env MODE=once uv run python -m wishlist_monitor
```

- Skip file: `/mnt/myzmirror/truenas-cron/skips/jasmeralia/wishlist-monitor.skip`
- Wrapper JSON-lines log: `/mnt/myzmirror/truenas-cron/logs/jasmeralia/wishlist-monitor.log`
  (ingested to OpenSearch `native-cron-logs` by the shared fluent-bit pipeline in
  [cam-watcher-tools](https://github.com/jasmeralia/cam-watcher-tools))
- `pyproject.toml` + `uv.lock` supply dependencies for `uv sync` in the git checkout
  under `/mnt/myzmirror/truenas-cron/git/jasmeralia/wishlist-monitor/`
- `storage.py` persists state to `DB_PATH` (SQLite on the dataset); state survives
  between cron invocations

See `~/git/truenas-typhoon/AGENTS.md` for `truenas-cron` wrapper details and cron management.

### Docker Compose (legacy / alternate)

The Compose YAML app `wishlist-monitor` may still run until cutover is complete.
After every merged PR that touches the Docker image, once the `Build and Publish
Docker image to GHCR` workflow succeeds and tags a new release (e.g. `v1.2.17`),
deploy it using the `truenas-app` wrapper:

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

Refer to `~/git/truenas-typhoon/AGENTS.md` for general TrueNAS stack-management rules (classification, safety, raw `midclt` usage).

## Docker

- Image published to GHCR via `.github/workflows/docker-ghcr.yml`.
- The workflow runs `make lint` and `make test`; Docker publication only runs after
  both pass.
- Base image: `python:3.10-slim` (or newer slim).
- App lives at `/app`; data volume at `/data`.
