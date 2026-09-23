#!/usr/bin/env python3
"""Collect, reprocess, and validate role-bounded Codex live evidence.

Version 2 never searches aggregate transcript text. It preserves packet,
native transport, selector, runtime, and final-echo identity as distinct
dimensions, and reports assignment overlap separately from session lifetime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


CASES = ("investigation-reuse", "serial-escalation", "parallel-disjoint", "architecture-review")
IGNORED_WORKSPACE_NAMES = {".git", ".codex-model-router"}
PLACEHOLDER = re.compile(r"(?:^|[-_])(unknown|unavailable|unexposed|unresolved|placeholder)(?:$|[-_])", re.I)
SHA256 = re.compile(r"\b[0-9a-f]{64}\b")


class LiveEvidenceError(ValueError):
    """Observed evidence is missing, contaminated, or internally inconsistent."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise LiveEvidenceError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_sha256(files: dict[str, str]) -> str:
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
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in IGNORED_WORKSPACE_NAMES for part in relative.parts):
            continue
        if path.is_file():
            result[relative.as_posix()] = sha256(path)
    return result


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


def session_meta(path: Path) -> Optional[dict[str, Any]]:
    try:
        value = json.loads(path.open("r", encoding="utf-8", errors="replace").readline())
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
    for _line, value in json_lines(transcript):
        if value.get("type") == "thread.started" and isinstance(value.get("thread_id"), str):
            return value["thread_id"]
    raise LiveEvidenceError(f"no thread.started event in {transcript}")


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


def route_events_from_items(items: list[tuple[int, dict[str, Any]]]) -> list[dict[str, Any]]:
    result = []
    for line, value in items:
        payload = value.get("payload")
        if value.get("type") != "event_msg" or not isinstance(payload, dict) or payload.get("type") != "agent_message":
            continue
        message = payload.get("message")
        if not isinstance(message, str):
            continue
        for route in (item.strip() for item in message.splitlines() if item.strip().startswith("ROUTE:")):
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
            routes.extend({"line": line, "text": route.strip(), "role": "controller_agent_message"}
                          for route in text.splitlines() if route.strip().startswith("ROUTE:"))
        message = value.get("message")
        if isinstance(message, str) and "hook" in message.lower():
            warnings.append({"line": line, "text": message})
    usage = next((value["usage"] for _line, value in reversed(items)
                  if value.get("type") == "turn.completed" and isinstance(value.get("usage"), dict)), None)
    failed = sum(value.get("type") == "item.completed" and isinstance(value.get("item"), dict)
                 and value["item"].get("status") == "failed" for _line, value in items)
    return {"route_events": routes, "warnings": warnings, "usage": usage, "failed_tool_events": failed,
            "raw_log_bytes": path.stat().st_size, "sha256": sha256(path)}


def session_record(path: Path, meta: dict[str, Any]) -> dict[str, Any]:
    items = json_lines(path)
    context = turn_context(items)
    spans = assignment_spans(items)
    echo, echo_line, echo_text = final_echo(items)
    source = meta.get("source")
    agent_path = meta.get("agent_path")
    if not agent_path and isinstance(source, dict):
        agent_path = source.get("subagent", {}).get("thread_spawn", {}).get("agent_path")
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
        final_state = "unknown"
        if echo["worker_name"] and echo["task_id"]:
            suffix = "-" + re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-") + "-" + \
                     re.sub(r"[^a-z0-9]+", "-", effort.lower()).strip("-") if model and effort else None
            final_state = "pass" if (echo["worker_name"] == echo["task_id"] and suffix
                                     and echo["worker_name"].endswith(suffix)
                                     and not PLACEHOLDER.search(echo["worker_name"])) else "fail"
        selection_state = "pass" if isinstance(args.get("model"), str) and isinstance(args.get("reasoning_effort"), str) else "unknown"
        dimensions = {
            "packet_identity": {"status": "pass" if packet["worker_name"] else "unknown", "value": packet},
            "native_transport": {"status": "pass" if spawn and native_key else "unknown",
                                 "value": {"field": "task_name" if "task_name" in args else "name" if "name" in args else None,
                                           "name": native_key or None, "spawn_line": spawn["line"] if spawn else None}},
            "spawn_selection": {"status": selection_state,
                                "value": {"model": args.get("model"), "reasoning_effort": args.get("reasoning_effort"),
                                          "inheritance_verified": False}},
            "runtime_metadata": {"status": "pass" if model and effort else "unknown", "value": child["runtime"]},
            "final_worker_echo": {"status": final_state, "value": echo, "evidence_line": child["final_echo_line"]},
        }
        output.append({"thread_id": child["thread_id"], "status": aggregate(list(dimensions.values())),
                       "dimensions": dimensions})
    return output


