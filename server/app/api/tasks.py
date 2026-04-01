# server/app/api/tasks.py
"""
Tasks as all-day events on a dedicated "Assistant Tasks" calendar.
"""

from __future__ import annotations
from datetime import date, datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

# Planned modules (implement later)
from app.auth.google_oauth import require_user
from app.models.user import User
from app.services.errors import to_http_exc
from app.services.tasks_service import TasksService

router = APIRouter(prefix="/tasks", tags=["tasks"])


class TaskAddRequest(BaseModel):
    title: str
    due: Optional[date] = Field(None, description="If omitted, default to today")
    estimate_min: Optional[int] = Field(None, ge=5)


class TaskItem(BaseModel):
    id: str
    title: str
    due: date
    status: str = Field(description="pending|done")
    event_id: str


class TaskAddResponse(BaseModel):
    op_id: str
    task: TaskItem


class TaskListResponse(BaseModel):
    tasks: list[TaskItem]


class TaskCompleteRequest(BaseModel):
    task_event_id: str


class TaskUpdateRequest(BaseModel):
    task_event_id: str
    title: Optional[str] = None
    due: Optional[date] = None
    estimate_min: Optional[int] = Field(None, ge=5)
    status: Optional[Literal["pending", "done"]] = None


class TaskDeleteRequest(BaseModel):
    task_event_id: str


class TaskDeleteResponse(BaseModel):
    op_id: str
    task_event_id: str


class TaskScheduleRequest(BaseModel):
    task_event_id: str
    start: datetime
    end: Optional[datetime] = None
    duration_min: Optional[int] = Field(None, ge=5)
    title: Optional[str] = None
    calendar_id: Optional[str] = None
    priority: Optional[Literal["high", "routine"]] = None


class TaskScheduleResponse(BaseModel):
    op_id: str
    task_event_id: str
    scheduled_event_id: str


@router.post("/add", response_model=TaskAddResponse)
async def add_task(
    body: TaskAddRequest,
    user: User = Depends(require_user),
) -> TaskAddResponse:
    try:
        svc = TasksService(user=user)
        op_id, task = await svc.add_task(
            title=body.title,
            due=body.due,
            estimate_min=body.estimate_min,
        )
        return TaskAddResponse(op_id=op_id, task=task)
    except Exception as err:
        raise to_http_exc(err)


@router.get("/list", response_model=TaskListResponse)
async def list_tasks(
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    user: User = Depends(require_user),
) -> TaskListResponse:
    try:
        svc = TasksService(user=user)
        items = await svc.list_tasks(from_date=from_date, to_date=to_date)
        return TaskListResponse(tasks=items)
    except Exception as err:
        raise to_http_exc(err)


@router.post("/complete", response_model=TaskAddResponse)
async def complete_task(
    body: TaskCompleteRequest,
    user: User = Depends(require_user),
) -> TaskAddResponse:
    try:
        svc = TasksService(user=user)
        op_id, task = await svc.complete_task(task_event_id=body.task_event_id)
        return TaskAddResponse(op_id=op_id, task=task)
    except Exception as err:
        raise to_http_exc(err)


@router.post("/update", response_model=TaskAddResponse)
async def update_task(
    body: TaskUpdateRequest,
    user: User = Depends(require_user),
) -> TaskAddResponse:
    try:
        svc = TasksService(user=user)
        op_id, task = await svc.update_task(
            task_event_id=body.task_event_id,
            title=body.title,
            due=body.due,
            estimate_min=body.estimate_min,
            status=body.status,
        )
        return TaskAddResponse(op_id=op_id, task=task)
    except Exception as err:
        raise to_http_exc(err)


@router.post("/delete", response_model=TaskDeleteResponse)
async def delete_task(
    body: TaskDeleteRequest,
    user: User = Depends(require_user),
) -> TaskDeleteResponse:
    try:
        svc = TasksService(user=user)
        op_id, task_event_id = await svc.delete_task(task_event_id=body.task_event_id)
        return TaskDeleteResponse(op_id=op_id, task_event_id=task_event_id)
    except Exception as err:
        raise to_http_exc(err)


@router.post("/schedule", response_model=TaskScheduleResponse)
async def schedule_task(
    body: TaskScheduleRequest,
    user: User = Depends(require_user),
) -> TaskScheduleResponse:
    try:
        svc = TasksService(user=user)
        op_id, scheduled_event_id = await svc.schedule_task(
            task_event_id=body.task_event_id,
            start=body.start,
            end=body.end,
            duration_min=body.duration_min,
            title=body.title,
            calendar_id=body.calendar_id,
            priority=body.priority,
        )
        return TaskScheduleResponse(
            op_id=op_id,
            task_event_id=body.task_event_id,
            scheduled_event_id=scheduled_event_id,
        )
    except Exception as err:
        raise to_http_exc(err)
