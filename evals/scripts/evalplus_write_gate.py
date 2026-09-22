"""Offline confinement evidence and mandatory paid write-artifact verification."""
import hashlib
import json
import re
import secrets
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    from . import evalplus_runner as runner, evalplus_workspace as editor
except ImportError:
    import evalplus_runner as runner
    import evalplus_workspace as editor

MARKER = "write-probe.txt"


def tool_names(tools, prefix=""):
    if not isinstance(tools, list) or any(not isinstance(tool, dict) for tool in tools):
        raise runner.HarnessError("malformed structured tool declaration")
    for tool in tools:
        identifier = tool.get("name", tool.get("type", ""))
        if not isinstance(identifier, str) or not identifier:
            raise runner.HarnessError("malformed structured tool name")
        name = prefix + identifier
        yield name
        yield from tool_names(tool.get("tools", []), name + ".")


def canonical_tool_name(name):
    prefix = "mcp__evalplus_workspace__"
    return "mcp__evalplus_workspace." + name[len(prefix):] if name.startswith(prefix) else name


def request_tool_evidence(body):
    """Read structured tool declarations, never infer tools from user prose.

    Native workers can carry a restricted code-mode dispatcher in additional_tools.
    Its MCP tools may be deferred until ALL_TOOLS discovery; the paid write marker
    remains mandatory when that catalog is not yet visible in a request.
    """
    names = set(tool_names(body.get("tools", [])))
    code_mode = False
    for item in body.get("input", []):
        if item.get("type") != "additional_tools":
            continue
        if item.get("role") not in ("developer", "system"):
            raise runner.HarnessError("untrusted additional_tools declaration")
        # Validate the complete schema before treating the dispatcher specially.
        list(tool_names(item.get("tools", [])))
        for tool in item.get("tools", []):
            if tool.get("name") == "exec":
                description = tool.get("description", "")
                if (tool.get("type") != "custom" or not isinstance(description, str)
                        or "Runs raw JavaScript -- no Node, no file system, no network access" not in description
                        or "ALL_TOOLS" not in description):
                    raise runner.HarnessError("unrestricted or unknown exec dispatcher")
                code_mode = True
                names.update(re.findall(r"declare const tools:\s*\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(", description))
            else:
                names.update(tool_names([tool]))
    if code_mode:
        discoveries = {item.get("call_id") for item in body.get("input", [])
                       if item.get("type") == "custom_tool_call" and item.get("name") == "exec"
                       and "ALL_TOOLS" in item.get("input", "")}
        for item in body.get("input", []):
            if item.get("type") != "custom_tool_call_output" or item.get("call_id") not in discoveries:
                continue
            output = item.get("output", [])
            if not isinstance(output, list):
                raise runner.HarnessError("malformed tool discovery output")
            for block in output:
                if not isinstance(block, dict) or block.get("type") not in ("input_text", "text"):
                    continue
                try:
                    catalog = json.loads(block.get("text", ""))
                except (ValueError, TypeError):
                    continue
                if isinstance(catalog, list) and all(isinstance(t, dict) and isinstance(t.get("name"), str) for t in catalog):
                    names.update(t["name"] for t in catalog)
    return {canonical_tool_name(name) for name in names}, code_mode


