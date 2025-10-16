# server/app/api/policies.py
"""
Soft rules memory (NOT hard constraints): the LLM saves durable preferences here.
The backend stays dumb about these; the model reads & applies them when planning.
"""

from __future__ import annotations
from typing import Optional, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

# Planned modules (implement later)
from app.auth.google_oauth import require_user
from app.models.user import User
from app.models.policy import Policy
from app.services.policy_store import PolicyStore  # DB-backed storage

router = APIRouter(prefix="/policies", tags=["policies"])


class PolicyCreate(BaseModel):
    text: str = Field(..., description="Natural-language rule")
    json: Optional[dict[str, Any]] = Field(None, description="Optional structured form")
    active: bool = True


class PolicyOut(BaseModel):
    id: str
    text: str
    json: Optional[dict[str, Any]] = None
    active: bool


class PolicyList(BaseModel):
    items: list[PolicyOut]


@router.post("/save", response_model=PolicyOut)
async def save_policy(
    body: PolicyCreate,
    user: User = Depends(require_user),
) -> PolicyOut:
    store = PolicyStore(user=user)
    p: Policy = await store.create(text=body.text, json=body.json, active=body.active)
    return PolicyOut(id=p.id, text=p.text, json=p.json, active=p.active)


@router.get("/list", response_model=PolicyList)
async def list_policies(
    user: User = Depends(require_user),
) -> PolicyList:
    store = PolicyStore(user=user)
    items: list[Policy] = await store.list_all()
    return PolicyList(items=[PolicyOut(id=p.id, text=p.text, json=p.json, active=p.active) for p in items])


@router.delete("/{policy_id}", response_model=PolicyOut)
async def delete_policy(
    policy_id: str,
    user: User = Depends(require_user),
) -> PolicyOut:
    store = PolicyStore(user=user)
    p: Policy = await store.delete(policy_id)
    if not p:
        raise HTTPException(status_code=404, detail="Policy not found")
    return PolicyOut(id=p.id, text=p.text, json=p.json, active=p.active)
