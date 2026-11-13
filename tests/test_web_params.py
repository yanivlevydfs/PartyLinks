"""site.web (GET /site → web): the web's fallbacks in useWeb.ts equal WebSettings' defaults."""

import re
from pathlib import Path

from partylinks.config import WebSettings

SRC = Path(__file__).parents[1] / "web" / "src" / "site_params" / "useWeb.ts"


def test_web_defaults_match_settings() -> None:
    block = SRC.read_text(encoding="utf-8").split("WEB_DEFAULTS = {", 1)[1].split("};", 1)[0]
    web = {k: int(v) for k, v in re.findall(r"(\w+): (\d+),", block)}
    assert web == WebSettings().model_dump()
