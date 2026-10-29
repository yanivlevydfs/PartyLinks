"""Optional Google accounts (see docs/accounts-alerts-design.md). Nothing on the site needs one: an
account only adds saved events across devices, alerts, the bell and email.

- ``config``: the ``accounts:`` settings block.
- ``models``: ``users`` and ``user_sessions``, on the app's one ``Base``.
- ``sessions``: the ``spotx_session`` cookie (only its SHA-256 is stored) and the current-user dependency.
- ``service``: signing in and deleting an account.
- ``google``: sign in with Google through a server redirect (Authlib); no Google script on our pages.
- ``routes``: ``/api/me`` (profile, digest consent, sign out, delete).
- ``admin_routes``: the admin's users tab.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

    from partylinks.config import Settings


def sign_in_state(app: FastAPI, settings: Settings) -> None:
    """Authlib's state, nonce and PKCE verifier, in a signed cookie for the 10 minutes of one sign-in.
    Only when Google sign-in is configured."""
    from starlette.middleware.sessions import SessionMiddleware

    if settings.accounts.ready:
        assert settings.accounts.session_secret is not None
        app.add_middleware(
            SessionMiddleware,
            secret_key=settings.accounts.session_secret.get_secret_value(),
            session_cookie="spotx_signin",
            max_age=600,
            same_site="lax",
            https_only=settings.site.base_url.startswith("https://"),
        )


def install(app: FastAPI, settings: Settings) -> None:
    """The account routers; the Google ones only when sign-in is configured."""
    from partylinks.accounts.admin_routes import router as admin_router
    from partylinks.accounts.google import router as google_router
    from partylinks.accounts.routes import router as me_router

    if settings.accounts.ready:
        app.include_router(google_router)
    app.include_router(me_router)  # /api/me answers 401 when nobody can sign in
    app.include_router(admin_router)

    from partylinks.accounts.admin_routes import COUNTS
    from partylinks.saved.routes import saved_counts

    COUNTS["saved"] = saved_counts
