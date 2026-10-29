# SPOTX: API + scheduler + built website in one container (Railway, or any Docker host).
#   docker build -t partylinks . && docker run -p 8000:8000 -v partylinks-data:/app/data partylinks

# ---------------------------------------------------------------- website (React -> web/dist)
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
ARG RAILWAY_GIT_COMMIT_SHA
RUN npm run build

# ---------------------------------------------------------------- app
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # ~90 source threads each got a glibc malloc arena that never shrinks: the worker grew to the
    # 8 GB limit and was killed (Oct 2026). Two arenas keep freed memory reusable.
    MALLOC_ARENA_MAX=2 \
    # Hosts collect stdout: one JSON object per line, no log file inside the container.
    PARTYLINKS_LOGGING__FORMAT=json \
    PARTYLINKS_LOGGING__FILE= \
    # The platform's edge proxy is the only way in, so its X-Forwarded-For is the visitor.
    PARTYLINKS_SECURITY__TRUSTED_PROXIES='["*"]'
WORKDIR /app

# Runtime dependencies pinned to exact versions, so a deploy never picks up an untested release.
# Regenerate after changing pyproject.toml:
#   uv pip compile pyproject.toml --python-version 3.12 --python-platform linux -o requirements.lock
COPY requirements.lock ./
RUN pip install -r requirements.lock && rm requirements.lock \
 && useradd --system --no-create-home --shell /usr/sbin/nologin app

COPY src/ src/
COPY config/ config/
COPY content/ content/
COPY --from=web /web/dist web/dist
# Ticket sites' display names: the website builds them in, the server draws them on event artwork.
COPY web/src/sourceNames.json web/src/sourceNames.json

# Images, caches, inboxes: mount a volume at /app/data or everything resets on
# deploy (Railway: a service volume; it refuses a VOLUME instruction here).
EXPOSE 8000

# $PORT is set by Railway; the public domain becomes the site's canonical URL unless set explicitly.
# Starts as root only to hand the volume (mounted root-owned) and config/ (admin edits, backups) to
# the unprivileged "app" user, then drops to it.
CMD ["sh", "-c", "export PARTYLINKS_API__PORT=\"${PORT:-8000}\"; if [ -z \"$PARTYLINKS_SITE__BASE_URL\" ] && [ -n \"$RAILWAY_PUBLIC_DOMAIN\" ]; then export PARTYLINKS_SITE__BASE_URL=\"https://$RAILWAY_PUBLIC_DOMAIN\"; fi; mkdir -p data && chown -R app:app data config && exec setpriv --reuid=app --regid=app --clear-groups python -m partylinks serve"]
