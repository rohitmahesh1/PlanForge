from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List, Optional

from evals.models import EvalCase


def load_cases(root: Path, *, suite: Optional[str] = None) -> List[EvalCase]:
    cases: List[EvalCase] = []
    for path in sorted(root.rglob("*.json")):
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        case = EvalCase(
            id=str(payload["id"]),
            suite=str(payload["suite"]),
            adapter=str(payload["adapter"]),
            description=str(payload["description"]),
            input=dict(payload.get("input", {})),
            expected=dict(payload.get("expected", {})),
            tags=[str(tag) for tag in payload.get("tags", [])],
        )
        if suite and case.suite != suite:
            continue
        cases.append(case)
    return cases


def iter_case_ids(cases: Iterable[EvalCase]) -> List[str]:
    return [case.id for case in cases]
