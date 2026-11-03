# Configuration

Every setting lives in `config/settings.yaml`. There are no hard-coded URLs, limits or schedules in
the code. A different file can be used with `PARTYLINKS_CONFIG=/path/to/settings.yaml`.

## Admin overrides (database)

The admin's **config** tab overrides any setting without a deploy. Only the changed keys are stored,
in the `config_overrides` table (each save is a version; the newest is in force), and they are deep-merged
over `settings.yaml` when settings load, in the web and the worker process alike, at startup and on
every apply. A deploy replaces the file, never the overrides; "Back to file" drops one key. `database`
and `admin` cannot be set there. Priority: environment variables > database overrides > `settings.yaml`.
Code: `partylinks/config_store/`.

## Environment overrides

Any key can be overridden with a `PARTYLINKS_` variable, using `__` for nesting. Variables are read
from the environment or from `.env` in the repo root.

```bash
PARTYLINKS_DATABASE__URL=postgresql://user:pass@host/partylinks
PARTYLINKS_SOURCES__ZYGO__ENABLED=false
PARTYLINKS_HTTP__CONCURRENCY=16
PARTYLINKS_MEDIA__STORAGE=filesystem
```

Secrets (admin token, proxy credentials, access headers, the Google client secret, the session secret,
the Resend key, the Telegram bot token and webhook secret) belong in variables only: Railway's variables in production, `.env` (gitignored) locally.

## Sections

| Section | What it controls |
| --- | --- |
| `timezone` | App time zone (`Asia/Jerusalem`): local dates, cron times, display |
| `database` | PostgreSQL URL (empty: the embedded local server in `local_dir`), pool, timeouts, keepalives, write mode, retries, startup wait |
| `logging` | Level, text or JSON, access log, rotating file |
| `site` | `base_url`, brand name (`SPOTX`), the contact address (`email`; empty: `privacy.email`, now contact@spotx.fun), accessibility and privacy contacts |
| `utm` | UTM tags on ticket links: `enabled`, `source`, `medium` (see [ticket links](ticket-links.md)) |
| `accounts` | Optional Google / Facebook accounts: `enabled`, `session_days`, `max_alerts`, `max_saved`; secrets (`google_client_id`, `google_client_secret`, `facebook_app_id`, `facebook_app_secret`, `session_secret`, `admin_emails`) from the environment only (see [accounts](accounts.md)) |
| `mail` | Email through Resend: `from_address` (`SPOTX <events@spotx.fun>`, sender only; replies go to the site's contact address), `digest_cron` (Thursday 10:00), `digest_max_events`, `instant_min_interval_minutes`, `matcher_interval_minutes`, `calendar_attachment` (the events' `.ics` on every email with events; on); `PARTYLINKS_MAIL__RESEND_API_KEY` from the environment only |
| `bot` | The Telegram bot: `enabled`, `webhook_url` (default `site.base_url` + `/bot/telegram`), `results`, `inline_results`, `max_text_length`, `messages_per_minute`, `inline_per_minute`, `memory_minutes`, `near_km`, `max_alerts`, `alert_interval_minutes`, `alert_max_events`, `alert_max_per_day`, `reminder_hours`, `max_reminders`, `max_watches`, `watch_min_drop_percent`, `similar_results`, `digest_weekday`, `digest_hour`, `digest_results`, `channel_id`, `channel_hour`,  `PARTYLINKS_BOT__TELEGRAM_TOKEN` and `PARTYLINKS_BOT__TELEGRAM_WEBHOOK_SECRET` from the environment only (see [bot](bot.md)) |
| `security` | Trusted proxies, allowed hosts, rate limits (site, admin, `/go/`), bans, blocked bots, API docs |
| `api` | Host, port, page sizes, page cache freshness |
| `scheduler` | On or off (off locally), run on startup, run timeout, the worker process (`separate_process`, heartbeat, stale / command timeouts, restart backoff), and the run manager: `max_parallel_runs` (6), `retry_after_minutes` (15, 60, 180), `low_count_ratio` (0.3), `low_count_min_usual` (10), `max_failure_ratio` (0.15), `failure_min` (5) (see [admin](admin.md)) |
| `http` | Default transport for every source: timeout, retries, delay, concurrency, user agent, proxy (`source_proxy`; `israel_proxy` and `israel_proxy_feed` for sources with `israel_proxy: true`; `israel_vpn` for sources with `vpn: true`), `impersonate` (speak as a browser, e.g. `chrome`, through curl_cffi) |
| `enrichment` | Genre keywords, lineup parsing, time slots, weekend days, free-entry words, cities, areas |
| `dedup` | Rules for grouping duplicate listings |
| `geocoding` | Nominatim settings and manual venue pins (`venues`: keyword matched inside "venue \| address" -> [lat, lon], from OSM only; ~110 pins, for halls OSM doesn't know by the sellers' names) |
| `retention` | Purge schedule, in-progress window, run-history age, `max_future_days` (730: a start further ahead is a broken date, never imported, hidden, purged) |
| `popularity` | Likes / views / watching now: `watching_minutes` (5), `min_watching` (2: the reader counts too), `keep_days` (365, view rows) |
| `media` | Download artwork or not, store in the DB or on disk, size and type limits, WebP copies (`webp_quality`, default 78; `variant_threads`, default 2, 0 = no backfill of older files) |
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
