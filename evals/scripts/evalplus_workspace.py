#!/usr/bin/env python3
"""Tiny stdio MCP file editor confined to one fresh benchmark workspace.

No shell, subprocess, network, import/evaluation, or arbitrary execution tool is
exposed. Normal plugin configuration is unrelated to this benchmark capability.
"""
import argparse
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

SERVER_NAME = "evalplus_workspace"
PROFILE_NAME = "evalplus-task"
MAX_BYTES = 262144
PROTECTED = {".git", ".codex", ".agents", ".codex-model-router"}


def command_overrides(workspace):
    """Both controller and native workers inherit this exact bounded root."""
    candidate = Path(workspace).absolute()
    if candidate.exists() or candidate.is_symlink():
        WorkspaceEditor(candidate)  # Reject aliased roots before granting access.
    root = str(candidate)
    policy = {":minimal": "read", root: "write"}
    for name in sorted(PROTECTED):
        policy[str(Path(root) / name)] = "read"
    filesystem = "{ " + ", ".join(json.dumps(k) + " = " + json.dumps(v) for k, v in policy.items()) + " }"
    server = ("{ command = " + json.dumps(sys.executable) + ", args = "
              + json.dumps(["-I", str(Path(__file__).resolve()), "--root", root])
              + ", required = true, startup_timeout_sec = 20 }")
    return ["--config", 'default_permissions="' + PROFILE_NAME + '"',
            "--config", "permissions." + PROFILE_NAME + ".filesystem=" + filesystem,
            "--config", "permissions." + PROFILE_NAME + ".network.enabled=false",
            "--config", "mcp_servers." + SERVER_NAME + "=" + server,
            "--disable", "shell_tool", "--disable", "tool_search"]


class WorkspaceEditor:
    def __init__(self, root):
        self.root = Path(root).absolute()
        self._directory()

    @staticmethod
    def _plain(path, directory=False):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("links and reparse points are forbidden")
        expected = stat.S_ISDIR if directory else stat.S_ISREG
        if not expected(info.st_mode):
            raise ValueError("unexpected filesystem object")
        if not directory and info.st_nlink > 1:
            raise ValueError("hard-linked files are forbidden")

    def _directory(self):
        self._plain(self.root, directory=True)
        if self.root.resolve() != self.root:
            raise ValueError("workspace root is not canonical")

    def target(self, relative):
        self._directory()
        # A single filename suffices for solution.py and preflight markers. This
        # excludes drive/UNC paths, traversal, nested links, and NTFS streams.
        if (not isinstance(relative, str) or not relative or len(relative) > 120
                or relative in (".", "..") or relative.lower() in PROTECTED
                or any(c in relative for c in '/\\:\x00<>|?*"')
                or relative.endswith((".", " "))
                or relative.split('.')[0].upper() in {"CON", "PRN", "AUX", "NUL", *('COM%d' % n for n in range(1, 10)), *('LPT%d' % n for n in range(1, 10))}):
            raise ValueError("use an unprotected filename within the assigned workspace")
        path = self.root / relative
        if path.exists() or path.is_symlink():
            self._plain(path)
        return path

    def read(self, relative):
        path = self.target(relative)
        if path.stat().st_size > MAX_BYTES:
            raise ValueError("file exceeds byte limit")
        return path.read_text(encoding="utf-8")

    def write(self, relative, content):
        path = self.target(relative)
        if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_BYTES:
            raise ValueError("content must be bounded UTF-8 text")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.root, prefix=".evalplus-write-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content.encode("utf-8"))
            self.target(relative)
            os.replace(temporary, path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        import hashlib
        return {"path": relative, "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def tools_schema():
    filename = {"type": "string", "description": "Root-level relative filename, e.g. solution.py. No absolute paths, subdirectories, streams, or protected names."}
    return [
        {"name": "read_file", "description": "Read a UTF-8 file in the assigned benchmark workspace only.",
         "inputSchema": {"type": "object", "properties": {"path": filename},
                         "required": ["path"], "additionalProperties": False}},
        {"name": "write_file", "description": "Create or replace a UTF-8 artifact in the assigned benchmark workspace only. Never executes its contents.",
         "inputSchema": {"type": "object", "properties": {"path": filename, "content": {"type": "string"}},
                         "required": ["path", "content"], "additionalProperties": False}},
    ]


def serve(root):
    editor = WorkspaceEditor(root)
    for line in sys.stdin:
        message = json.loads(line)
        if "id" not in message:
            continue
        method, params = message.get("method"), message.get("params", {})
        try:
            if method == "initialize":
                result = {"protocolVersion": params.get("protocolVersion", "2024-11-05"),
                          "capabilities": {"tools": {}}, "serverInfo": {"name": SERVER_NAME, "version": "1"}}
            elif method == "tools/list":
                result = {"tools": tools_schema()}
            elif method == "tools/call":
                name, arguments = params["name"], params.get("arguments", {})
                if name == "read_file" and set(arguments) == {"path"}:
                    output = editor.read(arguments["path"])
                elif name == "write_file" and set(arguments) == {"path", "content"}:
                    output = json.dumps(editor.write(arguments["path"], arguments["content"]))
                else:
                    raise ValueError("unsupported tool or arguments")
                result = {"content": [{"type": "text", "text": output}], "isError": False}
            elif method == "ping":
                result = {}
            else:
                raise ValueError("unsupported MCP method")
            reply = {"jsonrpc": "2.0", "id": message["id"], "result": result}
        except (ValueError, OSError, KeyError) as error:
            if method == "tools/call":
                reply = {"jsonrpc": "2.0", "id": message["id"], "result": {
                    "content": [{"type": "text", "text": "Workspace editor refused: " + str(error)}], "isError": True}}
            else:
                reply = {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32602, "message": str(error)}}
        print(json.dumps(reply), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    serve(parser.parse_args().root)
