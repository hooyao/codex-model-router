#!/usr/bin/env python3
"""Verify CLI lifecycle delivery against a rejecting loopback sink, never a model."""

import argparse
import json
import queue
import re
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from . import evalplus_isolation as isolation
    from . import evalplus_runner as runner
    from . import evalplus_live as live
    from . import evalplus_profile as profile
    from . import evalplus_write_gate as write_gate
    from . import evalplus_workspace as workspace_editor
except ImportError:
    import evalplus_isolation as isolation
    import evalplus_runner as runner
    import evalplus_live as live
    import evalplus_profile as profile
    import evalplus_write_gate as write_gate
    import evalplus_workspace as workspace_editor


def offline_overrides(port, provider_id="copilot-bridge"):
    # Keep the campaign provider identity/protocol while replacing its transport
    # with a credential-free sink. No request is forwarded to the real bridge.
    if not re.fullmatch(r"[a-z0-9-]+", provider_id):
        raise runner.HarnessError("invalid offline provider ID")
    provider = {"name": "Rejecting offline hook probe", "base_url": "http://127.0.0.1:%d" % port,
                "wire_api": "responses", "requires_openai_auth": False,
                "supports_websockets": False, "request_max_retries": 0, "stream_max_retries": 0}
    return ["--config", 'model_provider=' + json.dumps(provider_id), "--config",
            "model_providers." + provider_id + "=" + isolation._toml_value(provider),
            "--disable", "remote_models", "--disable", "enable_request_compression"]


def registry(home, workspace, arm):
    """Use read-only app-server RPCs; never start a thread or turn."""
    command = ["codex", "app-server"] + offline_overrides(1)
    for feature in ("plugins", "hooks", "multi_agent"):
        command += ["--enable" if arm == "router" else "--disable", feature]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, encoding="utf-8",
                               cwd=str(workspace), env=isolation.arm_environment(home))
    lines, errors = queue.Queue(), []
    readers = [threading.Thread(target=lambda: [lines.put(line) for line in process.stdout], daemon=True),
               threading.Thread(target=lambda: errors.extend(process.stderr), daemon=True)]
    for reader in readers:
        reader.start()

    def request(identifier, method, params):
        process.stdin.write(json.dumps({"id": identifier, "method": method, "params": params}) + "\n")
        process.stdin.flush()
        while True:
            try:
                message = json.loads(lines.get(timeout=30))
            except queue.Empty:
                raise runner.HarnessError("hook registry RPC timed out")
            if message.get("id") == identifier:
                if "error" in message:
                    raise runner.HarnessError("hook registry RPC failed: " + str(message["error"]))
                return message["result"]

    try:
        request(1, "initialize", {"clientInfo": {"name": "evalplus_hook_probe", "version": "1"},
                                  "capabilities": {"experimentalApi": True}})
        process.stdin.write('{"method":"initialized","params":{}}\n')
        process.stdin.flush()
        hooks = request(2, "hooks/list", {"cwds": [str(workspace)]})
        config = request(3, "config/read", {"cwd": str(workspace), "includeLayers": False})
        return {"hooks": hooks, "features": config["config"].get("features"), "stderr": errors}
    finally:
        process.terminate()
        process.wait(timeout=10)
        for reader in readers:
            reader.join(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()


def capture_request(manifest, home, workspace, arm, evidence, variant=None):
    requests = []

    class Sink(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            requests.append({"path": self.path, "body": json.loads(payload)})
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"offline hook probe: no model exists"}}')

        def do_GET(self):
            self.send_response(404)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Sink)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        command = runner.build_codex_command(manifest, {"variant": variant or arm}, workspace)
        command[-1:-1] = offline_overrides(server.server_port)
        status = isolation.capture(evidence, arm, command, workspace, isolation.arm_environment(home),
                                   "Offline hook delivery probe.", 60)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    runner.write_json(evidence / (arm + ".request.json"), requests)
    return status, requests


def validate_primary_request(body):
    """Configuration intent is insufficient: check the actual Responses body."""
    reasoning = body.get("reasoning")
    if body.get("model") != "gpt-6-astra" or not isinstance(reasoning, dict) or reasoning.get("effort") != "xhigh":
        raise runner.HarnessError("primary request must explicitly carry gpt-6-astra and reasoning.effort=xhigh")
    return {"model": body["model"], "reasoning_effort": reasoning["effort"]}


