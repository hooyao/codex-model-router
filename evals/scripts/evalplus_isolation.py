#!/usr/bin/env python3
"""Fresh per-arm CLI state and a fail-closed treatment-delivery preflight.

No ambient plugin installation is read. Provider transport/authentication is an
explicit, separate input, never an imported user behavioral configuration.
"""

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    from . import evalplus_runner as runner
    from . import evalplus_profile as profile
except ImportError:
    import evalplus_runner as runner
    import evalplus_profile as profile


TRANSPORT_FIELDS = {
    "name", "base_url", "wire_api", "env_key", "requires_openai_auth",
    "supports_websockets", "auth", "http_headers", "env_http_headers",
}
PROBE_PROMPT = (
    "This is a CLI environment diagnostic, not a business task. Do not use tools, "
    "delegate, or create files. Using only context already delivered, return one JSON "
    "object with keys skills (array of all available skill names), "
    "session_start_excerpt (quote the beginning of SessionStart hook context, or null), "
    "user_prompt_submit_excerpt (quote the beginning of UserPromptSubmit hook context, "
    "or null), routing_config_present (boolean), native_spawn_tool_name (string or null), "
    "native_model_effort_selection (boolean). Report absent context honestly. "
    "Do not infer hook delivery from skill descriptions."
)


def arm_environment(home, inherited=None):
    """Scope overrides to the child, including implicit ~/.agents discovery."""
    original = os.environ if inherited is None else inherited
    environment = {
        key: value for key, value in original.items()
        if not key.upper().startswith(("CODEX_", "AGENTS_", "CLAUDE_"))
        and key.upper() not in {"RUST_LOG", "PYTHONPATH", "PYTHONHOME"}
    }
    profile = home.parent / (home.name + "-profile")
    environment.update({
        "CODEX_HOME": str(home.resolve()),
        "CODEX_SQLITE_HOME": str(home.resolve()),
        "HOME": str(profile.resolve()),
        "USERPROFILE": str(profile.resolve()),
        "XDG_CONFIG_HOME": str((profile / ".config").resolve()),
        "XDG_DATA_HOME": str((profile / ".local/share").resolve()),
        "APPDATA": str((profile / "AppData/Roaming").resolve()),
        "LOCALAPPDATA": str((profile / "AppData/Local").resolve()),
        "PYTHONDONTWRITEBYTECODE": "1",
        # Plugin hooks call `python` on Windows. Use the interpreter that is
        # actually running this harness, without changing the host's PATH.
        "PATH": str(Path(sys.executable).parent) + os.pathsep + environment.get("PATH", ""),
    })
    return environment


def tree_hash(root):
    records = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or getattr(os.lstat(str(path)), "st_file_attributes", 0) & 0x400:
            raise runner.HarnessError("package links/reparse points are forbidden")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix not in (".pyc", ".pyo"):
            records[path.relative_to(root).as_posix()] = runner.sha256_file(path)
    if not records:
        raise runner.HarnessError("empty candidate package")
    return runner.sha256_bytes(json.dumps(records, sort_keys=True).encode())


def _toml_value(value):
    if isinstance(value, dict):
        return "{ " + ", ".join(json.dumps(k) + " = " + _toml_value(v) for k, v in value.items()) + " }"
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, (str, bool, int)):
        return json.dumps(value)
    raise runner.HarnessError("unsupported transport configuration value")


