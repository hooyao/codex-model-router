"""Bounded, in-memory App Server capability probe for selective dispatch audits.

Design decision: this evaluation helper uses the repository's existing Python
tooling. It owns a fresh stdio App Server process and prints one reduced JSON
receipt after the process exits. It never saves protocol frames or prompt text.
The built-in dry run is synthetic and cannot establish runtime provenance.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import ntpath
import os
from pathlib import Path
import queue
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from typing import Callable


MAX_FRAME = 256 * 1024
MAX_EVENTS = 2000
MAX_TEXT = 16 * 1024
MAX_CALLS = 16
MAX_HOOKS = 64
MAX_HOOK_ID_BYTES = 512
MAX_HOOK_CONFIG = 128 * 1024
HOOK_INVENTORY_LIMIT = 200
HOOK_PROBE_MATCHER = "^collaborationspawn_agent$"
SESSION_FLAGS_SOURCE_PATH = r"C:\<session-flags>\config.toml"
DEFENDER_HOOK_ROOT = (r"C:\ProgramData\Microsoft\Windows Defender\Platform"
                      r"\4.18.26080.4-0")
DEFENDER_COMMAND_SHA256 = "69a412ab356f4971e82f4a9845af998cab844d8680f704a9d699bad009bae7f5"
DEFENDER_SOURCE_PATH_SHA256 = "2febe16e940ddfa967aee05d1469ec0b06e830dc1f3e40c43daa027a86f8d02d"
DEFENDER_HOOK_PINS = {
    "permissionRequest": ("permission_request", "fe9bdbb17be283402003dd4935b40673b278b461601d6bff55f8d2f3536bcabd", ".*"),
    "postToolUse": ("post_tool_use", "5495307b6401a28cd3e1514fe86706492488cc50c36f74eb2cdd86f2f81fb5a1", ".*"),
    "preToolUse": ("pre_tool_use", "6bb497c4b0f5d6e4f2e25bd84a670cef357346fc2ee48a8ea1d98e99bc2b6a1c", ".*"),
    "sessionStart": ("session_start", "ef183847c2302e7a106e2a71b87124be072e238f53333f8b5123166f72833d04", "startup|resume|clear"),
    "stop": ("stop", "8cc0f6b7167db9ae9932b821aa2bd93e7173a1b8ba55132c13ef4c4b79a5e689", None),
    "userPromptSubmit": ("user_prompt_submit", "6c99ac94855f8b2a7e950e4da72822b9f8ccba10b51aa3f99c9709042a1c2d2d", None),
}
AUDIT_CONTEXT_PREFIX = "CODEX_DISPATCH_AUDIT_V1 "
CANARY_PREFIX = "CODEX_PACKET_CANARY_V1 "
MAX_CANARY_ENTRY = 131_072
CANARY_SUITE = "RSA-OAEP-SHA256+AES-256-GCM"
BUNDLED_PYTHON = Path(r"C:\Users\yahu2\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe")
BUNDLED_PYTHON_SHA256 = "4208e595bfa15ae7097fead82aca06eb935ef874aa373696f78d9cb408b42fcf"
AUDIT_ATTEMPT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_:/.-]{0,127}\Z")
FIXED_SPAWN_NAME = "capability_probe"
CHILD_NONBUSINESS_ITEM_TYPES = {"userMessage", "agentMessage", "reasoning", "plan",
                                "contextCompaction"}
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
MODEL_RE = re.compile(r"[a-z0-9][a-z0-9.-]{0,63}\Z")
EFFORTS = {"minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
DEFAULT_PARENT_MODEL = "gpt-6.1-sol"
# Keep the older Sol selector for explicitly pinned compatibility campaigns.
APPROVED_PARENT_SELECTORS = {(DEFAULT_PARENT_MODEL, "low"), ("gpt-6-sol", "low"),
                             ("gpt-6-luna", "medium")}
HOOK_EVENTS = {"preToolUse", "postToolUse"}
ALL_HOOK_EVENTS = {"preToolUse", "permissionRequest", "postToolUse", "preCompact",
                   "postCompact", "sessionStart", "userPromptSubmit", "subagentStart",
                   "subagentStop", "stop"}
HOOK_STATUSES = {"running", "completed", "failed", "blocked", "stopped"}
HOOK_ENTRY_KINDS = {"warning", "stop", "feedback", "context", "error"}
COLLAB_TOOLS = {"spawnAgent", "sendInput", "resumeAgent", "wait", "closeAgent"}
SUBAGENT_KINDS = {"started", "completed", "interacted", "interrupted"}
AUDIT_HOOK_COMMAND = 'cmd.exe /d /c python "%PLUGIN_ROOT%\\hooks\\dispatch_audit.py"'
AUDIT_HOOK_MATCHER = "^(Agent|spawn_agent|collaborationspawn_agent)$"
COUNTED_NOTIFICATIONS = {"thread/started", "turn/started", "turn/completed",
                         "item/started", "item/updated", "item/completed",
                         "hook/started", "hook/completed"}
COUNTED_ITEM_TYPES = {"userMessage", "agentMessage", "reasoning", "plan",
                      "commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall",
                      "collabAgentToolCall", "subAgentActivity", "functionCallOutput",
                      "webSearch", "imageView", "imageGeneration", "sleep"}


class ProbeFailure(Exception):
    """A deliberately non-reflective failure suitable for a public receipt."""

    def __init__(self, code: str, stage: str | None = None):
        super().__init__(code)
        self.code = code
        self.stage = stage


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ProbeFailure("invalid_arguments")


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ProbeFailure("duplicate_json_key")
        value[key] = item
    return value


def decode_frame(frame: bytes) -> dict:
    if len(frame) > MAX_FRAME:
        raise ProbeFailure("oversized_frame")
    if not frame.endswith(b"\n"):
        raise ProbeFailure("unterminated_frame")
    try:
        value = json.loads(frame, object_pairs_hook=_no_duplicates,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except ProbeFailure:
        raise
    except (UnicodeError, ValueError, RecursionError, TypeError):
        raise ProbeFailure("malformed_frame") from None
    if (not isinstance(value, dict) or
            ("jsonrpc" in value and value["jsonrpc"] != "2.0") or
            ("id" not in value and "method" not in value)):
        raise ProbeFailure("invalid_jsonrpc_frame")
    return value


def strict_id(value: object) -> str | None:
    return value if isinstance(value, str) and ID_RE.fullmatch(value) else None


def hook_id_digest(value: object) -> dict | None:
    """Bind opaque schema string IDs without exposing their raw characters."""
    if not isinstance(value, str) or not value or len(value) > MAX_HOOK_ID_BYTES:
        return None
    try:
        raw = value.encode("utf-8")
    except UnicodeError:
        return None
    if len(raw) > MAX_HOOK_ID_BYTES:
        return None
    return {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def selector(value: object, *, effort: bool = False) -> str | None:
    if not isinstance(value, str):
        return None
    if effort:
        return value if value in EFFORTS else None
    return value if MODEL_RE.fullmatch(value) else None


def digest_text(value: object, *, limit: int = MAX_TEXT) -> dict | None:
    if not isinstance(value, str):
        return None
    try:
        raw = value.encode("utf-8")
    except UnicodeError:
        return None
    if len(raw) > limit:
        return None
    return {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def _mapping(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _enum(value: object, allowed: set[str]) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


def _same_path(observed: object, expected: str | None) -> bool:
    # Never resolve or open a path supplied by a protocol event.
    return bool(isinstance(observed, str) and expected and
                ntpath.normcase(ntpath.normpath(observed)) ==
                ntpath.normcase(ntpath.normpath(expected)))


def _hook_failure_category(text_value: str) -> str:
    if (re.fullmatch(r"hook timed out after [0-9]+s", text_value) or
            text_value.startswith("failed to write hook stdin:")):
        return "command_start_shell_or_timeout"
    if (text_value.startswith("failed to serialize pre tool use hook input:") or
            text_value.startswith("failed to serialize post tool use hook input:")):
        return "serialization"
    if text_value in {"hook returned invalid pre-tool-use JSON output",
                      "hook returned invalid post-tool-use JSON output"} or (
            text_value.startswith("PreToolUse hook returned unsupported ") or
            text_value.startswith("PostToolUse hook returned unsupported ")):
        return "invalid_hook_json_output"
    exit_match = re.fullmatch(r"hook exited with code (-?[0-9]{1,10})", text_value)
    if exit_match and int(exit_match.group(1)) not in (0, 2):
        return "non_2_exit"
    return "other_unknown"


class Reducer:
    """Only protocol-envelope and typed-item fields enter the receipt."""

    def __init__(self, sentinel: str, *, requested_parent_model: str | None = None,
                 requested_parent_effort: str | None = None,
                 expected_hook_source_path: str | None = None,
                 expected_hook_source_class: str = "plugin",
                 expected_child_model: str = "gpt-6-luna",
                 expected_child_effort: str = "low"):
        expected = digest_text(sentinel)
        if expected is None or not sentinel:
            raise ProbeFailure("invalid_sentinel")
        self.expected = expected
        self._sentinel_value = sentinel
        self.requested_parent_model = selector(requested_parent_model)
        self.requested_parent_effort = selector(requested_parent_effort, effort=True)
        self.expected_hook_source_path = expected_hook_source_path
        self.expected_hook_source_class = expected_hook_source_class
        self.expected_child_model = expected_child_model
        self.expected_child_effort = expected_child_effort
        self.events = 0
        self.initialize_ok = False
        self.parent_thread_id: str | None = None
        self.parent_turn_id: str | None = None
        self.parent_model: str | None = None
        self.parent_effort: str | None = None
        self.multi_agent_feature_enabled: bool | None = None
        self.feature_query_status = "not_requested"
        self.turn_terminal: str | None = None
        self.notification_counts: dict[str, int] = {}
        self.item_event_type_counts: dict[str, int] = {}
        self._terminal_message_digest: dict | None = None
        self._terminal_message_seen = False
        self._terminal_message_final = False
        self.calls: dict[str, dict] = {}
        self.hooks: dict[str, dict] = {}
        self.subagent_lineage: dict[str, dict] = {}
        self.subagent_activities: dict[str, dict] = {}
        self.subagent_completions: dict[str, str] = {}
        self.subagent_activity_conflict = False
        self.collab_tool_counts: dict[str, int] = {}
        self.collab_shape_counts: dict[str, int] = {}
        self.hook_event_counts: dict[str, int] = {}
        self.hook_shape_counts: dict[str, int] = {}
        self.subagent_kind_counts: dict[str, int] = {}
        self.subagent_shape_counts: dict[str, int] = {}
        self.invalid_spawn_items = 0
        self.malformed_typed_fields = 0

    def feed(self, frame: bytes) -> None:
        self.events += 1
        if self.events > MAX_EVENTS:
            raise ProbeFailure("event_limit")
        event = decode_frame(frame)
        if "id" in event:
            self._response(event)
            return
        method = event.get("method")
        params = _mapping(event.get("params"))
        if not isinstance(method, str):
            raise ProbeFailure("invalid_jsonrpc_frame")
        method_label = method if method in COUNTED_NOTIFICATIONS else "other"
        self.notification_counts[method_label] = self.notification_counts.get(method_label, 0) + 1
        if method in {"item/started", "item/completed", "item/updated"}:
            self._item(method, params)
        elif method in {"hook/started", "hook/completed"}:
            self._hook(method, params)
        elif method == "turn/completed":
            if (strict_id(params.get("threadId")) == self.parent_thread_id and
                    strict_id(_mapping(params.get("turn")).get("id")) == self.parent_turn_id):
                turn = _mapping(params.get("turn"))
                status = turn.get("status")
                self.turn_terminal = _enum(status, {"completed", "failed", "interrupted"})
                if self.turn_terminal is None:
                    self.malformed_typed_fields += 1
                    self.turn_terminal = "unknown"
                items = turn.get("items")
                if isinstance(items, list) and len(items) <= MAX_EVENTS:
                    for item in items:
                        self._record_agent_message(_mapping(item))
        # All other notifications, including assistant text, are intentionally ignored.

    def _response(self, event: dict) -> None:
        request_id = event.get("id")
        if type(request_id) is not int or request_id not in {1, 2, 3, 4}:
            raise ProbeFailure("unexpected_response")
        if request_id == 4:
            if not self.parent_thread_id or self.feature_query_status != "not_requested":
                raise ProbeFailure("unexpected_response")
            if "error" in event:
                self.feature_query_status = "unavailable"
                return
            features = _mapping(event.get("result")).get("data")
            if not isinstance(features, list) or len(features) > 256:
                self.feature_query_status = "unavailable"
                return
            matches = [_mapping(value).get("enabled") for value in features
                       if _mapping(value).get("name") == "multi_agent"]
            if len(matches) == 1 and type(matches[0]) is bool:
                self.multi_agent_feature_enabled = matches[0]
                self.feature_query_status = "available"
            else:
                self.feature_query_status = "unavailable"
            return
        if "error" in event:
            raise ProbeFailure("server_error")
        result = _mapping(event.get("result"))
        if request_id == 1:
            self.initialize_ok = bool(result)
            if not self.initialize_ok:
                raise ProbeFailure("initialize_missing")
        elif request_id == 2:
            if not self.initialize_ok or self.parent_thread_id is not None:
                raise ProbeFailure("unexpected_response")
            thread = _mapping(result.get("thread"))
            self.parent_thread_id = strict_id(thread.get("id"))
            self.parent_model = selector(thread.get("model")) or selector(result.get("model"))
            self.parent_effort = selector(thread.get("reasoningEffort"), effort=True)
            if not self.parent_thread_id:
                raise ProbeFailure("parent_thread_id_missing")
        else:
            if not self.parent_thread_id or self.parent_turn_id is not None:
                raise ProbeFailure("unexpected_response")
            self.parent_turn_id = strict_id(_mapping(result.get("turn")).get("id"))
            if not self.parent_turn_id:
                raise ProbeFailure("parent_turn_id_missing")

    def _item(self, method: str, params: dict) -> None:
        if (not self.parent_turn_id or
                strict_id(params.get("threadId")) != self.parent_thread_id or
                strict_id(params.get("turnId")) != self.parent_turn_id):
            if _mapping(params.get("item")).get("type") == "subAgentActivity":
                self.subagent_activity_conflict = True
                self._count(self.subagent_shape_counts, "non_parent_activity")
            return
        item = _mapping(params.get("item"))
        raw_type = item.get("type")
        type_label = raw_type if isinstance(raw_type, str) and raw_type in COUNTED_ITEM_TYPES else "other"
        self.item_event_type_counts[type_label] = self.item_event_type_counts.get(type_label, 0) + 1
        if raw_type == "agentMessage" and method == "item/completed":
            self._record_agent_message(item)
        if raw_type == "subAgentActivity":
            self._subagent_activity(item)
            return
        if item.get("type") != "collabAgentToolCall" or item.get("tool") != "spawnAgent":
            if raw_type == "collabAgentToolCall":
                tool = _enum(item.get("tool"), COLLAB_TOOLS) or "other"
                self._count(self.collab_tool_counts, tool)
                self._count(self.collab_shape_counts,
                            "non_spawn_tool" if tool != "other" else "unknown_tool")
            return
        self._count(self.collab_tool_counts, "spawnAgent")
        call_id = strict_id(item.get("id"))
        sender = strict_id(item.get("senderThreadId"))
        if not call_id:
            self._count(self.collab_shape_counts, "invalid_call_id")
            self.invalid_spawn_items += 1
            return
        if call_id not in self.calls and len(self.calls) >= MAX_CALLS:
            raise ProbeFailure("spawn_limit")
        sender_matches_parent = sender == self.parent_thread_id
        if not sender_matches_parent:
            self._count(self.collab_shape_counts,
                        "invalid_sender_id" if sender is None else "sender_mismatch")
            self.invalid_spawn_items += 1
        else:
            self._count(self.collab_shape_counts, "recognized_spawn")
        call = self.calls.setdefault(call_id, {"call_id": call_id, "sender_thread_id": sender,
                                               "sender_matches_parent": sender_matches_parent,
                                               "receiver_thread_ids": [], "prompt_present": False,
                                               "prompt_digest": None, "sentinel_exact_match": False,
                                               "spawn_requested_model": None,
                                               "spawn_requested_effort": None, "status": None,
                                               "inconsistent": False})
        if call["sender_thread_id"] != sender:
            call["inconsistent"] = True
        call["sender_matches_parent"] = call["sender_matches_parent"] and sender_matches_parent
        prompt = item.get("prompt")
        prompt_digest = digest_text(prompt)
        if prompt_digest is not None:
            if call["prompt_digest"] is not None and call["prompt_digest"] != prompt_digest:
                call["inconsistent"] = True
            call["prompt_present"] = True
            call["prompt_digest"] = prompt_digest
            call["sentinel_exact_match"] = prompt == self._sentinel_value
        for key, observed in (("spawn_requested_model", selector(item.get("model"))),
                              ("spawn_requested_effort",
                               selector(item.get("reasoningEffort"), effort=True))):
            if observed and call[key] and observed != call[key]:
                call["inconsistent"] = True
            call[key] = observed or call[key]
        receivers = item.get("receiverThreadIds")
        if isinstance(receivers, list) and len(receivers) <= 8:
            observed = sorted({value for value in receivers if strict_id(value) and value != sender})
            if observed and call["receiver_thread_ids"] and observed != call["receiver_thread_ids"]:
                call["inconsistent"] = True
            call["receiver_thread_ids"] = observed or call["receiver_thread_ids"]
        status = item.get("status")
        validated_status = _enum(status, {"inProgress", "completed", "failed", "interrupted"})
        if "status" in item and validated_status is None:
            self.malformed_typed_fields += 1
        call["status"] = validated_status or call["status"]

    @staticmethod
    def _count(counter: dict[str, int], label: str) -> None:
        counter[label] = counter.get(label, 0) + 1

    def _subagent_activity(self, item: dict) -> None:
        kind = _enum(item.get("kind"), SUBAGENT_KINDS) or "other"
        self._count(self.subagent_kind_counts, kind)
        activity_id = strict_id(item.get("id"))
        child_id = strict_id(item.get("agentThreadId"))
        if not activity_id:
            self._count(self.subagent_shape_counts, "invalid_activity_id")
            self.subagent_activity_conflict = True
            return
        if not child_id:
            self._count(self.subagent_shape_counts, "invalid_agent_thread_id")
            self.subagent_activity_conflict = True
            return
        self._count(self.subagent_shape_counts, "recognized_activity")
        observed = {"activity_id": activity_id, "agent_thread_id": child_id, "kind": kind}
        previous = self.subagent_activities.get(activity_id)
        if previous and previous != observed:
            self.subagent_activity_conflict = True
        if activity_id not in self.subagent_activities and len(self.subagent_activities) >= MAX_EVENTS:
            raise ProbeFailure("subagent_activity_limit")
        self.subagent_activities.setdefault(activity_id, observed)
        if kind == "started":
            if activity_id not in self.subagent_lineage and len(self.subagent_lineage) >= MAX_CALLS:
                raise ProbeFailure("subagent_lineage_limit")
            self.subagent_lineage.setdefault(activity_id, observed)
        elif kind == "completed":
            previous_completion = self.subagent_completions.get(child_id)
            if (not any(row["agent_thread_id"] == child_id
                        for row in self.subagent_lineage.values()) or
                    (previous_completion is not None and previous_completion != activity_id)):
                self.subagent_activity_conflict = True
            self.subagent_completions.setdefault(child_id, activity_id)
        else:
            self.subagent_activity_conflict = True

    def _record_agent_message(self, item: dict) -> None:
        if item.get("type") != "agentMessage":
            return
        phase = item.get("phase")
        if phase == "final_answer" or (phase is None and not self._terminal_message_final):
            text = item.get("text")
            if isinstance(text, str):
                self._terminal_message_seen = True
                self._terminal_message_digest = digest_text(text, limit=MAX_FRAME)
            if phase == "final_answer":
                self._terminal_message_final = True

    def _hook(self, method: str, params: dict) -> None:
        if (not self.parent_thread_id or
                strict_id(params.get("threadId")) != self.parent_thread_id):
            self._count(self.hook_shape_counts, "non_parent_thread")
            return
        run = _mapping(params.get("run"))
        if not run:
            self._count(self.hook_shape_counts, "missing_run")
            return
        hook_digest = hook_id_digest(run.get("id"))
        raw_event_name = run.get("eventName")
        if "eventName" in run and not isinstance(raw_event_name, str):
            self.malformed_typed_fields += 1
        event_name = _enum(raw_event_name, ALL_HOOK_EVENTS) or "other"
        self._count(self.hook_event_counts, event_name)
        if event_name not in HOOK_EVENTS:
            self._count(self.hook_shape_counts, "non_audit_event")
            return
        if hook_digest is None:
            self._count(self.hook_shape_counts, "invalid_hook_id")
            return
        hook_id = hook_digest["sha256"]
        if hook_id not in self.hooks and len(self.hooks) >= MAX_HOOKS:
            raise ProbeFailure("hook_limit")
        turn_id = strict_id(params.get("turnId"))
        turn_matches_parent = bool(self.parent_turn_id and turn_id == self.parent_turn_id)
        if not turn_matches_parent:
            self._count(self.hook_shape_counts,
                        "unlinked_turn" if turn_id is None else "other_turn")
        else:
            self._count(self.hook_shape_counts, "recognized_audit_hook")
        source_is_plugin = _enum(run.get("source"), {"plugin"}) == "plugin"
        source_matches_expected = run.get("source") == self.expected_hook_source_class
        handler_is_command = _enum(run.get("handlerType"), {"command"}) == "command"
        scope_is_turn = _enum(run.get("scope"), {"turn"}) == "turn"
        source_path_match = _same_path(run.get("sourcePath"), self.expected_hook_source_path)
        identity_valid = (source_matches_expected and handler_is_command and
                          scope_is_turn and source_path_match)
        hook = self.hooks.setdefault(hook_id, {"hook_id_sha256": hook_id,
                                               "hook_id_bytes": hook_digest["bytes"],
                                               "event_name": event_name,
                                               "started": False, "completed": False, "status": None,
                                               "entry_kind_counts": {},
                                               "failure_category": None,
                                               "entry_diagnostic_conflict": False,
                                               "router_audit_identity": identity_valid,
                                               "turn_matches_parent": turn_matches_parent,
                                               "source_is_plugin": source_is_plugin,
                                               "source_matches_expected": source_matches_expected,
                                               "handler_is_command": handler_is_command,
                                               "scope_is_turn": scope_is_turn,
                                               "source_path_match": source_path_match})
        if hook["event_name"] != event_name:
            hook["router_audit_identity"] = False
            self._count(self.hook_shape_counts, "event_name_changed")
            return
        hook["router_audit_identity"] = hook["router_audit_identity"] and identity_valid
        for key, observed in (("turn_matches_parent", turn_matches_parent),
                              ("source_is_plugin", source_is_plugin),
                              ("source_matches_expected", source_matches_expected),
                              ("handler_is_command", handler_is_command),
                              ("scope_is_turn", scope_is_turn),
                              ("source_path_match", source_path_match)):
            hook[key] = hook[key] and observed
        if method == "hook/started":
            hook["started"] = True
        else:
            hook["completed"] = True
        status = run.get("status")
        validated_status = _enum(status, HOOK_STATUSES)
        if "status" in run and validated_status is None:
            self.malformed_typed_fields += 1
        hook["status"] = validated_status or hook["status"]
        if (method == "hook/completed" and hook["router_audit_identity"] and
                hook["turn_matches_parent"]):
            self._record_hook_failure(run, hook)

    def _record_hook_failure(self, run: dict, hook: dict) -> None:
        entries = run.get("entries")
        status_message = run.get("statusMessage")
        malformed = not isinstance(entries, list) or len(entries) > 16
        if status_message is not None and digest_text(status_message, limit=1024) is None:
            malformed = True
        counts: dict[str, int] = {}
        candidate_categories: set[str] = set()
        if isinstance(entries, list) and len(entries) <= 16:
            for raw_entry in entries:
                entry = _mapping(raw_entry)
                kind = _enum(entry.get("kind"), HOOK_ENTRY_KINDS) or "other"
                self._count(counts, kind)
                if not isinstance(raw_entry, dict) or kind == "other":
                    malformed = True
                value = entry.get("text")
                if digest_text(value, limit=MAX_TEXT) is None:
                    malformed = True
                    continue
                if kind == "error" and hook["status"] == "failed":
                    candidate_categories.add(_hook_failure_category(value))
        if (hook["status"] == "failed" and isinstance(status_message, str) and
                digest_text(status_message, limit=1024) is not None):
            status_category = _hook_failure_category(status_message)
            if status_category != "other_unknown":
                candidate_categories.add(status_category)
        if malformed:
            self.malformed_typed_fields += 1
        category = None
        if hook["status"] == "failed":
            category = (next(iter(candidate_categories)) if not malformed and
                        len(candidate_categories) == 1 else "other_unknown")
        if hook["entry_kind_counts"] and hook["entry_kind_counts"] != counts:
            hook["entry_diagnostic_conflict"] = True
            category = "other_unknown" if hook["status"] == "failed" else None
        hook["entry_kind_counts"] = counts
        hook["failure_category"] = category

    def receipt(self, *, source: str, executable: dict | None = None) -> dict:
        calls = [self.calls[key] for key in sorted(self.calls)]
        hooks = [self.hooks[key] for key in sorted(self.hooks)]
        hook_entry_kind_counts: dict[str, int] = {}
        hook_failure_counts: dict[str, int] = {}
        for hook in hooks:
            if not hook["router_audit_identity"] or not hook["turn_matches_parent"]:
                continue
            for kind, count in hook["entry_kind_counts"].items():
                hook_entry_kind_counts[kind] = hook_entry_kind_counts.get(kind, 0) + count
            if hook["failure_category"] is not None:
                category = hook["failure_category"]
                hook_failure_counts[category] = hook_failure_counts.get(category, 0) + 1
        complete_hooks = all(any(h["event_name"] == event_name and h["started"] and
                                     h["completed"] and h["status"] == "completed" and
                                     h["router_audit_identity"] and h["turn_matches_parent"]
                                     for h in hooks)
                             for event_name in HOOK_EVENTS)
        complete_call = len(calls) == 1 and self.invalid_spawn_items == 0 and any(
                            c["sender_matches_parent"] and c["prompt_present"] and
                            c["sentinel_exact_match"] and
                            c["spawn_requested_model"] == self.expected_child_model and
                            c["spawn_requested_effort"] == self.expected_child_effort
                            and len(c["receiver_thread_ids"]) == 1
                            and c["status"] == "completed" and not c["inconsistent"]
                            for c in calls)
        approved_parent_request = ((self.requested_parent_model, self.requested_parent_effort)
                                   in APPROVED_PARENT_SELECTORS)
        thread_model_matches_requested = (approved_parent_request and
                                          self.parent_model == self.requested_parent_model)
        complete = bool(source == "launched_app_server" and self.turn_terminal == "completed"
                        and complete_call and complete_hooks and thread_model_matches_requested
                        and self.malformed_typed_fields == 0 and not self.subagent_activity_conflict)
        return {"schema_version": 1, "source": source, "executable": executable,
                "parent_thread_id": self.parent_thread_id, "parent_turn_id": self.parent_turn_id,
                "thread_model_at_start": self.parent_model,
                "thread_configured_effort_at_start": self.parent_effort,
                "requested_turn_model": self.requested_parent_model,
                "requested_turn_effort": self.requested_parent_effort,
                "turn_terminal": self.turn_terminal, "expected_message_digest": self.expected,
                "malformed_typed_fields": self.malformed_typed_fields,
                "calls": calls, "hooks": hooks,
                "subagent_lineage": [self.subagent_lineage[key]
                                     for key in sorted(self.subagent_lineage)],
                "diagnostics": {"notification_counts": dict(sorted(self.notification_counts.items())),
                                "item_event_type_counts": dict(sorted(self.item_event_type_counts.items())),
                                "collab_tool_counts": dict(sorted(self.collab_tool_counts.items())),
                                "collab_shape_counts": dict(sorted(self.collab_shape_counts.items())),
                                "hook_event_counts": dict(sorted(self.hook_event_counts.items())),
                                "hook_shape_counts": dict(sorted(self.hook_shape_counts.items())),
                                "audit_hook_entry_kind_counts": dict(sorted(
                                    hook_entry_kind_counts.items())),
                                "audit_hook_failure_category_counts": dict(sorted(
                                    hook_failure_counts.items())),
                                "subagent_kind_counts": dict(sorted(self.subagent_kind_counts.items())),
                                "subagent_shape_counts": dict(sorted(self.subagent_shape_counts.items())),
                                "subagent_activity_conflict": self.subagent_activity_conflict,
                                "terminal_agent_message_observed": bool(
                                    self.turn_terminal == "completed" and self._terminal_message_seen),
                                "terminal_agent_message_digest": (
                                    self._terminal_message_digest if self.turn_terminal == "completed"
                                    and self._terminal_message_seen else None),
                                "multi_agent_feature_enabled": self.multi_agent_feature_enabled,
                                "multi_agent_feature_query_status": self.feature_query_status},
                "capabilities": {"typed_spawn_prompt_and_selectors": bool(complete_call),
                                 "hook_lifecycle": complete_hooks,
                                 "approved_parent_selector_request": approved_parent_request,
                                 "thread_model_matches_requested": bool(thread_model_matches_requested),
                                 "usable_for_dispatch_provenance": complete,
                                 "classification": "complete_capability" if complete else "partial_capability"}}


class FixedSentinelReducer(Reducer):
    """Reduce trusted session-hook context without retaining hook output text."""

    def __init__(self, sentinel: str, source_path: str, *, canary_private_key=None,
                 canary_run_id: str | None = None):
        super().__init__(sentinel, requested_parent_model="gpt-6-sol",
                         requested_parent_effort="low",
                         expected_hook_source_path=source_path,
                         expected_hook_source_class="sessionFlags",
                         expected_child_model="gpt-6-astra",
                         expected_child_effort="xhigh")
        self.audit_attempts: dict[str, dict] = {}
        self.audit_context_malformed = 0
        self.child_threads: dict[str, dict] = {}
        self.child_thread_conflict = False
        self.native_activity_markers: set[tuple[str, str, str]] = set()
        self.child_business_items: set[str] = set()
        self.child_user_messages: set[str] = set()
        self.parent_followup_items: set[str] = set()
        self._canary_private_key = canary_private_key
        self._canary_run_id = canary_run_id
        self.canary_records: dict[str, dict] = {}
        self.canary_malformed = 0
        self.canary_context_leak = False
        self.canary_agent_text_seen = False
        self.canary_context_exclusion_unknown = False

    def feed(self, frame: bytes) -> None:
        if self.events >= MAX_EVENTS:
            self.canary_context_exclusion_unknown = True
        try:
            event = decode_frame(frame)
        except ProbeFailure:
            self.canary_context_exclusion_unknown = True
            raise
        method = event.get("method")
        if method == "hook/completed":
            entries = _mapping(_mapping(event.get("params")).get("run")).get("entries")
            if not isinstance(entries, list) or len(entries) > 16:
                self.canary_context_exclusion_unknown = True
            else:
                for entry in entries:
                    if (not isinstance(entry, dict) or
                            _enum(entry.get("kind"), HOOK_ENTRY_KINDS) is None or
                            digest_text(entry.get("text"), limit=MAX_CANARY_ENTRY) is None):
                        self.canary_context_exclusion_unknown = True
                        continue
                    if entry["kind"] == "context" and CANARY_PREFIX in entry["text"]:
                        self.canary_context_leak = True
        elif method == "turn/completed":
            turn = _mapping(_mapping(event.get("params")).get("turn"))
            if "items" in turn:
                items = turn["items"]
                if not isinstance(items, list) or len(items) > MAX_EVENTS:
                    self.canary_context_exclusion_unknown = True
                else:
                    for item in items:
                        if not isinstance(item, dict):
                            self.canary_context_exclusion_unknown = True
                        elif item.get("type") == "agentMessage":
                            text_value = item.get("text")
                            if not isinstance(text_value, str):
                                self.canary_context_exclusion_unknown = True
                            elif CANARY_PREFIX in text_value:
                                self.canary_agent_text_seen = True
        if method in {"item/started", "item/completed", "item/updated"}:
            params = _mapping(event.get("params"))
            if not isinstance(params.get("item"), dict):
                self.canary_context_exclusion_unknown = True
            item = _mapping(params.get("item"))
            if item.get("type") == "agentMessage":
                if not isinstance(item.get("text"), str):
                    self.canary_context_exclusion_unknown = True
                else:
                    self.canary_agent_text_seen |= CANARY_PREFIX in item["text"]
            event_thread = strict_id(params.get("threadId"))
            item_id = hook_id_digest(item.get("id"))
            item_key = item_id["sha256"] if item_id else f"unknown-{self.events}"
            if self.parent_thread_id and event_thread == self.parent_thread_id:
                if item.get("type") == "subAgentActivity":
                    activity = strict_id(item.get("id"))
                    child = strict_id(item.get("agentThreadId"))
                    kind = _enum(item.get("kind"), SUBAGENT_KINDS)
                    if activity and child and kind and kind != "completed":
                        self.native_activity_markers.add((activity, child, kind))
                if (item.get("type") == "collabAgentToolCall" and
                        (not isinstance(item.get("tool"), str) or
                         item.get("tool") not in {"spawnAgent", "wait"})):
                    self.parent_followup_items.add(item_key)
            elif self.parent_thread_id and event_thread and event_thread != self.parent_thread_id:
                item_type = item.get("type")
                if item_type == "userMessage":
                    self.child_user_messages.add(item_key)
                elif not isinstance(item_type, str) or item_type not in CHILD_NONBUSINESS_ITEM_TYPES:
                    self.child_business_items.add(item_key)
        if event.get("method") == "thread/started":
            thread = _mapping(_mapping(event.get("params")).get("thread"))
            child_id = strict_id(thread.get("id"))
            if (child_id and self.parent_thread_id and
                    strict_id(thread.get("parentThreadId")) == self.parent_thread_id):
                if child_id not in self.child_threads and len(self.child_threads) >= MAX_CALLS:
                    raise ProbeFailure("child_thread_limit")
                observed_child = {
                    "child_thread_id": child_id,
                    "thread_model_at_start": selector(thread.get("model")),
                    "thread_configured_effort_at_start": selector(
                        thread.get("reasoningEffort"), effort=True),
                }
                if (child_id in self.child_threads and
                        self.child_threads[child_id] != observed_child):
                    self.child_thread_conflict = True
                self.child_threads[child_id] = observed_child
        try:
            super().feed(frame)
        except ProbeFailure:
            self.canary_context_exclusion_unknown = True
            raise

    def _record_hook_failure(self, run: dict, hook: dict) -> None:
        # The encrypted warning is bounded by the hook's own output limit, not
        # the smaller diagnostic-text limit used by ordinary audit entries.
        if (self._canary_private_key is not None and hook["event_name"] == "preToolUse" and
                hook["status"] == "completed"):
            entries = run.get("entries")
            if isinstance(entries, list) and len(entries) <= 16:
                counts: dict[str, int] = {}
                valid = True
                for raw in entries:
                    entry = _mapping(raw)
                    kind = _enum(entry.get("kind"), HOOK_ENTRY_KINDS) or "other"
                    self._count(counts, kind)
                    value = entry.get("text")
                    if (not isinstance(raw, dict) or kind == "other" or
                            digest_text(value, limit=MAX_CANARY_ENTRY) is None):
                        valid = False
                hook["entry_kind_counts"] = counts
                if valid:
                    return
            self.malformed_typed_fields += 1
            return
        super()._record_hook_failure(run, hook)

    def _hook(self, method: str, params: dict) -> None:
        super()._hook(method, params)
        if method != "hook/completed":
            return
        run = _mapping(params.get("run"))
        identity = hook_id_digest(run.get("id"))
        hook = self.hooks.get(identity["sha256"]) if identity else None
        if not hook or not hook["router_audit_identity"] or not hook["turn_matches_parent"]:
            return
        if hook["status"] != "completed":
            return
        entries = run.get("entries")
        if not isinstance(entries, list) or len(entries) > 16:
            if hook["event_name"] == "preToolUse" and self._canary_private_key is not None:
                self.canary_malformed += 1
            return
        if hook["event_name"] == "preToolUse" and self._canary_private_key is not None:
            warnings = [entry.get("text") for entry in entries if isinstance(entry, dict)
                        and entry.get("kind") == "warning"]
            if (len(entries) != 1 or len(warnings) != 1 or
                    not isinstance(warnings[0], str) or run.get("statusMessage") is not None):
                self.canary_malformed += 1
                return
            record = self._canary_record(warnings[0], run.get("id"), hook)
            if record is None:
                self.canary_malformed += 1
                return
            call_id = record["call_id"]
            if call_id in self.canary_records or len(self.canary_records) >= MAX_CALLS:
                self.canary_malformed += 1
                return
            self.canary_records[call_id] = record
            return
        for entry in entries:
            item = _mapping(entry)
            value = item.get("text")
            if item.get("kind") != "context" or not isinstance(value, str) or not value.startswith(
                    AUDIT_CONTEXT_PREFIX):
                continue
            record = self._audit_record(value[len(AUDIT_CONTEXT_PREFIX):], hook,
                                        run.get("id"))
            if record is None:
                self.audit_context_malformed += 1
                continue
            attempt_id = record["attempt_id"]
            if attempt_id not in self.audit_attempts and len(self.audit_attempts) >= MAX_CALLS:
                raise ProbeFailure("audit_attempt_limit")
            attempt = self.audit_attempts.setdefault(attempt_id, {"attempt_id": attempt_id})
            kind = record["kind"]
            if kind in attempt and (self._canary_private_key is not None or
                                    attempt[kind] != record):
                attempt["conflict"] = True
            attempt[kind] = record

    def _canary_record(self, text_value: str, run_id: object, hook: dict) -> dict | None:
        if (not text_value.startswith(CANARY_PREFIX) or
                digest_text(text_value, limit=MAX_CANARY_ENTRY) is None):
            return None
        try:
            envelope = json.loads(text_value[len(CANARY_PREFIX):],
                                  object_pairs_hook=_no_duplicates,
                                  parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if (not isinstance(envelope, dict) or set(envelope) != {
                    "schema_version", "suite", "header", "wrapped_key_b64",
                    "nonce_b64", "ciphertext_b64"} or
                    envelope["schema_version"] != 1 or envelope["suite"] != CANARY_SUITE):
                return None
            header = envelope["header"]
            if not isinstance(header, dict) or set(header) != {
                    "run_id", "phase", "session_id", "turn_id", "call_id",
                    "message_sha256", "message_bytes"}:
                return None
            call_id = header["call_id"]
            size = header["message_bytes"]
            digest = header["message_sha256"]
            if (header["run_id"] != self._canary_run_id or header["phase"] != "pre" or
                    header["session_id"] != self.parent_thread_id or
                    header["turn_id"] != self.parent_turn_id or
                    not isinstance(call_id, str) or not AUDIT_ATTEMPT_RE.fullmatch(call_id) or
                    not isinstance(run_id, str) or not run_id.endswith(":" + call_id) or
                    type(size) is not int or not 0 < size <= 65_536 or
                    not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
                return None
            wrapped = base64.b64decode(envelope["wrapped_key_b64"], validate=True)
            nonce = base64.b64decode(envelope["nonce_b64"], validate=True)
            ciphertext = base64.b64decode(envelope["ciphertext_b64"], validate=True)
            if (len(wrapped) != self._canary_private_key.key_size // 8 or
                    len(nonce) != 12 or len(ciphertext) != size + 16):
                return None
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import padding
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            key = self._canary_private_key.decrypt(wrapped, padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(), label=None))
            if len(key) != 32:
                return None
            aad = json.dumps(header, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True).encode("ascii")
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, aad)
            if (len(plaintext) != size or hashlib.sha256(plaintext).hexdigest() != digest):
                return None
            decoded = plaintext.decode("utf-8")
            return {"call_id": call_id, "hook_id_sha256": hook["hook_id_sha256"],
                    "message_sha256": digest, "message_bytes": size,
                    "contains_expected_sentinel": self._sentinel_value in decoded,
                    "sentinel_exact_match": decoded == self._sentinel_value,
                    "aad_verified": True}
        except Exception:
            # JSON, base64, RSA, AES-GCM and UTF-8 failures are indistinguishable.
            return None

    def _audit_record(self, raw: str, hook: dict, run_id: object = None) -> dict | None:
        if len(raw.encode("utf-8", errors="ignore")) > MAX_TEXT:
            return None
        try:
            value = json.loads(raw, object_pairs_hook=_no_duplicates)
        except (ProbeFailure, ValueError, TypeError, RecursionError):
            return None
        if not isinstance(value, dict):
            return None
        kind = value.get("kind")
        attempt_id = value.get("attempt_id")
        expected_kind = "pre" if hook["event_name"] == "preToolUse" else "post"
        if (value.get("schema_version") != 1 or kind != expected_kind or
                value.get("tool_name") != "collaborationspawn_agent" or
                value.get("session_id") != self.parent_thread_id or
                value.get("turn_id") != self.parent_turn_id or
                not isinstance(attempt_id, str) or not AUDIT_ATTEMPT_RE.fullmatch(attempt_id)):
            return None
        if (self._canary_private_key is not None and
                (not isinstance(run_id, str) or not run_id.endswith(":" + attempt_id))):
            return None
        reduced = {"attempt_id": attempt_id, "kind": kind,
                   "hook_id_sha256": hook["hook_id_sha256"]}
        if kind == "pre":
            digest = value.get("message_sha256")
            size = value.get("message_bytes")
            if (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or
                    type(size) is not int or not 0 < size <= 65_536):
                return None
            selector_state = _enum(value.get("selector_state"), {"explicit", "omitted"})
            if (selector_state is None or value.get("native_name") != FIXED_SPAWN_NAME or
                    value.get("fork_turns") != "none"):
                return None
            if selector_state == "omitted" and (value.get("model") is not None or
                                                 value.get("effort") is not None):
                return None
            reduced.update({"message_sha256": digest, "message_bytes": size,
                            "selector_state": selector_state,
                            "native_name_matches": True, "fork_turns_none": True})
        else:
            child_id = value.get("child_id")
            if child_id is not None and (not isinstance(child_id, str) or
                                         not AUDIT_ATTEMPT_RE.fullmatch(child_id)):
                return None
            observed = _mapping(value.get("post_observed_input"))
            digest = observed.get("message_sha256")
            size = observed.get("message_bytes")
            if (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or
                    type(size) is not int or not 0 < size <= 65_536):
                return None
            reduced.update({"child_id": child_id,
                            "result_observed": value.get("result_observed") is True,
                            "post_observed_input": {"sha256": digest, "bytes": size}})
        return reduced

    def receipt(self, *, source: str, executable: dict | None = None) -> dict:
        result = super().receipt(source=source, executable=executable)
        attempts = []
        for attempt_id in sorted(self.audit_attempts.keys() | self.canary_records.keys()):
            attempt = self.audit_attempts.get(attempt_id, {})
            pre = attempt.get("pre")
            post = attempt.get("post")
            canary_internal = self.canary_records.get(attempt_id)
            canary = ({key: value for key, value in canary_internal.items()
                       if key != "call_id"} if canary_internal else None)
            sentinel_match = bool(pre and pre.get("message_sha256") == self.expected["sha256"]
                                  and pre.get("message_bytes") == self.expected["bytes"])
            pre_commitment = pre or canary
            post_matches = bool(pre_commitment and post and
                                post.get("post_observed_input") == {
                                    "sha256": pre_commitment["message_sha256"],
                                    "bytes": pre_commitment["message_bytes"]})
            activity_matches = any(item["activity_id"] == attempt_id and
                                   item["agent_thread_id"] == post.get("child_id")
                                   for item in result["subagent_lineage"]) if post else False
            child = self.child_threads.get(post.get("child_id")) if post else None
            child_selector_match = bool(child and
                                        child["thread_model_at_start"] == "gpt-6-astra" and
                                        child["thread_configured_effort_at_start"] == "xhigh")
            attempts.append({"attempt_id": attempt_id, "pre": pre, "post": post,
                             "encrypted_pre": canary,
                             "sentinel_digest_match": sentinel_match,
                             "post_observed_input_match": post_matches,
                             "native_activity_child_match": activity_matches,
                             "child_configured_selector_match": child_selector_match,
                             "role_selector_omitted": bool(pre and
                                                          pre.get("selector_state") == "omitted"),
                             "single_native_activity": bool(
                                 len(self.native_activity_markers) == 1 and
                                 (attempt_id, post.get("child_id"), "started") in
                                 self.native_activity_markers) if post else False,
                             "single_child_thread": bool(
                                 len(self.child_threads) == 1 and
                                 post.get("child_id") in self.child_threads) if post else False,
                             "conflict": attempt.get("conflict") is True})
        context_complete = self.audit_context_malformed == 0 and len(attempts) == 1 and any(
                               item["sentinel_digest_match"] and
                               item["post_observed_input_match"] and
                               item["native_activity_child_match"] and
                               item["child_configured_selector_match"] and
                               item["role_selector_omitted"] and
                               item["single_native_activity"] and
                               item["single_child_thread"] and
                               item["post"] and item["post"]["result_observed"] and
                               not item["conflict"] for item in attempts)
        context_complete = bool(context_complete and not self.child_business_items and
                                not self.child_thread_conflict and
                                not self.subagent_activity_conflict and
                                len(self.child_user_messages) <= 1 and
                                not self.parent_followup_items and
                                len(result["subagent_lineage"]) == 1 and
                                len(result["calls"]) <= 1 and
                                self.invalid_spawn_items == 0)
        packet_complete = bool(self._canary_private_key is not None and
            self.canary_malformed == 0 and self.audit_context_malformed == 0 and
            result["capabilities"]["hook_lifecycle"] and
            result["capabilities"]["thread_model_matches_requested"] and
            not self.canary_context_exclusion_unknown and
            not self.canary_context_leak and
            not self.canary_agent_text_seen and len(self.canary_records) == 1 and
            len(attempts) == 1 and not self.child_business_items and
            not self.child_thread_conflict and len(self.child_user_messages) <= 1 and
            not self.subagent_activity_conflict and
            not self.parent_followup_items and len(result["subagent_lineage"]) == 1 and
            len(result["calls"]) <= 1 and self.invalid_spawn_items == 0 and
            result["turn_terminal"] == "completed" and all(
                item["encrypted_pre"] and item["encrypted_pre"]["sentinel_exact_match"] and
                item["post"] and item["post_observed_input_match"] and
                item["native_activity_child_match"] and
                item["child_configured_selector_match"] and
                item["single_native_activity"] and item["single_child_thread"] and
                item["post"]["result_observed"] and not item["conflict"]
                for item in attempts))
        result["mode"] = "fixed_sentinel_probe"
        result["audit_attempts"] = attempts
        result["child_threads"] = [self.child_threads[key] for key in sorted(self.child_threads)]
        result["diagnostics"]["audit_context_malformed"] = self.audit_context_malformed
        result["diagnostics"]["packet_canary_malformed"] = self.canary_malformed
        result["diagnostics"]["packet_canary_context_entry_seen"] = self.canary_context_leak
        result["diagnostics"]["packet_canary_agent_text_seen"] = self.canary_agent_text_seen
        result["diagnostics"]["context_exclusion_unknown"] = (
            self.canary_context_exclusion_unknown)
        result["diagnostics"]["child_business_item_count"] = len(self.child_business_items)
        result["diagnostics"]["child_user_message_count"] = len(self.child_user_messages)
        result["diagnostics"]["parent_followup_item_count"] = len(self.parent_followup_items)
        result["diagnostics"]["native_activity_marker_count"] = len(
            self.native_activity_markers)
        result["diagnostics"]["child_thread_conflict"] = self.child_thread_conflict
        result["capabilities"]["hook_context"] = "OBSERVED" if context_complete else "UNKNOWN"
        result["capabilities"]["encrypted_pre_packet"] = "OBSERVED" if packet_complete else "UNKNOWN"
        result["capabilities"]["context_exclusion"] = (
            "OBSERVED" if self.canary_records and not self.canary_context_exclusion_unknown
            and not self.canary_context_leak and not self.canary_agent_text_seen
            and result["turn_terminal"] == "completed" else "UNKNOWN")
        result["capabilities"]["system_message_warning_only"] = bool(
            self.canary_records and self.canary_malformed == 0 and
            not self.canary_context_exclusion_unknown and
            not self.canary_context_leak and
            not self.canary_agent_text_seen)
        result["capabilities"]["effective_dispatch_proof"] = "UNKNOWN"
        result["capabilities"]["usable_for_dispatch_provenance"] = False
        result["capabilities"]["classification"] = "partial_capability"
        return result


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validated_router_hook_source(config_path: Path, script_path: Path) -> str:
    if (not config_path.is_absolute() or not script_path.is_absolute() or
            not config_path.is_file() or not script_path.is_file() or
            config_path.name != "hooks.json" or script_path.name != "dispatch_audit.py" or
            config_path.parent.resolve() != script_path.parent.resolve()):
        raise ProbeFailure("invalid_router_hook_identity")
    with config_path.open("rb") as stream:
        raw = stream.read(MAX_HOOK_CONFIG + 1)
    if len(raw) > MAX_HOOK_CONFIG:
        raise ProbeFailure("oversized_hook_config")
    try:
        config = json.loads(raw, object_pairs_hook=_no_duplicates)
    except ProbeFailure:
        raise
    except (UnicodeError, ValueError, RecursionError, TypeError):
        raise ProbeFailure("invalid_router_hook_identity") from None
    hooks = _mapping(_mapping(config).get("hooks"))
    for event_name in ("PreToolUse", "PostToolUse"):
        entries = hooks.get(event_name)
        if not isinstance(entries, list) or len(entries) != 1:
            raise ProbeFailure("invalid_router_hook_identity")
        entry = _mapping(entries[0])
        handlers = entry.get("hooks")
        if (entry.get("matcher") != AUDIT_HOOK_MATCHER or not isinstance(handlers, list) or
                len(handlers) != 1):
            raise ProbeFailure("invalid_router_hook_identity")
        handler = _mapping(handlers[0])
        if (handler.get("type") != "command" or
                handler.get("commandWindows") != AUDIT_HOOK_COMMAND):
            raise ProbeFailure("invalid_router_hook_identity")
    return str(config_path.resolve())


def _send(process: subprocess.Popen, request: dict) -> None:
    frame = (json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8")
    if len(frame) > MAX_FRAME:
        raise ProbeFailure("oversized_request")
    try:
        process.stdin.write(frame)
        process.stdin.flush()
    except (OSError, BrokenPipeError):
        raise ProbeFailure("protocol_write_failed") from None


def _reader(stream, messages: queue.Queue) -> None:
    try:
        while True:
            frame = stream.readline(MAX_FRAME + 1)
            messages.put(frame)
            if not frame or len(frame) > MAX_FRAME:
                break
    except OSError:
        messages.put(None)


def drive_protocol(process: subprocess.Popen, reducer: Reducer, prompt: str,
                   model: str, effort: str, timeout: float, workspace: str,
                   pre_thread_check: Callable[[dict], None] | None = None) -> None:
    if ((model, effort) not in APPROVED_PARENT_SELECTORS or
            reducer.requested_parent_model != model or
            reducer.requested_parent_effort != effort):
        raise ProbeFailure("unapproved_parent_selector")
    messages: queue.Queue = queue.Queue(maxsize=32)
    threading.Thread(target=_reader, args=(process.stdout, messages), daemon=True).start()
    deadline = time.monotonic() + timeout
    phase = 1
    stage = "initialize"

    def send(request: dict, request_stage: str) -> None:
        nonlocal stage
        stage = request_stage
        try:
            _send(process, request)
        except ProbeFailure as error:
            raise ProbeFailure(error.code, stage) from None
        except OSError:
            raise ProbeFailure("protocol_write_failed", stage) from None

    send({"id": 1, "method": "initialize",
          "params": {"clientInfo": {"name": "codex-model-router-probe", "version": "1"}}},
         "initialize")
    while reducer.turn_terminal is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProbeFailure("timeout", stage)
        try:
            frame = messages.get(timeout=remaining)
        except queue.Empty:
            raise ProbeFailure("timeout", stage) from None
        if frame is None:
            raise ProbeFailure("protocol_read_failed", stage)
        if frame == b"":
            try:
                exited = process.poll() is not None
            except OSError:
                exited = False
            raise ProbeFailure("process_exited" if exited else "protocol_eof", stage)
        if phase == 5:
            preflight = decode_frame(frame)
            if preflight.get("id") == 5:
                if "error" in preflight or not isinstance(preflight.get("result"), dict):
                    raise ProbeFailure("fixed_inventory_list_failed", "hook_live_preflight")
                try:
                    pre_thread_check(preflight["result"])
                except ProbeFailure:
                    raise
                send({"id": 2, "method": "thread/start",
                      "params": {"cwd": workspace, "model": model, "sandbox": "read-only",
                                 "approvalPolicy": "never", "ephemeral": True}}, "thread_start")
                phase = 2
                continue
        try:
            reducer.feed(frame)
        except ProbeFailure as error:
            raise ProbeFailure(error.code, stage) from None
        if phase == 1 and reducer.initialize_ok:
            send({"method": "initialized", "params": {}}, "thread_start")
            if pre_thread_check is not None:
                send({"id": 5, "method": "hooks/list", "params": {"cwds": [workspace]}},
                     "hook_live_preflight")
                phase = 5
            else:
                send({"id": 2, "method": "thread/start",
                      "params": {"cwd": workspace, "model": model, "sandbox": "read-only",
                                 "approvalPolicy": "never", "ephemeral": True}}, "thread_start")
                phase = 2
        elif phase == 2 and reducer.parent_thread_id:
            send({"id": 4, "method": "experimentalFeature/list",
                  "params": {"threadId": reducer.parent_thread_id, "limit": 128}}, "feature_query")
            phase = 3
        elif phase == 3 and reducer.feature_query_status != "not_requested":
            send({"id": 3, "method": "turn/start",
                  "params": {"threadId": reducer.parent_thread_id,
                             "input": [{"type": "text", "text": prompt}],
                             "model": model, "effort": effort, "cwd": workspace,
                             "sandboxPolicy": {"type": "readOnly"},
                             "approvalPolicy": "never"}}, "turn_start")
            phase = 4
        if phase == 4 and reducer.parent_turn_id:
            stage = "turn_execution"


def run_live(cli: Path, prompt: str, sentinel: str, model: str, effort: str,
             timeout: float, expected_sha256: str | None, hook_config: Path,
             audit_script: Path, hook_files: list[Path]) -> dict:
    if os.name != "nt" or not cli.is_absolute() or not cli.is_file() or cli.suffix.lower() != ".exe":
        raise ProbeFailure("invalid_windows_executable")
    if (model, effort) not in APPROVED_PARENT_SELECTORS:
        raise ProbeFailure("unapproved_parent_selector")
    hook_source_path = _validated_router_hook_source(hook_config, audit_script)
    cli_hash = _file_sha256(cli)
    if expected_sha256 and cli_hash != expected_sha256:
        raise ProbeFailure("executable_hash_mismatch")
    hook_hashes = [_file_sha256(path) for path in hook_files if path.is_absolute() and path.is_file()]
    if len(hook_hashes) != len(hook_files):
        raise ProbeFailure("invalid_hook_file")
    try:
        version = subprocess.run([str(cli), "--version"], capture_output=True,
                                 timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ProbeFailure("version_probe_failed", "spawn") from None
    version_text = version.stdout.decode("ascii", errors="ignore").strip()
    if version.returncode or not re.fullmatch(r"codex-cli [0-9A-Za-z.+-]{1,48}", version_text):
        raise ProbeFailure("invalid_executable_version")
    executable = {"sha256": cli_hash, "version": version_text,
                  "router_hook_config_sha256": _file_sha256(hook_config),
                  "router_audit_script_sha256": _file_sha256(audit_script),
                  "additional_hook_sha256": hook_hashes}
    reducer = Reducer(sentinel, requested_parent_model=model, requested_parent_effort=effort,
                      expected_hook_source_path=hook_source_path)
    try:
        temporary_workspace = tempfile.TemporaryDirectory(prefix="codex-app-probe-")
    except OSError:
        raise ProbeFailure("workspace_setup_failed", "spawn") from None
    workspace = temporary_workspace.name
    process: subprocess.Popen | None = None
    failure: ProbeFailure | None = None
    teardown_failed = False
    try:
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            process = subprocess.Popen([str(cli), "app-server", "--stdio"], cwd=workspace,
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL, creationflags=creation_flags)
        except OSError:
            failure = ProbeFailure("spawn_failed", "spawn")
        if process is not None:
            try:
                drive_protocol(process, reducer, prompt, model, effort, timeout, workspace)
            except ProbeFailure as error:
                failure = error
            except (OSError, subprocess.TimeoutExpired):
                failure = ProbeFailure("protocol_io_failed", "protocol")
    finally:
        if process is not None:
            try:
                observed_exit = process.poll()
                running = observed_exit is None
                if observed_exit not in (None, 0):
                    teardown_failed = True
            except OSError:
                running = True
                teardown_failed = True
            if running:
                try:
                    process.kill()
                except OSError:
                    teardown_failed = True
            try:
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                teardown_failed = True
        try:
            temporary_workspace.cleanup()
        except OSError:
            teardown_failed = True
    if process is None:
        raise failure or ProbeFailure("spawn_failed", "spawn")
    receipt = reducer.receipt(source="launched_app_server", executable=executable)
    receipt["parent_prompt_digest"] = digest_text(prompt)
    if failure is not None or teardown_failed:
        receipt["failure"] = failure.code if failure is not None else "teardown_failed"
        receipt["failure_stage"] = failure.stage if failure is not None else "teardown"
        if teardown_failed and failure is not None:
            receipt["teardown_failure"] = "teardown_failed"
        receipt["capabilities"]["usable_for_dispatch_provenance"] = False
        receipt["capabilities"]["classification"] = "partial_capability"
    return receipt


def _toml_string(value: str) -> str:
    if not value or len(value.encode("utf-8")) > 1024 or any(ord(ch) < 32 for ch in value):
        raise ProbeFailure("invalid_toml_value", "inventory")
    return json.dumps(value, ensure_ascii=True)


def _hook_probe_overrides(command: str, role_path: Path) -> list[str]:
    handler = ("{type=\"command\",command=" + _toml_string(command) +
               ",commandWindows=" + _toml_string(command) + ",timeout=5}")
    group = "[{matcher=" + _toml_string(HOOK_PROBE_MATCHER) + ",hooks=[" + handler + "]}]"
    role = ("{description=\"Capability probe worker\",config_file=" +
            _toml_string(str(role_path)) + "}")
    return ["-c", "hooks.PreToolUse=" + group,
            "-c", "hooks.PostToolUse=" + group,
            "-c", "agents.default=" + role]


def _inventory_request(cli: Path, overrides: list[str], workspace: str,
                       timeout: float) -> dict:
    command = [str(cli), "--disable", "plugins", "--enable", "hooks",
               *overrides, "app-server", "--stdio"]
    try:
        process = subprocess.Popen(command, cwd=workspace, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError:
        raise ProbeFailure("inventory_spawn_failed", "inventory") from None
    messages: queue.Queue = queue.Queue(maxsize=32)
    threading.Thread(target=_reader, args=(process.stdout, messages), daemon=True).start()
    deadline = time.monotonic() + timeout
    try:
        _send(process, {"id": 1, "method": "initialize",
                        "params": {"clientInfo": {"name": "router-hook-inventory", "version": "1"},
                                   "capabilities": {"experimentalApi": True}}})
        initialized = False
        for _ in range(HOOK_INVENTORY_LIMIT):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProbeFailure("inventory_timeout", "inventory")
            try:
                raw = messages.get(timeout=remaining)
            except queue.Empty:
                raise ProbeFailure("inventory_timeout", "inventory") from None
            if not raw:
                raise ProbeFailure("inventory_protocol_read_failed", "inventory")
            event = decode_frame(raw)
            if event.get("id") == 1 and not initialized:
                if "error" in event or not isinstance(event.get("result"), dict):
                    raise ProbeFailure("inventory_initialize_failed", "inventory")
                initialized = True
                _send(process, {"method": "initialized", "params": {}})
                _send(process, {"id": 2, "method": "hooks/list",
                                "params": {"cwds": [workspace]}})
            elif event.get("id") == 2 and initialized:
                if "error" in event or not isinstance(event.get("result"), dict):
                    raise ProbeFailure("inventory_list_failed", "inventory")
                return event["result"]
        raise ProbeFailure("inventory_event_limit", "inventory")
    finally:
        try:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            raise ProbeFailure("inventory_teardown_failed", "inventory") from None


def _inventory_handlers(result: dict, workspace: str, command: str,
                        *, trusted: bool) -> list[dict]:
    data = result.get("data")
    if not isinstance(data, list) or len(data) != 1:
        raise ProbeFailure("inventory_workspace_mismatch", "inventory")
    item = _mapping(data[0])
    cwd = item.get("cwd")
    if (not isinstance(cwd, str) or
            ntpath.normcase(ntpath.normpath(cwd)) != ntpath.normcase(ntpath.normpath(workspace))):
        raise ProbeFailure("inventory_workspace_mismatch", "inventory")
    if item.get("warnings") != [] or item.get("errors") != []:
        raise ProbeFailure("inventory_load_warning", "inventory")
    hooks = item.get("hooks")
    if not isinstance(hooks, list) or len(hooks) != 2:
        raise ProbeFailure("inventory_extra_or_missing_hooks", "inventory")
    handlers: list[dict] = []
    seen_events: set[str] = set()
    for raw in hooks:
        hook = _mapping(raw)
        event_name = hook.get("eventName")
        key = hook.get("key")
        current_hash = hook.get("currentHash")
        source_path = hook.get("sourcePath")
        key_digest = digest_text(key, limit=512)
        source_digest = digest_text(source_path, limit=1024)
        if (not isinstance(event_name, str) or event_name not in HOOK_EVENTS or
                event_name in seen_events or key_digest is None or
                any(ord(ch) < 32 or ord(ch) == 127 for ch in key) or
                not isinstance(current_hash, str) or
                not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", current_hash) or
                source_digest is None):
            raise ProbeFailure("inventory_invalid_handler", "inventory")
        if (hook.get("handlerType") != "command" or
                hook.get("matcher") != HOOK_PROBE_MATCHER or hook.get("command") != command or
                hook.get("source") != "sessionFlags" or hook.get("isManaged") is not False or
                hook.get("pluginId") is not None or hook.get("timeoutSec") != 5):
            raise ProbeFailure("inventory_unrelated_handler", "inventory")
        if trusted and (hook.get("enabled") is not True or
                        hook.get("trustStatus") != "trusted"):
            raise ProbeFailure("inventory_trust_failed", "inventory")
        if not isinstance(hook.get("trustStatus"), str) or hook.get("trustStatus") not in {
                "untrusted", "trusted", "modified"}:
            raise ProbeFailure("inventory_invalid_handler", "inventory")
        seen_events.add(event_name)
        handlers.append({"key": key, "current_hash": current_hash,
                         "source": "sessionFlags", "event": event_name,
                         "matcher": HOOK_PROBE_MATCHER,
                         "handler_type": "command", "is_managed": False,
                         "enabled": hook.get("enabled"),
                         "trust_status": hook.get("trustStatus"),
                         "source_path_sha256": source_digest["sha256"]})
    if seen_events != HOOK_EVENTS or len({h["key"] for h in handlers}) != 2:
        raise ProbeFailure("inventory_invalid_handler", "inventory")
    return sorted(handlers, key=lambda value: value["event"])


def _matcher_covers_spawn(matcher: object) -> str:
    if matcher in (None, "", "*", ".*", "^.*$"):
        return "yes"
    if matcher in ("^collaborationspawn_agent$", "collaborationspawn_agent"):
        return "yes"
    if isinstance(matcher, str) and re.fullmatch(r"\^?[A-Za-z0-9_]+\$?", matcher):
        literal = matcher.removeprefix("^").removesuffix("$")
        return "yes" if literal in "collaborationspawn_agent" else "no"
    return "unknown"


def _extra_hook_summary(raw: object) -> dict:
    hook = _mapping(raw)
    key = hook.get("key")
    current_hash = hook.get("currentHash")
    command = hook.get("command")
    source_path = hook.get("sourcePath")
    matcher = hook.get("matcher")
    if (not isinstance(key, str) or not key or
            digest_text(key, limit=512) is None or
            any(ord(ch) < 32 or ord(ch) == 127 for ch in key) or
            not isinstance(current_hash, str) or
            not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", current_hash) or
            digest_text(source_path, limit=1024) is None or
            (matcher is not None and digest_text(matcher, limit=256) is None) or
            (command is not None and digest_text(command, limit=4096) is None)):
        raise ProbeFailure("inventory_invalid_extra_hook", "inventory")
    event_name = _enum(hook.get("eventName"), ALL_HOOK_EVENTS) or "other"
    source = _enum(hook.get("source"), {"system", "user", "project", "mdm",
        "sessionFlags", "plugin", "cloudRequirements", "cloudManagedConfig",
        "legacyManagedConfigFile", "legacyManagedConfigMdm", "unknown"}) or "other"
    handler_type = _enum(hook.get("handlerType"), {"command", "mcpTool", "prompt", "agent"}) or "other"
    trust_status = _enum(hook.get("trustStatus"), {"managed", "untrusted",
                                                     "trusted", "modified"}) or "other"
    tool_effect = {
        "preToolUse": "may_rewrite_or_deny",
        "permissionRequest": "may_gate_permission",
        "postToolUse": "may_observe_or_stop",
    }.get(event_name, "lifecycle")
    matcher_covers_spawn = (_matcher_covers_spawn(matcher) if tool_effect != "lifecycle"
                            else "not_applicable")
    return {"key": key, "current_hash": current_hash, "source": source,
            "source_path_sha256": digest_text(source_path, limit=1024)["sha256"],
            "event": event_name, "matcher": matcher,
            "matcher_covers_spawn": matcher_covers_spawn,
            "handler_type": handler_type, "command_sha256": (
                digest_text(command, limit=4096)["sha256"] if command is not None else None),
            "is_managed": hook.get("isManaged") is True,
            "enabled": hook.get("enabled") is True,
            "trust_status": trust_status, "tool_effect": tool_effect}


def _partition_inventory(result: dict, workspace: str, command: str,
                         *, trusted: bool) -> tuple[list[dict], list[dict]]:
    data = result.get("data")
    if not isinstance(data, list) or len(data) != 1:
        raise ProbeFailure("inventory_workspace_mismatch", "inventory")
    entry = _mapping(data[0])
    hooks = entry.get("hooks")
    if not isinstance(hooks, list) or len(hooks) > 64:
        raise ProbeFailure("inventory_hook_limit", "inventory")
    intended = []
    extra = []
    for raw in hooks:
        hook = _mapping(raw)
        if (hook.get("source") == "sessionFlags" and
                isinstance(hook.get("eventName"), str) and
                hook.get("eventName") in HOOK_EVENTS and
                hook.get("matcher") == HOOK_PROBE_MATCHER and
                hook.get("handlerType") == "command" and
                hook.get("command") == command and
                hook.get("isManaged") is False and hook.get("pluginId") is None):
            intended.append(raw)
        else:
            extra.append(_extra_hook_summary(raw))
    filtered = {"data": [{**entry, "hooks": intended}]}
    handlers = _inventory_handlers(filtered, workspace, command, trusted=trusted)
    return handlers, sorted(extra, key=lambda value: (value["source"], value["event"], value["key"]))


def _fixed_defender_inventory_matches(extras: list[dict]) -> bool:
    if len(extras) != len(DEFENDER_HOOK_PINS):
        return False
    by_event = {entry.get("event"): entry for entry in extras}
    if len(by_event) != len(DEFENDER_HOOK_PINS):
        return False
    for event, (suffix, digest, matcher) in DEFENDER_HOOK_PINS.items():
        entry = by_event.get(event)
        expected = {
            "key": f"{DEFENDER_HOOK_ROOT}:{suffix}:0:0",
            "current_hash": "sha256:" + digest,
            "source": "system", "source_path_sha256": DEFENDER_SOURCE_PATH_SHA256,
            "event": event, "matcher": matcher, "handler_type": "command",
            "command_sha256": DEFENDER_COMMAND_SHA256, "is_managed": True,
            "enabled": True, "trust_status": "managed",
        }
        if entry is None or any(entry.get(key) != value for key, value in expected.items()):
            return False
    return True


def _trusted_state_override(handlers: list[dict]) -> list[str]:
    state_value = "{" + ",".join(
        _toml_string(handler["key"]) + "={enabled=true,trusted_hash=" +
        _toml_string(handler["current_hash"]) + "}"
        for handler in handlers) + "}"
    return ["-c", "hooks.state=" + state_value]


def _windows_cmd_exe() -> Path:
    system_root = os.environ.get("SystemRoot")
    if not system_root:
        raise ProbeFailure("inventory_invalid_cmd", "inventory")
    root = Path(system_root)
    if not root.is_absolute():
        raise ProbeFailure("inventory_invalid_cmd", "inventory")
    cmd_exe = (root / "System32" / "cmd.exe").resolve()
    comspec = os.environ.get("ComSpec")
    if (cmd_exe.name.lower() != "cmd.exe" or not cmd_exe.is_file() or
            (comspec and Path(comspec).resolve() != cmd_exe)):
        raise ProbeFailure("inventory_invalid_cmd", "inventory")
    return cmd_exe


def _audit_hook_command(cmd_exe: Path, python_exe: Path, audit_script: Path) -> str:
    """Use an explicit Windows shell so PowerShell cannot parse bare quotes."""
    paths = (cmd_exe, python_exe, audit_script)
    if any(not path.is_absolute() or not path.is_file() for path in paths):
        raise ProbeFailure("inventory_invalid_hook_command", "inventory")
    normalized = [str(path.resolve()) for path in paths]
    unsafe = re.compile(r'[\s"%!&|<>^();$`\'\x00]')
    if any(len(value) > 1024 or not value.isascii() or unsafe.search(value) or
           not re.fullmatch(r"[A-Za-z]:", ntpath.splitdrive(value)[0])
           for value in normalized):
        raise ProbeFailure("inventory_invalid_hook_command", "inventory")
    shell, python, script = normalized
    if Path(shell).name.lower() != "cmd.exe" or Path(python).name.lower() != "python.exe" or \
            Path(script).name != "dispatch_audit.py":
        raise ProbeFailure("inventory_invalid_hook_command", "inventory")
    # All three paths are absolute and exclude whitespace/metacharacters.
    # Avoid nested cmd quote stripping and PowerShell's bare-quoted-expression error.
    return f'{shell} /d /c {python} {script}'


def run_hook_inventory(cli: Path, expected_cli_hash: str, audit_script: Path,
                       expected_audit_hash: str, role_path: Path,
                       expected_role_hash: str, timeout: float) -> dict:
    if os.name != "nt" or not cli.is_absolute() or not cli.is_file() or cli.suffix.lower() != ".exe":
        raise ProbeFailure("invalid_windows_executable", "inventory")
    for path, expected, code in ((cli, expected_cli_hash, "cli_hash_mismatch"),
                                 (audit_script, expected_audit_hash, "audit_hash_mismatch"),
                                 (role_path, expected_role_hash, "role_hash_mismatch")):
        if (not path.is_absolute() or not path.is_file() or
                not isinstance(expected, str) or
                not re.fullmatch(r"[0-9a-fA-F]{64}", expected) or
                _file_sha256(path) != expected.lower()):
            raise ProbeFailure(code, "inventory")
    if audit_script.name != "dispatch_audit.py" or role_path.name != "astra-hard-kernel-role.toml":
        raise ProbeFailure("inventory_wrong_assets", "inventory")
    if role_path.stat().st_size > 512:
        raise ProbeFailure("inventory_wrong_role", "inventory")
    if role_path.read_text(encoding="utf-8").splitlines() != [
            'model = "gpt-6-astra"', 'model_reasoning_effort = "xhigh"']:
        raise ProbeFailure("inventory_wrong_role", "inventory")
    python_exe = Path(sys.executable).resolve()
    cmd_exe = _windows_cmd_exe()
    audited_command = _audit_hook_command(cmd_exe, python_exe, audit_script)
    try:
        version = subprocess.run([str(cli), "--version"], capture_output=True,
                                 timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ProbeFailure("inventory_version_failed", "inventory") from None
    if version.returncode or version.stdout.strip() != b"codex-cli 0.144.1":
        raise ProbeFailure("inventory_wrong_cli_version", "inventory")
    overrides = _hook_probe_overrides(audited_command, role_path)
    with tempfile.TemporaryDirectory(prefix="codex-hook-inventory-") as workspace:
        first, extras_first = _partition_inventory(
            _inventory_request(cli, overrides, workspace, timeout),
            workspace, audited_command, trusted=False)
        trusted_overrides = [*overrides, *_trusted_state_override(first)]
        second, extras_second = _partition_inventory(
            _inventory_request(cli, trusted_overrides, workspace, timeout),
            workspace, audited_command, trusted=True)
        if [(h["key"], h["current_hash"], h["source_path_sha256"]) for h in first] != [
                (h["key"], h["current_hash"], h["source_path_sha256"]) for h in second]:
            raise ProbeFailure("inventory_identity_changed", "inventory")
        if extras_first != extras_second:
            raise ProbeFailure("inventory_extra_identity_changed", "inventory")
    extra_tool_hooks = [h for h in extras_second if h["event"] in {
        "preToolUse", "postToolUse", "permissionRequest"} and
        h["matcher_covers_spawn"] != "no"]
    managed_lifecycle = any(h["is_managed"] and h["tool_effect"] == "lifecycle"
                            for h in extras_second)
    ready = not extra_tool_hooks and not managed_lifecycle
    fixed_managed_inventory = _fixed_defender_inventory_matches(extras_second)
    return {"schema_version": 1, "mode": "hook_inventory", "source": "launched_app_server",
            "cli_sha256": expected_cli_hash.lower(),
            "cli_version": "codex-cli 0.144.1",
            "audit_script_sha256": expected_audit_hash.lower(),
            "role_sha256": expected_role_hash.lower(),
            "cmd_sha256": _file_sha256(cmd_exe),
            "python_sha256": _file_sha256(python_exe),
            "command_sha256": hashlib.sha256(audited_command.encode("utf-8")).hexdigest(),
            "handlers": [{**final, "initial_trust_status": initial["trust_status"]}
                         for initial, final in zip(first, second)],
            "extra_handlers": extras_second,
            "gates": {"exact_handlers": True, "process_local_trust": True,
                      "no_extra_tool_hooks": not extra_tool_hooks,
                      "no_managed_lifecycle_hooks": not managed_lifecycle,
                      "ready_for_hook_live": ready,
                      "fixed_managed_inventory_pinned": fixed_managed_inventory,
                      "ready_for_fixed_sentinel_probe": fixed_managed_inventory}}


def _validated_canary_interpreter(expected_hash: str | None) -> Path:
    executable = Path(sys.executable).resolve()
    bundled = BUNDLED_PYTHON.resolve()
    if expected_hash is None:
        if executable != bundled:
            raise ProbeFailure("canary_interpreter_not_bundled", "preflight")
        expected_hash = BUNDLED_PYTHON_SHA256
    elif not isinstance(expected_hash, str) or not re.fullmatch(
            r"[0-9a-fA-F]{64}", expected_hash):
        raise ProbeFailure("invalid_canary_python_hash", "preflight")
    if not executable.is_file() or _file_sha256(executable) != expected_hash.lower():
        raise ProbeFailure("canary_python_hash_mismatch", "preflight")
    try:
        import cryptography
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        if cryptography.__version__ != "50.0.1" or not callable(rsa.generate_private_key) or \
                not callable(AESGCM.generate_key):
            raise ImportError("unsupported cryptography")
    except (ImportError, AttributeError):
        raise ProbeFailure("canary_crypto_unavailable", "preflight") from None
    return executable


def _canary_environment(base: dict[str, str], public_der: bytes,
                        combined_run_id: str) -> dict[str, str]:
    env = {key: value for key, value in base.items()
           if not key.startswith("CODEX_MODEL_ROUTER_PACKET_CANARY")}
    env["CODEX_MODEL_ROUTER_DISPATCH_AUDIT"] = "1"
    env["CODEX_MODEL_ROUTER_PACKET_CANARY"] = "1"
    env["CODEX_MODEL_ROUTER_PACKET_CANARY_RUN_ID"] = combined_run_id
    env["CODEX_MODEL_ROUTER_PACKET_CANARY_PUBLIC_KEY_B64"] = base64.b64encode(
        public_der).decode("ascii")
    return env


def _new_canary_run_id() -> str:
    """Bind independent 120-bit run and 128-bit launch IDs within the hook's 64-char bound."""
    run_id = secrets.token_hex(15)
    launch_id = secrets.token_hex(16)
    return run_id + "-" + launch_id


