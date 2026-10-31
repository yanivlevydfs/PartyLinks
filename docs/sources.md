# Sources

SPOTX collects from 88 configured sources, all on. Each one has its own module in
`src/partylinks/scrapers/` and its own block under `sources:` in `config/settings.yaml`.

- **Schedule:** every source runs twice a day, on its own staggered minute (Israel time, the
  `cron` of its block).
- **Bad runs:** a run that failed, was blocked, found no events or far fewer than usual is retried after 15
  minutes, 1 hour and 3 hours; a source still bad after that is paused and the admins are emailed (see
  [admin](admin.md), "Run manager").
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
| `airdrop` | Airdrop (airdrop.co.il): party guide for clubs, festivals and party trips abroad | 06:25, 18:25 | yes |
| `popit` | Popit (popit.events): party board that aggregates events from ticketing sites | 07:03, 19:03 | yes |
| `chillz` | Chillz (chillz.com) | 07:31, 19:31 | yes |
| `barby` | Barby (barby.co.il): Tel Aviv live-music club | 06:07, 18:07 | yes |
| `grayclub` | Gray Club (grayclub.co.il): live-show club; scraped for every configured branch | 07:52, 19:52 | yes |
| `epgb` | Radio EPGB (epgb.co.il): the club's own programme | 07:38, 19:38 | yes |
| `teder` | Teder (teder.fm): the radio station's nights at Beit Romano, including the Rafi room | 08:27, 20:27 | yes |
| `gagarin` | Gagarin (gagarin.co.il): club site on WordPress + The Events Calendar (`tribe.py`) | 08:46, 20:46 | yes |
| `secrettelaviv` | Secret Tel Aviv (secrettelaviv.com/tickets): curated city listings (parties, live music, culture...) | 07:59, 19:59 | yes |
| `makore` | Makore (makore.co.il, "מה קורה"): an events guide for all of Israel | 09:41, 21:41 | yes |
| `happytlv` | HappyTLV (happytlv.com): Tel Aviv parties, and bars' happy hours (one event per day the deal runs, category **Happy Hour**), plus places across Israel, one event per day each is open (all day when its page gives no hours): escape rooms, attractions, coffee carts, chef restaurants, work spaces, volunteering, clubs, team activities (no address, so no map pin), each its own category | 05:13, 17:13 | yes |
| `beatradar` | Beat Radar (beatradar.co.il): a party guide gathering Go-Out and Selector parties, with its own genre tags; merged with those sources by dedup. Blocked for now (see below) | 08:26, 20:26 | yes |
| `yotzim` | Yotzim (yotzim.com): party guide (Tel Aviv nights, nature parties, open bar, 40+, teen parties, festivals, Koh Phangan, holidays), read by category page; each event takes the genre words of every category listing it. Only upcoming events are fetched | 09:12, 21:12 | yes |
| `shotgun` | Shotgun (shotgun.live): electronic music ticketing. Every city page worldwide (240, mostly France, Brazil and the US; ~3,300 events, to the abroad section), plus its Israeli pages, which list nothing yet | 08:52, 20:52 | yes |
| `posh` | Posh (posh.vip): party and nightlife ticketing, almost all in the US (~1,600 events, to the abroad section, in each venue's own time zone). No categories of its own: night out by default, others from the title | 09:17, 21:17 | yes |
| `showshappening` | ShowsHappening (showshappening.com): ticketing in Malta, the UK, Germany and Spain (~270 events, ~1,000 dates, abroad section) with its own categories and tags. No venue or price (loaded from a private API) | 10:08, 22:08 | yes |
| `eventimlight` | Eventim Light (eventim-light.com): CTS Eventim's self-service box office, a shop per organizer in every market (de, at, ch, uk, nl, es, dk, fi, fr, no, se; ~11,800 events from ~2,000 of ~5,300 known shops, Oct 2026, ~90% on the map). Artwork is fetched as Chrome too (Akamai). No catalogue: shops come from the Common Crawl URL index and are kept in `data/eventimlight_shops.json`, growing run by run; one request per shop (its upcoming events as JSON, with venue, country and coordinates). Israeli venues to Israel, the rest abroad | 04:33, 16:33 | yes |
| `vibez` | Vibez (vibez.io): ticketing for communities and party producers: raves, ecstatic dance, club nights | 08:36, 20:36 | yes |
| `babebar` | Babe Bar (babebar.co.il): club site on WordPress + The Events Calendar (`tribe.py`) | 09:06, 21:06 | yes |

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
| `comy` | Comy (comy.co.il), stand-up box office. On beside the `comy` host under `pres` (prices, tickets left); dedup merges the two | 06:33, 18:33 | yes |
| `jerusalemtheatre` | Jerusalem Theatre (jerusalem-theatre.co.il): theatre, music, dance, festivals and children's shows | 07:33, 19:33 | yes |
| `beershevatheatre` | Be'er Sheva Theatre (b7t.co.il): repertoire, guest shows and children's theatre | 06:53, 18:53 | yes |
| `arenas` | Arenas, stadiums and amphitheaters, from Tickchak LIVE (`tickchak.py`) | 07:57, 19:57 | yes |
| `hamenagen` | HaMenagen (hamenagen.net): Jewish and Hasidic music concert calendar | 04:43, 16:43 | yes |
| `hatarbut` | Heichal HaTarbut Tel Aviv (hatarbut.co.il): the Charles Bronfman Auditorium's show calendar | 05:07, 17:07 | yes |
| `habama` | Habama (habama.co.il): performing-arts portal: theatre, dance, classical, kids, comedy | 06:11, 18:11 | yes |
| `friendshist` | FRIENDS (friends-hist.co.il): the Histadrut's culture club, shows and plays in halls nationwide | 07:34, 19:34 | yes |
| `barak` | Barak Tickets (barak-tickets.co.il): Haifa box office: concerts, theatre, stand-up, kids | 07:04, 19:04 | yes |
| `kikarhamusica` | Kikar HaMusica (kikar-hamusica.com): Jerusalem's music square, daily live sets | 03:33, 15:33 | yes |
| `bargiyora` | Bar Giyora (bar-giyora.co.il): live-show bar in Tel Aviv: music, stand-up, lectures | 06:26, 18:26 | yes |
| `haezor` | HaEzor / The Zone (haezor.com): live-music club in Tel Aviv | 06:36, 18:36 | yes |
| `levontin7` | Levontin 7 (levontin7.com): live-music club in Tel Aviv | 06:46, 18:46 | yes |
| `zoatlv` | ZOA House (zoatlv.co.il): culture house in Tel Aviv: stand-up, plays, kids' shows, lectures, courses | 07:26, 19:26 | yes |
| `yellowsubmarine` | Yellow Submarine (yellowsubmarine.org.il): live-music club in Jerusalem | 06:58, 18:58 | yes |
| `halutz` | HaHalutz 33 (halutz.co.il): culture bar in Be'er Sheva's Old City, second stage in Sderot | 07:16, 19:16 | yes |
| `viagogo` | viagogo (viagogo.com/il): international ticket resale, events in Israel | 06:44, 18:44 | yes |

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

## Cities, community and guides

Municipal calendars, community centres and event guides: mostly culture, kids and community events,
many of them free.

| Source | Site | Runs daily at | On |
| --- | --- | --- | --- |
| `telaviv` | Tel Aviv-Yafo municipality (tel-aviv.gov.il): the city's own events listing | 05:51, 17:51 | yes |
| `haifamuni` | Haifa municipality (haifa.muni.il): culture, sport, community | 04:57, 16:57 | yes |
| `ramatganmuni` | Ramat Gan municipality (ramat-gan.muni.il): culture, community, seniors | 05:38, 17:38 | yes |
| `holon` | Holon municipality (holon.muni.il): free and paid events across the city | 04:09, 16:09 | yes |
| `munievents` | Municipal and council calendars on the A.S. Bina CMS: ~90 cities, councils, community centres | 06:03, 18:03 | yes |
| `matnasnet` | Matnasnet (also `*.atarix.co.il`): the CMS of most community centres and some councils | 05:41, 17:41 | yes |
| `onecity` | OneCity municipal sites (WordPress + GeoDirectory: Karmiel, Even Yehuda, ...) | 04:17, 16:17 | yes |
| `ogo` | OGO (ogo.co.il): a human-moderated guide to cultural events across Israel | 06:27, 18:27 | yes |
| `muzi` | Muzi (muzi.co.il): community-run guide to concerts and shows across Israel | 05:29, 17:29 | yes |
| `tiuli` | Tiuli (tiuli.com): trips and leisure guide, its events section | 04:39, 16:39 | yes |
| `festivalim` | Festivalim (festivalim.co.il): festivals, carnivals and street celebrations | 04:26, 16:26 | yes |
| `poalimatarim` | Poalim Atarim (poalimatarim.com): Bank Hapoalim's free entry to ~50 museums, parks, heritage sites (seasonal) | 09:54, 21:54 | yes |
| `meety` | Meety (meety.co.il): social meetups: salsa and bachata nights, folk dancing, running groups, board games | 06:47, 18:47 | yes |
| `funzing` | Funzing (il.funzing.com): experiences: workshops, tours, lectures, culinary evenings, family days | 07:19, 19:19 | yes |
| `easytix` | EASYTIX (easytix.co.il): the nimi ticketing platform and its white-label box offices (Camel Comedy Club, ZOA House, Jerusalem Zoo / Israel Aquarium, Hormesis) | 08:37, 20:37 | yes |

## Tech and community

| Source | Site | Runs daily at | On |
| --- | --- | --- | --- |
| `luma` | Luma (luma.com): RSVP pages for tech meetups, hi-tech conferences, startup and community events | 07:23, 19:23 | yes |
| `eventbrite` | Eventbrite (eventbrite.com/d/israel/all-events/): conferences, meetups, workshops, tours and parties in Israel; its categories and formats are mapped to the site's | 09:43, 21:43 | yes |
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
| `livetickets` | LiveTickets (livetickets.co.il): tickets to concerts, sports and festivals abroad | 05:37, 17:37 | yes |
| `tickethouse` | Ticket House (ticket-house.com): tickets and packages to concerts and sports abroad | 05:48, 17:48 | yes |
| `ticketeams` | Ticketeams (ticketeams.co.il): football, basketball, tennis, Formula 1 and concerts abroad | 05:49, 17:49 | yes |
| `xplorer` | Xplorer (xplorer.co.il): a flights site's board of concerts and festivals abroad | 03:53, 15:53 | yes |
| `megaevents` | Mega Events (mega-events.co.il): packages (flight + hotel + ticket) to concerts and matches abroad | 05:43, 17:43 | yes |

## Prices and availability: source quirks

- **Tickchak / arenas:** a `minPrice` / `maxPrice` of 0 / 0 means Tickchak has no price (phone-only,
  sold out, priced on the seller's page), not free entry. It is stored as no price.
- **Smarticket:** a performance with `availability: false`, `tickets_available: false` and no
  `unavailability_msg` reads "רכישה טלפונית בלבד" on the seller's page and is sold out on
  Tickchak, so it is stored as sold out. With a message (often an external link) it is sold
  elsewhere and stays closed, not sold out.

## Packages abroad (flight + hotel + ticket)

Issta and Live Events sell packages to shows abroad. Only the show is stored as an event; the
description says it is a package.

Issta's listing gives the trip dates but not the show date. The package page is disallowed by
its robots.txt, so an Issta event spans the trip, from departure to return.

## Chillz: the Vercel checkpoint

Chillz event pages sit behind a Vercel Security Checkpoint. From 2026-10-22 it answered `429` with
`X-Vercel-Mitigated: challenge`, even from a residential Israeli IP: it refused Python's TLS handshake,
not the address. The source now speaks as Chrome (`http.impersonate: chrome`, curl_cffi), which passes,
through the usual proxy. If the checkpoint still answers, the run stops after two requests and is recorded
as `blocked`. Pages saved by hand into `data/chillz_inbox/` are still imported on every run.

## Blocked: Beat Radar

Beat Radar's Cloudflare admits Israeli home connections only (October 2026). Railway, the Evomi proxy IPs
(USA) and Israeli datacenter proxies all get its `403` block page, which is recorded as a `blocked` run, not
a crash. The source has `israel_proxy: true` and speaks as Chrome (`http.impersonate: chrome`), so an Israeli
IP that reaches it also passes Cloudflare's client check. The fix needs an Israeli residential or ISP proxy
first in `PARTYLINKS_HTTP__ISRAEL_PROXY`.

## Checked and not added

| Site | Why |
| --- | --- |
| T-TLV | No live site at t-tlv.com |
| Ticketnet | ticketnet.co.il no longer answers (last working capture 2024) |
| GetIn | The public catalogue has no Israeli events right now. The getin.xyz sitemap and schema.org pages would work if that changes |