def validate_delivery(arm, registered, requests, status, home, identity):
    """Require runtime registration, explicit trust activation, and two messages."""
    if len(requests) != 1 or requests[0].get("path") != "/responses" or status.get("exit_code") != 1:
        raise runner.HarnessError("expected exactly one rejected loopback request")
    entries = registered["hooks"]["data"]
    if len(entries) != 1 or entries[0]["errors"] or entries[0]["warnings"]:
        raise runner.HarnessError("hook registry has errors or warnings")
    hooks = entries[0]["hooks"]
    active = [h for h in hooks if h["enabled"] and not h["isManaged"]]
    body = requests[0]["body"]
    primary_request = validate_primary_request(body)
    capability = write_gate.validate_capability(body, Path(status["cwd"]))
    developer = ["\n".join(c.get("text", "") for c in m.get("content", []))
                 for m in body.get("input", []) if m.get("role") == "developer"]
    contexts = [text for text in developer if "CONTROLLER ROLE ONLY:" in text]
    skills = re.findall(r"^- ([\w:-]+): .*?\(file:", "\n".join(developer), re.MULTILINE)
    if arm == "baseline":
        if (active or skills or contexts or any(h.get("source") == "plugin" for h in hooks)
                or any("ROUTING_CONFIG_" in text or "WORKER ROLE OVERRIDE" in text for text in developer)):
            raise runner.HarnessError("baseline has candidate or ambient hook/skill context")
        return {"events": [], "skills": [], "registered_hooks": [], "primary_request": primary_request,
                "write_capability": capability}
    expected = {"sessionStart", "userPromptSubmit", "subagentStart"}
    if len(active) != 3 or {h["eventName"] for h in active} != expected:
        raise runner.HarnessError("candidate hooks are not explicitly registered")
    relative = Path(identity["installed_path"]).relative_to(Path(identity["homes"]["router"])).as_posix()
    if isolation.tree_hash(home / relative) != identity["candidate_sha256"]:
        raise runner.HarnessError("probe candidate differs from the isolated snapshot")
    directory = home / profile.PROFILE_DIRECTORY
    if isolation.tree_hash(directory) != identity["profile_sha256"]:
        raise runner.HarnessError("probe profile differs from the isolated snapshot")
    for hook in active:
        if (hook["source"] != "user" or Path(hook["sourcePath"]).resolve() != (home / "config.toml").resolve()
                or profile.PROFILE_DIRECTORY + "/hook.py" not in hook["command"]
                or sys.executable not in hook["command"] or not hook["currentHash"]):
            raise runner.HarnessError("active hook is not the isolated candidate program")
    if "--dangerously-bypass-hook-trust" not in status["argv"]:
        raise runner.HarnessError("explicit invocation hook trust is absent")
    if set(skills) != {"codex-model-router:initialize-router", "codex-model-router:model-router"}:
        raise runner.HarnessError("candidate skill inventory missing or contaminated")
    session = [text for text in contexts if "Codex Model Router\nActive controller model:" in text]
    prompt = [text for text in contexts if "Active controller model:" not in text]
    if len(contexts) != 2 or len(session) != 1 or len(prompt) != 1:
        raise runner.HarnessError("distinct SessionStart and UserPromptSubmit context not delivered")
    try:
        complete = [profile.parse_context(session[0], directory, "SessionStart"),
                    profile.parse_context(prompt[0], directory, "UserPromptSubmit")]
    except ValueError as error:
        raise runner.HarnessError("critical context gate failed: " + str(error))
    dispatch = [tool for namespace in body.get("tools", []) for tool in namespace.get("tools", [])
                if tool.get("name") == "spawn_agent"]
    if len(dispatch) != 1 or not {"model", "reasoning_effort"}.issubset(dispatch[0]["parameters"]["properties"]):
        raise runner.HarnessError("native model/effort dispatch schema absent")
    metadata, _, _ = profile.load_profile(directory)
    for route in metadata["resolved_routes"].values():
        match = re.search(r"- `" + re.escape(route["model"]) + r"`:.*?Reasoning efforts: ([^\n]+)",
                          dispatch[0].get("description", ""))
        if match is None or not re.search(r"\b" + re.escape(route["effort"]) + r"\b", match[1]):
            raise runner.HarnessError("resolved route unavailable in the captured native spawn schema")
    return {"events": ["SessionStart", "UserPromptSubmit"], "skills": skills,
            "primary_request": primary_request,
            "write_capability": capability,
            "registered_hooks": [{key: h[key] for key in ("key", "eventName", "currentHash", "trustStatus")}
                                 for h in active],
            "trust_activation": "--dangerously-bypass-hook-trust",
            "context_sha256": [runner.sha256_bytes(text.encode()) for text in contexts],
            "critical_context": complete,
            "context_spilled": False,
            "native_model_effort_selection": True}


