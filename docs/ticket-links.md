# Ticket links: UTM tags and click counting

Every ticket link on the site points to `/go/<event id>`, never straight to the seller. This
redirect is the only place a seller's URL leaves the server. On each click it:

1. **Looks up the event.** An unknown event or a non-http(s) URL returns `404`.
2. **Counts the click** in the `clicks` table (`clicks.py`).
3. **Adds UTM tags** to the seller's URL (`utm.py`).
4. **Redirects (302)** with `Cache-Control: private, no-store` and `X-Robots-Tag: noindex, nofollow`.

`/go/` is rate-limited per IP (`security.go_rate_limit_per_minute`, 20 per minute) and disallowed in
`robots.txt`.

## UTM tags

These let sellers see, in their own analytics (GA4 or similar), the visits, clicks and sales that
came from SPOTX, per event.

```
https://seller.example/e/123?ref=x&utm_source=spotx&utm_medium=affiliate
    &utm_campaign=<event slug>&utm_content=buy&utm_term=<city>
```

| Tag | Value |
| --- | --- |
| `utm_source` | `utm.source` in settings (`spotx`) |
| `utm_medium` | `utm.medium` in settings (`affiliate`) |
| `utm_campaign` | The event's slug (or id): one campaign per event |
| `utm_content` | Where on our page it was clicked (see below) |
| `utm_term` | The event's city |

How the seller's URL is handled:

- **Seller parameters stay as they are.** Every other parameter is kept byte for byte.
- **Old UTM tags are replaced.** Any `utm_*` tag already on the URL belonged to whoever linked the
  seller before, so ours replace it.
- **Empty values are left out.**
- **Turning it off.** `utm.enabled: false` disables tagging.

There is no affiliate commission or payment system yet. The tags are for attribution only. A
seller's affiliate ID can be added per source in `utm.py` when there is one.

## Where the click came from (`?at=`)

| Value | Link |
| --- | --- |
| `buy` | Main "buy" button on the event page |
| `alt` | The same event at another seller ("also at") |
| `bar` | Mobile sticky buy bar |
| `seo` | Server-rendered page for crawlers |

Any other value is ignored and counted as `unknown`.

## Click counting

Each click stores the event id, seller, event title, city, placement and time.

- **No personal data.** No IP address, cookie or user agent is stored, so no consent banner entry
  is needed.
- **History outlives events.** Clicks are not tied to events by a foreign key, so they stay after
  retention deletes the event.

The numbers are in the admin console's **clicks** tab and at `GET /admin/api/clicks?days=N`.

Counting started with the deploy of 2026-10-22; there is no history before it.