def validate_capability(body, workspace):
    names, code_mode = request_tool_evidence(body)
    required = {"mcp__evalplus_workspace.read_file", "mcp__evalplus_workspace.write_file"}
    deferred = code_mode and not required.issubset(names)
    if not required.issubset(names) and not deferred:
        raise runner.HarnessError("bounded workspace editor is absent from the request")
    if any(name.startswith("mcp__") and name not in required | {"mcp__evalplus_workspace"} for name in names):
        raise runner.HarnessError("unrelated MCP capability exposed")
    forbidden = {"exec", "exec_command", "shell", "shell_command", "write_stdin", "web_search", "browser", "computer", "code_interpreter"}
    if any(name.split('.')[-1] in forbidden for name in names):
        raise runner.HarnessError("shell/network/execution tool exposed")
    text = "\n".join(c.get("text", "") for item in body.get("input", []) for c in item.get("content", []))
    blocks = re.findall(r"<filesystem>.*?</filesystem>", text, re.DOTALL)
    if len(blocks) != 1 or "Network access is restricted." not in text:
        raise runner.HarnessError("effective filesystem/network policy missing")
    try:
        policy = ET.fromstring(blocks[0])
    except ET.ParseError as error:
        raise runner.HarnessError("malformed filesystem policy: " + str(error))
    root = Path(workspace).resolve()
    roots = [Path(item.text).resolve() for item in policy.findall("./workspace_roots/root")]
    fs = policy.find("./permission_profile/file_system")
    if roots != [root] or fs is None or fs.get("type") != "restricted":
        raise runner.HarnessError("workspace roots or filesystem policy are not confined")
    writes = []
    for entry in fs.findall("entry"):
        path, special = entry.find("path"), entry.find("special")
        if special is not None:
            if special.text != ":minimal" or entry.get("access") != "read":
                raise runner.HarnessError("unexpected special filesystem grant")
        elif path is not None:
            target = Path(path.text).resolve()
            if entry.get("access") == "write":
                writes.append(target)
            elif entry.get("access") != "read" or target not in [root / name for name in editor.PROTECTED]:
                raise runner.HarnessError("unexpected filesystem grant")
        else:
            raise runner.HarnessError("unknown filesystem policy entry")
    if writes != [root]:
        raise runner.HarnessError("writes are not limited to the fresh task workspace")
    return {"workspace": str(root), "write_roots": [str(root)], "editor_tools": sorted(required & names),
            "code_mode_dispatcher": code_mode, "deferred_editor_catalog": deferred,
            "requires_write_artifact": True,
            "shell_enabled": False, "network_enabled": False}


def exercise_editor(workspace, evidence_path, environment=None):
    """Invoke the real stdio MCP server directly; no model or generated code."""
    workspace = workspace.resolve()
    outside = workspace.parent / (workspace.name + "-outside-sentinel.txt")
    if outside.exists() or (workspace / "editor-proof.txt").exists():
        raise runner.HarnessError("editor probe requires fresh fixture paths")
    outside.write_text("outside must remain unchanged\n", encoding="utf-8")
    outside_hash = runner.sha256_file(outside)
    denied = ["../" + outside.name, str(outside), str(runner.ROOT / "AGENTS.md"),
              r"C:\Windows\write-escape.txt", r"\\localhost\share\escape", "file.txt:stream",
              ".git", ".codex", "subdir/file.txt", "CON", "file.txt."]
    calls = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "offline-boundary-test", "version": "1"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "write_file", "arguments": {"path": "editor-proof.txt", "content": "bounded editor proof\n"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "read_file", "arguments": {"path": "editor-proof.txt"}}},
    ]
    calls.extend({"jsonrpc": "2.0", "id": index + 5, "method": "tools/call", "params": {
        "name": "write_file", "arguments": {"path": target, "content": "must not be written"}}} for index, target in enumerate(denied))
    command = [sys.executable, "-I", str(Path(editor.__file__).resolve()), "--root", str(workspace)]
    process = subprocess.run(command, input="".join(json.dumps(c) + "\n" for c in calls),
                             capture_output=True, text=True, encoding="utf-8", timeout=30, env=environment)
    responses = [json.loads(line) for line in process.stdout.splitlines()]
    passed = (process.returncode == 0 and len(responses) == len(calls)
              and responses[2].get("result", {}).get("isError") is False
              and responses[3].get("result", {}).get("content") == [{"type": "text", "text": "bounded editor proof\n"}]
              and all(r.get("result", {}).get("isError") is True for r in responses[4:])
              and runner.sha256_file(outside) == outside_hash
              and (workspace / "editor-proof.txt").read_bytes() == b"bounded editor proof\n")
    proof = {"passed": passed, "model_invocations": 0, "command": command, "requests": calls,
             "responses": responses, "stderr": process.stderr, "outside_unchanged": runner.sha256_file(outside) == outside_hash,
             "artifact_sha256": runner.sha256_file(workspace / "editor-proof.txt") if (workspace / "editor-proof.txt").exists() else None,
             "outside_sha256": outside_hash, "editor_sha256": runner.sha256_file(Path(editor.__file__))}
    runner.write_json(evidence_path, proof)
    if not passed:
        raise runner.HarnessError("bounded editor MCP verification failed")
    return proof


