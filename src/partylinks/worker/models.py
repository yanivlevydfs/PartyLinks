"""Tables linking the web process and the worker (see the package docstring)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from partylinks.db import Base, NullableJSON, UTCDateTime, utcnow

STATE_ID = 1  # worker_state holds one row


class WorkerStateRow(Base):
    """What the worker is doing, rewritten every heartbeat."""

    __tablename__ = "worker_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pid: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    heartbeat_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    scheduler: Mapped[str] = mapped_column(String(16))  # running | paused | stopped
    # [{"id", "name", "next_run_at" (ISO or None), "paused"}] of every scheduled job.
    jobs: Mapped[list[dict[str, Any]] | None] = mapped_column(NullableJSON)
    # {"last_run_at", "last_success_at", "last_removed", "last_error"} of the last retention pass.
    retention: Mapped[dict[str, Any] | None] = mapped_column(NullableJSON)
    # The worker's committed writes so far: when it moves, the web's cached pages are stale.
    changes: Mapped[int] = mapped_column(BigInteger, default=0)


class WorkerCommandRow(Base):
    """One admin request to the worker; the worker sets ``done_at`` and ``result``."""

    __tablename__ = "worker_commands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    # pause | resume | pause_job | resume_job | scan | reload
    command: Mapped[str] = mapped_column(String(32))
    args: Mapped[dict[str, Any] | None] = mapped_column(NullableJSON)
    done_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(NullableJSON)
