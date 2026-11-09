"""An event's description in blocks, and the scrapers keeping the structure their sources have."""

from __future__ import annotations

from collections import Counter

from partylinks.event_text import blocks_html, description_layout
from partylinks.event_text.config import EventTextSettings
from partylinks.parsing import html_to_text
from partylinks.scrapers.chillz import parse_page
from partylinks.scrapers.hatarbut import _full_text
from partylinks.scrapers.telaviv import _BREAK

S = EventTextSettings()


def kinds(text: str) -> list[str]:
    layout = description_layout(text, S)
    return [b.kind for b in layout.blocks] if layout else []


def test_nothing_to_show() -> None:
    assert description_layout(None, S) is None and description_layout("  \n ", S) is None and blocks_html(None) == ""


def test_flat_text_is_cut_between_sentences_and_keeps_every_word() -> None:
    flat = " ".join(f"משפט מספר {i} שמספר על האירוע הזה בפירוט רב ובמילים רבות." for i in range(40))
    layout = description_layout(flat, S)
    assert layout is not None and len(layout.blocks) > 3
    assert all(b.kind == "p" and b.text.endswith(".") for b in layout.blocks)
    assert Counter(" ".join(b.text for b in layout.blocks).split()) == Counter(flat.split())


def test_abbreviations_and_numbers_are_no_sentence_end() -> None:
    flat = "x" * 400 + " the Sgt. Pepper album and 12. Then more of it follows " + "y" * 400
    assert [b.text for b in description_layout(flat, S).blocks] == [flat]  # type: ignore[union-attr]


def test_heading_list_and_stacked_lines() -> None:
    text = "מה יש לעשות? (לאורך הרחוב)\nדיזנגוף 142 - פסנתרים\nדיזנגוף 143: קפה\nLee - דיזנגוף 162\nיום שבת\n20:00"
    layout = description_layout(text, S)
    assert layout is not None
    assert [b.kind for b in layout.blocks] == ["h", "ul", "p"]
    assert layout.blocks[1].plain and len(layout.blocks[1].items) == 3
    assert layout.blocks[2].text == "יום שבת\n20:00"


def test_a_last_line_is_no_heading_and_a_heading_ends_a_list() -> None:
    assert kinds("טקסט כלשהו\nלפרטים:") == ["p"]
    assert kinds("א 1 - ב\nא 2 - ב\nא 3 - ב\nתערוכות - כניסה חינם:\nטקסט") == ["ul", "h", "p"]


def test_bullets_lose_their_mark_and_inline_ones_become_a_list() -> None:
    layout = description_layout("- אחד\n\n- שתיים", S)
    assert layout is not None and layout.blocks[0].items == ["אחד", "שתיים"] and not layout.blocks[0].plain
    assert kinds("Vibe • Golden EDM • Melodic Bass • Tech House") == ["p", "ul"]
    assert kinds("תנאים: *אירוע פרטי *גיל 18 *ללא החזר") == ["h", "ul"]


def test_markdown_titles_rules_and_html() -> None:
    assert kinds("**Event Details**\n---\ntext\n## Speaker:\nname") == ["h", "p", "h", "p"]
    html = blocks_html(description_layout("**About**\nsee [the site](https://a.example/x) 1 < 2 & **soon**\nline", S))
    assert html.startswith('<h3 dir="ltr">About</h3>')
    assert "see the site 1 &lt; 2 &amp; soon<br>line" in html and "**" not in html


def test_direction_follows_the_text_not_the_first_word() -> None:
    hebrew = "המופע המרכזי של הערב בכיכובה של הזמרת האהובה בליווי להקה גדולה ומיוחדת במינה"
    english = "A long English paragraph about the band and the night that runs well past the threshold."
    layout = description_layout(f"{hebrew}\n\nLee\n\n{english}\n\n{hebrew}", S)
    assert layout is not None
    assert [b.dir for b in layout.blocks] == ["rtl", "rtl", "ltr", "rtl"]  # short Latin keeps the page's side


def test_collapse_only_when_long() -> None:
    assert not description_layout("קצר.", S).collapse  # type: ignore[union-attr]
    assert description_layout("מילה " * 500, S).collapse  # type: ignore[union-attr]


def test_html_to_text_drops_code_and_keeps_list_items() -> None:
    text = html_to_text(
        "<style>.a{color:red}</style><p>שלום<script>var d = document;</script></p><ul><li>אחד</li><li>שתיים</li></ul>"
    )
    assert text == "שלום\n• אחד\n• שתיים"


def test_telaviv_runs_of_spaces_are_line_breaks() -> None:
    raw = "מה יש לעשות?\u200b    19:00 - סיור.\xa0  בית לסין: הצגות.  לרכישה:  https://bit.ly/x"
    assert _BREAK.sub("\n", raw.replace("\u200b", "")).split("\n") == [
        "מה יש לעשות?",
        "19:00 - סיור.",
        "בית לסין: הצגות.  לרכישה:  https://bit.ly/x",
    ]


def test_chillz_reads_the_full_page_text_not_the_cut_jsonld() -> None:
    page = (
        '<script type="application/ld+json">{"@type":"Event","name":"N","startDate":"2027-01-01T20:00:00+02:00",'
        '"description":"Doors open..."}</script><div class="prose"><p>Doors open: 21:00</p><p>All of the text</p></div>'
    )
    event = parse_page(page, "https://chillz.com/en/n", "Asia/Jerusalem")
    assert event is not None and event.description == "Doors open: 21:00\nAll of the text"


def test_hatarbut_finds_the_full_text_by_the_teasers_end() -> None:
    block = '<div class="elementor-widget-text-editor">{}</div>'
    page = block.format("<p>פתיחת דלתות</p>") + block.format(
        "<p>לאור ההצלחה חוזר המחזמר האהוב לבמות בחנוכה</p><p>וזה ההמשך</p>"
    )
    assert (
        _full_text(page, "תאריכים לאור ההצלחה חוזר המחזמר האהוב לבמות בחנוכה...")
        == "לאור ההצלחה חוזר המחזמר האהוב לבמות בחנוכה\nוזה ההמשך"
    )
    assert _full_text(page, "טקסט אחר לגמרי שלא מופיע...") is None
