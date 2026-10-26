"""IndexNow: tell Bing, Yandex, Seznam and Naver about new pages as soon as they exist, instead of
waiting for a crawl. Bing's index is what ChatGPT search and Copilot answer from.

The key is public by design: it is served at ``/<key>.txt`` to prove the site owns the URLs.
URLs already sent are remembered in ``site.indexnow.state_file``, so each run sends only new ones
(new events, venues, artists, landing pages). Only for a public https base_url: never from localhost.
"""

from __future__ import annotations

import html
import logging
import re
from pathlib import Path

import httpx

from partylinks.config import Settings
from partylinks.db import Database

log = logging.getLogger(__name__)

ENDPOINT = "https://api.indexnow.org/indexnow"
BATCH = 10_000  # the protocol's per-request limit
LOC = re.compile(r"<loc>([^<]+)</loc>")


def new_urls(urls: list[str], state: Path) -> list[str]:
    sent = set(state.read_text(encoding="utf-8").split()) if state.is_file() else set()
    return [u for u in dict.fromkeys(urls) if u not in sent]


def submit(settings: Settings, db: Database) -> int:
    """Send pages not sent before; returns how many were accepted."""
    cfg = settings.site
    if not cfg.indexnow.key or not cfg.base_url.startswith("https://"):
        return 0
    from partylinks.seo import sitemap_urls  # local: seo is heavy and imports the API's models

    with db.session(write=False) as session:
        urls = [html.unescape(m) for entry in sitemap_urls(session, settings) for m in LOC.findall(entry)]
    state = Path(cfg.indexnow.state_file)
    todo = new_urls(urls, state)
    host = cfg.base_url.split("://", 1)[1]
    sent = 0
    for i in range(0, len(todo), BATCH):
        chunk = todo[i : i + BATCH]
        body = {"host": host, "key": cfg.indexnow.key, "keyLocation": f"{cfg.base_url}/{cfg.indexnow.key}.txt", "urlList": chunk}
        response = httpx.post(ENDPOINT, json=body, timeout=60)
        if response.status_code not in (200, 202):  # 403: key file not reachable yet, 422: URL off-host
            log.warning("indexnow: %s refused %d URLs: %s", response.status_code, len(chunk), response.text[:200])
            break
        state.parent.mkdir(parents=True, exist_ok=True)
        with state.open("a", encoding="utf-8") as f:
            f.write("\n".join(chunk) + "\n")
        sent += len(chunk)
    log.info("indexnow: sent %d new URLs (%d known)", sent, len(urls) - len(todo))
    return sent


if __name__ == "__main__":  # self-check: only unseen URLs, in order, without duplicates
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "sent.txt"
        state.write_text("https://a/1\nhttps://a/2\n", encoding="utf-8")
        assert new_urls(["https://a/2", "https://a/3", "https://a/1", "https://a/3"], state) == ["https://a/3"]
        assert new_urls(["https://a/9"], Path(tmp) / "missing.txt") == ["https://a/9"]
    print("ok")
