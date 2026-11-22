---
name: new-source
description: Add a new SPOTX source / scraper / scanner (סורק, סקאנר) for an events site, end to end - site recon, scraper module, categories, map, stores, tests, deploy and production check. Use whenever the user gives a site URL to add, asks for a "smart scanner" like Chillz, or asks to fix how a new source was added.
---

# New source (scanner)

Follow `docs/adding-a-source.md` step by step. It is the procedure; this skill only says how to run it.

## Mandatory for every source (none may be skipped)

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
- **Config valid before every push (and after every rebase):**
  `python -c "import yaml; yaml.safe_load(open('config/settings.yaml',encoding='utf-8'))"` and
  `python -c "from partylinks.config import get_settings; get_settings()"`. A broken settings.yaml
  stops prod from deploying. Files are CRLF: keep each line inside its own source's block.

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
6. Report to the user in Hebrew: counts from production (events, map, genres), what the site lacks,
   commits. Tick every box of section 9; a source with an unticked box is not finished.