def clean_config(transport):
    if set(transport) != {"provider_id", "provider"}:
        raise runner.HarnessError("transport input may contain only provider_id and provider")
    provider_id = transport["provider_id"]
    provider = transport["provider"]
    if not isinstance(provider_id, str) or not re.fullmatch(r"[a-z0-9-]+", provider_id):
        raise runner.HarnessError("invalid provider ID")
    if not isinstance(provider, dict) or set(provider) - TRANSPORT_FIELDS:
        raise runner.HarnessError("transport contains unrelated provider settings")
    # Both HTTP and stream retries are disabled, including inherited workers.
    lines = [
        'model = "gpt-6-astra"', 'model_reasoning_effort = "xhigh"',
        'approval_policy = "never"', 'sandbox_mode = "workspace-write"',
        'web_search = "disabled"', 'project_doc_max_bytes = 0',
        'model_provider = ' + json.dumps(provider_id),
        '[features]', 'apps = false', 'remote_plugin = false',
        'memories = false',
        '[skills.bundled]', 'enabled = false',
        '[memories]', 'use_memories = false', 'generate_memories = false',
        '[agents]', 'max_depth = 1', 'max_threads = 2',
        '[model_providers.' + json.dumps(provider_id) + ']',
    ]
    lines.extend(json.dumps(key) + ' = ' + _toml_value(value) for key, value in provider.items())
    lines.extend(['request_max_retries = 0', 'stream_max_retries = 0'])
    return '\n'.join(lines) + '\n'


def candidate_hooks_config(home, installed_path):
    """Register the exact candidate program in the CLI's active user layer.

    CLI 0.144.1 lists plugin hooks but does not deliver them in codex exec.
    Preserve candidate event metadata; use the bounded benchmark adapter.
    CODEX_HOME-relative commands remain valid in fresh per-slot copies.
    """
    definitions = runner.read_json(installed_path / "hooks/hooks.json")["hooks"]
    events = {"SessionStart": "session_start", "UserPromptSubmit": "user_prompt_submit",
              "SubagentStart": "subagent_start"}
    if set(definitions) != set(events):
        raise runner.HarnessError("unexpected candidate lifecycle events")
    program = profile.PROFILE_DIRECTORY + "/hook.py"
    if not (installed_path / "hooks/router_hook.py").is_file():
        raise runner.HarnessError("candidate hook program missing")
    windows = "& '" + sys.executable.replace("'", "''") + "' (Join-Path $env:CODEX_HOME '" + program.replace("'", "''") + "')"
    posix = shlex.quote(sys.executable) + ' "$CODEX_HOME/' + program + '"'
    state = {}
    for event, groups in definitions.items():
        for group_index, group in enumerate(groups):
            for index, handler in enumerate(group["hooks"]):
                if handler.get("type") != "command" or handler.get("command") != 'python3 "$PLUGIN_ROOT/hooks/router_hook.py"':
                    raise runner.HarnessError("candidate hook command is not the reviewed program")
                handler["command"] = posix
                handler["commandWindows"] = windows
                key = "codex-model-router@evalplus-candidate:hooks/hooks.json:%s:%d:%d" % (events[event], group_index, index)
                state[key] = {"enabled": False}
    definitions["state"] = state
    return '\n[hooks]\n' + '\n'.join(key + ' = ' + _toml_value(value)
                                      for key, value in definitions.items()) + '\n'


def provision_profile(home, installed_path, catalog):
    directory = home / profile.PROFILE_DIRECTORY
    directory.mkdir(exist_ok=False)
    routes = profile.resolve_routes(catalog)
    runner.write_json(directory / "profile.json", {
        "schema_version": 1, "candidate_relative": installed_path.relative_to(home).as_posix(),
        "controller_model": runner.validate_manifest()["execution"]["model"],
        "resolved_routes": routes,
        "catalog_sha256": runner.sha256_bytes(json.dumps(catalog, sort_keys=True).encode()),
    })
    runner.write_json(directory / "routing.json", profile.minimal_routing(routes))
    shutil.copyfile(Path(profile.__file__), directory / "hook.py")
    # Render all events now; oversize or schema-invalid profiles cannot be installed.
    for event in profile.EVENTS:
        profile.build_output(directory, {"hook_event_name": event})
    return tree_hash(directory)


