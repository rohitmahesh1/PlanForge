from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, Dict, List, Tuple

from evals.case_loader import load_cases
from evals.models import EvalCase, EvalMetrics, EvalResult
from evals.report import build_markdown_report, write_report_artifacts
from evals.scorers import score_case


AdapterFn = Callable[[EvalCase], Tuple[dict, EvalMetrics]]


def main() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parent.parent
    cases = load_cases(
        repo_root / "evals" / "cases",
        suite=args.suite,
        include_live=args.include_live,
    )
    if not cases:
        print("No eval cases found.", file=sys.stderr)
        return 1

    adapters = _adapters(repo_root, args)
    results: List[EvalResult] = []

    for case in cases:
        adapter = adapters.get(case.adapter)
        if adapter is None:
            actual = {"error": f"Unsupported adapter: {case.adapter}"}
            metrics = EvalMetrics(latency_ms=0, model="unsupported")
            result = EvalResult(
                case=case,
                passed=False,
                actual=actual,
                metrics=metrics,
                assertions=[],
            )
        else:
            actual, metrics = adapter(case)
            result = score_case(case, actual, metrics)
        results.append(result)

    report = build_markdown_report(results)
    print(report)

    if args.write_artifacts:
        artifacts = write_report_artifacts(repo_root / "evals" / "reports", results)
        print("")
        print(f"Wrote JSON report to {artifacts['json']}")
        print(f"Wrote Markdown report to {artifacts['markdown']}")

    return 0 if all(result.passed for result in results) else 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run PlanForge eval cases.")
    parser.add_argument(
        "--suite",
        default=None,
        help="Optional suite name, for example 'workflow'. Defaults to all cases.",
    )
    parser.add_argument(
        "--include-live",
        action="store_true",
        help="Include live benchmark cases. Deterministic-only runs are the default.",
    )
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help="Allow outbound live-model benchmark execution for cases marked mode=live.",
    )
    parser.add_argument(
        "--live-model",
        default=None,
        help="Optional model override for live OpenAI benchmark adapters.",
    )
    parser.add_argument(
        "--write-artifacts",
        action="store_true",
        help="Write JSON and Markdown reports under evals/reports/.",
    )
    return parser.parse_args()


def _adapters(
    repo_root: Path,
    args: argparse.Namespace,
) -> Dict[str, Callable[[EvalCase], Tuple[dict, EvalMetrics]]]:
    from evals.adapters.live_openai_workflow import run_case as run_live_openai_workflow_case
    from evals.adapters.router_stub import run_case as run_router_stub_case
    from evals.adapters.sandbox_plan import run_case as run_sandbox_plan_case
    from evals.adapters.tool_host import run_case as run_tool_host_case
    from evals.adapters.workflow_heuristic import run_case as run_workflow_case

    return {
        "workflow_heuristic": lambda case: run_workflow_case(case, repo_root=repo_root),
        "sandbox_plan": lambda case: run_sandbox_plan_case(case, repo_root=repo_root),
        "router_stub": lambda case: run_router_stub_case(case, repo_root=repo_root),
        "tool_host": lambda case: run_tool_host_case(case, repo_root=repo_root),
        "live_openai_workflow": lambda case: run_live_openai_workflow_case(
            case,
            repo_root=repo_root,
            allow_live=args.allow_live,
            default_model=args.live_model,
        ),
    }


if __name__ == "__main__":
    raise SystemExit(main())
