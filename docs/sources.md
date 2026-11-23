# Sources

SPOTX collects from 155 configured sources, 154 on. Each one has its own module in
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
| `tickchak` | Tickchak LIVE (live.tickchak.co.il): an aggregator across ticketing providers; events on no category page (municipal halls, museums) from its event sitemap (`tickchak_sitemap.py`) | 07:45, 19:45 | yes |
| `selector` | Selector (selector.org.il) | 08:06, 20:06 | yes |
| `drop` | Drop (tdrop.net): self-serve ticketing for party producers: raves, house parties, festivals | 08:17, 20:17 | yes |
| `agol` | AGOL (agol-join.vercel.app): organic & progressive house collective; its one-page sign-up site names the next party (guest list, no price) | 09:37, 21:37 | yes |
| `airdrop` | Airdrop (airdrop.co.il): party guide for clubs, festivals and party trips abroad; listing + sitemap pages + their event links, artists (/artists, Spotify, links), clubs as venue profiles (/clubs: pin, address, logo) | 06:25, 18:25 | yes |
| `hypd` | hypd (hypd.co.il): curated party board; events sitemap + each page's MusicEvent JSON-LD (coordinates, lineup, producer's seller link), genres from its /genre/ links | 07:39, 19:39 | yes |
| `getin` | GETIN (get-in.com / getin.xyz): club-night and festival ticketing, mostly abroad (US, Canada, Dubai, Mexico) and a few Israeli nights; api/filter-events per filter + each event page for currency | 07:42, 19:42 | yes |
| `popit` | Popit (popit.events): party board that aggregates events from ticketing sites | 07:03, 19:03 | yes |
| `chillz` | Chillz (chillz.com) | 07:31, 19:31 | yes |
| `barby` | Barby (barby.co.il): Tel Aviv live-music club | 06:07, 18:07 | yes |
| `grayclub` | Gray Club (grayclub.co.il): live-show club; scraped for every configured branch | 07:52, 19:52 | yes |
| `epgb` | Radio EPGB (epgb.co.il): the club's own programme | 07:38, 19:38 | yes |
| `teder` | Teder (teder.fm): the radio station's nights at Beit Romano, including the Rafi room | 08:27, 20:27 | yes |
| `gagarin` | Gagarin (gagarin.co.il): club site on WordPress + The Events Calendar (`tribe.py`) | 08:46, 20:46 | yes |
| `secrettelaviv` | Secret Tel Aviv (secrettelaviv.com/tickets): curated city listings (parties, live music, culture...) | 07:59, 19:59 | yes |
| `secrettelavivapp` | Secret Tel Aviv App (secrettelaviv.app): the same team's newer Base44 guide, read from the public entities API its pages call (upcoming / live events only, venue locations, categories mapped like the .com's); 3 requests a run, coordinates on almost every event; merged with `secrettelaviv` by dedup | 08:29, 20:29 | yes |
| `gototlv` | Daniel Tel Aviv (gototlv.co.il): a hand-picked two-week Tel Aviv guide (parties, culture, free entry); its page loads `events.json`, the whole edition (~30 events) in one request, plus the linked page's og:image per new event (the guide has no images); no coordinates (streets geocoded in Tel Aviv); party rows link to Go-Out / Eventer / Chillz and merge with them by dedup | 09:50, 21:50 | yes |
| `inperson` | inperson (inperson.social): a Tel Aviv social-meetup app (tables of six, matchmaking evenings, yoga, cocktail workshops), invite-only and age-gated; `/events` lists every coming event in an ItemList JSON-LD, one request per event page for its SocialEvent JSON-LD; its communities (the organizer, `/com/<slug>`) are the categories (dating tables, workshops); no coordinates (the city is geocoded; dating tables hide the venue), ends a day after the start dropped | 08:23, 20:23 | yes |
| `makore` | Makore (makore.co.il, "מה קורה"): an events guide for all of Israel | 09:41, 21:41 | yes |
| `happytlv` | HappyTLV (happytlv.com): Tel Aviv parties, and bars' happy hours (one event per day the deal runs, category **Happy Hour**), plus places across Israel, one event per day each is open (all day when its page gives no hours): escape rooms, attractions, coffee carts, chef restaurants, work spaces, volunteering, clubs, team activities (no address, so no map pin), each its own category. Each place also keeps its weekly hours, rating, Ontopo link, amenities and more (`partylinks.placeinfo`, which adds Ontopo's venues and the place's own website) | 05:13, 17:13 | yes |
| `beatradar` | Beat Radar (beatradar.co.il): a party guide gathering Go-Out and Selector parties, with its own genre tags; merged with those sources by dedup. Blocked for now (see below) | 08:26, 20:26 | yes |
| `yotzim` | Yotzim (yotzim.com): party guide (Tel Aviv nights, nature parties, open bar, 40+, teen parties, festivals, Koh Phangan, holidays), read by category page; each event takes the genre words of every category listing it. Only upcoming events are fetched | 09:12, 21:12 | yes |
| `shotgun` | Shotgun (shotgun.live): electronic music ticketing. Every city page worldwide (240, mostly France, Brazil and the US; ~3,300 events, to the abroad section), plus its Israeli pages, which list nothing yet | 08:52, 20:52 | yes |
| `posh` | Posh (posh.vip): party and nightlife ticketing, almost all in the US (~1,600 events, to the abroad section, in each venue's own time zone). No categories of its own: night out by default, others from the title | 09:17, 21:17 | yes |
| `ticketingo` | Ticketingo (ticketingo.co.il), Sportsevents365's Hebrew storefront: concerts, opera, ballet, theatre and musicals abroad (no sport) | 10:16, 22:16 | yes |
| `showshappening` | ShowsHappening (showshappening.com): ticketing in Malta, the UK, Germany and Spain (~270 events, ~1,000 dates, abroad section) with its own categories and tags. No venue or price (loaded from a private API) | 10:08, 22:08 | yes |
| `eventimlight` | Eventim Light (eventim-light.com): CTS Eventim's self-service box office, a shop per organizer in every market (de, at, ch, uk, nl, es, dk, fi, fr, no, se; ~11,800 events from ~2,000 of ~5,300 known shops, Oct 2026, ~90% on the map). Artwork is fetched as Chrome too (Akamai). No catalogue: shops come from the Common Crawl URL index and are kept in `data/eventimlight_shops.json`, growing run by run; one request per shop (its upcoming events as JSON, with venue, country and coordinates). Israeli venues to Israel, the rest abroad | 04:33, 16:33 | yes |
| `vibez` | Vibez (vibez.io): ticketing for communities and party producers: raves, ecstatic dance, club nights; producers in `projects` (Bamboo Club) are read even when unlisted | 08:36, 20:36 | yes |
| `klika` | KLIKA (klika.live): ticketing app for party lines, a Supabase app read with the anon key its own pages send: the public `events` table (listed and line-only events), each line's genres (`public_seo_catalog`) and the ticket page per event (`public_ticket_page_v2`: tiers, artwork, lineup); no coordinates, the address is geocoded | 11:53, 23:53 | yes |
| `babebar` | Babe Bar (babebar.co.il): club site on WordPress + The Events Calendar (`tribe.py`) | 09:06, 21:06 | yes |
| `plusparty` | Plus Party (plusparty.co.il, "מסיבות פלוס"): guide to parties, shows and festivals across Israel; one request, the `/events` page's embedded query cache (every upcoming event, ~160, with kind, venue, street, city, price, age). No seller link (behind its sign-up); nature parties with a secret place get no venue | 09:19, 21:19 | yes |

## Concerts, theatre, stand-up and shows

| Source | Site | Runs daily at | On |
| --- | --- | --- | --- |
| `ticketmaster` | Ticketmaster Israel (ticketmaster.co.il): concerts, shows and festivals nationwide | 06:49, 18:49 | yes |
| `eventim` | Eventim Israel (eventim.co.il): concerts, theatre, shows and sport; Akamai refuses event pages after ~13: those events come from the city pages' ItemList (`eventim_listing.py`), the run then partial | 08:13, 20:13 | yes |
| `zappa` | Zappa clubs (Herzliya, Tel Aviv Midtown, Jerusalem, Haifa) and Amphi Shuni, on the Eventim platform (`eventim.py`); Akamai refuses event pages after ~13: those events come from Eventim's public search API (`eventim_api.py`, every Zappa show), the run stays clean | 09:47, 21:47 | yes |
| `pres` | PRES ticketing platform (pres.global): Kupat Tel Aviv, Cameri, Habima, the Israeli Opera, the Khan and more | 06:28, 18:28 | yes |
| `pres_il` | PRES box offices that answer Israeli IPs only: Beit Lessin, the Israel Philharmonic (`pres.py`, through the Israeli proxies) | 06:34, 18:34 | yes |
| `smarticket_il` | Smarticket box offices that answer our Israeli VPN only: FRIENDS (`smarticket.py`, through the VPN) | 06:48, 18:48 | yes |
| `kupatbravo` | Kupat Bravo (kupatbravo.co.il): nationwide box office (theatre, concerts, opera, dance, kids) | 06:41, 18:41 | yes |
| `smarticket` | Smarticket - white-label box office used by theatres and municipal culture halls | 06:14, 18:14 | yes |
| `leaan` | Leaan (leaan.co.il): ticketing site for shows, stand-up, kids' shows and sport (Next.js) | 07:17, 19:17 | yes |
| `mevalim` | Mevalim (mevalim.co.il): show listings for every configured region (theatre, stand-up, concerts...) | 06:21, 18:21 | yes |
| `hulyo` | Hulyo (hulyo.co.il): last-minute show deals | 06:42, 18:42 | yes |
| `megatickets` | Megatickets (megatickets.co.il) | 06:00, 18:00 | yes |
| `music80s` | 80sMusic (80smusic.co.il): guide to 80s / 90s / new wave / dark 80s parties; tickets on IWANT | 10:15, 22:15 | yes |
| `iwant` | IWANT (iwant.co.il): Tel Aviv party box office (80s / new wave / dark eighties, grunge, trance) | 09:22, 21:22 | yes |
| `eventbuzz` | Eventbuzz (eventbuzz.co.il): independent productions and community events; unlisted events from the Wayback index, a `site:` search, other sources' ticket links, its own stored upcoming pages (so an archive.org outage misses only new links, not a partial run) and the show pages of venues selling through it (`venue_pages`: Hasimta Theatre, ~49 dates); past ones dropped before saving | 07:10, 19:10 | yes |
| `standupist` | Standupist (standupist.co.il), stand-up portal and box office: its EventON API in one call (repeats, pin, prices), post tags name the artists, the listing page marks sold out | 05:47, 17:47 | yes |
| `standupfactory` | Stand Up Factory (standupfactory.co.il): Tel Aviv's stand-up club (שביל המרץ 5), a show every night; the show board (~6 pages, nearest first), each date's post (GoShow's sale iframe: the purchase link, so it merges with `goshow`) and 2 wp-json calls (permalinks, categories: the comedian as artist, the series as genre); no price or text (GoShow's) | 10:27, 22:27 | yes |
| `comy` | Comy (comy.co.il), stand-up box office. On beside the `comy` host under `pres` (prices, tickets left); dedup merges the two | 06:33, 18:33 | yes |
| `concertevents` | Concert (concert-events.com): Mizrahi / Mediterranean shows and club nights nationwide; the sitemap is in date order, so a binary search finds the first upcoming page (~10 requests), then each upcoming page's MusicEvent JSON-LD, genre chips and contact block (phone, price). No coordinates (venues geocoded), mostly sold by phone | 08:55, 20:55 | yes |
| `hangar11` | Hangar 11 (hangar11.co.il), Tel Aviv Port concert hall: the list view's iCal feed (every upcoming event, the hall's point) and each event page for the ticket link and price; Crawl-Delay 20 s. Also reached via eventer and arenas, merged in dedup | 09:20, 21:20 | yes |
| `jerusalemtheatre` | Jerusalem Theatre (jerusalem-theatre.co.il): theatre, music, dance, festivals and children's shows | 07:33, 19:33 | yes |
| `beershevatheatre` | Be'er Sheva Theatre (b7t.co.il): repertoire, guest shows and children's theatre | 06:53, 18:53 | yes |
| `nikonitai` | Niko Nitai Theatre (nikonitai.smarticket.co.il), Jaffa: plays, festivals and talks; its Smarticket tenant's API is empty, so each performance's JSON-LD is read | 06:39, 18:39 | yes |
| `arenas` | Arenas, stadiums and amphitheaters, from Tickchak LIVE (`tickchak.py`) | 07:57, 19:57 | yes |
| `hamenagen` | HaMenagen (hamenagen.net): Jewish and Hasidic music concert calendar | 04:43, 16:43 | yes |
| `feelbeit` | FeelBeit (feelbeit.com): a Jerusalem arts space and its yearly LAYERS festival of free concerts and DJ sets; one wp-json call (`layers_event`, the Hebrew posts), the venue pinned in config | 06:22, 18:22 | yes |
| `tlvjazzfest` | Tel Aviv-Yafo Jazz Festival (tlvjazzfest26.com): three free days at Beit Romano; the home page's Festival JSON-LD, the schedule (`data.js`) and the press bios (`artists-info.js`): one event per day with its programme, one per headliner | 06:31, 18:31 | yes |
| `israelmusicfest` | Israel Music Festival (israelmusicfest.co.il, חג המוזיקה הישראלית): the Culture Ministry's week of free Israeli-music concerts; wp-json list, each concert's page, and its Wix registration page's JSON-LD (time, address, SoldOut) | 05:53, 17:53 | yes |
| `hatarbut` | Heichal HaTarbut Tel Aviv (hatarbut.co.il): the Charles Bronfman Auditorium's show calendar | 05:07, 17:07 | yes |
| `habama` | Habama (habama.co.il): performing-arts portal: theatre, dance, classical, kids, comedy | 06:11, 18:11 | yes |
| `friendshist` | FRIENDS (friends-hist.co.il): the Histadrut's culture club, shows and plays in halls nationwide | 07:34, 19:34 | yes |
| `barak` | Barak Tickets (barak-tickets.co.il): Haifa box office: concerts, theatre, stand-up, kids; Akamai refuses event pages after ~13: those events come from the city pages' ItemList (`eventim_listing.py`), the run then partial | 07:04, 19:04 | yes |
| `ticket4u` | Ticket4U / Shefa (ticket4u.co.il): Shefa Productions' box office, an Eventim tenant read with the Eventim reader (events sitemap, city pages' ItemList for dates, each page's pageMeta); its Zappa shows are left to `zappa`, and every event is also on eventim.co.il | 04:41, 16:41 | yes |
| `kikarhamusica` | Kikar HaMusica (kikar-hamusica.com): Jerusalem's music square, daily live sets | 03:33, 15:33 | yes |
| `bargiyora` | Bar Giyora (bar-giyora.co.il): live-show bar in Tel Aviv: music, stand-up, lectures | 06:26, 18:26 | yes |
| `havanaclub` | Havana Music Club (havanaclub.co.il): salsa, bachata and kizomba nights on Yigal Alon, Tel Aviv; the Wix ticket page's repeater (styles, weekday, date, the night's /event/ page), each weekday's start from config, no price or artwork | 05:11, 17:11 | yes |
| `parties247` | Parties24/7 (parties247.co.il): a national party guide, mostly Go-Out's parties; the lists its home page links (days, cities, clubs, genres), then each party page's schema.org Event (price, the seller's link: dedup merges it with `goout`) | 05:52, 17:52 | yes |
| `newacropolis` | New Acropolis (newacropolis.org.il): philosophy talks, open evenings, fairs and trips at its branches; the Wix Events sitemap's recently updated pages, each its schema.org Event (the course's sessions and volunteering days left out) | 06:08, 18:08 | yes |
| `beithagefen` | Beit HaGefen (beit-hagefen.com), the Arab-Jewish culture centre in Haifa, and its December festival "חג החגים" (haifahag.com): one CMS; each site's AllEvents cards, then each item page's performances, hall, price, map pin and seller link; the free yard shows and exhibitions are sold nowhere else | 06:13, 18:13 | yes |
| `hamesila` | HaMesila B' (hamesila.org.il), Herzliya's culture and community house: lectures, family shows, concerts; wp-json `events` list + each event page (day, price, seller), the venue pinned in config | 06:17, 18:17 | yes |
| `jerusalemarts` | Jerusalem Arts Festival (jerusalemarts.co.il): two December evenings of theatre, dance, performance and music at the Jerusalem Arts Campus; one wp-json call (each post's schema.org Event) + the tag names | 09:36, 21:36 | yes |
| `bimot` | Bimot Global (bimotglobal.co.il, was bimot.co.il): a producer of dance, music, opera and theatre, mostly at the Israeli Opera; the wp-json show list without the archive + the show types + one page per current show (dates, hall, seller link) | 10:47, 22:47 | yes |
| `israelshow` | IsraelShow (israelshow.co.il): a national listings site selling through its Kupat Bravo white label; ~50 genre / theatre pages list the show ids (the genre pages are its categories), and each show's performances come from the same id on kupatbravo.co.il (the white label's robots.txt disallows all) | 10:19, 22:19 | yes |
| `kzattarbut` | Kzat Tarbut (kzat-tarbut.com): a culture magazine linking the shows it writes about to its Kupat Bravo white label; the wp-json posts (categories + show links), then each show's performances from the same id / slug on kupatbravo.co.il (the white label's robots.txt disallows all) | 10:29, 22:29 | yes |
| `glatticket` | Glatticket (home.glatticket.co.il): Tickchak's box office for the Haredi and religious public; Tickchak hub search API + each event's page (map pin, tiers, organiser) | 06:23, 18:23 | yes |
| `arthurmurray` | Arthur Murray Israel (arthurmurray.co.il): the ballroom school's public evening "The Salon" in Ra'anana; the Wix page's embedded landing (read through the page JSON): date, doors and start, single / couple tickets on PayMe | 07:44, 19:44 | yes |
| `basalon` | Basalon (basalon.co.il): talks, stand-up, shows, plays, kids' shows and workshops in living rooms, pubs and studios nationwide; the category pages' dated cards, then each event page's JSON-LD (no appointment-only, private or at-home listings) | 03:37, 15:37 | yes |
| `radical` | Radical (radical.org.il, "בית רדיקל"): culture house in Jaffa, two halls (auditorium, basement): lectures, films, concerts, workshops, book clubs. WooCommerce behind Cloudflare (`impersonate: chrome`): the calendar's Elementor grids give date (no year: the weekday picks it), time and artwork, paged only while dates are still coming; the Store API adds categories, price range, stock and text (~6 requests). No end times | 09:44, 21:44 | yes |
| `haezor` | HaEzor / The Zone (haezor.com): live-music club in Tel Aviv | 06:36, 18:36 | yes |
| `goshow` | GoShow (goshow.co.il): box office of AMAMA, Stand Up Factory, Comedy Bar and small stages and pubs | 07:21, 19:21 | yes |
| `tzkrti` | Tzkrti (tzkrti.com): the Arab sector's ticketing site: Nazareth, Haifa, Akka, Tarshiha | 07:27, 19:27 | yes |
| `fever` | Fever (feverup.com): Candlelight concerts in Tel Aviv, Jerusalem and Haifa | 07:13, 19:13 | yes |
| `towerofdavid` | Tower of David Museum, Jerusalem: night shows, gallery talks, tours, concerts | 07:47, 19:47 | yes |
| `eventpro` | EventPro (Interdeal) community-centre box offices, 14 sites (Emek Hefer, South Netanya, Netanya's libraries (sifrianet), Even Yehuda, Kadima, Kfar Yona, Modiin, Jerusalem...): shows, kids' plays, stand-up, lectures, films, trips | 06:37, 18:37 | yes |
| `parks` | Israel Nature and Parks Authority (parks.org.il): guided tours, family activities and shows in national parks and nature reserves, by date (its Checkfront booking pages) | 10:13, 22:13 | yes |
| `tamuseum` | Tel Aviv Museum of Art: concerts, gallery talks, tours, lectures, films, workshops | 07:53, 19:53 | yes |
| `jaffatheatre` | Jaffa Theatre (jaffatheatre.org.il): the Arab-Hebrew stage in Jaffa, via its TicketTool box office | 07:18, 19:18 | yes |
| `levontin7` | Levontin 7 (levontin7.com): live-music club in Tel Aviv | 06:46, 18:46 | yes |
| `reading3` | Reading 3 (reading3.co.il): concert hall at the Tel Aviv port | 09:18, 21:18 | yes |
| `zoatlv` | ZOA House (zoatlv.co.il): culture house in Tel Aviv: stand-up, plays, kids' shows, lectures, courses | 07:26, 19:26 | yes |
| `yellowsubmarine` | Yellow Submarine (yellowsubmarine.org.il): live-music club in Jerusalem | 06:58, 18:58 | yes |
| `halutz` | HaHalutz 33 (halutz.co.il): culture bar in Be'er Sheva's Old City, second stage in Sderot | 07:16, 19:16 | yes |
| `viagogo` | viagogo (viagogo.com/il): international ticket resale, events in Israel | 06:44, 18:44 | yes |
| `swaptix` | SwapTix (swaptix.co.il): Israeli ticket resale; events with tickets on sale | 07:09, 19:09 | yes |

## Cinema

| Source | Site | Runs daily at | On |
| --- | --- | --- | --- |
| `planet` | Planet (planetcinema.co.il, ex Yes Planet), cinema screenings | 09:11, 21:11 | yes |
| `ravhen` | Rav-Hen (rav-hen.co.il), cinema screenings on the Planet platform (`planet.py`) | 06:45, 18:45 | yes |
| `cinemacity` | Cinema City (cinema-city.co.il): eight multiplexes, via its PRES host | 09:23, 21:23 | yes |
| `hotcinema` | HOT Cinema (hotcinema.co.il): cinema chain: Modiin, Kiryon, Haifa, Kfar Saba, Petah Tikva, Rehovot.. | 09:35, 21:35 | yes |
| `lev` | Lev cinemas (lev.co.il, "קולנוע לב"): the art-house chain's own box office | 06:59, 18:59 | yes |
| `movieland` | Movieland (movieland.co.il): cinema screenings of the chain's six branches | 08:02, 20:02 | yes |
| `cinematheque_tlv` | Tel Aviv Cinematheque (cinema.co.il, "סינמטק תל אביב"): its PRES box office; festival pages in `festivals` (Soundtrack Tel Aviv 2026) add artwork, the site page and festival genres to their screenings | 10:05, 22:05 | yes |
| `cinematheque_jlm` | Jerusalem Cinematheque (jer-cin.org.il), its PRES box office (`cinematheque_tlv.py`); films' pictures from the cinematheque's own film pages at its robots.txt Crawl-delay 180 (up to 15 a run, each film once) | 10:17, 22:17 | yes |

## Cities, community and guides

Municipal calendars, community centres and event guides: mostly culture, kids and community events,
many of them free.

| Source | Site | Runs daily at | On |
| --- | --- | --- | --- |
| `telaviv` | Tel Aviv-Yafo municipality (tel-aviv.gov.il): the city's own events listing | 05:51, 17:51 | yes |
| `haifamuni` | Haifa municipality (haifa.muni.il): culture, sport, community | 04:57, 16:57 | yes |
| `ramatganmuni` | Ramat Gan municipality (ramat-gan.muni.il): culture, community, seniors | 05:38, 17:38 | yes |
| `petahtikvamuni` | Petah Tikva municipality (petah-tikva.muni.il): its Umbraco events API; Smarticket mirrors left to `smarticket` | 05:16, 17:16 | yes |
| `haderamuni` | Hadera municipality (hadera.muni.il): WordPress sitemap (120 days of lastmod) and event pages; `eventshadera` Smarticket sales left to `smarticket`; Israeli VPN | 05:54, 17:54 | yes |
| `ironit` | Ashdod culture company (ironit.org.il): its calendar feed (admin-ajax `get_events`), REST types and event pages; sales on the city's Smarticket tenant (`ashdod`) left to `smarticket`, so mostly free community events | 09:14, 21:14 | yes |
| `jerusalemmuni` | Jerusalem municipality calendar (jerusalem.muni.il): its GetEventsNew listing, one card per date, a page per event (pin, prices, ticket link); Israeli VPN | 04:37, 16:37 | yes |
| `sharepointmuni` | Municipal event lists on SharePoint (Rishon LeZion); rows sold by the culture hall / Smarticket left to `smarticket`. Off: the site answers home Israeli IPs only | 05:22, 17:22 | no |
| `holon` | Holon municipality (holon.muni.il): free and paid events across the city | 04:09, 16:09 | yes |
| `munievents` | Municipal and council calendars on the A.S. Bina CMS: ~90 cities, councils, community centres | 06:03, 18:03 | yes |
| `matnasnet` | Matnasnet (also `*.atarix.co.il`): the CMS of most community centres and some councils | 05:41, 17:41 | yes |
| `onecity` | OneCity municipal sites (WordPress + GeoDirectory: Karmiel, Even Yehuda, ...) | 04:17, 16:17 | yes |
| `milgammuni` | Metropolinet city multisite (WordPress + Elementor: Bet She'an): events grid, then event pages until 3 past in a row; Israeli VPN | 06:32, 18:32 | yes |
| `ogo` | OGO (ogo.co.il): a human-moderated guide to cultural events across Israel | 06:27, 18:27 | yes |
| `ogo_us` | OGO USA (us.ogo.events): the same guide for the USA, events abroad; no VPN (US site, our VPN is Israeli); robots.txt `Disallow: /`, read by the owner's choice | 09:33, 21:33 | yes |
| `ogo_de` | OGO Germany (de.ogo.events): the same guide for Germany, events abroad; no VPN; same robots.txt | 07:49, 19:49 | yes |
| `muzi` | Muzi (muzi.co.il): community-run guide to concerts and shows across Israel | 05:29, 17:29 | yes |
| `mugics` | Mugics (mugics.com): live-music board where musicians and venues post shows; read from the map-pins API its pages call (30-day windows, ~25 requests, ~390 shows, coordinates on all); ticket links merge with the venues' own listings. robots.txt disallows `/api/` on www, read anyway by the owner's choice | 09:13, 21:13 | yes |
| `efokulam` | EfoKulam (efokulam.co.il, "איפה כולם"): nightlife guide (club nights, parties, festivals, mostly Tel Aviv); read from its wp-json `events` post type (~6 requests, ~50 current events of ~460 rows); no coordinates (addresses geocoded), no venue field; prices from the article's highlighted chips, artists from its "ליינאפ" block (chips / ✓ list); seller links (Go-Out, Eventer) merge with those sources | 08:43, 20:43 | yes |
| `liveisrael` | LiveIsrael (liveisrael.co): party guide (club nights, trance / nature parties, festivals, all of Israel); read from its wp-json `party_events` post type (~6 requests, ~45 current events of ~440 rows), Chrome TLS (Mod_Security refuses plain clients); lineup from its `lineup_artists` field, time / place / age from the article's details list; secret spots keep only the tag city; no price; seller links (Zygo, bit.ly) merge with Zygo | 09:53, 21:53 | yes |
| `tiuli` | Tiuli (tiuli.com): trips and leisure guide, its events section | 04:39, 16:39 | yes |
| `festivalim` | Festivalim (festivalim.co.il): festivals, carnivals and street celebrations | 04:26, 16:26 | yes |
| `poalimatarim` | Poalim Atarim (poalimatarim.com): Bank Hapoalim's free entry to ~50 museums, parks, heritage sites (seasonal) | 09:54, 21:54 | yes |
| `meety` | Meety (meety.co.il): social meetups: salsa and bachata nights, folk dancing, running groups, board games | 06:47, 18:47 | yes |
| `salsavida` | Salsa Vida (salsavida.com): weekly salsa / bachata "Class & Social" nights in Israel (Petah Tikva, Haifa, Rehovot, Kfar Saba, Jerusalem, Tel Aviv); the guide's microdata cards and its load-more windows to 90 days, each series' page for venue and pin; Havana Club left to `havanaclub` | 05:58, 17:58 | yes |
| `yadvashem` | Yad Vashem online reservations (online-reservations.yadvashem.org): the dated public tours (Friday museum tours in Hebrew / English / Russian, the "Living Memory" exhibition tour, synagogue tours) from the booking app's JSON API, places left per slot, 8 months ahead | 08:31, 20:31 | yes |
| `ganeyyehoshua` | Ganei Yehoshua (park.co.il): the Yarkon Park company's own events in its Tel Aviv parks (Yarkon, Park Begin, Ganei HaTeva, Rosh Tzipor): birdwatching and nature tours, family days, sport, Lumagica; the listing page (type, park) + each event's page (every session, prices, meeting point), one event per session; pins from the meeting point (`places`) or the park; Lumagica's Kupat Tel Aviv link merges with `kupat` | 09:52, 21:52 | yes |
| `funzing` | Funzing (il.funzing.com): experiences: workshops, tours, lectures, culinary evenings, family days | 07:19, 19:19 | yes |
| `kolsadna` | Kol Sadna (kolsadna.co.il): a directory of ~1,400 workshops; only the dated sessions anyone can join (the booking script's `a_schedule` on each recently changed workshop page), with the site's kinds as genres | 10:33, 22:33 | yes |
| `easytix` | EASYTIX (easytix.co.il): the nimi ticketing platform and its white-label box offices (Camel Comedy Club, ZOA House, Jerusalem Zoo / Israel Aquarium, Hormesis) | 08:43, 20:43 | yes |

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
| `livetickets` | LiveTickets (livetickets.co.il): tickets to concerts, sports and festivals abroad | 05:37, 17:37 | yes |
| `tickethouse` | Ticket House (ticket-house.com): tickets and packages to concerts and sports abroad | 05:48, 17:48 | yes |
| `ticketeams` | Ticketeams (ticketeams.co.il): football, basketball, tennis, Formula 1 and concerts abroad | 05:49, 17:49 | yes |
| `xplorer` | Xplorer (xplorer.co.il): a flights site's board of concerts and festivals abroad | 03:53, 15:53 | yes |
| `takeabreak` | Take A Break (takebreaktravel.com): packages (flight + hotel, often the ticket) to club nights, concerts, festivals and matches abroad, plus the dated nights of its Dubai / Athens / Limassol / Mykonos / Ibiza lineups and Phangan's full moons; artist and venue profiles | 06:52, 18:52 | yes |
| `dice` | DICE (dice.fm): the whole catalogue (~19k events, every city and country, every kind: gigs, DJ, parties, comedy, talks, theatre, film...) from its sitemaps and event data routes; up to 5000 pages a run, every stored event re-read within 8 runs; venue and artist profiles | 05:14, 17:14 | yes |
| `xceed` | Xceed (xceed.me): its whole upcoming catalogue from the site's public API - club nights, concerts, festivals and shows in every city it sells (~170 cities, ~23 countries; venue profiles weekly) | 06:01, 18:01 | yes |
| `skiddle` | Skiddle (skiddle.com): every UK and Irish city it covers (~220) and every event type; official API with a key, else 5 city pages a run in rotation (~70 trending events each) | 01:28 and 13:28 UTC | yes |
| `eventbrite` | Eventbrite (eventbrite.com): all of Israel every run, and abroad the concerts, club nights, festivals, stage, film, food and holiday events of 150 cities SPOTX lists (city discovery pages in rotation, every city once in 14 days); each event page adds price, lineup and organizer; categories, subcategories and formats mapped to the site's; artist and venue profiles | 09:43, 21:43 | yes |
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
| OnTour | ontour.co.il (Issta Music) sells the same ~16 packages that `issta` already lists |
| Netanya municipality | netanya.muni.il (SharePoint): the events list comes from SharePoint search, and the site blocks `/_api` with a Cloudflare firewall rule; its RSS feed has only the 25 latest-changed pages, without titles. Most of its events are community centres, libraries, Planetanya and the culture hall, which `matnasnet`, `eventpro` and `smarticket` already read (Nov 2026) |
| Ra'anana municipality | raanana.muni.il (WordPress) has no events data of its own: its events block is the Smarticket tenant `tickets.raanana.muni.il` (= raanana.smarticket.co.il), already read by `smarticket`, as is `mishkan.raanana.muni.il` (Nov 2026) |
| Stand Up Factory | standupfactory.co.il: every show page embeds GoShow's purchase frame; all 18 calendar dates already stored (15 by `goshow`), the club's pages are staler (Nov 2026) |
| tarbut.events | An aggregator copying venue sites (~4,300 cards, admin-ajax only, robots.txt disallows ClaudeBot): 94.6% of a 720-card sample already stored, the rest mostly not events (Nov 2026) |
| Cluberim | cluberim.co.il (WordPress): 9 listed events in all, each linking to Go-Out, Zygo or Eventer; all 9 already stored by goout / zygo / eventer (Nov 2026) |
| Tickster | tickster.co.il (~930 event pages) resells Kupat Bravo's seances (tickster.kartisim.co.il white label, same seance ids) and Funzing experiences: 52/52 sampled events already stored by kupatbravo / funzing (Nov 2026) |
| Ticketsi | ticketsi.co.il: an SEO magazine (977 show articles, mostly stale) copying Kupat Bravo / Mevalim titles; one date-only JSON-LD date per article, no seller link: 57/57 future articles sampled already stored (Nov 2026) |
| Jerooz Events | jeroozevents.co.il: Vercel "Deployment Paused" (402 `DEPLOYMENT_DISABLED`, the host's billing switch, not a block) direct, through the Israeli VPN and the free Israeli proxies, with or without Chrome's TLS; no Wayback capture, no other site under the name (re-checked 23 Nov 2026) |
| iShow | ishow.co.il: a Cloudflare JS challenge (403 "Just a moment...") for every client: direct, Chrome's TLS, the Israeli VPN, the free Israeli proxies; behind it the origin is down: challenge-free paths (/favicon.ico) answer Cloudflare 522 "origin timed out" from the Amsterdam and Frankfurt edges and from an Israeli IP (re-checked 23 Nov 2026) |
| Where to Dance Salsa | where-to-dance-salsa.com: Israel = Havana Music Club's weekly nights (read by havanaclub), last checked May 2026 (Nov 2026) |
| Ashdod municipality | ashdod.muni.il's events archive has listed nothing since 2023; the city's events are on `ironit` and the Smarticket tenants `ashdod` / `mishkan-ashdod` (Nov 2026) |