def capture(root, name, command, cwd, environment=None, prompt=None, timeout=120):
    root.mkdir(parents=True, exist_ok=True)
    raw = root / (name + ".jsonl")
    stderr = root / (name + ".stderr.txt")
    if raw.exists() or stderr.exists():
        raise runner.HarnessError("refusing to repeat/overwrite " + name)
    started = time.monotonic()
    status = {"argv": command, "cwd": str(cwd), "exit_code": None}
    runner.write_json(root / (name + ".command.json"), status)
    with raw.open("wb") as stdout, stderr.open("wb") as errors:
        try:
            process = subprocess.run(command, cwd=str(cwd), env=environment,
                                     input=None if prompt is None else prompt.encode(),
                                     stdout=stdout, stderr=errors, timeout=timeout)
            status["exit_code"] = process.returncode
        except subprocess.TimeoutExpired:
            status["timed_out"] = True
        except OSError as error:
            status["error"] = str(error)
    status.update({"elapsed_seconds": time.monotonic() - started,
                   "raw_sha256": runner.sha256_file(raw),
                   "stderr_sha256": runner.sha256_file(stderr)})
    runner.write_json(root / (name + ".status.json"), status)
    return status


def prepare_homes(state_root, candidate, transport_path, auth_path=None):
    state_root = runner.require_outside_workspace(state_root)
    if state_root.exists():
        raise runner.HarnessError("isolated state root must be new")
    config = clean_config(runner.read_json(transport_path))
    identity = runner.read_json(candidate / ".codex-plugin/plugin.json")
    if identity["name"] != "codex-model-router" or identity["version"] != "0.1.3":
        raise runner.HarnessError("candidate package identity mismatch")
    source_hash = tree_hash(candidate)
    state_root.mkdir(parents=True)
    homes = {arm: state_root / arm for arm in ("baseline", "router")}
    for arm, home in homes.items():
        home.mkdir()
        profile = home.parent / (home.name + "-profile")
        profile.mkdir()
        (home / "config.toml").write_text(config, encoding="utf-8")
        if auth_path is not None:
            shutil.copyfile(str(auth_path), str(home / "auth.json"))
    marketplace = state_root / "candidate-marketplace"
    snapshot = marketplace / "plugins/codex-model-router"
    shutil.copytree(str(candidate), str(snapshot), ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
    if tree_hash(snapshot) != source_hash:
        raise runner.HarnessError("candidate snapshot differs")
    runner.write_json(marketplace / ".agents/plugins/marketplace.json", {
        "name": "evalplus-candidate", "interface": {"displayName": "EvalPlus candidate"},
        "plugins": [{"name": identity["name"], "source": {"source": "local", "path": "./plugins/codex-model-router"},
                     "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}, "category": "Productivity"}],
    })
    environment = arm_environment(homes["router"])
    for name, command in (
        ("marketplace-add", ["codex", "plugin", "marketplace", "add", str(marketplace), "--json"]),
        ("plugin-add", ["codex", "plugin", "add", "codex-model-router@evalplus-candidate", "--json"]),
    ):
        result = capture(state_root / "setup", name, command, state_root, environment)
        if result["exit_code"] != 0:
            raise runner.HarnessError("isolated plugin installation failed: " + name)
    installed = runner.read_json(state_root / "setup/plugin-add.jsonl")
    installed_path = Path(installed["installedPath"]).resolve()
    if not runner._is_relative_to(installed_path, homes["router"].resolve()):
        raise runner.HarnessError("installed package escaped isolated router home")
    if tree_hash(installed_path) != source_hash:
        raise runner.HarnessError("installed package does not match exact candidate")
    catalog_status = capture(state_root / "setup", "model-catalog",
                             ["codex", "debug", "models", "--bundled"], state_root, environment)
    if catalog_status["exit_code"] != 0:
        raise runner.HarnessError("cannot read the offline CLI model catalog")
    profile_hash = provision_profile(homes["router"], installed_path,
                                     runner.read_json(state_root / "setup/model-catalog.jsonl"))
    config_path = homes["router"] / "config.toml"
    config_path.write_text(config_path.read_text(encoding="utf-8")
                           + candidate_hooks_config(homes["router"], installed_path), encoding="utf-8")
    record = {
        "schema_version": 1, "candidate_path": str(candidate.resolve()),
        "candidate_sha256": source_hash, "installed_path": str(installed_path),
        "homes": {arm: str(home) for arm, home in homes.items()},
        "config_sha256": {arm: runner.sha256_file(home / "config.toml") for arm, home in homes.items()},
        "transport_sha256": runner.sha256_file(transport_path),
        "profile_sha256": profile_hash,
    }
    runner.write_json(state_root / "isolation.json", record)
    return record


def validate_homes(homes):
    if homes["baseline"].parent != homes["router"].parent or homes["baseline"] == homes["router"]:
        raise runner.HarnessError("homes must be separate siblings in an isolated state root")
    record = runner.read_json(homes["baseline"].parent / "isolation.json")
    for arm, home in homes.items():
        if str(home.resolve()) != record["homes"][arm]:
            raise runner.HarnessError("isolated home identity changed")
        if runner.sha256_file(home / "config.toml") != record["config_sha256"][arm]:
            raise runner.HarnessError("isolated home config changed")
        for relative in ("AGENTS.md", "AGENTS.override.md", "hooks.json"):
            if (home / relative).exists():
                raise runner.HarnessError("unexpected user context in isolated home: " + relative)
        if any((home / "skills").rglob("SKILL.md")) or any((home / "memories").rglob("*.md")):
            raise runner.HarnessError("unexpected user skill or memory")
        user_profile = home.parent / (home.name + "-profile")
        if any(user_profile.rglob("SKILL.md")) or any(user_profile.rglob("AGENTS.md")):
            raise runner.HarnessError("unexpected user profile instructions")
    if tree_hash(Path(record["installed_path"])) != record["candidate_sha256"]:
        raise runner.HarnessError("installed candidate changed since setup")
    if (homes["baseline"] / profile.PROFILE_DIRECTORY).exists():
        raise runner.HarnessError("baseline contains a benchmark router profile")
    directory = homes["router"] / profile.PROFILE_DIRECTORY
    if not directory.is_dir() or tree_hash(directory) != record.get("profile_sha256"):
        raise runner.HarnessError("benchmark router profile missing or changed")
    if runner.sha256_file(directory / "hook.py") != runner.sha256_file(Path(profile.__file__)):
        raise runner.HarnessError("benchmark adapter changed; reprovision the isolated homes")
    return record


def validate_runtime_flags(manifest):
    """Documentation can be newer than the installed CLI. Check before a probe."""
    result = subprocess.run(["codex", "features", "list"], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise runner.HarnessError("cannot discover CLI feature flags")
    available = {line.split()[0] for line in result.stdout.splitlines() if line.split()}
    for arm in ("baseline", "router"):
        command = runner.build_codex_command(manifest, {"variant": arm}, Path.cwd())
        required = {command[index + 1] for index, arg in enumerate(command[:-1]) if arg in ("--enable", "--disable")}
        if required - available:
            raise runner.HarnessError("installed CLI lacks flags: " + repr(sorted(required - available)))


def check_grader(campaign_root, image):
    """Import the pinned grader in a locked-down container; never run a sample."""
    evidence = campaign_root / "grader-preflight"
    inspect = capture(evidence, "image", ["docker", "image", "inspect", image,
                                        "--format", "{{json .Id}}"], campaign_root)
    if inspect["exit_code"] != 0:
        raise runner.HarnessError("Docker grader image unavailable")
    image_id = runner.read_json(evidence / "image.jsonl")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
        raise runner.HarnessError("Docker image identity is not immutable")
    command = [
        "docker", "run", "--rm", "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--user", "65534:65534", "--memory", "2g", "--cpus", "2",
        "--pids-limit", "64", "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m",
        image_id, "python", "-B", "-c",
        "import importlib.metadata; from evalplus.evaluate import check_correctness; "
        "assert importlib.metadata.version('evalplus') == '0.3.1'; print('evalplus-0.3.1-ready')",
    ]
    status = capture(evidence, "import", command, campaign_root)
    ready = status["exit_code"] == 0 and (evidence / "import.jsonl").read_text().strip() == "evalplus-0.3.1-ready"
    receipt = {"passed": ready, "backend": "docker", "image_id": image_id,
               "generated_code_executed": False,
               "raw_sha256": runner.sha256_file(evidence / "import.jsonl"),
               "status_sha256": runner.sha256_file(evidence / "import.status.json")}
    runner.write_json(campaign_root / "grader-preflight.json", receipt)
    return receipt


def require_grader_receipt(campaign_root):
    receipt = runner.read_json(campaign_root / "grader-preflight.json")
    if receipt.get("passed") is not True or receipt.get("backend") != "docker":
        raise runner.HarnessError("isolated grader preflight did not pass")
    evidence = campaign_root / "grader-preflight"
    if (receipt.get("raw_sha256") != runner.sha256_file(evidence / "import.jsonl")
            or receipt.get("status_sha256") != runner.sha256_file(evidence / "import.status.json")):
        raise runner.HarnessError("grader evidence changed")
    status = runner.read_json(evidence / "import.status.json")
    if status.get("exit_code") != 0 or receipt["image_id"] not in status.get("argv", []):
        raise runner.HarnessError("grader receipt does not match successful execution")
    result = subprocess.run(["docker", "image", "inspect", receipt["image_id"]],
                            capture_output=True, timeout=30)
    if result.returncode:
        raise runner.HarnessError("preflighted Docker image is no longer available")


def model_messages(raw_path):
    events = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    messages = [e["item"]["text"] for e in events if e.get("type") == "item.completed"
                and e.get("item", {}).get("type") == "agent_message"]
    if not messages:
        raise runner.HarnessError("preflight has no model-visible evidence report")
    text = messages[-1].strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    return json.loads(text)


def validate_probe_report(arm, report):
    skills = report.get("skills")
    if not isinstance(skills, list):
        raise runner.HarnessError("missing preflight skill inventory")
    if arm == "baseline":
        if skills or report.get("session_start_excerpt") or report.get("user_prompt_submit_excerpt") or report.get("routing_config_present"):
            raise runner.HarnessError("baseline received plugin/skill/hook context")
    else:
        if not skills or any(not name.startswith("codex-model-router:") for name in skills):
            raise runner.HarnessError("router skill catalog missing or contaminated")
        if not any(name.endswith(":model-router") for name in skills):
            raise runner.HarnessError("router model-router skill missing")
        if "Active controller model:" not in (report.get("session_start_excerpt") or ""):
            raise runner.HarnessError("router SessionStart context not observed")
        if "CONTROLLER ROLE ONLY" not in (report.get("user_prompt_submit_excerpt") or ""):
            raise runner.HarnessError("router UserPromptSubmit context not observed")
        if report.get("routing_config_present") is not True:
            raise runner.HarnessError("router routing config context not observed")
        if not report.get("native_spawn_tool_name") or report.get("native_model_effort_selection") is not True:
            raise runner.HarnessError("native model-aware worker dispatch unavailable")


def run_preflight(manifest, campaign_root, homes):
    # Registration and both lifecycle deliveries must pass before a paid probe.
    try:
        from .evalplus_hooks import require_hook_receipt
    except ImportError:
        from evalplus_hooks import require_hook_receipt
    require_hook_receipt(campaign_root, homes)
    record = validate_homes(homes)
    validate_runtime_flags(manifest)
    evidence = campaign_root / "treatment-preflight"
    evidence.mkdir(parents=True, exist_ok=False)
    reports, errors = {}, []
    # Exactly one attempt per arm. A failing arm cannot trigger model retries.
    for arm, home in homes.items():
        task = evidence / (arm + "-workspace")
        task.mkdir()
        subprocess.run(["git", "init", "--quiet"], cwd=str(task), check=True)
        command = runner.build_codex_command(manifest, {"variant": arm}, task)
        status = capture(evidence, arm, command, task, arm_environment(home), PROBE_PROMPT, 240)
        try:
            if status["exit_code"] != 0:
                raise runner.HarnessError("preflight CLI did not complete")
            report = model_messages(evidence / (arm + ".jsonl"))
            reports[arm] = report
            validate_probe_report(arm, report)
        except (runner.HarnessError, ValueError) as error:
            errors.append(arm + ": " + str(error))
    receipt = {
        "passed": not errors, "errors": errors, "reports": reports,
        "isolation_sha256": runner.sha256_file(homes["baseline"].parent / "isolation.json"),
        "candidate_sha256": record["candidate_sha256"],
        "command_sha256": runner.sha256_file(Path(runner.__file__)),
        "preflight_code_sha256": runner.sha256_file(Path(__file__)),
        "evidence_sha256": {p.name: runner.sha256_file(p) for p in evidence.glob("*.jsonl")},
    }
    runner.write_json(campaign_root / "treatment-preflight.json", receipt)
    return receipt


def require_treatment_receipt(campaign_root, manifest, homes):
    try:
        from .evalplus_hooks import require_hook_receipt
    except ImportError:
        from evalplus_hooks import require_hook_receipt
    require_hook_receipt(campaign_root, homes)
    record = validate_homes(homes)
    receipt = runner.read_json(campaign_root / "treatment-preflight.json")
    if receipt.get("passed") is not True:
        raise runner.HarnessError("treatment preflight did not pass")
    for arm in homes:
        raw = campaign_root / "treatment-preflight" / (arm + ".jsonl")
        if model_messages(raw) != receipt["reports"][arm]:
            raise runner.HarnessError("preflight report differs from saved model output")
        validate_probe_report(arm, receipt["reports"][arm])
    expected = {
        "isolation_sha256": runner.sha256_file(homes["baseline"].parent / "isolation.json"),
        "candidate_sha256": record["candidate_sha256"],
        "command_sha256": runner.sha256_file(Path(runner.__file__)),
        "preflight_code_sha256": runner.sha256_file(Path(__file__)),
    }
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise runner.HarnessError("treatment preflight binding changed")
    for name, digest in receipt["evidence_sha256"].items():
        if Path(name).name != name or runner.sha256_file(campaign_root / "treatment-preflight" / name) != digest:
            raise runner.HarnessError("treatment preflight evidence changed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("prepare-homes")
    setup.add_argument("--state-root", required=True, type=Path)
    setup.add_argument("--candidate", required=True, type=Path)
    setup.add_argument("--transport", required=True, type=Path)
    setup.add_argument("--auth", type=Path)
    probe = commands.add_parser("preflight")
    probe.add_argument("--campaign-root", required=True, type=Path)
    probe.add_argument("--state-root", required=True, type=Path)
    grader = commands.add_parser("check-grader")
    grader.add_argument("--campaign-root", required=True, type=Path)
    grader.add_argument("--image", required=True)
    args = parser.parse_args()
    if args.command == "prepare-homes":
        result = prepare_homes(args.state_root, args.candidate, args.transport, args.auth)
    elif args.command == "preflight":
        result = run_preflight(runner.validate_manifest(), args.campaign_root,
                               {arm: args.state_root / arm for arm in ("baseline", "router")})
    else:
        result = check_grader(args.campaign_root, args.image)
    print(json.dumps(result, indent=2))
    return 0 if result.get("passed", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
