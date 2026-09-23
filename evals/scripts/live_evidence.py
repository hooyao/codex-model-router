#!/usr/bin/env python3
"""Collect and validate observed Codex router-scenario evidence.

This intentionally does not turn the deterministic synthetic runner into a
model runner.  It derives a compact, hash-bound live record from Codex CLI
JSONL transcripts, persisted parent/child rollouts, and exact fixture trees.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional


CASES = (
    "investigation-reuse",
    "serial-escalation",
    "parallel-disjoint",
    "architecture-review",
)
IGNORED_WORKSPACE_NAMES = {".git", ".codex-model-router"}


class LiveEvidenceError(ValueError):
    """Observed evidence is missing or internally inconsistent."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise LiveEvidenceError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def nested_text(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from nested_text(item)
    elif isinstance(value, list):
        for item in value:
            yield from nested_text(item)


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


def session_meta(path: Path) -> Optional[dict[str, Any]]:
    try:
        first = path.open("r", encoding="utf-8", errors="replace").readline()
        value = json.loads(first)
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
        own_id = meta.get("id")
        parent_id = meta.get("parent_thread_id")
        if own_id in found:
            found[own_id].append((path, meta))
        if parent_id in found:
            found[parent_id].append((path, meta))
    return found


def extract_parent_id(transcript: Path) -> str:
    for _line, value in json_lines(transcript):
        if value.get("type") == "thread.started" and isinstance(value.get("thread_id"), str):
            return value["thread_id"]
    raise LiveEvidenceError(f"no thread.started event in {transcript}")


def turn_context(items: list[tuple[int, dict[str, Any]]]) -> dict[str, Any]:
    for _line, value in items:
        if value.get("type") == "turn_context" and isinstance(value.get("payload"), dict):
            return value["payload"]
    return {}


def completion(items: list[tuple[int, dict[str, Any]]]) -> tuple[Optional[str], Optional[int]]:
    for _line, value in reversed(items):
        payload = value.get("payload")
        if value.get("type") == "event_msg" and isinstance(payload, dict) and payload.get("type") == "task_complete":
            return value.get("timestamp"), payload.get("duration_ms")
    return None, None


def identity_fields(text: str) -> dict[str, Optional[str]]:
    result = {}
    for field in ("Worker name", "Task ID", "Native task name"):
        match = re.search(rf"(?m)^{re.escape(field)}:\s*([^\r\n]+)", text)
        result[field.lower().replace(" ", "_")] = match.group(1).strip() if match else None
    return result


def session_record(path: Path, meta: dict[str, Any]) -> dict[str, Any]:
    items = json_lines(path)
    text = "\n".join(nested_text([value for _line, value in items]))
    context = turn_context(items)
    completed_at, duration_ms = completion(items)
    evidence_lines = sorted({
        line for line, value in items
        if any(marker in "\n".join(nested_text(value)) for marker in (
            "Worker name:", "Task ID:", "Native task name:", "spawn_agent", "task_complete"
        ))
    })
    source = meta.get("source")
    agent_path = meta.get("agent_path")
    if not agent_path and isinstance(source, dict):
        agent_path = source.get("subagent", {}).get("thread_spawn", {}).get("agent_path")
    start = meta.get("timestamp")
    return {
        "thread_id": meta.get("id"),
        "parent_thread_id": meta.get("parent_thread_id"),
        "agent_path": agent_path,
        "start_at": start,
        "end_at": completed_at,
        "duration_ms": duration_ms,
        "model": context.get("model"),
        "reasoning_effort": context.get("effort"),
        "identity": identity_fields(text),
        "source_path": str(path.resolve()),
        "source_sha256": sha256(path),
        "source_evidence_lines": evidence_lines or [1],
    }


def transcript_observations(path: Path) -> dict[str, Any]:
    items = json_lines(path)
    messages = [value["item"].get("text", "") for _line, value in items
                if value.get("type") == "item.completed" and isinstance(value.get("item"), dict)
                and value["item"].get("type") == "agent_message"]
    route_lines = []
    for message in messages:
        route_lines.extend(line.strip() for line in message.splitlines() if line.strip().startswith("ROUTE:"))
    usage = None
    for _line, value in reversed(items):
        if value.get("type") == "turn.completed" and isinstance(value.get("usage"), dict):
            usage = value["usage"]
            break
    failed_tools = sum(
        value.get("type") == "item.completed"
        and isinstance(value.get("item"), dict)
        and value["item"].get("status") == "failed"
        for _line, value in items
    )
    return {"route_lines": route_lines, "usage": usage, "failed_tool_events": failed_tools,
            "raw_log_bytes": path.stat().st_size, "sha256": sha256(path)}


