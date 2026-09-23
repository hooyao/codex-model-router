#!/usr/bin/env python3
"""Collect, reprocess, and validate role-bounded Codex live evidence.

Version 3 never searches aggregate transcript text. It preserves packet,
native transport, selector, runtime, and final-echo identity as distinct
dimensions, and reports assignment overlap separately from session lifetime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

try:
    from . import contract, fixture
except ImportError:
    import contract
    import fixture


CASES = ("investigation-reuse", "serial-escalation", "parallel-disjoint", "architecture-review")
IGNORED_WORKSPACE_NAMES = {".git", ".codex-model-router"}
PLACEHOLDER = re.compile(r"(?:^|[-_])(unknown|unavailable|unexposed|unresolved|placeholder)(?:$|[-_])", re.I)
SHA256 = re.compile(r"\b[0-9a-f]{64}\b")
ACTIVATION_SPEC = "raw/activation-spec.json"
BUSINESS_ITEM_TYPES = {"command_execution", "file_change", "mcp_tool_call", "web_search", "image_generation"}
REPO_ROOT = Path(__file__).resolve().parents[2]
IANA_TIMEZONES = frozenset(
    line for line in (REPO_ROOT / "evals" / "data" / "iana-timezones.txt").read_text(encoding="utf-8").splitlines()
    if line and not line.startswith("#")
)
sys.path.insert(0, str(REPO_ROOT / "plugins" / "codex-model-router" / "hooks"))
from routing_config import validate_config, serialized_config  # noqa: E402
from router_hook import CONTROLLER_CONTRACT  # noqa: E402


def frozen_case(case_id: str) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    """Only repository-pinned case/fixture data can define an oracle."""
    _manifest, cases = contract.load_manifest(REPO_ROOT / "evals" / "benchmark.json")
    matches = [case for case in cases if case["id"] == case_id]
    require(len(matches) == 1 and case_id in CASES, "unknown frozen live case")
    case = matches[0]
    path = fixture.fixture_for_case(REPO_ROOT / "evals", case)
    value = fixture.load_fixture(path)
    return case, contract.safe_path(path.parent, value["reference"]["path"]), value["reference"]["tree"]


def canonical_path(root: Path, value: str, expected: str) -> Path:
    require(value == expected, f"reference must use canonical scenario path: {expected}")
    return contract.safe_path(root, expected)


class LiveEvidenceError(ValueError):
    """Observed evidence is missing, contaminated, or internally inconsistent."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise LiveEvidenceError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_json(path: Path) -> Any:
    """Read JSON while rejecting duplicate keys."""
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in items:
            require(key not in value, f"duplicate JSON key in {path}: {key}")
            value[key] = item
        return value
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)
    except json.JSONDecodeError as error:
        raise LiveEvidenceError(f"invalid JSON in {path}: {error}") from error


def referenced_path(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def evidence_ref(root: Path, path: Path) -> dict[str, str]:
    resolved = path.resolve()
    try:
        name = resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        name = str(resolved)
    return {"path": name, "sha256": sha256(resolved)}


def tree_sha256(files: dict[str, Any]) -> str:
    payload = json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def json_lines(path: Path) -> list[tuple[int, dict[str, Any]]]:
    values = []
    for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            values.append((number, value))
    return values


def snapshot(root: Path) -> dict[str, str]:
    require(root.is_dir(), f"missing business tree: {root}")
    contract.check_node(root)
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in IGNORED_WORKSPACE_NAMES for part in relative.parts):
            continue
        contract.check_node(path)
        if path.is_file():
            result[relative.as_posix()] = sha256(path)
    return result


def business_directories(root: Path) -> list[str]:
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*")
                  if path.is_dir() and not any(part in IGNORED_WORKSPACE_NAMES
                      for part in path.relative_to(root).parts))


