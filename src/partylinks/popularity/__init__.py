"""How many liked / viewed / are watching each event, shown on its page and its cards.

- likes: users with the event in ``saved_events`` (the save heart; a signed-out visitor is asked to
  sign in before saving, so every heart is an account's).
- views: one ``event_views`` row per (event, visitor, day), written by ``GET /events/{ref}``.
  The visitor is a hash of IP + user agent with a per-process salt: no cookie, no IP kept. Crawlers don't count.
- watching: distinct visitors whose last view of the event is within ``watching_minutes``.

``config``: ``popularity:`` in settings.yaml. ``models``: the table, the counts and the clean-up hooks.
"""
