from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence


@dataclass(frozen=True)
class WorkflowDefinition:
    key: str
    description: str
    allowed_tools: Sequence[str]
    primary_tools: Sequence[str]
    planner_notes: Sequence[str]

    def allows_tool(self, tool_name: str) -> bool:
        return not self.allowed_tools or tool_name in self.allowed_tools

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "description": self.description,
            "allowed_tools": list(self.allowed_tools),
            "primary_tools": list(self.primary_tools),
            "planner_notes": list(self.planner_notes),
        }


@dataclass
class WorkflowIntent:
    intent: str
    workflow: str
    confidence: str
    rationale: str
    needs_lookup: bool = False
    expects_write: bool = False
    needs_clarification: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent,
            "workflow": self.workflow,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "needs_lookup": self.needs_lookup,
            "expects_write": self.expects_write,
            "needs_clarification": self.needs_clarification,
        }


@dataclass
class WorkflowTrace:
    intent: str
    workflow: str
    confidence: str
    rationale: str
    source: str
    mode: str
    execution_mode: str
    status: str = "pending"
    used_tools: List[str] = field(default_factory=list)
    tool_call_count: int = 0
    op_count: int = 0
    elapsed_ms: Optional[int] = None
    needs_clarification: bool = False
    result_kind: Optional[str] = None

    def record_tool(self, tool_name: str) -> None:
        self.tool_call_count += 1
        if tool_name not in self.used_tools:
            self.used_tools.append(tool_name)

    def finish(
        self,
        *,
        status: str,
        op_ids: List[str],
        elapsed_ms: int,
        needs_clarification: bool = False,
        result_kind: Optional[str] = None,
    ) -> None:
        self.status = status
        self.op_count = len(op_ids)
        self.elapsed_ms = elapsed_ms
        self.needs_clarification = needs_clarification
        self.result_kind = result_kind

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent,
            "workflow": self.workflow,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "source": self.source,
            "mode": self.mode,
            "execution_mode": self.execution_mode,
            "status": self.status,
            "used_tools": list(self.used_tools),
            "tool_call_count": self.tool_call_count,
            "op_count": self.op_count,
            "elapsed_ms": self.elapsed_ms,
            "needs_clarification": self.needs_clarification,
            "result_kind": self.result_kind,
        }