def copy_business_tree(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if any(part in IGNORED_WORKSPACE_NAMES for part in relative.parts):
            continue
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def content_text(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    return "\n".join(item.get("text", "") for item in content
                     if isinstance(item, dict) and isinstance(item.get("text"), str))


def environment_metadata(text: str, cwd: str) -> bool:
    """Accept only the captured runtime metadata grammar, never free-form text."""
    if "<!" in text or "<?" in text:
        return False
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return False

    def container(node: ET.Element, tag: str, attributes: dict[str, str]) -> bool:
        return node.tag == tag and node.attrib == attributes and not (node.text or "").strip() and \
            all(not (child.tail or "").strip() for child in node)

    def leaf(node: ET.Element) -> Optional[str]:
        return node.text if not node.attrib and len(node) == 0 else None

    if not container(root, "environment_context", {}):
        return False
    fields = {child.tag: child for child in root}
    if len(fields) != len(root) or not {"cwd", "shell"} <= set(fields) or \
            not set(fields) <= {"cwd", "shell", "current_date", "timezone", "filesystem"}:
        return False
    if leaf(fields["cwd"]) != cwd or leaf(fields["shell"]) not in {
            "powershell", "pwsh", "cmd", "bash", "zsh", "sh", "fish"}:
        return False
    if "current_date" in fields:
        date = leaf(fields["current_date"])
        try:
            if not date or datetime.strptime(date, "%Y-%m-%d").strftime("%Y-%m-%d") != date:
                return False
        except ValueError:
            return False
    if "timezone" in fields:
        zone = leaf(fields["timezone"])
        if zone not in IANA_TIMEZONES:
            return False
    if "filesystem" in fields:
        filesystem = fields["filesystem"]
        if not container(filesystem, "filesystem", {}) or [child.tag for child in filesystem] != [
                "workspace_roots", "permission_profile"]:
            return False
        roots, profile = filesystem
        if not container(roots, "workspace_roots", {}) or len(roots) != 1 or \
                roots[0].tag != "root" or leaf(roots[0]) != cwd:
            return False
        if not container(profile, "permission_profile", {"type": "disabled"}) or len(profile) != 1:
            return False
        permission = profile[0]
        if not container(permission, "file_system", {"type": "unrestricted"}) or len(permission):
            return False
    return True


def successful_terminal_check(compact: list[tuple[int, dict[str, Any]]],
                              parent: list[tuple[int, dict[str, Any]]]) -> dict[str, Any]:
    """Require one successful CLI turn and its matching completed parent task."""
    terminals = [(line, value) for line, value in compact if value.get("type") == "error" or
                 (isinstance(value.get("type"), str) and value["type"].startswith(("turn.", "thread.")) and
                  value["type"] not in ("turn.started", "thread.started"))]
    task_events = [(line, value["payload"]) for line, value in parent if
                   value.get("type") == "event_msg" and isinstance(value.get("payload"), dict) and
                   isinstance(value["payload"].get("type"), str) and
                   (value["payload"]["type"].startswith(("task_", "turn_")) or
                    value["payload"]["type"] == "error")]
    messages = [(line, value["item"].get("text")) for line, value in compact if
                value.get("type") == "item.completed" and isinstance(value.get("item"), dict) and
                value["item"].get("type") == "agent_message"]
    valid = len(terminals) == 1 and terminals[0][1].get("type") == "turn.completed" and \
        terminals[0][0] == compact[-1][0] and len(task_events) == 2 and \
        [payload["type"] for _line, payload in task_events] == ["task_started", "task_complete"]
    if valid:
        terminal = terminals[0][1]
        start, complete = task_events[0][1], task_events[1][1]
        final = complete.get("last_agent_message")
        valid = isinstance(start.get("turn_id"), str) and bool(start["turn_id"]) and \
            complete.get("turn_id") == start["turn_id"] and \
            terminal.get("turn_id", start["turn_id"]) == start["turn_id"] and \
            all(value.get("status", "completed") == "completed" and value.get("error") is None
                for value in (terminal, complete)) and \
            isinstance(final, str) and bool(final.strip()) and bool(messages) and \
            isinstance(messages[-1][1], str) and messages[-1][1].strip() == final.strip() and \
            not any(line > task_events[-1][0] and value.get("type") == "response_item"
                    for line, value in parent)
    return status("successful-terminal-outcome", "pass" if valid else "fail",
                  [f"transcript:{line}" for line, _value in terminals] +
                  [f"parent-session:{line}" for line, _value in task_events],
                  "one successful CLI terminal and matching parent task completion/final output")


def session_meta(path: Path) -> Optional[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            value = json.loads(stream.readline())
    except (OSError, json.JSONDecodeError):
        return None
    if value.get("type") != "session_meta" or not isinstance(value.get("payload"), dict):
        return None
    return value["payload"]


def session_catalog(sessions_root: Path, parent_ids: set[str]) -> dict[str, list[tuple[Path, dict[str, Any]]]]:
    found = {parent_id: [] for parent_id in parent_ids}
    for path in sessions_root.rglob("rollout-*.jsonl"):
        meta = session_meta(path)
        if not meta:
            continue
        if meta.get("id") in found:
            found[meta["id"]].append((path, meta))
        if meta.get("parent_thread_id") in found:
            found[meta["parent_thread_id"]].append((path, meta))
    return found


def extract_parent_id(transcript: Path) -> str:
    ids = [value.get("thread_id") for _line, value in json_lines(transcript)
           if value.get("type") == "thread.started"]
    require(len(ids) == 1 and isinstance(ids[0], str) and bool(ids[0]),
            f"expected exactly one thread.started event in {transcript}")
    return ids[0]


def turn_context(items: list[tuple[int, dict[str, Any]]]) -> dict[str, Any]:
    for _line, value in reversed(items):
        if value.get("type") == "turn_context" and isinstance(value.get("payload"), dict):
            return value["payload"]
    return {}


def assignment_spans(items: list[tuple[int, dict[str, Any]]]) -> list[dict[str, Any]]:
    starts: dict[str, tuple[int, str]] = {}
    spans = []
    for line, value in items:
        payload = value.get("payload")
        if value.get("type") != "event_msg" or not isinstance(payload, dict):
            continue
        turn_id = payload.get("turn_id")
        if payload.get("type") == "task_started" and isinstance(turn_id, str) and isinstance(value.get("timestamp"), str):
            starts[turn_id] = (line, value["timestamp"])
        elif payload.get("type") == "task_complete" and turn_id in starts and isinstance(value.get("timestamp"), str):
            start_line, start_at = starts.pop(turn_id)
            spans.append({"turn_id": turn_id, "start_at": start_at, "end_at": value["timestamp"],
                          "start_line": start_line, "end_line": line})
    return spans


def empty_identity() -> dict[str, Optional[str]]:
    return {"worker_name": None, "task_id": None, "native_task_name": None}


def identity_fields(text: str) -> dict[str, Optional[str]]:
    result = empty_identity()
    for field in ("Worker name", "Task ID", "Native task name"):
        match = re.search(rf"(?m)^{re.escape(field)}:\s*([^\r\n]+)", text)
        result[field.lower().replace(" ", "_")] = match.group(1).strip() if match else None
    return result


def final_echo(items: list[tuple[int, dict[str, Any]]]) -> tuple[dict[str, Optional[str]], Optional[int], str]:
    for line, value in reversed(items):
        payload = value.get("payload")
        if value.get("type") == "event_msg" and isinstance(payload, dict) and payload.get("type") == "task_complete":
            text = payload.get("last_agent_message") if isinstance(payload.get("last_agent_message"), str) else ""
            return identity_fields(text), line, text
    return empty_identity(), None, ""


def spawn_events(items: list[tuple[int, dict[str, Any]]]) -> list[dict[str, Any]]:
    result = []
    for line, value in items:
        payload = value.get("payload")
        if value.get("type") != "response_item" or not isinstance(payload, dict) or \
                payload.get("type") != "function_call" or payload.get("name") != "spawn_agent":
            continue
        try:
            arguments = json.loads(payload.get("arguments", "{}"))
        except json.JSONDecodeError:
            arguments = {}
        message = arguments.get("message")
        packet = identity_fields(message) if isinstance(message, str) and "Worker name:" in message else empty_identity()
        result.append({"line": line, "timestamp": value.get("timestamp"), "arguments": arguments,
                       "call_id": payload.get("call_id"),
                       "packet_identity": packet,
                       "packet_visibility": "plaintext" if packet["worker_name"] else "encrypted-or-unavailable"})
    return result


def result_events(items: list[tuple[int, dict[str, Any]]]) -> list[dict[str, Any]]:
    result = []
    for line, value in items:
        payload = value.get("payload")
        if value.get("type") == "response_item" and isinstance(payload, dict) and \
                payload.get("type") == "agent_message" and isinstance(payload.get("author"), str):
            text = content_text(payload.get("content"))
            result.append({"line": line, "timestamp": value.get("timestamp"), "author": payload["author"],
                           "identity": identity_fields(text), "text": text})
    return result


def first_parent_business_line(items: list[tuple[int, dict[str, Any]]]) -> Optional[int]:
    """Return the first controller tool action other than routing transport."""
    lines = parent_business_lines(items)
    return lines[0] if lines else None


def parent_business_lines(items: list[tuple[int, dict[str, Any]]]) -> list[int]:
    transport = {"spawn_agent", "wait_agent", "wait_threads", "send_message", "send_message_to_thread"}
    lines = []
    for line, value in items:
        payload = value.get("payload")
        if value.get("type") == "response_item" and isinstance(payload, dict) and \
                payload.get("type") == "function_call" and payload.get("name") not in transport:
            lines.append(line)
    return lines


def route_events_from_items(items: list[tuple[int, dict[str, Any]]]) -> list[dict[str, Any]]:
    result = []
    for line, value in items:
        payload = value.get("payload")
        if value.get("type") != "event_msg" or not isinstance(payload, dict) or payload.get("type") != "agent_message":
            continue
        message = payload.get("message")
        if not isinstance(message, str):
            continue
        # Quoted examples and later prose do not constitute a routing decision.
        if message.startswith("ROUTE:"):
            route = message.splitlines()[0].rstrip()
            result.append({"line": line, "timestamp": value.get("timestamp"), "text": route,
                           "role": "controller_agent_message"})
    return result


def transcript_observations(path: Path) -> dict[str, Any]:
    items = json_lines(path)
    routes, warnings = [], []
    for line, value in items:
        item = value.get("item")
        if value.get("type") == "item.completed" and isinstance(item, dict) and item.get("type") == "agent_message":
            text = item.get("text", "")
            if isinstance(text, str) and text.startswith("ROUTE:"):
                routes.append({"line": line, "text": text.splitlines()[0].rstrip(),
                               "role": "controller_agent_message"})
        message = value.get("message")
        if isinstance(message, str) and "hook" in message.lower():
            warnings.append({"line": line, "text": message})
    usage = next((value["usage"] for _line, value in reversed(items)
                  if value.get("type") == "turn.completed" and isinstance(value.get("usage"), dict)), None)
    failed = sum(value.get("type") == "item.completed" and isinstance(value.get("item"), dict)
                 and value["item"].get("status") == "failed" for _line, value in items)
    business_lines = []
    for line, value in items:
        item = value.get("item")
        if value.get("type") in ("item.started", "item.completed") and isinstance(item, dict) and \
                item.get("type") in BUSINESS_ITEM_TYPES:
            business_lines.append(line)
    return {"route_events": routes, "warnings": warnings, "usage": usage, "failed_tool_events": failed,
            "first_business_line": min(business_lines) if business_lines else None,
            "raw_log_bytes": path.stat().st_size, "sha256": sha256(path)}


def session_record(path: Path, meta: dict[str, Any]) -> dict[str, Any]:
    items = json_lines(path)
    require(sum(value.get("type") == "session_meta" for _line, value in items) == 1 and
            isinstance(meta.get("id"), str) and bool(meta["id"]) and
            meta.get("session_id", meta["id"]) == meta["id"], "missing or contradictory session metadata")
    context = turn_context(items)
    spans = assignment_spans(items)
    echo, echo_line, echo_text = final_echo(items)
    source = meta.get("source")
    agent_path = meta.get("agent_path")
    if isinstance(source, dict):
        ancestry = source.get("subagent", {}).get("thread_spawn", {})
        nested_path = ancestry.get("agent_path")
        require(not agent_path or not nested_path or agent_path == nested_path,
                "contradictory native path metadata")
        require("parent_thread_id" not in ancestry or ancestry["parent_thread_id"] == meta.get("parent_thread_id"),
                "contradictory parent metadata")
        agent_path = agent_path or nested_path
    return {
        "thread_id": meta.get("id"), "parent_thread_id": meta.get("parent_thread_id"),
        "agent_path": agent_path, "session_start_at": meta.get("timestamp"),
        "session_end_at": spans[-1]["end_at"] if spans else None, "assignment_spans": spans,
        "runtime": {"model": context.get("model"), "reasoning_effort": context.get("effort"),
                    "evidence_line": next((line for line, value in reversed(items) if value.get("type") == "turn_context"), None)},
        "final_echo": echo, "final_echo_line": echo_line, "final_text": echo_text,
        "source_path": str(path.resolve()), "source_sha256": sha256(path),
    }


def intervals(spans: list[dict[str, Any]]) -> list[tuple[datetime, datetime]]:
    return [(parse_iso(span["start_at"]), parse_iso(span["end_at"])) for span in spans]


def overlap_ms_from_spans(spans: list[dict[str, Any]]) -> int:
    require(bool(spans), "cannot calculate overlap without assignment spans")
    values = intervals(spans)
    return max(0, round((min(end for _start, end in values) - max(start for start, _end in values)).total_seconds() * 1000))


def overlap_ms(sessions: list[dict[str, Any]]) -> int:
    spans = []
    for session in sessions:
        spans.append(session["assignment_spans"][0] if session.get("assignment_spans") else
                     {"start_at": session.get("start_at"), "end_at": session.get("end_at")})
    return overlap_ms_from_spans(spans)


def ordered_without_overlap(sessions: list[dict[str, Any]]) -> bool:
    spans = [session["assignment_spans"][0] if session.get("assignment_spans") else
             {"start_at": session["start_at"], "end_at": session["end_at"]} for session in sessions]
    ordered = sorted(intervals(spans))
    return all(left[1] <= right[0] for left, right in zip(ordered, ordered[1:]))


def status(name: str, state: str, refs: list[str], detail: str) -> dict[str, Any]:
    require(state in ("pass", "fail", "unknown"), f"invalid check state: {state}")
    return {"name": name, "status": state, "evidence_refs": refs, "detail": detail}


def aggregate(checks: list[dict[str, Any]]) -> str:
    if not checks:
        return "unknown"
    states = [item["status"] for item in checks]
    return "fail" if "fail" in states else "unknown" if "unknown" in states else "pass"


def identity_checks(children: list[dict[str, Any]], spawns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_native = {event["arguments"].get("task_name") or event["arguments"].get("name"): event for event in spawns}
    output = []
    for child in children:
        native_key = (child.get("agent_path") or "").rsplit("/", 1)[-1]
        spawn = by_native.get(native_key)
        packet = spawn["packet_identity"] if spawn else empty_identity()
        args = spawn["arguments"] if spawn else {}
        echo = child["final_echo"]
        model = child["runtime"]["model"]
        effort = child["runtime"]["reasoning_effort"]
        explicit = (args.get("model"), args.get("reasoning_effort"))
        inherited = spawn.get("verified_inheritance") if spawn else None
        if all(isinstance(item, str) and item and not PLACEHOLDER.search(item) for item in explicit):
            selected_model, selected_effort = explicit
            selection_state = "pass"
            selection_mode = "explicit"
        elif "model" in args or "reasoning_effort" in args:
            selected_model, selected_effort = explicit
            selection_state = "fail"
            selection_mode = "explicit"
        elif isinstance(inherited, dict) and inherited.get("verified") is True:
            selected_model, selected_effort = inherited.get("model"), inherited.get("reasoning_effort")
            selection_state = "pass" if all(isinstance(item, str) and item and not PLACEHOLDER.search(item)
                                             for item in (selected_model, selected_effort)) else "fail"
            selection_mode = "verified_inheritance"
        else:
            selected_model = selected_effort = None
            selection_state = "unknown"
            selection_mode = None
        suffix = None
        if model and effort and not PLACEHOLDER.search(model) and not PLACEHOLDER.search(effort):
            suffix = "-" + re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-") + "-" + \
                     re.sub(r"[^a-z0-9]+", "-", effort.lower()).strip("-")
        canonical = packet.get("worker_name")
        expected_native = canonical.replace("-", "_") if canonical else None
        packet_values = (packet.get("worker_name"), packet.get("task_id"), packet.get("native_task_name"))
        packet_contradiction = bool((packet.get("task_id") and canonical and packet["task_id"] != canonical) or
                                    (packet.get("native_task_name") and expected_native and
                                     packet["native_task_name"] != expected_native) or
                                    (canonical and PLACEHOLDER.search(canonical)))
        packet_state = "fail" if packet_contradiction else "pass" if (
            all(packet_values) and packet.get("task_id") == canonical and
            packet.get("native_task_name") == expected_native) else "unknown"
        transport_state = "unknown" if not spawn or not native_key or expected_native is None else "pass" if (
            native_key == expected_native
        ) else "fail"
        runtime_state = "unknown" if not model or not effort or selection_state == "unknown" else "pass" if (
            suffix is not None and selection_state == "pass" and model == selected_model and effort == selected_effort
        ) else "fail"
        echo_values = (echo.get("worker_name"), echo.get("task_id"), echo.get("native_task_name"))
        echo_contradiction = bool((canonical and echo.get("worker_name") and echo["worker_name"] != canonical) or
                                  (canonical and echo.get("task_id") and echo["task_id"] != canonical) or
                                  (expected_native and echo.get("native_task_name") and
                                   echo["native_task_name"] != expected_native) or
                                  (echo.get("worker_name") and suffix and not echo["worker_name"].endswith(suffix)))
        final_state = "fail" if echo_contradiction else "pass" if (
            all(echo_values) and canonical and echo.get("worker_name") == canonical and
            echo.get("task_id") == canonical and echo.get("native_task_name") == expected_native and suffix
        ) else "unknown"
        dimensions = {
            "packet_identity": {"status": packet_state, "value": packet},
            "native_transport": {"status": transport_state,
                                 "value": {"field": "task_name" if "task_name" in args else "name" if "name" in args else None,
                                           "name": native_key or None, "spawn_line": spawn["line"] if spawn else None}},
            "spawn_selection": {"status": selection_state,
                                "value": {"mode": selection_mode, "model": selected_model,
                                          "reasoning_effort": selected_effort,
                                          "inheritance_verified": selection_mode == "verified_inheritance"}},
            "runtime_metadata": {"status": runtime_state, "value": child["runtime"]},
            "final_worker_echo": {"status": final_state, "value": echo, "evidence_line": child["final_echo_line"]},
        }
        output.append({"thread_id": child["thread_id"], "status": aggregate(list(dimensions.values())),
                       "dimensions": dimensions})
    return output


def route_contains(routes: list[dict[str, Any]], *parts: str) -> bool:
    return any(all(part in route["text"] for part in parts) for route in routes)


def child_for_name(children: list[dict[str, Any]], fragment: str) -> Optional[dict[str, Any]]:
    return next((item for item in children if fragment in (item.get("agent_path") or "")), None)


def stage_ordered(children: list[dict[str, Any]], fragments: tuple[str, ...]) -> bool:
    selected = [child_for_name(children, fragment) for fragment in fragments]
    if any(item is None or not item.get("assignment_spans") for item in selected):
        return False
    spans = [item["assignment_spans"] for item in selected if item]
    return all(parse_iso(left[-1]["end_at"]) <= parse_iso(right[0]["start_at"])
               for left, right in zip(spans, spans[1:]))


def linked_result(results: list[dict[str, Any]], producer: dict[str, Any], consumer_spawn: dict[str, Any]) -> bool:
    """Require a producer-authored hash receipt quoted by the dependent spawn packet."""
    producer_path = producer.get("agent_path") or ""
    message = consumer_spawn.get("arguments", {}).get("message")
    if not isinstance(message, str):
        return False
    for item in results:
        if item.get("author") != producer_path:
            continue
        hashes = SHA256.findall(item.get("text", ""))
        if hashes and any(digest in message for digest in hashes) and item.get("line", 0) < consumer_spawn.get("line", 0):
            return True
    return False


def routes_precede_spawns(routes: list[dict[str, Any]], spawns: list[dict[str, Any]],
                          first_business_line: Optional[int | list[int]] = None,
                          topology: Optional[str] = None) -> bool:
    if not routes:
        return False
    delegated = False
    previous_line = 0
    delegated_topology = None
    for route in routes:
        ownership, active_topology = route_decision(route["text"])
        if ownership is None or route["line"] <= previous_line:
            return False
        if delegated and (ownership != "DELEGATE" or active_topology != delegated_topology):
            return False
        if ownership == "DELEGATE":
            delegated = True
            delegated_topology = active_topology
        previous_line = route["line"]
    for spawn in spawns:
        prior = [route for route in routes if route["line"] < spawn["line"]]
        ownership, active_topology = route_decision(prior[-1]["text"]) if prior else (None, None)
        if ownership != "DELEGATE" or active_topology is None or (topology and active_topology != topology):
            return False
    lines = first_business_line if isinstance(first_business_line, list) else (
        [] if first_business_line is None else [first_business_line])
    for line in lines:
        prior = [route for route in routes if route["line"] < line]
        if not prior or route_decision(prior[-1]["text"]) != ("DIRECT", None):
            return False
    return True


def route_decision(text: str) -> tuple[Optional[str], Optional[str]]:
    match = re.match(r"^ROUTE: (DIRECT|DELEGATE)(?:\s+[—-]\s+|$)", text)
    if not match or len(re.findall(r"\b(?:DIRECT|DELEGATE)\b", text)) != 1:
        return None, None
    topology = re.findall(r"\b(?:ISOLATED_SERIAL|PARALLEL)\b", text)
    if match[1] == "DIRECT":
        return ("DIRECT", None) if not topology else (None, None)
    return ("DELEGATE", topology[0]) if len(topology) == 1 else (None, None)


def reviewer_verdict(text: str) -> str:
    """An exact verdict line, with no competing status token, is authoritative."""
    verdicts = re.findall(r"(?m)^Verdict: (PASS|FAIL)$", text)
    tokens = re.findall(r"\b(?:PASS|FAIL)\b", text, flags=re.I)
    if len(verdicts) != 1 or len(tokens) != 1:
        return "fail"
    return "pass" if verdicts == ["PASS"] else "fail"


def process_checks(case_id: str, routes: list[dict[str, Any]], children: list[dict[str, Any]],
                   spawns: list[dict[str, Any]], results: list[dict[str, Any]],
                   first_business_line: Optional[int | list[int]] = None) -> list[dict[str, Any]]:
    route_refs = [f"transcript:{item['line']}" for item in routes]
    spawn_refs = [f"parent-session:{item['line']}" for item in spawns]
    names = [(item["arguments"].get("task_name") or item["arguments"].get("name") or "") for item in spawns]
    chronology = status("route-before-worker-business", "pass" if routes_precede_spawns(
        routes, spawns, first_business_line,
        "PARALLEL" if case_id == "parallel-disjoint" else "ISOLATED_SERIAL") else "fail",
                        route_refs + spawn_refs, "a role-bounded route must precede every worker dispatch")
    if case_id == "investigation-reuse":
        producer = child_for_name(children, "investigat")
        consumer = next((item for item in spawns if "implement" in
                         (item["arguments"].get("task_name") or item["arguments"].get("name") or "")), None)
        receipt_link = bool(producer and consumer and linked_result(results, producer, consumer))
        return [
            status("route", "pass" if route_contains(routes, "DELEGATE", "ISOLATED_SERIAL") else "fail", route_refs, "role-bounded route event"),
            chronology,
            status("two-distinct-workers", "pass" if len({item["thread_id"] for item in children}) >= 2 else "fail", spawn_refs, "persisted child metadata"),
            status("receipt-result", "pass" if receipt_link else "fail", spawn_refs,
                   "producer-authored receipt hash must be consumed by the dependent packet"),
            status("stage-ownership", "pass" if any("investigat" in item for item in names) and any("implement" in item for item in names) else "unknown", spawn_refs, "native task names only"),
            status("dependency-stage-order", "pass" if stage_ordered(children, ("investigat", "implement")) else "fail",
                   ["session-index:assignment_spans"], "producer assignment ends before consumer begins"),
        ]
    if case_id == "serial-escalation":
        serial = len(children) >= 4 and ordered_without_overlap(children)
        return [
            status("direct-then-delegate", "pass" if routes and routes[0]["text"].startswith("ROUTE: DIRECT") and route_contains(routes[1:], "DELEGATE", "ISOLATED_SERIAL") else "fail", route_refs, "ordered route events"),
            chronology,
            status("scope-expanded", "pass" if route_contains(routes, "scope-expanded") else "fail", route_refs, "route trigger"),
            status("four-stages", "pass" if all(any(part in item for item in names) for part in ("plan", "schema", "api", "contract")) else "unknown", spawn_refs, "native task names only"),
            status("serial-assignment-timing", "pass" if serial and stage_ordered(children, ("plan", "schema", "api", "contract")) else "fail", ["session-index:assignment_spans"], "dependency stages use ordered assignment spans"),
        ]
    if case_id == "parallel-disjoint":
        implementation = [child for child in children if any(part in (child.get("agent_path") or "") for part in ("alpha", "beta", "gamma"))]
        spans = [child["assignment_spans"][0] for child in implementation if child["assignment_spans"]]
        ownership = []
        for label in ("alpha", "beta", "gamma"):
            child = next((item for item in implementation if label in (item.get("agent_path") or "")), None)
            ownership.append(bool(child and f"reports/{label}.md" in child["final_text"]))
        return [
            status("route", "pass" if route_contains(routes, "DELEGATE", "PARALLEL") else "fail", route_refs, "role-bounded route event"),
            chronology,
            status("three-workers", "pass" if len(implementation) == 3 else "fail", spawn_refs, "persisted child metadata"),
            status("assignment-overlap", "pass" if len(spans) == 3 and overlap_ms_from_spans(spans) > 0 else "fail", ["session-index:assignment_spans"], "first assignment span per worker"),
            status("disjoint-ownership-results", "pass" if all(ownership) else "unknown", ["worker-session:final_echo"], "each worker's own final result"),
        ]
    authors = [item for item in children if "author" in (item.get("agent_path") or "")]
    reviewers = [item for item in children if "review" in (item.get("agent_path") or "")]
    distinct = bool(authors and reviewers and authors[0]["thread_id"] != reviewers[0]["thread_id"])
    ordered = distinct and authors[0]["assignment_spans"] and reviewers[0]["assignment_spans"] and \
        parse_iso(authors[0]["assignment_spans"][-1]["end_at"]) <= parse_iso(reviewers[0]["assignment_spans"][0]["start_at"])
    review_pass = len(reviewers) == 1 and reviewer_verdict(reviewers[0]["final_text"]) == "pass"
    return [
        status("route", "pass" if route_contains(routes, "DELEGATE", "ISOLATED_SERIAL") else "fail", route_refs, "role-bounded route event"),
        chronology,
        status("independent-review-required", "pass" if route_contains(routes, "INDEPENDENT_REVIEW") else "unknown", route_refs, "route evidence only"),
        status("distinct-author-reviewer", "pass" if distinct else "fail", ["session-index:thread_ids"], "persisted child metadata"),
        status("review-after-author", "pass" if ordered else "fail", ["session-index:assignment_spans"], "assignment chronology"),
        status("review-result", "pass" if review_pass else "fail", ["worker-session:final_echo"],
               "exact Verdict: PASS line; missing, failing, or contradictory verdicts fail closed"),
    ]


def activation_diagnostics(root: Path) -> dict[str, Any]:
    """Rebuild the activation gate only from its formal, source-bound capture."""
    spec_path = root / ACTIVATION_SPEC
    if not spec_path.is_file():
        checks = [status("formal-probe-spec", "fail", [], f"missing {ACTIVATION_SPEC}")]
        return {"status": "fail", "checks": checks, "diagnostic_observations": [],
                "root_cause": "unknown", "evidence": {}}
    spec = parse_json(spec_path)
    canonical_spec = REPO_ROOT / "evals" / "live" / "fresh-activation-probe-v3.json"
    spec_ok = spec == parse_json(canonical_spec) and sha256(spec_path) == sha256(canonical_spec)
    checks = [status("formal-probe-spec", "pass" if spec_ok else "fail", [ACTIVATION_SPEC],
                     "activation-probe-v3 exact schema")]
    if not spec_ok:
        return {"status": "fail", "checks": checks, "diagnostic_observations": [],
                "root_cause": "unknown", "evidence": {"probe_spec": evidence_ref(root, spec_path)}}
    paths = spec["paths"]
    required_paths = {"transcript", "final_output", "routing_config", "environment", "hook_provenance",
                      "codex_version", "plugin_manifest", "spawn_schema", "model_catalog",
                      "activation_session_index", "python_preflight", "note_before"}
    paths_ok = isinstance(paths, dict) and set(paths) == required_paths and len(set(paths.values())) == len(paths) and all(
        isinstance(value, str) and ".." not in Path(value).parts and
        (value.startswith("raw/") if key != "activation_session_index" else
         value == "session-evidence/activation/session-index.json") for key, value in paths.items())
    checks.append(status("canonical-capture-layout", "pass" if paths_ok else "fail", [ACTIVATION_SPEC],
                         "activation inputs use raw/ plus the canonical activation session index"))
    if not paths_ok:
        return {"status": "fail", "checks": checks, "diagnostic_observations": [],
                "root_cause": "unknown", "evidence": {"probe_spec": evidence_ref(root, spec_path)}}
    resolved = {key: root / value for key, value in paths.items()}
    present = all(path.is_file() and path.stat().st_size > 0 for path in resolved.values())
    checks.append(status("required-capture-files", "pass" if present else "fail",
                         [value for key, value in paths.items() if resolved[key].is_file()],
                         "transcript, final, config, environment, provenance, CLI, manifest, and capabilities"))
    evidence = {"probe_spec": evidence_ref(root, spec_path)}
    for key, path in resolved.items():
        if path.is_file():
            evidence[key] = evidence_ref(root, path)
    if not present:
        return {"status": "fail", "checks": checks, "diagnostic_observations": [],
                "root_cause": "unknown", "evidence": evidence}

    transcript = resolved["transcript"]
    observed = transcript_observations(transcript)
    route_ok = bool(observed["route_events"] and observed["route_events"][0]["text"].startswith(
        f"ROUTE: {spec['expected_route']}") and (observed["first_business_line"] is None or
        observed["route_events"][0]["line"] < observed["first_business_line"]))
    checks.append(status("hook-route-before-business", "pass" if route_ok else "fail",
                         [f"{paths['transcript']}:{item['line']}" for item in observed["route_events"]],
                         "the controller message itself must begin with ROUTE: before business action"))

    config = parse_json(resolved["routing_config"])
    try:
        validate_config(config, str(resolved["routing_config"]))
        config_ok = True
    except ValueError:
        config_ok = False
    checks.append(status("routing-config", "pass" if config_ok else "fail", [paths["routing_config"]],
                         "parsed routing schema, not file existence"))

    environment = parse_json(resolved["environment"])
    fields = spec["required_environment_fields"]
    env_complete = isinstance(fields, list) and fields and isinstance(environment, dict) and \
        set(environment) == set(fields)
    python_path = Path(environment.get("resolved_python_executable", "")) if isinstance(environment, dict) else Path()
    env_values = env_complete and python_path.is_file() and \
        isinstance(environment.get("resolved_python_executable_sha256"), str) and \
        environment["resolved_python_executable_sha256"] == sha256(python_path) and \
        environment.get("python_encodings_import_exit_code") == 0 and \
        isinstance(environment.get("python_version"), str) and bool(environment["python_version"]) and \
        environment.get("sandbox_probe_exit_code") == 0 and \
        environment.get("sandbox_mode_requested") in ("read-only", "workspace-write", "danger-full-access") and \
        isinstance(environment.get("codex_version"), str) and bool(environment["codex_version"]) and \
        isinstance(environment.get("plugin_manifest_sha256"), str) and \
        re.fullmatch(r"[0-9a-f]{64}", environment["plugin_manifest_sha256"] or "") is not None and \
        environment["plugin_manifest_sha256"] != "0" * 64
    manifest = parse_json(resolved["plugin_manifest"])
    env_bound = bool(env_values and isinstance(manifest, dict) and manifest.get("name") == "codex-model-router" and
                     isinstance(manifest.get("version"), str) and
                     sha256(resolved["plugin_manifest"]) == environment["plugin_manifest_sha256"] and
                     environment["codex_version"] in resolved["codex_version"].read_text(encoding="utf-8"))
    checks.append(status("environment-preflight", "pass" if env_bound else "fail",
                         [paths["environment"], paths["codex_version"], paths["plugin_manifest"]],
                         "resolved Python, import, sandbox, CLI, and manifest facts are complete and hash-bound"))
    try:
        from .activation_preflight import validate_runtime_capture
    except ImportError:
        from activation_preflight import validate_runtime_capture
    runtime_ok = validate_runtime_capture(parse_json(resolved["python_preflight"]), environment)
    checks.append(status("configured-hook-python", "pass" if runtime_ok else "fail",
                         [paths["python_preflight"], paths["environment"]],
                         "the configured hook command resolves and imports encodings in its launch environment"))

    hook = parse_json(resolved["hook_provenance"])
    source = referenced_path(root, hook.get("source_config_path", "")) if isinstance(hook, dict) else Path()
    thread_id = extract_parent_id(transcript)
    hook_ok = isinstance(hook, dict) and set(hook) == {"schema_version", "hook_event", "thread_id",
        "source_config_path", "source_config_sha256", "captured_config_sha256"} and \
        hook.get("schema_version") == 1 and hook.get("hook_event") in ("SessionStart", "UserPromptSubmit") and \
        hook.get("thread_id") == thread_id and source.is_file() and \
        hook.get("source_config_sha256") == sha256(source) and \
        hook.get("captured_config_sha256") == sha256(resolved["routing_config"]) == sha256(source)
    checks.append(status("source-bound-hook-artifact", "pass" if hook_ok else "fail",
                         [paths["hook_provenance"], paths["routing_config"]],
                         "hook provenance binds the live thread and original workspace config"))

    checks.extend(activation_session_checks(root, spec, resolved, config, source, thread_id, observed))

    spawn_schema = parse_json(resolved["spawn_schema"])
    model_catalog = parse_json(resolved["model_catalog"])
    capability_ok = isinstance(spawn_schema, dict) and set(spawn_schema) == {
        "schema_version", "kind", "tool", "supported_arguments"} and \
        spawn_schema.get("schema_version") == 1 and spawn_schema.get("kind") == "spawn_schema" and \
        spawn_schema.get("tool") == "spawn_agent" and isinstance(spawn_schema.get("supported_arguments"), list) and \
        {"message", "model", "reasoning_effort"}.issubset(spawn_schema["supported_arguments"]) and \
        any(item in spawn_schema["supported_arguments"] for item in ("name", "task_name")) and \
        isinstance(model_catalog, dict) and set(model_catalog) == {"schema_version", "kind", "models"} and \
        model_catalog.get("schema_version") == 1 and model_catalog.get("kind") == "model_catalog" and \
        isinstance(model_catalog.get("models"), list) and bool(model_catalog["models"]) and all(
            isinstance(item, dict) and set(item) == {"id", "reasoning_efforts"} and
            isinstance(item["id"], str) and bool(item["id"]) and
            isinstance(item["reasoning_efforts"], list) and bool(item["reasoning_efforts"])
            for item in model_catalog["models"])
    capability_ok = capability_ok and spec["capability_kinds"] == ["spawn_schema", "model_catalog"]
    checks.append(status("dispatch-capabilities", "pass" if capability_ok else "fail",
                         [paths["spawn_schema"], paths["model_catalog"]],
                         "parsed capability sources are available before delegated cases"))
    return {"status": aggregate(checks), "checks": checks,
            "diagnostic_observations": observed["warnings"], "root_cause": "unknown", "evidence": evidence}


def activation_session_checks(root: Path, spec: dict[str, Any], resolved: dict[str, Path],
                              config: dict[str, Any], source: Path, thread_id: str,
                              observed: dict[str, Any]) -> list[dict[str, Any]]:
    """Bind the real user turn, hook content, and successful result to one session."""
    checks = []
    refs = [spec["paths"]["activation_session_index"], spec["paths"]["transcript"]]
    try:
        index = resolved["activation_session_index"]
        records = _index_records(parse_json(index))
        require(len(records) == 1 and records[0].get("relation") == "parent", "activation requires one parent only")
        record = records[0]
        path_value = record.get("copied_path") or record.get("source_path")
        require(isinstance(path_value, str), "activation session path missing")
        path = referenced_path(root, path_value)
        digest = record.get("copied_sha256") or record.get("source_sha256")
        require(path.is_file() and digest == sha256(path), "activation session hash mismatch")
        _validate_session_index(index, [{"path": path_value, "sha256": digest}], root, thread_id)
        meta = session_meta(path)
        require(meta is not None and meta.get("id") == thread_id, "activation transcript/session ID mismatch")
        require(isinstance(meta.get("cwd"), str) and
                source.resolve() == (Path(meta["cwd"]) / ".codex-model-router" / "routing.json").resolve(),
                "hook config is not from the activation workspace")
        runtime_capture = parse_json(resolved["python_preflight"])
        require(Path(runtime_capture["cwd"]).resolve() == Path(meta["cwd"]).resolve() and
                parse_iso(runtime_capture["captured_at"]) <= parse_iso(meta["timestamp"]),
                "Python preflight must precede activation in the same launch workspace")
        items = json_lines(path)
        routes = route_events_from_items(items)
        require([route["text"] for route in routes] == [route["text"] for route in observed["route_events"]]
                and bool(routes) and all(route_decision(route["text"]) == ("DIRECT", None) for route in routes),
                "activation route/session mismatch")
        require(not spawn_events(items), "activation must not delegate")
        business = first_parent_business_line(items)
        require(business is not None and routes[0]["line"] < business, "activation route must precede real business")
        checks.append(status("activation-session-binding", "pass", refs, "transcript, index, metadata, workspace, and route agree"))

        prompts = [(line, value["payload"].get("message")) for line, value in items if
                   value.get("type") == "event_msg" and isinstance(value.get("payload"), dict) and
                   value["payload"].get("type") == "user_message"]
        prompt_ok = len(prompts) == 1 and prompts[0][1] == spec["prompt"] and prompts[0][0] < routes[0]["line"]
        user_messages = [(line, value["payload"].get("content")) for line, value in items if
                         value.get("type") == "response_item" and isinstance(value.get("payload"), dict) and
                         value["payload"].get("type") == "message" and value["payload"].get("role") == "user"]
        # The only permitted auxiliary user message is runtime environment metadata.
        # Skill requests, AGENTS instructions, and additional prompts contaminate this probe.
        prompt_ok = prompt_ok and all(line < routes[0]["line"] and isinstance(content, list) and
            len(content) == 1 and isinstance(content[0], dict) and set(content[0]) == {"type", "text"} and
            content[0]["type"] == "input_text" and isinstance(content[0]["text"], str)
            for line, content in user_messages)
        texts = [content_text(content) for _line, content in user_messages]
        metadata_messages = [text for text in texts if environment_metadata(text, meta["cwd"])]
        prompt_ok = prompt_ok and len(metadata_messages) <= 1 and \
            [text for text in texts if text not in metadata_messages] == [spec["prompt"]]
        checks.append(status("neutral-user-prompt", "pass" if prompt_ok else "fail", refs,
                             "exact repository-controlled neutral user message, once, before routing"))

        hook_lines = []
        config_json = serialized_config(config, str(source))
        for line, value in items:
            payload = value.get("payload")
            if value.get("type") != "response_item" or not isinstance(payload, dict) or \
                    payload.get("type") != "message" or payload.get("role") != "developer":
                continue
            text = content_text(payload.get("content"))
            if CONTROLLER_CONTRACT in text and text.count("ROUTING_CONFIG_BEGIN") == 1 and \
                    text.count("ROUTING_CONFIG_END") == 1:
                block = text.split("ROUTING_CONFIG_BEGIN\n", 1)[-1].split("\nROUTING_CONFIG_END", 1)[0]
                if block.startswith(f"Workspace routing config: {source}\n") and block.endswith("\n" + config_json):
                    hook_lines.append(line)
        hook_ok = len(hook_lines) == 1 and hook_lines[0] < routes[0]["line"]
        checks.append(status("actual-hook-context", "pass" if hook_ok else "fail", refs,
                             "complete controller contract and exact config content injected before routing"))

        expected_note = spec["expected_note_contents"]
        note_before = resolved["note_before"]
        workspace = Path(meta["cwd"])
        note_ok = note_before.read_bytes() == expected_note.encode("utf-8") and \
            snapshot(workspace) == {"note.txt": sha256(note_before)} and not business_directories(workspace)
        _identity, final_line, final_text = final_echo(items)
        captured_final = resolved["final_output"].read_text(encoding="utf-8").strip()
        compact = json_lines(resolved["transcript"])
        terminal = successful_terminal_check(compact, items)
        messages = [value["item"].get("text") for _line, value in compact if
                    value.get("type") == "item.completed" and isinstance(value.get("item"), dict) and
                    value["item"].get("type") == "agent_message"]
        reads = [value["item"] for _line, value in compact if value.get("type") == "item.completed" and
                 isinstance(value.get("item"), dict) and value["item"].get("type") == "command_execution" and
                 "note.txt" in value["item"].get("command", "") and value["item"].get("exit_code") == 0 and
                 value["item"].get("status") == "completed" and
                 value["item"].get("aggregated_output", "").strip() == expected_note.strip()]
        edits = [value for _line, value in compact if isinstance(value.get("item"), dict) and
                 value["item"].get("type") == "file_change"]
        allowed_finals = {expected_note.strip(), "```\n" + expected_note.rstrip("\n") + "\n```",
                          "```text\n" + expected_note.rstrip("\n") + "\n```"}
        completed = bool(note_ok and terminal["status"] == "pass" and
                         final_line and final_line > business and messages and
                         final_text.strip() == captured_final == messages[-1].strip() and
                         captured_final in allowed_finals and reads and not edits and observed["failed_tool_events"] == 0)
        checks.append(status("completed-probe-outcome", "pass" if completed else "fail", refs,
                             "successful note read, unchanged tree, matching final outputs, and completed turn"))
    except (LiveEvidenceError, OSError, ValueError, KeyError, TypeError) as error:
        checks.append(status("activation-session-binding", "fail", refs, str(error)))
    return checks


def observation(case_id: str, root: Path, repo_root: Path, transcript_path: Path,
                parent_pair: tuple[Path, dict[str, Any]], child_pairs: list[tuple[Path, dict[str, Any]]],
                actual_root: Path, result_path: Path, index_path: Path, copy_results: bool,
                artifact_publish_path: Optional[Path] = None) -> dict[str, Any]:
    require(repo_root.resolve() == REPO_ROOT, "oracle repository must be the validator's own checkout")
    parent = session_record(*parent_pair)
    children = sorted((session_record(path, meta) for path, meta in child_pairs),
                      key=lambda item: item["session_start_at"] or "")
    parent_items = json_lines(parent_pair[0])
    spawns = spawn_events(parent_items)
    results = result_events(parent_items)
    compact = transcript_observations(transcript_path)
    routes = route_events_from_items(parent_items) or compact["route_events"]
    actual_tree = snapshot(actual_root)
    actual_directories = business_directories(actual_root)
    _case, oracle_path, oracle_snapshot = frozen_case(case_id)
    expected_tree = oracle_snapshot["files"]
    artifact_matches = actual_tree == expected_tree and actual_directories == oracle_snapshot["directories"]
    if copy_results:
        copy_business_tree(actual_root, result_path)
    checks = process_checks(case_id, routes, children, spawns, results, parent_business_lines(parent_items))
    terminal = successful_terminal_check(json_lines(transcript_path), parent_items)
    checks.append(terminal)
    identities = identity_checks(children, spawns)
    published = artifact_publish_path or result_path
    artifact_ref = {"path": published.relative_to(root).as_posix(), "files": actual_tree,
                    "directories": actual_directories,
                    "tree_sha256": tree_sha256({"files": actual_tree, "directories": actual_directories})}
    lifetime_sessions = [{"start_at": child["session_start_at"], "end_at": child["session_end_at"]}
                         for child in children]
    return {
        "case_id": case_id, "outcome": "completed" if artifact_matches and terminal["status"] == "pass" else "failed",
        "parent_session": parent, "worker_sessions": children, "route_events": routes,
        "timing": {
            "parent_session_lifetime_ms": round((parse_iso(parent["session_end_at"]) - parse_iso(parent["session_start_at"])).total_seconds() * 1000) if parent["session_end_at"] else None,
            "all_worker_assignment_overlap_ms": overlap_ms_from_spans([child["assignment_spans"][0] for child in children]) if len(children) > 1 and all(child["assignment_spans"] for child in children) else None,
            "session_lifetime_overlap_ms": overlap_ms(lifetime_sessions) if len(children) > 1 else 0,
        },
        "artifact_validation": {"status": "pass" if artifact_matches else "fail",
                                "actual_tree": actual_tree, "expected_tree": expected_tree,
                                "actual_directories": actual_directories,
                                "expected_directories": oracle_snapshot["directories"]},
        "process_validation": {"status": aggregate(checks), "checks": checks},
        "identity_contract_validation": {"status": aggregate(identities), "workers": identities},
        "usage": {"kind": "observed", "scope": "codex-exec-terminal-event", "tokens": compact["usage"]},
        "cost": {"kind": "unavailable", "complete": False, "usd": None},
        "evidence": {
            "transcript": {"path": transcript_path.relative_to(root).as_posix(), "sha256": compact["sha256"]},
            "session_index": {"path": index_path.relative_to(root).as_posix(), "sha256": sha256(index_path)},
            "artifact_tree": artifact_ref,
            "oracle_tree": {"path": str(oracle_path.resolve()),
                            "files": expected_tree, "directories": oracle_snapshot["directories"],
                            "tree_sha256": tree_sha256(oracle_snapshot)},
            "session_sources": [{"path": item["source_path"], "sha256": item["source_sha256"]}
                                for item in [parent, *children]],
        },
    }


def recompute_acceptance(report: dict[str, Any]) -> dict[str, Any]:
    observations = report["observations"]
    artifact = all(item["artifact_validation"]["status"] == "pass" for item in observations)
    process = all(item["process_validation"]["status"] == "pass" for item in observations)
    identity = all(item["identity_contract_validation"]["status"] == "pass" for item in observations)
    activation = report["activation_gate"]["status"] == "pass"
    completed = sum(item["outcome"] == "completed" for item in observations)
    return {"scheduled_runs": len(observations),
            "completed_runs": completed,
            "artifact_pass": artifact, "required_route_process_pass": process,
            "identity_contract_pass": identity, "activation_gate_pass": activation,
            "campaign_pass": completed == len(observations) and activation and artifact and process and identity}


def validate_acceptance(report: dict[str, Any]) -> dict[str, Any]:
    """Reject authored campaign flags that disagree with derived statuses."""
    expected = recompute_acceptance(report)
    require(report.get("acceptance") == expected,
            "acceptance flags do not match recomputed evidence statuses")
    return expected


def build_report(root: Path, repo_root: Path, transcript_paths: dict[str, Path],
                 pairs: dict[str, tuple[tuple[Path, dict[str, Any]], list[tuple[Path, dict[str, Any]]]]],
                 actual_roots: dict[str, Path], copy_results: bool, original: Optional[Path] = None,
                 staging_results: Optional[Path] = None) -> dict[str, Any]:
    observations = []
    for case_id in CASES:
        index_path = root / "session-evidence" / case_id / "session-index.json"
        require(index_path.is_file(), f"missing session index: {index_path}")
        parent_pair, child_pairs = pairs[case_id]
        result_path = (staging_results or (root / "results")) / case_id
        observations.append(observation(case_id, root, repo_root, transcript_paths[case_id], parent_pair,
                                        child_pairs, actual_roots[case_id], result_path, index_path, copy_results,
                                        root / "results" / case_id))
    report = {
        "schema_version": 3, "campaign_id": root.name, "data_origin": "observed",
        "analysis_version": "source-derived-v3", "historical_result_preserved": bool(original),
        "original_result": evidence_ref(root, original) if original else None,
        "activation_gate": activation_diagnostics(root), "observations": observations, "acceptance": {},
        "claim_boundary": {"measured_usd_available": False, "automatic_hook_compliance_established": False,
                           "context_deletion_or_sandboxing_established": False,
                           "comparative_cost_or_latency_claim_supported": False},
    }
    report["acceptance"] = recompute_acceptance(report)
    return report


def collect(root: Path, repo_root: Path, sessions_root: Path,
            staging_results: Optional[Path] = None) -> dict[str, Any]:
    transcripts = {case: (root / "raw" / f"{case}-rerun.jsonl" if (root / "raw" / f"{case}-rerun.jsonl").is_file()
                          else root / "raw" / f"{case}.jsonl") for case in CASES}
    parent_ids = {case: extract_parent_id(path) for case, path in transcripts.items()}
    catalog = session_catalog(sessions_root, set(parent_ids.values()))
    pairs = {}
    for case, parent_id in parent_ids.items():
        matched = catalog[parent_id]
        parents = [(path, meta) for path, meta in matched if meta.get("id") == parent_id]
        children = [(path, meta) for path, meta in matched if meta.get("parent_thread_id") == parent_id]
        require(len(parents) == 1 and children, f"incomplete persisted session set for {case}")
        pairs[case] = (parents[0], children)
    actual = {case: root / "workspaces" / (f"{case}-rerun" if transcripts[case].stem.endswith("-rerun") else case)
              for case in CASES}
    return build_report(root, repo_root, transcripts, pairs, actual, True,
                        staging_results=staging_results)


def reprocess(root: Path, repo_root: Path, original: Path) -> dict[str, Any]:
    prior = parse_json(original)
    by_case = {item["case_id"]: item for item in prior["observations"]}
    transcripts = {case: root / by_case[case]["evidence"]["transcript"]["path"] for case in CASES}
    pairs = {}
    for case in CASES:
        session_values = [by_case[case]["parent_session"], *by_case[case]["worker_sessions"]]
        resolved = []
        for item in session_values:
            path = Path(item["source_path"])
            require(path.is_file() and sha256(path) == item["source_sha256"], f"session source mismatch: {path}")
            meta = session_meta(path)
            require(meta is not None, f"missing session metadata: {path}")
            resolved.append((path, meta))
        pairs[case] = (resolved[0], resolved[1:])
    actual = {case: root / "results" / case for case in CASES}
    return build_report(root, repo_root, transcripts, pairs, actual, False, original)


def _index_records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return value if all(isinstance(item, dict) for item in value) else []
    if isinstance(value, dict) and value.get("relation") in ("parent", "child"):
        return [value]
    if isinstance(value, dict) and isinstance(value.get("records"), list):
        return _index_records(value["records"])
    if isinstance(value, dict) and isinstance(value.get("parent"), dict) and isinstance(value.get("children"), list):
        if not all(isinstance(item, dict) for item in value["children"]):
            return []
        return [{"relation": "parent", **value["parent"]},
                *[{"relation": "child", **item} for item in value["children"]]]
    return []


def _validate_session_index(index_path: Path, sources: list[dict[str, Any]],
                            root: Path, parent_id: str) -> None:
    records = _index_records(parse_json(index_path))
    require(records and len(records) == len(sources), f"session index/source cardinality mismatch: {index_path}")
    require(len({item["sha256"] for item in sources}) == len(sources), "duplicate session sources")
    require(len({item.get("thread_id") for item in records}) == len(records), "duplicate index thread IDs")
    for source in sources:
        source_hash = source["sha256"]
        matched = [item for item in records if item.get("source_sha256") == source_hash or
                   item.get("copied_sha256") == source_hash]
        require(len(matched) == 1, f"session index does not uniquely bind source: {source['path']}")
        item = matched[0]
        path = referenced_path(root, source["path"])
        meta = session_meta(path)
        require(meta is not None, "indexed session has no metadata")
        relation = "parent" if meta.get("id") == parent_id else "child"
        expected_path = session_record(path, meta)["agent_path"]
        require(item.get("relation") == relation and item.get("thread_id") == meta.get("id") and
                item.get("parent_thread_id") == meta.get("parent_thread_id") and
                item.get("agent_path") == expected_path, "session index metadata/ancestry mismatch")
        require(any(isinstance(item.get(prefix + "_path"), str) and
                    referenced_path(root, item[prefix + "_path"]).resolve() == path.resolve() and
                    item.get(prefix + "_sha256") == source_hash for prefix in ("source", "copied")),
                "session index path is not bound to source")
        for prefix in ("source", "copied"):
            if prefix + "_path" in item or prefix + "_sha256" in item:
                reference = item.get(prefix + "_path")
                require(isinstance(reference, str) and item.get(prefix + "_sha256") == source_hash,
                        "session index has contradictory source/copy references")
                candidate = referenced_path(root, reference)
                require(candidate.is_file() and sha256(candidate) == source_hash,
                        "session index source/copy reference is unverified")
        lines = item.get("source_evidence_lines")
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        require(isinstance(lines, list) and bool(lines) and all(type(line) is int and
                1 <= line <= line_count for line in lines), "invalid session index evidence lines")


def validate_session_ancestry(parent: dict[str, Any], children: list[dict[str, Any]],
                              items: list[tuple[int, dict[str, Any]]],
                              spawns: list[dict[str, Any]]) -> None:
    require(len({child["thread_id"] for child in children}) == len(children), "duplicate child sessions")
    require(len(spawns) == len(children) and bool(children), "native dispatch/child cardinality mismatch")
    native_paths = []
    for spawn in spawns:
        call_id = spawn.get("call_id")
        require(isinstance(call_id, str) and bool(call_id) and
                sum(other.get("call_id") == call_id for other in spawns) == 1,
                "missing or duplicate native dispatch call ID")
        outputs = [(line, value["payload"]) for line, value in items if
                   value.get("type") == "response_item" and isinstance(value.get("payload"), dict) and
                   value["payload"].get("type") == "function_call_output" and
                   value["payload"].get("call_id") == call_id]
        require(len(outputs) == 1 and outputs[0][0] > spawn["line"], "native dispatch result is not linked")
        try:
            output = json.loads(outputs[0][1].get("output", ""))
        except (TypeError, json.JSONDecodeError) as error:
            raise LiveEvidenceError("native dispatch result must be captured JSON") from error
        native_path = output.get("task_name") if isinstance(output, dict) else None
        native_name = spawn["arguments"].get("task_name") or spawn["arguments"].get("name")
        require(isinstance(native_path, str) and isinstance(native_name, str) and
                native_path == (parent.get("agent_path") or "/root") + "/" + native_name,
                "native dispatch result/path mismatch")
        native_paths.append(native_path)
    require(len(set(native_paths)) == len(native_paths), "ambiguous repeated native dispatch name")
    require(set(native_paths) == {child.get("agent_path") for child in children},
            "child paths do not match native dispatch results")
    require(all(child.get("parent_thread_id") == parent["thread_id"] for child in children),
            "child ancestry does not match transcript parent")


def _derived_observation(root: Path, authored: dict[str, Any]) -> dict[str, Any]:
    evidence = authored["evidence"]
    case_id = authored["case_id"]
    _case, frozen_oracle_path, frozen_snapshot = frozen_case(case_id)
    require(evidence["transcript"]["path"] in (f"raw/{case_id}.jsonl", f"raw/{case_id}-rerun.jsonl"),
            "transcript path does not belong to scenario")
    transcript_path = referenced_path(root, evidence["transcript"]["path"])
    index_path = canonical_path(root, evidence["session_index"]["path"],
                                f"session-evidence/{case_id}/session-index.json")
    for key in ("transcript", "session_index"):
        path = referenced_path(root, evidence[key]["path"])
        require(path.is_file() and sha256(path) == evidence[key]["sha256"], f"evidence mismatch: {path}")
    sources = evidence["session_sources"]
    require(isinstance(sources, list) and sources, "missing session source references")
    session_values = []
    for source_ref in sources:
        path = referenced_path(root, source_ref["path"])
        require(path.is_file() and sha256(path) == source_ref["sha256"], f"session source mismatch: {path}")
        meta = session_meta(path)
        require(meta is not None, f"missing session metadata: {path}")
        session_values.append(session_record(path, meta))
    parent_id = extract_parent_id(transcript_path)
    _validate_session_index(index_path, sources, root, parent_id)
    parents = [item for item in session_values if item["thread_id"] == parent_id]
    require(len(parents) == 1, "session evidence must contain exactly one transcript parent")
    parent = parents[0]
    children = sorted([item for item in session_values if item["thread_id"] != parent["thread_id"]],
                      key=lambda item: item["session_start_at"] or "")
    parent_path = Path(parent["source_path"])
    parent_items = json_lines(parent_path)
    spawns = spawn_events(parent_items)
    validate_session_ancestry(parent, children, parent_items, spawns)
    results = result_events(parent_items)
    compact = transcript_observations(transcript_path)
    routes = route_events_from_items(parent_items)
    require([item["text"] for item in routes] == [item["text"] for item in compact["route_events"]],
            "transcript routes disagree with parent session")
    checks = process_checks(authored["case_id"], routes, children, spawns, results,
                            parent_business_lines(parent_items))
    terminal = successful_terminal_check(json_lines(transcript_path), parent_items)
    checks.append(terminal)
    identities = identity_checks(children, spawns)

    artifact = evidence["artifact_tree"]
    artifact_path = canonical_path(root, artifact["path"], f"results/{case_id}")
    actual_tree = snapshot(artifact_path)
    actual_directories = business_directories(artifact_path)
    require(actual_tree == artifact["files"] and actual_directories == artifact.get("directories") and
            tree_sha256({"files": actual_tree, "directories": actual_directories}) == artifact["tree_sha256"],
            f"artifact reference mismatch: {artifact_path}")
    oracle = evidence["oracle_tree"]
    oracle_path = referenced_path(root, oracle["path"])
    require(oracle_path.resolve() == frozen_oracle_path.resolve(), "oracle path is not the frozen case oracle")
    expected_tree = frozen_snapshot["files"]
    require(snapshot(oracle_path) == expected_tree, "repository oracle differs from pinned fixture snapshot")
    require(expected_tree == oracle["files"] and frozen_snapshot["directories"] == oracle.get("directories") and
            tree_sha256(frozen_snapshot) == oracle["tree_sha256"],
            f"frozen oracle reference mismatch: {oracle_path}")
    artifact_matches = actual_tree == expected_tree and actual_directories == frozen_snapshot["directories"]
    lifetime_sessions = [{"start_at": child["session_start_at"], "end_at": child["session_end_at"]}
                         for child in children]
    return {
        "outcome": "completed" if artifact_matches and terminal["status"] == "pass" else "failed",
        "parent_session": parent,
        "worker_sessions": children,
        "route_events": routes,
        "timing": {
            "parent_session_lifetime_ms": round((parse_iso(parent["session_end_at"]) -
                parse_iso(parent["session_start_at"])).total_seconds() * 1000) if parent["session_end_at"] else None,
            "all_worker_assignment_overlap_ms": overlap_ms_from_spans(
                [child["assignment_spans"][0] for child in children]) if len(children) > 1 and
                all(child["assignment_spans"] for child in children) else None,
            "session_lifetime_overlap_ms": overlap_ms(lifetime_sessions) if len(children) > 1 else 0,
        },
        "artifact_validation": {"status": "pass" if artifact_matches else "fail",
                                "actual_tree": actual_tree, "expected_tree": expected_tree,
                                "actual_directories": actual_directories,
                                "expected_directories": frozen_snapshot["directories"]},
        "process_validation": {"status": aggregate(checks), "checks": checks},
        "identity_contract_validation": {"status": aggregate(identities), "workers": identities},
        "usage": {"kind": "observed", "scope": "codex-exec-terminal-event", "tokens": compact["usage"]},
        "cost": {"kind": "unavailable", "complete": False, "usd": None},
    }


def validate(results_path: Path) -> dict[str, Any]:
    root = results_path.parent
    value = parse_json(results_path)
    require(isinstance(value, dict) and set(value) == {"schema_version", "campaign_id", "data_origin",
            "analysis_version", "historical_result_preserved", "original_result", "activation_gate",
            "observations", "acceptance", "claim_boundary"}, "live report keys differ from schema")
    require(value.get("schema_version") == 3 and value.get("analysis_version") == "source-derived-v3" and
            value.get("data_origin") == "observed", "not an observed live-results-v3 artifact")
    require([item.get("case_id") for item in value.get("observations", [])] == list(CASES),
            "live campaign must retain ordered required scenarios")
    require(value.get("claim_boundary") == {"measured_usd_available": False,
            "automatic_hook_compliance_established": False,
            "context_deletion_or_sandboxing_established": False,
            "comparative_cost_or_latency_claim_supported": False},
            "claim boundary disagrees with observed evidence limits")
    original = value.get("original_result")
    if original is not None:
        original_path = referenced_path(root, original["path"])
        require(original_path.is_file() and sha256(original_path) == original["sha256"],
                f"original result mismatch: {original_path}")
    for observation_value in value["observations"]:
        require(set(observation_value["evidence"]) == {"transcript", "session_index", "artifact_tree",
                "oracle_tree", "session_sources"},
                f"{observation_value['case_id']} evidence keys differ from schema")
        derived = _derived_observation(root, observation_value)
        for key, expected in derived.items():
            require(observation_value.get(key) == expected,
                    f"authored {observation_value['case_id']} {key} disagrees with raw evidence")
    derived_activation = activation_diagnostics(root)
    require(value.get("activation_gate") == derived_activation,
            "authored activation gate disagrees with raw evidence")
    expected = validate_acceptance(value)
    return {"validated_live_records": len(value["observations"]), **expected}


def publish_json_fresh(destination: Path, value: dict[str, Any]) -> None:
    """Publish one new JSON artifact with no observable partial file."""
    require(not destination.exists(), f"refusing to overwrite evidence report: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def collect_and_publish(root: Path, repo_root: Path, sessions_root: Path, destination: Path) -> dict[str, Any]:
    """Preflight every destination, stage all copies, and publish fresh or leave nothing."""
    results_destination = root / "results"
    require(destination.parent.resolve() == root.resolve(),
            "collection output must use the evidence root so relative references remain bound")
    require(not destination.exists(), f"refusing to overwrite evidence report: {destination}")
    require(not results_destination.exists(), f"refusing to overwrite collected results: {results_destination}")
    stage = root / f".live-evidence-stage-{uuid.uuid4().hex}"
    require(not stage.exists(), f"staging destination already exists: {stage}")
    stage_results = stage / "results"
    published_results = False
    try:
        stage_results.mkdir(parents=True)
        result = collect(root, repo_root, sessions_root, stage_results)
        os.replace(stage_results, results_destination)
        published_results = True
        temporary_report = root / f".live-results-{uuid.uuid4().hex}.json"
        publish_json_fresh(temporary_report, result)
        try:
            validation = validate(temporary_report)
            require(not destination.exists(), f"refusing to overwrite evidence report: {destination}")
            os.replace(temporary_report, destination)
        finally:
            if temporary_report.exists():
                temporary_report.unlink()
        return {"results": str(destination), **validation}
    except Exception:
        if published_results and results_destination.is_dir():
            shutil.rmtree(results_destination)
        raise
    finally:
        if stage.is_dir():
            shutil.rmtree(stage)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    collect_parser = sub.add_parser("collect")
    collect_parser.add_argument("--root", type=Path, required=True)
    collect_parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    collect_parser.add_argument("--sessions-root", type=Path, required=True)
    collect_parser.add_argument("--output", type=Path)
    reprocess_parser = sub.add_parser("reprocess")
    reprocess_parser.add_argument("--root", type=Path, required=True)
    reprocess_parser.add_argument("--original", type=Path, required=True)
    reprocess_parser.add_argument("--output", type=Path, required=True)
    reprocess_parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "validate":
            output = validate(args.results.resolve())
        else:
            root = args.root.resolve()
            if args.command == "collect":
                destination = args.output.resolve() if args.output else root / "live-results-v3.json"
                output = collect_and_publish(root, args.repo_root.resolve(), args.sessions_root.resolve(), destination)
            else:
                result = reprocess(root, args.repo_root.resolve(), args.original.resolve())
                destination = args.output.resolve()
                require(not destination.exists(), f"refusing to overwrite evidence report: {destination}")
                publish_json_fresh(destination, result)
                try:
                    output = {"results": str(destination), **validate(destination)}
                except Exception:
                    destination.unlink()
                    raise
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
