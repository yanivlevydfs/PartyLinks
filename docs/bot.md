# The Telegram bot

People write to the SPOTX bot what they look for and get event cards with ticket links. The design
record (decisions D1 to D11, phases): [telegram-bot-design.md](telegram-bot-design.md).

## What it does (phase 1a)

- **Search in plain words**, Hebrew or English: a style, a place, a time, a price, an artist or a venue
  ("טכנו בתל אביב בסופ״ש", "הופעה בחיפה מחר", "something free tonight", "Infected Mushroom"). Chat words
  ("מה יש", "תמצא לי", "please", `search.FILLER`) are dropped. The rest goes to the site's own search
  (`search.smart_filters` through `api.build_event_filters`), so the bot finds what the site finds: every
  category by its label and the words people use for it (`search.SYNONYMS`: "סרט", "הצגה", "הרצאה",
  "תערוכה", "סיור", "movie"...), a day by number ("ב-7.11", "7/11/26"). The site's search box reads the
  same way, and when nothing is found it widens as the bot does (without the date, then without the place)
  and says so (`/events` → `widened`, `widened_q`).
- **Follow-ups** within `bot.memory_minutes`: "ומחר?", "ובחיפה?", "and in Haifa?", "ועד 80" replace that
  part of the last search. "עוד" / "more" shows the next cards.
- **Cheapest first** ("הכי זול", "cheapest"), and **near me**: share a location (the 📍 button) for events
  within `bot.near_km`, nearest first, each with its distance. A follow-up of a near-me search ("ומחר?",
  "הכי זול") stays near, unless it names a place.
- **No results:** the bot widens the date, then the place, and says what it did; still nothing: "did you
  mean" (a button), and an offer to alert when it shows up.
- **Cards:** picture (a JPEG crop, `/images/{id}/mail-1104x552.jpg`), name, day and time in the venue's
  clock, venue and city, weather (the site's MET cache, ~10 days ahead), price or free entry or sale, and two
  buttons: 🎟 tickets (`/go/{id}?at=tg`: counted, UTM `utm_content=tg`) and ℹ️ details (the event page).
- **Alerts:** 🔔 under a search, then a confirm. Never created any other way. The worker matches new events
  every `bot.alert_interval_minutes` and sends at most `alert_max_events` cards a message and
  `alert_max_per_day` a day per chat. No event is sent twice, past events never. At most once: an alert is
  marked sent before it goes out, so one cut short is never resent from its start. A match not sent within a
  day (over a cap) is dropped, and a chat that blocks the bot loses what was waiting: no late news.
- **Inline mode:** "@bot jazz tonight" in any chat offers up to `bot.inline_results` cards to send there
  (no weather: Telegram asks on every keystroke; at most `bot.inline_per_minute` per user).
- **Commands:** `/start`, `/alerts` (tap one to delete it), `/lang` (Hebrew / English; until chosen, the
  language follows the letters of each message), `/help`, `/stop` (deletes the chat's row and its alerts).
- Private chats only; a group gets nothing. Stickers, voice and photos get a friendly "text or location".

## Under every card (phases 1b and 1c)

A second row of buttons: ⏰ 👁 🔁 ➕. They carry the event's short number (`bot_refs`: callback data is at
most 64 bytes, an event id may be longer); an event that is over answers "no longer available".

- **⏰ Remind:** a message `bot.reminder_hours` before the start (default 24 h and 2 h; only those still
  ahead), with the card and fresh weather, saying how long is left. A second tap cancels.
  At most `bot.max_reminders` events a chat.
- **👁 Watch:** a message when the price drops by at least `bot.watch_min_drop_percent`, when it sells out,
  and when tickets are back. A second tap stops. At most `bot.max_watches` a chat.
- **🔁 Similar:** up to `bot.similar_results` upcoming events of its genres in its area (else its genres,
  else its area), never the event or another listing of it.
- **➕ More:** 📅 an `.ics` file (the `icalendar` library: Apple, Outlook, any calendar) and a Google Calendar
  link, the same entry as the site's "add to calendar"; 👥 "who's coming?": a site plan (`plans`, the page
  `/plan/{id}`) with a Telegram share button; ⭐ follow the venue or one of the first three artists: an
  alert on that name, after the same ✅ as every alert.
- **➕ 📍 How to get there** (`core/navigate.py`): the venue as a Telegram map pin (`send_venue`) with Waze
  and Google Maps buttons; no coordinates: says so, with the address. A place (a bar, a restaurant: a
  `happy_hour` listing or an Ontopo match) also gets what the site's place page shows
  (`placeinfo.details.place_details`): opening hours, the phone (+972, tap to call), Ontopo booking
  (`place_info.ontopo_booking`, `?source=spotx`) or its own booking link, and the website. Counted as
  `navigate` and `place_info` in the bot tab.
