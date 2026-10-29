# Event artwork for events with no picture

The scrapers bring the pictures. An event none of whose listings (its whole duplicate group) has a picture,
stored or still downloading, gets a drawn card instead: the event's name as the largest text, every ticket
site selling it as pills, day, time and city, and SPOTX, on one of about 200 backgrounds (8 palettes × 3
textures × glow positions) chosen by the event's id, so an event always looks the same.

## Rules

- **Only with no picture.** A picture a scraper found but has not stored yet means wait. A real picture
  always wins: once one is stored, the card is deleted on the next pass, and the save step itself re-checks
  (a picture that arrives while a card is being drawn means the card is not written).
- **Always the event's name**, plus the stores, the date and the place.
- **Gone with the event.** Retention deletes the card with its event (`db.delete_events` ->
  `artwork.models.delete_artwork`); a pass also deletes cards whose event ended or is no longer its group's
  primary listing.
- **In the database**, as the event's picture: table `event_artwork` (its own table: the scrapers rewrite
  `event_images` on every run).

## How it runs

A worker job, `artwork:draw`, two minutes after start and then every 15 minutes (`artwork/jobs.py`):

1. **Plan:** one short transaction finds the events that need a card (`service.needing`) and deletes the
   cards no longer needed; the events to draw (new, or changed since their card was drawn: name, stores,
   date, city) are copied into plain `Job` objects. At most 1,500 a pass (production had 4,284 events with no picture on 2026-10-29: three passes).
2. **Draw, concurrently:** asyncio tasks, 4 at a time (`CONCURRENCY`, bounded by the CPUs), each in a thread.
   Pillow's blur, resize and WebP encoding run outside the GIL. No transaction is open while drawing.
3. **Save, one transaction per card:** re-check that the event still has no picture, then one upsert writes
   both sizes (WebP 1280 and 640) and the signature. The bytes live only in that row, so card and database
   succeed or fail together. One card's failure never stops the others; it is tried again next pass.

Measured locally on 28,000 events: finding them 1.5 s; drawing 104 ms a card with 4 at a time (320 ms one at a
time).

## Where it shows

`/artwork/{id}` (1280) and `/artwork/{id}/{640|1280}.webp`, cached an hour. It is the event's `cover_url` only
when neither the event nor its group has a picture (`api._summary_extras`): cards, the event page, saved
events, the bell, emails. Link previews (og:image) use it too; Google's event data (JSON-LD) keeps real
pictures only (`seo._cover(..., drawn=False)`). `/artwork/` counts as static for the rate limit, like
`/images/`.

## Drawing

`artwork/draw.py`: Pillow, the Heebo font (`artwork/fonts/Heebo.ttf`, OFL) and `python-bidi` for Hebrew
(Pillow here has no bidi engine). Store names come from `web/src/sourceNames.json`, the same file the website
builds in (the Docker image copies it beside the code). A new source needs its name there.
