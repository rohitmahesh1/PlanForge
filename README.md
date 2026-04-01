# PlanForge

PlanForge is a FastAPI backend for an AI scheduling assistant that can inspect calendar availability, create and update events, reorganize a day after delays, track undo history, and persist lightweight scheduling policies.

## Current Phase

The repo currently supports the core web/API scheduling flow:
- `/message` for agent-driven scheduling requests
- calendar CRUD, reorg, tasks, prefs, policies, and undo endpoints
- OpenAI tool-calling orchestration via `LLMRouter`

Telegram and Twilio scaffolds are present but disabled by default until their missing service dependencies are implemented.

## Local Notes

- `LLM_ROUTER_MODE=stub` keeps the app in a local fallback mode.
- `LLM_ROUTER_MODE=openai` enables the OpenAI tool-calling flow when `OPENAI_API_KEY` is set.
- `LLM_EXECUTION_MODE=native_tools` keeps OpenAI in the direct tool-calling loop.
- `LLM_EXECUTION_MODE=sandbox_plan` asks the model for a JSON execution plan and runs it through `SandboxExecutor`.
- `SANDBOX_BACKEND=python_plan` uses the in-process Python plan interpreter.
- `SANDBOX_BACKEND=quickjs_plan` routes sandbox-plan execution through the JS sidecar at `sandbox/quickjs/runner.js`.
- `SANDBOX_RUNTIME_COMMAND="node sandbox/quickjs/runner.js"` overrides the sidecar launch command.
- `ENABLE_TELEGRAM_INTEGRATION=true` and `ENABLE_TWILIO_INTEGRATION=true` opt into those integrations only when their backing services are available.

The current `quickjs_plan` backend uses a protocol-compatible Node sidecar that is ready to be swapped to an actual QuickJS/WASM embed internally without changing the Python host/tool boundary.

## Database Migrations

- Install server dependencies from [server/requirements.txt](/home/user/Projects/PlanForge/server/requirements.txt).
- Run `python server/create_db.py` to apply migrations to `head` for the current `DATABASE_URL` or the local `data.db` fallback.
- Create future revisions with `alembic -c alembic.ini revision -m "describe change"` and upgrade with `alembic -c alembic.ini upgrade head`.
