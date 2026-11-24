---
name: new-source
description: Add a new SPOTX source / scraper / scanner (סורק, סקאנר) for an events site, end to end - site recon, scraper module, categories, map, stores, tests, deploy and production check. Use whenever the user gives a site URL to add, asks for a "smart scanner" like Chillz, or asks to fix how a new source was added.
---

# New source (scanner)

Follow `docs/adding-a-source.md` step by step. It is the procedure; this skill only says how to run it.

## Mandatory for every source (none may be skipped)

- **Full coverage: every category and every menu (the owner's rule, 23.11.2026).** List every
  section the site has: the main menu and its sub-menus, every category / genre / tag page (try the
  ids or slugs around the known ones, e.g. `/category/1..60`), the footer links, the sitemap, the
  calendar and any events API. The scanner reads all of them, and every section that holds events is
  read. Write the list (section → read / not read and why) in the source's config comment. "Not
  read" only for what truly is no event (a blog, a shop, a gift card, past archive). Tzavta's
  Short Theatre Festival (`/category/17`) was missing because only some categories were read. This
  applies to existing scanners too: when touching one, check its site's sections again.
- **Every event in every section is extracted.** Pagination runs to the last page (no `max_pages` / limit
  caps that cut it off; "load more" / infinite-scroll APIs followed to the end); no fixed N-day horizon
  when the site lists further ahead (planet / ravhen read only 14 days); every date / performance of an
  event is read, not only the first. Where the site shows a count per category, the scanner's count
  matches it: write both numbers in the report.
- **Every slug, not only the listed ones (the owner's rule, 24.11.2026).** Events / products that no
  category or menu lists still have a page: collect their slugs from every place that names one, and
  read each: the XML sitemaps (every entry of the index; one that answers HTML is broken, say so), the
  HTML site map page (`/sitemap/`, `/site-map/`), landing pages (every page of the page / post sitemaps),
  the home page, sibling subdomains and their sitemaps (`app.`, `tickets.`, `shop.`), the box office's
  own lists (an Eventer profile `/user/<u>/getData`, a Smarticket tenant), sequential ids in the backing
  API (scan 1, 2, 3... to a gap of misses past the highest known id), the Wayback index when the site
  has none of these. Count what each one adds over the categories and write it in the config comment.
  toMix: 132 of 540 products were in no category (only `/sitemap/` named them, Anafaza among them), and
  BiggerPicture master 30 (a February show) has no app page (only the id scan finds it).
  **Links from other sources too:** a box office / ticket site whose events can be link-only (a
  link-in-bio party, a private Eventbuzz / Go-Out / Ticket Tailor page) reads `self.linked_urls` (other
  sources' `purchase_url`s + its own stored pages) and sets `link_hosts = ("<its host>", ...)` on the
  scraper class, so links to it inside other sources' descriptions (a municipality's page) are read too.
  A link the owner sends that nothing names goes in the source's `extra_slugs` / `event_pages` config
  with its date (eventbuzz `rainn26`, `dmrt66`). The stored `url` is the page the owner knows (iwant's
  `/<slug>`, not `event.php?id=N`) when the site has one.
- **Categories:** every site category / chip mapped to our genres (`enrichment.genres` keywords, or
  `declared_genres_only` when the site's own categories are precise). `check_source.py` shows no `NO GENRE`.
- **WebP:** artwork URLs passed through; in production `https://spotx.fun/images/<id>/640.webp` answers
  `200 image/webp`, and `event_images.content_type` is set in the DB.
- **Scheduler:** its own `cron:` twice a day on a free minute (`check_source.py` shows no `CRON CLASH`).
- **JSON + shops:** a display name in `web/src/sourceNames.json`, an entry in `STORES` in
  `web/src/pages/Stores.tsx` (shop URL + kind: tickets / guide / venue / abroad), so it appears on
  `/ticket-sites` and in the source filter; `search.source_aliases`; the `SCRAPERS` registry; the
  `docs/sources.md` row and count.
- **Existing coverage checked first:** `git grep -n -i -E "<domain>|<venue name>" -- config src`
  (eventer `users:`, arenas venue slugs, Smarticket / PRES / Tickchak tenants). Name any overlap in the
  config comment; dedup merges it.
- **Audit before it is called finished (mandatory, both runs):**
  1. Static, before the commit: `python scripts/audit_sources.py <name>` shows `PASS` in every column
     (registry, sourceNames.json, STORES, alias, docs row and its times, free cron, test file, no key in its fixtures,
     skip_title_words, tenants with a city or venue).
  2. Production, after the deploy and the source's first finished run (read-only):
     `railway ssh --service web -- "PYTHONIOENCODING=utf-8 python - --db --json" < scripts/audit_sources.py > prod.txt`,
     then `python scripts/audit_sources.py <name> --prod prod.txt`. No `FAIL` for the source: genres,
     city, map, artwork stored, `content_type`, failed downloads, the 640 / 960 WebP copies, the run,
     unpinned venues, unmapped categories, tenants. A `WARN` gets a fix or a line in the report. A low
     city / map coverage the site itself causes (secret spots, region-only listings) goes into the
     script's `REASONS` with the evidence; a pinned venue without a city is our gap, never a reason.
- **Rules the source audit of 23.11.2026 found missing (each one broke a live source):**
  - An alias is never an everyday word ("לאן", "מה קורה", "יוצאים", "פרס", "הבמה"): an alias in a query
    turns the whole search into "sold on that site".
  - A category that is a genre only as the site's own label ("מלודיק", "דאנס", "טברנה") goes into
    `enrichment.declared_genres`, not `enrichment.genres` (which also reads titles and texts).
  - A tenant / host whose halls are all in one town gets `city:`: hall names that name a village
    under `places_min_population` never give a city. A council or company playing in several towns
    gets none.
  - One tenant, site or listing page that fails costs only its own events: wrap each one in
    `optional()` / `stream_isolated` (one site's 503 failed a whole eventpro run).
  - Every yielded event passes `is_current`, today's past hours too (not only `date < today`).
  - Artwork URLs must download: after the first run `art` and `dlerr` are clean (a listing's other
    picture path answered 404 for every Barak event; an S3 header broke curl for the cinematheque).
  - An event abroad has a city: when the site has no city field, the venue address's town.
  - A site that is empty between editions or parties (a festival, a two-week guide, a one-party
    page, an app with test rows only) sets `may_be_empty: true`, or 0-event runs get it paused.
  - Pages saved as test fixtures carry the site's third-party keys and tokens (a Google Maps `AIza...`
    embed key, analytics IDs with secrets, any `key=` / `token=` value). Blank each one
    (`key=REDACTED_THIRD_PARTY_KEY`) before the commit: GitHub secret scanning flagged tarbut.events'
    Maps key (`6c06079`). The audit's `keys` column greps `tests/fixtures` for them.
- **Config valid before every push (and after every rebase):**
  `python -c "import yaml; yaml.safe_load(open('config/settings.yaml',encoding='utf-8'))"` and
  `python -c "from partylinks.config import get_settings; get_settings()"`. A broken settings.yaml
  stops prod from deploying. Files are CRLF: keep each line inside its own source's block.
- **Types:** `python -m pyrefly check <your files>` shows no ERROR (the owner's IDE shows them; a None
  attribute access is a crash waiting).

1. Read `docs/adding-a-source.md` in full before touching code.
2. Recon (section 1): robots.txt, the data the page itself loads, platform already covered?, access
   (plain / `impersonate: chrome` / `israel_proxy` / `vpn: true` when even the free Israeli proxies fail). If the auto-mode classifier denies probing,
   stop, explain, and ask the user for a narrow `autoMode.allow` rule in `.claude/settings.local.json`.
   **Access test, in this order, cheapest first:** 1) direct (plain request) 2) via proxy
   (`israel_proxy: true`) only if direct fails 3) VPN (`vpn: true`) only if both fail. Configure the
   first that works and report it (direct / proxy / vpn).
   **Israeli-only sites:** the local machine may be abroad, so a local request proves nothing.
   Run the dry run inside the production container, through the Israeli VPN (it writes nothing):
   `railway ssh --service web -- "PYTHONIOENCODING=utf-8 python - <name> --show 10" < scripts/check_source.py`. The image has no `scripts/`, so the script goes in on stdin. `railway run` would run
   locally, not in Railway. Needs this machine's SSH key registered (`railway.cmd ssh keys add`)
   and ssh.railway.com in `~/.ssh/known_hosts`.
