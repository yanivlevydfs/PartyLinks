# Telegram Bot: Design

**Status:** design validated 2026-11-01. Phases 1a, 1b, 1c and the Telegram part of 2 built, with
usage statistics in the admin: how it works now is [bot.md](bot.md). Not built: the WhatsApp bot and
WhatsApp channel (no posting API for channels).

**Deviations in 1b/1c/2:** buttons carry a short event number (`bot_refs`); reminders and watches are
listed by `/reminders`; the calendar uses `icalendar` (fewer dependencies than `ics`); the digest is for
everything or the last search (no home area and genres columns); the broadcast channel posts once a day.

**Deviations in 1a:** env names follow the app's convention (`PARTYLINKS_BOT__TELEGRAM_TOKEN`,
`PARTYLINKS_BOT__TELEGRAM_WEBHOOK_SECRET`, `PARTYLINKS_BOT__WEBHOOK_URL`); the webhook path is
`/bot/telegram` with the secret in Telegram's header, not in the path; card pictures are the JPEG crops
made for emails (Telegram fetches JPEG reliably, WebP not); `/images/` was already outside the rate limit,
so only the webhook needed an exemption; a search with no results offers an alert too.

## Understanding summary

- **What:** a SPOTX Telegram bot. Users write free text in Hebrew or English ("טכנו בתל אביב בסופ״ש",
  "something free in the north", an artist or a venue) or tap buttons. The bot answers with event cards
  that link to tickets (`/go/`) and to the event page.
- **Why:** to find events and get people to the ticket links, in the most human way a bot can manage.
- **Who:** SPOTX visitors, in Israel and abroad, in private chat and inline (`@bot ...` in any chat).
- **"Human" without an LLM:** the site's Hebrew parser (`search.smart_filters`, `search.date_ranges`,
  "did you mean"), varied phrasing, one follow-up question when a detail is missing, and memory of the
  last search ("ומחר?", "ובצפון?"). With no results, the bot widens the search on its own and says what
  it did.
- **Updates:** alerts are created only by an explicit "🔔 עדכן אותי" plus a confirm. They also cover
  reminders, event watches, artist and venue follows, and an opt-in weekend digest.
- **Code:** its own package, `src/partylinks/bot/`. `core/` holds the logic and knows nothing about
  channels. `telegram/` is a thin adapter. A WhatsApp adapter can be added later without touching `core`.
  A failure in the bot never reaches the site.
- **Config:** secrets in env. Every other parameter is in the admin config tab (`config_overrides`),
  with the env value only as the default. No value is hardcoded.

**Non-goals (phase 1):** an LLM, WhatsApp, group chats, payment or purchase in the bot, and linking
to a site account.

**Phase 2 (next, planned for, not built):** a Telegram channel and a WhatsApp channel that push
interesting events. `core/picks.py` and `core/card.py` serve them too. WhatsApp Channels may have no
public posting API: check before phase 2.

## Assumptions

1. Library: `python-telegram-bot` (PTB), fed by a FastAPI webhook. No polling loop.
2. A reply takes 1–2 s. Search runs in-process against the DB, not over HTTP, so the site's rate
   limit (300 req/min) does not apply to it.
3. Scale: thousands of users. Sends follow Telegram's limits: about 30 messages a second overall
   and 1 a second per chat.
4. Privacy: the bot stores chat_id, language, the last search and the user's alerts, reminders and
   watches. It never stores a name or a phone number, and logs a hashed chat_id only. `/stop` deletes
   everything.
5. Every event card has an image (WebP), weather (rule `weather-every-event`) and a `/go/` link with
   `utm_source=telegram`. Past events never show (rule `never-past-events`).
6. Times use `Asia/Jerusalem`. Events abroad use the place's timezone.

## Decision log

