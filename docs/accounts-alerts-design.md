# Accounts, event alerts and email: design

**Date:** 2026-10-29
**Status:** Design approved in brainstorming. Not implemented.
**Model:** CheapFlights (`H:\GitHub\cheapflights`): Google accounts, deal alerts, report email, admin users tab. This copies that concept and applies it to events.

---

## 1. Summary

- **What:** optional Google accounts on SPOTX (sign in, sign out, delete account). A signed-in user saves any number of **event alerts**, each describing the events they are looking for: genres, regions or city, time slots, free only, date range, artist or venue. Matches reach the user in three ways:
  - a **weekly digest email**, Thursday at 10:00 Israel time;
  - an optional **instant email** per alert;
  - an in-site **notification bell**.
  Signed-in users also get a **"For you" row** on the home page.
- **Saved events:** a heart on every event card and event page, with a `/saved` page. It works **without login**: hearts are kept in the browser. On sign-in they are merged into the account and synced across devices. Saved events coming up that week lead the digest.

- **Why:** personalised events and a weekly reason to come back. This is the same retention loop CheapFlights runs for flights.

- **Who:** visitors to the site, in Hebrew and English, mostly in Israel.

- **Login is never required.** Everything public today stays public and anonymous. An account only adds the personal extras above (see the rule in section 3).

- **Data:** email, name and Google picture URL are stored in the PartyLinks PostgreSQL DB, as CheapFlights does. Deleting an account removes them at once.

- **Admin:** a users tab (list, detail, revoke, block, delete, roles), an email tab (switches, test send, preview, send runs), and stats. Admins can sign in with Google; the token stays as a fallback.

- **Code:** new packages only. Existing files get hook lines (router include, tab entry, retention call).

## 2. Assumptions

| # | Assumption |
| --- | --- |
| A1 | **Israeli Communications Law §30A (anti-spam).** The digest carries ticket links and sponsored items, so it counts as advertising. Digest opt-in is therefore **off by default**: the user ticks it. Instant alerts are switched on per alert by the user. Every email carries a one-click unsubscribe. |
| A2 | **Scale:** up to about 10k users and about 5 alerts each (≈50k alerts), against about 28k upcoming events. |
| A3 | **Resend:** the account does not exist yet. Until `PARTYLINKS_MAIL__RESEND_API_KEY` is set, every email feature is off and the admin shows a warning. Plan on Resend's paid tier, since the free tier is 3,000 emails a month and 100 a day. The sending domain is `spotx.fun` and needs SPF and DKIM records. |
| A4 | **Instant email cap:** at most one instant email per user per hour; several matches go into one email. |
| A5 | **No email address or Google `sub` is ever written to logs.** Logs use the internal user id. This is the same rule CheapFlights follows. |
| A6 | **Weather on every event in emails.** The `weather.py` cache already covers events in Israel and abroad. |
| A7 | **Never past events.** Alert hits and bell items whose event has ended are deleted by retention. |
| A8 | **Minimum age 16** in the Terms, as in CheapFlights. |
| A9 | **Admin role:** `PARTYLINKS_ACCOUNTS__ADMIN_EMAILS` lists the emails promoted to `admin` on sign-in. `PARTYLINKS_ADMIN__TOKEN` remains a break-glass fallback. |

## 3. Rules this design must keep

- **Login is optional, always.** No feature that is public today moves behind login. No sign-in wall and no forced popup. Account features show a soft "Sign in to save" prompt where they appear.

- **New features in their own files.** Existing files only get hook lines.

- **Change every surface:** filters, quiz, map, event page, SEO landing pages, blog, and i18n in both HE and EN, all at once.

- **Prefer libraries:** Authlib, itsdangerous, resend and Jinja2. Each one is added to `pyproject.toml`, `requirements.txt` **and** `requirements.lock` (Docker installs the lock).

- **PostgreSQL docs:** update `docs/postgres-migration.md` and its updates log with every new table.

- **Measure at production size:** time the matcher against about 28k events and about 50k alerts before pushing.

- **Rate limit:** the existing 300 requests a minute still applies. The bell polls at most once a minute, and only while the tab is visible.

- **Scraping stays off locally.** The matcher and email jobs run in the worker, so they follow the same switch.

## 4. Decision log

