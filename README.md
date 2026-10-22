# Pulse (פולס)

Pulse collects events from 47 Israeli ticketing sites on a schedule and shows them on one website
in Hebrew and English. It covers parties, concerts, theatre, stand-up, cinema, tech meetups and
shows abroad.

For every event it keeps:

- every ticket tier, with price, booking fee, status, and whether entry is free or paid
- the artwork and the lineup
- genres, local date, weekday, time slot, venue, city and map location
- price changes over time

Each ticket link sends the visitor to the seller through a UTM-tagged, counted redirect.

The backend is Python (FastAPI, SQLAlchemy, SQLite) and the website is React + TypeScript (Vite).
Production runs on Railway and deploys on every push to `master`.

## Quick start

```bash
pip install -r requirements.txt
set PYTHONPATH=src                     # PowerShell: $env:PYTHONPATH="src"
python -m partylinks serve             # http://localhost:8000 (the scheduler is off locally)
python -m partylinks scrape issta      # run one source once
python -m pytest
```

The website: `cd web && npm install && npm run build`, or run `start.bat`.

## Documentation

All docs are in [docs/](docs/README.md):

- [Architecture](docs/architecture.md)
- [Sources](docs/sources.md)
- [Running locally](docs/running-locally.md)
- [Deployment](docs/deployment.md)
- [Configuration](docs/configuration.md)
- [API](docs/api.md)
- [Admin console](docs/admin.md)
- [Ticket links: UTM and clicks](docs/ticket-links.md)
- [SEO and blog](docs/seo-and-blog.md)
- [Legal and privacy](docs/legal.md)
- [Adding a source](docs/adding-a-source.md)