def require_editor_proof(path, workspace):
    proof = runner.read_json(path)
    expected_command = [sys.executable, "-I", str(Path(editor.__file__).resolve()), "--root", str(workspace.resolve())]
    if (proof.get("passed") is not True or proof.get("model_invocations") != 0
            or proof.get("command") != expected_command
            or proof.get("editor_sha256") != runner.sha256_file(Path(editor.__file__))
            or runner.sha256_file(workspace / "editor-proof.txt") != proof.get("artifact_sha256")
            or runner.sha256_file(workspace.parent / (workspace.name + "-outside-sentinel.txt")) != proof.get("outside_sha256")):
        raise runner.HarnessError("bounded editor proof missing, stale or changed")
    return proof


def create_challenge(workspace, arm):
    if (workspace / MARKER).exists():
        raise runner.HarnessError("paid write probe marker already exists")
    content = "EVALPLUS_WRITE_PROBE " + arm + " " + secrets.token_hex(24) + "\n"
    return {"arm": arm, "workspace": str(workspace.resolve()), "filename": MARKER,
            "content": content, "sha256": hashlib.sha256(content.encode()).hexdigest()}


def write_prompt(challenge):
    operation = "Use evalplus_workspace.write_file to write " + json.dumps(challenge["filename"]) + " with exactly " + json.dumps(challenge["content"]) + ". "
    if challenge["arm"] == "router":
        operation = ("Follow the pure-controller policy: dispatch exactly one native worker with the canonical name/task ID and a bounded packet. Its task is: "
                     + operation + "The worker must use no shell, network, or code execution and must not delegate. Collect and close it; do not perform its file write yourself. ")
    return ("This is a write-capability and context preflight. " + operation
            + "Do not execute any code or retry. Return one JSON object with skills (all available names), session_start_excerpt, user_prompt_submit_excerpt, routing_config_present, native_spawn_tool_name, and native_model_effort_selection. Report missing context honestly; the harness independently verifies the written file.")


def verify_artifact(challenge, raw_path):
    root = Path(challenge["workspace"])
    if challenge.get("filename") != MARKER:
        raise runner.HarnessError("unexpected write probe filename")
    try:
        path = editor.WorkspaceEditor(root).target(MARKER)
        content = path.read_bytes()
    except (ValueError, OSError) as error:
        raise runner.HarnessError("write artifact missing or unsafe: " + str(error))
    digest = hashlib.sha256(content).hexdigest()
    if digest != challenge["sha256"] or content != challenge["content"].encode():
        raise runner.HarnessError("write artifact does not match fresh challenge")
    events = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    spawned = {sid for e in events if e.get("type") == "item.completed"
               and e.get("item", {}).get("type") == "collab_tool_call"
               and e["item"].get("tool") in ("spawn", "spawn_agent")
               for sid in e["item"].get("receiver_thread_ids", [])}
    if challenge["arm"] == "router" and len(spawned) != 1:
        raise runner.HarnessError("router write preflight must use one native worker")
    if challenge["arm"] == "baseline" and spawned:
        raise runner.HarnessError("baseline write preflight delegated")
    return {"path": str(path), "sha256": digest, "bytes": len(content), "native_worker_ids": sorted(spawned)}
