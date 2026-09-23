#!/usr/bin/env python3
"""Offline package and hook-output validator for Codex Model Router."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
HOOK_DIRECTORY = PLUGIN_ROOT / "hooks"
if str(HOOK_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(HOOK_DIRECTORY))

from routing_config import (  # noqa: E402
    MAX_SERIALIZED_CONFIG_BYTES,
    RoutingConfigError,
    load_config,
)


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
SEMVER_PATTERN = re.compile(
    r"^(?P<major>0|[1-9][0-9]*)\.(?P<minor>0|[1-9][0-9]*)\.(?P<patch>0|[1-9][0-9]*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
WINDOWS_HOOK_COMMAND = 'cmd.exe /d /c python "%PLUGIN_ROOT%\\hooks\\router_hook.py"'
POSIX_HOOK_COMMAND = 'python3 "$PLUGIN_ROOT/hooks/router_hook.py"'
MINIMUM_CONTEXT_LIMIT = MAX_SERIALIZED_CONFIG_BYTES + 8_192
VERSION_BUMP_IGNORED_PATH_PARTS = {"__pycache__", ".pytest_cache"}
VERSION_BUMP_IGNORED_SUFFIXES = {".pyc", ".pyo"}


def parse_semver(version: object) -> tuple[tuple[int, int, int], tuple[str, ...] | None] | None:
    """Parse SemVer 2.0.0 precedence fields, intentionally excluding build metadata.

    Build metadata is valid SemVer but does not affect release precedence, so it
    cannot be used to satisfy this plugin's source-change release policy.
    """
    if not isinstance(version, str):
        return None
    match = SEMVER_PATTERN.fullmatch(version)
    if not match:
        return None
    prerelease = match.group("prerelease")
    identifiers = tuple(prerelease.split(".")) if prerelease else None
    if identifiers and any(
        _is_ascii_numeric_identifier(identifier) and len(identifier) > 1 and identifier.startswith("0")
        for identifier in identifiers
    ):
        return None
    return (
        (int(match.group("major")), int(match.group("minor")), int(match.group("patch"))),
        identifiers,
    )


def _is_ascii_numeric_identifier(identifier: str) -> bool:
    return bool(identifier) and all("0" <= character <= "9" for character in identifier)


def compare_semver(left: object, right: object) -> int:
    """Return SemVer precedence comparison for two already-valid versions."""
    parsed_left = parse_semver(left)
    parsed_right = parse_semver(right)
    if parsed_left is None or parsed_right is None:
        raise ValueError("SemVer comparison requires valid versions")
    left_core, left_pre = parsed_left
    right_core, right_pre = parsed_right
    if left_core != right_core:
        return (left_core > right_core) - (left_core < right_core)
    if left_pre is None or right_pre is None:
        if left_pre is None and right_pre is None:
            return 0
        return 1 if left_pre is None else -1
    for left_identifier, right_identifier in zip(left_pre, right_pre):
        if left_identifier == right_identifier:
            continue
        left_numeric = _is_ascii_numeric_identifier(left_identifier)
        right_numeric = _is_ascii_numeric_identifier(right_identifier)
        if left_numeric and right_numeric:
            return (int(left_identifier) > int(right_identifier)) - (int(left_identifier) < int(right_identifier))
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return (left_identifier > right_identifier) - (left_identifier < right_identifier)
    return (len(left_pre) > len(right_pre)) - (len(left_pre) < len(right_pre))


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
    if parse_semver(manifest.get("version")) is None:
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
                if handler.get("command") != POSIX_HOOK_COMMAND:
                    errors.append(f"{label} command must be {POSIX_HOOK_COMMAND!r}")
                if handler.get("commandWindows") != WINDOWS_HOOK_COMMAND:
                    errors.append(f"{label} commandWindows must be {WINDOWS_HOOK_COMMAND!r}")
                context_limit = handler.get("additionalContextLimit")
                if type(context_limit) is not int or context_limit < MINIMUM_CONTEXT_LIMIT:
                    errors.append(
                        f"{label} additionalContextLimit must be at least {MINIMUM_CONTEXT_LIMIT} bytes"
                    )

    if not (plugin_root / "hooks" / "router_hook.py").is_file():
        errors.append("hook program is missing: hooks/router_hook.py")
    if not (plugin_root / "hooks" / "routing_config.py").is_file():
        errors.append("shared routing config module is missing: hooks/routing_config.py")
    if not (plugin_root / "hooks" / "execution_decision.py").is_file():
        errors.append("routing decision contract is missing: hooks/execution_decision.py")


def validate_policy_files(plugin_root: Path, errors: list[str]) -> None:
    skill_path = plugin_root / "skills" / "model-router" / "SKILL.md"
    init_skill_path = plugin_root / "skills" / "initialize-router" / "SKILL.md"
    policy_path = plugin_root / "skills" / "model-router" / "references" / "routing-policy.md"
    decision_contract_path = plugin_root / "skills" / "model-router" / "references" / "decision-contract.md"
    naming_path = plugin_root / "hooks" / "subagent_naming.py"
    for path, label in (
        (skill_path, "model-router Skill"),
        (init_skill_path, "initialize-router Skill"),
        (policy_path, "routing policy"),
        (decision_contract_path, "routing decision contract"),
        (naming_path, "worker naming helper"),
        (plugin_root / "scripts" / "init_router.py", "router init script"),
    ):
        if not path.is_file():
            errors.append(f"{label} is missing: {path.relative_to(plugin_root)}")

    if init_skill_path.is_file():
        content = init_skill_path.read_text(encoding="utf-8")
        for phrase in (
            "name: initialize-router",
            r"scripts\init_router.py",
            "--workspace .",
            "installed plugin",
            "never overwrites",
            "Automatic initialization",
        ):
            if phrase not in content:
                errors.append(f"initialize-router Skill is missing required guidance: {phrase}")


def validate_default_config(plugin_root: Path, errors: list[str]) -> None:
    path = plugin_root / "defaults" / "default-routing.json"
    try:
        load_config(path)
    except RoutingConfigError as error:
        errors.append(f"default routing config is invalid: {error}")


def _git(working_directory: Path, *arguments: str) -> subprocess.CompletedProcess[str] | None:
    """Run Git without letting a missing executable break offline validation."""
    try:
        return subprocess.run(
            ["git", "-C", str(working_directory), *arguments],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return None


def _git_bytes(working_directory: Path, *arguments: str) -> subprocess.CompletedProcess[bytes] | None:
    """Run Git in byte mode so porcelain paths remain safe for unusual names."""
    try:
        return subprocess.run(
            ["git", "-C", str(working_directory), *arguments],
            capture_output=True,
            check=False,
        )
    except OSError:
        return None


def _is_relevant_plugin_path(path: str) -> bool:
    parts = Path(path).parts
    if path.replace("\\", "/").casefold() == ".codex-plugin/plugin.json":
        return False
    return not any(part in VERSION_BUMP_IGNORED_PATH_PARTS for part in parts) and not Path(path).suffix in VERSION_BUMP_IGNORED_SUFFIXES


def _porcelain_untracked_and_ignored_paths(
    repository_root: Path,
    plugin_relative: str,
    errors: list[str],
) -> tuple[list[str], list[str]]:
    """Return relevant untracked and ignored plugin paths from NUL porcelain output."""
    status = _git_bytes(
        repository_root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--ignored=matching",
        "--",
        plugin_relative,
    )
    if status is None or status.returncode != 0:
        errors.append("plugin version policy could not inspect untracked or ignored plugin files")
        return [], []

    prefix = (plugin_relative.rstrip("/") + "/").replace("\\", "/")
    untracked: list[str] = []
    ignored: list[str] = []
    for record in status.stdout.split(b"\0"):
        if len(record) < 4 or record[:2] not in (b"??", b"!!"):
            continue
        path = os.fsdecode(record[3:]).replace("\\", "/")
        if not path.casefold().startswith(prefix.casefold()):
            continue
        relative_path = path[len(prefix):]
        if not _is_relevant_plugin_path(relative_path):
            continue
        if record[:2] == b"??":
            untracked.append(relative_path)
        else:
            ignored.append(relative_path)
    return untracked, ignored


def validate_version_bump(
    plugin_root: Path,
    errors: list[str],
    baseline: str | None = None,
) -> None:
    """Require a strictly higher SemVer manifest version when plugin content changed.

    The default baseline is HEAD, which protects normal working-tree validation.
    CI/release callers can supply a merge-base or target ref through ``--baseline``.
    A source archive without Git history is intentionally skipped: there is no
    trustworthy prior version to compare, and the manifest schema is still checked.
    """
    top_level = _git(plugin_root, "rev-parse", "--show-toplevel")
    if top_level is None or top_level.returncode != 0:
        return
    try:
        plugin_relative = Path(os.path.relpath(plugin_root, top_level.stdout.strip())).as_posix()
    except ValueError:
        errors.append("plugin version policy could not locate the plugin below its Git repository")
        return

    baseline_ref = baseline or "HEAD"
    baseline_commit = _git(plugin_root, "rev-parse", "--verify", f"{baseline_ref}^{{commit}}")
    if baseline_commit is None or baseline_commit.returncode != 0:
        errors.append(
            f"plugin version policy baseline {baseline_ref!r} is unavailable; pass --baseline <Git ref> "
            "or validate from a checkout with that ref"
        )
        return

    manifest_relative = f"{plugin_relative}/.codex-plugin/plugin.json"
    baseline_manifest = _git(plugin_root, "show", f"{baseline_ref}:{manifest_relative}")
    if baseline_manifest is None or baseline_manifest.returncode != 0:
        # A newly added plugin has no previous package version to compare.
        return
    try:
        baseline_version = json.loads(baseline_manifest.stdout)["version"]
    except (json.JSONDecodeError, KeyError, TypeError):
        errors.append(f"plugin version policy could not read baseline manifest at {baseline_ref}:{manifest_relative}")
        return

    repository_root = Path(top_level.stdout.strip())
    changed = _git(repository_root, "diff", "--name-only", "-z", baseline_ref, "--", plugin_relative)
    if changed is None or changed.returncode != 0:
        errors.append(f"plugin version policy could not inspect changes since {baseline_ref}")
        return
    relative_prefix = f"{plugin_relative}/"
    changed_paths = [
        path[len(relative_prefix):]
        for path in changed.stdout.split("\0")
        if path.casefold().startswith(relative_prefix.casefold())
        and _is_relevant_plugin_path(path[len(relative_prefix):])
    ]
    untracked_paths, ignored_paths = _porcelain_untracked_and_ignored_paths(
        repository_root, plugin_relative, errors
    )
    if ignored_paths:
        preview = ", ".join(ignored_paths[:5])
        remainder = "" if len(ignored_paths) <= 5 else f" (+{len(ignored_paths) - 5} more)"
        errors.append(
            "relevant plugin implementation files are ignored by Git and cannot bypass the version policy: "
            f"{preview}{remainder}. Track them or adjust the ignore rule; only cache artifacts are ignored."
        )
    changed_paths.extend(untracked_paths)
    if not changed_paths:
        return

    manifest = load_json(plugin_root / ".codex-plugin" / "plugin.json", "plugin manifest", errors)
    current_version = manifest.get("version") if isinstance(manifest, dict) else None
    current_parsed = parse_semver(current_version)
    baseline_parsed = parse_semver(baseline_version)
    if current_parsed is None:
        errors.append("plugin version policy requires the current manifest version to be valid SemVer")
        return
    if baseline_parsed is None:
        errors.append(f"plugin version policy baseline manifest version is not valid SemVer: {baseline_version!r}")
        return
    if compare_semver(current_version, baseline_version) <= 0:
        preview = ", ".join(changed_paths[:5])
        remainder = "" if len(changed_paths) <= 5 else f" (+{len(changed_paths) - 5} more)"
        errors.append(
            "plugin implementation changed without a strictly increasing SemVer release version since "
            f"{baseline_ref}: {preview}{remainder}. Bump the manifest with scripts/bump_version.py before validation/reinstall; "
            "build metadata does not change SemVer precedence."
        )


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
    elif "ROUTING_CONFIG_BEGIN" not in context or "ROUTING_CONFIG_END" not in context:
        errors.append(f"{label}: additionalContext is missing the delimited routing config")
    return errors


def run_hook_fixture(plugin_root: Path, event_name: str, fixture_name: str) -> list[str]:
    fixture_path = plugin_root / "tests" / "fixtures" / fixture_name
    errors: list[str] = []
    fixture = load_json(fixture_path, f"fixture {fixture_name}", errors)
    if not isinstance(fixture, dict):
        return errors

    hook_path = plugin_root / "hooks" / "router_hook.py"
    runtime = shutil.which("python")
    if not runtime:
        return [
            f"fixture {fixture_name}: required Windows hook command 'python' is unavailable on PATH"
        ]
    with tempfile.TemporaryDirectory() as temporary_directory:
        fixture["cwd"] = temporary_directory
        try:
            result = subprocess.run(
                [runtime, str(hook_path)],
                input=json.dumps(fixture),
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
        except OSError as error:
            return [f"fixture {fixture_name}: could not run hook program with {runtime}: {error}"]

        initialized_config = Path(temporary_directory) / ".codex-model-router" / "routing.json"
        if result.returncode == 0 and not initialized_config.is_file():
            return [f"fixture {fixture_name}: hook did not initialize {initialized_config}"]

    if result.returncode != 0:
        return [f"fixture {fixture_name}: hook exited {result.returncode}: {result.stderr.strip()}"]
    return validate_hook_output(result.stdout, event_name, f"fixture {fixture_name}")


def validate_package(
    plugin_root: Path,
    run_fixtures: bool = True,
    baseline: str | None = None,
) -> list[str]:
    errors: list[str] = []
    validate_manifest(plugin_root, errors)
    validate_hook_configuration(plugin_root, errors)
    validate_policy_files(plugin_root, errors)
    validate_default_config(plugin_root, errors)
    validate_version_bump(plugin_root, errors, baseline)

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
    parser.add_argument(
        "--baseline",
        help="Git ref to compare against for version-bump enforcement (defaults to HEAD).",
    )
    args = parser.parse_args()
    errors = validate_package(args.plugin_root.resolve(), baseline=args.baseline)
    if errors:
        print("Codex Model Router validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"Codex Model Router validation passed: {args.plugin_root.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
