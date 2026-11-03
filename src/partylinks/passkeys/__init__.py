"""Sign in with a fingerprint, face or screen lock: passkeys (WebAuthn, through py_webauthn).

A signed-in user adds a passkey on their account page; from then on that device signs them in without
Google or Facebook. The passkey is discoverable, so signing in asks for no email first. Only an existing
account gets one: new accounts still start with Google or Facebook (their verified email).

- ``models``: ``user_passkeys``, one row per passkey (its public key; the private key never leaves the device).
- ``routes``: ``/api/passkeys``: add, list, remove, sign in.
"""
