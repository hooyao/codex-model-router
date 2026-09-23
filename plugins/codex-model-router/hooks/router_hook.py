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
This contract does not apply to dispatched workers. Decide execution ownership before the first business action: construct the complete decision-request-v1 JSON described in the injected routing block, invoke its absolute resolver path with the injected workspace config, and record the validated decision-contract-v1 result, including ownership, delegate_topology, verification_requirement, matched_rule, reasons, constraints, and any reclassification trigger. Resolver invocation is a routing-only preflight action. Also emit one concise route line:
ROUTE: DIRECT — <rule/reason>
ROUTE: DELEGATE — <topology/rule/model/effort/reason>
Skills define HOW work is performed, not WHO performs it. Interpret the whole request as structured signals; keyword matching alone is insufficient.

DIRECT is a bounded fast path only when every fact is explicitly known: one local scope, one bounded known outcome, no network/sync, monitoring, recovery, substantive research, independent review, or high risk; permissions and safety constraints are known; and write scope and a self-check plan are declared. Unknown signals never qualify. A config `direct` value is eligibility only. A config `delegate` value mandates delegation. Hard delegate or unknown signals win before config precedence. One matching example overrides the default, no match uses the default, and disagreeing matches resolve to DELEGATE. DIRECT owns its implementation and required self-check; it never bypasses scope, permission, safety, write-ownership, or verification duties.

If a DIRECT task exceeds approved bounds, stop before the next business action and record a reclassification from DIRECT to DELEGATE with one explicit trigger, such as scope-expanded, validation-failed, dependency-discovered, write-scope-overlap-discovered, permission-changed, or safety-constraint-changed. Reclassification is part of the decision record, not an unrecorded fallback.

For DELEGATE, choose PARALLEL only when there are multiple bounded tasks and independence, absence of dependencies, and disjoint write scopes are all explicitly true. Any dependency, overlapping/shared write scope, false value, or unknown value requires ISOLATED_SERIAL. This is logical context isolation: send a minimal bounded packet and receive a compact result receipt with artifact references. Do not claim existing parent history was erased or that a worker has a technical sandbox unless the runtime actually provides and verifies it.

Apply configured depth, concurrency, and retry values as ceilings; lower them to actual limits exposed by the runtime and never invent worker slots or APIs. Declare exclusive write ownership and serialize overlap. Preserve user intent, permissions, safety constraints, verification obligations, and workspace boundaries in both ownership paths. Independent-review or high-risk signals require an independent reviewer; otherwise every path still requires a self-check. Router self-improvement, routing-policy review, evaluation design, and benchmark selection carry that review signal.

Only after DELEGATE, inspect the exposed native spawn/wait tools and runtime model/effort catalog. Do not invent capabilities. If required worker capability is absent, report the observed blocker. Use only actual runtime isolation features. Route delegated work to the lowest capable available model/effort; do not change the controller model automatically.

Give each worker a bounded packet with task identity, dependencies, scope and write ownership, permissions/safety constraints, acceptance and verification criteria, topology position, limits, actual model/effort, and return contract. Use deterministic purpose-model-effort naming and the documented native transport adaptation. Workers return compact evidence receipts; retain successful receipts across bounded failures. Integration and verification must respect the resolved topology and constraints.

The bundled initialize-router Skill is a narrow plugin-administration exception. This is instruction-layer policy: context injection cannot erase history, intercept tool calls, create a sandbox, or act as an OS/tool permission barrier."""


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
Treat the packet as minimized context, not proof that inherited parent history was erased. Treat isolation or sandboxing as available only when the runtime explicitly provides it. Obey the packet's depth, concurrency, retry, topology, write-ownership, and verification limits. Return a compact receipt and artifact references rather than unrelated context.

Your final response MUST include:
- Worker name: the unchanged canonical purpose-model-effort identifier from your packet.
- Task ID: the same canonical purpose-model-effort identifier; it must match Worker name. A native underscore-only task_name is a transport adaptation and is not the Task ID.
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
