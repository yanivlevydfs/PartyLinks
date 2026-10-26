# Configuration

Every setting lives in `config/settings.yaml`. There are no hard-coded URLs, limits or schedules in
the code. A different file can be used with `PARTYLINKS_CONFIG=/path/to/settings.yaml`.

## Environment overrides

Any key can be overridden with a `PARTYLINKS_` variable, using `__` for nesting. Variables are read
from the environment or from `.env` in the repo root.

```bash
PARTYLINKS_DATABASE__URL=postgresql+psycopg://user:pass@host/partylinks
PARTYLINKS_SOURCES__ZYGO__ENABLED=false
PARTYLINKS_HTTP__CONCURRENCY=16
PARTYLINKS_MEDIA__STORAGE=filesystem
```

Secrets (admin token, proxy credentials, access headers) belong in variables only.

## Sections

| Section | What it controls |
| --- | --- |
| `timezone` | App time zone (`Asia/Jerusalem`): local dates, cron times, display |
| `database` | Database URL (SQLite by default, in `data/`) |
| `logging` | Level, text or JSON, access log, rotating file |
| `site` | `base_url`, brand name (`SPOTX`), accessibility and privacy contacts |
| `utm` | UTM tags on ticket links: `enabled`, `source`, `medium` (see [ticket links](ticket-links.md)) |
| `security` | Trusted proxies, allowed hosts, rate limits (site, admin, `/go/`), bans, blocked bots, API docs |
| `api` | Host, port, page sizes |
| `scheduler` | On or off (off locally), run on startup, run timeout |
| `http` | Default transport for every source: timeout, retries, delay, concurrency, user agent, proxy |
| `enrichment` | Genre keywords, lineup parsing, time slots, weekend days, free-entry words, cities, areas |
| `dedup` | Rules for grouping duplicate listings |
| `geocoding` | Nominatim settings and manual venue pins |
| `retention` | Purge schedule, in-progress window, run-history age, space reclaim |
| `media` | Download artwork or not, store in the DB or on disk, size and type limits |
| `sources` | One block per source (see below) |

## A source block

```yaml
sources:
  issta:
    enabled: true
    cron: "47 4,16 * * *"        # Israel time
    site_url: https://www.issta.co.il
    listing_path: /tickets
    # optional, any source:
    http:                        # overrides of the global http block
      concurrency: 2
      request_delay_seconds: 1.0
    proxied: true                # go through http.source_proxy (default)
    unlist_missing: true         # hide events a complete run no longer lists
```

Each source validates its own block with its own settings model, so a bad block only disables that
source and the others keep running.

## Changing settings in production

The admin console's **config** tab edits `settings.yaml` with validation. It keeps a backup in
`config/backups/` and applies changes without a restart. Only `database` and `api` need a restart.
