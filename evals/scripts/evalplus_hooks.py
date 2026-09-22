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
except ImportError:
    import evalplus_isolation as isolation
    import evalplus_runner as runner
    import evalplus_live as live


def offline_overrides(port):
    provider = {"name": "Rejecting offline hook probe", "base_url": "http://127.0.0.1:%d" % port,
                "wire_api": "responses", "requires_openai_auth": False,
                "supports_websockets": False, "request_max_retries": 0, "stream_max_retries": 0}
    return ["--config", 'model_provider="hook-probe"', "--config",
            "model_providers.hook-probe=" + isolation._toml_value(provider),
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


def capture_request(manifest, home, workspace, arm, evidence):
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
        command = runner.build_codex_command(manifest, {"variant": arm}, workspace)
        command[-1:-1] = offline_overrides(server.server_port)
        status = isolation.capture(evidence, arm, command, workspace, isolation.arm_environment(home),
                                   "Offline hook delivery probe.", 60)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    runner.write_json(evidence / (arm + ".request.json"), requests)
    return status, requests


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
    developer = ["\n".join(c.get("text", "") for c in m.get("content", []))
                 for m in body.get("input", []) if m.get("role") == "developer"]
    contexts = [text for text in developer if "CONTROLLER ROLE ONLY:" in text]
    skills = re.findall(r"^- ([\w:-]+): .*?\(file:", "\n".join(developer), re.MULTILINE)
    if arm == "baseline":
        if active or skills or contexts or any(h.get("source") == "plugin" for h in hooks):
            raise runner.HarnessError("baseline has candidate or ambient hook/skill context")
        return {"events": [], "skills": [], "registered_hooks": []}
    expected = {"sessionStart", "userPromptSubmit", "subagentStart"}
    if len(active) != 3 or {h["eventName"] for h in active} != expected:
        raise runner.HarnessError("candidate hooks are not explicitly registered")
    relative = Path(identity["installed_path"]).relative_to(Path(identity["homes"]["router"])).as_posix()
    for hook in active:
        if (hook["source"] != "user" or Path(hook["sourcePath"]).resolve() != (home / "config.toml").resolve()
                or relative + "/hooks/router_hook.py" not in hook["command"]
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
    if any("ROUTING_CONFIG_BEGIN" not in text or "ROUTING_CONFIG_END" not in text for text in contexts):
        raise runner.HarnessError("both lifecycle messages must include routing config")
    dispatch = [tool for namespace in body.get("tools", []) for tool in namespace.get("tools", [])
                if tool.get("name") == "spawn_agent"]
    if len(dispatch) != 1 or not {"model", "reasoning_effort"}.issubset(dispatch[0]["parameters"]["properties"]):
        raise runner.HarnessError("native model/effort dispatch schema absent")
    return {"events": ["SessionStart", "UserPromptSubmit"], "skills": skills,
            "registered_hooks": [{key: h[key] for key in ("key", "eventName", "currentHash", "trustStatus")}
                                 for h in active],
            "trust_activation": "--dangerously-bypass-hook-trust",
            "context_sha256": [runner.sha256_bytes(text.encode()) for text in contexts],
            "context_spilled": any("Full hook output saved to:" in text for text in contexts),
            "native_model_effort_selection": True}


def bindings(homes, identity):
    executable = shutil.which("codex")
    if executable is None:
        raise runner.HarnessError("Codex executable unavailable")
    return {"isolation_sha256": runner.sha256_file(homes["baseline"].parent / "isolation.json"),
            "candidate_sha256": identity["candidate_sha256"],
            "cli_sha256": runner.sha256_file(Path(executable)),
            "python_sha256": runner.sha256_file(Path(sys.executable)),
            "code_sha256": {Path(m.__file__).name: runner.sha256_file(Path(m.__file__))
                            for m in (isolation, runner, live)},
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
        except (runner.HarnessError, KeyError, TypeError) as error:
            result["errors"].append(arm + ": " + str(error))
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
    required = {arm + suffix for arm in homes for suffix in
                (".registry.json", ".request.json", ".command.json", ".status.json", ".jsonl", ".stderr.txt")}
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