WORKFLOW_DEFINITIONS: Dict[str, WorkflowDefinition] = {
    "availability_lookup": WorkflowDefinition(
        key="availability_lookup",
        description="Inspect free time or confirm when the user is available.",
        allowed_tools=(
            "calendar.freebusy",
            "calendar.list",
            "calendar.search",
            "calendar.get",
            "tasks.list",
            "prefs.get",
            "policies.list",
        ),
        primary_tools=("calendar.freebusy", "calendar.list", "calendar.search"),
        planner_notes=(
            "Answer with concrete windows or constraints.",
            "Stay read-only unless the user explicitly asks for a change.",
            "Use calendar.search or calendar.list before discussing a specific event.",
        ),
    ),
    "event_management": WorkflowDefinition(
        key="event_management",
        description="Create, inspect, move, update, or delete calendar events.",
        allowed_tools=(
            "calendar.freebusy",
            "calendar.list",
            "calendar.search",
            "calendar.get",
            "calendar.create",
            "calendar.update",
            "calendar.move",
            "calendar.delete",
            "prefs.get",
            "policies.list",
            "ops.history",
        ),
        primary_tools=("calendar.search", "calendar.get", "calendar.freebusy"),
        planner_notes=(
            "Lookup before mutating when the event is not already unambiguous.",
            "Use freebusy before proposing or creating a new time block.",
            "If multiple plausible events match, stop and ask one short clarification.",
        ),
    ),
    "task_management": WorkflowDefinition(
        key="task_management",
        description="Manage task-like items and optionally place them on the calendar.",
        allowed_tools=(
            "tasks.add",
            "tasks.list",
            "tasks.complete",
            "tasks.update",
            "tasks.delete",
            "tasks.schedule",
            "calendar.freebusy",
            "calendar.list",
            "calendar.search",
            "calendar.get",
            "prefs.get",
            "policies.list",
            "ops.history",
        ),
        primary_tools=("tasks.list", "tasks.schedule", "calendar.freebusy"),
        planner_notes=(
            "Treat the task list as the source of truth for task edits.",
            "Use freebusy before scheduling task blocks unless timing is explicit.",
            "When scheduling from a task, preserve the link between the task and the work block.",
        ),
    ),
    "policy_management": WorkflowDefinition(
        key="policy_management",
        description="Persist or inspect durable scheduling rules.",
        allowed_tools=("policies.save", "policies.list", "policies.delete", "prefs.get"),
        primary_tools=("policies.list", "policies.save"),
        planner_notes=(
            "Use policies for durable habits or rules that should apply later.",
            "Reflect the saved rule back to the user in plain language.",
        ),
    ),
    "preference_management": WorkflowDefinition(
        key="preference_management",
        description="Inspect or update core planning preferences like sleep windows and buffers.",
        allowed_tools=("prefs.get", "prefs.update", "policies.list"),
        primary_tools=("prefs.get", "prefs.update"),
        planner_notes=(
            "Use prefs for core planning constraints such as sleep and buffers.",
            "If the request sounds like a durable habit rather than a hard constraint, consider policies instead.",
        ),
    ),
    "day_reorganization": WorkflowDefinition(
        key="day_reorganization",
        description="Repair the rest of the day after a delay, oversleep, or disruption.",
        allowed_tools=("calendar.reorg_today", "calendar.list", "calendar.freebusy", "prefs.get", "policies.list"),
        primary_tools=("calendar.reorg_today", "calendar.list"),
        planner_notes=(
            "Use the dedicated day reorganization tool when the request is about a delay or oversleep.",
            "Preserve fixed meetings and user constraints.",
        ),
    ),
    "undo_workflow": WorkflowDefinition(
        key="undo_workflow",
        description="Inspect recent operations or undo one.",
        allowed_tools=("ops.undo", "ops.history"),
        primary_tools=("ops.undo", "ops.history"),
        planner_notes=(
            "Prefer undo when the user clearly wants to reverse a recent change.",
            "Use history first only if the user refers to an older or ambiguous operation.",
        ),
    ),
    "image_schedule_intake": WorkflowDefinition(
        key="image_schedule_intake",
        description="Extract scheduling details from an image, then plan the right calendar or task action.",
        allowed_tools=(
            "calendar.freebusy",
            "calendar.list",
            "calendar.search",
            "calendar.get",
            "calendar.create",
            "calendar.update",
            "calendar.move",
            "tasks.add",
            "tasks.list",
            "tasks.schedule",
            "prefs.get",
            "policies.list",
        ),
        primary_tools=("calendar.search", "calendar.freebusy", "tasks.add"),
        planner_notes=(
            "Extract the relevant scheduling facts from the image before deciding on tools.",
            "Clarify only if the image is ambiguous enough that acting would be risky.",
        ),
    ),
    "general_planning": WorkflowDefinition(
        key="general_planning",
        description="Handle broad planning requests that may span multiple tool families.",
        allowed_tools=(),
        primary_tools=("calendar.freebusy", "calendar.search", "tasks.list"),
        planner_notes=(
            "Start with the smallest set of tools that can answer the request.",
            "Prefer lookup before writes when the user's target is ambiguous.",
        ),
    ),
}


INTENT_TO_WORKFLOW = {
    "find_availability": "availability_lookup",
    "manage_event": "event_management",
    "manage_task": "task_management",
    "update_policy": "policy_management",
    "update_preferences": "preference_management",
    "reorganize_day": "day_reorganization",
    "undo_change": "undo_workflow",
    "process_schedule_image": "image_schedule_intake",
    "general_planning": "general_planning",
}


