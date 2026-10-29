# SPOTX

SPOTX collects events from 88 sources on a schedule (Israeli ticketing sites, clubs and venues, city and
community-centre calendars, event guides, and sellers of shows abroad) and shows them on one website in
Hebrew and English. It covers parties, concerts, theatre, stand-up, cinema, tech meetups, community
events and shows abroad.

For every event it keeps:

- every ticket tier, with price, booking fee, status, and whether entry is free or paid
- the artwork and the lineup
- genres, local date, weekday, time slot, venue, city and map location
- price changes over time

Each ticket link sends the visitor to the seller through a UTM-tagged, counted redirect. Friends plan
together with "who's coming with me?": a poll per event (or between up to four), shared in WhatsApp.

Browsing never needs an account. An optional Google sign-in adds saved events (the 🔔 save bell), alerts
for new events that match a saved search, a notification bell, a "For you" row and email: an instant alert
(at most hourly) and a weekly digest on Thursday morning, only for those who ask for it.

The backend is Python (FastAPI, SQLAlchemy, PostgreSQL) and the website is React + TypeScript (Vite).
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
- [Accounts, saved events, alerts and email](docs/accounts.md)
- [Ticket links: UTM and clicks](docs/ticket-links.md)
- [SEO and blog](docs/seo-and-blog.md)
- [Legal and privacy](docs/legal.md)
- [Adding a source](docs/adding-a-source.md)
