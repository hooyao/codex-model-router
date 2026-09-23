#!/usr/bin/env python3
"""Capture the Python command used by lifecycle hooks; never invokes a model."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK_CONFIG = REPO_ROOT / "plugins" / "codex-model-router" / "hooks" / "hooks.json"
PROBE_CODE = ("import encodings,json,sys; "
              "print(json.dumps({'executable':sys.executable,'version':'.'.join(map(str,sys.version_info[:3])),"
              "'encodings_imported':True}))")
REMEDIATION = ("Install a complete Python 3.9+ runtime and put it first on PATH before starting Codex. "
               "Restart Codex from that environment and rerun this preflight. A working interpreter "
               "used to launch this collector does not prove the hook's python command works.")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def configured_command(platform: str) -> str:
    config = json.loads(HOOK_CONFIG.read_text(encoding="utf-8"))
    key = "commandWindows" if platform == "win32" else "command"
    expected = 'cmd.exe /d /c python "%PLUGIN_ROOT%\\hooks\\router_hook.py"' if platform == "win32" else \
        'python3 "$PLUGIN_ROOT/hooks/router_hook.py"'
    commands = [handler[key] for groups in config["hooks"].values() for group in groups for handler in group["hooks"]]
    if not commands or any(command != expected for command in commands):
        raise ValueError("hook commands differ from the supported preflight contract")
    return expected


def probe_command(platform: str) -> Any:
    # The only shell text is this fixed repository-owned expression, never a report field.
    return f'cmd.exe /d /s /c python -c "{PROBE_CODE}"' if platform == "win32" else ["python3", "-c", PROBE_CODE]


def capture_runtime(workspace: Path) -> dict[str, Any]:
    platform = sys.platform
    command = configured_command(platform)
    try:
        result = subprocess.run(probe_command(platform), cwd=workspace, text=True,
                                capture_output=True, timeout=10, check=False)
        exit_code, stdout, stderr = result.returncode, result.stdout, result.stderr
    except (OSError, subprocess.TimeoutExpired) as error:
        exit_code, stdout, stderr = -1, "", str(error)
    executable_hash = None
    if exit_code == 0:
        try:
            executable_hash = digest(Path(json.loads(stdout)["executable"]))
        except (ValueError, KeyError, OSError, TypeError):
            pass
    return {"schema_version": 1, "kind": "configured-hook-python", "platform": platform,
            "cwd": str(workspace.resolve()), "captured_at": datetime.now(timezone.utc).isoformat(),
            "hook_command": command, "hooks_config_sha256": digest(HOOK_CONFIG),
            "probe_command": probe_command(platform), "exit_code": exit_code,
            "stdout": stdout, "stderr": stderr, "executable_sha256": executable_hash,
            "remediation": REMEDIATION if exit_code != 0 or executable_hash is None else None}


def validate_runtime_capture(value: Any, environment: Any) -> bool:
    keys = {"schema_version", "kind", "platform", "cwd", "captured_at", "hook_command", "hooks_config_sha256",
            "probe_command", "exit_code", "stdout", "stderr", "executable_sha256", "remediation"}
    try:
        if not isinstance(value, dict) or set(value) != keys or not isinstance(environment, dict):
            return False
        if value["schema_version"] != 1 or value["kind"] != "configured-hook-python" or \
                value["platform"] not in ("win32", "linux", "darwin") or \
                value["hook_command"] != configured_command(value["platform"]) or \
                value["probe_command"] != probe_command(value["platform"]) or \
                value["hooks_config_sha256"] != digest(HOOK_CONFIG) or \
                type(value["exit_code"]) is not int or value["exit_code"] != 0 or \
                value["stderr"].strip() or value["remediation"] is not None:
            return False
        facts = json.loads(value["stdout"])
        runtime = Path(facts["executable"])
        return (set(facts) == {"executable", "version", "encodings_imported"} and
                facts["encodings_imported"] is True and runtime.is_absolute() and runtime.is_file() and
                str(runtime.resolve()) == str(Path(environment["resolved_python_executable"]).resolve()) and
                digest(runtime) == value["executable_sha256"] == environment["resolved_python_executable_sha256"] and
                facts["version"] == environment["python_version"] and
                tuple(int(part) for part in facts["version"].split(".")) >= (3, 9) and
                environment["python_encodings_import_exit_code"] == value["exit_code"] and
                datetime.fromisoformat(value["captured_at"]).tzinfo is not None and
                Path(value["cwd"]).is_dir())
    except (KeyError, ValueError, TypeError, OSError, AttributeError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    value = capture_runtime(args.workspace.resolve())
    # Preserve every attempt, including failure. A retry must use a fresh destination.
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
    if value["remediation"]:
        print(value["stderr"], file=sys.stderr)
        print(value["remediation"], file=sys.stderr)
        return 2
    print(f"Configured hook Python preflight captured: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
