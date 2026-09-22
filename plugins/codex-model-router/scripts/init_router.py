#!/usr/bin/env python3
"""Initialize and preflight Codex Model Router for a workspace."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
HOOK_DIRECTORY = PLUGIN_ROOT / "hooks"
if str(HOOK_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(HOOK_DIRECTORY))

from routing_config import (  # noqa: E402
    RoutingConfigError,
    discover_config_path,
    load_default_template,
    load_workspace_config,
    resolve_workspace_cwd,
)


SUPPORTED_EVENTS = ("SessionStart", "UserPromptSubmit", "SubagentStart")
WINDOWS_HOOK_COMMAND = 'python "%CLAUDE_PLUGIN_ROOT%\\hooks\\router_hook.py"'
POSIX_HOOK_COMMAND = 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/router_hook.py"'
MINIMUM_PYTHON = (3, 10)


class PreflightError(RuntimeError):
    """Raised when an init preflight assumption is not satisfied."""


def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise PreflightError(f"{label} is missing: {path}") from error
    except OSError as error:
        raise PreflightError(f"cannot read {label} {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise PreflightError(
            f"{label} is malformed JSON at line {error.lineno}, column {error.colno}: {error.msg}"
        ) from error


def _configured_handlers() -> list[tuple[str, dict[str, Any]]]:
    hook_path = PLUGIN_ROOT / "hooks" / "hooks.json"
    config = _load_json(hook_path, "hook configuration")
    hooks = config.get("hooks") if isinstance(config, dict) else None
    if not isinstance(hooks, dict):
        raise PreflightError("hook configuration must contain a hooks object")
    handlers: list[tuple[str, dict[str, Any]]] = []
    for event in SUPPORTED_EVENTS:
        groups = hooks.get(event)
        if not isinstance(groups, list) or not groups:
            raise PreflightError(f"hook configuration is missing {event}")
        for group in groups:
            event_handlers = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(event_handlers, list) or not event_handlers:
                raise PreflightError(f"hook configuration {event} group has no handlers")
            for handler in event_handlers:
                if not isinstance(handler, dict) or handler.get("type") != "command":
                    raise PreflightError(f"hook configuration {event} contains a non-command handler")
                if handler.get("commandWindows") != WINDOWS_HOOK_COMMAND:
                    raise PreflightError(
                        f"hook configuration {event} must use Windows command {WINDOWS_HOOK_COMMAND!r}"
                    )
                if handler.get("command") != POSIX_HOOK_COMMAND:
                    raise PreflightError(
                        f"hook configuration {event} must use POSIX command {POSIX_HOOK_COMMAND!r}"
                    )
                handlers.append((event, handler))
    return handlers


def _python_runtime() -> tuple[Path, str]:
    command = "python" if sys.platform == "win32" else "python3"
    runtime = shutil.which(command)
    if not runtime:
        raise PreflightError(
            f"the required {command!r} command is not available on PATH; install Python 3.10+ "
            "or add it to PATH before Codex starts"
        )
    try:
        probe = subprocess.run(
            [runtime, "-c", "import json, pathlib, sys; print('.'.join(map(str, sys.version_info[:3])))"],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except OSError as error:
        raise PreflightError(f"could not launch configured Python runtime {runtime}: {error}") from error
    if probe.returncode != 0:
        raise PreflightError(
            f"configured Python runtime {runtime} exited {probe.returncode}: {probe.stderr.strip()}"
        )
    version = probe.stdout.strip()
    try:
        parts = tuple(int(part) for part in version.split("."))
    except ValueError as error:
        raise PreflightError(f"configured Python runtime returned an invalid version: {version!r}") from error
    if parts < MINIMUM_PYTHON:
        raise PreflightError(
            f"configured Python runtime {runtime} is {version}; Python 3.10 or newer is required"
        )
    return Path(runtime).resolve(), version


def _hook_fixture(event_name: str, workspace: Path) -> dict[str, str]:
    return {
        "hook_event_name": event_name,
        "session_id": "init-preflight",
        "cwd": str(workspace),
        "source": "startup",
        "model": "preflight",
    }


def _validate_smoke_output(
    event_name: str, result: subprocess.CompletedProcess[str], config_path: Path, label: str
) -> None:
    if result.returncode != 0:
        diagnostic = result.stderr.strip() or result.stdout.strip() or "no output"
        raise PreflightError(f"{label} exited {result.returncode}: {diagnostic}")
    try:
        output = json.loads(result.stdout)
        hook_output = output["hookSpecificOutput"]
        context = hook_output["additionalContext"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise PreflightError(f"{label} returned invalid output: {error}") from error
    if hook_output.get("hookEventName") != event_name:
        raise PreflightError(f"{label} returned the wrong lifecycle event")
    if "ROUTING_CONFIG_BEGIN" not in context or "ROUTING_CONFIG_END" not in context:
        raise PreflightError(f"{label} did not inject the routing config block")
    if str(config_path) not in context:
        raise PreflightError(f"{label} did not use workspace config {config_path}")


def _smoke_hook(
    runtime: Path,
    workspace: Path,
    config_path: Path,
    handlers: list[tuple[str, dict[str, Any]]],
) -> None:
    hook_path = PLUGIN_ROOT / "hooks" / "router_hook.py"
    if not hook_path.is_file():
        raise PreflightError(f"hook program is missing: {hook_path}")
    for event_name, handler in handlers:
        fixture = _hook_fixture(event_name, workspace)
        if sys.platform == "win32":
            command = handler["commandWindows"]
            # _configured_handlers restricts this to the bundled literal command,
            # so cmd receives no user-controlled shell content.
            environment = os.environ.copy()
            environment["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
            label = f"Windows lifecycle smoke command for {event_name} ({command!r})"
            # Passing a sequence would make subprocess escape the hook path's
            # quotes while building the Windows command line. This raw command
            # line preserves the configured command exactly. It remains safe
            # because _configured_handlers accepts only the bundled literal.
            cmd_command = f"cmd.exe /d /s /c {command}"
            try:
                result = subprocess.run(
                    cmd_command,
                    input=json.dumps(fixture),
                    text=True,
                    capture_output=True,
                    timeout=10,
                    check=False,
                    env=environment,
                )
            except OSError as error:
                raise PreflightError(f"could not launch {label}: {error}") from error
        else:
            label = f"POSIX hook smoke path for {event_name} ({runtime})"
            try:
                result = subprocess.run(
                    [str(runtime), str(hook_path)],
                    input=json.dumps(fixture),
                    text=True,
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
            except OSError as error:
                raise PreflightError(f"could not launch {label}: {error}") from error
        _validate_smoke_output(event_name, result, config_path, label)


def initialize(workspace_value: str | None) -> tuple[Path, bool, Path, str]:
    if sys.version_info < MINIMUM_PYTHON:
        raise PreflightError(
            f"init is running on Python {sys.version.split()[0]}; Python 3.10 or newer is required"
        )
    handlers = _configured_handlers()
    runtime, version = _python_runtime()
    load_default_template()
    workspace = resolve_workspace_cwd(workspace_value)
    intended_path, existed = discover_config_path(workspace)
    config_path, _config, created = load_workspace_config(workspace)
    if config_path != intended_path:
        raise PreflightError(
            f"routing config discovery changed unexpectedly from {intended_path} to {config_path}"
        )
    if existed and created:
        raise PreflightError("routing config existence changed unexpectedly during initialization")
    _smoke_hook(runtime, workspace, config_path, handlers)
    return config_path, created, runtime, version


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create routing.json when absent and preflight the Codex Model Router hook runtime."
    )
    parser.add_argument(
        "--workspace",
        help="Workspace or nested directory to initialize (defaults to the current directory).",
    )
    args = parser.parse_args()
    try:
        config_path, created, runtime, version = initialize(args.workspace)
    except (PreflightError, RoutingConfigError) as error:
        print(f"codex-model-router init failed: {error}", file=sys.stderr)
        return 2

    action = "created" if created else "validated existing"
    print(f"Codex Model Router init passed ({action} config).")
    print(f"Python command: {runtime}")
    print(f"Python version: {version}")
    print(f"Routing config: {config_path}")
    print("Existing routing config is never overwritten by init.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
