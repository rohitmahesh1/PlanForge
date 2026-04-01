from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "compare":
        return compare_command(args)
    if args.command == "refresh":
        return refresh_command(args)

    parser.print_help()
    return 1


def compare_command(args: argparse.Namespace) -> int:
    baseline = _load_report(Path(args.baseline))
    candidate = _load_report(Path(args.candidate))
    comparison = compare_reports(
        baseline,
        candidate,
        fail_on_new_cases=args.fail_on_new_cases,
        max_case_latency_regression_ms=args.max_case_latency_regression_ms,
        max_case_latency_regression_pct=args.max_case_latency_regression_pct,
        max_total_cost_regression_usd=args.max_total_cost_regression_usd,
    )
    print(render_comparison_summary(comparison))
    return 0 if comparison["passed"] else 1


def refresh_command(args: argparse.Namespace) -> int:
    source = Path(args.source)
    target = Path(args.target)
    if not source.exists():
        print(f"Source report does not exist: {source}", file=sys.stderr)
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    print(f"Refreshed baseline: {target}")
    return 0


def compare_reports(
    baseline: Dict[str, Any],
    candidate: Dict[str, Any],
    *,
    fail_on_new_cases: bool,
    max_case_latency_regression_ms: int | None,
    max_case_latency_regression_pct: float | None,
    max_total_cost_regression_usd: float | None,
) -> Dict[str, Any]:
    baseline_cases = _index_results(baseline)
    candidate_cases = _index_results(candidate)

    baseline_ids = set(baseline_cases)
    candidate_ids = set(candidate_cases)
    missing_cases = sorted(baseline_ids - candidate_ids)
    new_cases = sorted(candidate_ids - baseline_ids)

    status_regressions: List[Dict[str, Any]] = []
    latency_regressions: List[Dict[str, Any]] = []

    for case_id in sorted(baseline_ids & candidate_ids):
        base = baseline_cases[case_id]
        cand = candidate_cases[case_id]

        if bool(base.get("passed")) and not bool(cand.get("passed")):
            status_regressions.append(
                {
                    "case_id": case_id,
                    "baseline_status": bool(base.get("passed")),
                    "candidate_status": bool(cand.get("passed")),
                }
            )

        latency_issue = _latency_regression(
            base,
            cand,
            max_ms=max_case_latency_regression_ms,
            max_pct=max_case_latency_regression_pct,
        )
        if latency_issue is not None:
            latency_regressions.append({"case_id": case_id, **latency_issue})

    baseline_cost = _total_cost(baseline)
    candidate_cost = _total_cost(candidate)
    cost_regression = None
    if (
        max_total_cost_regression_usd is not None
        and (candidate_cost - baseline_cost) > max_total_cost_regression_usd
    ):
        cost_regression = {
            "baseline_cost_usd": baseline_cost,
            "candidate_cost_usd": candidate_cost,
            "delta_usd": round(candidate_cost - baseline_cost, 6),
            "threshold_usd": max_total_cost_regression_usd,
        }

    failures: List[str] = []
    if missing_cases:
        failures.append(f"missing_cases={len(missing_cases)}")
    if fail_on_new_cases and new_cases:
        failures.append(f"new_cases={len(new_cases)}")
    if status_regressions:
        failures.append(f"status_regressions={len(status_regressions)}")
    if latency_regressions:
        failures.append(f"latency_regressions={len(latency_regressions)}")
    if cost_regression is not None:
        failures.append("cost_regression=1")

    return {
        "passed": not failures,
        "failure_reasons": failures,
        "missing_cases": missing_cases,
        "new_cases": new_cases,
        "status_regressions": status_regressions,
        "latency_regressions": latency_regressions,
        "cost_regression": cost_regression,
        "baseline_cases": len(baseline_cases),
        "candidate_cases": len(candidate_cases),
        "baseline_cost_usd": baseline_cost,
        "candidate_cost_usd": candidate_cost,
    }


