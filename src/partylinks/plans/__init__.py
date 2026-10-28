""""Who's coming with me?": a group plan for one event, shared in WhatsApp.

WhatsApp gives a web page no way to open a group or a poll, so the poll is ours: a short link
(``/plan/<id>``) the friends open from the chat and answer with a name only (I'm in / maybe / not
this time), no sign-up. Each browser keeps a random voter key (localStorage) to change its own
answer; the key is never shown to anyone else.

- ``models``: the ``plans`` and ``plan_votes`` tables, on the app's one ``Base`` (``partylinks.db``
  imports them, so ``create_schema`` sees them).
- ``routes``: ``POST /plans``, ``GET /plans/{id}``, ``POST /plans/{id}/votes`` (public writes, under the
  general rate limit), ``delete_plans`` (called by ``db.delete_events``: no plan outlives its event),
  and the server-rendered ``/plan/{id}`` that WhatsApp's link preview reads (never indexed).

Hooks into the rest of the app, one line each: ``db`` imports ``models`` and calls ``delete_plans``,
``api`` includes ``router``, ``seo.register`` calls ``register_page``.
"""
