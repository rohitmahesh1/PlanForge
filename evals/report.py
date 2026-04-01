from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from evals.models import EvalResult


def build_markdown_report(results: List[EvalResult]) -> str:
    total = len(results)
    passed = sum(1 for result in results if result.passed)
    failed = total - passed
    avg_latency = round(
        sum(result.metrics.latency_ms for result in results) / total,
        1,
    ) if total else 0.0
    total_cost = sum(result.metrics.estimated_cost_usd for result in results)

    lines = [
        "# Eval Report",
        "",
        f"- Cases: {total}",
        f"- Passed: {passed}",
        f"- Failed: {failed}",
        f"- Avg latency: {avg_latency} ms",
        f"- Estimated cost: ${total_cost:.6f}",
        "",
        "## Modes",
        "",
        "| Mode | Cases | Passed | Failed | Avg latency (ms) | Estimated cost |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for mode_name, summary in sorted(_mode_summary(results).items()):
        lines.append(
            f"| `{mode_name}` | {summary['cases']} | {summary['passed']} | {summary['failed']} | "
            f"{summary['avg_latency_ms']} | ${summary['estimated_cost_usd']:.6f} |"
        )

    lines.extend(
        [
            "",
        "## Suites",
        "",
        "| Suite | Cases | Passed | Failed | Avg latency (ms) | Estimated cost |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for suite_name, summary in sorted(_suite_summary(results).items()):
        lines.append(
            f"| `{suite_name}` | {summary['cases']} | {summary['passed']} | {summary['failed']} | "
            f"{summary['avg_latency_ms']} | ${summary['estimated_cost_usd']:.6f} |"
        )

    lines.extend(
        [
            "",
            "## Cases",
            "",
        "| Case | Suite | Status | Latency (ms) | Model | Notes |",
        "| --- | --- | --- | ---: | --- | --- |",
        ]
    )
    for result in results:
        note = _result_note(result)
        status = "pass" if result.passed else "fail"
        lines.append(
            f"| `{result.case.id}` | `{result.case.suite}` | `{status}` | "
            f"{result.metrics.latency_ms} | `{result.metrics.model}` | {note} |"
        )
    return "\n".join(lines)


def write_report_artifacts(report_dir: Path, results: List[EvalResult]) -> Dict[str, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "generated_at": timestamp,
        "summary": {
            "cases": len(results),
            "passed": sum(1 for result in results if result.passed),
            "failed": sum(1 for result in results if not result.passed),
            "mode_summary": _mode_summary(results),
            "suite_summary": _suite_summary(results),
        },
        "results": [result.to_dict() for result in results],
    }
    json_path = report_dir / f"report-{timestamp}.json"
    latest_json = report_dir / "latest.json"
    md_path = report_dir / f"report-{timestamp}.md"
    latest_md = report_dir / "latest.md"

    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    latest_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    markdown = build_markdown_report(results) + "\n"
    md_path.write_text(markdown, encoding="utf-8")
    latest_md.write_text(markdown, encoding="utf-8")

    return {
        "json": json_path,
        "latest_json": latest_json,
        "markdown": md_path,
        "latest_markdown": latest_md,
    }


def _result_note(result: EvalResult) -> str:
    if result.passed:
        return "ok"
    failed = [assertion for assertion in result.assertions if not assertion.passed]
    if not failed:
        return "unexpected failure"
    first = failed[0]
    return f"`{first.name}` expected `{first.expected}` got `{first.actual}`"


def _suite_summary(results: List[EvalResult]) -> Dict[str, Dict[str, float]]:
    return _group_summary(results, key_fn=lambda result: result.case.suite)


def _mode_summary(results: List[EvalResult]) -> Dict[str, Dict[str, float]]:
    return _group_summary(results, key_fn=lambda result: result.case.mode)


def _group_summary(results: List[EvalResult], key_fn) -> Dict[str, Dict[str, float]]:
    summary: Dict[str, Dict[str, float]] = {}
    for result in results:
        group_name = key_fn(result)
        group_summary = summary.setdefault(
            group_name,
            {
                "cases": 0,
                "passed": 0,
                "failed": 0,
                "latency_total_ms": 0.0,
                "estimated_cost_usd": 0.0,
            },
        )
        group_summary["cases"] += 1
        group_summary["passed"] += 1 if result.passed else 0
        group_summary["failed"] += 0 if result.passed else 1
        group_summary["latency_total_ms"] += result.metrics.latency_ms
        group_summary["estimated_cost_usd"] += result.metrics.estimated_cost_usd

    for group_summary in summary.values():
        cases = group_summary["cases"] or 1
        group_summary["avg_latency_ms"] = round(group_summary["latency_total_ms"] / cases, 1)
        group_summary.pop("latency_total_ms", None)

    return summary
