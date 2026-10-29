# Running locally

Requirements: Python 3.12+ (the system Python, no virtualenv) and Node.js for the website.

## Backend

```bash
pip install -r requirements.txt              # once, and after dependencies change
set PYTHONPATH=src                           # PowerShell: $env:PYTHONPATH="src"; bash: export PYTHONPATH=src
python -m partylinks serve                   # API + website on http://localhost:8000
python -m partylinks scrape                  # every enabled source, once
python -m partylinks scrape goout zygo       # only these sources
python -m partylinks sources                 # list configured sources
python -m partylinks cleanup                 # run retention now
python -m partylinks chillz-links            # a page of Chillz links to open and save into the inbox
python -m partylinks artists --help          # curate the artist dictionary
```

The first start builds `data/geonames-<version>.json` (the GeoNames cities the app uses, read once from
geonamescache's 96 MB of city files, a few seconds); every later start reads only that file.

## Database

PostgreSQL, with nothing to install: with no `PARTYLINKS_DATABASE__URL`, the app starts an embedded
PostgreSQL server (`pgserver`, in `requirements.txt`) in `data/pgdata`, with a database `partylinks`
(`localpg.py`), and stops it when the last process using it exits. A fresh local database is empty:
run `scrape` for some events. To use another server, set `PARTYLINKS_DATABASE__URL=postgresql://...`.

The scheduler is **off locally** (`scheduler.enabled: false`), so `serve` never scrapes on its own.
Only Railway scrapes on a schedule. Use `scrape` to run a source by hand.

To try a source without touching your local database, give it a throwaway database on the local
server (created on first use):

```bash
PARTYLINKS_DATABASE__LOCAL_DATABASE=scratch python -m partylinks scrape issta
```

Interactive API docs at `/docs` are off by default (`security.api_docs`).

## Accounts and email locally

Settings and secrets are read from `.env` in the repo root (gitignored; `.env.example` shows the format).
Google sign-in works on localhost when `.env` has `PARTYLINKS_ACCOUNTS__GOOGLE_CLIENT_ID`,
`PARTYLINKS_ACCOUNTS__GOOGLE_CLIENT_SECRET` and `PARTYLINKS_ACCOUNTS__SESSION_SECRET` (and
`PARTYLINKS_ACCOUNTS__ADMIN_EMAILS` to sign in to `/admin` with Google). The OAuth client allows
`http://localhost:8000/auth/google/callback`, so use port 8000. Without those variables sign-in is off: the
site shows no account UI, and the save bell keeps saved events on the device (localStorage `spotx-saved`).

The alert matcher and the welcome, instant and digest emails are scheduler jobs, so with the scheduler off
they never run locally, even with `PARTYLINKS_MAIL__RESEND_API_KEY` set (only the admin's test send would go). To time a matcher pass at production size, on its own throwaway
local database: `python scripts/time_matcher.py [--events 28000] [--alerts 50000] [--new 500]`.

## Tests and lint

```bash
python -m pytest
ruff check src tests
uvx pyrefly check src tests --python-interpreter-path "$(python -c 'import sys; print(sys.executable)')"   # type check: 0 errors expected
```

Parser tests run against real pages saved under `tests/fixtures/<source>/`.

## Website (React + TypeScript, Hebrew / English)

Stack: Vite, React Router, TanStack Query, zustand, Leaflet. The map draws OpenFreeMap vector
tiles through MapLibre (`@maplibre/maplibre-gl-leaflet`), so labels follow the site language
(`name:he` / `name:en`, then the English or Latin name); raster OSM tiles write each place in its
local script (Lebanon and Syria in Arabic). The RTL text plugin shapes Hebrew labels. The CSP
allows `tiles.openfreemap.org` and `blob:` workers for it.

The footer shows `© <year> SPOTX · All rights reserved · v<short sha> · <build date>`. The SHA is
`RAILWAY_GIT_COMMIT_SHA` (a Docker build arg) on Railway, `git rev-parse HEAD` locally.

```bash
cd web && npm install
npm run dev        # http://localhost:5173, proxies the API on :8000, hot reload
npm run build      # into web/dist
```

`python -m partylinks serve` serves `web/dist`, so **rebuild after every change under `web/`**,
or localhost keeps showing the old site.

## Windows shortcuts (repo root)

| File | Does |
| --- | --- |
| `start.bat` | Builds the site if missing, then runs API + site on <http://localhost:8000/> |
| `build.bat` | `npm install` + `npm run build` into `web/dist` |
| `dev.bat` | API in its own window + the Vite dev server on <http://localhost:5173/> |
| `reload.bat` | Rebuilds the site, then serves it with auto-restart on code changes (`serve --reload`) |

## Logs

The same log format is used everywhere (app, uvicorn, access lines), configured under `logging:`.
The default level is **INFO**: server status, source runs, geocoding and warnings. Debug mode (`-v`,
`logging.debug: true` or the switch in `/admin`) adds every HTTP request and the HTTP client's lines
on top.

- **Server status:** a block at startup (version, URLs, database and its size, events, scheduler and
  next job, sources, proxy host, admin token set or not, logging), then one status line every
  `logging.status_every_minutes` (uptime, events, runs in the last 24 hours, scheduler, clicks), and
  a line at shutdown (`status.py`). Secrets never appear.

- **stdout:** INFO and below.
- **stderr:** WARNING and above.
- **File:** a rotating file at `data/logs/partylinks.log` (10 MB × 5).
- **JSON:** `format: json` writes one JSON object per line.

Each source run logs whether it goes through the proxy (`via <host>` or
`via direct connection (no proxy)`).
