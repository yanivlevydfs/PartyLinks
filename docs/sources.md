# Sources

Pulse collects from 47 configured sources. Each one has its own module in
`src/partylinks/scrapers/` and its own block under `sources:` in `config/settings.yaml`.

- **Schedule:** every source runs twice a day, on its own staggered minute (Israel time, the
  `cron` of its block).
- **Where scraping happens:** only on Railway. Locally the scheduler is always off.
- **How sites are read:** scrapers read a site's own JSON or schema.org data over plain HTTP
  where it exists. Otherwise they parse HTML with selectors kept in config. No browser is used.
- **Ticket links:** every link leads back to the seller through `/go/`
  (see [ticket links](ticket-links.md)).

The module docstring at the top of each scraper explains how that site is read: endpoints,
pagination and what the listing and the event page provide.

## Parties and nightlife

| Source | Site | Runs daily at | On |
| --- | --- | --- | --- |
| `goout` | GO-OUT (go-out.co) | 06:35, 18:35 | yes |
| `eventer` | Eventer (eventer.co.il) | 06:56, 18:56 | yes |
| `zygo` | ZYGO (zygo.co.il) | 07:24, 19:24 | yes |
| `tickchak` | Tickchak LIVE (live.tickchak.co.il): an aggregator across ticketing providers | 07:45, 19:45 | yes |
| `selector` | Selector (selector.org.il) | 08:06, 20:06 | yes |
| `drop` | Drop (tdrop.net): self-serve ticketing for party producers: raves, house parties, festivals | 08:17, 20:17 | yes |
| `popit` | Popit (popit.events): party board that aggregates events from ticketing sites | 07:03, 19:03 | yes |
| `chillz` | Chillz (chillz.com) | 07:31, 19:31 | yes |
| `barby` | Barby (barby.co.il): Tel Aviv live-music club | 06:07, 18:07 | yes |
| `grayclub` | Gray Club (grayclub.co.il): live-show club; scraped for every configured branch | 07:52, 19:52 | yes |
| `epgb` | Radio EPGB (epgb.co.il): the club's own programme | 07:38, 19:38 | yes |
| `teder` | Teder (teder.fm): the radio station's nights at Beit Romano, including the Rafi room | 08:27, 20:27 | yes |
| `gagarin` | Gagarin (gagarin.co.il): club site on WordPress + The Events Calendar (`tribe.py`) | 08:46, 20:46 | yes |
| `secrettelaviv` | Secret Tel Aviv (secrettelaviv.com/tickets): curated city listings (parties, live music, culture...) | 07:59, 19:59 | yes |
| `makore` | Makore (makore.co.il, "מה קורה"): an events guide for all of Israel | 09:41, 21:41 | yes |

## Concerts, theatre, stand-up and shows

| Source | Site | Runs daily at | On |
| --- | --- | --- | --- |
| `ticketmaster` | Ticketmaster Israel (ticketmaster.co.il): concerts, shows and festivals nationwide | 06:49, 18:49 | yes |
| `eventim` | Eventim Israel (eventim.co.il): concerts, theatre, shows and sport | 08:13, 20:13 | yes |
| `zappa` | Zappa clubs (Herzliya, Tel Aviv Midtown, Jerusalem, Haifa) and Amphi Shuni, on the Eventim platform (`eventim.py`) | 09:47, 21:47 | yes |
| `pres` | PRES ticketing platform (pres.global): Kupat Tel Aviv, Cameri, Habima, the Israeli Opera, the Philharmonic, Beit Lessin, the Khan and more | 06:28, 18:28 | yes |
| `kupatbravo` | Kupat Bravo (kupatbravo.co.il): nationwide box office (theatre, concerts, opera, dance, kids) | 06:41, 18:41 | yes |
| `smarticket` | Smarticket - white-label box office used by theatres and municipal culture halls | 06:14, 18:14 | yes |
| `leaan` | Leaan (leaan.co.il): ticketing site for shows, stand-up, kids' shows and sport (Next.js) | 07:17, 19:17 | yes |
| `mevalim` | Mevalim (mevalim.co.il): show listings for every configured region (theatre, stand-up, concerts...) | 06:21, 18:21 | yes |
| `hulyo` | Hulyo (hulyo.co.il): last-minute show deals | 06:42, 18:42 | yes |
| `megatickets` | Megatickets (megatickets.co.il) | 06:00, 18:00 | yes |
| `eventbuzz` | Eventbuzz (eventbuzz.co.il): independent productions and community events | 07:10, 19:10 | yes |
| `comy` | Comy (comy.co.il), stand-up box office. Off: the `comy` host under `pres` reads the same shows with prices | 06:33, 18:33 | no |
| `jerusalemtheatre` | Jerusalem Theatre (jerusalem-theatre.co.il): theatre, music, dance, festivals and children's shows | 07:33, 19:33 | yes |
| `beershevatheatre` | Be'er Sheva Theatre (b7t.co.il): repertoire, guest shows and children's theatre | 06:53, 18:53 | yes |
| `arenas` | Arenas, stadiums and amphitheaters, from Tickchak LIVE (`tickchak.py`) | 07:57, 19:57 | yes |

