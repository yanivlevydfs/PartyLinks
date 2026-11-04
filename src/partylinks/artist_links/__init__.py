"""Artists' music / social links (Spotify, YouTube, Apple Music, SoundCloud, Instagram...), found automatically.

- ``lookup``: the worker's pass asking MusicBrainz about lineup artists with upcoming shows (hits and misses
  cached in ``artist_links``; conservative matching: a wrong artist's links are worse than none).
- ``models``: the table, name keys, and the links the site shows (ticket-site profiles merged with MusicBrainz),
  read from the database into memory; no web request ever calls MusicBrainz.
- ``jobs``: the scheduler hook. ``routes``: the admin's counts.

``config``: ``artist_links:`` in settings.yaml.
"""
