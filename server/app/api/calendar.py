# server/app/api/calendar.py
"""
Calendar CRUD & utilities used by the LLM tools:
- freebusy, create, update, move, delete
- reorg_today (slept-in shift/trim/push on routine blocks)
"""

from __future__ import annotations
from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

# Planned modules (implement later)
from app.auth.google_oauth import require_user
from app.models.user import User
from app.models.changelog import ChangeLogEntry
from app.models.prefs import Prefs
from app.services.calendar_projection import detail_event, summarize_event
from app.services.gcal import GCalClient
from app.services.freebusy import FreeBusyService
from app.services.undo import ChangeLogger
from app.services.reorg import ReorgService
from app.utils import utcnow

router = APIRouter(prefix="/calendar", tags=["calendar"])


# ------------ Schemas ------------

class FreeBusyRequest(BaseModel):
    start: datetime
    end: datetime


class FreeWindow(BaseModel):
    start: datetime
    end: datetime


class BusyWindow(BaseModel):
    start: datetime
    end: datetime
    event_id: Optional[str] = None


class FreeBusyResponse(BaseModel):
    free_windows: list[FreeWindow]
    busy_windows: list[BusyWindow]


class EventSummary(BaseModel):
    id: str
    title: str = ""
    start: Optional[str] = None
    end: Optional[str] = None
    all_day: bool = False
    location: Optional[str] = None
    attendees: list[str] = Field(default_factory=list)
    priority: Optional[str] = None
    status: Optional[str] = None


class EventDetail(EventSummary):
    notes: Optional[str] = None
    calendar_id: Optional[str] = None
    html_link: Optional[str] = None


class EventListRequest(BaseModel):
    start: datetime
    end: datetime
    calendar_id: Optional[str] = None
    limit: int = Field(default=20, ge=1, le=100)


class EventSearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    calendar_id: Optional[str] = None
    limit: int = Field(default=10, ge=1, le=100)


class EventListResponse(BaseModel):
    events: list[EventSummary]


class EventGetRequest(BaseModel):
    event_id: str
    calendar_id: Optional[str] = None


class EventCreateRequest(BaseModel):
    title: str
    start: datetime
    end: datetime
    attendees: Optional[list[str]] = None
    location: Optional[str] = None
    notes: Optional[str] = None
    calendar_id: Optional[str] = None
    priority: Optional[str] = Field(
        None, description="LLM may tag 'high' or 'routine' to aid reorg logic"
    )


class EventUpdateRequest(BaseModel):
    event_id: str
    patch: dict[str, Any] = Field(
        ..., description="Partial Google Event body (title, start/end, notes, etc.)"
    )


class EventMoveRequest(BaseModel):
    event_id: str
    new_start: datetime
    new_end: datetime


class EventDeleteRequest(BaseModel):
    event_id: str


class OpResult(BaseModel):
    op_id: str
    event_id: Optional[str] = None


class ReorgTodayRequest(BaseModel):
    now: datetime
    delay_min: int = Field(..., ge=1, description="How many minutes the user overslept")


class ReorgTodayResult(BaseModel):
    moved: list[str] = []
    trimmed: list[str] = []
    pushed: list[str] = []
    op_ids: list[str] = []


# ------------ Endpoints ------------

@router.post("/freebusy", response_model=FreeBusyResponse)
async def freebusy(
    body: FreeBusyRequest,
    user: User = Depends(require_user),
) -> FreeBusyResponse:
    gcal = GCalClient(user=user)
    prefs: Prefs = await gcal.get_prefs()  # or DB
    fb = FreeBusyService(gcal=gcal, prefs=prefs)
    free, busy = await fb.query(body.start, body.end)
    return FreeBusyResponse(free_windows=free, busy_windows=busy)


@router.post("/list", response_model=EventListResponse)
async def list_events(
    body: EventListRequest,
    user: User = Depends(require_user),
) -> EventListResponse:
    gcal = GCalClient(user=user)
    events = await gcal.list_events(
        body.start,
        body.end,
        calendar_id=body.calendar_id,
        max_results=body.limit,
    )
    return EventListResponse(events=_event_summaries(events))


