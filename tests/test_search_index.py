"""The search's full-text index (partylinks.search_index) and ranking: Hebrew and English, partial words,
final letters / niqqud / geresh, sound-alike venues, spelling fixes, Israel before abroad, suggestions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select, text, update

from partylinks import search_index
from partylinks.api import create_app
from partylinks.db import EventRow
from partylinks.search import NameIndex, SuggestIndex, correct_spelling
from partylinks.search_index import norm
from tests.test_storage_api import event, seed, settings_for


def test_text_is_normalised_as_the_index_holds_it() -> None:
    assert norm("NEXT with Omer Adam, Odeya – אושר כהן!") == "next with omer adam odeya אושר כהנ"
    assert norm("ג׳אז") == norm("ג'אז") == norm("גאז") == "גאז"
    assert norm("שָׁלוֹם") == norm("שלום") == "שלומ"  # niqqud, final mem
    assert norm('״הבימה״ / "Beyoncé"') == "הבימה beyonce"
    assert norm("סטנד-אפ") == "סטנד אפ"
    assert search_index.query_text(["omer"]) == "('omer':*)"
    assert "'בבארבי':*" in (search_index.query_text(["בארבי"]) or "")  # behind a prefix letter


def test_the_database_normalises_as_python_does(tmp_path: Path) -> None:
    db = seed(settings_for(tmp_path), [])
    with db.engine.connect() as conn:
        for s in (
            "NEXT with Omer Adam, Odeya – אושר כהן!",
            "ג׳אז",
            "שָׁלוֹם",
            '״הבימה״ / "Beyoncé"',
            "a\tb\nc",
            "🤟 Rock'n'Roll",
        ):
            assert " ".join(conn.scalar(text("SELECT spotx_norm(:s)"), {"s": s}).split()) == norm(s), s


def test_the_search_reads_the_index(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    db = seed(settings, [event("a", datetime.now(UTC) + timedelta(days=1))])
    assert search_index.ready()
    with db.engine.connect() as conn:
        conn.execute(text("SET enable_seqscan = off"))
        sql = (
            select(EventRow.id)
            .where(search_index.matches("techno"))
            .compile(db.engine, compile_kwargs={"literal_binds": True})
        )
        plan = "\n".join(conn.scalars(text(f"EXPLAIN {sql}")))
    assert search_index.INDEX in plan


def test_partial_words_finals_and_spellings_find_the_event(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    soon = datetime.now(UTC) + timedelta(days=1)
    db = seed(settings, [
        event("omer", soon, title="עומר אדם בהופעה", venue_name="היכל מנורה"),
        event("shalom", soon, title="שָׁלוֹם חֲנוֹך"),
        event("jazz", soon, title="ערב ג׳אז", description="נגנים"),
        event("barby", soon, title="Hadag Nahash", venue_name="Barby"),
        event("he_barby", soon + timedelta(days=1), title="אמיר לב", venue_name="בארבי"),
        event("text", soon, title="Quiet night", description="אחרי ההופעה בבארבי נמשיך"),
        event("past", datetime.now(UTC) - timedelta(days=3), title="עומר אדם בעבר"),
    ])  # fmt: skip
    with db.session() as session:  # lineups come from curated artists only: set it as stored
        session.execute(update(EventRow).where(EventRow.source_event_id == "omer").values(lineup=["עומר אדם"]))
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        ids = lambda q: sorted(e["source_event_id"] for e in client.get("/events", params={"q": q}).json()["items"])
        assert (
            ids("עומ") == ids("עומר") == ids("omer adam") == ["omer"]
        )  # a word's start; the other alphabet; never past events
        assert ids("שלום חנוך") == ["shalom"]  # niqqud and final letters
        assert ids("ג'אז") == ids("גאז") == ["jazz"]
        assert {"barby", "he_barby", "text"} <= set(ids("בארבי"))  # the venue in either spelling, the text behind "ב"
        assert {"barby", "he_barby"} <= set(ids("barby"))
        assert ids("מנורה") == ["omer"]  # the venue


def test_the_whole_search_together_ranks_first_and_israel_before_abroad(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    soon = datetime.now(UTC) + timedelta(days=1)
    seed(settings, [
        event("eve", soon, title="Sam Fawcett at The Adam & Eve", description="with omer"),
        event("next", soon + timedelta(days=5), title="NEXT with Omer Adam, Odeya"),
        event("abroad", soon, title="Jazz at the Blue Note", city="New York", country="US", address="New York, USA"),
        event("here", soon + timedelta(days=3), title="Jazz on the roof"),
    ])  # fmt: skip
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        ids = lambda q: [e["source_event_id"] for e in client.get("/events", params={"q": q}).json()["items"]]
        assert ids("omer adam") == ["next", "eve"]  # both words, together in the title, before an earlier "Adam"
        assert ids("jazz") == ["here", "abroad"]  # same match: the event in Israel first, though later


def test_a_misspelled_search_shows_the_closest_spelling(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    soon = datetime.now(UTC) + timedelta(days=1)
    db = seed(settings, [event("im", soon, title="Psy night"), event("adam", soon, title="Omer Adam live")])
    with db.session() as session:  # lineups come from curated artists only: set it as stored
        session.execute(update(EventRow).where(EventRow.source_event_id == "im").values(lineup=["Infected Mushroom"]))
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        page = client.get("/events", params={"q": "infested mushrom"}).json()  # not a sound-alike: a letter off
        assert [e["source_event_id"] for e in page["items"]] == ["im"]
        assert (page["widened"], page["widened_q"]) == ("spelling", "infected mushroom")
        page = client.get("/events", params={"q": "omer adm"}).json()
        assert [e["source_event_id"] for e in page["items"]] == ["adam"] and page["widened_q"] == "omer adam"


def test_spelling_fixes_keep_known_words() -> None:
    index = NameIndex.of([(["Infected Mushroom"], "Barby", "Omer Adam live")])
    assert correct_spelling("omer adm", index, 0.85) == "omer adam"
    assert correct_spelling("infected mushroom", index, 0.85) is None
    assert correct_spelling("zzzz qqqq", index, 0.85) is None


def test_suggestions_match_starts_words_and_the_other_alphabet(tmp_path: Path) -> None:
    index = SuggestIndex({
        "artists": [{"value": "עומר אדם", "events": 9}, {"value": "Omer Adam", "events": 2}, {"value": "Adam Ten", "events": 5},
                    {"value": "Infected Mushroom", "events": 3}],
        "venues": [{"value": "Barby", "events": 40}, {"value": "בארבי", "events": 30}],
    })  # fmt: skip
    names = lambda q, kind="artists": [x["value"] for x in index.find(q, 5)[kind]]
    assert names("omer") == ["Omer Adam"]
    assert names("omer adam") == ["Omer Adam", "עומר אדם"]  # the same name in Hebrew, by its sound
    assert names("adam") == ["Adam Ten", "Omer Adam"]  # a name's start first, then a word's
    assert names("mush") == ["Infected Mushroom"]
    assert names("בבארבי", "venues") == ["בארבי"] and names("barb", "venues") == ["Barby"]
    assert names("x") == []

    settings = settings_for(tmp_path)
    seed(settings, [event("a", datetime.now(UTC) + timedelta(days=1), venue_name="Zappa Herzliya")])
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        found = client.get("/search/suggest", params={"q": "herz"}).json()
        assert [(v["value"], v["events"]) for v in found["venues"]] == [("Zappa Herzliya", 1)]
        assert client.get("/search/suggest", params={"q": "z"}).json()["venues"] == []
