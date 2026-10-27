"""Advertising: paid placements picked in the admin, kept apart from the rest of the app.

- ``models``: the ``sponsored`` (picks) and ``sponsored_stats`` (daily views / clicks) tables, on the
  app's one ``Base`` (``partylinks.db`` imports them, so ``create_schema`` sees them).
- ``routes``: ``/events/sponsored`` (the side rails), ``/events/sponsored/hit`` and the admin's
  ``/admin/api/sponsored...`` endpoints.

Hooks into the rest of the app: ``partylinks.api`` includes the two routers.
"""
