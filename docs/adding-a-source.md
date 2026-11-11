# Adding a source (a new scanner)

The full procedure for adding a source, from the first look at the site to a checked run in
production. A source is not finished until every box in [the final checklist](#9-done-checklist)
is ticked.

Each source has one module in `src/partylinks/scrapers/`, one block under `sources:` in
`config/settings.yaml` and one test file. Everything else gets one hook line only. Existing
sources are never changed to make room for a new one.

## Rules every source follows

| Rule | What it means in practice |
| --- | --- |
| **Never past events** | Do not fetch past events, and do not keep a skip-list file of their URLs. Filter them out from the listing itself: server-side query, sitemap `<lastmod>`, card dates. Before yielding, apply `retention.is_current`, the same rule the site uses to display and purge. |
| **On the map** | Every event with a place carries `latitude` / `longitude`, or an address that geocoding can find. For a single-venue source, set the venue's coordinates in its config block (look them up in Nominatim with `geocoding.user_agent`, never a personal email). Weather comes from these coordinates. |
| **In our categories** | `source_genres` carries the site's own categories. The keywords in `enrichment.genres` map them to site genres, and every event must end up with at least one. |
| **In the stores** | Add the display name to `web/src/sourceNames.json` and a line to `STORES` in `web/src/pages/Stores.tsx`. |
| **WebP images** | Pass artwork URLs only. `media.py` downloads them and stores WebP copies (640 / 1280). Never add a path that saves or serves another format. |
| **Read what the page itself reads** | Use the site's own JSON API, `__NEXT_DATA__`, JSON-LD, sitemap or iCal feed before parsing HTML. Fewer requests is better: one listing call is best. |
| **No scale limits** | Bandwidth and DB size are not limits. Scrape the whole catalogue, never cap by volume. |
| **Libraries over hand-written code** | When a library covers the job, use it and add it to `pyproject.toml`, `requirements.txt` **and** `requirements.lock`. Docker installs only the lock. |

## 1. Look at the site first

1. **`robots.txt`:** read it, and do not fetch paths it disallows.
2. **Find the data source.** In order of preference:

   | Data | Where to look | Example sources |
   | --- | --- | --- |
   | A JSON API the page calls | Browser network tab; `/api/`, `/wp-json/`, GraphQL | `barby`, `eventer`, `funzing` |
   | App backends (Base44, Supabase, Firebase) | `/api/apps/<id>/entities/<Entity>` with `?q=<json filter>` | `secrettelavivapp` |
   | `__NEXT_DATA__` / Nuxt state | `parsing.next_data(html)` | several box offices |
   | schema.org JSON-LD `Event` | `parsing.jsonld_nodes(html)` | `chillz`, `makore` |
   | iCal feed | `/?ical=1`, `.ics` links | `secrettelaviv` |
   | Sitemap | `/sitemap.xml`, `robots.txt` `Sitemap:`, `<lastmod>` filters | `chillz` |
   | HTML | `selectolax` (`LexborHTMLParser`) | `meety` |

3. **Check whether a platform we already read covers it.** If the site runs on PRES, Smarticket,
   Tickchak, The Events Calendar (`tribe.py`) or Eventer, add a host or an organizer to that
   source's settings instead of writing a new scraper.
4. **Check access.** Note how the site answers a plain client:

   | Answer | What to use |
   | --- | --- |
   | 200 for plain httpx | Defaults |
   | 403 / Vercel checkpoint / Cloudflare TLS block, 200 for Chrome | `http.impersonate: chrome` (curl_cffi) |
   | Answers Israeli IPs only | `israel_proxy: true` (free Israeli proxies) |
   | Refuses the free Israeli proxies too | `vpn: true`: our Israeli VPN only (Railway production, `vpn/`) |
   | Cloudflare JS challenge for every client | Stop and tell the user; the tool's permission classifier may block bypass work |

5. **Count the requests per run.** The site's own rate limits matter; set `http.concurrency` and
   `http.request_delay_seconds` to match.
6. **Before any probing that may be read as circumvention** (a hidden API, a blocked site),
   the auto-mode classifier may deny it. Ask the user for a narrow `autoMode.allow` rule in
   `.claude/settings.local.json` naming the site, then continue.

## 2. Write the scraper

Create `src/partylinks/scrapers/<name>.py`. Its module docstring says how the site is read:
endpoints, filters, pagination, what each request gives, what the site lacks (no price, no venue).

```python
class MySiteSettings(SourceSettings):
    site_url: str = "https://mysite.example"
    listing_path: str = "/api/events"
    social_hosts: list[str] = Field(default_factory=lambda: ["instagram.com", "facebook.com"])


class MySiteScraper(BaseScraper[MySiteSettings]):
    name = "mysite"               # = the config key, sourceNames.json key, Stores key
    settings_model = MySiteSettings
    # abroad_only = True          # sellers of events abroad only

    def is_blocked(self, response: httpx.Response) -> bool:   # only if the site has a block page
        return response.headers.get("cf-mitigated") == "challenge"

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        rows = await self.get_json(self.settings.site_url + self.settings.listing_path)
        for row in rows:
            try:
                event = parse_event(row, self.settings, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one bad row must not cost the others
                self.record_failure(f"event {row.get('id')}", exc)
                continue
            if event and is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone):
                yield event
```

- **Constants live in the settings model:** paths, ids, placeholder values, host lists. Mutable
  defaults go through `Field(default_factory=...)` (ruff RUF012).
- **Parsing lives in plain module functions** (`parse_listing`, `parse_event`), so tests can call
  them without the network.

### `BaseScraper` helpers

| Helper | Does |
| --- | --- |
| `get_text`, `get_json`, `post_json`, `request` | HTTP with retries, backoff, delay, proxy and failover |
| `paginate(...)` | Parallel listing pages |
| `stream_isolated(items, fn, label)` | Detail requests in parallel; one failure skips only that item |
| `optional(what, call)` | One request whose failure is logged and skipped (lookups, extras) |
| `record_failure(what, exc)` | Record one bad item without stopping the run |
| `known_images` | Images already stored per event id: skip re-fetching, and don't overwrite a stored event with less |
| `default_headers()` | Extra headers (Origin, Referer, an app id) |
| `is_blocked(response)` + `SourceBlockedError` | Records the run as `blocked` rather than `failed` |

### `parsing.py` helpers

`clean_str`, `to_float`, `to_decimal`, `to_int`, `parse_iso`, `from_unix`, `zone`, `read_clock`,
`has_time`, `html_to_text`, `og_image`, `jsonld_nodes`, `next_data`, `slugify`. For events abroad:
`scrapers/abroad.py` (`country_code`, `local_zone`).

### `ScrapedEvent` fields to fill

| Field | Required | Notes |
| --- | --- | --- |
| `source`, `source_event_id` | yes | A stable id from the site (not the slug if the slug can change) |
| `url` | yes | The public event page |
| `title` | yes | Hebrew when the site writes it in Hebrew; else the site's language |
| `start_at` | yes | Aware datetime in `zone(self.timezone)` (or the venue's zone abroad) |
| `end_at` | when known | Drop the site's placeholder ends ("23:55", "11:55 pm") |
| `time_known` | yes | `False` only when the site gives a date without a time |
| `venue_name`, `address`, `city` | yes | `address` drives geocoding when there are no coordinates |
| `latitude`, `longitude` | when the site has them | Both or neither |
| `source_genres` | yes | The site's own category names, lower case |
| `images` / `image_url` | when present | Absolute http(s) URLs of real artwork, never a placeholder logo |
| `extra["purchase_url"]` | when present | The seller / original event link. Dedup links listings through it. Not a social profile |
| `tickets`, `min_price`, `max_price` | when the site prices | `TicketTier` per tier |
| `description` | when present | Passed as it comes; `tidy_text` cleans it |
| `country`, `time_zone` | abroad | Israel leaves them `None` |

## 3. Categories and genres

1. List the site's categories and how many events each one has (`check_source.py` prints them
   as `declared`).
2. **Match `enrichment.genres` in `config/settings.yaml`:** each canonical genre has keywords
   that are matched against `source_genres` and event types, and (when
   `detect_genres_from_text` is on) against the title and description. Add the site's category
   name to the right genre's keywords when it maps to nothing.
3. **A category that is new to the site** (no genre fits): add the genre to `enrichment.genres`
   and to one family in `enrichment.genre_groups`, which the filter shows. Every genre needs a
   group. Then carry the change to every surface: filter, quiz, map, SEO pages, blog and i18n.
4. **A site whose categories are precise** (its text names genres in passing): add it to
   `declared_genres_only`.
5. **A site with category pages:** give each page a label in config (see `tickchak.categories`),
   so its events land in the site category.

## 4. Hook lines in existing files

Only these lines change outside the new files:

| File | Line to add |
| --- | --- |
| `src/partylinks/scrapers/__init__.py` | `from partylinks.scrapers.<name> import <Class>` and the class in `SCRAPERS` |
| `config/settings.yaml` | The `sources.<name>` block (see below) |
| `web/src/sourceNames.json` | `"<name>": "<Display Name>"`. Used by the site, the source filter, and event artwork |
| `web/src/pages/Stores.tsx` | `<name>: ["<site url>", "<kind>"]` in `STORES`; kind is one of `tickets`, `venue`, `culture`, `cinema`, `abroad`, `guide`, `city` |
| `src/partylinks/search.py` | `SOURCE_ALIASES`: names people type (Hebrew spelling too). Never reuse another source's alias |
| `config/settings.yaml` `enrichment.genres` | Only when a category maps to nothing (section 3) |
| Other sources' `ticket_hosts` | When a venue site links to this seller, so the listings merge |
| `docs/sources.md` | A row in the right section's table, and the source count in the header |

### The config block

```yaml
  mysite:
    # What the site is and how it is read, in 2-5 lines: endpoints, filters, requests per run.
    enabled: true
    cron: "29 8,20 * * *"     # twice a day, on a minute + hours no other source uses
    site_url: https://mysite.example
    # venue_name / address / city / latitude / longitude   - single-venue sources
    # israel_proxy: true                                   - Israeli IPs only (free Israeli proxies)
    # vpn: true                                            - only through our Israeli VPN (Railway production)
    # unlist_missing: false                                - the listing is not the full catalogue
    http:
      # impersonate: chrome                                - TLS fingerprint blocks (curl_cffi)
      concurrency: 2
      request_delay_seconds: 1.0
```

`check_source.py` reports a cron that shares a minute and an hour with another enabled source.

## 5. Tables and JSON

A source writes through the existing import path (`service.py`); it creates no tables. Per run:

| Table | Written |
| --- | --- |
| `events` | One row per listing (dedup sets `group_id` / `is_primary`; `_finished_before` refuses past events) |
| `ticket_tiers` | One row per `TicketTier` |
| `event_images` | One row per artwork URL; `media.py` stores the WebP copies |
| `scrape_runs` | The run: status (`success` / `partial` / `failed` / `blocked`), events found, errors |

A source that needs a **new table, column or query** follows the database rule. Update
[postgres-migration.md](postgres-migration.md) and its updates log in the same commit.

The JSON files that change: `web/src/sourceNames.json` (always). `data/<name>_*.json` files are
not allowed for past events (see the rules).

## 6. Test

1. **A unit test, `tests/test_<name>.py`:** a real row or page as input, either inline or saved under
   `tests/fixtures/<name>/`. Assert the title, the start and end in the right zone, the venue,
   the coordinates, `source_genres`, the purchase link, and that `Enricher.enrich` gives site
   genres. Also cover the edge cases: the placeholder end, a social link, a missing slug or date.
2. **A dry run against the live site.** Read-only, with nothing written to any database:

   ```bash
   python scripts/check_source.py <name> --show 10
   ```

   It prints coverage (map, genre, venue, city, artwork, ticket link, price, time), the declared
   categories against the site genres, every event without a genre, any past event and any cron
   clash, and ends in `OK` or `FAIL`. Fix every `FAIL`, and read the coverage lines: low map or
   ticket-link coverage needs a reason.
3. **Lint:** `python -m ruff check src tests scripts` and `python -m ruff format --check ...`.
4. **The whole suite:** `python -m pytest -q` (about 10 minutes, on an embedded PostgreSQL it
   starts itself).
5. **The website, when `web/` changed:** `cd web && npx tsc --noEmit -p .`

The scheduler stays off on localhost, so never start `serve` with it on to test a source.

## 7. Commit and deploy

Other sessions commit to `master` at the same time:

1. **Check before staging:** `git status` and `git diff <file>` on every shared file must show
   only your hunks.
2. **Stage and commit in one command:** stage your files by name, with the new module together
   with the files that import it. Never leave anything staged between commands.
3. **Push:** `git pull --rebase`, re-run the new test, then `git push origin master`.
4. **Wait for the deploy:** `railway deployment list --service web --json` shows `SUCCESS` for
   your commit.

## 8. Verify in production

1. **Start it:** `railway run --service web -- python scripts/run_sources.py <name>`
2. **Read the run:** `railway run --service web -- python scripts/run_sources.py --runs <name>`.
   It must say `success` with events found. Fix `partial` / `failed` / `blocked` before going on.
3. **Check the stored events (read-only):** use a script inside the container (`railway ssh --service web`,
   `db.session(write=False)`) that counts the source's events, coordinates, genres, cities
   and `event_images.content_type`.
4. **Check the WebP:** `curl -sI https://spotx.fun/images/<image id>/640.webp` must give
   `200 image/webp`.
5. **Check an event page:** `https://spotx.fun/event/<slug>` must return 200 and show the
   weather, the map pin and the ticket button.
6. **Check the stores:** the source appears on `/ticket-sites` and in the source filter.
7. **Check the venue and artist pages:** they are built from the stored events, so each venue the
   source writes must open at `https://spotx.fun/venue/<venue name>` (200, with its events) and appear
   in `/venues`. Halls of one building should land under one venue (`rooms: true`, or a pin in
   `geocoding.venues`), not one page per room. When the site names artists, open one
   `/artist/<name>` page. A tenant or host added to an existing source (Smarticket, PRES, Tickchak)
   gets the same checks: it adds venues even when it adds no source.
8. **Later runs:** `run_sources.py --report 24` the next day. A source that keeps failing gets paused,
   and the admins are emailed.

## 9. Done checklist

- [ ] Data read from the site's own API / feed / JSON-LD; `robots.txt` respected
- [ ] Own module with a docstring; settings model with no hard-coded constants
- [ ] No past event fetched or yielded (`is_current`), no past-URL file; no start more than `retention.max_future_days` ahead (`check_source.py` prints `TOO FAR`: a broken date the site sends)
- [ ] Every event on the map (coordinates or a geocodable address; venue coords for single-venue sites)
- [ ] Every event has a site genre (`check_source.py` shows no `NO GENRE`)
- [ ] Purchase links kept, social profiles dropped; artwork URLs passed
- [ ] `SCRAPERS`, config block on a free cron slot, `sourceNames.json`, `Stores.tsx`, `SOURCE_ALIASES`
- [ ] `docs/sources.md` row and count; `postgres-migration.md` if the DB changed
- [ ] `tests/test_<name>.py`; `check_source.py` says `OK`; ruff; full pytest; `tsc` if web changed
- [ ] Committed with only my hunks, pushed, deploy `SUCCESS`
- [ ] Prod run `success`; events, coords, genres in the DB; `640.webp` is `image/webp`; event page 200
- [ ] Its venues open under `/venue/<name>` and in `/venues` (one venue per building); an `/artist/` page when the site names artists