- **/reminders** (and the keyboard's ⏰ button): the chat's reminders and watches; a tap deletes one.
- **/digest:** opt-in only. Every `bot.digest_weekday` at `bot.digest_hour` (default Thursday 12:00), up to
  `bot.digest_results` weekend picks (`core/picks.py`: the most clicked in the last week first), for
  everything or for the chat's last search. At most once a week.

## The broadcast channel (phase 2)

Set `bot.channel_id` (`@name` or `-100...`) and make the bot an admin of that Telegram channel. Every day at
`bot.channel_hour` it posts up to `bot.channel_results` of tonight's picks (on `bot.digest_weekday`: the
weekend's), never an event it posted there before (`bot_channel_posts`). The channel's cards have the
ticket and details links only. A WhatsApp channel has no posting API: not built.

## Usage statistics (the admin's bot tab)

`GET /admin/api/bot` and the admin's **bot** tab (`web/src/bot/AdminBot.tsx`): whether the bot runs (token,
secret, webhook, channel, digest time), the chats, those active and new in 7 days, those that blocked the
bot, active alerts, pending reminders, watches, digest subscribers, channel posts; over a chosen period:
people, messages, searches, inline queries, ticket clicks from the bot's cards (`/go/...?at=tg`), each per
day, what the bot did (by kind), top searches and searches that found nothing. Rows in `bot_usage` carry a
hash of the chat id, never the id; `/stop` unlinks them; kept `bot.usage_days`.

## The entry screen and the menu