| # | Decision | Alternatives | Why |
| --- | --- | --- | --- |
| D1 | Store email, name and picture in the PartyLinks DB | Hashed ids with the email kept at Resend; no server accounts | Owner's call: copy CheapFlights as it is. Simpler sending and admin. |
| D2 | Many alerts per user, CheapFlights `deal_alerts` style | One preference set; one set plus instant alerts | Owner: "same concept like CheapFlights, but for events" |
| D3 | Deliver by weekly digest, instant email per alert, and bell | Digest only; digest plus instant | Owner: do all |
| D4 | Mailing list through Google sign-in only | Plus an email-only footer signup with double opt-in | Google verifies the email: no double opt-in flow and no fake signups |
| D5 | Admin: users, roles (Google admin login), blocking, digest preview | Users tab only | Owner: all |
| D6 | Digest Thursday 10:00 Asia/Jerusalem | Other days | Arrives before the weekend |
| D7 | "For you" row on the home page for signed-in users | None | Owner: yes |
| D8 | Server redirect OAuth through Authlib | Google Identity Services popup / One Tap (as in CheapFlights); hosted auth | No Google script on our pages, so no new consent category; uses a library. Hosted auth would put users outside our DB. |
| D9 | Login never required | Gate some features | Owner rule |
| D10 | Bell items are `alert_hits` rows with `seen_at`; no separate notifications table | A `notifications` table as in CheapFlights | One row already means "this event matched this alert"; a second table would copy it |
| D11 | The matcher reuses the `/events` filter builder | A separate Python matcher | One source of truth: an alert matches exactly what the same filters show on the site |
| D13 | Saved events (a heart) in v1, working signed out through localStorage and merged on sign-in | Account-only hearts; leave it for later | Owner: do it as well. The login-optional rule means the heart can't need an account. |
| D12 | Sessions in the DB, token stored as SHA-256, 30-day rolling, no IP or user agent stored | CheapFlights' 24h sessions; JWT | Users of an event site expect to stay signed in. A DB row can be revoked. Least data kept. |

## 5. Architecture

```text
Browser ──"Continue with Google"──► /auth/google/login ──► Google ──► /auth/google/callback
                                                                         │ Authlib: state + nonce + PKCE, ID token verified
                                                                         ▼
                                                    accounts.service.sign_in(sub, email, name, picture)
                                                    → users row (upsert), sessions row, cookie spotx_session
                                                                         │
Web process (partylinks serve)                                           │
  /api/me, /api/me/alerts, /api/me/bell, /api/me/for-you ◄──── session cookie
  /api/email/unsubscribe (GET page + POST one-click)
  /admin/api/users..., /admin/api/email...

Worker process (partylinks worker, APScheduler)
  alerts.matcher   every 15 min: new events (first_seen_at > last pass) × active alerts → alert_hits
  mail.instant     right after the matcher: users with unsent instant hits, at most 1 email per hour
  mail.digest      cron Thu 10:00 Asia/Jerusalem: opted-in users → one digest each
  mail.welcome     after sign-up (within 5 min)
  retention hook   delete alert_hits of ended or removed events; delete expired sessions
                                   │
                                   ▼
                        Resend (batch API, 100 per call, idempotency keys)
```

The web process never sends bulk email. It only sends a single test send from the admin. Bulk work is in the worker, so visitors never wait behind it (see `docs/architecture.md`, "Processes").

## 6. Data model (PostgreSQL, SQLAlchemy on the app's `Base`)

All tables are created by `Database.create_schema`, like `plans/models.py`. Every one goes into `docs/postgres-migration.md`.

### `users`

| Column | Type | Notes |
| --- | --- | --- |
| id | `String(32)` PK | `secrets.token_hex(16)`; the only id used in logs and URLs |
| google_sub | `Text` unique | Never logged |
| email | `Text` unique | Lowercased; never logged |
| name | `Text` | From Google; the user can edit it |
| picture | `Text` null | Google's picture URL |
| locale | `String(2)` | `he` / `en`, the language of the emails |
| role | `String(10)` | `user` / `admin` |
| blocked_at | `UTCDateTime` null | Set: sign-in refused, sessions revoked, no emails |
| digest_opt_in | `Boolean` default **false** | §30A explicit consent |
| digest_opt_in_at | `UTCDateTime` null | Proof of consent: the time it was given |
| last_digest_at | `UTCDateTime` null | |
| last_instant_at | `UTCDateTime` null | Enforces the 1-per-hour cap |
| welcome_sent_at | `UTCDateTime` null | |
| created_at, last_login_at | `UTCDateTime` | |

### `user_sessions`

| Column | Type | Notes |
| --- | --- | --- |
| id | `Integer` PK | |
| user_id | FK `users.id` **ON DELETE CASCADE**, indexed | |
| token_hash | `String(64)` unique | SHA-256 of the cookie value; the raw token is never stored |
| created_at, expires_at, last_seen_at | `UTCDateTime` | 30 days, rolled forward at most once a day |

### `event_alerts`