@router.post("/search", response_model=EventListResponse)
async def search_events(
    body: EventSearchRequest,
    user: User = Depends(require_user),
) -> EventListResponse:
    gcal = GCalClient(user=user)
    now = utcnow()
    start = body.start or (now - timedelta(days=30))
    end = body.end or (now + timedelta(days=90))
    events = await gcal.search_events(
        query=body.query,
        start=start,
        end=end,
        calendar_id=body.calendar_id,
        max_results=body.limit,
    )
    return EventListResponse(events=_event_summaries(events))


@router.post("/get", response_model=EventDetail)
async def get_event(
    body: EventGetRequest,
    user: User = Depends(require_user),
) -> EventDetail:
    gcal = GCalClient(user=user)
    event = await gcal.get_event(body.event_id, calendar_id=body.calendar_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return EventDetail(**detail_event(event))


@router.post("/create", response_model=OpResult)
async def create_event(
    body: EventCreateRequest,
    user: User = Depends(require_user),
) -> OpResult:
    gcal = GCalClient(user=user)
    logger = ChangeLogger(user=user)
    before = None  # creating from scratch
    event = await gcal.create_event(
        title=body.title,
        start=body.start,
        end=body.end,
        attendees=body.attendees,
        location=body.location,
        notes=body.notes,
        calendar_id=body.calendar_id,
        priority=body.priority,
    )
    entry: ChangeLogEntry = await logger.record_create(after_json=event)
    return OpResult(op_id=entry.op_id, event_id=event.get("id"))


@router.post("/update", response_model=OpResult)
async def update_event(
    body: EventUpdateRequest,
    user: User = Depends(require_user),
) -> OpResult:
    gcal = GCalClient(user=user)
    logger = ChangeLogger(user=user)

    current = await gcal.get_event(body.event_id)
    if not current:
        raise HTTPException(status_code=404, detail="Event not found")

    updated = await gcal.update_event(event_id=body.event_id, patch=body.patch)
    entry: ChangeLogEntry = await logger.record_update(
        event_id=body.event_id, before_json=current, after_json=updated
    )
    return OpResult(op_id=entry.op_id, event_id=body.event_id)


@router.post("/move", response_model=OpResult)
async def move_event(
    body: EventMoveRequest,
    user: User = Depends(require_user),
) -> OpResult:
    gcal = GCalClient(user=user)
    logger = ChangeLogger(user=user)

    current = await gcal.get_event(body.event_id)
    if not current:
        raise HTTPException(status_code=404, detail="Event not found")

    updated = await gcal.update_event(
        event_id=body.event_id, patch={"start": body.new_start, "end": body.new_end}
    )
    entry: ChangeLogEntry = await logger.record_update(
        event_id=body.event_id, before_json=current, after_json=updated
    )
    return OpResult(op_id=entry.op_id, event_id=body.event_id)


@router.post("/delete", response_model=OpResult)
async def delete_event(
    body: EventDeleteRequest,
    user: User = Depends(require_user),
) -> OpResult:
    gcal = GCalClient(user=user)
    logger = ChangeLogger(user=user)

    current = await gcal.get_event(body.event_id)
    if not current:
        raise HTTPException(status_code=404, detail="Event not found")

    await gcal.delete_event(body.event_id)
    entry: ChangeLogEntry = await logger.record_delete(
        event_id=body.event_id, before_json=current
    )
    return OpResult(op_id=entry.op_id, event_id=body.event_id)


@router.post("/reorg_today", response_model=ReorgTodayResult)
async def reorg_today(
    body: ReorgTodayRequest,
    user: User = Depends(require_user),
) -> ReorgTodayResult:
    gcal = GCalClient(user=user)
    prefs: Prefs = await gcal.get_prefs()
    reorg = ReorgService(gcal=gcal, prefs=prefs)
    plan = await reorg.shift_day(now=body.now, delay_min=body.delay_min)

    # `plan` should carry which events were moved/trimmed/pushed and the created op_ids
    return ReorgTodayResult(
        moved=plan.moved_ids, trimmed=plan.trimmed_ids, pushed=plan.pushed_ids, op_ids=plan.op_ids
    )


def _event_summaries(items: list[dict[str, Any]]) -> list[EventSummary]:
    return [EventSummary(**summarize_event(item)) for item in items]