- **Entry screen** (`core/consent.py`): the first message of a new chat, whatever it is, gets the banner
  (`bot.banner_url`, default the site's `/bot-banner-he.png` and `-en.png`, made by
  `scripts/bot_banner.py`; the welcome after "I agree" shows the chat's language) with what the bot does, that it is free, a
  disclaimer and what it keeps, in Hebrew and English, and "I agree" / "Decline". Before "I agree" nothing
  is kept: no `bot_chats` row, no usage tag. "Decline" deletes whatever there was. A chat silent for
  `bot.inactive_days` (365) is deleted with its alerts, reminders and watches. The site's privacy page has
  a section on the bot.
- **Menu** (`core/menu.py`, `/menu`, the 🏠 keyboard button, and under the welcome): categories (the
  site's genre groups, then a group's genres), areas (the six regions and abroad) and dates (tonight,
  tomorrow, the weekend, this week), plus free, near me, my alerts, my reminders and English / עברית.
  Every button carries the whole choice (`m:<screen>:<genre>:<area>:<when>`, under 64 bytes), so the menu
  keeps no state; the pickers replace the pressed message instead of adding one. A choice runs as the
  site's search words ("טכנו בצפון בסופ״ש"), so "more", 🔔 alert me and "and tomorrow?" work on it; under
  its results a bar changes one part and keeps the others.
- **🔥 Top of the week and holidays** (`core/highlights.py`): a menu row with 🔥 the site's weekly top
  (`weekly_top.core.top`, the /top page's events in its order; also asked in words: "הכי חם השבוע", "what's
  hot this week") and a button for each holiday starting within `holiday_pages.rail_days` (like the home
  page's holiday row; 🕎 for Hanukkah, 🎊 the others), with the holiday page's events (`holiday_pages.where`,
  soonest first). No holiday that close: no button. `bot.results` cards a page; "more" carries its offset in
  the button (`hl:t:<n>`, `hl:h:<key>:<n>`). Counted as `top` and `holiday` in the bot tab.
- **🌙 Plan my evening** (`core/evening.py`): a menu button and words ("תכנן לי ערב בתל אביב", "plan an
  evening in Haifa tomorrow"). The place and date are read like any search (tonight by default); no place:
  the chat's last search's place, else the area buttons. Of the first `bot.evening_tries` events found, the
  first with places around it gets the site's evening (`evening.plan.plan`, as on an event's page: the
  nearest of each `evening.steps`), sent as labelled cards in the site's order: 🍸 before (happy hour), 🍽️
  before (dinner), 🎉 the event, ☕ after (morning coffee), each place with its walk from the event. No
  event has any: the first event alone. 🔁 "another evening" carries the search and the next offset
  (`ev:<n>:<words>`, dropped when over 64 bytes): no state. Counted as `evening` in the bot tab.
- **Hebrew first**, like the site: English only when the user writes English or picks it.
- **On the site** (`web/src/bot/TelegramFab.tsx`): a floating Telegram button on every page except /admin,
  bottom-right on desktop and phones (the accessibility button has bottom-left; on phones it sits above the
  tab bar and the event page's buy bar). Shown only while the bot runs (`GET /site` gives its `telegram`
  @name), opening `t.me/<bot>?start=widget`. A speech bubble beside it ("Looking for a party? Ask our bot")
  can be closed and stays closed for 30 days; the button stays. It replaced the home page's banner
  (TelegramPromo, desktop only).

## Code (`src/partylinks/bot/`)

| File | Role |
| --- | --- |
| `config.py` | the `bot:` settings block |
| `models.py` | `bot_chats`, `bot_alerts`, `bot_alert_hits`, `bot_state`, `bot_refs`, `bot_reminders`, `bot_watches`, `bot_channel_posts`, `bot_usage` |
| `core/understand.py` | a message -> search words, cheapest / near, follow-ups, chat commands |
| `core/find.py` | search through the site's filters and ranking; near me; widening |
| `core/card.py`, `core/forecast.py` | the event card and its weather line |
| `core/phrases.py` | everything the bot says, he / en, several ways each |
| `core/conversation.py` | message / location / button -> a `Reply` (no chat app) |
| `core/alerts.py` | match new events, what is due, mark sent / blocked, prune |
| `core/flood.py` | `bot.messages_per_minute` per chat |
| `core/refs.py` | events' short numbers for buttons; the extras every card needs |
| `core/event_actions.py` | the card's ⏰ 👁 🔁 ➕ buttons, /reminders |
| `core/remind.py` | reminders and watches: set, list, due, prune |
| `core/similar.py`, `core/calendar.py` | 🔁 similar events; 📅 the `.ics` file and Google link |
| `core/evening.py` | 🌙 plan my evening: an event and the site's evening around it |
| `core/highlights.py` | 🔥 the week's top and the coming holidays' events in the menu |
| `core/navigate.py` | 📍 the map pin, Waze / Google Maps, a place's hours, phone and booking |
| `core/picks.py`, `core/digest.py`, `core/channel.py` | "interesting events"; /digest; the broadcast channel |
| `core/usage.py`, `admin_routes.py` | usage rows and the admin's bot tab (`GET /admin/api/bot`) |
| `telegram/webhook.py` | `POST /bot/telegram`, starts the bot with the app, sets the webhook |
| `telegram/handlers.py` | Telegram updates -> the conversation |
| `telegram/render.py` | a `Reply` -> Telegram messages, inline results |
| `telegram/jobs.py` | the worker's jobs: alerts, reminders, watches (`bot:alerts`), digest (`bot:digest`), channel (`bot:channel`) |

Hook lines in existing files: `api.py` (the route), `config.py` (`bot:`), `db.py` (the tables),
`scheduler.py` (the job), `security.py` (the webhook is outside the per-IP rate limit and needs no user
agent: all of Telegram's posts come from a few addresses), `utm.py` (the `tg` placement),
`alerts/matcher.py` (`new_event_ids`, shared), `web/src/pages/Admin.tsx` (the bot tab).

## Switching it on

1. In Telegram, talk to [@BotFather](https://t.me/BotFather): `/newbot`, then `/setinline` (a placeholder
   such as "techno tonight") for inline mode. Keep the token.
2. Railway variables of the web service (both processes read them):
   - `PARTYLINKS_BOT__TELEGRAM_TOKEN` = the token
   - `PARTYLINKS_BOT__TELEGRAM_WEBHOOK_SECRET` = a random string (1-256 of `A-Z a-z 0-9 _ -`), e.g.
     `python -c "import secrets; print(secrets.token_urlsafe(32))"` (16 characters at least, or the bot
     stays off; a wrong secret is a strike toward the site's ban, so it cannot be guessed)
3. Deploy. On startup the log says `bot: Telegram webhook at https://spotx.fun/bot/telegram`; the command
   menu is set in Hebrew and English.

Without the secret the bot stays off (logged): updates without it could be posted in anyone's name.
Changes in the admin's config tab apply when applied (the worker rebuilds its scheduler: the alert interval
and `enabled` too); the token and secret are read at startup.

**Locally:** use a second bot (its own token) and an https tunnel to localhost:8000 (for example
`cloudflared tunnel --url http://localhost:8000`), then set `PARTYLINKS_BOT__WEBHOOK_URL` to the tunnel's
address + `/bot/telegram`. Alerts run in the worker's scheduler, which is off locally (scraping is off
there); the search, cards, buttons and inline mode all work.

## Tests

`tests/test_bot_understand.py` (real sentences -> search words; every misunderstanding found later becomes
a row), `tests/test_bot_conversation.py` (the conversation on a database), `tests/test_bot_telegram.py`
(rendering, the webhook's secret, the alert job with a fake bot), `tests/test_bot_features.py` (the card's
buttons, reminders, watches, similar, calendar, plans, follow, digest, channel, usage and the admin tab),
`tests/test_bot_navigate.py` (📍 the pin, its links and a place's info),
`tests/test_bot_highlights.py` (🔥 the week's top, a holiday's button and its events),
`tests/test_bot_evening.py` (🌙 an evening's steps in order, another evening, the area buttons).