| Column | Type | Notes |
| --- | --- | --- |
| id | `Integer` PK | |
| user_id | FK cascade, indexed | |
| name | `Text` | Auto-named from the criteria ("Techno · Tel Aviv · weekend"); editable |
| criteria | `NullableJSON` | **A subset of the web `Filters`:** `q` (artist, venue, keyword), `genres`, `regions`, `areas`, `city`, `slots`, `freeOnly`, `discountOnly`, `when` (`weekend` / `week` / a date range / `all`) |
| criteria_hash | `String(64)` indexed | Lets the matcher run each identical set of criteria once |
| instant | `Boolean` default false | |
| active | `Boolean` default true | Pausing does not reset hits |
| created_at, updated_at | `UTCDateTime` | |

Limit: 20 alerts per user (`accounts.max_alerts`).

### `alert_hits` (matches, the bell, and "already sent")

| Column | Type | Notes |
| --- | --- | --- |
| alert_id | FK cascade | PK part |
| event_id | `Text` | PK part; the group's primary event. No FK, like `plans`: retention deletes hits with their event |
| user_id | `String(32)` indexed | Denormalised for bell and digest reads |
| matched_at | `UTCDateTime` | |
| seen_at | `UTCDateTime` null | Bell read state |
| instant_sent_at, digest_sent_at | `UTCDateTime` null | Each event is sent at most once per channel |

The primary key `(alert_id, event_id)` is what guarantees an event is never notified twice for the same alert. This is the events version of CheapFlights' ratchet.

### `saved_events` (the heart)

| Column | Type | Notes |
| --- | --- | --- |
| user_id | FK `users.id` cascade | PK part |
| event_id | `Text` | PK part. No FK (like `plans`); retention deletes the row when the event ends or is removed (never past events). Read through `group_id`, so a regrouped event still shows its primary listing. |
| saved_at | `UTCDateTime` | |

Limit: 500 per user.

### `email_runs` (admin stats; no personal data)

| Column | Type | Notes |
| --- | --- | --- |
| id | PK | |
| kind | `welcome` / `instant` / `digest` / `test` | |
| started_at, finished_at | | |
| sent, failed, skipped | `Integer` | |
| error | `Text` null | The first error, with any email address removed |

## 7. Sign-in flow (Authlib, server redirect)

1. A "Continue with Google" button links to `/auth/google/login?next=/current/path`. `next` must be a relative path that starts with `/` and not `//`; anything else becomes `/`, which closes the open-redirect hole.
2. Authlib's Starlette client redirects to Google with scope `openid email profile` and state, nonce and PKCE. Authlib keeps the state in Starlette's `SessionMiddleware` cookie (itsdangerous-signed, 10 minutes, used only during sign-in).
3. On `/auth/google/callback`, Authlib checks state and the ID token (signature, `aud`, `iss`, expiry, nonce). The flow requires `email_verified = true`.
4. `accounts.service.sign_in` does the following:
   - refuse if `blocked_at` is set;
   - upsert by `google_sub` (and keep the email in sync);
   - promote to admin if the email is in `admin_emails`;
   - on a first sign-in, queue the welcome email.
5. The callback creates a session and sets the cookie `spotx_session`: HttpOnly, Secure, `SameSite=Lax` (Lax is required for the redirect back from Google), Path `/`, 30 days. It then redirects to `next`.
6. A first sign-in lands on `next?welcome=1`, which opens a small sheet: "Get the weekly digest every Thursday?" with the box unticked (A1), and "Create your first alert from these filters".

**Logout** (`POST /api/auth/logout`): deletes the session row and clears the cookie.
**Delete account** (`DELETE /api/me`, confirmed by typing "delete"/"מחק"): one transaction deletes the user. Sessions, alerts and hits cascade. Click records already hold no user, so nothing else is left.
**CSRF:** every mutating `/api/me*` route requires the `X-Requested-With: spotx` header and a same-origin `Origin`. With `SameSite=Lax`, a cross-site POST carries no cookie anyway.

## 8. API

### Public (cookie session)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/auth/google/login`, `/auth/google/callback` | Sign in |
| POST | `/api/auth/logout` | Sign out |
| GET | `/api/me` | `{id, name, email, picture, locale, role, digest_opt_in}` or 401 |
| PATCH | `/api/me` | Name, locale, digest_opt_in |
| DELETE | `/api/me` | Delete account |
| GET / POST | `/api/me/alerts` | List / create (validated against the facets: unknown genres or regions are refused) |
| PATCH / DELETE | `/api/me/alerts/{id}` | Edit, pause, instant on/off / delete |
| GET | `/api/me/alerts/{id}/preview` | Events that match now (same as `/events` with those filters) |
| GET | `/api/me/bell` | Unseen count and the latest 30 hits with event cards |
| POST | `/api/me/bell/seen` | Mark all as seen |
| GET | `/api/me/for-you` | Up to 12 upcoming events matching any active alert, soonest first, grouped like `/events` |
| GET | `/api/me/saved` | Saved events as event cards, upcoming only, soonest first |
| PUT / DELETE | `/api/me/saved/{event_id}` | Heart on / off |
| POST | `/api/me/saved/merge` | `{ids: [...]}` from localStorage after sign-in; unknown and past ids are dropped. Returns the full list. |
| GET | `/api/events/cards?ids=` | Signed out: cards for the hearts kept in localStorage (at most 100 ids; a public read that goes through the page cache) |
| GET / POST | `/api/email/unsubscribe?t=…` | GET shows a confirm page; POST is RFC 8058 one-click |