def run_fixed_sentinel_probe(cli: Path, expected_cli_hash: str, audit_script: Path,
                             expected_audit_hash: str, role_path: Path,
                             expected_role_hash: str, timeout: float,
                             expected_python_hash: str | None = None) -> dict:
    python_exe = _validated_canary_interpreter(expected_python_hash)
    inventory = run_hook_inventory(cli, expected_cli_hash, audit_script,
                                   expected_audit_hash, role_path, expected_role_hash,
                                   min(timeout, 20))
    if not inventory["gates"]["ready_for_fixed_sentinel_probe"]:
        return {"schema_version": 1, "mode": "fixed_sentinel_probe",
                "source": "not_launched", "inventory_gates": inventory["gates"],
                "capabilities": {"hook_context": "UNKNOWN",
                                 "effective_dispatch_proof": "UNKNOWN",
                                 "usable_for_dispatch_provenance": False},
                "failure": "fixed_inventory_gate_closed", "failure_stage": "preflight"}
    source_digest = digest_text(SESSION_FLAGS_SOURCE_PATH, limit=1024)
    if any(handler["source_path_sha256"] != source_digest["sha256"]
           for handler in inventory["handlers"]):
        raise ProbeFailure("fixed_session_source_mismatch", "preflight")
    for path, expected in ((cli, expected_cli_hash), (audit_script, expected_audit_hash),
                           (role_path, expected_role_hash)):
        if _file_sha256(path) != expected.lower():
            raise ProbeFailure("fixed_asset_drift", "preflight")
    cmd_exe = _windows_cmd_exe()
    if _file_sha256(python_exe) != inventory["python_sha256"]:
        raise ProbeFailure("fixed_python_drift", "preflight")
    if _file_sha256(cmd_exe) != inventory["cmd_sha256"]:
        raise ProbeFailure("fixed_cmd_drift", "preflight")
    command = _audit_hook_command(cmd_exe, python_exe, audit_script)
    overrides = [*_hook_probe_overrides(command, role_path),
                 *_trusted_state_override(inventory["handlers"])]
    sentinel = "Capability probe " + secrets.token_hex(16) + ". Reply OK without tools."
    prompt = ("Use the native spawn_agent tool exactly once with task_name " +
              FIXED_SPAWN_NAME + ", agent_type default, fork_turns none, and "
              "message exactly " + json.dumps(sentinel) +
              ". Do not set model or reasoning_effort. Wait for the child, "
              "then reply OK. Do not write files.")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    public_der = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo)
    combined_run_id = _new_canary_run_id()
    reducer = FixedSentinelReducer(sentinel, SESSION_FLAGS_SOURCE_PATH,
                                   canary_private_key=private_key,
                                   canary_run_id=combined_run_id)

    def check_live_inventory(result: dict) -> None:
        handlers, extras = _partition_inventory(result, workspace, command, trusted=True)
        expected_handlers = [{key: value for key, value in item.items()
                              if key != "initial_trust_status"}
                             for item in inventory["handlers"]]
        if (handlers != expected_handlers or extras != inventory["extra_handlers"] or
                not _fixed_defender_inventory_matches(extras)):
            raise ProbeFailure("fixed_inventory_drift", "hook_live_preflight")

    temporary_workspace = tempfile.TemporaryDirectory(prefix="codex-fixed-sentinel-")
    workspace = temporary_workspace.name
    process: subprocess.Popen | None = None
    failure: ProbeFailure | None = None
    teardown_failed = False
    try:
        env = _canary_environment(os.environ, public_der, combined_run_id)
        try:
            process = subprocess.Popen(
                [str(cli), "--disable", "plugins", "--enable", "hooks", *overrides,
                 "app-server", "--stdio"], cwd=workspace, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError:
            failure = ProbeFailure("fixed_spawn_failed", "spawn")
        if process is not None:
            try:
                drive_protocol(process, reducer, prompt, "gpt-6-sol", "low", timeout,
                               workspace, pre_thread_check=check_live_inventory)
            except ProbeFailure as error:
                failure = error
            except (OSError, subprocess.TimeoutExpired):
                failure = ProbeFailure("fixed_protocol_io_failed", "protocol")
    finally:
        if process is not None:
            try:
                observed_exit = process.poll()
                running = observed_exit is None
                if observed_exit not in (None, 0):
                    teardown_failed = True
            except OSError:
                running = True
                teardown_failed = True
            if running:
                try:
                    process.kill()
                except OSError:
                    teardown_failed = True
            try:
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                teardown_failed = True
        try:
            temporary_workspace.cleanup()
        except OSError:
            teardown_failed = True
    if process is None:
        raise failure or ProbeFailure("fixed_spawn_failed", "spawn")
    receipt = reducer.receipt(source="launched_app_server",
                              executable={"sha256": expected_cli_hash.lower(),
                                          "version": "codex-cli 0.144.1"})
    receipt["parent_prompt_digest"] = digest_text(prompt)
    receipt["inventory_gates"] = inventory["gates"]
    receipt["inventory_identity_sha256"] = hashlib.sha256(json.dumps(
        {"handlers": inventory["handlers"], "extra_handlers": inventory["extra_handlers"]},
        sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    if failure is not None or teardown_failed:
        receipt["failure"] = failure.code if failure is not None else "teardown_failed"
        receipt["failure_stage"] = failure.stage if failure is not None else "teardown"
        if teardown_failed and failure is not None:
            receipt["teardown_failure"] = "teardown_failed"
        receipt["capabilities"]["hook_context"] = "UNKNOWN"
        receipt["capabilities"]["encrypted_pre_packet"] = "UNKNOWN"
    return receipt


def dry_run() -> dict:
    sentinel = "capability sentinel"
    fake_hook_path = "C:\\installed-plugin\\hooks\\hooks.json"
    reducer = Reducer(sentinel, requested_parent_model=DEFAULT_PARENT_MODEL,
                      requested_parent_effort="low", expected_hook_source_path=fake_hook_path)
    events = [
        {"jsonrpc": "2.0", "id": 1, "result": {"userAgent": "synthetic"}},
        {"jsonrpc": "2.0", "id": 2, "result": {"thread": {"id": "parent-1",
            "model": DEFAULT_PARENT_MODEL, "reasoningEffort": "low"},
            "model": DEFAULT_PARENT_MODEL}},
        {"jsonrpc": "2.0", "id": 4, "result": {"data": [
            {"name": "multi_agent", "enabled": True}]}},
        {"jsonrpc": "2.0", "id": 3, "result": {"turn": {"id": "turn-1"}}},
        {"jsonrpc": "2.0", "method": "item/completed", "params": {
            "threadId": "parent-1", "turnId": "turn-1", "item": {
                "type": "collabAgentToolCall", "tool": "spawnAgent", "id": "call-1",
                "senderThreadId": "parent-1", "receiverThreadIds": ["child-1"],
                "prompt": sentinel, "model": "gpt-6-luna", "reasoningEffort": "low",
                "status": "completed"}}},
        {"jsonrpc": "2.0", "method": "hook/started", "params": {"threadId": "parent-1",
            "turnId": "turn-1", "run": {"id": "hook-1", "eventName": "preToolUse",
            "status": "running", "source": "plugin", "sourcePath": fake_hook_path,
            "handlerType": "command", "scope": "turn"}}},
        {"jsonrpc": "2.0", "method": "hook/completed", "params": {"threadId": "parent-1",
            "turnId": "turn-1", "run": {"id": "hook-1", "eventName": "preToolUse",
            "status": "completed", "source": "plugin", "sourcePath": fake_hook_path,
            "handlerType": "command", "scope": "turn"}}},
        {"jsonrpc": "2.0", "method": "hook/started", "params": {"threadId": "parent-1",
            "turnId": "turn-1", "run": {"id": "hook-2", "eventName": "postToolUse",
            "status": "running", "source": "plugin", "sourcePath": fake_hook_path,
            "handlerType": "command", "scope": "turn"}}},
        {"jsonrpc": "2.0", "method": "hook/completed", "params": {"threadId": "parent-1",
            "turnId": "turn-1", "run": {"id": "hook-2", "eventName": "postToolUse",
            "status": "completed", "source": "plugin", "sourcePath": fake_hook_path,
            "handlerType": "command", "scope": "turn"}}},
        {"jsonrpc": "2.0", "method": "turn/completed", "params": {
            "threadId": "parent-1", "turn": {"id": "turn-1", "status": "completed"}}},
    ]
    for event in events:
        method = event.get("method")
        if method == "item/completed":
            event["params"]["completedAtMs"] = 2
            event["params"]["item"].setdefault("agentsStates", {})
        if method in {"hook/started", "hook/completed"}:
            run = event["params"]["run"]
            run.update({"executionMode": "sync", "displayOrder": 0,
                        "startedAt": 1, "completedAt": 2 if method == "hook/completed" else None,
                        "durationMs": 1 if method == "hook/completed" else None,
                        "statusMessage": None, "entries": []})
        reducer.feed((json.dumps(event) + "\n").encode("utf-8"))
    return reducer.receipt(source="synthetic")


def live_prompt() -> tuple[str, str]:
    """Create a harmless one-child turn; neither string enters the receipt."""
    sentinel = "Capability probe " + secrets.token_hex(16) + ". Reply OK without tools."
    prompt = ("Use spawn_agent exactly once with task_name capability_probe, "
              "model gpt-6-luna, reasoning_effort low, fork_turns none, and "
              "message exactly " + json.dumps(sentinel) +
              ". Wait for that child, then reply OK. Do not write files.")
    return prompt, sentinel


def main(argv: list[str] | None = None) -> int:
    parser = SafeArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Launch one paid App Server turn")
    parser.add_argument("--hook-inventory", action="store_true",
                        help="Inspect and verify process-local audit hook trust without a model turn")
    parser.add_argument("--fixed-sentinel-live", action="store_true",
                        help="Run one gated Sol/low to role-selected Astra/xhigh hook probe")
    parser.add_argument("--codex-cli", type=Path, help="Exact installed codex.exe path")
    parser.add_argument("--expected-cli-sha256", help="Optional pinned executable SHA-256")
    parser.add_argument("--audit-script", type=Path,
                        help="Absolute dispatch_audit.py path for the isolated hook probe")
    parser.add_argument("--expected-audit-sha256", help="Pinned audit script SHA-256")
    parser.add_argument("--role-config", type=Path,
                        help="Absolute frozen Astra/xhigh role file")
    parser.add_argument("--expected-role-sha256", help="Pinned role file SHA-256")
    parser.add_argument("--expected-python-sha256",
                        help="Explicit SHA-256 pin for a non-bundled canary interpreter")
    parser.add_argument("--router-hook-config", type=Path,
                        help="Absolute installed router hooks.json path")
    parser.add_argument("--router-audit-script", type=Path,
                        help="Absolute installed dispatch_audit.py path")
    parser.add_argument("--hook-file", type=Path, action="append", default=[],
                        help="Operator-selected hook file to hash; repeat as needed")
    parser.add_argument("--model", default=DEFAULT_PARENT_MODEL)
    parser.add_argument("--effort", default="low", choices=sorted(EFFORTS))
    parser.add_argument("--timeout", type=float, default=90.0)
    arguments = sys.argv[1:] if argv is None else argv
    live_requested = "--live" in arguments
    inventory_requested = "--hook-inventory" in arguments
    fixed_requested = "--fixed-sentinel-live" in arguments
    try:
        args = parser.parse_args(arguments)
        if sum((args.hook_inventory, args.fixed_sentinel_live, args.live)) > 1:
            raise ProbeFailure("conflicting_modes", "preflight")
        if args.hook_inventory or args.fixed_sentinel_live:
            if not all((args.codex_cli, args.expected_cli_sha256,
                                     args.audit_script, args.expected_audit_sha256,
                                     args.role_config, args.expected_role_sha256)):
                raise ProbeFailure("missing_inventory_argument", "inventory")
            if not math.isfinite(args.timeout) or args.timeout <= 0 or args.timeout > (
                    120 if args.fixed_sentinel_live else 60):
                raise ProbeFailure("invalid_inventory_timeout", "inventory")
            if args.fixed_sentinel_live:
                result = run_fixed_sentinel_probe(
                    args.codex_cli, args.expected_cli_sha256,
                    args.audit_script, args.expected_audit_sha256,
                    args.role_config, args.expected_role_sha256, args.timeout,
                    args.expected_python_sha256)
            else:
                result = run_hook_inventory(args.codex_cli, args.expected_cli_sha256,
                                            args.audit_script, args.expected_audit_sha256,
                                            args.role_config, args.expected_role_sha256,
                                            args.timeout)
        elif args.live:
            if not args.codex_cli or not args.router_hook_config or not args.router_audit_script:
                raise ProbeFailure("missing_live_argument")
            if (not selector(args.model) or not math.isfinite(args.timeout) or
                    args.timeout <= 0 or args.timeout > 600 or len(args.hook_file) > 16):
                raise ProbeFailure("invalid_live_argument")
            prompt, sentinel = live_prompt()
            result = run_live(args.codex_cli, prompt, sentinel, args.model, args.effort,
                              args.timeout, args.expected_cli_sha256, args.router_hook_config,
                              args.router_audit_script, args.hook_file)
        else:
            result = dry_run()
        print(json.dumps(result, separators=(",", ":"), sort_keys=True))
        if args.hook_inventory:
            return 0 if (result["gates"]["ready_for_hook_live"] or
                         result["gates"]["ready_for_fixed_sentinel_probe"]) else 2
        if args.fixed_sentinel_live:
            return 0 if (result.get("turn_terminal") == "completed" and
                         result["capabilities"].get("encrypted_pre_packet") == "OBSERVED" and
                         "failure" not in result) else 2
        return 0 if result["capabilities"]["usable_for_dispatch_provenance"] or not args.live else 2
    except (ProbeFailure, OSError, subprocess.TimeoutExpired) as error:
        if isinstance(error, ProbeFailure):
            reason, stage = error.code, error.stage or "preflight"
        else:
            reason, stage = "preflight_io_failed", "preflight"
        source = "hook_inventory_attempt" if inventory_requested else (
            "fixed_sentinel_probe_attempt" if fixed_requested else (
                "live_probe_attempt" if live_requested else "synthetic"))
        print(json.dumps({"schema_version": 1, "source": source,
                          "capabilities": {"usable_for_dispatch_provenance": False},
                          "failure": reason, "failure_stage": stage}, sort_keys=True))
        return 2
    except Exception:
        # A malformed protocol field must never surface a traceback or event text.
        source = "hook_inventory_attempt" if inventory_requested else (
            "fixed_sentinel_probe_attempt" if fixed_requested else (
                "live_probe_attempt" if live_requested else "synthetic"))
        print(json.dumps({"schema_version": 1, "source": source,
                          "capabilities": {"usable_for_dispatch_provenance": False},
                          "failure": "probe_internal_error", "failure_stage": "internal"},
                         sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
