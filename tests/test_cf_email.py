"""Cloudflare's hidden emails ("[email protected]") are written out for every source (parsing.cf_emails)."""

from __future__ import annotations

from partylinks.parsing import cf_emails, drop_lost_emails


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


def test_a_lost_placeholder_goes_with_its_label() -> None:
    text = "ניתן לפנות, טל': 050-1234567 | דוא\"ל: [email protected]"
    assert drop_lost_emails(text) == "ניתן לפנות, טל': 050-1234567"
    lost = "Tel.: 050-1234567 | Email: [email protected]\nSee you"
    assert drop_lost_emails(lost) == "Tel.: 050-1234567\nSee you"
    assert drop_lost_emails("contact us only at [email protected] today") == (
        "contact us only at (on the organizer's page) today"
    )
    assert drop_lost_emails("write to info@example.com") == "write to info@example.com"
    assert drop_lost_emails(None) is None