## Cinema

| Source | Site | Runs daily at | On |
| --- | --- | --- | --- |
| `planet` | Planet (planetcinema.co.il, ex Yes Planet), cinema screenings | 09:11, 21:11 | yes |
| `ravhen` | Rav-Hen (rav-hen.co.il), cinema screenings on the Planet platform (`planet.py`) | 06:45, 18:45 | yes |
| `cinemacity` | Cinema City (cinema-city.co.il): eight multiplexes, via its PRES host | 09:23, 21:23 | yes |
| `hotcinema` | HOT Cinema (hotcinema.co.il): cinema chain: Modiin, Kiryon, Haifa, Kfar Saba, Petah Tikva, Rehovot.. | 09:35, 21:35 | yes |
| `lev` | Lev cinemas (lev.co.il, "קולנוע לב"): the art-house chain's own box office | 06:59, 18:59 | yes |
| `movieland` | Movieland (movieland.co.il): cinema screenings of the chain's six branches | 08:02, 20:02 | yes |
| `cinematheque_tlv` | Tel Aviv Cinematheque (cinema.co.il, "סינמטק תל אביב"): its PRES box office | 10:05, 22:05 | yes |
| `cinematheque_jlm` | Jerusalem Cinematheque (jer-cin.org.il), its PRES box office (`cinematheque_tlv.py`) | 10:17, 22:17 | yes |

## Tech and community

| Source | Site | Runs daily at | On |
| --- | --- | --- | --- |
| `luma` | Luma (luma.com): RSVP pages for tech meetups, hi-tech conferences, startup and community events | 07:23, 19:23 | yes |
| `meetup` | Meetup (meetup.com): community meetups: tech talks, developer groups, startup and social events | 07:41, 19:41 | yes |
| `geektime` | Geektime (geektime.co.il/event/): the hi-tech events calendar: meetups, conferences, workshops | 07:29, 19:29 | yes |
| `peoplecomputers` | People & Computers (anashim umahshevim, events.co.il): Israel's hi-tech conference organizer | 07:37, 19:37 | yes |

## Abroad

| Source | Site | Runs daily at | On |
| --- | --- | --- | --- |
| `tiktik` | TikTik Online (tiktik-online.co.il): tickets and packages for concerts and festivals abroad | 04:27, 16:27 | yes |
| `kishreyteufa` | Kishrey Teufa (kishrey-teufa.co.il): tickets to concerts and festivals abroad | 03:07, 15:07 | yes |
| `ticketplace` | Ticket Place (ticketplace.co.il): resale tickets to concerts and festivals abroad | 03:19, 15:19 | yes |
| `livevents` | Live Events (livevents.co.il): packages (flight + hotel + ticket) and tickets to shows in Europe | 03:31, 15:31 | yes |
| `issta` | Issta (issta.co.il): packages (flight + hotel + ticket) to concerts abroad | 04:47, 16:47 | yes |

## Packages abroad (flight + hotel + ticket)

Issta and Live Events sell packages to shows abroad. Only the show is stored as an event; the
description says it is a package.

Issta's listing gives the trip dates but not the show date. The package page is disallowed by
its robots.txt, so an Issta event spans the trip, from departure to return.

## Blocked: Chillz

Chillz event pages sit behind a Vercel Security Checkpoint, a JavaScript challenge. Since
2026-10-22 the scraper gets `429` with `X-Vercel-Mitigated: challenge` on event pages, even from
a residential Israeli IP, so a plain proxy does not help. Its runs are recorded as `blocked`.

Pages saved by hand into `data/chillz_inbox/` are still imported on every run, but no manual
work is planned. The scraper stays as it is.

## Checked and not added

| Site | Why |
| --- | --- |
| T-TLV | No live site at t-tlv.com |
| Ticketnet | ticketnet.co.il no longer answers (last working capture 2024) |
| GetIn | The public catalogue has no Israeli events right now. The getin.xyz sitemap and schema.org pages would work if that changes |
