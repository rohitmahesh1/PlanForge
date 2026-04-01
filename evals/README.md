# Evals

This folder is the foundation for Bullet 2: versioned eval cases, deterministic runners, scorers, fixtures, and report output.

## What Exists Today

- `cases/` holds versioned JSON eval cases.
- `adapters/` contains execution backends. The initial adapter runs deterministic workflow classification checks against [agent_workflows.py](/home/user/Projects/PlanForge/server/app/services/agent_workflows.py).
- Deterministic sandbox-plan and router-stub adapters can exercise [sandbox_executor.py](/home/user/Projects/PlanForge/server/app/services/sandbox_executor.py) and the stub path in [llm_router.py](/home/user/Projects/PlanForge/server/app/services/llm_router.py) without live services.
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

The next layers to add are:

1. fixture-backed tool-host evals for [tool_host.py](/home/user/Projects/PlanForge/server/app/services/tool_host.py)
2. live-model benchmark runs that record token usage, latency, and estimated cost
3. CI automation and baseline comparison against prior artifacts

## Running It

From the repo root:

```bash
python -m evals.runner --suite workflow
python -m evals.runner --write-artifacts
```

The runner exits non-zero if any case fails.

## Case Format

Each case is a JSON object with:

- `id`: stable identifier
- `suite`: logical grouping such as `workflow`
- `adapter`: execution backend such as `workflow_heuristic`
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