def render_comparison_summary(comparison: Dict[str, Any]) -> str:
    status = "pass" if comparison["passed"] else "fail"
    lines = [
        "# Baseline Comparison",
        "",
        f"- Status: {status}",
        f"- Baseline cases: {comparison['baseline_cases']}",
        f"- Candidate cases: {comparison['candidate_cases']}",
        f"- Baseline cost: ${comparison['baseline_cost_usd']:.6f}",
        f"- Candidate cost: ${comparison['candidate_cost_usd']:.6f}",
    ]
    if comparison["failure_reasons"]:
        lines.append(f"- Failure reasons: {', '.join(comparison['failure_reasons'])}")

    lines.append("")
    lines.append("## Differences")
    lines.append("")
    lines.append(f"- Missing cases: {', '.join(comparison['missing_cases']) or 'none'}")
    lines.append(f"- New cases: {', '.join(comparison['new_cases']) or 'none'}")

    if comparison["status_regressions"]:
        lines.append("- Status regressions:")
        for item in comparison["status_regressions"]:
            lines.append(
                f"  - `{item['case_id']}` baseline={item['baseline_status']} candidate={item['candidate_status']}"
            )
    else:
        lines.append("- Status regressions: none")

    if comparison["latency_regressions"]:
        lines.append("- Latency regressions:")
        for item in comparison["latency_regressions"]:
            lines.append(
                f"  - `{item['case_id']}` baseline={item['baseline_latency_ms']}ms "
                f"candidate={item['candidate_latency_ms']}ms delta={item['delta_ms']}ms "
                f"({item['delta_pct']:.1f}%)"
            )
    else:
        lines.append("- Latency regressions: none")

    if comparison["cost_regression"] is not None:
        item = comparison["cost_regression"]
        lines.append(
            f"- Cost regression: baseline=${item['baseline_cost_usd']:.6f} "
            f"candidate=${item['candidate_cost_usd']:.6f} delta=${item['delta_usd']:.6f}"
        )
    else:
        lines.append("- Cost regression: none")

    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Refresh or compare PlanForge eval baselines.")
    subparsers = parser.add_subparsers(dest="command")

    compare = subparsers.add_parser("compare", help="Compare a candidate report against a baseline.")
    compare.add_argument("--baseline", required=True, help="Path to the committed baseline JSON report.")
    compare.add_argument("--candidate", required=True, help="Path to the candidate JSON report.")
    compare.add_argument(
        "--fail-on-new-cases",
        action="store_true",
        help="Treat new candidate cases as a failure until the baseline is refreshed.",
    )
    compare.add_argument(
        "--max-case-latency-regression-ms",
        type=int,
        default=None,
        help="Optional per-case latency regression threshold in milliseconds.",
    )
    compare.add_argument(
        "--max-case-latency-regression-pct",
        type=float,
        default=None,
        help="Optional per-case latency regression threshold in percent.",
    )
    compare.add_argument(
        "--max-total-cost-regression-usd",
        type=float,
        default=None,
        help="Optional threshold for total estimated cost regression.",
    )

    refresh = subparsers.add_parser("refresh", help="Copy a candidate report into the committed baseline path.")
    refresh.add_argument("--source", required=True, help="Path to the source JSON report.")
    refresh.add_argument("--target", required=True, help="Path to the baseline JSON to overwrite.")

    return parser


def _load_report(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _index_results(report: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    results = report.get("results", [])
    indexed: Dict[str, Dict[str, Any]] = {}
    if not isinstance(results, list):
        return indexed
    for item in results:
        if not isinstance(item, dict):
            continue
        case = item.get("case", {})
        case_id = case.get("id") if isinstance(case, dict) else None
        if not isinstance(case_id, str):
            continue
        indexed[case_id] = item
    return indexed


def _total_cost(report: Dict[str, Any]) -> float:
    summary = report.get("summary")
    if isinstance(summary, dict):
        suite_summary = summary.get("suite_summary")
        if isinstance(suite_summary, dict):
            total = 0.0
            for value in suite_summary.values():
                if isinstance(value, dict):
                    total += float(value.get("estimated_cost_usd", 0.0) or 0.0)
            return round(total, 6)
    return 0.0


def _latency_regression(
    baseline_item: Dict[str, Any],
    candidate_item: Dict[str, Any],
    *,
    max_ms: int | None,
    max_pct: float | None,
) -> Dict[str, Any] | None:
    if max_ms is None and max_pct is None:
        return None

    baseline_latency = _item_latency(baseline_item)
    candidate_latency = _item_latency(candidate_item)
    delta_ms = candidate_latency - baseline_latency
    if delta_ms <= 0:
        return None

    if baseline_latency <= 0:
        delta_pct = 100.0 if candidate_latency > 0 else 0.0
    else:
        delta_pct = (delta_ms / baseline_latency) * 100.0

    ms_fail = max_ms is not None and delta_ms > max_ms
    pct_fail = max_pct is not None and delta_pct > max_pct

    if max_ms is not None and max_pct is not None:
        should_fail = ms_fail and pct_fail
    else:
        should_fail = ms_fail or pct_fail

    if not should_fail:
        return None

    return {
        "baseline_latency_ms": baseline_latency,
        "candidate_latency_ms": candidate_latency,
        "delta_ms": delta_ms,
        "delta_pct": round(delta_pct, 2),
    }


def _item_latency(item: Dict[str, Any]) -> int:
    metrics = item.get("metrics")
    if not isinstance(metrics, dict):
        return 0
    return int(metrics.get("latency_ms", 0) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
