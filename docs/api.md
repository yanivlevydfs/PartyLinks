# API

FastAPI, served by the same process as the website.

- **Times:** stored in UTC and returned with their offset.
- **Local fields:** dates and weekdays are in Asia/Jerusalem.
- **Interactive docs:** `/docs` is off by default (`security.api_docs`).

## Public

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/events` | Events, one per duplicate group. Filters below; `sort`, `page`, `page_size` (max 50) |
| GET | `/events/map` | Every matching event with coordinates at once (same filters, no paging), plus a count of events without a place |
| GET | `/events/{id}` | One event by id (`<source>:<source_event_id>`) or slug: tickets with live status, images, other listings (`alternatives`), the latest deal changes |
| GET | `/go/{id}` | Redirect (302) to the seller's page, UTM-tagged and counted. `?at=` is where on our page it was clicked. Rate-limited per IP (see [ticket links](ticket-links.md)) |
| GET | `/changes` | Deal-change feed, newest first: `since` (default last 24 hours), `kind` (e.g. `price_down`), `source`, `limit` |
| GET | `/categories` | Counts per genre, date, weekday, time slot, entry type, venue, city, lineup, event type, source |
| GET | `/images/{id}` | Stored artwork bytes (the original, as downloaded) |
| GET | `/images/{id}/{640\|1280}.webp` | The artwork resized to that width as WebP, cached a year (`immutable`). Until the copy exists: the original, cached an hour |
| GET | `/posts`, `/posts/{slug}` | Blog posts as JSON |
| GET | `/site` | Site name, base URL, accessibility and privacy contacts |
| GET | `/health` | Liveness and readiness: database, scheduler, per-source freshness, config errors |

### `/events` filters

`source`, `genre`, `artist`, `venue`, `venue_slug`, `city`, `in_target_city`, `abroad` (events
abroad instead of in Israel), `entry_type` (`free` / `paid` / `free_and_paid`), `free_entry` (free
right now, honouring "free until" times), `sold_out`, `discount`, `day_of_week`, `time_slot`,
`area`, `weekend`, `on_date`, `date_from`, `date_to`, `min_price`, `max_price`, `q` (text search),
`upcoming` (default true), `include_duplicates` (every listing instead of one per event).

The API never returns a seller's raw URL. Every event's `url` is its `/go/` link.

## Admin (token required)

Send `Authorization: Bearer <PARTYLINKS_ADMIN__TOKEN>`. Without a token configured, only requests
from the same machine are accepted, and proxied requests never count as local.

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/sources` | Config, running state, next run, last run of each source |
| POST | `/sources/{name}/run` | Run a source now (202; 409 if already running) |
| GET | `/runs` | Run history; filter by `source`, `status` |
| GET/POST | `/maintenance/retention` | Retention settings and next run / purge now |
| GET | `/admin/api/overview` | Events, storage, scheduler, warnings |
| GET | `/admin/api/clicks?days=N` | Ticket-link clicks: total, today, per day, per seller, per placement, per city, top events |
| POST | `/admin/api/scheduler/{action}` | Pause or resume the scheduler |
| POST | `/admin/api/jobs/{job_id}/{action}` | Pause, resume or run one scheduled job |
| POST | `/admin/api/scan` | Run several sources now |
| GET/POST | `/admin/api/logging` | Debug logging on or off |
| GET/PUT | `/admin/api/config` | Read or save `settings.yaml` (with backup) |
| POST | `/admin/api/config/validate`, `/admin/api/config/apply` | Validate, then apply without a restart |
| GET | `/admin/api/config/backups/{name}` | One config backup |
| POST | `/admin/api/maintenance/vacuum`, `/geocode`, `/areas`, `/regroup` | Reclaim space, geocode missing places, recompute areas, regroup duplicates |
| POST | `/admin/api/maintenance/orphans` | Artwork files no image row claims: counted (`dry_run=true`, the default) or deleted (`dry_run=false`). Returns `files`, `bytes` |

## Rate limits

Set in `security`. Over the limit the API returns `429`:

| Limit | Value | Setting |
| --- | --- | --- |
| Per IP, whole site (not `/images/`, `/assets/`, `/icons/`, `/favicon*`) | 300 requests per minute | `rate_limit_per_minute` |
| Admin API | 30 requests per minute | `admin_rate_limit_per_minute` |
| `/go/` | 20 requests per minute | `go_rate_limit_per_minute` |

Repeated strikes (vulnerability probes, wrong admin tokens, rate-limit hits) ban the IP for
`ban_minutes`. Card artwork and the site's own files stay out of the per-minute count: one home
page draws well over a hundred images, and counted there a few reloads banned a visitor's home IP.
Bans live in memory, so a restart (any deploy) lifts them.
