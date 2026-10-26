from partylinks.config import UtmSettings
from partylinks.db import EventRow
from partylinks.utm import tag_url, tagged, utm_params


def row(**kw: object) -> EventRow:
    return EventRow(id="test:a", source="test", source_event_id="a", url="https://s.example/a", title="A", **kw)


def test_one_event_gets_its_own_campaign_placement_and_city() -> None:
    params = utm_params(UtmSettings(), row(slug="techno-night", city="Haifa"), "alt")
    assert params == {
        "utm_source": "spotx",
        "utm_medium": "affiliate",
        "utm_campaign": "techno-night",
        "utm_content": "alt",
        "utm_term": "Haifa",
    }


def test_unknown_placement_and_missing_fields_are_left_out() -> None:
    params = utm_params(UtmSettings(), row(), "<script>")
    assert params == {"utm_source": "spotx", "utm_medium": "affiliate", "utm_campaign": "test:a"}


def test_seller_params_are_kept_as_is_and_old_utm_replaced() -> None:
    url = "https://s.example/e?id=1&q=a+b%2Fc&UTM_source=other&utm_medium=x#seats"
    assert tag_url(url, {"utm_source": "spotx"}) == "https://s.example/e?id=1&q=a+b%2Fc&utm_source=spotx#seats"
    assert tag_url("https://s.example/e", {"utm_campaign": "תל אביב"}) == (
        "https://s.example/e?utm_campaign=%D7%AA%D7%9C+%D7%90%D7%91%D7%99%D7%91"
    )


def test_disabled_leaves_the_url_alone() -> None:
    assert tagged("https://s.example/a?x=1", UtmSettings(enabled=False), row()) == "https://s.example/a?x=1"
