"""Cloudflare's hidden emails ("[email protected]") are written out for every source (parsing.cf_emails)."""

from __future__ import annotations

from partylinks.parsing import cf_emails


def test_cloudflare_emails_are_decoded_in_html_and_json() -> None:
    page = (b'x <a href="/cdn-cgi/l/email-protection" class="__cf_email__" '
            b'data-cfemail="61080f070e210419000c110d044f020e0c">[email&#160;protected]</a> y')  # fmt: skip
    assert cf_emails(page) == b"x info@example.com y"
    as_json = (b'{"d":"<span class=\\"__cf_email__\\" data-cfemail=\\"61080f070e210419000c110d044f020e0c\\">'
               b'[email protected]<\\/span>"}')  # fmt: skip
    assert cf_emails(as_json) == b'{"d":"info@example.com"}'
    link = b'<a href="/cdn-cgi/l/email-protection#61080f070e210419000c110d044f020e0c">'
    assert cf_emails(link) == b'<a href="mailto:info@example.com">'
    assert cf_emails(b"no email here") == b"no email here"
