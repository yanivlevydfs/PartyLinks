"""Everything we know about a place, beyond one listing's fields: hours, rating, booking, photos, links.

Three sources, none of them called in a web request:

* HappyTLV's page, read with the listing (``pages.happytlv_details``), kept in the listing's ``extra``.
* Ontopo, the Israeli booking site: the worker reads its venue pages from the public sitemap into
  ``ontopo_venues`` and ties each place to one in ``place_info`` (``refresh``).
* The place's own website, JSON-LD only, when the others leave hours or a phone missing (``refresh``).

- ``details``: merges them, for the place page and its crawler version (``seo``).
- ``jobs``: the scheduler hook. ``routes``: the page's data and the stored photos.
"""
