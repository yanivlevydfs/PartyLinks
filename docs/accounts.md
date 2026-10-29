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

## API

| Method | Path | |
| --- | --- | --- |
| GET | `/api/me` | The signed-in user, or 401 (`Cache-Control: private, no-store`) |
| PATCH | `/api/me` | Name, email language, weekly digest consent |
| DELETE | `/api/me` | Delete the account |
| POST | `/api/auth/logout` | Sign out |

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
