from __future__ import annotations

from typing import Any, Dict, List

from evals.models import EvalAssertion, EvalCase, EvalMetrics, EvalResult


EXACT_KEYS = {
    "intent",
    "workflow",
    "needs_lookup",
    "expects_write",
    "needs_clarification",
    "status",
    "result_status",
    "trace_status",
    "result_kind",
    "op_ids_count",
    "tool_calls",
}


def score_case(case: EvalCase, actual: Dict[str, Any], metrics: EvalMetrics) -> EvalResult:
    assertions: List[EvalAssertion] = []
    expected = case.expected

    for key in EXACT_KEYS:
        if key not in expected:
            continue
        actual_value = actual.get(key)
        expected_value = expected[key]
        assertions.append(
            EvalAssertion(
                name=key,
                passed=actual_value == expected_value,
                expected=expected_value,
                actual=actual_value,
            )
        )

    if "confidence_in" in expected:
        actual_value = actual.get("confidence")
        allowed = list(expected["confidence_in"])
        assertions.append(
            EvalAssertion(
                name="confidence_in",
                passed=actual_value in allowed,
                expected=allowed,
                actual=actual_value,
            )
        )

    if "allowed_tools_include" in expected:
        actual_tools = list(actual.get("workflow_definition", {}).get("allowed_tools", []))
        required_tools = list(expected["allowed_tools_include"])
        missing = [tool for tool in required_tools if tool not in actual_tools]
        assertions.append(
            EvalAssertion(
                name="allowed_tools_include",
                passed=not missing,
                expected=required_tools,
                actual=actual_tools,
                details="" if not missing else f"Missing tools: {', '.join(missing)}",
            )
        )

    if "primary_tools_include" in expected:
        actual_tools = list(actual.get("workflow_definition", {}).get("primary_tools", []))
        required_tools = list(expected["primary_tools_include"])
        missing = [tool for tool in required_tools if tool not in actual_tools]
        assertions.append(
            EvalAssertion(
                name="primary_tools_include",
                passed=not missing,
                expected=required_tools,
                actual=actual_tools,
                details="" if not missing else f"Missing tools: {', '.join(missing)}",
            )
        )

    if "rationale_contains" in expected:
        actual_rationale = str(actual.get("rationale", ""))
        expected_snippet = str(expected["rationale_contains"])
        assertions.append(
            EvalAssertion(
                name="rationale_contains",
                passed=expected_snippet.lower() in actual_rationale.lower(),
                expected=expected_snippet,
                actual=actual_rationale,
            )
        )

    if "summary_contains" in expected:
        actual_summary = str(actual.get("summary", ""))
        expected_snippet = str(expected["summary_contains"])
        assertions.append(
            EvalAssertion(
                name="summary_contains",
                passed=expected_snippet.lower() in actual_summary.lower(),
                expected=expected_snippet,
                actual=actual_summary,
            )
        )

    if "summary_not_contains" in expected:
        actual_summary = str(actual.get("summary", ""))
        forbidden = str(expected["summary_not_contains"])
        assertions.append(
            EvalAssertion(
                name="summary_not_contains",
                passed=forbidden.lower() not in actual_summary.lower(),
                expected=forbidden,
                actual=actual_summary,
            )
        )

    if "trace_tools_exact" in expected:
        actual_tools = list(actual.get("trace_tools", []))
        expected_tools = list(expected["trace_tools_exact"])
        assertions.append(
            EvalAssertion(
                name="trace_tools_exact",
                passed=actual_tools == expected_tools,
                expected=expected_tools,
                actual=actual_tools,
            )
        )

    if "trace_tools_include" in expected:
        actual_tools = list(actual.get("trace_tools", []))
        required_tools = list(expected["trace_tools_include"])
        missing = [tool for tool in required_tools if tool not in actual_tools]
        assertions.append(
            EvalAssertion(
                name="trace_tools_include",
                passed=not missing,
                expected=required_tools,
                actual=actual_tools,
                details="" if not missing else f"Missing tools: {', '.join(missing)}",
            )
        )

    if "trace_kinds_exact" in expected:
        actual_kinds = list(actual.get("trace_kinds", []))
        expected_kinds = list(expected["trace_kinds_exact"])
        assertions.append(
            EvalAssertion(
                name="trace_kinds_exact",
                passed=actual_kinds == expected_kinds,
                expected=expected_kinds,
                actual=actual_kinds,
            )
        )

    if "used_tools_include" in expected:
        actual_tools = list(actual.get("used_tools", []))
        required_tools = list(expected["used_tools_include"])
        missing = [tool for tool in required_tools if tool not in actual_tools]
        assertions.append(
            EvalAssertion(
                name="used_tools_include",
                passed=not missing,
                expected=required_tools,
                actual=actual_tools,
                details="" if not missing else f"Missing tools: {', '.join(missing)}",
            )
        )

    passed = all(assertion.passed for assertion in assertions) if assertions else True
    return EvalResult(
        case=case,
        passed=passed,
        actual=actual,
        metrics=metrics,
        assertions=assertions,
    )
