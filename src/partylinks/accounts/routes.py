"""The signed-in user's own account: read, edit, sign out, delete."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from partylinks.accounts import sessions
from partylinks.accounts.guard import SameOrigin, limited
from partylinks.accounts.models import Gender, UserRow
from partylinks.accounts.service import delete_user
from partylinks.accounts.sessions import UserDep
from partylinks.db import utcnow

router = APIRouter(prefix="/api", tags=["accounts"], include_in_schema=False)
NO_STORE = {"Cache-Control": "private, no-store"}
WRITE = [SameOrigin, limited("me", 30)]


class MeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    email: str
    picture: str | None
    locale: str
    role: str
    digest_opt_in: bool
    gender: Gender | None
    created_at: datetime


class MePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    locale: Literal["he", "en"] | None = None
    digest_opt_in: bool | None = None
    gender: Gender | None = None


@router.get("/me", response_model=MeOut)
def me(user: UserDep, response: Response) -> MeOut:
    response.headers.update(NO_STORE)
    return MeOut.model_validate(user)


@router.patch("/me", response_model=MeOut, dependencies=WRITE)
def edit_me(body: MePatch, user: UserDep, request: Request, response: Response) -> MeOut:
    with request.app.state.db.session() as session:
        row = session.get(UserRow, user.id)
        assert row is not None  # current_user just read it
        if body.name is not None and body.name.strip():
            row.name = body.name.strip()
        if body.locale is not None:
            row.locale = body.locale
        if body.gender is not None:
            row.gender = body.gender
        if body.digest_opt_in is not None and body.digest_opt_in != row.digest_opt_in:
            row.digest_opt_in = body.digest_opt_in
            row.digest_opt_in_at = utcnow() if body.digest_opt_in else None  # proof of consent (§30A)
        out = MeOut.model_validate(row)
    response.headers.update(NO_STORE)
    return out


@router.post("/auth/logout", status_code=204, dependencies=WRITE)
def logout(request: Request) -> Response:
    response = Response(status_code=204)
    with request.app.state.db.session() as session:
        sessions.end(session, request, response)
    return response


@router.delete("/me", status_code=204, dependencies=WRITE)
def delete_me(user: UserDep, request: Request) -> Response:
    response = Response(status_code=204)
    with request.app.state.db.session() as session:
        sessions.end(session, request, response)
        delete_user(session, user.id)
    return response
