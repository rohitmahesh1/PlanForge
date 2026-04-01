from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(frozen=True)
class EvalCase:
    id: str
    suite: str
    adapter: str
    description: str
    input: Dict[str, Any]
    expected: Dict[str, Any]
    tags: List[str] = field(default_factory=list)


@dataclass
class EvalMetrics:
    latency_ms: int
    tokens_in: int = 0
    tokens_out: int = 0
    estimated_cost_usd: float = 0.0
    model: str = "offline"
    tool_calls: int = 0


@dataclass
class EvalAssertion:
    name: str
    passed: bool
    expected: Any
    actual: Any
    details: str = ""


@dataclass
class EvalResult:
    case: EvalCase
    passed: bool
    actual: Dict[str, Any]
    metrics: EvalMetrics
    assertions: List[EvalAssertion]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case": {
                "id": self.case.id,
                "suite": self.case.suite,
                "adapter": self.case.adapter,
                "description": self.case.description,
                "tags": self.case.tags,
            },
            "passed": self.passed,
            "actual": self.actual,
            "metrics": {
                "latency_ms": self.metrics.latency_ms,
                "tokens_in": self.metrics.tokens_in,
                "tokens_out": self.metrics.tokens_out,
                "estimated_cost_usd": self.metrics.estimated_cost_usd,
                "model": self.metrics.model,
                "tool_calls": self.metrics.tool_calls,
            },
            "assertions": [
                {
                    "name": assertion.name,
                    "passed": assertion.passed,
                    "expected": assertion.expected,
                    "actual": assertion.actual,
                    "details": assertion.details,
                }
                for assertion in self.assertions
            ],
        }