`/api/me*` responses are `Cache-Control: private, no-store` and are kept out of the page cache in `api.py`. The script-client block in `security.py` covers the data API. `/api/me*` must work for browsers only, which a session cookie already ensures.

### Admin (`/admin/api`, token **or** a session with role admin)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/users?q=&page=` | Search by email or name: joined date, last login, number of alerts, digest, active sessions, role, blocked |
| GET | `/users/{id}` | Detail: profile, alerts, the last 50 hits |
| POST | `/users/{id}/revoke` | Delete all sessions |
| POST | `/users/{id}/block`, `/unblock` | Block or unblock |
| POST | `/users/{id}/role` | `user` / `admin` |
| DELETE | `/users/{id}` | Same path as the user's own delete |
| GET | `/users/stats?days=` | Signups per day, opted-in count, alerts, top alert genres, regions and artists |
| GET | `/email` | Switches, Resend status (key present, domain verified), last runs |
| POST | `/email/switches` | Digest, instant and welcome on/off. Stored in the one-row `mail_switches` table (`worker_state` is rewritten every heartbeat). |
| POST | `/email/test?kind=digest` | Sends to the signed-in admin's own email |
| GET | `/email/preview?user_id=&kind=digest` | The rendered HTML, not sent |

`require_admin` in `admin.py` gets one hook line: accept the token, **or** a valid session whose user has `role = admin` and is not blocked.

## 9. Matching engine (`alerts/matcher.py`)

- **When:** a worker job every 15 minutes, plus once after each source run finishes. One run at a time, behind a lock.

- **Input:** event group primaries with `first_seen_at > last_pass_at` that are still upcoming. The cursor is stored in the one-row `alert_state` table.

- **How:**
  1. If there are no new events, stop.
  2. Load the active alerts (blocked users excluded) and **group them by `criteria_hash`**.
  3. For each distinct set of criteria, run the **same filter builder `/events` uses**, restricted to `id = ANY(:new_ids)`. Step 0 of the build moves that builder out of the `/events` handler in `api.py` into a function both can call; the endpoint's behaviour does not change.
  4. Insert `alert_hits` rows with `ON CONFLICT DO NOTHING`.
- **Why reuse matters:** an alert "techno, Tel Aviv, weekend" must match exactly what those filters show on the site. Two implementations would drift apart.

- **Cost:** one query per distinct set of criteria, each over a small id list (the new events of 15 minutes, usually under 500). `ponytail:` ceiling is about 50k distinct criteria sets per pass. If measurements show it is slow, upgrade to batching criteria sets with the same genre and region into one query using `VALUES` joins. **Measure at production size before pushing.**

- **"For you"** (`/api/me/for-you`) uses the same builder: OR of the user's active alerts, upcoming events only, limit 12. It is cached for 10 minutes per user in memory (cachetools).

## 10. Email (`mail/` package, Resend)

| Email | When | Content | Respects |
| --- | --- | --- | --- |
| Welcome | ≤5 min after first sign-in | What alerts are, a "create your first alert" link, the digest checkbox reminder | Welcome switch |
| Instant | After a matcher pass | New events for alerts that have `instant` on, combined into one email, at most 1 per user per hour | User's `instant` flag and the instant switch |
| Weekly digest | **Thu 10:00 Asia/Jerusalem** | First, **"Your saved events this week"** (hearts with dates in the next 7 days). Then up to 12 upcoming events (next 10 days) matching the user's alerts that were not in an earlier digest; then "This weekend's free events" as filler. With no alerts at all, weekend highlights. Each card: image (`/images/{id}/640.webp`), date and time, venue, **weather**, price or "free", button to the event page. | `digest_opt_in` and the digest switch |
| Contact / test | Admin | | |

- **Sending:** the Resend batch API in chunks of 100. Each email has an idempotency key `{kind}:{user_id}:{period}`, where period is the ISO week for the digest and the hour for instant, so a retried run never sends twice. Errors are classified as in CheapFlights' `is_account_error`: an account or key error stops the run; a per-recipient error is counted and skipped.

- **Templates:** Jinja2, HE (RTL) and EN, with table layout and inline CSS. They live in `mail/templates/`. Links carry the site's UTM tags (`utm_source=email&utm_campaign=digest|instant|welcome`).

