from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from app.models.prefs import Prefs
from app.models.policy import Policy
from app.models.user import User
from app.services.freebusy import FreeBusyService
from app.services.gcal import GCalClient
from app.services.policy_store import PolicyStore


@dataclass
class IngressContext:
    prefs: Prefs
    policies: List[Policy]
    freebusy_snapshot: Dict[str, Any]


class IngressContextService:
    """
    Build the shared scheduling context passed into the LLM entrypoints.
    """

    def __init__(self, user: User):
        self.user = user

    async def build(self, *, hours_ahead: int = 36) -> IngressContext:
        gcal = GCalClient(user=self.user)
        prefs = await gcal.get_prefs()
        policies = await PolicyStore(user=self.user).list_all()
        freebusy_snapshot = await FreeBusyService(gcal=gcal, prefs=prefs).snapshot(
            hours_ahead=hours_ahead
        )
        return IngressContext(
            prefs=prefs,
            policies=policies,
            freebusy_snapshot=freebusy_snapshot,
        )
