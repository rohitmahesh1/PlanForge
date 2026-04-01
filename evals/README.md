# Evals

This folder is the foundation for Bullet 2: versioned eval cases, deterministic runners, scorers, fixtures, and report output.

## What Exists Today

- `cases/` holds versioned JSON eval cases.
- `adapters/` contains execution backends. The initial adapter runs deterministic workflow classification checks against [agent_workflows.py](/home/user/Projects/PlanForge/server/app/services/agent_workflows.py).
- Deterministic sandbox-plan and router-stub adapters can exercise [sandbox_executor.py](/home/user/Projects/PlanForge/server/app/services/sandbox_executor.py) and the stub path in [llm_router.py](/home/user/Projects/PlanForge/server/app/services/llm_router.py) without live services.
- A deterministic ToolHost adapter exercises the real host boundary in [tool_host.py](/home/user/Projects/PlanForge/server/app/services/tool_host.py) with fake downstream services.
- `scorers.py` turns raw outputs into pass/fail assertions.
- `report.py` builds Markdown and JSON summaries.
- `reports/` is reserved for generated artifacts and ignored in git except for its `.gitignore`.

## Current Scope

The first slice focuses on request classification because it is:

- deterministic
- central to Bullet 1 and Bullet 2
- runnable without live OpenAI or calendar dependencies

The current harness covers:

1. workflow classification
2. fixture-backed sandbox plan execution
3. router stub responses and workflow traces
4. fixture-backed ToolHost execution and dry-run enforcement
5. optional live OpenAI workflow benchmarks with token, latency, and estimated cost capture

The next layers to add are:

1. CI automation and baseline comparison against prior artifacts
2. cross-suite golden baselines and change summaries
3. live router-level and tool-calling benchmark cases beyond workflow classification

## Running It

From the repo root:

```bash
python -m evals.runner --suite workflow
python -m evals.runner --suite tool_host
python -m evals.runner --write-artifacts
python -m evals.runner --suite live_workflow --include-live --allow-live --live-model gpt-5
```

The runner exits non-zero if any case fails.

Live benchmark pricing is intentionally config-driven so we do not hardcode stale API prices. You can provide:

- `EVAL_OPENAI_PRICE_INPUT_PER_1M`
- `EVAL_OPENAI_PRICE_OUTPUT_PER_1M`

or model-specific variants such as:

- `EVAL_OPENAI_PRICE_GPT_5_INPUT_PER_1M`
- `EVAL_OPENAI_PRICE_GPT_5_OUTPUT_PER_1M`

## Baselines And CI

- Compare a candidate run against the committed deterministic baseline:
  `python -m evals.baseline compare --baseline evals/baselines/deterministic.json --candidate evals/reports/latest.json --fail-on-new-cases`
- Refresh the committed deterministic baseline after an intentional change:
  `python -m evals.baseline refresh --source evals/reports/latest.json --target evals/baselines/deterministic.json`
- CI uses [.github/workflows/evals.yml](/home/user/Projects/PlanForge/.github/workflows/evals.yml) to run deterministic evals on pull requests and pushes to `main`, then compare the result against the committed baseline.

## Case Format

Each case is a JSON object with:

- `id`: stable identifier
- `suite`: logical grouping such as `workflow`
- `adapter`: execution backend such as `workflow_heuristic`
- `mode`: `deterministic` or `live`
- `description`: one-line explanation
- `input`: case input payload
- `expected`: assertions to score
- `tags`: optional labels

Supported expectations in the initial scorer:

- exact checks: `intent`, `workflow`, `needs_lookup`, `expects_write`, `needs_clarification`
- execution/status checks: `status`, `result_status`, `trace_status`, `result_kind`, `op_ids_count`, `tool_calls`
- membership checks: `confidence_in`
- workflow contract checks: `allowed_tools_include`, `primary_tools_include`
- rationale/summary substring checks: `rationale_contains`, `summary_contains`, `summary_not_contains`
- trace checks: `trace_tools_exact`, `trace_tools_include`, `trace_kinds_exact`, `used_tools_include`

## Why This Matters

A strong Bullet 2 needs reproducible cases, standardized scoring, and benchmark artifacts. This scaffold gives the repo a concrete place to grow those capabilities instead of leaving them as ad hoc scripts.
