#!/usr/bin/env python3
"""Emit model-visible routing context for supported Codex lifecycle hooks."""

from __future__ import annotations

import json
import sys
from typing import Any


SUPPORTED_EVENTS = {"SessionStart", "UserPromptSubmit", "SubagentStart"}


def controller_session_context(model: str) -> str:
    return f"""Codex Model Router (MVP)
Active controller model: {model}.

You remain responsible for planning, verification, conflict resolution, and the final answer. Before starting parallel work, apply the delegation gate:
1. Decide whether direct execution is cheaper and safer than delegation.
2. If delegating, decompose work into bounded tasks with explicit dependencies, acceptance criteria, and non-overlapping write scopes.
3. Select the lowest-capability available worker that can reliably pass verification: Luna for clear and repeatable tasks; Terra for everyday tool use or read-heavy scans; Sol for complex, open-ended work; Astra for high-risk or sustained-judgment work.
4. Start at the lowest suitable reasoning effort and escalate only for ambiguity, risk, complexity, or failed verification.
5. Give workers only needed context, collect concise evidence, and verify the combined result yourself.

Do not assume a preferred model is available, and do not change the controller model automatically."""


def user_prompt_context() -> str:
    return """Apply the delegation gate to this request before delegating: handle simple work directly; otherwise create bounded tasks, identify dependencies and overlapping write scopes, select the lowest suitable available worker model and reasoning effort, define acceptance criteria, and retain responsibility for verification and final synthesis."""


def worker_context() -> str:
    return """You are a bounded worker in a Codex Model Router workflow. Work only on the assigned objective and honor the parent task's workspace, permission, and safety constraints. Do not start subagents unless the parent explicitly instructs you to do so. Avoid unrelated changes and raw intermediate logs.

Your final response MUST include:
- Outcome: what you completed or could not complete.
- Evidence: files, symbols, sources, or commands relevant to the result.
- Validation: checks run and their result.
- Risks or blockers: unresolved issues and the smallest next decision needed from the parent."""


def additional_context(event_name: str, event: dict[str, Any]) -> str:
    if event_name == "SessionStart":
        model = str(event.get("model") or "unknown")
        return controller_session_context(model)
    if event_name == "UserPromptSubmit":
        return user_prompt_context()
    if event_name == "SubagentStart":
        return worker_context()
    raise ValueError(f"Unsupported hook event: {event_name}")


def build_hook_output(event: dict[str, Any]) -> dict[str, Any]:
    event_name = event.get("hook_event_name")
    if not isinstance(event_name, str) or event_name not in SUPPORTED_EVENTS:
        raise ValueError("hook_event_name must be a supported lifecycle event")

    return {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": additional_context(event_name, event),
        }
    }


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook input must be a JSON object")
        print(json.dumps(build_hook_output(payload), separators=(",", ":")))
    except (json.JSONDecodeError, ValueError) as error:
        print(f"codex-model-router hook error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
