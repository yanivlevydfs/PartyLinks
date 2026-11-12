from partylinks.models import TicketTier


def test_every_currency_is_stored_as_an_iso_code() -> None:
    """OGO wrote "$" (2026-11-12) and the site's Intl.NumberFormat crashed the page."""
    codes = {raw: TicketTier(name="x", currency=raw).currency for raw in ("$", "usd", "₪", " eur ", "NIS", "ILS")}
    assert codes == {"$": "USD", "usd": "USD", "₪": "ILS", " eur ": "EUR", "NIS": "ILS", "ILS": "ILS"}
