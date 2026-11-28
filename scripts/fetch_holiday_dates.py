"""Fetches the dates of the holidays the Hebrew calendar does not give (Christian, Muslim, Mimouna...) and writes
src/partylinks/holiday_dates.json, which search._holiday reads before computing a date. Run once a year (the
Muslim dates are tentative: run again a week before Ramadan / the Eids), from the project root:

    python scripts/fetch_holiday_dates.py

Sources: Google's public "Holidays in Israel" calendar (the en.jewish holiday calendar: Jewish, Israeli,
Christian - Western, Orthodox, Armenian -, Druze and Muslim days, the Muslim ones marked tentative) for the
dates, and Hebcal's Israel calendar (https://www.hebcal.com, CC BY 4.0) to check our own Hebrew-calendar
dates (search._holiday) against it: the script stops if they disagree.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from partylinks.search import _holiday

OUT = ROOT / "src/partylinks/holiday_dates.json"
GOOGLE = "https://calendar.google.com/calendar/ical/en.jewish%23holiday%40group.v.calendar.google.com/public/basic.ics"
HEBCAL = "https://www.hebcal.com/hebcal?v=1&cfg=json&maj=on&min=on&mod=on&i=on&year={year}"
# Our key -> (the calendar's day that opens it, the day that ends it): the eve to the last day.
SPANS = {
    "ramadan": ("Ramadan begins", "Eid al-Fitr Eve"),  # the eve of the first fast to the last fast
    "eid_al_fitr": ("Eid al-Fitr Eve", "Eid al-Fitr Holiday"),
    "eid_al_adha": ("Eid al-Adha Eve", "Eid al-Adha Holiday"),
    "mimouna": ("Mimouna Day", "Mimouna Day"),
}
# Hebcal title -> our key, for the cross-check of the dates we compute (their first day).
HEBCAL_CHECK = {"Chanukah: 1 Candle": "hanukkah", "Purim": "purim", "Pesach I": "passover", "Shavuot": "shavuot",
                "Rosh Hashana 5787": "rosh_hashanah", "Rosh Hashana 5788": "rosh_hashanah", "Sukkot I": "sukkot",
                "Lag BaOmer": "lag_baomer", "Tu BiShvat": "tu_bishvat", "Tu B'Av": "tu_bav",
                "Yom HaAtzma'ut": "independence_day"}  # fmt: skip


def google_days(ics: str) -> list[tuple[date, str]]:
    days = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", ics.replace("\r\n ", ""), re.DOTALL):
        start, title = re.search(r"DTSTART;VALUE=DATE:(\d{8})", block), re.search(r"SUMMARY:(.*)", block)
        if start and title:
            d = start.group(1)
            name = re.sub(r"\s*\(tentative\)\s*$", "", title.group(1).strip())
            days.append((date(int(d[:4]), int(d[4:6]), int(d[6:])), name))
    return sorted(days)


def spans(days: list[tuple[date, str]]) -> dict[str, list[list[str]]]:
    """Each holiday's spans: its opening day to the first closing day after it (the last of a run of holiday days)."""
    out: dict[str, list[list[str]]] = {}
    for key, (first, last) in SPANS.items():
        for d, name in days:
            if name != first:
                continue
            ends = [e for e, n in days if n == last and d <= e <= d + timedelta(days=40)]
            if not ends:  # its closing day is past the calendar's end (Ramadan 2031): not a span yet
                continue
            # A run of "... Holiday" days: the last one before the next gap.
            end = ends[0]
            while any(e == end + timedelta(days=1) and n == last for e, n in days):
                end += timedelta(days=1)
            # The Mimouna is the night Passover ends: its parties start the evening before its day.
            opens = d - timedelta(days=1) if key == "mimouna" else d
            out.setdefault(key, []).append([opens.isoformat(), end.isoformat()])
    return out


def check_hebcal(years: list[int]) -> None:
    for year in years:
        items = httpx.get(HEBCAL.format(year=year), timeout=30).json()["items"]
        for item in items:
            key = HEBCAL_CHECK.get(item["title"])
            if not key:
                continue
            day = date.fromisoformat(item["date"][:10])
            start, end = _holiday(year, key)
            if not start <= day <= end:
                raise SystemExit(f"{key} {year}: Hebcal {day}, ours {start}..{end}")
    print(f"Hebcal agrees with our Hebrew-calendar dates ({', '.join(map(str, years))})")


def main() -> None:
    ics = httpx.get(GOOGLE, timeout=60, follow_redirects=True).text
    days = google_days(ics)
    data = spans(days)
    if not data.get("ramadan") or not data.get("eid_al_adha"):
        raise SystemExit("the calendar lost its Muslim dates: not written")
    years = sorted({d.year for d, _ in days if d >= date.today()})[:2]
    check_hebcal(years)
    payload = {"source": "Google Holidays in Israel calendar; checked against Hebcal", "fetched": date.today().isoformat(),
               "spans": data}  # fmt: skip
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for key, rows in data.items():
        print(key, rows)


if __name__ == "__main__":
    main()
