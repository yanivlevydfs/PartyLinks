# Accounts

Signing in is optional. Every page, the search, the map and the ticket links work without an account.
An account adds personal features on top: saved events on every device, alerts for events that match
what you like, a notification bell, a "For you" row and email updates. The full design is in
[accounts-alerts-design.md](accounts-alerts-design.md) and the build plan in
[plans/2026-10-29-accounts-alerts.md](plans/2026-10-29-accounts-alerts.md).

## Signing in

"Continue with Google" is a plain link to `/auth/google/login`. Our server redirects to Google, and
Google sends the visitor back to `/auth/google/callback`. Authlib checks the state, the nonce, PKCE and
the ID token. Only accounts with a verified email are accepted. No Google script loads on our pages, so
the cookie banner needs no extra category.

On the first sign-in the user lands on the page they came from with `?welcome=1`. Errors come back as
`?signin=failed|unverified|blocked|taken`.

## Sessions

The `spotx_session` cookie (HttpOnly, Secure on https, SameSite=Lax) holds a random token. The database
keeps only its SHA-256, in `user_sessions`. A session lasts 30 days (`accounts.session_days`) and is
moved forward at most once a day while in use. Signing out deletes the row. Blocking a user deletes all
their rows, and every request re-reads the user, so a block takes effect at once.

## What is stored

The `users` table holds the Google `sub`, email, name, picture URL, email language, role, block time,
digest consent with its time, and sign-in dates. No IP address or user agent is stored with an account.
Deleting an account (from `/account`, or from the admin) removes the user, and everything that belongs
to it goes with it (`ON DELETE CASCADE`).

Logs carry the user id only. A filter on every log handler (`accounts/redact.py`) replaces any email
address and any `code=`, `state=`, `t=` or `token=` value as a safety net.

## Saved events (the save bell)

Every event card, the event page and the map pins have a save bell (🔔; the header's bell with a tick links to them, the plain bell is alert news); the header links to `/saved` with the
count. It works without an account: the ids are kept in the browser (localStorage `spotx-saved`) and
`/saved` shows them through `GET /events/cards?ids=` (at most 100). Signed in, saved events live in
`saved_events`; on sign-in the browser's list is merged into the account once (`POST /api/me/saved/merge`)
and cleared from the device. A saved event is removed with its event when the event ends (never past events),
and the list always shows each group's primary listing, so a regrouped event stays. At most
`accounts.max_saved` (500) per user. Code: `src/partylinks/saved/`, `web/src/saved/`.

## Alerts ("save search"), the bell and "For you"

An alert is a saved search: the site's own filters (genres, regions, neighbourhoods or city, time slots,
free only, discount only, when as "any time / weekend / 7 days / 30 days" or a date range, and a word such
as an artist or venue). "🔔 Save as alert" appears in the filter bar (home, landing pages, map) whenever a
filter or search is set, on the quiz's last step, and on an event page for its first artist. Signed out, it
asks to sign in and saves the alert on the way back (`?save_alert=1`, or `q:<artist>`). `/alerts` lists them
(new matches, "email me at once", pause, see events, delete) and makes new ones. At most
`accounts.max_alerts` (20) per user; the same search saved twice is one alert.

The worker matches new events every `mail.matcher_interval_minutes` (15) (`alerts/matcher.py`, job
`alerts:match`): events first seen since the last pass, against every active alert of a user who is not
blocked, through `api.build_event_filters`, the same function `/events` uses, so an alert finds exactly
what the same filters show on the site. Each distinct set of filters is queried once. A match is a row in
`alert_hits`; its key (alert, event) keeps an event from being sent twice. The first pass after a deploy
only sets the cursor, so existing events never flood anyone.

The header bell (signed in) shows the newest 30 matches, polls once a minute while the tab is visible, and
marks them seen when opened. "For you" on the home page shows up to 12 upcoming events matching any active
alert (cached 10 minutes per user). Matches go with their event when it ends. Code: `src/partylinks/alerts/`,
`web/src/alerts/`.

## Email (Resend)

