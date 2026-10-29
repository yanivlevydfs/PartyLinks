# Legal and privacy

| Page | Basis | Source |
| --- | --- | --- |
| `/accessibility` | Accessibility statement, Israeli standard IS 5568 | `site.accessibility` in settings |
| `/privacy` | Privacy and cookie policy, Privacy Protection Law (Amendment 13) | `site.privacy` in settings, and the storage table in `web/src/consent.tsx` |
| `/terms` | Terms of use: SPOTX only aggregates listings sold on other sites; optional accounts (16+, one per person, best-effort alerts, blocking) | `web/src/pages/Terms.tsx` |

## Cookies and tracking

- **No cookies for ads or analytics.** The site sets none.
- **Necessary browser storage** (settings, consent choice) needs no consent, but it is listed on
  the privacy page.
- **The only optional category is external content:** OpenFreeMap map tiles
  (`tiles.openfreemap.org`, OpenStreetMap data), which load only after consent.
- **Fonts are self-hosted.**

The cookie banner and the list of everything stored on the device live in `web/src/consent.tsx`.
Keep that list in sync with the code.

## Optional accounts (personal data, since 2026-10-29)

Signing in with Google is optional; browsing, search, the map and ticket links never need it. Saving an
event or an alert does. For those who sign in, the `users`
table keeps the Google id, email, name, picture URL, email language, saved events and alerts, and the
weekly-digest consent with its time (Communications Law §30A: the digest is off until the user ticks
it). No IP address or user agent is kept with an account. Deleting the account removes everything at
once. Processors: Railway (hosting, DB), Google (sign-in), Resend (email). The privacy page section
"User account (optional)" and the terms section "Accounts, alerts and emails" describe this; the
storage list has `spotx_session`, `spotx_signin` and `spotx-saved` (saved event ids on the device, used only
where Google sign-in is not configured, and moved into the account at sign-in). Emails come from
`SPOTX <events@spotx.fun>` (a sender with no inbox); replies and every request go to contact@spotx.fun, which
the site shows only on a click (never in the HTML or as plain text in `/site`). Each email has a one-click
unsubscribe; the digest goes only to users who ticked it. See [accounts](accounts.md).

## Server-side records with no personal data

- **Ticket-link clicks** store no IP address, cookie or user agent (see [ticket links](ticket-links.md)).
- **UTM tags** go to the seller in the link itself; we set nothing in the visitor's browser.

Adding client-side analytics later would mean updating `consent.tsx` and the privacy page.

## Open items

- [x] **Privacy contact email:** `site.privacy.email` is contact@spotx.fun (also the contact address).
- [ ] **Accessibility coordinator:** `site.accessibility.email` is contact@spotx.fun; the coordinator's name is still empty.
  The law requires a reachable coordinator.
- [ ] Have a lawyer review the privacy policy text, the account and email sections, and the §30A
  consent wording.
- [ ] Privacy Protection (Data Security) Regulations 2017: `users` is a personal-data database. Write
  its definition document and access list, and check which security level applies.