| # | Decision | Alternatives | Why |
|---|---|---|---|
| D1 | No LLM. Existing parser, buttons, varied phrasing | LLM with tools; hybrid | Owner's choice: deterministic, no per-message cost |
| D2 | Telegram first; WhatsApp later on the same `core` | Meta Cloud API; Twilio | Telegram is free and quick. WhatsApp needs a Meta Business account |
| D3 | Alerts in the bot, without a site account | Alerts plus account link; search only | No login rule. Reuses `Criteria` and `Matcher` |
| D4 | Webhook inside the `web` service | Separate polling service | No extra service, direct DB access. Telegram retries what a redeploy drops |
| D5 | Hebrew and English, detected automatically | Hebrew only | Tourists and events abroad |
| D6 | Private chat and inline; no groups | Private only; groups too | Inline covers sharing with friends without group permissions or spam |
| D7 | Secrets in env, parameters in admin (`config_overrides`) | Everything in env | Rule `params-admin-and-db`: tune without a redeploy |
| D8 | `python-telegram-bot` | aiogram 3; raw Bot API on httpx | Most mature. Rate limiter built in. Rule `prefer-libraries` |
| D9 | Own tables (`bot_*`), the same `Criteria` | Reuse `event_alerts` | `event_alerts` has an FK to `users`, which requires Google sub and email |
| D10 | All nine extra features, built in phases 1a, 1b, 1c | A subset | Owner's choice. Phases keep each release testable |
| D11 | Phase 2: broadcast channels on `core/picks.py` | Separate code | One selection and one card for every surface |

## Design

### Components

```
src/partylinks/bot/
  __init__.py      # wiring: router + jobs. The only hook the site sees
  config.py        # bot settings from admin overrides, env defaults
  models.py        # bot_chats, bot_alerts, bot_alert_hits, bot_reminders, bot_watches
  core/
    understand.py  # text (+ last criteria) -> Criteria; smart_filters + date_ranges
    find.py        # Criteria (+ location, sort) -> events; build_event_filters
    reply.py       # human phrasing per situation, he/en
    phrases.py     # the phrase variants (content, not parameters)
    card.py        # event -> card: name, date, place, weather, price/deal, buttons
    memory.py      # last search per chat
    picks.py       # "interesting events" (digest now, channels in phase 2)
  telegram/
    webhook.py     # POST /bot/telegram/{secret}, checks X-Telegram-Bot-Api-Secret-Token
    handlers.py    # commands, text, callbacks, location, inline
    jobs.py        # alerts, reminders, watches, digest; AIORateLimiter
```

**Reused, not copied:** `alerts.criteria.Criteria`, `alerts.matcher.Matcher.match`,
`search.smart_filters`, `api.build_event_filters`, `_best_deals`, alternatives, `/changes`,
`plans`, `weather.py`, `utm.py`, `/images/{id}/640.webp`.

**Hook lines in existing files:** `api.py` (router), `alerts/jobs.py` (bot jobs after `_match`),
`db.py` (models import), the admin config tab (Bot section).

### Tables

- `bot_chats`: channel, chat_id, lang, last_criteria (JSON), digest_on, home_area, genres,
  created_at, blocked_at.
- `bot_alerts`: id, chat, criteria, criteria_hash, active, created_at. Follows are alerts with `q`
  set to the artist or venue.
- `bot_alert_hits`: (alert, event) key. An event is sent once per alert.
- `bot_reminders`: chat, event_id, remind_at, sent_at.
- `bot_watches`: chat, event_id, last_price, last_status.

### Conversation flow

1. The webhook checks the secret, answers 200 at once and handles the update in the background.
2. `memory` loads the chat's last criteria. `understand` builds new `Criteria`. A short follow-up
   ("ומחר?", "and Haifa?") changes only the field it names. Anything that is not a genre, area or date
   becomes `q`, with "did you mean".
3. `find` returns up to N upcoming events. Sort options: date, price, distance. Free and discount
   filters apply.
