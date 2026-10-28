"""Capability-based Codex runtime adapter for new benchmark campaigns.

No version allowlist is used. Historical replay runners keep their original
identity pins; callers of this adapter choose a CLI and record its metadata.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from evals.long_horizon_v1.transport import AppServerTransport
from .run_paired_arm import LONG_CONTEXT_THRESHOLD, SessionMeter, USAGE_KEYS, response_cost_interval


REQUIRED_METHODS = {"initialize", "thread/start", "turn/start", "turn/interrupt",
                    "hooks/list", "model/list"}


def discover_cli(candidate: str | None = None) -> Path:
    selected = candidate or shutil.which("codex")
    if not selected or not Path(selected).is_file():
        raise ValueError("Codex CLI is unavailable; supply --cli or put it on PATH")
    return Path(selected).resolve()


def runtime_capabilities(cli: Path) -> dict:
    version = subprocess.run([str(cli), "--version"], capture_output=True, text=True,
                             check=True, timeout=15).stdout.strip()
    with tempfile.TemporaryDirectory() as directory:
        subprocess.run([str(cli), "app-server", "generate-json-schema", "--out", directory],
                       capture_output=True, check=True, timeout=30)
        request = Path(directory) / "ClientRequest.json"
        if not request.is_file():
            raise ValueError("runtime did not export its request schema")
        raw = request.read_bytes()
        document = json.loads(raw)
        methods = {method for variant in document.get("oneOf", []) if isinstance(variant, dict)
                   for method in ((variant.get("properties") or {}).get("method") or {}).get("enum", [])}
    missing = REQUIRED_METHODS - methods
    if missing:
        raise ValueError("required runtime methods unavailable: " + ", ".join(sorted(missing)))
    return {"version": version, "cli_sha256": hashlib.sha256(cli.read_bytes()).hexdigest(),
            "request_schema_sha256": hashlib.sha256(raw).hexdigest(),
            "required_methods": sorted(REQUIRED_METHODS), "version_allowlist": False}


def hook_trust_fields(value: str) -> dict:
    """Accept observed declaration-only or declaration/content trust forms."""
    if not isinstance(value, str):
        raise ValueError("runtime hook hash is missing")
    parts = value.split("/")
    if len(parts) not in (1, 2) or any(not re.fullmatch(r"sha256:[0-9a-f]{64}", p) for p in parts):
        raise ValueError("unsupported runtime hook trust representation")
    fields = {"enabled": True, "trusted_hash": parts[0]}
    if len(parts) == 2:
        fields["trusted_content_hash"] = parts[1]
    return fields


def toml_value(value) -> str:
    if isinstance(value, str):
        return json.dumps(value)
    if type(value) is bool:
        return str(value).lower()
    if type(value) in (int, float):
        return str(value)
    if isinstance(value, list):
        return "[" + ",".join(toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(k) + "=" + toml_value(v) for k, v in value.items()) + "}"
    raise ValueError("unsupported value in private runtime configuration")


def isolated_environment(home: Path) -> dict[str, str]:
    environment = os.environ.copy()
    # This variable retains its documented role: the child runtime's own home.
    environment["CODEX_HOME"] = str(home)
    return environment


class PortableTransport(AppServerTransport):
    """Use workspace boundaries and save raw events only to a private sink."""

    def __init__(self, *args, event_sink=None, **kwargs):
        self.event_sink = event_sink
        super().__init__(*args, **kwargs)

    def _send(self, method: str, params: dict, **kwargs):
        params = dict(params)
        if method == "thread/start":
            params["sandbox"] = "workspace-write"
        if method == "turn/start":
            params["sandboxPolicy"] = {"type": "workspaceWrite", "writableRoots": [str(self.workspace)],
                                       "networkAccess": False}
        return super()._send(method, params, **kwargs)

    def _frame(self, **kwargs):
        event = super()._frame(**kwargs)
        if event and self.event_sink is not None:
            self.event_sink.write(json.dumps(event) + "\n")
            self.event_sink.flush()
        return event

    def initialize(self) -> None:
        request = self._send("initialize", {"clientInfo": {"name": "router-regression", "version": "1"},
                                             "capabilities": {"experimentalApi": True}})
        self._response(request)
        self._send("initialized", {}, notification=True)

    def request(self, method: str, params: dict) -> dict:
        return self._response(self._send(method, params))

    def start_thread(self, developer_instructions: str | None = None) -> dict:
        params = {"cwd": str(self.workspace), "model": self.model,
                  "sandbox": "workspace-write", "approvalPolicy": "never", "ephemeral": False}
        if developer_instructions:
            params["developerInstructions"] = developer_instructions
        result = self.request("thread/start", params)
        thread = result.get("thread") or {}
        if result.get("model") != self.model or not isinstance(thread.get("id"), str):
            raise ValueError("runtime did not confirm the requested model and thread identity")
        if result.get("reasoningEffort") not in (None, self.effort):
            raise ValueError("runtime changed the requested reasoning effort")
        self.thread_id = thread["id"]
        return result

    def start(self) -> str:
        self.initialize()
        self.start_thread()
        return self.thread_id


class RuntimeSessionMeter(SessionMeter):
    """Use a complete native response receipt when an interrupted tool has no later snapshot.

    Historical SessionMeter behavior is unchanged. An open turn, invalid raw
    identity, missing class, or later conflicting event still remains unknown.
    """

    def refresh(self) -> None:
        super().refresh()
        if self.unknown_usage:
            return
        for state in self.paths.values():
            raw = state.get("pending_usage_record")
            if state.get("terminal") != "turn_aborted" or raw is None:
                continue
            current = state["turns"][-1]
            usage, total = raw.get("usage"), raw.get("thread_token_usage")
            previous = state.get("last_total_usage") or {}
            if not isinstance(usage, dict) or not isinstance(total, dict) or any(
                    type(usage.get(key)) is not int or type(total.get(key)) is not int or
                    total[key] != previous.get(key, 0) + usage[key] for key in USAGE_KEYS):
                self.unknown_usage.append(f"{state['id']}: interrupted native response classes do not reconcile")
                continue
            if "total_tokens" in usage and usage["total_tokens"] != usage["input_tokens"] + usage["output_tokens"] or \
                    "total_tokens" in total and total["total_tokens"] != total["input_tokens"] + total["output_tokens"]:
                self.unknown_usage.append(f"{state['id']}: interrupted native total differs from its classes")
                continue
            model = state["model"]
            if model not in self.rate_card:
                self.unknown_models.add(str(model))
                continue
            try:
                low, high = response_cost_interval(usage, model, self.rate_card)
            except (ValueError, TypeError) as error:
                self.unknown_usage.append(f"{state['id']}: {error}")
                continue
            self.cost += low
            self.cost_upper += high
            self.calls += 1
            if usage["input_tokens"] > LONG_CONTEXT_THRESHOLD:
                self.long_context_calls += 1
            state["cost"] += low
            state["cost_upper"] += high
            state["calls"] += 1
            current["calls"] += 1
            for key in USAGE_KEYS:
                if self.tokens[key] is not None:
                    self.tokens[key] += usage[key]
                if state["tokens"][key] is not None:
                    state["tokens"][key] += usage[key]
            state["responses"].append({"turn_id": current["turn_id"], "root_turn_id": raw["root_turn_id"],
                "model": model, "effort": state["effort"], "token_classes": {k: usage[k] for k in USAGE_KEYS},
                "response_id_sha256": hashlib.sha256(raw["response_id"].encode()).hexdigest(),
                "usage_source": "closed_native_response", "estimated_usd": low if low == high else None,
                "estimated_usd_lower_bound": low, "estimated_usd_upper_bound": high})
            state["last_total_usage"] = total
            state["last_response_usage"] = usage
            state["pending_usage_record"] = None

    def integrity_issues(self) -> list[str]:
        issues = super().integrity_issues()
        interrupted = {state["id"] for state in self.paths.values() if state.get("terminal") == "turn_aborted"}
        return [issue for issue in issues if issue not in
                {f"session {session} lacks terminal result" for session in interrupted}]
