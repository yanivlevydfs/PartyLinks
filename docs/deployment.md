# Deployment (Railway)

Production runs on Railway, in the `PartyLinks` project and `web` service. **Every push to `master`
deploys automatically.**

The repo ships two files Railway uses:

- **`Dockerfile`:** builds the website, then runs the API, the scheduler and the site in one
  container, as a non-root user.
- **`railway.json`:** Dockerfile build, `/health` check, one replica, restart on failure.

## Setup

1. Create a project from this repo (Railway picks up `railway.json`).
2. **Add a volume mounted at `/app/data`.** It holds the SQLite database, the artist dictionary and
   the images. Without it every deploy starts empty.
3. Set the variables below.
4. Generate a domain (Settings → Networking). The app listens on Railway's `$PORT`.

Keep **one replica**: SQLite and the scheduler are single-instance.

## Variables

| Variable | Purpose |
| --- | --- |
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