4. `reply` picks a situation:
   - **Results:** a varied opening, then the cards.
   - **Missing detail:** one question, with area and date buttons.
   - **No results:** widen the date, then the area, and say what was widened.
   - **Not understood:** examples and buttons.
5. Buttons under the results: more, 🔔 alert me, change area, change date.

**Event card:** the image, then name, day and time, venue and city, weather, and price or best deal.
Buttons: 🎟 tickets (`/go/{id}?utm_source=telegram`), ℹ️ details, ⏰ remind me, 👁 watch,
🔁 similar, 👥 who's coming (a `plans` poll and a share link), 📅 calendar (`.ics`, `ics` library).

**Commands:** `/start`, `/alerts`, `/reminders`, `/digest`, `/lang`, `/stop`, `/help`. Sharing a
location runs a "near me" search within the admin radius and shows the distance.

**Inline:** the same `understand` and `find`, up to 10 cards. No memory, no alerts.

### Jobs

Every job runs after the existing matcher, on the worker's scheduler. Every job checks before sending
that the event still exists and is upcoming.

- **Alerts:** `Matcher.match` on new events. At most one message per chat per interval, up to X
  events in a message.
- **Reminders:** at the admin offsets (default 24 h and 2 h), with fresh weather.
- **Watches:** a price drop, last tickets (admin threshold), sold out.
- **Digest:** on the admin day and time (default Thursday), for chats with `digest_on` only, from
  `picks` filtered by `home_area` and `genres`.

### Errors and edge cases

- PTB's error handler logs to `bot.*` with the hashed chat_id and sends the user a short apology.
  The site is unaffected.
- With no `TELEGRAM_BOT_TOKEN`, the bot does not load. One log line says so.
- `setWebhook` runs on startup.
- Flood: N messages a minute per chat (admin), then a gentle "wait".
- Blocked bot (403): set `blocked_at` and stop every job for that chat.
- A stale button or a deleted event: "no longer available", with similar events.
- `callback_data` is at most 64 bytes, so it carries short ids only.
- Text is cut at an admin length limit.
- **Images:** Telegram fetches `/images/...`. Its IPs must not hit the 300 req/min ban (rule
  `rate-limit-page-weight`). Exempt the official Telegram IP ranges. If that is not enough, upload
  the WebP once and cache its `file_id`.

### Config

- **env:** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `TELEGRAM_WEBHOOK_URL`.
- **admin, Bot section:** enabled, results per page, inline results, messages per minute, max text
  length, alert interval, events per alert message, reminder offsets, digest day and time, last
  tickets threshold, near-me radius.

### Testing

- `understand`: a table of about 80 real sentences (he/en) and their expected `Criteria`. Every
  misunderstanding found later becomes a new row.
- `reply` and `card`: no past events, weather present, UTM on `/go/`, `callback_data` within 64 bytes,
  he/en parity.
- `handlers`: every flow on PTB with a mocked bot, no network.
- Jobs: no duplicate sends, deleted events skipped, 403 marks a block, the digest goes to opted-in
  chats only.
- Timing at prod size (about 28k events, thousands of alerts) before any push (rule
  `measure-at-prod-size`).
- Manual: a separate test bot with its own token, through a tunnel to localhost.

### Build phases

- **1a:** search, cards, inline, alerts, near me, price sort.
- **1b:** reminders, event watches, artist and venue follows.
- **1c:** similar, who's coming, calendar, weekend digest.
- **2:** Telegram and WhatsApp broadcast channels.

**Docs updated with the code:** `docs/bot.md` (new), `docs/configuration.md`,
`docs/postgres-migration.md` and its updates log, `docs/architecture.md`.

## Risks

- Image fetches by Telegram triggering the rate-limit ban (see Errors and edge cases).
- Parser accuracy on real phrasing. The sentence table is the guard.
- Webhook updates lost during a redeploy. Telegram retries them, and handlers must be idempotent.
- WhatsApp Channels may have no posting API (phase 2).
