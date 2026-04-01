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
- `ENABLE_TELEGRAM_INTEGRATION=true` and `ENABLE_TWILIO_INTEGRATION=true` opt into those integrations only when their backing services are available.

## Database Migrations

- Install server dependencies from [server/requirements.txt](/home/user/Projects/PlanForge/server/requirements.txt).
- Run `python server/create_db.py` to apply migrations to `head` for the current `DATABASE_URL` or the local `data.db` fallback.
- Create future revisions with `alembic -c alembic.ini revision -m "describe change"` and upgrade with `alembic -c alembic.ini upgrade head`.
