# SPOTX documentation

SPOTX collects events from Israeli ticketing sites on a schedule and shows them on one
website. It covers parties, concerts, theatre, stand-up, cinema, tech meetups and shows abroad.
Every ticket link points back to the seller.

| Document | What it covers |
| --- | --- |
| [Architecture](architecture.md) | How a run works, how the parts fit together, the code layout |
| [Sources](sources.md) | Every site we collect from, its schedule, and the sites we left out |
| [Running locally](running-locally.md) | Install, run, test, build the website |
| [Deployment](deployment.md) | Railway setup, variables, proxy, logs |
| [Configuration](configuration.md) | `config/settings.yaml` and environment overrides |
| [API](api.md) | Public and admin endpoints |
| [Admin console](admin.md) | The `/admin` page and what each tab does |
| [Ticket links: UTM and clicks](ticket-links.md) | How outbound links are tagged and counted |
| [SEO and blog](seo-and-blog.md) | Server-rendered pages, sitemaps, `llms.txt`, the blog |
| [Legal and privacy](legal.md) | Accessibility, privacy, cookies, open items |
| [Adding a source](adding-a-source.md) | Step-by-step guide for a new site |
| [Postgres migration](postgres-migration.md) | How production moved to PostgreSQL (done 2026-10-28), and the planned search index (pg_trgm). In Hebrew |
| [Postgres performance](postgres-performance.md) | Performance plan for the production database: baseline, steps, measurements, rollback. In Hebrew |