3. Build in a new file only (`src/partylinks/scrapers/<name>.py` + `tests/test_<name>.py`);
   existing files get the hook lines of section 4 and nothing else. Never edit another source to
   make room.
4. **Every event fully placed and categorised:** site categories mapped to our genres (no `NO GENRE`;
   a category that maps to nothing gets keywords or a new genre, section 3), ISO `country` + venue
   `time_zone` for every event abroad, `city` on every event, and a location: coordinates or a
   geocodable venue + address (never a city centre as the venue's pin). Read the coverage lines:
   city below 100% or map below ~90% needs a fix or a stated reason.
   Run `python scripts/check_source.py <name> --show 10` until it says `OK`; fix every `NO GENRE`,
   `PAST` and `CRON CLASH` line. Then ruff, the full pytest, and `npx tsc` in `web/` if web changed.
5. Commit only your hunks (other sessions share master), push, wait for the Railway deploy
   `SUCCESS`, then verify in production (section 8): run, `--runs`, read-only DB counts, `640.webp`
   is `image/webp`, event page 200.
6. Audit it (the mandatory step above): `scripts/audit_sources.py <name>` static before the commit,
   the production `--db` run after its first run. Fix every `FAIL` before calling it finished.
7. Report to the user in Hebrew: counts from production (events, map, genres), what the site lacks,
   the audit line, commits. Tick every box of section 9; a source with an unticked box is not finished.
