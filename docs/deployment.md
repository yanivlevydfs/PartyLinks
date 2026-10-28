# Deployment (Railway)

Production runs on Railway, in the `PartyLinks` project and `web` service. **Every push to `master`
deploys automatically.**

The repo ships two files Railway uses:

- **`Dockerfile`:** builds the website, then runs the API, the scheduler and the site in one
  container, as a non-root user.
- **`railway.json`:** Dockerfile build, `/health` check (120 s), one replica, restart on failure (5
  tries), overlap 0 s, draining 30 s. Railway reads it on every deploy. **Keep it:** Railway calls this
  "Config as Code" deprecated (it works until 2026-12-01), but a deploy without it (2026-10-28) was built
  with Railpack instead of the Dockerfile and failed. Moving off it is on hold; try it on a separate
  Railway environment first, never on production.
- **`.railway/railway.ts`:** the same settings as Railway's Infrastructure as Code, pulled from the live
  service and applied once (`railway config plan` reports it up to date), plus the `/app/data` volume, the
  domains and the variables (`preserve()`: values stay in Railway, never in git). Field names follow the
  SDK's types (`node_modules/railway/dist/index-*.d.ts`, `BuildConfig` / `DeployConfig`).

  Change it, then `railway config plan` (read-only, shows the diff) and `railway config apply`.
  A push alone does not apply it. The SDK is the root `package.json` dev dependency (`npm install`).
  On Windows the SDK checks the CLI through `$env:_`: in PowerShell first
  `$env:_ = "$env:APPDATA\npm\node_modules\@railway\cli\bin\railway.exe"`.

## Setup

1. Create a project from this repo (Railway picks up `railway.json`).
2. **Add a volume mounted at `/app/data`.** It holds the SQLite database, the artist dictionary, the
   images, and the caches (weather, `geonames-<version>.json`: the GeoNames cities, built once per
   library version). Without it every deploy starts empty.
3. Set the variables below.
4. Generate a domain (Settings → Networking). The app listens on Railway's `$PORT`.

Keep **one replica**: the scheduler, the response cache and the bans live in the process (and SQLite
is a file). Production runs on PostgreSQL (the `Postgres` service, database `railway`); the volume holds only the
images and the caches. No SQLite file is left on Railway: the last SQLite copies (`partylinks.db`,
`artists.db`, 2026-10-28) were downloaded, verified and deleted from the server.

## Variables

| Variable | Purpose |
| --- | --- |
| `PARTYLINKS_DATABASE__URL` | PostgreSQL: `postgresql://${{Postgres.PGUSER}}:${{Postgres.PGPASSWORD}}@${{Postgres.PGHOST}}:${{Postgres.PGPORT}}/partylinks` (the driver is set in code). Unset = SQLite on the volume |
| `PARTYLINKS_DATABASE__SERIALIZE_WRITES` | `true` queues writers one at a time on PostgreSQL too (the first week after the switch) |
| `PARTYLINKS_SCHEDULER__ENABLED=true` | Turns scraping on (it is off in `settings.yaml`, so localhost never scrapes) |
| `PARTYLINKS_SCHEDULER__RUN_ON_STARTUP=true` | Runs every source once when the process starts |
| `PARTYLINKS_ADMIN__TOKEN` | A long random string for the admin console and the mutating API |
| `PARTYLINKS_HTTP__SOURCE_PROXY` | Proxy every source goes through, so sites never see the server's IP. A comma-separated list is a failover pool: a source moves to the next URL only when the proxy itself fails. A source can opt out with `proxied: false` |
| `PARTYLINKS_SITE__BASE_URL` | Only for a custom domain; otherwise `https://$RAILWAY_PUBLIC_DOMAIN` is used |
| `PARTYLINKS_SECURITY__ALLOWED_HOSTS` | Host names the site answers to, e.g. `["spotx.fun","www.spotx.fun"]` |
| `PARTYLINKS_SOURCES__<NAME>__HTTP__HEADERS` | Secret headers for one source (e.g. an access key a site gave us) |

Keep secrets in Railway variables or `.env`, never in `settings.yaml`.

The allowed hosts in `settings.yaml` include `healthcheck.railway.app`. Without it, Railway's
deploy health probe is refused and no deploy goes live.

## Dependencies

`requirements.lock` pins every package for the Linux image. After changing dependencies in
`pyproject.toml` (and `requirements.txt` for local installs), regenerate it:

```bash
uv pip compile pyproject.toml --python-version 3.12 --python-platform linux -o requirements.lock
```

## Logs

Logs are JSON lines on stdout (`level`, `logger`, `message`), with warnings and errors on stderr,
so Railway's log filters work. Each source run logs whether it went through the proxy (`via <host>`).