Sent by the worker through Resend's batch API (100 a request, at most two requests a second, an
idempotency key per request) from `mail.from_address` (`SPOTX <events@spotx.fun>`; the domain is verified
at Resend), reply-to the contact address. Nothing is sent without `PARTYLINKS_MAIL__RESEND_API_KEY`
(a send-only key is enough), and each kind has an admin switch (email tab).

| Email | When | To whom |
| --- | --- | --- |
| Welcome | Every 5 minutes, for accounts made 2+ minutes ago | Every new account, once |
| Instant | After each matcher pass | Alerts set to "email me at once"; at most one email per user per `mail.instant_min_interval_minutes` (60), up to 10 events |
| Weekly digest | `mail.digest_cron` (Thursday 10:00, Israel time) | Only users who ticked it (off by default: it carries ticket links, so it is advertising under the Communications Law §30A; the tick's time is kept). Saved events this week first, then new matches (next 10 days), then this weekend's free events and highlights in a quiet week. Once a week. |

Every email (Hebrew right to left or English, by the account's email language) has its events as cards
with picture, day and time, place, weather (from the site's forecast cache) and price, links tagged
`utm_source=email`, a footer with why it came, and an unsubscribe link plus the RFC 8058 one-click headers.
The link is a signed `{user, kind}` token that never expires: `GET /api/email/unsubscribe?t=` shows a confirm
page (link scanners open links), `POST` unsubscribes (`digest`: digest off; `instant`: every alert's instant
off; `all`, in the welcome: both). Each run is recorded in `email_runs` (counts and the first error only).
A Resend account problem (key, quota, billing) stops the run and shows in the admin. Code: `src/partylinks/mail/`.

## API

| Method | Path | |
| --- | --- | --- |
| GET | `/api/me` | The signed-in user, or 401 (`Cache-Control: private, no-store`) |
| PATCH | `/api/me` | Name, email language, weekly digest consent |
| DELETE | `/api/me` | Delete the account |
| POST | `/api/auth/logout` | Sign out |
| GET | `/api/me/saved` | Saved events as cards, upcoming, soonest first |
| PUT / DELETE | `/api/me/saved/{event_id}` | Save on / off (404 for an unknown or ended event, 409 over the limit) |
| POST | `/api/me/saved/merge` | `{ids: [...]}` from the browser after sign-in |
| GET | `/events/cards?ids=` | Public: cards for a signed-out browser's saved events |
| GET / POST | `/api/me/alerts` | List (with unseen counts) / create (422 for no filter or an unknown genre / region / slot, 409 over the limit) |
| PATCH / DELETE | `/api/me/alerts/{id}` | Edit criteria, name, instant, active / delete |
| GET | `/api/me/alerts/{id}/preview` | What the alert finds now: `{total, items}` |
| POST | `/api/me/alerts/preview` | The same for criteria not saved yet (the form's "N events match now") |
| GET | `/api/me/bell` | `{unseen, items: [{event, alert, matched_at, seen}]}` |
| POST | `/api/me/bell/seen` | Mark all as seen |
| GET | `/api/me/for-you` | Up to 12 upcoming events matching any active alert |

Writes need the header `X-Requested-With: spotx` and, when the browser sends one, an `Origin` matching
`site.base_url`. Sign-in is limited to 10 requests a minute per IP, and account writes to 30.

## Configuration

`accounts:` in `config/settings.yaml` (see [configuration](configuration.md)). Secrets come from the
environment only:

| Variable | |
| --- | --- |
| `PARTYLINKS_ACCOUNTS__GOOGLE_CLIENT_ID` | The OAuth client (Google Cloud project `spotx-513112`, "SPOTX web") |
| `PARTYLINKS_ACCOUNTS__GOOGLE_CLIENT_SECRET` | Its secret |
| `PARTYLINKS_ACCOUNTS__SESSION_SECRET` | A long random string: signs the sign-in state cookie and unsubscribe links |
| `PARTYLINKS_ACCOUNTS__ADMIN_EMAILS` | JSON list of emails promoted to admin on sign-in |

Without all of the first three, sign-in is off: `/site` reports `"accounts": false` and the site shows no
account UI. The OAuth client's redirect URIs are `https://spotx.fun/auth/google/callback` and
`http://localhost:8000/auth/google/callback`.
