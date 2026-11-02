"""settings.yaml has no duplicate keys: YAML keeps the last one silently (munievents had two `http:`
blocks, so the 429 fix's concurrency 3 never took effect, Nov 2026)."""

from __future__ import annotations

from pathlib import Path

import yaml

SETTINGS = Path(__file__).parent.parent / "config" / "settings.yaml"


def test_no_duplicate_keys() -> None:
    duplicates: list[str] = []

    class Loader(yaml.SafeLoader):
        pass

    def mapping(loader: Loader, node: yaml.MappingNode, deep: bool = False) -> dict:
        keys = [loader.construct_object(k) for k, _ in node.value]
        duplicates.extend(f"{k} (line {node.start_mark.line + 1})" for k in set(keys) if keys.count(k) > 1)
        return loader.construct_mapping(node, deep)

    Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    yaml.load(SETTINGS.read_text(encoding="utf-8"), Loader)
    assert duplicates == []
