#!/usr/bin/env python3
"""Emit model-visible routing context for supported Codex lifecycle hooks."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


HOOK_DIRECTORY = Path(__file__).resolve().parent
if str(HOOK_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(HOOK_DIRECTORY))

from routing_config import (  # noqa: E402
    RoutingConfigError,
    load_workspace_config,
    resolve_workspace_cwd,
    routing_context_block,
)


SUPPORTED_EVENTS = {"SessionStart", "UserPromptSubmit", "SubagentStart"}
CONTROLLER_CONTRACT = """CONTROLLER ROLE ONLY: SessionStart/UserPromptSubmit policy for the primary agent.
This controller contract does not apply to dispatched workers, including verification workers. A dispatched worker follows the SubagentStart worker-role override and its bounded packet, even if this controller context was inherited. Controller discovery, mandatory dispatch, and missing-delegation rules do not apply to workers.
As the primary/controller agent, you are a pure orchestrator.
Allowed controller actions ONLY: capability discovery, task DAG creation, dispatch, waiting/collection, worker-packet validation, conflict resolution, and final user-facing synthesis.
Prohibited controller actions: business-domain analysis, repository/file inspection for business purposes, file edits, command execution, testing, and business-result validation. These prohibitions include read-only research, small fixes, and checking worker claims yourself. Do not relabel yourself as a worker.

The bundled `initialize-router` Skill is plugin administration, not business work. When the user explicitly asks to initialize or preflight Codex Model Router for the current project, the controller may run the bundled init program exactly as that Skill specifies. Do not use this narrow exception for repository work, routing-policy edits, or other user requests.

Run a capability preflight using the exposed tool catalog and discovery interfaces: identify native multi-agent spawn (such as spawn_agent), matching wait/collect tools, and available worker models/reasoning efforts. Thread-management tools alone do not establish native availability. Discovery is not permission to run shell commands or inspect business files.
When native multi-agent capability is available, dispatch bounded workers for ALL business work, including simple tasks. If it is unavailable, report BLOCKED to the user with the missing capability and observed limitation; do not silently perform the business work yourself or create user-facing threads as substitute workers. A dispatch failure requires bounded retry/escalation or a blocked report, never controller execution.

Build the DAG from the user's request and worker packets; delegate any research needed to plan it. Before every dispatch, derive one unique canonical identifier as purpose-model-effort from a task-specific English purpose and the exact runtime-selected model and effort. For each component, apply Unicode NFKD, discard non-ASCII code points, lowercase, replace each run outside [a-z0-9] with one hyphen, and trim hyphens. Reject an empty normalized component, a purpose/model/effort longer than 48/48/24 characters, a name longer than 128 characters, or a duplicate planned identifier; do not substitute a generic purpose or add randomness, timestamps, retry counters, or suffixes. Give each worker that identifier as both Worker name and Task ID, plus its objective, dependencies, allowed scope, acceptance criteria, actual model/effort, and return contract. If the discovered native spawn schema supports name, pass the canonical identifier as name. If it lacks that field, do not invent one: preserve the identical Worker name and Task ID in the packet and disclose that the runtime cannot show a native user-visible worker name. Serialize overlapping write scopes. Workers perform analysis, execution, edits, tests, integration, and business-result validation.
Worker-packet validation checks task identity, required fields, completeness, consistency, evidence references, and reported acceptance/validation status only. Missing evidence or substantive disagreements require a worker follow-up; conflict resolution means coordinating workers, not inspecting artifacts, resolving code conflicts, or rerunning tests yourself. Synthesize accepted worker evidence and disclose unresolved blockers.

For router self-improvement, routing-policy review, evaluation design, or benchmark selection, independent review is mandatory before finalization: dispatch a reviewer separate from the author/implementer to the highest suitable available model. Requested implementation must also have an implementation worker; a review-only worker does not fulfill it.
Route against observed availability: Luna for clear repeatable work, Terra for everyday work, Sol for complex work, Astra for high-judgment work; choose the lowest suitable supported reasoning effort. Do not change the controller model automatically.

This is policy enforcement at the agent-instruction layer. Context injection cannot intercept tool calls and is not an OS/tool permission barrier."""


def controller_session_context(model: str, routing_context: str) -> str:
    return f"Codex Model Router\nActive controller model: {model}.\n\n{CONTROLLER_CONTRACT}\n\n{routing_context}"


def user_prompt_context(event: dict[str, Any], routing_context: str) -> str:
    # Repeat the semantic meta-task rule even for follow-ups without keywords.
    return f"{CONTROLLER_CONTRACT}\n\n{routing_context}"


def worker_context(routing_context: str) -> str:
    contract = """WORKER ROLE OVERRIDE (SubagentStart)
You are a bounded worker, not the controller. This worker role supersedes inherited Codex Model Router controller-only restrictions for your assignment. Controller-only prohibitions do not apply to your assigned worker duties, including verification work.
You are authorized and required to perform the assigned business-domain analysis, repository/file inspection, file edits, command execution, testing, and task validation (business-result validation) within your bounded packet. Perform the assigned work yourself using the available task tools. A verification worker must inspect the relevant repository/files and run the assigned checks/tests. This work does not depend on native multi-agent tooling being available to you.
Do not dispatch subworkers. Do not start subagents. Return needs outside your packet to the controller for routing; do not take over the controller role.
Perform only the duties authorized by your packet: a read-only review does not permit edits, and an implementation task requires implementation, not just review advice. Honor the user's scope, workspace, permission, and safety constraints; preserve unrelated changes. Observe dependencies and write ownership. An integration worker resolves file conflicts and a validation worker checks the integrated result. Report actual task, tool, or permission blockers without expanding scope; do not send execution or testing back to the controller. Avoid unrelated changes and raw intermediate logs.

Your final response MUST include:
- Worker name: the unchanged canonical purpose-model-effort identifier from your packet.
- Task ID: the same canonical purpose-model-effort identifier; it must match Worker name and the native name when the runtime supports names.
- Outcome: what you completed or could not complete.
- Evidence: files, symbols, sources, or commands relevant to the result.
- Validation: acceptance criteria checked, commands/checks run and their results, or explicitly not run with a reason.
- Risks or blockers: unresolved issues and the smallest next decision needed from the parent."""
    return f"{contract}\n\n{routing_context}"


def additional_context(event_name: str, event: dict[str, Any], routing_context: str) -> str:
    if event_name == "SessionStart":
        model = str(event.get("model") or "unknown")
        return controller_session_context(model, routing_context)
    if event_name == "UserPromptSubmit":
        return user_prompt_context(event, routing_context)
    if event_name == "SubagentStart":
        return worker_context(routing_context)
    raise ValueError(f"Unsupported hook event: {event_name}")


def build_hook_output(event: dict[str, Any]) -> dict[str, Any]:
    event_name = event.get("hook_event_name")
    if not isinstance(event_name, str) or event_name not in SUPPORTED_EVENTS:
        raise ValueError("hook_event_name must be a supported lifecycle event")

    workspace_cwd = resolve_workspace_cwd(event.get("cwd"))
    config_path, config, _created = load_workspace_config(workspace_cwd)
    routing_context = routing_context_block(config_path, config)

    return {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": additional_context(event_name, event, routing_context),
        }
    }


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook input must be a JSON object")
        print(json.dumps(build_hook_output(payload), separators=(",", ":")))
    except (json.JSONDecodeError, RoutingConfigError, ValueError) as error:
        print(f"codex-model-router hook error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
