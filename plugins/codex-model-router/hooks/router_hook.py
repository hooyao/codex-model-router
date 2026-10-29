#!/usr/bin/env python3
"""Emit bounded routing entrypoints only when the context needs refreshing."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any


if __name__ == "__main__":
    sys.dont_write_bytecode = True

HOOK_DIRECTORY = Path(__file__).resolve().parent
if str(HOOK_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(HOOK_DIRECTORY))

from context_cache import already_emitted, remember_emission, state_path  # noqa: E402
from routing_config import (  # noqa: E402
    RoutingConfigError, load_workspace_config, resolve_workspace_cwd, routing_context_block,
)


SUPPORTED_EVENTS = {"SessionStart", "UserPromptSubmit", "SubagentStart"}
MAX_HOOK_OUTPUT_BYTES = 4_096
CONTEXT_TOKEN_THRESHOLD = 1_500
CONTROLLER_ENTRYPOINT = (
    "CONTROLLER: Run the Decision context program below with --config set to the workspace config "
    "when context is missing or revised. It supplies the template and validated config. "
    "Send classified JSON on stdin to the Decision resolver program with the same --config, record its validated result, "
    "and emit ROUTE: DIRECT or ROUTE: DELEGATE with its rule/reason before acting. "
    "DIRECT needs no skill/reference read. DELEGATE loads the coordination skill and relevant references. "
    "One decision covers a request/stage's declared reads, receipt, edit and self-check under the same owner/scope. "
    "Resolve new requests or separately owned stages; reclassify changed bounds before acting. "
    "Status-only replies reuse accepted results. "
    "This is instruction-layer policy, not a permission barrier."
)
WORKER_ENTRYPOINT = (
    "WORKER ROLE OVERRIDE (SubagentStart)\n"
    "Execute your bounded assignment yourself, including its analysis, edits, commands, tests, and validation. "
    "Inherited router controller-only restrictions do not apply. Respect the packet's permissions, safety, "
    "dependencies, write scope, limits, and acceptance criteria; preserve unrelated work. "
    "Do not start subagents. A read-only assignment permits no edits. "
    "Return the unchanged Worker name and Task ID, outcome, evidence, validation, and risks or blockers. "
    "A minimal packet does not prove erased history or a technical sandbox."
)


def controller_session_context(model: str, routing_context: str, revision: str | None = None) -> str:
    """Construct the controller entrypoint; model selection remains runtime-owned."""
    update = f"\nInstructions revision: {revision}" if revision else ""
    return f"{CONTROLLER_ENTRYPOINT}{update}\n\n{routing_context}"


def instruction_signature() -> str:
    """Refresh on any shipped routing instruction or entrypoint change."""
    root = HOOK_DIRECTORY.parent
    paths = [root / ".codex-plugin" / "plugin.json", Path(__file__),
             HOOK_DIRECTORY / "describe_router.py", HOOK_DIRECTORY / "execution_decision.py",
             HOOK_DIRECTORY / "routing_config.py",
             root / "skills" / "model-router" / "SKILL.md"]
    paths += sorted((root / "skills" / "model-router" / "references").glob("*.md"))
    digest = hashlib.sha256()
    try:
        for path in paths:
            digest.update(str(path).encode("utf-8"))
            digest.update(path.read_bytes())
    except OSError as error:
        raise RoutingConfigError(f"cannot read routing instructions: {error}") from error
    return digest.hexdigest()


def prepare_hook_output(event: dict[str, Any]) -> tuple[dict[str, Any], Path | None, str]:
    event_name = event.get("hook_event_name")
    if not isinstance(event_name, str) or event_name not in SUPPORTED_EVENTS:
        raise ValueError("hook_event_name must be a supported lifecycle event")
    if event_name == "SubagentStart":
        context, cache_path, signature = WORKER_ENTRYPOINT, None, ""
    else:
        workspace = resolve_workspace_cwd(event.get("cwd"))
        config_path, config, _created = load_workspace_config(workspace)
        block = routing_context_block(config_path, config)
        revision = instruction_signature()
        signature = hashlib.sha256((block + revision).encode("utf-8")).hexdigest()
        cache_path = state_path(event, config_path)
        if event_name == "UserPromptSubmit" and already_emitted(cache_path, signature):
            return {}, None, ""
        context = controller_session_context(str(event.get("model") or "unknown"), block, revision)
        if event_name == "SessionStart" and event.get("source") in ("compact", "clear"):
            context += "\nContext reset: reload compact decision context; load coordination references if delegated."
    output = {"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": context}}
    size = len(json.dumps(output, separators=(",", ":")).encode("utf-8")) + len(os.linesep)
    if size > MAX_HOOK_OUTPUT_BYTES:
        raise RoutingConfigError(f"hook output is {size} bytes; maximum is {MAX_HOOK_OUTPUT_BYTES} bytes")
    return output, cache_path, signature


def build_hook_output(event: dict[str, Any]) -> dict[str, Any]:
    """Build/validate output without marking it delivered to stdout."""
    return prepare_hook_output(event)[0]


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook input must be a JSON object")
        output, cache_path, signature = prepare_hook_output(payload)
        print(json.dumps(output, separators=(",", ":")), flush=True)
        remember_emission(cache_path, signature)
    except (json.JSONDecodeError, RoutingConfigError, ValueError) as error:
        print(f"codex-model-router hook error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