def route_contains(routes: list[dict[str, Any]], *parts: str) -> bool:
    return any(all(part in route["text"] for part in parts) for route in routes)


def process_checks(case_id: str, routes: list[dict[str, Any]], children: list[dict[str, Any]],
                   spawns: list[dict[str, Any]], results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    route_refs = [f"transcript:{item['line']}" for item in routes]
    spawn_refs = [f"parent-session:{item['line']}" for item in spawns]
    names = [(item["arguments"].get("task_name") or item["arguments"].get("name") or "") for item in spawns]
    if case_id == "investigation-reuse":
        receipt = next((item for item in results if "docs/ops.md" in item["text"] and "45" in item["text"]
                        and SHA256.search(item["text"])), None)
        return [
            status("route", "pass" if route_contains(routes, "DELEGATE", "ISOLATED_SERIAL") else "fail", route_refs, "role-bounded route event"),
            status("two-distinct-workers", "pass" if len({item["thread_id"] for item in children}) >= 2 else "fail", spawn_refs, "persisted child metadata"),
            status("receipt-result", "pass" if receipt else "unknown", [f"parent-session:{receipt['line']}"] if receipt else [], "specific child result, not inherited prompt text"),
            status("stage-ownership", "pass" if any("investigat" in item for item in names) and any("implement" in item for item in names) else "unknown", spawn_refs, "native task names only"),
        ]
    if case_id == "serial-escalation":
        serial = len(children) >= 4 and ordered_without_overlap(children)
        return [
            status("direct-then-delegate", "pass" if routes and routes[0]["text"].startswith("ROUTE: DIRECT") and route_contains(routes[1:], "DELEGATE", "ISOLATED_SERIAL") else "fail", route_refs, "ordered route events"),
            status("scope-expanded", "pass" if route_contains(routes, "scope-expanded") else "fail", route_refs, "route trigger"),
            status("four-stages", "pass" if all(any(part in item for item in names) for part in ("plan", "schema", "api", "contract")) else "unknown", spawn_refs, "native task names only"),
            status("serial-assignment-timing", "pass" if serial else "fail", ["session-index:assignment_spans"], "assignment spans, not session lifetime"),
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
            status("three-workers", "pass" if len(implementation) == 3 else "fail", spawn_refs, "persisted child metadata"),
            status("assignment-overlap", "pass" if len(spans) == 3 and overlap_ms_from_spans(spans) > 0 else "fail", ["session-index:assignment_spans"], "first assignment span per worker"),
            status("disjoint-ownership-results", "pass" if all(ownership) else "unknown", ["worker-session:final_echo"], "each worker's own final result"),
        ]
    authors = [item for item in children if "author" in (item.get("agent_path") or "")]
    reviewers = [item for item in children if "review" in (item.get("agent_path") or "")]
    distinct = bool(authors and reviewers and authors[0]["thread_id"] != reviewers[0]["thread_id"])
    ordered = distinct and authors[0]["assignment_spans"] and reviewers[0]["assignment_spans"] and \
        parse_iso(authors[0]["assignment_spans"][-1]["end_at"]) <= parse_iso(reviewers[0]["assignment_spans"][0]["start_at"])
    review_pass = bool(reviewers and re.search(r"\bPASS\b", reviewers[0]["final_text"]))
    return [
        status("route", "pass" if route_contains(routes, "DELEGATE", "ISOLATED_SERIAL") else "fail", route_refs, "role-bounded route event"),
        status("independent-review-required", "pass" if route_contains(routes, "INDEPENDENT_REVIEW") else "unknown", route_refs, "route evidence only"),
        status("distinct-author-reviewer", "pass" if distinct else "fail", ["session-index:thread_ids"], "persisted child metadata"),
        status("review-after-author", "pass" if ordered else "fail", ["session-index:assignment_spans"], "assignment chronology"),
        status("review-result", "pass" if review_pass else "unknown", ["worker-session:final_echo"], "reviewer's own final result"),
    ]


def activation_diagnostics(root: Path) -> dict[str, Any]:
    transcript = root / "raw" / "activation.jsonl"
    require(transcript.is_file(), f"missing activation transcript: {transcript}")
    observed = transcript_observations(transcript)
    config = root / "raw" / "activation-routing.json"
    env = root / "raw" / "activation-environment.json"
    checks = [
        status("hook-route-event", "pass" if route_contains(observed["route_events"], "ROUTE: DIRECT") else "fail",
               [f"raw/activation.jsonl:{item['line']}" for item in observed["route_events"]], "controller agent-message source"),
        status("hook-config-artifact", "pass" if config.is_file() else "fail",
               ["raw/activation-routing.json"] if config.is_file() else [], "hook-created source-specific artifact"),
        status("environment-preflight", "pass" if env.is_file() else "unknown",
               ["raw/activation-environment.json"] if env.is_file() else [], "Python and sandbox preflight evidence"),
    ]
    evidence = {"transcript": {"path": "raw/activation.jsonl", "sha256": sha256(transcript)}}
    if config.is_file():
        evidence["routing_config"] = {"path": "raw/activation-routing.json", "sha256": sha256(config)}
    if env.is_file():
        evidence["environment_preflight"] = {"path": "raw/activation-environment.json", "sha256": sha256(env)}
    return {"status": aggregate(checks), "checks": checks,
            "diagnostic_observations": observed["warnings"], "root_cause": "unknown",
            "evidence": evidence}


def observation(case_id: str, root: Path, repo_root: Path, transcript_path: Path,
                parent_pair: tuple[Path, dict[str, Any]], child_pairs: list[tuple[Path, dict[str, Any]]],
                actual_root: Path, result_path: Path, index_path: Path, copy_results: bool) -> dict[str, Any]:
    parent = session_record(*parent_pair)
    children = sorted((session_record(path, meta) for path, meta in child_pairs),
                      key=lambda item: item["session_start_at"] or "")
    parent_items = json_lines(parent_pair[0])
    spawns = spawn_events(parent_items)
    results = result_events(parent_items)
    compact = transcript_observations(transcript_path)
    routes = route_events_from_items(parent_items) or compact["route_events"]
    actual_tree = snapshot(actual_root)
    expected_tree = snapshot(repo_root / "evals" / "fixtures" / case_id / "reference")
    if copy_results:
        copy_business_tree(actual_root, result_path)
    checks = process_checks(case_id, routes, children, spawns, results)
    identities = identity_checks(children, spawns)
    artifact_ref = {"path": result_path.relative_to(root).as_posix(), "files": actual_tree,
                    "tree_sha256": tree_sha256(actual_tree)}
    lifetime_sessions = [{"start_at": child["session_start_at"], "end_at": child["session_end_at"]}
                         for child in children]
    return {
        "case_id": case_id, "outcome": "completed" if actual_tree == expected_tree else "failed",
        "parent_session": parent, "worker_sessions": children, "route_events": routes,
        "timing": {
            "parent_session_lifetime_ms": round((parse_iso(parent["session_end_at"]) - parse_iso(parent["session_start_at"])).total_seconds() * 1000) if parent["session_end_at"] else None,
            "all_worker_assignment_overlap_ms": overlap_ms_from_spans([child["assignment_spans"][0] for child in children]) if len(children) > 1 and all(child["assignment_spans"] for child in children) else None,
            "session_lifetime_overlap_ms": overlap_ms(lifetime_sessions) if len(children) > 1 else 0,
        },
        "artifact_validation": {"status": "pass" if actual_tree == expected_tree else "fail",
                                "actual_tree": actual_tree, "expected_tree": expected_tree},
        "process_validation": {"status": aggregate(checks), "checks": checks},
        "identity_contract_validation": {"status": aggregate(identities), "workers": identities},
        "usage": {"kind": "observed", "scope": "codex-exec-terminal-event", "tokens": compact["usage"]},
        "cost": {"kind": "unavailable", "complete": False, "usd": None},
        "evidence": {
            "transcript": {"path": transcript_path.relative_to(root).as_posix(), "sha256": compact["sha256"]},
            "session_index": {"path": index_path.relative_to(root).as_posix(), "sha256": sha256(index_path)},
            "artifact_tree": artifact_ref,
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
    return {"scheduled_runs": len(observations),
            "completed_runs": sum(item["outcome"] == "completed" for item in observations),
            "artifact_pass": artifact, "required_route_process_pass": process,
            "identity_contract_pass": identity, "activation_gate_pass": activation,
            "campaign_pass": activation and artifact and process and identity}


def validate_acceptance(report: dict[str, Any]) -> dict[str, Any]:
    """Reject authored campaign flags that disagree with derived statuses."""
    expected = recompute_acceptance(report)
    require(report.get("acceptance") == expected,
            "acceptance flags do not match recomputed evidence statuses")
    return expected


def build_report(root: Path, repo_root: Path, transcript_paths: dict[str, Path],
                 pairs: dict[str, tuple[tuple[Path, dict[str, Any]], list[tuple[Path, dict[str, Any]]]]],
                 actual_roots: dict[str, Path], copy_results: bool, original: Optional[Path] = None) -> dict[str, Any]:
    observations = []
    for case_id in CASES:
        index_path = root / "session-evidence" / case_id / "session-index.json"
        require(index_path.is_file(), f"missing session index: {index_path}")
        parent_pair, child_pairs = pairs[case_id]
        observations.append(observation(case_id, root, repo_root, transcript_paths[case_id], parent_pair,
                                        child_pairs, actual_roots[case_id], root / "results" / case_id,
                                        index_path, copy_results))
    report = {
        "schema_version": 2, "campaign_id": root.name, "data_origin": "observed",
        "analysis_version": "role-bounded-v2", "historical_result_preserved": bool(original),
        "original_result": {"path": original.relative_to(root).as_posix(), "sha256": sha256(original)} if original else None,
        "activation_gate": activation_diagnostics(root), "observations": observations, "acceptance": {},
        "claim_boundary": {"measured_usd_available": False, "automatic_hook_compliance_established": False,
                           "context_deletion_or_sandboxing_established": False,
                           "comparative_cost_or_latency_claim_supported": False},
    }
    report["acceptance"] = recompute_acceptance(report)
    return report


def collect(root: Path, repo_root: Path, sessions_root: Path) -> dict[str, Any]:
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
    return build_report(root, repo_root, transcripts, pairs, actual, True)


def reprocess(root: Path, repo_root: Path, original: Path) -> dict[str, Any]:
    prior = json.loads(original.read_text(encoding="utf-8"))
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


def validate(results_path: Path) -> dict[str, Any]:
    root = results_path.parent
    value = json.loads(results_path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 2 and value.get("data_origin") == "observed",
            "not an observed live-results-v2 artifact")
    require([item.get("case_id") for item in value.get("observations", [])] == list(CASES),
            "live campaign must retain ordered required scenarios")
    for observation_value in value["observations"]:
        evidence = observation_value["evidence"]
        for key in ("transcript", "session_index"):
            path = root / evidence[key]["path"]
            require(path.is_file() and sha256(path) == evidence[key]["sha256"], f"evidence mismatch: {path}")
        artifact = evidence["artifact_tree"]
        artifact_path = root / artifact["path"]
        files = snapshot(artifact_path)
        require(files == artifact["files"] and tree_sha256(files) == artifact["tree_sha256"],
                f"artifact reference mismatch: {artifact_path}")
        expected_artifact_status = "pass" if observation_value["artifact_validation"]["actual_tree"] == \
            observation_value["artifact_validation"]["expected_tree"] else "fail"
        require(observation_value["artifact_validation"]["status"] == expected_artifact_status,
                "artifact status does not match recorded trees")
        require(observation_value["process_validation"]["status"] ==
                aggregate(observation_value["process_validation"]["checks"]),
                "process status does not match derived checks")
        for worker in observation_value["identity_contract_validation"]["workers"]:
            require(worker["status"] == aggregate(list(worker["dimensions"].values())),
                    "worker identity status does not match dimensions")
        require(observation_value["identity_contract_validation"]["status"] ==
                aggregate(observation_value["identity_contract_validation"]["workers"]),
                "identity status does not match worker evidence")
        for source in evidence["session_sources"]:
            path = Path(source["path"])
            require(path.is_file() and sha256(path) == source["sha256"], f"session source mismatch: {path}")
    for activation in value["activation_gate"]["evidence"].values():
        activation_path = root / activation["path"]
        require(activation_path.is_file() and sha256(activation_path) == activation["sha256"],
                f"activation evidence mismatch: {activation_path}")
    require(value["activation_gate"]["status"] == aggregate(value["activation_gate"]["checks"]),
            "activation status does not match derived checks")
    expected = validate_acceptance(value)
    return {"validated_live_records": len(value["observations"]), **expected}


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
                result = collect(root, args.repo_root.resolve(), args.sessions_root.resolve())
                destination = args.output.resolve() if args.output else root / "live-results-v2.json"
            else:
                result = reprocess(root, args.repo_root.resolve(), args.original.resolve())
                destination = args.output.resolve()
            require(not destination.exists(), f"refusing to overwrite evidence report: {destination}")
            destination.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            output = {"results": str(destination), **validate(destination)}
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
