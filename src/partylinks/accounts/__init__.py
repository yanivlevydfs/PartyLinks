"""Optional Google / Facebook accounts (see docs/accounts-alerts-design.md). Nothing on the site needs one: an
account only adds saved events across devices, alerts, the bell and email.

- ``config``: the ``accounts:`` settings block.
- ``models``: ``users`` and ``user_sessions``, on the app's one ``Base``.
- ``sessions``: the ``spotx_session`` cookie (only its SHA-256 is stored) and the current-user dependency.
- ``service``: signing in and deleting an account.
- ``google``: sign in with Google through a server redirect (Authlib); no Google script on our pages.
- ``facebook``: the same with Facebook, and Meta's data deletion callback.
- ``routes``: ``/api/me`` (profile, digest consent, sign out, delete).
- ``admin_routes``: the admin's users tab.

Passkeys (fingerprint / face sign-in for an existing account) live in their own package, ``partylinks.passkeys``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

    from partylinks.config import Settings


def sign_in_state(app: FastAPI, settings: Settings) -> None:
    """Authlib's state, nonce and PKCE verifier, in a signed cookie for the 10 minutes of one sign-in.
    Only when Google or Facebook sign-in is configured."""
    from starlette.middleware.sessions import SessionMiddleware

    if settings.accounts.ready:
        assert settings.accounts.session_secret is not None
        app.add_middleware(
            SessionMiddleware,
            secret_key=settings.accounts.session_secret.get_secret_value(),
            session_cookie="spotx_signin",
            max_age=settings.accounts.sign_in_minutes * 60,
            same_site="lax",
            https_only=settings.site.base_url.startswith("https://"),
        )


def install(app: FastAPI, settings: Settings) -> None:
    """The account routers; each sign-in provider's only when it is configured."""
    from partylinks.accounts.admin_routes import router as admin_router
    from partylinks.accounts.facebook import router as facebook_router
    from partylinks.accounts.google import router as google_router
    from partylinks.accounts.routes import router as me_router

    if settings.accounts.google_ready:
        app.include_router(google_router)
    if settings.accounts.facebook_ready:
        app.include_router(facebook_router)
    if settings.accounts.ready:  # passkeys sign in to accounts made with Google / Facebook (partylinks.passkeys)
        from partylinks.passkeys.routes import router as passkeys_router

        app.include_router(passkeys_router)
    app.include_router(me_router)  # /api/me answers 401 when nobody can sign in
    app.include_router(admin_router)

    from partylinks.accounts.admin_routes import COUNTS
    from partylinks.alerts.routes import alert_counts
    from partylinks.alerts.routes import router as alerts_router
    from partylinks.saved.routes import saved_counts

    COUNTS["saved"] = saved_counts
    COUNTS["alerts"] = alert_counts
    app.include_router(alerts_router)
    from partylinks.mail.admin_routes import router as mail_admin_router
    from partylinks.mail.routes import router as mail_router

    app.include_router(mail_router)  # unsubscribe links
    app.include_router(mail_admin_router)