def simulate_worker(manifest, home, identity, evidence):
    """Deliver the real SubagentStart output through the CLI's context injector.

    Only the event carrier is remapped to SessionStart; no agent is dispatched.
    The adapter renders the unchanged candidate worker contract and routing JSON.
    """
    destination = live.clone_slot_home(home, evidence / "homes/worker-simulation", "router", identity, include_auth=False)
    workspace = evidence / "worker-simulation-workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=workspace, check=True)
    original = runner.read_json(evidence / "router.registry.json")
    handler = next(h for h in original["hooks"]["data"][0]["hooks"]
                   if h["enabled"] and not h["isManaged"] and h["eventName"] == "subagentStart")
    command = handler["command"] + " --simulate-subagent-start"
    config = isolation.clean_config({"provider_id": "simulation", "provider": {
        "name": "Offline worker simulation", "base_url": "http://127.0.0.1:1", "wire_api": "responses",
        "requires_openai_auth": False}})
    config += '\n[plugins."codex-model-router@evalplus-candidate"]\nenabled = false\n'
    config += '\n[hooks]\nSessionStart = ' + isolation._toml_value([{"hooks": [{
        "type": "command", "command": command, "timeout": 10}]}]) + '\n'
    (destination / "config.toml").write_text(config, encoding="utf-8")
    registered = registry(destination, workspace, "router")
    runner.write_json(evidence / "worker-simulation.registry.json", registered)
    status, requests = capture_request(manifest, destination, workspace, "worker-simulation", evidence, variant="router")
    runner.write_json(evidence / "worker-simulation.event.json", {
        "event": "SubagentStart", "carrier": "SessionStart", "actual_worker_spawned": False,
        "source_hook_key": handler["key"], "source_hook_hash": handler["currentHash"],
        "config_sha256": runner.sha256_file(destination / "config.toml"),
    })
    return validate_worker_simulation(evidence, identity)


def validate_worker_simulation(evidence, identity):
    home = evidence / "homes/worker-simulation"
    event = runner.read_json(evidence / "worker-simulation.event.json")
    requests = runner.read_json(evidence / "worker-simulation.request.json")
    status = runner.read_json(evidence / "worker-simulation.status.json")
    registered = runner.read_json(evidence / "worker-simulation.registry.json")["hooks"]["data"]
    source = runner.read_json(evidence / "router.registry.json")["hooks"]["data"][0]["hooks"]
    handler = next(h for h in source if h["key"] == event["source_hook_key"])
    if (event["event"] != "SubagentStart" or event["carrier"] != "SessionStart"
            or event["actual_worker_spawned"] is not False or handler["eventName"] != "subagentStart"
            or handler["currentHash"] != event["source_hook_hash"]
            or runner.sha256_file(home / "config.toml") != event["config_sha256"]):
        raise runner.HarnessError("worker simulation event binding changed")
    if (len(registered) != 1 or registered[0]["errors"] or registered[0]["warnings"]
            or len(requests) != 1 or requests[0].get("path") != "/responses" or status["exit_code"] != 1
            or "--dangerously-bypass-hook-trust" not in status["argv"]):
        raise runner.HarnessError("worker simulation did not reach the rejecting sink")
    active = [h for h in registered[0]["hooks"] if h["enabled"] and not h["isManaged"]]
    if (len(active) != 1 or active[0]["eventName"] != "sessionStart"
            or active[0]["command"] != handler["command"] + " --simulate-subagent-start"):
        raise runner.HarnessError("worker simulation registered the wrong program")
    relative = Path(identity["installed_path"]).relative_to(Path(identity["homes"]["router"]))
    if (isolation.tree_hash(home / relative) != identity["candidate_sha256"]
            or isolation.tree_hash(home / profile.PROFILE_DIRECTORY) != identity["profile_sha256"]):
        raise runner.HarnessError("worker simulation candidate/profile changed")
    developer = ["\n".join(c.get("text", "") for c in m.get("content", []))
                 for m in requests[0]["body"].get("input", []) if m.get("role") == "developer"]
    capability = write_gate.validate_capability(requests[0]["body"], Path(status["cwd"]))
    contexts = [text for text in developer if "WORKER ROLE OVERRIDE" in text]
    if len(contexts) != 1 or any("CONTROLLER ROLE ONLY:" in text for text in developer):
        raise runner.HarnessError("worker override absent or mixed with controller output")
    try:
        report = profile.parse_context(contexts[0], home / profile.PROFILE_DIRECTORY, "SubagentStart")
    except ValueError as error:
        raise runner.HarnessError("worker critical context gate failed: " + str(error))
    return dict(report, actual_worker_spawned=False, event_simulation=True, write_capability=capability,
                context_sha256=runner.sha256_bytes(contexts[0].encode()))


