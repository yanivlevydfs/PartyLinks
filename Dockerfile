# Pulse: API + scheduler + built website in one container (Railway, or any Docker host).
#   docker build -t partylinks . && docker run -p 8000:8000 -v partylinks-data:/app/data partylinks

# ---------------------------------------------------------------- website (React -> web/dist)
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---------------------------------------------------------------- app
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Hosts collect stdout: one JSON object per line, no log file inside the container.
    PARTYLINKS_LOGGING__FORMAT=json \
    PARTYLINKS_LOGGING__FILE= \
    # The platform's edge proxy is the only way in, so its X-Forwarded-For is the visitor.
    PARTYLINKS_SECURITY__TRUSTED_PROXIES='["*"]'
WORKDIR /app

# Runtime dependencies only (the [project] list in pyproject.toml), cached apart from the code.
COPY pyproject.toml ./
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" > /tmp/req.txt \
 && pip install -r /tmp/req.txt && rm /tmp/req.txt

COPY src/ src/
COPY config/ config/
COPY content/ content/
COPY --from=web /web/dist web/dist

# SQLite DB, artist dictionary, images, inboxes: mount a volume here or everything resets on deploy.
VOLUME ["/app/data"]
EXPOSE 8000

# $PORT is set by Railway; the public domain becomes the site's canonical URL unless set explicitly.
CMD ["sh", "-c", "export PARTYLINKS_API__PORT=\"${PORT:-8000}\"; if [ -z \"$PARTYLINKS_SITE__BASE_URL\" ] && [ -n \"$RAILWAY_PUBLIC_DOMAIN\" ]; then export PARTYLINKS_SITE__BASE_URL=\"https://$RAILWAY_PUBLIC_DOMAIN\"; fi; exec python -m partylinks serve"]
