"""Drawn cards for events with no picture (see docs/artwork.md).

The scrapers bring the pictures. Only an event none of whose listings has a picture, stored or still
downloading, gets a card: its name as the largest text, every ticket site selling it, day, time and city, on
one of ~200 backgrounds chosen by the event's id. A real picture always wins: once one is stored, the card
is deleted.

- ``draw``: the drawing (Pillow, Heebo, python-bidi for Hebrew), WebP.
- ``models``: ``event_artwork`` (the card's two sizes, in the database).
- ``service``: which events need a card, drawing and removing them (a worker job every 15 minutes).
- ``routes``: ``/artwork/{id}`` and ``/artwork/{id}/{640|1280}.webp``.
"""
