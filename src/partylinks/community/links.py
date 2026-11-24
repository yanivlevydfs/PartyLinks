"""Ticket links a user types: https only, a real public host name, no password in the link, never a link
shortener or another blocked host (CommunitySettings.blocked_link_hosts). Links leave the site only through
``/go/{id}?link=n`` (api.go), which reads them from the listing's ``extra.ticket_links``."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit, urlunsplit

MAX_LENGTH = 1000


class BadLinkError(ValueError):
    pass


def _blocked(host: str, blocked: list[str]) -> bool:
    return any(host == b or host.endswith(f".{b}") for b in (x.strip().lower().lstrip(".") for x in blocked) if b)


def checked_link(raw: str, blocked: list[str]) -> str:
    """The link as stored (host lowercased, no #fragment), or BadLinkError saying why not."""
    text = raw.strip()
    if not text or len(text) > MAX_LENGTH:
        raise BadLinkError("link: empty or too long")
    if any(c.isspace() or ord(c) < 32 for c in text):
        raise BadLinkError("link: has spaces")
    try:
        parts = urlsplit(text)
        port = parts.port  # a port that is not a number raises here
    except ValueError as exc:
        raise BadLinkError("link: not a web address") from exc
    if parts.scheme.lower() != "https":
        raise BadLinkError("link: must start with https://")
    if parts.username or parts.password:
        raise BadLinkError("link: must not hold a user name or password")
    host = (parts.hostname or "").rstrip(".").lower()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise BadLinkError("link: an IP address, not a site")
    if "." not in host or host.endswith((".local", ".localhost", ".internal")) or host == "localhost":
        raise BadLinkError("link: not a public site")
    if _blocked(host, blocked):
        raise BadLinkError(f"link: {host} is not accepted (a link shortener or a blocked site)")
    netloc = host if port in (None, 443) else f"{host}:{port}"
    return urlunsplit(("https", netloc, parts.path or "/", parts.query, ""))