class AgentWorkflowService:
    async def classify(
        self,
        *,
        text: Optional[str],
        image_url: Optional[str],
        source: Optional[str],
        dry_run: bool,
        user_content: Any,
        client: Any = None,
        model: Optional[str] = None,
        fallback: Optional[WorkflowIntent] = None,
    ) -> WorkflowIntent:
        heuristic = fallback or self.classify_heuristic(
            text=text,
            image_url=image_url,
            source=source,
            dry_run=dry_run,
        )
        if client is None or not model:
            return heuristic

        try:
            parsed = await self._classify_with_llm(
                client=client,
                model=model,
                user_content=user_content,
                source=source,
                dry_run=dry_run,
            )
        except Exception:
            return heuristic

        return self._normalize_intent(parsed, fallback=heuristic, dry_run=dry_run)

    def classify_heuristic(
        self,
        *,
        text: Optional[str],
        image_url: Optional[str],
        source: Optional[str],
        dry_run: bool,
    ) -> WorkflowIntent:
        raw_text = (text or "").strip()
        lowered = raw_text.lower()
        has_image = bool(image_url)

        if re.search(r"\b(undo|revert|roll back|go back)\b", lowered):
            return WorkflowIntent(
                intent="undo_change",
                workflow="undo_workflow",
                confidence="high",
                rationale="The user is explicitly asking to reverse a prior operation.",
                expects_write=not dry_run,
            )

        if re.search(r"\b(overslept|slept in|running late|delay|behind schedule|reorg)\b", lowered):
            return WorkflowIntent(
                intent="reorganize_day",
                workflow="day_reorganization",
                confidence="high",
                rationale="The request is about shifting the rest of the day after a disruption.",
                needs_lookup=True,
                expects_write=not dry_run,
            )

        if re.search(r"\b(task|todo|to-do|assignment|homework|problem set|pset|due|complete|finish)\b", lowered):
            return WorkflowIntent(
                intent="manage_task",
                workflow="task_management",
                confidence="medium",
                rationale="The request uses task-oriented language or refers to due work.",
                needs_lookup=not re.search(r"\b(add|create)\b", lowered),
                expects_write=not dry_run and bool(re.search(r"\b(add|create|complete|finish|update|delete|schedule)\b", lowered)),
            )

        if re.search(r"\b(policy|rule|always|whenever|habit|every time)\b", lowered):
            return WorkflowIntent(
                intent="update_policy",
                workflow="policy_management",
                confidence="medium",
                rationale="The request sounds like a durable planning rule or habit.",
                expects_write=not dry_run,
            )

        if re.search(r"\b(sleep|buffer|meeting length|default length|preference|timezone)\b", lowered):
            return WorkflowIntent(
                intent="update_preferences",
                workflow="preference_management",
                confidence="medium",
                rationale="The request refers to core scheduling preferences or constraints.",
                expects_write=not dry_run,
            )

        if has_image and not raw_text:
            return WorkflowIntent(
                intent="process_schedule_image",
                workflow="image_schedule_intake",
                confidence="medium",
                rationale="An image-only request needs extraction before a scheduling action.",
                needs_lookup=True,
                expects_write=not dry_run,
            )

        if re.search(r"\b(free|availability|available|open slot|when can|find time)\b", lowered):
            return WorkflowIntent(
                intent="find_availability",
                workflow="availability_lookup",
                confidence="medium",
                rationale="The request is asking for open time or scheduling feasibility.",
                needs_lookup=True,
            )

        if has_image:
            return WorkflowIntent(
                intent="process_schedule_image",
                workflow="image_schedule_intake",
                confidence="medium",
                rationale="The request includes an image that likely contains scheduling context.",
                needs_lookup=True,
                expects_write=not dry_run,
            )

        if re.search(
            r"\b(move|reschedule|rename|delete|cancel|update|create|schedule|book|add to (my )?calendar|put .*calendar)\b",
            lowered,
        ):
            return WorkflowIntent(
                intent="manage_event",
                workflow="event_management",
                confidence="medium",
                rationale="The request appears to target a calendar event or new calendar block.",
                needs_lookup=not bool(re.search(r"\b(create|schedule|book|add to (my )?calendar)\b", lowered)),
                expects_write=not dry_run,
            )

        return WorkflowIntent(
            intent="general_planning",
            workflow="general_planning",
            confidence="low",
            rationale=f"No narrow workflow matched cleanly for the current {source or 'web'} request.",
            needs_lookup=True,
            expects_write=not dry_run,
        )

    def definition_for(self, intent: WorkflowIntent) -> WorkflowDefinition:
        return WORKFLOW_DEFINITIONS.get(intent.workflow, WORKFLOW_DEFINITIONS["general_planning"])

    def workflow_system_message(
        self,
        intent: WorkflowIntent,
        definition: WorkflowDefinition,
        *,
        dry_run: bool,
    ) -> str:
        primary_tools = ", ".join(definition.primary_tools) or "all applicable tools"
        notes = "\n".join(f"- {note}" for note in definition.planner_notes)
        dry_run_note = (
            "\n- Dry-run is active. Stay read-only and describe the proposed action."
            if dry_run
            else ""
        )
        return (
            f"Selected workflow: {definition.key}\n"
            f"Intent classification: {intent.intent} (confidence: {intent.confidence})\n"
            f"Classifier rationale: {intent.rationale}\n"
            f"Primary tools for this workflow: {primary_tools}\n"
            "Workflow notes:\n"
            f"{notes}"
            f"{dry_run_note}"
        )

    def new_trace(
        self,
        *,
        intent: WorkflowIntent,
        source: Optional[str],
        mode: str,
        execution_mode: str,
    ) -> WorkflowTrace:
        return WorkflowTrace(
            intent=intent.intent,
            workflow=intent.workflow,
            confidence=intent.confidence,
            rationale=intent.rationale,
            source=source or "web",
            mode=mode,
            execution_mode=execution_mode,
        )

    async def _classify_with_llm(
        self,
        *,
        client: Any,
        model: str,
        user_content: Any,
        source: Optional[str],
        dry_run: bool,
    ) -> Dict[str, Any]:
        options = {
            "intent_options": sorted(INTENT_TO_WORKFLOW.keys()),
            "workflow_options": sorted(WORKFLOW_DEFINITIONS.keys()),
        }
        response = await client.chat.completions.create(
            model=model,
            temperature=0.0,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Classify the user's scheduling request.\n"
                        "Return JSON only with keys: intent, workflow, confidence, rationale, "
                        "needs_lookup, expects_write, needs_clarification.\n"
                        "Choose exactly one intent and one workflow from the provided options."
                    ),
                },
                {"role": "system", "content": "Classification options (JSON): " + json.dumps(options)},
                {
                    "role": "system",
                    "content": json.dumps(
                        {
                            "source": source or "web",
                            "dry_run": dry_run,
                            "has_image": isinstance(user_content, list),
                        }
                    ),
                },
                {"role": "user", "content": user_content},
            ],
        )
        content = response.choices[0].message.content or "{}"
        return _parse_json_object(content)

    def _normalize_intent(
        self,
        payload: Dict[str, Any],
        *,
        fallback: WorkflowIntent,
        dry_run: bool,
    ) -> WorkflowIntent:
        intent_name = str(payload.get("intent") or fallback.intent)
        if intent_name not in INTENT_TO_WORKFLOW:
            intent_name = fallback.intent

        workflow_name = str(payload.get("workflow") or INTENT_TO_WORKFLOW[intent_name])
        if workflow_name not in WORKFLOW_DEFINITIONS:
            workflow_name = INTENT_TO_WORKFLOW[intent_name]

        confidence = str(payload.get("confidence") or fallback.confidence).lower()
        if confidence not in {"low", "medium", "high"}:
            confidence = fallback.confidence

        rationale = str(payload.get("rationale") or fallback.rationale).strip() or fallback.rationale

        return WorkflowIntent(
            intent=intent_name,
            workflow=workflow_name,
            confidence=confidence,
            rationale=rationale,
            needs_lookup=bool(payload.get("needs_lookup", fallback.needs_lookup)),
            expects_write=bool(payload.get("expects_write", fallback.expects_write)) and not dry_run,
            needs_clarification=bool(payload.get("needs_clarification", fallback.needs_clarification)),
        )


def _parse_json_object(text: str) -> Dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        parsed = json.loads(cleaned[start:end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("Expected a JSON object")
    return parsed
