"""The overrides as a pydantic-settings source (imported by partylinks.config; no database import here)."""

from __future__ import annotations

from typing import Any

from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

# The overrides in force in this process, set by config_store.store.load (empty until then: file only).
_active: dict[str, Any] = {}


def active() -> dict[str, Any]:
    return _active


def set_active(data: dict[str, Any]) -> None:
    global _active
    _active = data


class OverridesSource(PydanticBaseSettingsSource):
    """Returns the overrides as top-level fields; pydantic-settings deep-merges them over the YAML file."""

    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(settings_cls)

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return _active.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return {k: v for k, v in _active.items() if k in self.settings_cls.model_fields}
