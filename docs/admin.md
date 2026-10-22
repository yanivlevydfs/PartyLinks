# Admin console

`/admin` is not linked from the site and is marked `noindex`. All times are in Asia/Jerusalem.

**Sign in** with `PARTYLINKS_ADMIN__TOKEN`. The token is kept for the browser session only. Without
a token configured, the console works only from the same machine.

## Tabs

| Tab | What it shows and does |
| --- | --- |
| **overview** | Events (upcoming, total, not on the map), ticket tiers, storage, artwork, scheduler state, events per source, and warnings such as missing legal contacts or a localhost `base_url` |
| **clicks** | Ticket-link clicks for the last 1, 7, 30, 90 or 365 days: today, total, daily average, a per-day chart, per seller, per spot on the page, per city, and the top 50 events |
| **sources** | Each source's schedule and last run; run now, pause or resume a schedule, debug logging |
| **runs** | Run history, filtered by source and status, with counts (found, saved, failed, changed, removed) and errors |
| **config** | Edit `settings.yaml` with validation, automatic backups in `config/backups/`, and apply without a restart |
| **maintenance** | Purge now, reclaim space, full vacuum, geocode missing places, recompute areas, regroup duplicates |

## Checking a source

To see whether a source is working in production:

1. Open **runs** and filter by the source.
2. Read the status:
   - `success`: everything was read.
   - `partial`: some events failed, and the run lists them.
   - `failed`: an error stopped the run.
   - `blocked`: the site refused us.

Railway's logs show whether a run went through the proxy (`via <host>`).
