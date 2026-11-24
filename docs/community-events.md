# Events users post (community)

A signed-in user posts an event on `/submit` (`web/src/community/SubmitPage.tsx`, linked in the footer). The site sells no tickets: an event has ticket links, or free
entry with no ticket. Each posted event is listed as the `community` source and goes through the same
enrichment and upsert as every scraped event (genres, lineup, entry type, slug, area, grouping with other
listings of the same event). Code: `src/partylinks/community/`. Tests: `tests/test_community.py`.

## Flow

1. The form calls `GET /api/community/config` for the categories, the limits and the Turnstile site key.
2. Pictures go to `POST /api/community/uploads`, one file at a time. Each returns an id and a preview URL.
3. `POST /api/community/events` sends the event with the CAPTCHA token and the "public event" promise.
4. Every event, and every edit, is saved as `pending` until an admin approves it
   (`POST /admin/api/community/{id}/approve`); only then is it listed. Only an admin's own events go live at once
   (the owner's rule, 24.11.2026: nothing is listed without our approval). `community.auto_publish: true` would
   trust an account after `community.trusted_after` approvals; it is off.
5. A rejected event shows its reason to the owner. After the owner edits it, it waits for review again.
   An event an admin took down (`take_down: true`), or one from a blocked account, is `removed` and can't be
   edited.

## Fields

`EventIn` (`schemas.py`) holds every field a scraper fills: title, description, organizer, artists, minimum
age, start day and hour (the hour is optional: `time_known=False`), end, doors hour, venue, address, city,
coordinates, country and time zone abroad, event types (`community.event_types`), genres (the keys of
`enrichment.genres`), ticket tiers and pictures. Each ticket tier has a name, a link, a price, the price
before a discount, a booking fee, a currency, how many people it admits, free or pay-at-door, sold out, and
when sales end.

## Ticket links

Links must be https, on a public host name, with no user name or password, and not a link shortener
(`community.blocked_link_hosts`). They are kept in the listing's `extra.ticket_links`. The event page sends
the visitor through `/go/{id}?link=n`, so each click is counted and UTM-tagged like any other ticket link.
A free event with no ticket points at `/community/events/{id}`, which redirects to its event page.

## Pictures

Accepted types: JPEG, PNG, WebP, GIF, AVIF and HEIC (`media.allowed_content_types`; never SVG). The size limit
is `community.upload_max_bytes`, and each side must be at least `community.upload_min_side` pixels. A picture
must decode in full. It is stored only as WebP copies 640 and 960 wide, under `<media.directory>/community/`.
The original is deleted, and its EXIF data (including a phone's GPS position) goes with it. `MediaStore.delete_files`
and `sweep_orphans` never delete a file that a `community_uploads` row still claims. A picture that none of the
owner's events uses is deleted after `community.unused_upload_hours`.

## Protection

- Sign-in is required. A blocked or deleted account's events are taken off the site.
- Nothing goes live without an admin's approval (`auto_publish: false`), so a script or an AI agent that gets
  through every other check still publishes nothing.
- `/api/community` is an app path (`security.APP_API_PATHS`): script user agents (python-requests, curl,
  headless browsers...) are refused before the route runs.
- Cloudflare Turnstile checks every post and edit on our server, and the token must have been solved on our own
  host name (a token solved on another site is refused). Set `community.turnstile_site_key` and the
  environment variable `PARTYLINKS_COMMUNITY__TURNSTILE_SECRET`. Until both are set, posting answers 503.
- Writes are rate-limited per IP (`accounts.rate_limits.community`, `community_upload`) and per account
  (`community.max_per_day`, `uploads_per_day`). Writes must come from our pages (`SameOrigin`), and the form
  has a hidden honeypot field.
- `enrichment.skip_titles` applies here too: an event it drops is refused with 422 (public events only).

## Sponsored link

"I want a sponsored link" requires a mobile number. It opens a promotion request (`promote_requests`, in the
admin's requests tab) that names the event. Nothing is charged on the site.
