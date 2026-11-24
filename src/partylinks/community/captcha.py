"""Cloudflare Turnstile, checked on our server for every posted or edited event: the page's widget hands the form a
one-time token, and Cloudflare's siteverify says whether a person solved it (for this site, once, recently)."""

from __future__ import annotations

import logging

import httpx
from fastapi import HTTPException, status

from partylinks.community.config import CommunitySettings

log = logging.getLogger(__name__)
TIMEOUT = httpx.Timeout(8.0)


def verify(cfg: CommunitySettings, token: str, ip: str | None) -> None:
    """Return when the token is good; 400 when it is not, 503 when the check can't be made."""
    if not cfg.captcha_required:
        return
    if cfg.turnstile_secret is None or not cfg.turnstile_secret.get_secret_value():
        log.error("community: captcha_required but PARTYLINKS_COMMUNITY__TURNSTILE_SECRET is not set")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "captcha: not set up")
    if not token:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "captcha: missing")
    form = {"secret": cfg.turnstile_secret.get_secret_value(), "response": token}
    if ip:
        form["remoteip"] = ip
    try:
        res = httpx.post(cfg.turnstile_verify_url, data=form, timeout=TIMEOUT)
        res.raise_for_status()
        answer = res.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("community: captcha check failed: %s", type(exc).__name__)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "captcha: could not be checked, try again") from exc
    if not answer.get("success"):
        log.info("community: captcha refused (%s)", ",".join(answer.get("error-codes") or []) or "-")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "captcha: not solved, try again")
