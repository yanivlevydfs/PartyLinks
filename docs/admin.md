# Admin console

`/admin` is not linked from the site and is marked `noindex`. All times are in Asia/Jerusalem.

**Sign in** with Google, using an account whose role is `admin`. Emails listed in
`PARTYLINKS_ACCOUNTS__ADMIN_EMAILS` become admins when they sign in, and the **users** tab can make any
account an admin. `PARTYLINKS_ADMIN__TOKEN` still works as a fallback: the token is kept for the browser
session only. With neither configured, the console works only from the same machine.

## Tabs

| Tab | What it shows and does |
| --- | --- |
| **overview** | Events (upcoming, total, not on the map), ticket tiers, storage, artwork, scheduler state, events per source, and warnings such as missing legal contacts or a localhost `base_url` |
| **clicks** | Ticket-link clicks for the last 1, 7, 30, 90 or 365 days: today, total, daily average, a per-day chart, per seller, per spot on the page, per city, and the top 50 events |
| **users** | Google accounts: sign-ups (total, last 7 and 30 days, per day), digest opt-ins; search by email or name; per user: sign out everywhere, block or unblock, make admin or user, delete (type the email to confirm) |
| **sponsored** | Paid placements: the site-wide switch, search and add events, per pick where it shows (side rails, top of results, or both), rail side and order, campaign end; and the advertiser report (views, clicks, CTR, the part from results) with CSV download |
| **sources** | Each source's schedule and last run; run now, pause or resume a schedule, debug logging |
| **runs** | Run history, filtered by source and status, with counts (found, saved, failed, changed, removed) and errors |
| **config** | Edit `settings.yaml` with validation, automatic backups in `config/backups/`, and apply without a restart |
| **maintenance** | Purge now, database size, geocode missing places (and correct city centres abroad), recompute areas, regroup duplicates |

## Bans

Repeated failed attempts (probes, wrong admin tokens, rate-limit hits) ban an IP for an hour, in memory
(a deploy lifts every ban). Requests with the valid admin token are not rate limited, but a banned IP
is refused even with the token. To lift a ban:
`railway run --service web -- python scripts/run_sources.py --unban [ip]` from another network, or
`--bans` to list them.

## Checking a source

To see whether a source is working in production:

1. Open **runs** and filter by the source.
2. Read the status:
   - `success`: everything was read.
   - `partial`: some events failed, and the run lists them.
   - `failed`: an error stopped the run.
   - `blocked`: the site refused us.

Railway's logs show whether a run went through the proxy (`via <host>`).
