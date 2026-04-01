# PlanForge

PlanForge is a FastAPI backend for an AI scheduling assistant that can inspect calendar availability, create and update events, reorganize a day after delays, track undo history, and persist lightweight scheduling policies.

## Current Phase

The repo currently supports the core web/API scheduling flow:
- `/message` for agent-driven scheduling requests
- workflow classification, workflow-specific tool routing, and execution tracing for each `/message` request
- calendar CRUD, reorg, tasks, prefs, policies, and undo endpoints
- OpenAI tool-calling orchestration via `LLMRouter`

Telegram and Twilio scaffolds are present but disabled by default until their missing service dependencies are implemented.

## Local Notes

- `LLM_ROUTER_MODE=stub` keeps the app in a local fallback mode.
- `LLM_ROUTER_MODE=openai` enables the OpenAI tool-calling flow when `OPENAI_API_KEY` is set.
- `LLM_ENABLE_INTENT_CLASSIFICATION=true` enables the request classification pass before planning.
- `LLM_INTENT_MODEL=gpt-5` overrides the model used for workflow/intent classification.
- `LLM_EXECUTION_MODE=native_tools` keeps OpenAI in the direct tool-calling loop.
- `LLM_EXECUTION_MODE=sandbox_plan` asks the model for a JSON execution plan and runs it through `SandboxExecutor`.
- `SANDBOX_BACKEND=python_plan` uses the in-process Python plan interpreter.
- `SANDBOX_BACKEND=quickjs_plan` routes sandbox-plan execution through the QuickJS/WASM sidecar at `sandbox/quickjs/runner.js`.
- `SANDBOX_RUNTIME_COMMAND="node sandbox/quickjs/runner.js"` overrides the sidecar launch command.
- `ENABLE_TELEGRAM_INTEGRATION=true` and `ENABLE_TWILIO_INTEGRATION=true` opt into those integrations only when their backing services are available.

The current `quickjs_plan` backend already runs plans inside QuickJS/WASM while preserving the Python host/tool boundary.

The `/message` response now includes lightweight workflow metadata so you can inspect the classified intent, selected workflow, tools used, and execution status for each request.

## Evals

- Run `python -m evals.runner --suite workflow` for the first deterministic workflow-classification suite.
- Run `python -m evals.runner --suite sandbox` for fixture-backed sandbox plan execution.
- Run `python -m evals.runner --suite router_stub` for stub-mode router and workflow-trace checks.
- Run `python -m evals.runner --suite tool_host` for fixture-backed ToolHost execution and dry-run enforcement.
- Run `python -m evals.runner --suite live_workflow --include-live --allow-live --live-model gpt-5` for optional live OpenAI workflow benchmarks.
- Run `python -m evals.runner --write-artifacts` to emit JSON and Markdown reports under `evals/reports/`.
- See [evals/README.md](/home/user/Projects/PlanForge/evals/README.md) for the case format, scoring rules, and next planned eval layers.

## Database Migrations

- Install server dependencies from [server/requirements.txt](/home/user/Projects/PlanForge/server/requirements.txt).
- Run `python server/create_db.py` to apply migrations to `head` for the current `DATABASE_URL` or the local `data.db` fallback.
- Create future revisions with `alembic -c alembic.ini revision -m "describe change"` and upgrade with `alembic -c alembic.ini upgrade head`.
