"""Admin API: auth guard, overview, scheduler control, config editing, maintenance.

Mounted under ``/admin/api``. The React admin page lives at ``/admin`` (not linked from the site).
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import time
from datetime import datetime
from typing import Annotated, Any
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from partylinks import clicks, searches
from partylinks.config import Settings, config_path, get_settings, project_path
from partylinks.db import EventRow, ImageRow, ScrapeRunRow, TicketRow, upcoming_filter
from partylinks.logs import debug_enabled, set_debug
from partylinks.scheduler import RETENTION_JOB_ID, build_scheduler, next_run_times
from partylinks.scrapers import configured_sources
from partylinks.service import ScrapeService, SourceBusyError

log = logging.getLogger(__name__)

LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}
BACKUP_DIR = "config/backups"
KEEP_BACKUPS = 30
# Changing these only takes effect after the process restarts.
RESTART_KEYS = ("database", "api")


# --------------------------------------------------------------------------- auth
def require_admin(request: Request) -> None:
    """Bearer token when ``admin.token`` is set; otherwise only requests from this machine."""
    token = request.app.state.settings.admin.token
    if token is None:
        # A reverse proxy makes every request look local, so forwarded requests never count.
        forwarded = any(h in request.headers for h in ("x-forwarded-for", "x-real-ip", "forwarded"))
        if forwarded or request.client is None or request.client.host not in LOOPBACK:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "admin is localhost-only until PARTYLINKS_ADMIN__TOKEN is set"
            )
        return
    given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(given.encode(), token.get_secret_value().encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid admin token", {"WWW-Authenticate": "Bearer"})


AdminDep = Depends(require_admin)
router = APIRouter(prefix="/admin/api", tags=["admin"], dependencies=[AdminDep])


def _session(request: Request) -> Any:
    with request.app.state.db.session(write=False) as session:
        yield session


SessionDep = Annotated[Session, Depends(_session)]


# --------------------------------------------------------------------------- overview
@router.get("/overview")
def overview(request: Request, session: SessionDep) -> dict[str, Any]:
    cfg: Settings = request.app.state.settings
    scheduler: AsyncIOScheduler | None = request.app.state.scheduler
    upcoming = upcoming_filter()
    per_source = session.execute(select(EventRow.source, func.count()).group_by(EventRow.source)).all()
    upcoming_per_source = session.execute(
        select(EventRow.source, func.count()).where(upcoming).group_by(EventRow.source)
    ).all()
    images = session.execute(
        select(func.count(), func.count(ImageRow.downloaded_at), func.coalesce(func.sum(ImageRow.size_bytes), 0))
    ).one()
    return {
        "timezone": cfg.timezone,
        "server_time": datetime.now(ZoneInfo(cfg.timezone)),
        "events": {
            "total": session.scalar(select(func.count()).select_from(EventRow)) or 0,
            "upcoming": session.scalar(select(func.count()).select_from(EventRow).where(upcoming)) or 0,
            "upcoming_without_location": session.scalar(
                select(func.count()).select_from(EventRow).where(upcoming, EventRow.latitude.is_(None))
            )
            or 0,
            # Every configured source, at 0 until it saves an event (a new, failing or disabled one still shows).
            "per_source": {**dict.fromkeys(configured_sources(cfg), 0), **{s: n for s, n in per_source}},
            "upcoming_per_source": {s: n for s, n in upcoming_per_source},
        },
        "tickets": session.scalar(select(func.count()).select_from(TicketRow)) or 0,
        "images": {"total": images[0], "stored": images[1], "stored_bytes": images[2]},
        "runs": session.scalar(select(func.count()).select_from(ScrapeRunRow)) or 0,
        "storage": request.app.state.db.storage_stats(),
        "database": make_url(cfg.database.url).render_as_string(hide_password=True),
        "warnings": [
            *(
                []
                if cfg.site.accessibility.has_contact
                else [
                    "Accessibility statement has no coordinator contact (site.accessibility in Config) - required by law in Israel."
                ]
            ),
            *(
                []
                if cfg.site.privacy.email
                else [
                    "Privacy policy has no contact email (site.privacy.email in Config): visitors need a way to send privacy requests."
                ]
            ),
            *(
                ["site.base_url is still localhost: canonical links, sitemap and share previews point to it."]
                if "localhost" in cfg.site.base_url
                else []
            ),
            *([] if cfg.admin.token else ["No admin token set: admin works from this machine only."]),
        ],
        "retention": {**cfg.retention.model_dump(), "next_run_at": next_run_times(scheduler).get(RETENTION_JOB_ID)},
        "scheduler": {
            "enabled": scheduler is not None,
            "running": bool(scheduler and scheduler.state == 1),  # STATE_RUNNING
            "paused": bool(scheduler and scheduler.state == 2),  # STATE_PAUSED
            "jobs": [
                {"id": j.id, "name": j.name, "next_run_at": j.next_run_time, "paused": j.next_run_time is None}
                for j in (scheduler.get_jobs() if scheduler else [])
            ],
        },
    }


# --------------------------------------------------------------------------- clicks
@router.get("/clicks")
def click_stats(request: Request, session: SessionDep, days: int = Query(30, ge=1, le=365)) -> dict[str, Any]:
    """Ticket-link clicks counted by GET /go/... (see partylinks.clicks)."""
    return clicks.stats(session, request.app.state.settings.timezone, days)


# --------------------------------------------------------------------------- searches
@router.get("/searches")
def search_stats(request: Request, session: SessionDep, days: int = Query(30, ge=1, le=365)) -> dict[str, Any]:
    """Searches on the site counted by GET /events?q=... (see partylinks.searches)."""
    return searches.stats(session, request.app.state.settings.timezone, days)


# --------------------------------------------------------------------------- bans
@router.get("/bans")
def bans(request: Request) -> dict[str, float]:
    """Banned addresses and the minutes left on each (security middleware, this process)."""
    guard = getattr(request.app.state, "guard", None)
    now = time.monotonic()
    return {ip: round((until - now) / 60, 1) for ip, until in (guard.banned.items() if guard else ()) if until > now}


@router.post("/bans/lift")
def lift_bans(request: Request, ip: str | None = None) -> dict[str, list[str]]:
    """Lift the ban of ``ip`` (every ban when left out): the owner locked out by their own checks.
    Call it from an address that is not banned, or from inside the server (localhost is never banned)."""
    guard = getattr(request.app.state, "guard", None)
    lifted = guard.pardon(ip) if guard else []
    log.warning("bans lifted by admin: %s", ", ".join(lifted) or "none")
    return {"lifted": lifted}


# --------------------------------------------------------------------------- scheduler
def _scheduler(request: Request) -> AsyncIOScheduler:
    scheduler = request.app.state.scheduler
    if scheduler is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "scheduler is disabled (scheduler.enabled: false)")
    return scheduler


@router.post("/scheduler/{action}")
def scheduler_control(action: str, request: Request) -> dict[str, str]:
    """``pause`` / ``resume`` every job (runtime only; the YAML decides what runs after a restart)."""
    scheduler = _scheduler(request)
    if action == "pause":
        scheduler.pause()
    elif action == "resume":
        scheduler.resume()
    else:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "action must be pause or resume")
    return {"status": action}


class ScanIn(BaseModel):
    sources: list[str] | None = None  # None = every enabled source


@router.post("/scan")
async def scan(request: Request, body: ScanIn | None = None) -> dict[str, list[str]]:
    """Start the chosen sources now, or every enabled one (works with the scheduler off).
    Sources already running are skipped and reported as busy."""
    service: ScrapeService = request.app.state.service
    configured = configured_sources(request.app.state.settings)
    chosen = body.sources if body and body.sources else [n for n, s in configured.items() if s.enabled]
    if unknown := [n for n in chosen if n not in configured]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown sources: {', '.join(unknown)}")
    started, busy = [], []
    for name in chosen:
        try:
            future = service.start_source(name, trigger="admin")
        except SourceBusyError:
            busy.append(name)
            continue
        started.append(name)
        task = asyncio.create_task(_watch(name, asyncio.wrap_future(future)))
        request.app.state.tasks.add(task)
        task.add_done_callback(request.app.state.tasks.discard)
    return {"started": started, "busy": busy}


async def _watch(name: str, run: Any) -> None:
    try:
        await run
    except Exception:
        log.exception("admin full scan: %s failed", name)


class DebugIn(BaseModel):
    on: bool


@router.get("/logging")
def logging_state() -> dict[str, bool]:
    return {"debug": debug_enabled()}


@router.post("/logging")
def logging_switch(body: DebugIn) -> dict[str, bool]:
    """Debug logging on/off for this process, until restart (logging.debug in the YAML decides then)."""
    set_debug(body.on)
    log.warning("debug logging switched %s from admin", "on" if body.on else "off")
    return {"debug": debug_enabled()}


@router.post("/jobs/{job_id}/{action}")
def job_control(job_id: str, action: str, request: Request) -> dict[str, Any]:
    """Pause / resume one source (or ``retention``) until the next restart or config apply."""
    scheduler = _scheduler(request)
    if scheduler.get_job(job_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no scheduled job {job_id!r} (disabled in config?)")
    if action == "pause":
        job = scheduler.pause_job(job_id)
    elif action == "resume":
        job = scheduler.resume_job(job_id)
    else:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "action must be pause or resume")
    return {"id": job_id, "next_run_at": job.next_run_time if job else None}


# --------------------------------------------------------------------------- config
class ConfigIn(BaseModel):
    yaml: str


PROXY_KEYS = ("proxy", "source_proxy", "israel_proxy", "israel_proxy_feed")


def _hide_url(value: str) -> str:
    """Proxy URLs (comma-separated pools too) keep their host; credentials and query (keys) are hidden."""
    parts = [urlsplit(u.strip()) for u in value.split(",")]
    return ",".join(
        urlunsplit(p._replace(netloc="••••@" + p.netloc.rsplit("@", 1)[1] if "@" in p.netloc else p.netloc,
                              query="••••" if p.query else ""))  # fmt: skip
        for p in parts
    )


def _mask(settings: Settings) -> dict[str, Any]:
    """Effective settings (YAML + environment) with secrets hidden."""
    data = settings.model_dump(mode="json", exclude={"admin"})
    data["database"]["url"] = make_url(settings.database.url).render_as_string(hide_password=True)
    blocks = [data["http"], *(s.get("http", {}) for s in data["sources"].values() if isinstance(s, dict))]
    for http in blocks:
        if not isinstance(http, dict):
            continue
        if http.get("headers"):
            http["headers"] = {k: "••••" for k in http["headers"]}
        for key in PROXY_KEYS:
            if http.get(key):
                http[key] = _hide_url(http[key])
    data["admin"] = {"token_set": settings.admin.token is not None}
    return data


def validate_config(text: str) -> tuple[Settings | None, list[str]]:
    """Parse and validate settings YAML the same way the app loads it (minus env overrides)."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return None, [f"YAML: {exc}"]
    if not isinstance(data, dict):
        return None, ["the file must be a YAML mapping"]
    try:
        settings = Settings.model_validate(data)
        configured_sources(settings)  # every source block against its own model
    except ValidationError as exc:
        return None, [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
    errors: list[str] = []
    try:
        ZoneInfo(settings.timezone)
    except Exception:
        errors.append(f"timezone: unknown zone {settings.timezone!r}")
    crons = {"retention.cron": settings.retention.cron} | {
        f"sources.{n}.cron": s.cron for n, s in configured_sources(settings).items()
    }
    for key, expr in crons.items():
        try:
            CronTrigger.from_crontab(expr)
        except ValueError as exc:
            errors.append(f"{key}: {exc}")
    return (None if errors else settings), errors


@router.get("/config")
def get_config(request: Request) -> dict[str, Any]:
    path = config_path()
    return {
        "path": str(path),
        "yaml": path.read_text(encoding="utf-8"),
        "effective": _mask(request.app.state.settings),
        "backups": sorted((p.name for p in project_path(BACKUP_DIR).glob("*.yaml")), reverse=True),
    }


@router.get("/config/backups/{name}")
def get_backup(name: str) -> dict[str, str]:
    path = project_path(BACKUP_DIR) / name
    if "/" in name or "\\" in name or ".." in name or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "backup not found")
    return {"name": name, "yaml": path.read_text(encoding="utf-8")}