- **Unsubscribe:** the token is itsdangerous-signed `{user_id, kind}`. Every email has the headers `List-Unsubscribe: <https://spotx.fun/api/email/unsubscribe?t=…>` and `List-Unsubscribe-Post: List-Unsubscribe=One-Click`, plus a footer link. Unsubscribing from the digest sets `digest_opt_in = false`; from instant, it turns off `instant` on all of that user's alerts.

- **From:** `SPOTX <events@spotx.fun>`. Reply-to is the site contact address.

- **Sender details (§30A):** the footer gives the sender's name and contact address and says how to refuse.

## 11. Frontend

### New files

```text
web/src/accounts/
  useMe.ts            react-query: /api/me (null when signed out); signIn(next), signOut()
  SignInButton.tsx    "Continue with Google" (a plain link to /auth/google/login, no Google script)
  UserMenu.tsx        Header avatar: my alerts, settings, sign out
  AccountPage.tsx     /account: name, email language, digest checkbox, delete account
  WelcomeSheet.tsx    After the first sign-in: digest opt-in and a first alert
  AdminUsers.tsx      Admin tab "users"
  AdminEmail.tsx      Admin tab "email"
  accounts.css, i18n.ts
web/src/alerts/
  AlertsPage.tsx      /alerts: list, create, edit, pause, instant toggle, live preview count
  AlertForm.tsx       Reuses GenrePicker / Calendar / region pickers from Filters.tsx
  AlertCard.tsx
  SaveAlertButton.tsx "Save as alert" from the current filters (signed out: soft sign-in prompt)
  Bell.tsx            Header bell, unseen badge, dropdown of event cards
  ForYouRow.tsx       Home-page row
  alerts.css, i18n.ts, types.ts
```

```text
web/src/saved/
  useSaved.ts         One hook for both states. Signed out: zustand persist key "spotx-saved" (event ids).
                      Signed in: react-query /api/me/saved with an optimistic toggle.
                      On sign-in: POST merge, then clear the local list.
  SaveHeart.tsx       Heart button (aria-pressed, labelled "Save event" / "שמור אירוע")
  SavedPage.tsx       /saved: upcoming saved events; signed out, a soft note "sign in to keep them on every device"
  saved.css, i18n.ts
```

### Hook lines in existing files

| File | Hook |
| --- | --- |
| `main.tsx` | Routes `/account`, `/alerts` |
| Header in `ui.tsx` | `<Bell/>` + `<UserMenu/>` or `<SignInButton/>` |
| `Filters.tsx` | `<SaveAlertButton filters={…}/>` next to the result count |
| `pages/Quiz.tsx` | Last step: "Save these answers as an alert" |
| `pages/Home.tsx` | `<ForYouRow/>` when signed in |
| `pages/MapPage.tsx` | `<SaveAlertButton/>` with the map's filters |
| `pages/EventPage.tsx` | "Alert me about {artist}" / "about {venue}" (an alert with `q`) |
| Event card in `ui.tsx`, `pages/EventPage.tsx`, the map popup in `pages/MapPage.tsx`, `ForYouRow` | `<SaveHeart eventId=…/>` |
| `main.tsx` | Route `/saved`; header link "Saved (n)" |
| `pages/Admin.tsx` | `TABS` gains `"users"`, `"email"` |
| `consent.tsx` | Storage list: `spotx_session` (necessary) and `session` (sign-in only, 10 minutes, necessary) |

### Every-surface checklist

- [ ] Filters bar, quiz, map, event page: "Save as alert"

- [ ] SEO landing pages (`/city/x`, `/genre/x`, server-rendered too): "Get these events every Thursday" (a sign-in link with `next`)

- [ ] Blog post announcing alerts, in HE and in `content/blog/en/<slug>.html`

- [ ] i18n: every string in HE and EN

- [ ] Heart on every surface that shows an event: home, browse, abroad, map popup, event page, "For you", landing pages (the React version)

- [ ] `robots.txt`: disallow `/account`, `/alerts`, `/saved`, `/auth/`; these pages are `noindex` and not in the sitemap

- [ ] Accessibility (IS 5568): bell and menus keyboard-reachable, `aria-live` on the bell count, form labels

## 12. Security

- **OAuth:** state, nonce and PKCE (Authlib); `email_verified` required; `next` limited to relative paths.

- **Sessions:** random 32-byte token, only the SHA-256 stored, server-side revocation, rolling 30 days.

- **Cookies:** HttpOnly, Secure, SameSite=Lax.

- **CSRF:** the custom header and Origin check in section 7.

- **Rate limits:** `/auth/*` 10 a minute per IP; `/api/me*` writes 30 a minute. Both reuse `security.py`'s guard (`over_limit`, `strike`).