def interval(session: dict[str, Any]) -> tuple[datetime, datetime]:
    require(session["start_at"] is not None and session["end_at"] is not None,
            f"session interval incomplete: {session['thread_id']}")
    return parse_iso(session["start_at"]), parse_iso(session["end_at"])


def overlap_ms(sessions: list[dict[str, Any]]) -> int:
    starts, ends = zip(*(interval(session) for session in sessions))
    return max(0, round((min(ends) - max(starts)).total_seconds() * 1000))


def ordered_without_overlap(sessions: list[dict[str, Any]]) -> bool:
    ordered = sorted(sessions, key=lambda session: interval(session)[0])
    return all(interval(left)[1] <= interval(right)[0] for left, right in zip(ordered, ordered[1:]))


def route_contains(routes: list[str], *parts: str) -> bool:
    return any(all(part in route for part in parts) for route in routes)


def process_checks(case_id: str, routes: list[str], children: list[dict[str, Any]], session_text: str) -> list[dict[str, Any]]:
    paths = [session.get("agent_path") or "" for session in children]
    if case_id == "investigation-reuse":
        values = {
            "route": route_contains(routes, "DELEGATE", "ISOLATED_SERIAL"),
            "two-distinct-workers": len(children) >= 2 and len({item["thread_id"] for item in children}) == len(children),
            "receipt-reuse": "docs/ops.md" in session_text and "45" in session_text
                             and bool(re.search(r"[0-9a-f]{64}", session_text)),
            "stage-ownership": any("investigat" in path for path in paths) and any("implement" in path for path in paths),
        }
    elif case_id == "serial-escalation":
        values = {
            "direct-then-delegate": bool(routes) and routes[0].startswith("ROUTE: DIRECT")
                                    and route_contains(routes[1:], "DELEGATE", "ISOLATED_SERIAL"),
            "scope-expanded": "scope-expanded" in session_text,
            "four-stages": all(any(name in path for path in paths)
                               for name in ("plan", "schema", "api", "contract")),
            "serial-timing": len(children) >= 4 and ordered_without_overlap(children),
        }
    elif case_id == "parallel-disjoint":
        implementation = [session for session in children
                          if any(name in (session.get("agent_path") or "") for name in ("alpha", "beta", "gamma"))]
        implementation = list({session["thread_id"]: session for session in implementation}.values())
        values = {
            "route": route_contains(routes, "DELEGATE", "PARALLEL"),
            "three-workers": len(implementation) == 3,
            "real-overlap": len(implementation) == 3 and overlap_ms(implementation) > 0,
            "disjoint-ownership": all(f"reports/{name}.md" in session_text for name in ("alpha", "beta", "gamma")),
        }
    else:
        authors = [item for item in children if "author" in (item.get("agent_path") or "")]
        reviewers = [item for item in children if "review" in (item.get("agent_path") or "")]
        distinct = bool(authors and reviewers and authors[0]["thread_id"] != reviewers[0]["thread_id"])
        serial = distinct and interval(authors[0])[1] <= interval(reviewers[0])[0]
        values = {
            "route": route_contains(routes, "DELEGATE", "ISOLATED_SERIAL"),
            "independent-review-required": "INDEPENDENT_REVIEW" in session_text,
            "distinct-author-reviewer": distinct,
            "review-after-author": serial,
            "review-pass": bool(reviewers) and "PASS" in session_text,
        }
    return [{"name": name, "passed": passed} for name, passed in values.items()]