@router.post("/config/validate")
def post_validate(body: ConfigIn) -> dict[str, Any]:
    _, errors = validate_config(body.yaml)
    return {"ok": not errors, "errors": errors}


@router.put("/config")
async def put_config(body: ConfigIn, request: Request) -> dict[str, Any]:
    """Validate, back up the current file, save, then apply without a restart when possible."""
    _, errors = validate_config(body.yaml)
    if errors:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, {"errors": errors})
    path = config_path()
    backups = project_path(BACKUP_DIR)
    backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(ZoneInfo(request.app.state.settings.timezone)).strftime("%Y%m%d-%H%M%S")
    (backups / f"settings-{stamp}.yaml").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    for old in sorted(backups.glob("settings-*.yaml"))[:-KEEP_BACKUPS]:
        old.unlink()
    path.write_text(body.yaml.replace("\r\n", "\n"), encoding="utf-8")
    log.info("admin: saved %s (backup settings-%s.yaml)", path, stamp)
    return {"saved": True, "backup": f"settings-{stamp}.yaml", **(await apply_config(request))}


@router.post("/config/apply")
async def apply_config(request: Request) -> dict[str, Any]:
    """Reload settings from disk + environment and rebuild the scrape service and scheduler."""
    app: FastAPI = request.app
    old: ScrapeService = app.state.service
    running = [name for name in configured_sources(app.state.settings) if old.is_running(name)]
    if running:
        return {"applied": False, "reason": f"waiting for running sources: {', '.join(running)}; apply again later"}
    get_settings.cache_clear()
    settings = get_settings()
    restart = [k for k in RESTART_KEYS if getattr(settings, k) != getattr(app.state.settings, k)]
    service = ScrapeService(settings, app.state.db)
    service.recompute_areas()
    if app.state.scheduler:
        app.state.scheduler.shutdown(wait=False)
    scheduler = build_scheduler(settings, service) if settings.scheduler.enabled else None
    if scheduler:
        scheduler.start()
    app.state.settings, app.state.service, app.state.scheduler = settings, service, scheduler
    old.shutdown()
    log.info("admin: config applied%s", f" (restart needed for {restart})" if restart else "")
    return {"applied": True, "restart_required": restart}