- **Admin through Google:** role checked on every request, never cached in the cookie.

- **Secrets** come from env only: `PARTYLINKS_ACCOUNTS__GOOGLE_CLIENT_ID`, `…__GOOGLE_CLIENT_SECRET`, `…__SESSION_SECRET`, `PARTYLINKS_MAIL__RESEND_API_KEY`. In the admin config view they are masked by `_mask`.

- **Logs** never contain an email address, `sub`, token or cookie (A5). A `logs.py` filter scrubs email-shaped strings as a safety net.

- **Blocked users** cannot sign in, lose every session, and get no email.

## 13. Configuration (`settings.yaml` + env)

```yaml
accounts:
  enabled: true                # false hides every account UI and turns off /auth
  google_client_id: ""         # env PARTYLINKS_ACCOUNTS__GOOGLE_CLIENT_ID
  google_client_secret: null   # env only (SecretStr)
  session_secret: null         # env only (SecretStr): itsdangerous for OAuth state + unsubscribe tokens
  session_days: 30
  max_alerts: 20
  admin_emails: []             # promoted to admin on sign-in
mail:
  resend_api_key: null         # env only; missing = every email off + admin warning
  from_address: "SPOTX <events@spotx.fun>"
  digest_cron: "0 10 * * thu"  # Asia/Jerusalem
  digest_max_events: 12
  instant_min_interval_minutes: 60
  matcher_interval_minutes: 15
```

Google Cloud console: an OAuth client of type "Web application", with redirect URIs `https://spotx.fun/auth/google/callback` and `http://localhost:<port>/auth/google/callback`. The consent screen lists the app name, privacy URL, terms URL and the scopes `openid email profile`, which are non-sensitive, so Google review is light.

## 14. Legal and pages

### `/privacy` (`Privacy.tsx` + `site.privacy`), HE and EN

Replace "we keep no personal data" with sections modelled on CheapFlights' `docs/privacy-policy.md`, adapted:
1. **What we collect, with an account:** Google id, email, name, picture URL, email language, the alerts you create, the events you save, which events matched, email preferences and when consent was given, sign-in dates. Without an account: nothing changes.
2. **Purposes and legal basis:** running the account and alerts (contract); the digest (consent, §30A); security.
3. **Processors:** Railway (hosting and DB), Resend (email delivery), Google (sign-in), Cloudflare.
4. **Transfers abroad:** United States and EU; Israel's adequacy status; SCCs in the providers' terms.
5. **Retention:** until the account is deleted. Hits are removed when the event ends. Sessions last 30 days.
6. **Rights (Amendment 13):** access, correction, deletion. The self-service account page covers them; otherwise the privacy email.
7. **Deletion:** immediate, from `/account`.
8. **Security:** as in section 12.
9. **Emails:** what is sent, how to unsubscribe.
10. **Age:** 16+.

### `/terms` (`Terms.tsx`)

Accounts are optional. Users must be 16 or older. One account per person. We may block abuse. Alerts are best effort: an event can be missed or arrive late, and the ticket seller's terms govern purchases.

### Cookie / storage list (`consent.tsx`)

Add `spotx_session` and the sign-in `session` cookie as necessary, and the localStorage key `spotx-saved` (saved event ids, necessary, kept on the device until cleared). No new optional category, because no Google script loads (D8).

### `docs/legal.md`

Update the table and the "server-side records" section.

### Open legal items (before launch)

- [ ] `site.privacy.email` must be set: rights requests need a working address.

- [ ] Privacy Protection (Data Security) Regulations 2017: the users table is a personal-data database. Write the database definition document and access list, and check which security level applies.

- [ ] Lawyer review of the new privacy text and the §30A consent wording.

## 15. Admin UI (`AdminUsers.tsx`, `AdminEmail.tsx`)

- **users tab:** a search box and a paged table (avatar, name, email, joined, last login, alerts, digest ✓, sessions, role, blocked). A row opens a drawer: alerts with their criteria, the last hits, and the buttons revoke / block / role / delete (delete needs the email typed). On top, stats cards (total, new in 7 and 30 days, opted-in, alerts, instant alerts) and a signups-per-day chart reusing the clicks tab's chart.

- **email tab:** a Resend status card (key set, domain verified, from address); three switches (digest, instant, welcome); "send test to me" per kind; "preview as user" (user picker, then an iframe of the rendered HTML); the last 20 `email_runs`.

- **overview tab:** warnings for "Resend key missing", "Google OAuth not configured" and "privacy email empty".

## 16. Edge cases

