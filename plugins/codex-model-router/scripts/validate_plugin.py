#!/usr/bin/env python3
"""Offline package and hook-output validator for Codex Model Router."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


REQUIRED_HOOK_EVENTS = {
    "SessionStart": "session-start.json",
    "UserPromptSubmit": "user-prompt-submit.json",
    "SubagentStart": "subagent-start.json",
}
REQUIRED_INTERFACE_FIELDS = {
    "displayName",
    "shortDescription",
    "longDescription",
    "developerName",
    "category",
}
SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")


def load_json(path: Path, label: str, errors: list[str]) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append(f"{label} is missing: {path}")
    except json.JSONDecodeError as error:
        errors.append(f"{label} is malformed JSON: {error}")
    return None


def resolve_local_path(root: Path, value: Any, label: str, errors: list[str]) -> Path | None:
    if not isinstance(value, str) or not value.startswith("./"):
        errors.append(f"{label} must be a ./-prefixed local path")
        return None

    candidate = (root / value[2:]).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        errors.append(f"{label} escapes the plugin root: {value}")
        return None

    if not candidate.exists():
        errors.append(f"{label} does not exist: {value}")
        return None
    return candidate


def validate_manifest(plugin_root: Path, errors: list[str]) -> None:
    manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
    manifest = load_json(manifest_path, "plugin manifest", errors)
    if not isinstance(manifest, dict):
        return

    for field in ("name", "version", "description", "author", "skills", "interface"):
        if field not in manifest:
            errors.append(f"plugin manifest is missing required field: {field}")

    if not isinstance(manifest.get("name"), str) or not manifest["name"]:
        errors.append("plugin manifest name must be a non-empty string")
    if not isinstance(manifest.get("version"), str) or not SEMVER_PATTERN.fullmatch(manifest["version"]):
        errors.append("plugin manifest version must use semantic versioning")
    if not isinstance(manifest.get("description"), str) or not manifest["description"]:
        errors.append("plugin manifest description must be a non-empty string")

    author = manifest.get("author")
    if not isinstance(author, dict) or not isinstance(author.get("name"), str) or not author["name"]:
        errors.append("plugin manifest author.name must be a non-empty string")

    resolve_local_path(plugin_root, manifest.get("skills"), "manifest path 'skills'", errors)

    interface = manifest.get("interface")
    if not isinstance(interface, dict):
        errors.append("plugin manifest interface must be an object")
        return
    for field in sorted(REQUIRED_INTERFACE_FIELDS):
        if not isinstance(interface.get(field), str) or not interface[field]:
            errors.append(f"plugin manifest interface.{field} must be a non-empty string")


def validate_hook_configuration(plugin_root: Path, errors: list[str]) -> None:
    hook_path = plugin_root / "hooks" / "hooks.json"
    config = load_json(hook_path, "hook configuration", errors)
    if not isinstance(config, dict):
        return

    hooks = config.get("hooks")
    if not isinstance(hooks, dict):
        errors.append("hook configuration hooks must be an object")
        return

    for event_name in REQUIRED_HOOK_EVENTS:
        event_groups = hooks.get(event_name)
        if not isinstance(event_groups, list) or not event_groups:
            errors.append(f"hook configuration is missing {event_name}")
            continue
        for group_index, group in enumerate(event_groups):
            handlers = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(handlers, list) or not handlers:
                errors.append(f"{event_name} group {group_index} has no handlers")
                continue
            for handler_index, handler in enumerate(handlers):
                label = f"{event_name} handler {handler_index}"
                if not isinstance(handler, dict) or handler.get("type") != "command":
                    errors.append(f"{label} must be a command hook")
                    continue
                for field in ("command", "commandWindows"):
                    command = handler.get(field)
                    if not isinstance(command, str) or "router_hook.py" not in command:
                        errors.append(f"{label} {field} must target router_hook.py")

    if not (plugin_root / "hooks" / "router_hook.py").is_file():
        errors.append("hook program is missing: hooks/router_hook.py")


def validate_policy_files(plugin_root: Path, errors: list[str]) -> None:
    skill_path = plugin_root / "skills" / "model-router" / "SKILL.md"
    policy_path = plugin_root / "skills" / "model-router" / "references" / "routing-policy.md"
    for path, label in ((skill_path, "model-router Skill"), (policy_path, "routing policy")):
        if not path.is_file():
            errors.append(f"{label} is missing: {path.relative_to(plugin_root)}")


def validate_hook_output(raw_output: str, expected_event: str, label: str) -> list[str]:
    errors: list[str] = []
    try:
        payload = json.loads(raw_output)
    except json.JSONDecodeError as error:
        return [f"{label}: invalid JSON hook output: {error.msg}"]

    output = payload.get("hookSpecificOutput") if isinstance(payload, dict) else None
    if not isinstance(output, dict):
        return [f"{label}: missing hookSpecificOutput object"]
    if output.get("hookEventName") != expected_event:
        errors.append(f"{label}: expected hook event {expected_event}")
    context = output.get("additionalContext")
    if not isinstance(context, str) or not context.strip():
        errors.append(f"{label}: additionalContext must be non-empty")
    return errors


def run_hook_fixture(plugin_root: Path, event_name: str, fixture_name: str) -> list[str]:
    fixture_path = plugin_root / "tests" / "fixtures" / fixture_name
    errors: list[str] = []
    fixture = load_json(fixture_path, f"fixture {fixture_name}", errors)
    if not isinstance(fixture, dict):
        return errors

    hook_path = plugin_root / "hooks" / "router_hook.py"
    try:
        result = subprocess.run(
            [sys.executable, str(hook_path)],
            input=json.dumps(fixture),
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except OSError as error:
        return [f"fixture {fixture_name}: could not run hook program: {error}"]

    if result.returncode != 0:
        return [f"fixture {fixture_name}: hook exited {result.returncode}: {result.stderr.strip()}"]
    return validate_hook_output(result.stdout, event_name, f"fixture {fixture_name}")


def validate_package(plugin_root: Path, run_fixtures: bool = True) -> list[str]:
    errors: list[str] = []
    validate_manifest(plugin_root, errors)
    validate_hook_configuration(plugin_root, errors)
    validate_policy_files(plugin_root, errors)

    if run_fixtures and not errors:
        for event_name, fixture_name in REQUIRED_HOOK_EVENTS.items():
            errors.extend(run_hook_fixture(plugin_root, event_name, fixture_name))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the Codex Model Router plugin offline.")
    parser.add_argument(
        "--plugin-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Plugin root to validate (defaults to this package).",
    )
    args = parser.parse_args()
    errors = validate_package(args.plugin_root.resolve())
    if errors:
        print("Codex Model Router validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"Codex Model Router validation passed: {args.plugin_root.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
