# Adding a source

## 1. Check the site first

- **`robots.txt`:** read it. Do not fetch paths it disallows; linking to them is fine.
- **Find the data the page itself loads:** a JSON API, `__NEXT_DATA__`, a schema.org JSON-LD
  block, a sitemap. Prefer that over parsing HTML.
- **Check the sitemap:** see whether the site already lists every event in one place.

## 2. Write the scraper

Create `src/partylinks/scrapers/<name>.py`. Start it with a module docstring that explains how the
site is read: endpoints, pagination, what the listing and the event page give, and any limits.

```python
class MySiteSettings(SourceSettings):
    site_url: str
    listing_path: str


class MySiteScraper(BaseScraper[MySiteSettings]):
    name = "mysite"
    settings_model = MySiteSettings
    # abroad_only = True       # for sites that sell only events abroad

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        page = await self.get_text(self.settings.site_url + self.settings.listing_path)
        for event in parse_listing(page):
            yield event
```

Keep parsing in plain module functions (`parse_listing`, `parse_event`), so tests can call them
without the network.

`BaseScraper` provides:

| Helper | Does |
| --- | --- |
| `get_text`, `get_json`, `post_json`, `request` | HTTP with retries, delay, proxy and failover |
| `paginate(...)` | Parallel listing pages |
| `stream_isolated(...)` | Per-event detail requests in parallel, where one failure only skips that event |
| `optional(...)` | One request whose failure is logged and skipped |
| `record_failure(...)` | Record one bad event without stopping the run |

Reuse what exists:

- **Text and numbers:** `parsing.py` (`clean_str`, `to_decimal`, `parse_iso`, `zone`, `slugify`).
- **Events abroad:** `scrapers/abroad.py` (`country_code`, `local_zone`).
- **Platforms already covered:** many box offices run on a platform we already read (PRES,
  Smarticket, Tickchak, The Events Calendar). Add a host to that source's settings instead of
  writing a new scraper.

## 3. Register and configure

1. Import the class and add it to `SCRAPERS` in `scrapers/__init__.py`.
2. Add a `sources.<name>` block to `config/settings.yaml`. Give it a short comment saying what the
   site is and how it is read, `enabled: true`, and a `cron` on a free minute, twice a day.
3. Add the display name to `SOURCE_NAMES` in `web/src/ui.tsx`, then rebuild the website.

## 4. Test

1. **Save a real page** as a fixture under `tests/fixtures/<name>/`.
2. **Write `tests/test_<name>.py`** against it: titles, dates and time zones, prices and currency,
   links, city and country.
3. **Run the source once** against a throwaway database:

   ```bash
   PARTYLINKS_DATABASE__URL=sqlite:///C:/temp/test.db python -m partylinks scrape <name>
   ```

4. **Run the checks:** `python -m pytest` and `ruff check src tests`.

## 5. Ship

Push to `master`. Railway deploys and the source runs at its next cron time. Check its first
runs in the admin console's **runs** tab, and add it to [sources](sources.md).
