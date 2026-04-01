# PlanForge

PlanForge is an agentic scheduling backend that turns natural-language planning requests into structured, auditable actions across calendar, tasks, preferences, and durable scheduling policies. It combines workflow-aware LLM orchestration, tool-based execution, undoable state changes, and sandboxed plan execution into a single system designed for real multi-step planning work.

At the center of the codebase is a workflow-driven planning loop. The `/message` entrypoint gathers live planning context, classifies the request, selects the right workflow, narrows tool access, and then coordinates execution through either direct host tools or a sandboxed execution path. That gives the system a clear separation between user intent, planning logic, and side-effecting operations.

The operational core lives behind `ToolHost`, which presents a unified tool boundary for calendar operations, free/busy inspection, task management, policy storage, preference updates, day reorganization, and undo history. Those tools are implemented by focused services such as `GCalClient`, `FreeBusyService`, `TasksService`, `PolicyStore`, `ReorgService`, and `ChangeLogger`, allowing the planner to reason at a high level while the backend handles persistence, external APIs, and scheduling rules.

PlanForge also includes a QuickJS/WASM-backed sandbox path for model-authored execution plans. `SandboxExecutor` can run plans in-process or hand them to the `sandbox/quickjs` sidecar, where a constrained runtime executes the plan and calls back into Python only through the shared host boundary. This architecture keeps orchestration flexible while preserving strong control over tool execution and state mutation.

The repository includes an evaluation harness under `evals/` with versioned cases, deterministic and live adapters, regression baselines, structured reports, and CI gating for deterministic suites. That gives the project a built-in way to measure workflow classification, sandbox behavior, router behavior, tool execution, latency, and benchmark cost as the system evolves.

## Architecture

![PlanForge architecture](assets/planforge-architecture.svg)

The diagram source lives at [assets/planforge-architecture.puml](/home/user/Projects/PlanForge/assets/planforge-architecture.puml), and the rendered diagram lives at [assets/planforge-architecture.svg](/home/user/Projects/PlanForge/assets/planforge-architecture.svg).
