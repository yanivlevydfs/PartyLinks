from datetime import date

import pytest
from pydantic import ValidationError

from partylinks.home_rails.config import HomeRail


def test_holiday_row_window_is_its_next_dates() -> None:
    assert HomeRail(key="new_year", holiday="new_year").window(date(2026, 11, 18)) == (
        date(2026, 12, 28),
        date(2027, 1, 1),
    )
    assert HomeRail(key="purim", holiday="purim").window(date(2026, 11, 18)) == (date(2027, 3, 19), date(2027, 3, 24))


def test_month_row_window_is_the_next_such_month() -> None:
    assert HomeRail(key="pride", month=6).window(date(2026, 11, 18)) == (date(2027, 6, 1), date(2027, 6, 30))
    assert HomeRail(key="pride", month=6).window(date(2027, 6, 10)) == (date(2027, 6, 10), date(2027, 6, 30))
    assert HomeRail(key="plain").window(date(2026, 11, 18)) is None


def test_unknown_holiday_is_refused() -> None:
    with pytest.raises(ValidationError):
        HomeRail(key="x", holiday="purrim")
