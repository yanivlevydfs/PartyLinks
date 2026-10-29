"""Saved events (the heart). Works without an account: the browser keeps the ids (localStorage
``spotx-saved``) and shows them through ``GET /events/cards``. A signed-in user's hearts live in
``saved_events``; on sign-in the browser's list is merged in once.

- ``models``: ``saved_events``, on the app's one ``Base``.
- ``routes``: ``/api/me/saved`` (list, heart on / off, merge), ``/events/cards``, the clean-up hook
  (``delete_saved``, called by ``db.delete_events``) and the per-user count for the admin.
"""