def identity_checks(children: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks = []
    for child in children:
        identity = child["identity"]
        worker = identity["worker_name"]
        task = identity["task_id"]
        native = identity["native_task_name"]
        model = re.sub(r"[^a-z0-9]+", "-", (child.get("model") or "").lower()).strip("-")
        effort = re.sub(r"[^a-z0-9]+", "-", (child.get("reasoning_effort") or "").lower()).strip("-")
        expected_suffix = f"-{model}-{effort}" if model and effort else None
        valid = bool(worker and task and worker == task and "unavailable" not in worker
                     and "unexposed" not in worker and expected_suffix
                     and worker.endswith(expected_suffix)
                     and native == worker.replace("-", "_"))
        checks.append({"thread_id": child["thread_id"], "worker_name": worker,
                       "task_id": task, "native_task_name": native, "passed": valid})
    return checks


def collect(root: Path, repo_root: Path, sessions_root: Path) -> dict[str, Any]:
    transcripts = {
        case_id: (root / "raw" / f"{case_id}-rerun.jsonl"
                  if (root / "raw" / f"{case_id}-rerun.jsonl").is_file()
                  else root / "raw" / f"{case_id}.jsonl")
        for case_id in CASES
    }
    for path in transcripts.values():
        require(path.is_file(), f"missing live transcript: {path}")
    parent_ids = {case_id: extract_parent_id(path) for case_id, path in transcripts.items()}
    catalog = session_catalog(sessions_root, set(parent_ids.values()))
    observations = []
    session_index_root = root / "session-evidence"
    result_root = root / "results"
    for case_id in CASES:
        parent_id = parent_ids[case_id]
        matched = catalog[parent_id]
        parents = [(path, meta) for path, meta in matched if meta.get("id") == parent_id]
        children = [(path, meta) for path, meta in matched if meta.get("parent_thread_id") == parent_id]
        require(len(parents) == 1, f"expected one persisted parent for {case_id}, found {len(parents)}")
        require(children, f"no persisted children for {case_id}")
        parent = session_record(*parents[0])
        child_records = sorted((session_record(path, meta) for path, meta in children),
                               key=lambda session: session["start_at"] or "")
        all_text = "\n".join(path.read_text(encoding="utf-8", errors="replace") for path, _meta in matched)
        transcript = transcript_observations(transcripts[case_id])
        rerun_workspace = root / "workspaces" / f"{case_id}-rerun"
        workspace = rerun_workspace if transcripts[case_id].stem.endswith("-rerun") else root / "workspaces" / case_id
        reference = repo_root / "evals" / "fixtures" / case_id / "reference"
        actual_tree = snapshot(workspace)
        expected_tree = snapshot(reference)
        result_path = result_root / case_id
        copy_business_tree(workspace, result_path)
        artifact_pass = actual_tree == expected_tree
        checks = process_checks(case_id, transcript["route_lines"], child_records, all_text)
        identity = identity_checks(child_records)
        session_index = {"parent": parent, "children": child_records}
        index_path = session_index_root / case_id / "session-index.json"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(json.dumps(session_index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        observations.append({
            "run_id": f"{root.name}-{case_id}-selective-1",
            "case_id": case_id,
            "treatment": "selective",
            "outcome": "completed" if artifact_pass else "failed",
            "parent_session": parent,
            "worker_sessions": child_records,
            "route_events": transcript["route_lines"],
            "timing": {
                "parent_duration_ms": parent["duration_ms"],
                "all_worker_overlap_ms": overlap_ms(child_records) if len(child_records) > 1 else 0,
            },
            "usage": {"kind": "observed", "scope": "codex-exec-terminal-event",
                      "tokens": transcript["usage"]},
            "cost": {"kind": "unavailable", "complete": False, "usd": None,
                     "reason": "runtime emitted tokens but no measured USD billing"},
            "tool_failures": transcript["failed_tool_events"],
            "raw_log_bytes": transcript["raw_log_bytes"],
            "artifact_validation": {
                "passed": artifact_pass,
                "actual_tree": actual_tree,
                "expected_tree": expected_tree,
                "result_path": str(result_path.relative_to(root).as_posix()),
            },
            "process_validation": {"passed": all(item["passed"] for item in checks), "checks": checks},
            "identity_contract_validation": {
                "passed": all(item["passed"] for item in identity), "workers": identity,
            },
            "evidence": {
                "transcript": {"path": str(transcripts[case_id].relative_to(root).as_posix()),
                               "sha256": transcript["sha256"]},
                "session_index": {"path": str(index_path.relative_to(root).as_posix()),
                                  "sha256": sha256(index_path)},
            },
        })
    activation = root / "raw" / "activation.jsonl"
    activation_text = activation.read_text(encoding="utf-8", errors="replace")
    activation_gate = {
        "passed": "ROUTE: DIRECT" in activation_text
                  and (root / "raw" / "activation-routing.json").is_file(),
        "transcript": {"path": str(activation.relative_to(root).as_posix()), "sha256": sha256(activation)},
        "observed_failures": [
            "plugin hook manifest ignored by CLI",
            "no automatic routing config or pre-action route event",
            "workspace-write Windows sandbox CreateProcessWithLogonW failed",
        ],
    }
    all_artifacts = all(item["artifact_validation"]["passed"] for item in observations)
    all_process = all(item["process_validation"]["passed"] for item in observations)
    all_identity = all(item["identity_contract_validation"]["passed"] for item in observations)
    return {
        "schema_version": 1,
        "campaign_id": root.name,
        "data_origin": "observed",
        "treatment": "selective",
        "runtime": {"codex_cli": (root / "codex-version.txt").read_text(encoding="utf-8").strip(),
                    "controller_model": "gpt-5.6-sol", "controller_reasoning_effort": "high"},
        "activation_gate": activation_gate,
        "observations": observations,
        "acceptance": {
            "scheduled_runs": len(CASES), "completed_runs": sum(item["outcome"] == "completed" for item in observations),
            "artifact_pass": all_artifacts, "required_route_process_pass": all_process,
            "identity_contract_pass": all_identity,
            "campaign_pass": activation_gate["passed"] and all_artifacts and all_process and all_identity,
        },
        "claim_boundary": {
            "measured_usd_available": False,
            "automatic_hook_compliance_established": activation_gate["passed"],
            "context_deletion_or_sandboxing_established": False,
            "comparative_cost_or_latency_claim_supported": False,
        },
    }


def validate(results_path: Path) -> dict[str, Any]:
    root = results_path.parent
    value = json.loads(results_path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("data_origin") == "observed",
            "not an observed live-results-v1 artifact")
    observations = value.get("observations")
    require(isinstance(observations, list) and len(observations) == len(CASES),
            "live campaign must retain all required scenarios")
    for observation in observations:
        evidence = observation["evidence"]
        for item in evidence.values():
            path = root / item["path"]
            require(path.is_file() and sha256(path) == item["sha256"], f"evidence hash mismatch: {path}")
        require(observation["outcome"] in ("completed", "failed"), "terminal outcome missing")
        require(observation["cost"]["kind"] == "unavailable" and observation["cost"]["usd"] is None,
                "live campaign must not invent USD cost")
        for session in [observation["parent_session"], *observation["worker_sessions"]]:
            path = Path(session["source_path"])
            require(path.is_file() and sha256(path) == session["source_sha256"],
                    f"persisted session hash mismatch: {path}")
    return {"validated_live_records": len(observations), "campaign_pass": value["acceptance"]["campaign_pass"],
            "artifact_pass": value["acceptance"]["artifact_pass"],
            "required_route_process_pass": value["acceptance"]["required_route_process_pass"],
            "identity_contract_pass": value["acceptance"]["identity_contract_pass"],
            "activation_gate_pass": value["activation_gate"]["passed"]}


def write_summary(root: Path, result: dict[str, Any]) -> None:
    lines = [
        f"# Live router scenario campaign: {result['campaign_id']}", "",
        "This is observed Codex runtime evidence, not the synthetic comparison campaign.", "",
        f"- Required scenarios completed: {result['acceptance']['completed_runs']}/{result['acceptance']['scheduled_runs']}",
        f"- Exact business trees: {'PASS' if result['acceptance']['artifact_pass'] else 'FAIL'}",
        f"- Required route/process evidence: {'PASS' if result['acceptance']['required_route_process_pass'] else 'FAIL'}",
        f"- Worker identity contract: {'PASS' if result['acceptance']['identity_contract_pass'] else 'FAIL'}",
        f"- Automatic hook activation: {'PASS' if result['activation_gate']['passed'] else 'FAIL'}",
        f"- Overall campaign acceptance: {'PASS' if result['acceptance']['campaign_pass'] else 'FAIL'}", "",
        "| Case | Artifact | Route/process | Identity contract | Parent | Workers |", "|---|---:|---:|---:|---|---:|",
    ]
    for item in result["observations"]:
        lines.append(
            f"| {item['case_id']} | {'PASS' if item['artifact_validation']['passed'] else 'FAIL'} "
            f"| {'PASS' if item['process_validation']['passed'] else 'FAIL'} "
            f"| {'PASS' if item['identity_contract_validation']['passed'] else 'FAIL'} "
            f"| `{item['parent_session']['thread_id']}` | {len(item['worker_sessions'])} |"
        )
    lines.extend([
        "", "## Claim boundary", "",
        "USD cost is unavailable because the runtime emitted token telemetry but no measured billing. "
        "The failed activation gate means this campaign validates only the explicit-skill dispatcher path. "
        "It does not establish automatic hook compliance, technical context deletion, sandbox isolation, "
        "or comparative cost/latency improvements.", "",
        "See `live-results.json`, `raw/*.jsonl`, and `session-evidence/*/session-index.json` for hash-bound evidence.",
    ])
    (root / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect_parser = subparsers.add_parser("collect")
    collect_parser.add_argument("--root", type=Path, required=True)
    collect_parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    collect_parser.add_argument("--sessions-root", type=Path, required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "collect":
            root = args.root.resolve()
            result = collect(root, args.repo_root.resolve(), args.sessions_root.resolve())
            results_path = root / "live-results.json"
            results_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            write_summary(root, result)
            output = validate(results_path)
            output.update({"results": str(results_path), "summary": str(root / "SUMMARY.md")})
        else:
            output = validate(args.results.resolve())
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"ERROR: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