| Case | Handling |
| --- | --- |
| Google email changes | Matched by `sub`; the email is updated on the next sign-in |
| Same email, different `sub` (rare) | Refuse with "contact us"; never merge silently |
| An event in an alert is regrouped (`dedup`) | Hits key on the primary. After a regroup, the next pass may add the new primary. Accepted: at most one duplicate, and the digest dedupes by `group_id`. |
| Event removed or ended | Retention deletes its hits (A7) |
| Alert edited | `criteria_hash` changes; old hits stay; only new events match the new criteria |
| A user with 0 alerts opts in to the digest | Gets weekend highlights and free events |
| A saved event ends or is removed | Retention deletes the row. Signed out, `useSaved` drops ids that `/api/events/cards` no longer returns. |
| Hearts on two devices, then sign-in on both | Each device merges its local list; PK `(user_id, event_id)` makes the merge `ON CONFLICT DO NOTHING` |
| Resend down mid-run | Each email has an idempotency key; the next run resends only the unsent |
| Worker restarts mid-digest | `last_digest_at` is stamped per user after a successful send, so a rerun skips those already sent |
| Blocked during an active session | Every `/api/me` call re-reads the user, so the user is signed out at the next request |
| User deletes account between matcher and send | Rows cascade; the send loop re-reads users and skips missing ones |
| `accounts.enabled: false` | Account UI hidden, `/auth` returns 404, jobs idle; the site is unchanged |

## 17. Testing

On the embedded pgserver, one small test file per package:

- `tests/test_accounts.py`
  - callback with a mocked Authlib token: user created, session cookie set;
  - blocked user refused;
  - logout revokes;
  - delete cascades;
  - `next` open-redirect refused;
  - admin by email promoted.
- `tests/test_alerts.py`
  - **parity:** for a set of criteria, `matcher` hits equal what `/events` returns with the same filters;
  - no duplicate hits on a rerun;
  - criteria validation;
  - retention deletes hits.
- `tests/test_mail.py`
  - Resend mocked;
  - digest picks only opted-in users;
  - idempotency key per week;
  - instant 1-per-hour cap;
  - unsubscribe token round-trip and the one-click POST;
  - no email address in captured logs.
- **Production-size timing:** a matcher pass against about 28k events and 50k synthetic alerts, before pushing.

- **Manual:**
  - sign in on localhost with a real Google test client;
  - digest preview in Gmail (HE RTL and EN);
  - mobile at 360 px;
  - `/admin` users and email tabs.

## 17a. CheapFlights code to port (same concept: alerts, save search, notifications)

Read these first, then rewrite them on SQLAlchemy and the PartyLinks feature packages. CheapFlights uses raw psycopg and Next.js; PartyLinks uses SQLAlchemy and Vite + react-router. Paths are relative to `H:\GitHub\cheapflights`.

| PartyLinks file | Port from CheapFlights | What changes |
| --- | --- | --- |
| `accounts/models.py` | `backend/users_db.py` (`users`, `sessions`, `user_settings` DDL; `get_or_create_user`, `delete_user`, `revoke_all_user_sessions`) | SQLAlchemy rows; no flight settings columns; `digest_opt_in` defaults to false |
| `accounts/google.py` | `backend/routers/auth.py` `auth_google` (ID token verify) | Authlib redirect flow instead of the GIS token POST |
| `accounts/sessions.py` | `backend/dependencies.py` (session cookie, token hash, `make_unsubscribe_token`) | 30-day rolling; cookie `spotx_session` |
| `accounts/routes.py` | `routers/auth.py` (`/me`, logout, `DELETE /account`, `email-subscription`, unsubscribe GET + one-click POST) | Paths under `/api/me` |
| `alerts/models.py`, `alerts/criteria.py` | `backend/alerts/schema.py`, `types.py` (`CRITERIA_FIELDS`, `validate_criteria`), `errors.py` | Criteria = the `Filters` subset; validated against the facets |
| `alerts/repo.py`, `alerts/routes.py` | `backend/alerts/repo.py`, `backend/routers/alerts.py` (5 CRUD endpoints, limit) | — |
| `alerts/matcher.py` | `backend/alerts/matcher.py` (`run_matcher`, `_fire`, `list_matches`) | The ratchet becomes "new event never hit"; matching through the `/events` filter builder |
| Bell API (`/api/me/bell`) | `backend/notifications.py`, `backend/routers/notifications.py` | Backed by `alert_hits.seen_at`, not a notifications table |
| `mail/send.py` | `backend/email_service.py` (Resend, idempotency key, `is_account_error`, `_log_send_failure`) | Jinja2 templates; batch API |
| `mail/templates/*` | `backend/email_templates/welcome*.html`, `cheap_report*.html` | Event cards with image, date, venue, weather and price |
| `mail/jobs.py` (worker) | `backend/scheduler.py` `_send_due_cheap_report_emails`, welcome dispatch | APScheduler cron Thu 10:00 in the worker process |
| `web/src/accounts/useMe.ts` | `frontend/src/context/AuthContext.tsx` | react-query hook, no Google script |
| `web/src/accounts/UserMenu.tsx` | `frontend/src/components/UserProfileMenu.tsx` | — |
| `web/src/accounts/AccountPage.tsx` | `frontend/src/components/ProfileClient.tsx` (email toggle, delete account) | — |
| `web/src/accounts/WelcomeSheet.tsx` | `frontend/src/components/WelcomeContent.tsx` | Digest opt-in and the first alert |
| `web/src/alerts/AlertsPage.tsx`, `AlertForm.tsx`, `AlertCard.tsx` | `frontend/src/components/AlertsClient.tsx`, `AlertForm.tsx`, `AlertCard.tsx`, `hooks/useAlerts.ts` | Form fields are the party filters (GenrePicker, regions, slots, free only, when) |
| `web/src/alerts/SaveAlertButton.tsx` ("Save search") | `frontend/src/components/deals/TierAlertCta.tsx`, `roundtrip/DropAlertButton.tsx` | Saves the current filters as an alert |
| `web/src/alerts/Bell.tsx` | `frontend/src/components/NotificationBell.tsx`, `NotificationsClient.tsx`, `utils/notificationsBus.ts` | Polls once a minute, only while the tab is visible |
| `web/src/accounts/AdminUsers.tsx`, `AdminEmail.tsx` | `frontend/src/components/AdminDashboardClient.tsx` (users section), `backend/routers/admin.py` | Plus block, preview and email runs |
| Tests | `backend/tests/test_auth_sessions.py`, `test_alerts_*.py`, `test_email_service.py`, `test_welcome_email_dispatch.py`, `test_admin_auth.py` | pgserver fixtures |
| Legal text | `docs/privacy-policy*.md`, `docs/terms-and-conditions*.md` | Remove flights, bots, analytics and ads; keep the account, email, deletion and rights sections |

