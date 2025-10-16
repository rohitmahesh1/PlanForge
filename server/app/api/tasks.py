# server/app/api/tasks.py
"""
Tasks as all-day events on a dedicated "Assistant Tasks" calendar.
"""

from __future__ import annotations
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

# Planned modules (implement later)
from app.auth.google_oauth import require_user
from app.models.user import User
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


@router.post("/add", response_model=TaskAddResponse)
async def add_task(
    body: TaskAddRequest,
    user: User = Depends(require_user),
) -> TaskAddResponse:
    svc = TasksService(user=user)
    op_id, task = await svc.add_task(
        title=body.title,
        due=body.due,
        estimate_min=body.estimate_min,
    )
    return TaskAddResponse(op_id=op_id, task=task)


@router.get("/list", response_model=TaskListResponse)
async def list_tasks(
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    user: User = Depends(require_user),
) -> TaskListResponse:
    svc = TasksService(user=user)
    items = await svc.list_tasks(from_date=from_date, to_date=to_date)
    return TaskListResponse(tasks=items)


@router.post("/complete", response_model=TaskAddResponse)
async def complete_task(
    body: TaskCompleteRequest,
    user: User = Depends(require_user),
) -> TaskAddResponse:
    svc = TasksService(user=user)
    op_id, task = await svc.complete_task(task_event_id=body.task_event_id)
    return TaskAddResponse(op_id=op_id, task=task)
