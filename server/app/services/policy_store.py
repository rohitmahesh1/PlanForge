# server/app/services/policy_store.py
from __future__ import annotations
from typing import Optional, Any, List

from sqlalchemy import select, delete

from app.models.base import get_session
from app.models.policy import Policy, PolicyORM
from app.models.user import User

class PolicyStore:
    def __init__(self, user: User):
        self.user = user

    async def create(self, *, text: str, json: Optional[dict[str, Any]], active: bool = True) -> Policy:
        async with get_session() as session:
            row = PolicyORM(user_id=self.user.id, text=text, json=json, active=active)
            session.add(row)
            await session.flush()
            return row.to_pyd()

    async def list_all(self) -> List[Policy]:
        async with get_session() as session:
            q = select(PolicyORM).where(PolicyORM.user_id == self.user.id).order_by(PolicyORM.created_at.desc())
            rows = (await session.execute(q)).scalars().all()
            return [r.to_pyd() for r in rows]

    async def delete(self, policy_id: str) -> Policy:
        async with get_session() as session:
            q = select(PolicyORM).where(PolicyORM.user_id == self.user.id, PolicyORM.id == policy_id)
            row = (await session.execute(q)).scalar_one_or_none()
            if not row:
                raise ValueError("Policy not found")
            pyd = row.to_pyd()
            await session.delete(row)
            await session.flush()
            return pyd
