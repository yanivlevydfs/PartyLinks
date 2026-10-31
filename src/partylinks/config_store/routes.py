"""Admin API for the overrides: /admin/api/config/overrides (read, validate, save, reset a key, versions)."""

from __future__ import annotations

from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from partylinks.admin import AdminDep, apply_config, validate_config
from partylinks.config_store import store

router = APIRouter(prefix="/admin/api/config/overrides", tags=["admin"], dependencies=[AdminDep])

# Not from the database: the connection itself (needs a restart anyway) and the admin login.
LOCKED = ("database", "admin")


class OverridesIn(BaseModel):
    yaml: str
    note: str | None = None


def _parse(text: str) -> tuple[dict[str, Any], list[str]]:
    """The overrides YAML -> (data, errors), checked as the app would load file + overrides."""
    try:
        data = yaml.safe_load(text) if text.strip() else {}
    except yaml.YAMLError as exc:
        return {}, [f"YAML: {exc}"]
    if not isinstance(data, dict):
        return {}, ["the overrides must be a YAML mapping (key: value)"]
    if locked := [k for k in LOCKED if k in data]:
        return {}, [f"{k}: set in the environment or settings.yaml, not here" for k in locked]
    unknown = [k for k in data if k not in store.Settings.model_fields]
    if unknown:
        return {}, [f"{k}: no such setting" for k in unknown]
    _, errors = validate_config(yaml.safe_dump(store.merge(store.file_data(), data), allow_unicode=True))
    return data, errors


def _view(request: Request) -> dict[str, Any]:
    data = store.latest(request.app.state.db)
    base = store.file_data()
    return {
        "yaml": yaml.safe_dump(data, allow_unicode=True, sort_keys=False) if data else "",
        "keys": [{"key": k, "value": v, "file_value": store.lookup(base, k)} for k, v in store.flatten(data).items()],
        "in_force": store.in_force() == data,
        "versions": store.versions(request.app.state.db),
    }


@router.get("")
def get_overrides(request: Request) -> dict[str, Any]:
    return _view(request)


@router.post("/validate")
def validate_overrides(body: OverridesIn) -> dict[str, Any]:
    _, errors = _parse(body.yaml)
    return {"ok": not errors, "errors": errors}


async def _save(request: Request, data: dict[str, Any], note: str | None) -> dict[str, Any]:
    version_id = store.save(request.app.state.db, data, note)
    return {"saved": True, "version": version_id, **(await apply_config(request)), **_view(request)}


@router.put("")
async def put_overrides(body: OverridesIn, request: Request) -> dict[str, Any]:
    """Validate, store as a new version in the database, then apply in both processes."""
    data, errors = _parse(body.yaml)
    if errors:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, {"errors": errors})
    return await _save(request, data, body.note)


@router.delete("/key/{path:path}")
async def reset_key(path: str, request: Request) -> dict[str, Any]:
    """Back to the file's value for one dotted key (e.g. retention.assumed_duration_hours)."""
    data = store.latest(request.app.state.db)
    if path not in store.flatten(data):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{path} is not overridden")
    return await _save(request, store.without(data, path), f"reset {path}")


@router.get("/versions/{version_id}")
def get_version(version_id: int, request: Request) -> dict[str, Any]:
    data = store.version(request.app.state.db, version_id)
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such version")
    return {"id": version_id, "yaml": yaml.safe_dump(data, allow_unicode=True, sort_keys=False) if data else ""}