| `saved/` (API in `accounts/routes.py` or `saved/routes.py`), `saved_events` table | `backend/routers/saved_items.py`, the saved-items DDL | Event ids instead of flights; merge endpoint for hearts saved while signed out |
| `web/src/saved/useSaved.ts`, `SaveHeart.tsx`, `SavedPage.tsx` | `frontend/src/context/SavedItemsContext.tsx`, `components/SavedFlightsClient.tsx` | Works signed out through localStorage (login-optional rule) |

## 18. Build order (phases; each ships on its own)

0. **Refactor:** move the `/events` filter builder into a reusable function (no behaviour change; existing tests must pass).
1. **Accounts:** models, Authlib flow, sessions, `/api/me`, header sign-in and menu, `/account` (delete included), consent list, admin role via Google, admin users tab. **Legal pages are updated in this same phase**, before any account goes live.
2. **Alerts:** models, CRUD API, `/alerts` page, "Save as alert" on every surface, matcher job, bell, "For you" row.
3. **Email:** `mail/` package, Resend, templates (HE and EN), welcome, instant, digest, unsubscribe, admin email tab, preview. Waits for the Resend account and the DNS records.
4. **Launch:** blog post (HE and EN), landing-page CTAs, docs (`docs/accounts.md`, `docs/admin.md`, `docs/legal.md`, `docs/postgres-migration.md`, `docs/configuration.md`).

New dependencies (pyproject, requirements and lock): `authlib`, `itsdangerous`, `resend`, `jinja2`.

## 19. Risks

| Risk | Mitigation |
| --- | --- |
| The matcher slows the worker (the earlier O(n²) incident) | Group by criteria, restrict to new ids, measure at production size, put the run time in `email_runs` and the admin view |
| §30A fine for an unsolicited advertising email (statutory damages per message) | Opt-in off by default, consent time stored, one-click unsubscribe, sender details in the footer |
| Emails landing in spam | SPF, DKIM and DMARC on spotx.fun; one-click unsubscribe; a steady weekly volume |
| Account takeover through a stolen cookie | HttpOnly and Secure, server-side revocation, admin "revoke all" |
| Personal data breach | Least data kept (no IP or user agent), restricted DB access, logs scrubbed; the 2017 security regulations checklist |
| The page cache serves one user's data to another | `/api/me*` excluded from the page cache, with `private, no-store`; covered by a test |

## 20. Open items

- [ ] Create the Resend account, verify spotx.fun (SPF, DKIM, DMARC), set the env key.

- [ ] Create the Google OAuth client and set the client id and secret in Railway.

- [ ] Set `site.privacy.email`.

- [ ] Lawyer review (privacy text, §30A wording, the 2017 security regulations).

- [ ] Decide whether long-inactive accounts (for example 24 months without sign-in) are deleted after a warning email. Not in v1.
