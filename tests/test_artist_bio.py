from datetime import date

from partylinks.artist_bio import Facts, compose


def test_compose_both_languages() -> None:
    f = Facts(3, date(2026, 11, 6), "Gagarin", "Tel Aviv", ["Techno"], ["Gagarin", "Haoman 17"], ["Tel Aviv", "Haifa"], ["Greece"])
    assert compose("en", "Boris", f, "SPOTX") == (
        "Boris has 3 upcoming shows on SPOTX, mostly Techno. Next: Friday 06/11/2026 at Gagarin, Tel Aviv. "
        "Venues: Gagarin and Haoman 17. Cities: Tel Aviv and Haifa. Abroad: Greece."
    )
    he = compose("he", "Boris", f, "SPOTX")
    assert he.startswith("ל־Boris יש 3 הופעות קרובות ב־SPOTX, בעיקר Techno. הקרובה: שישי 06/11/2026 בGagarin, Tel Aviv.")
    assert "מקומות: Gagarin ו־Haoman 17." in he


def test_one_show_no_venue_lists() -> None:
    f = Facts(1, None, None, None)
    assert compose("en", "X", f, "SPOTX") == "X has 1 upcoming show on SPOTX."
    assert compose("he", "X", f, "SPOTX") == "ל־X יש הופעה קרובה אחת ב־SPOTX."