# --------------------------------------------------------------------------- maintenance
@router.post("/maintenance/vacuum")
async def vacuum(request: Request, full: bool = False) -> dict[str, Any]:
    """Give deleted space back to the OS. ``full`` rewrites the whole file (slow, needs a quiet DB)."""
    db = request.app.state.db
    freed = await asyncio.to_thread(db.reclaim_space, full)
    return {"freed_bytes": freed, "storage": db.storage_stats()}


@router.post("/maintenance/orphans")
async def sweep_orphans(request: Request, dry_run: bool = True) -> dict[str, Any]:
    """Artwork files no image row claims (originals or WebP copies). ``dry_run`` (default) only
    counts them; ``dry_run=false`` deletes them. Retention runs the same sweep after each pass."""
    return await asyncio.to_thread(request.app.state.service.media.sweep_orphans, dry_run)


@router.post("/maintenance/geocode")
async def geocode(request: Request) -> dict[str, int]:
    """Find map coordinates for upcoming events that only have an address."""
    return await asyncio.to_thread(request.app.state.service.geocode)


@router.post("/maintenance/areas")
async def recompute_areas(request: Request) -> dict[str, int]:
    """Re-derive every stored event's area from the configured areas."""
    return {"updated": await asyncio.to_thread(request.app.state.service.recompute_areas)}


@router.post("/maintenance/regroup")
async def regroup(request: Request) -> dict[str, int]:
    """Recompute duplicate-event groups across all sources."""
    return {"merged_groups": await asyncio.to_thread(request.app.state.service.regroup)}
