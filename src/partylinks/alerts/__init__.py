"""Event alerts ("save this search"): personalised parties and events for a signed-in user.

An alert is a saved set of the site's own filters (genres, regions, time slots, free only, when, a word
such as an artist or venue). The worker matches every new event against the alerts through the same
filter builder ``/events`` uses (``api.build_event_filters``), so an alert finds exactly what those
filters show on the site. A match is an ``alert_hits`` row: it is the bell, the "For you" row's source,
and what keeps an event from being sent twice by email.

- ``criteria``: what an alert looks for, validated against the configured genres / regions / slots.
- ``models``: ``event_alerts``, ``alert_hits``, ``alert_state`` (the matcher's cursor).
- ``routes``: ``/api/me/alerts`` (CRUD, preview), ``/api/me/bell``, ``/api/me/for-you``; clean-up hook.
- ``matcher``: new events x active alerts -> hits.
- ``jobs``: the worker's schedule (matcher, then the email jobs).
"""
