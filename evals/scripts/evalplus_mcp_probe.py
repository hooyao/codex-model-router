#!/usr/bin/env python3
"""Scripted loopback Responses fixture exercising the CLI MCP approval path.

No model implementation, forwarding, auth, delegation, or generated solution
execution. The only scripted operations discover tools and request two writes.
"""
import argparse
import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from . import evalplus_runner as runner, evalplus_isolation as isolation, evalplus_hooks as hooks
except ImportError:
    import evalplus_runner as runner
    import evalplus_isolation as isolation
    import evalplus_hooks as hooks

CONTENT = "CLI mediated offline write proof\n"


def scripted_events(index, arguments, code_mode):
    call_id = "fixture-call-%d" % index
    if code_mode:
        script = "text(ALL_TOOLS);" if arguments is None else "text(await tools.mcp__evalplus_workspace__write_file(" + json.dumps(arguments) + "));"
        item = {"type": "custom_tool_call", "id": "ctc_" + call_id, "call_id": call_id, "name": "exec", "input": script, "status": "completed"}
    else:
        item = {"type": "function_call", "id": "fc_" + call_id, "call_id": call_id,
                "name": "write_file", "namespace": "mcp__evalplus_workspace", "arguments": json.dumps(arguments), "status": "completed"}
    response = {"id": "resp_fixture_%d" % index, "status": "completed", "output": [item],
                "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}}
    return [{"type": "response.created", "response": {"id": response["id"], "status": "in_progress"}},
            {"type": "response.output_item.added", "output_index": 0, "item": dict(item, status="in_progress")},
            {"type": "response.output_item.done", "output_index": 0, "item": item},
            {"type": "response.completed", "response": response}]


def run_fixture(evidence, code_mode=False, approval=True):
    evidence = runner.require_outside_workspace(evidence)
    evidence.mkdir(parents=True, exist_ok=False)
    home, task = evidence / "home", evidence / "task"
    home.mkdir(); task.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=task, check=True)
    config = isolation.clean_config({"provider_id": "copilot-bridge", "provider": {
        "name": "Local scripted fixture", "base_url": "http://127.0.0.1:1", "wire_api": "responses", "requires_openai_auth": False}})
    (home / "config.toml").write_text(config, encoding="utf-8")
    outside = evidence / "outside.txt"
    outside.write_text("unchanged\n", encoding="utf-8")
    outside_hash = runner.sha256_file(outside)
    operations = ([None] if code_mode else []) + [
        {"path": "cli-proof.txt", "content": CONTENT}, {"path": "../outside.txt", "content": "forbidden overwrite"}]
    requests, emitted = [], []

    class ScriptedServer(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            requests.append({"path": self.path, "body": body})
            index = len(requests) - 1
            if index >= len(operations):
                self.send_response(400); self.send_header("Content-Type", "application/json"); self.end_headers()
                self.wfile.write(b'{"error":{"message":"fixture complete; no model exists"}}')
                return
            events = scripted_events(index, operations[index], code_mode)
            emitted.extend(events)
            payload = "".join("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n" for event in events).encode()
            self.send_response(200); self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)

        def do_GET(self):
            self.send_response(404); self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), ScriptedServer)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        command = runner.build_codex_command(runner.validate_manifest(), {"variant": "baseline"}, task)
        command[-1:-1] = hooks.offline_overrides(server.server_port)
        if code_mode:
            # Render the same tool protocol as the observed native Luna worker;
            # this endpoint has no model and starts no subagent.
            command[command.index("--model") + 1] = "gpt-5.6-luna"
            command[-1:-1] = ["--config", 'model_reasoning_effort="low"']
        if not approval:
            for tool in ("read_file", "write_file"):
                command[-1:-1] = ["--config", "mcp_servers.evalplus_workspace.tools." + tool + '.approval_mode="prompt"']
        status = isolation.capture(evidence, "cli", command, task, isolation.arm_environment(home), "Offline scripted MCP invocation fixture.", 60)
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
    runner.write_json(evidence / "requests.json", requests)
    runner.write_json(evidence / "scripted-events.json", emitted)
    inside = task / "cli-proof.txt"
    inside_ok = inside.exists() and inside.read_bytes() == CONTENT.encode()
    unchanged = runner.sha256_file(outside) == outside_hash
    captured_outputs = json.dumps([item for row in requests for item in row["body"].get("input", [])
                                   if item.get("type") in ("function_call_output", "custom_tool_call_output")])
    expected_outputs = "Workspace editor refused:" in captured_outputs if approval else "user cancelled MCP tool call" in captured_outputs
    capabilities, capability_errors = [], []
    for row in requests:
        try:
            capability = hooks.write_gate.validate_capability(row["body"], task)
            if capability["code_mode_dispatcher"] != code_mode:
                raise runner.HarnessError("scripted fixture used the wrong tool protocol")
            capabilities.append(capability)
        except runner.HarnessError as error:
            capability_errors.append(str(error))
    passed = (status["exit_code"] == 1 and len(requests) == len(operations) + 1
              and status.get("config_integrity", {}).get("passed") is True
              and unchanged and expected_outputs and inside_ok == approval
              and not capability_errors and capabilities and not capabilities[-1]["deferred_editor_catalog"])
    result = {"passed": passed, "code_mode": code_mode, "approval": approval, "model_invocations": 0,
              "scripted_fixture": True, "delegations": 0, "inside_write_succeeded": inside_ok,
              "outside_unchanged": unchanged, "expected_tool_output_observed": expected_outputs,
              "request_count": len(requests), "cli_exit_code": status["exit_code"],
              "write_capabilities": capabilities, "capability_errors": capability_errors,
              "fixture_sha256": runner.sha256_file(Path(__file__)),
              "inside_sha256": runner.sha256_file(inside) if inside.exists() else None,
              "evidence_sha256": {p.name: runner.sha256_file(p) for p in evidence.iterdir() if p.is_file()}}
    runner.write_json(evidence / "result.json", result)
    return result


def require_fixture(evidence, code_mode):
    result = runner.read_json(evidence / "result.json")
    if (result.get("passed") is not True or result.get("approval") is not True
            or any(result.get(key) is not True for key in ("scripted_fixture", "inside_write_succeeded",
                                                         "outside_unchanged", "expected_tool_output_observed"))
            or result.get("code_mode") is not code_mode or result.get("model_invocations") != 0
            or result.get("delegations") != 0 or result.get("fixture_sha256") != runner.sha256_file(Path(__file__))
            or (evidence / "task/cli-proof.txt").read_bytes() != CONTENT.encode()
            or runner.sha256_file(evidence / "task/cli-proof.txt") != result.get("inside_sha256")):
        raise runner.HarnessError("CLI-mediated MCP write proof missing or stale")
    required = {"cli.command.json", "cli.jsonl", "cli.status.json", "cli.stderr.txt",
                "cli.config-before.json", "cli.config-after.json", "outside.txt", "requests.json", "scripted-events.json"}
    if set(result.get("evidence_sha256", {})) != required:
        raise runner.HarnessError("CLI MCP fixture evidence inventory incomplete")
    for name, digest in result["evidence_sha256"].items():
        if runner.sha256_file(evidence / name) != digest:
            raise runner.HarnessError("CLI MCP fixture evidence changed")
    isolation.config_receipt.require(evidence, "cli", evidence / "home", evidence / "task")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--code-mode", action="store_true")
    parser.add_argument("--prompt-control", action="store_true")
    args = parser.parse_args()
    result = run_fixture(args.evidence, args.code_mode, not args.prompt_control)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
