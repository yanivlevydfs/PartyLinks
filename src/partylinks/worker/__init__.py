"""The scraping worker: its own process beside the web server.

Python runs one thread at a time per process, so 84 scrapers and their whole-table passes (dedup,
geocoding, venues) in the web process left page requests waiting for their turn (a 0.6 s page took
40 s, and a crawler's burst filled the server's queue). Production therefore runs two processes in
one container, on one data volume and one PostgreSQL:

- ``serve`` (the web process) answers visitors and the admin, and starts and watches the worker
  (``supervisor.py``: started with the server, restarted if it ever exits).
- ``worker`` (``runner.py``) runs the scheduler, every source, retention and the startup passes.

They talk through two tables (``models.py``): ``worker_state`` (heartbeat, scheduler jobs, retention,
write counter; the web reads it for /health, the admin and cache freshness) and ``worker_commands``
(pause / resume / run now / reload config, from the admin; ``link.py`` on the web side).
"""
