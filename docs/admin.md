# Admin console

`/admin` is not linked from the site and is marked `noindex`. All times are in Asia/Jerusalem.

**Sign in** with Google, using an account whose role is `admin` (the owner's is spotxisrael@gmail.com).
Emails listed in `PARTYLINKS_ACCOUNTS__ADMIN_EMAILS` become admins when they sign in, and the **users** tab
can make any account an admin. The site header's account menu shows "⚙️ ניהול האתר / Site admin" to admins.
With the Google session, every change (any method but GET and HEAD) must also carry the header
`X-Requested-With: spotx` and, when the browser sends one, our own `Origin`: the cookie rides along on a
cross-site request, so changes are taken only from our own pages (the console sends the header).
`PARTYLINKS_ADMIN__TOKEN` still works as a fallback: the token is kept for the browser session only. With
neither configured, the console works only from the same machine.

## Tabs

| Tab | What it shows and does |
| --- | --- |
| **overview** | Events (upcoming, total, not on the map), ticket tiers, storage, artwork, scheduler state, events per source, paused sources, and warnings such as missing legal contacts or a localhost `base_url` |
| **health** | Every source's last 24 hours at a glance: OK, flaky, down, running; per source its runs by status, events of the latest good run, last good run, average time and latest error |
| **clicks** | Ticket-link clicks for the last 1, 7, 30, 90 or 365 days: today, total, daily average, a per-day chart, per seller, per spot on the page, per city, and the top 50 events |
| **users** | Google accounts: sign-ups (total, last 7 and 30 days, per day), digest opt-ins; search by email or name; per user: sign out everywhere, block or unblock, make admin or user, delete (type the email to confirm) |
| **searches** | What visitors searched for over a period: today, total, distinct, with no results, per day, the top searches, the ones that found nothing, and the latest |
| **email** | Resend ready or not, from address and digest schedule; on / off for welcome, instant and digest; "send me" a test digest or welcome (to the signed-in admin); preview any user's digest or welcome (sandboxed, nothing sent); the last 20 runs with sent / failed / skipped and the first error |
| **sponsored** | Paid placements: the site-wide switch, search and add events, per pick where it shows (side rails, top of results, or both), rail side and order, campaign end; and the advertiser report (views, clicks, CTR, the part from results) with CSV download |
| **sources** | Each source's schedule and last run; run now, pause or resume a source (kept across deploys; resume also lifts a run-manager pause), debug logging |
| **runs** | Run history, filtered by source and status, with counts (found, saved, failed, changed, removed) and errors |
| **config** | Edit `settings.yaml` with validation, automatic backups in `config/backups/`, and apply without a restart |
| **maintenance** | Purge now, database size, geocode missing places (and correct city centres abroad), recompute areas, regroup duplicates |

## Run manager (`runs/health.py`)

At most `scheduler.max_parallel_runs` (6) sources run at once; the rest wait in a queue. After every
scheduled run or retry the run manager judges it. A bad run is `failed`, `blocked`, found no events, or found
under `low_count_ratio` (30%) of the usual count (the median of the last 5 good runs, for sources usually
finding `low_count_min_usual` (10) or more). A bad run is run again after each of
`scheduler.retry_after_minutes` (15 min, 1 h, 3 h). Still bad after the last retry, the source is paused
(`paused:<source>` in the `flags` table, so it survives deploys) and the admins in
`PARTYLINKS_ACCOUNTS__ADMIN_EMAILS` get an email (when Resend is set up). An admin's own "run now" never
counts as a strike, and any healthy run unpauses the source: fix it, then run it now.

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
3. A source that stays bad is paused by the run manager: the **overview** lists it. Fix it, then
   **run now** (a healthy run resumes it) or **resume** it in **sources**.

Railway's logs show whether a run went through the proxy (`via <host>`).
