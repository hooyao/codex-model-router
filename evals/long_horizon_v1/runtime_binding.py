"""Fail-closed CLI and routing-config bindings for the native benchmark."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from .common import HERE, file_sha

CONFIG_RELATIVE = Path(".codex-model-router") / "routing.json"
ARM_NAMES = ("baseline", "treatment")


def _pinned_file(path: Path, expected: str, label: str) -> None:
    if not path.is_file() or file_sha(path) != expected:
        raise ValueError(f"{label} missing or SHA-256 mismatch: {path}")


def _plugin_files(spec: dict) -> tuple[Path, Path]:
    runtime = spec["runtime"]
    plugin = Path(runtime["plugin_root"])
    _pinned_file(plugin / ".codex-plugin" / "plugin.json",
                 runtime["plugin_manifest_sha256"], "pinned plugin manifest")
    hook = plugin / "hooks" / "router_hook.py"
    validator = plugin / "hooks" / "routing_config.py"
    _pinned_file(hook, runtime["router_hook_sha256"], "pinned router hook")
    _pinned_file(validator, runtime["routing_validator_sha256"], "pinned routing validator")
    _pinned_file(plugin / "hooks" / "hooks.json", runtime["plugin_hooks_sha256"],
                 "pinned plugin hooks")
    return hook, validator


def arm_execution(spec: dict, arm: str) -> dict:
    """Return a frozen arm policy and suffix, independent of the shared task text."""
    if arm not in ARM_NAMES:
        raise ValueError("unknown benchmark arm")
    descriptor = spec["arm_execution"]
    source = HERE / descriptor["path"]
    _pinned_file(source, descriptor["sha256"], "versioned arm execution policy")
    document = json.loads(source.read_text(encoding="utf-8"))
    if document.get("schema_version") != descriptor["schema_version"]:
        raise ValueError("arm execution schema mismatch")
    policy = document[arm]
    suffix = HERE / policy["prompt_suffix"]
    _pinned_file(suffix, descriptor["prompt_suffix_sha256"][arm],
                 "versioned arm prompt suffix")
    expected = (("gpt-6-astra", "xhigh", False) if arm == "baseline"
                else ("gpt-6-sol", "low", True))
    if (policy.get("model"), policy.get("effort"),
            policy.get("native_delegation_enabled")) != expected:
        raise ValueError("arm selector or delegation policy drift")
    options = policy.get("cli_options")
    if not isinstance(options, list) or not all(isinstance(item, str) for item in options):
        raise ValueError("arm CLI options invalid")
    expected_switches = (["--disable", "plugins", "--disable", "hooks",
                          "--disable", "multi_agent"] if arm == "baseline" else
                         ["--enable", "plugins", "--enable", "hooks",
                          "--enable", "multi_agent"])
    if (options[:1] != ["--strict-config"] or
            options[1:7] != expected_switches or
            policy.get("router_enabled") is not (arm == "treatment") or
            (arm == "baseline" and "enabled=false" not in " ".join(options)) or
            (arm == "treatment" and
             "expose_spawn_agent_model_overrides=true" not in " ".join(options))):
        raise ValueError("arm feature policy drift")
    return {**policy, "policy_sha256": descriptor["sha256"],
            "prompt_suffix_sha256": descriptor["prompt_suffix_sha256"][arm],
            "prompt_suffix_text": suffix.read_text(encoding="utf-8").rstrip("\n")}


def verify_arm_runtime(cli: Path, workspace: Path, spec: dict, arm: str) -> dict:
    """Inspect effective App Server config and hooks without starting a model turn."""
    from .transport import AppServerTransport
    import time
    policy = arm_execution(spec, arm)
    transport = AppServerTransport(cli, workspace, policy["cli_options"],
                                   policy["model"], policy["effort"], time.monotonic() + 30)
    try:
        init_id = transport._send("initialize", {"clientInfo": {
            "name": "long-horizon-arm-binding", "version": "1"},
            "capabilities": {"experimentalApi": True}})
        transport._response(init_id)
        transport._send("initialized", {}, notification=True)
        config_id = transport._send("config/read", {"cwd": str(workspace.resolve()),
                                                    "includeLayers": False})
        effective = transport._response(config_id)
        hooks_id = transport._send("hooks/list", {"cwds": [str(workspace.resolve())]})
        inventory = transport._response(hooks_id)
        catalog_id = transport._send("model/list", {"includeHidden": True})
        catalog = transport._response(catalog_id)
        thread_id = transport._send("thread/start", {"cwd": str(workspace.resolve()),
                                                     "model": policy["model"],
                                                     "sandbox": "danger-full-access",
                                                     "approvalPolicy": "never", "ephemeral": False})
        started = transport._response(thread_id)
    finally:
        transport.close()
    features = (effective.get("config") or {}).get("features") or {}
    expected_enabled = arm == "treatment"
    if any(features.get(name) is not expected_enabled
           for name in ("plugins", "hooks", "multi_agent")):
        raise ValueError("effective arm feature flags differ from frozen policy")
    v2 = features.get("multi_agent_v2") or {}
    if (v2.get("enabled") is not expected_enabled or
            v2.get("expose_spawn_agent_model_overrides") is not expected_enabled):
        raise ValueError("effective native delegation selector exposure differs")
    data = inventory.get("data")
    if not isinstance(data, list) or len(data) != 1 or data[0].get("errors") or data[0].get("warnings"):
        raise ValueError("arm hook inventory incomplete")
    if Path(data[0].get("cwd", "")).resolve() != workspace.resolve():
        raise ValueError("arm hook inventory inspected wrong workspace")
    thread = started.get("thread") or {}
    if (started.get("model") not in (None, policy["model"]) or
            not isinstance(thread.get("id"), str) or not thread["id"]):
        raise ValueError("arm thread start model or identity mismatch")
    router = [item for item in data[0].get("hooks", [])
              if item.get("pluginId") == "codex-model-router@personal"]
    if arm == "baseline":
        if router:
            raise ValueError("baseline loaded router hooks")
    else:
        expected_events = {"sessionStart", "userPromptSubmit", "subagentStart",
                           "preToolUse", "postToolUse"}
        hook_file = Path(spec["runtime"]["plugin_root"]) / "hooks" / "hooks.json"
        if (len(router) != len(expected_events) or
                {item.get("eventName") for item in router} != expected_events or
                any(item.get("enabled") is not True or item.get("trustStatus") != "trusted"
                    or Path(item.get("sourcePath", "")).resolve() != hook_file.resolve()
                    for item in router)):
            raise ValueError("treatment router hooks absent or untrusted")
    models = catalog.get("data")
    if not isinstance(models, list):
        raise ValueError("App Server model catalog missing")
    required = [(policy["model"], policy["effort"])]
    if arm == "treatment":
        required.append(("gpt-6-astra", "xhigh"))
    selected = {}
    for name, effort in required:
        matches = [row for row in models if isinstance(row, dict) and row.get("id") == name]
        if len(matches) != 1 or effort not in [entry.get("reasoningEffort")
                for entry in matches[0].get("supportedReasoningEfforts", [])
                if isinstance(entry, dict)]:
            raise ValueError("required model or reasoning effort absent from runtime catalog")
        selected[name] = effort
    protocol = verify_protocol_contract(cli, spec)
    return {"arm": arm, "cli_options": policy["cli_options"],
            "policy_sha256": policy["policy_sha256"],
            "prompt_suffix_sha256": policy["prompt_suffix_sha256"],
            "effective_features": {name: features.get(name) for name in
                                   ("plugins", "hooks", "multi_agent", "multi_agent_v2")},
            "router_hook_count": len(router), "thread_started_without_turn": True,
            "model_turns": 0, "model_catalog": selected,
            "protocol_contract": protocol}


def verify_protocol_contract(cli: Path, spec: dict) -> dict:
    """Check the pinned binary's generated no-turn RPC and native dispatch contracts."""
    _plugin_files(spec)
    hooks = Path(spec["runtime"]["plugin_root"]) / "hooks"
    dispatch = hooks / "dispatch_contract.py"
    if not dispatch.is_file():
        raise ValueError("installed native dispatch contract missing")
    closure = {path.name: file_sha(path) for path in hooks.glob("*.py") if path.is_file()}
    if not {"router_hook.py", "routing_config.py", "dispatch_audit.py",
            "dispatch_contract.py", "subagent_naming.py", "execution_decision.py"} <= set(closure):
        raise ValueError("installed router hook closure incomplete")
    closure_hash = hashlib.sha256(json.dumps(closure, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    with tempfile.TemporaryDirectory() as directory:
        destination = Path(directory)
        result = subprocess.run([str(cli), "app-server", "generate-json-schema",
                                 "--out", str(destination)], capture_output=True,
                                text=True, encoding="utf-8", timeout=30)
        source = destination / "ClientRequest.json"
        if result.returncode or not source.is_file():
            raise ValueError("pinned CLI protocol schema unavailable")
        document = json.loads(source.read_text(encoding="utf-8"))
        variants = document.get("oneOf")
        if not isinstance(variants, list):
            raise ValueError("pinned CLI request schema invalid")
        methods = {method for variant in variants if isinstance(variant, dict)
                   for method in ((variant.get("properties") or {}).get("method") or {}).get("enum", [])}
        if not {"initialize", "thread/start", "turn/start", "turn/interrupt",
                "model/list", "config/read", "hooks/list"} <= methods:
            raise ValueError("pinned CLI lacks required App Server methods")
        interrupt = (document.get("definitions") or {}).get("TurnInterruptParams") or {}
        if not {"threadId", "turnId"} <= set(interrupt.get("required") or []):
            raise ValueError("pinned CLI interrupt request contract changed")
        return {"client_request_sha256": file_sha(source),
                "dispatch_contract_sha256": file_sha(dispatch),
                "installed_hook_closure_sha256": closure_hash,
                "interrupt_method": "turn/interrupt"}


def _validated_config(path: Path, spec: dict) -> tuple[dict, str]:
    _, validator = _plugin_files(spec)
    module_spec = importlib.util.spec_from_file_location("benchmark_routing_validator", validator)
    if module_spec is None or module_spec.loader is None:
        raise ValueError("pinned routing validator cannot be loaded")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    config = module.load_config(path)
    canonical = module.serialized_config(config, str(path)).encode("utf-8")
    return config, hashlib.sha256(canonical).hexdigest()


def fixture_config(spec: dict) -> dict:
    descriptor = spec["routing_config"]
    source = HERE / descriptor["path"]
    _pinned_file(source, descriptor["raw_sha256"], "versioned routing fixture")
    config, digest = _validated_config(source, spec)
    if config.get("schema_version") != descriptor["schema_version"] or digest != descriptor["canonical_sha256"]:
        raise ValueError("versioned routing fixture schema or canonical SHA-256 mismatch")
    return descriptor


def bind_arm_config(workspace: Path, spec: dict) -> dict:
    """Install only into a newly prepared arm; never replace an existing config."""
    descriptor = fixture_config(spec)
    target = workspace / CONFIG_RELATIVE
    if target.exists():
        raise ValueError(f"arm routing config already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(HERE / descriptor["path"], target)
    exclude = workspace / ".git" / "info" / "exclude"
    with exclude.open("a", encoding="utf-8") as stream:
        stream.write("\n.codex-model-router/\n")
    return verify_arm_config(workspace, spec)


def verify_arm_config(workspace: Path, spec: dict) -> dict:
    """Check the arm config and the actual installed hook's selected path/digest."""
    descriptor = fixture_config(spec)
    target = workspace / CONFIG_RELATIVE
    _pinned_file(target, descriptor["raw_sha256"], "arm routing config")
    _, digest = _validated_config(target, spec)
    if digest != descriptor["canonical_sha256"]:
        raise ValueError("arm routing config canonical SHA-256 mismatch")
    hook, _ = _plugin_files(spec)
    event = {"hook_event_name": "SessionStart", "cwd": str(workspace.resolve())}
    result = subprocess.run([sys.executable, str(hook)], input=json.dumps(event),
                            capture_output=True, text=True, encoding="utf-8", timeout=15)
    if result.returncode:
        raise ValueError("pinned SessionStart hook binding failed: " + result.stderr[-500:])
    try:
        output = json.loads(result.stdout)
        context = output["hookSpecificOutput"]["additionalContext"]
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError("pinned SessionStart hook output invalid") from error
    path_match = re.search(r"^Workspace routing config: (.+)$", context, re.MULTILINE)
    hash_match = re.search(r"^Validated config SHA256 \(canonical JSON\): ([0-9a-f]{64})$",
                           context, re.MULTILINE)
    if (path_match is None or hash_match is None or
            Path(path_match.group(1)).resolve() != target.resolve() or
            hash_match.group(1) != digest):
        raise ValueError("SessionStart hook selected a different routing config or digest")
    return {"path": str(target.resolve()), "raw_sha256": descriptor["raw_sha256"],
            "canonical_sha256": digest, "hook_verified": True}


def verify_cli(cli: Path, spec: dict) -> dict:
    """Pin the complete desktop installation, including the code-mode sidecar."""
    runtime = spec["runtime"]
    expected_cli = Path(runtime["cli"])
    if cli.resolve() != expected_cli.resolve():
        raise ValueError("CLI must be the pinned complete desktop installation")
    host = cli.with_name("codex-code-mode-host.exe")
    _pinned_file(cli, runtime["cli_sha256"], "pinned desktop CLI")
    _pinned_file(host, runtime["code_mode_host_sha256"], "pinned code-mode host")
    version = subprocess.run([str(cli), "--version"], capture_output=True,
                             text=True, encoding="utf-8", timeout=10)
    if version.returncode or version.stdout.strip() != runtime["cli_version"]:
        raise ValueError("pinned desktop CLI version mismatch")
    return {"cli": str(cli.resolve()), "cli_sha256": runtime["cli_sha256"],
            "code_mode_host": str(host.resolve()),
            "code_mode_host_sha256": runtime["code_mode_host_sha256"],
            "cli_version": runtime["cli_version"]}