def bindings(homes, identity):
    executable = shutil.which("codex")
    if executable is None:
        raise runner.HarnessError("Codex executable unavailable")
    return {"isolation_sha256": runner.sha256_file(homes["baseline"].parent / "isolation.json"),
            "candidate_sha256": identity["candidate_sha256"],
            "cli_sha256": runner.sha256_file(Path(executable)),
            "python_sha256": runner.sha256_file(Path(sys.executable)),
            "code_sha256": {Path(m.__file__).name: runner.sha256_file(Path(m.__file__))
                            for m in (isolation, runner, live, profile, write_gate, workspace_editor)},
            "probe_sha256": runner.sha256_file(Path(__file__))}


def run_probe(campaign, homes):
    identity = isolation.validate_homes(homes)
    manifest = runner.validate_manifest()
    evidence = campaign / "hook-delivery"
    evidence.mkdir(parents=True, exist_ok=False)
    result = {"schema_version": 1, "model_invocations": 0, "reports": {}, "errors": [],
              "bindings": bindings(homes, identity)}
    for arm in ("baseline", "router"):
        home = live.clone_slot_home(homes[arm], evidence / "homes" / arm, arm, identity, include_auth=False)
        workspace = evidence / (arm + "-workspace")
        workspace.mkdir()
        subprocess.run(["git", "init", "--quiet"], cwd=workspace, check=True)
        registered = registry(home, workspace, arm)
        runner.write_json(evidence / (arm + ".registry.json"), registered)
        status, requests = capture_request(manifest, home, workspace, arm, evidence)
        try:
            result["reports"][arm] = validate_delivery(arm, registered, requests, status, home, identity)
            write_gate.exercise_editor(workspace, evidence / (arm + ".editor.json"), isolation.arm_environment(home))
        except (runner.HarnessError, KeyError, TypeError) as error:
            result["errors"].append(arm + ": " + str(error))
    if not result["errors"]:
        try:
            result["reports"]["worker-simulation"] = simulate_worker(manifest, homes["router"], identity, evidence)
        except (runner.HarnessError, ValueError, KeyError, TypeError) as error:
            result["errors"].append("worker-simulation: " + str(error))
    result["passed"] = not result["errors"]
    result["evidence_sha256"] = {p.name: runner.sha256_file(p) for p in evidence.iterdir() if p.is_file()}
    runner.write_json(campaign / "hook-delivery.json", result)
    return result


def require_hook_receipt(campaign, homes):
    identity = isolation.validate_homes(homes)
    receipt = runner.read_json(campaign / "hook-delivery.json")
    if receipt.get("passed") is not True or receipt.get("model_invocations") != 0:
        raise runner.HarnessError("offline hook delivery preflight did not pass")
    if receipt.get("bindings") != bindings(homes, identity):
        raise runner.HarnessError("hook delivery receipt binding changed")
    evidence = campaign / "hook-delivery"
    required = {arm + suffix for arm in (*homes, "worker-simulation") for suffix in
                (".registry.json", ".request.json", ".command.json", ".status.json", ".jsonl", ".stderr.txt")}
    required.add("worker-simulation.event.json")
    required.update(arm + ".editor.json" for arm in homes)
    required.update(arm + "-workspace-outside-sentinel.txt" for arm in homes)
    if set(receipt.get("evidence_sha256", {})) != required:
        raise runner.HarnessError("hook delivery evidence inventory incomplete")
    for name, digest in receipt["evidence_sha256"].items():
        if runner.sha256_file(evidence / name) != digest:
            raise runner.HarnessError("hook delivery evidence changed")
    for arm in homes:
        report = validate_delivery(arm, runner.read_json(evidence / (arm + ".registry.json")),
                                   runner.read_json(evidence / (arm + ".request.json")),
                                   runner.read_json(evidence / (arm + ".status.json")),
                                   evidence / "homes" / arm, identity)
        if report != receipt["reports"][arm]:
            raise runner.HarnessError("hook delivery report changed")
        write_gate.require_editor_proof(evidence / (arm + ".editor.json"), evidence / (arm + "-workspace"))
    if validate_worker_simulation(evidence, identity) != receipt["reports"]["worker-simulation"]:
        raise runner.HarnessError("worker delivery report changed")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-root", required=True, type=Path)
    parser.add_argument("--state-root", required=True, type=Path)
    args = parser.parse_args()
    result = run_probe(args.campaign_root, {arm: args.state_root / arm for arm in ("baseline", "router")})
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
