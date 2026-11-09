"""``place_info:`` in settings.yaml; the admin changes any of it in the config tab (config_overrides)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PlaceInfoSettings(BaseModel):
    enabled: bool = True
    # The worker's pass: daily, reading only what is new or older than refresh_days.
    interval_hours: int = Field(24, ge=1)
    user_agent: str = "Mozilla/5.0 (compatible; SPOTX/0.1; +https://spotx.fun)"
    timeout_seconds: float = Field(20, gt=0)
    # Between two requests, the pass makes one at a time. Ontopo's CloudFront refused (403) an IP after ~1,000
    # pages at 2-3 a second, for a few minutes; 1,691 pages at this pace passed (measured, Nov 2026). A first
    # read of all ~2,750 pages takes ~2.5 hours.
    delay_seconds: float = Field(3, ge=0)
    max_consecutive_failures: int = Field(5, ge=1)  # that many in a row end the pass (blocked or down); the next continues
    refresh_days: int = Field(14, ge=1)
    # Ontopo: robots.txt allows the pages and the sitemap, and disallows /api/ and query strings.
    ontopo_sitemap: str = "https://ontopo.com/sitemap.xml"
    ontopo_page: str = "https://ontopo.com/he/il/page/{slug}"
    ontopo_booking: str = "https://ontopo.com/{lang}/il/page/{slug}?source=spotx"
    ontopo_event_prefix: str = "אירוע ב"  # a one-off's page ("אירוע בקפה אירופה"), never matched: the venue has its own
    # Matching a place to an Ontopo venue: a like name within name_radius_m (SequenceMatcher ratio, on the
    # name without words like "bar"), or its phone within phone_radius_m with a name that sounds alike.
    # Measured against HappyTLV's own Ontopo links: phone alone was wrong for 1 in 5, a restaurateur's places share one.
    name_radius_m: float = Field(150, ge=0)
    name_similarity: float = Field(0.85, ge=0, le=1)
    phone_radius_m: float = Field(300, ge=0)
    sound_similarity: float = Field(0.7, ge=0, le=1)
    # The place's own website: the most bytes read, and hosts that are profiles, not a website.
    website_max_bytes: int = Field(2_000_000, ge=1000)
    social_hosts: list[str] = Field(default_factory=lambda: [
        "instagram.com", "facebook.com", "tiktok.com", "linktr.ee", "wa.me", "youtube.com", "ontopo.com", "ontopo.co.il",
    ])  # fmt: skip
    # A place's schema.org type by its genre, the first that fits; LocalBusiness otherwise.
    schema_types: dict[str, str] = Field(default_factory=lambda: {
        "fine_dining": "Restaurant", "happy_hour": "BarOrPub", "nightclubs": "NightClub", "coffee_carts": "CafeOrCoffeeShop",
        "work_spaces": "CafeOrCoffeeShop", "escape_rooms": "EntertainmentBusiness", "attractions": "TouristAttraction",
    })  # fmt: skip
    # HappyTLV's card tags worth showing (the rest describe the deal: "דיל חזק", "ימי חול בלבד").
    tags: list[str] = Field(default_factory=lambda: [
        "חברים", "דייט", "משפחה", "קוקטיילים", "בר יין", "קבוצה גדולה", "אינטימי", "אוכל איטלקי", "אוכל אסייתי",
        "מרחב מוגן", "בירה",
    ])  # fmt: skip
