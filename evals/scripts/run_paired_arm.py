"""Run one isolated Codex benchmark arm with a live API-price safety stop.

This is an internal paired comparison runner, not the official Harbor harness.
It retains the CLI transcript locally and prices every observed parent/child
model response. The safety stop is necessarily approximate because token usage
arrives only after each response, not before it is billed.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

try:
    from .dispatch_audit_reconcile import native_event_matches
except ImportError:  # Direct script execution.
    from dispatch_audit_reconcile import native_event_matches


RATES = {
    "gpt-6-astra": (10.0, 1.0, 12.5, 50.0),
    "gpt-5.6-sol": (4.0, 0.4, 5.0, 20.0),
    "gpt-6-sol": (2.0, 0.2, 2.5, 10.0),
    "gpt-6-luna": (0.1, 0.01, 0.125, 0.5),
    "gpt-5.6-terra": (2.0, 0.2, 2.5, 12.0),
    "gpt-5.6-luna": (0.2, 0.02, 0.25, 1.2),
}
LONG_CONTEXT_THRESHOLD = 272_000
USAGE_KEYS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
              "output_tokens", "reasoning_output_tokens")


def response_cost_interval(usage: dict, model: str) -> tuple[float, float]:
    if any(key not in usage for key in USAGE_KEYS if key != "cache_write_input_tokens"):
        raise ValueError(f"missing token class for {model}: {usage}")
    input_tokens = int(usage["input_tokens"])
    cached = int(usage["cached_input_tokens"])
    cache_write = (int(usage["cache_write_input_tokens"])
                   if "cache_write_input_tokens" in usage else None)
    output = int(usage["output_tokens"])
    reasoning = int(usage["reasoning_output_tokens"])
    uncached = input_tokens - cached
    if min(uncached, cached, output, reasoning) < 0 or reasoning > output or (
            cache_write is not None and not 0 <= cache_write <= uncached):
        raise ValueError(f"invalid usage classes for {model}: {usage}")
    rates = RATES[model]
    if input_tokens > LONG_CONTEXT_THRESHOLD:
        rates = tuple(rate * (1.5 if index == 3 else 2) for index, rate in enumerate(rates))
    def priced(writes: int) -> float:
        return ((uncached - writes) * rates[0] + cached * rates[1] +
                writes * rates[2] + output * rates[3]) / 1_000_000
    if cache_write is not None:
        exact = priced(cache_write)
        return exact, exact
    return priced(0), priced(uncached)


def response_cost(usage: dict, model: str) -> float:
    if "cache_write_input_tokens" not in usage:
        raise ValueError(f"missing token class for {model}: {usage}")
    return response_cost_interval(usage, model)[0]


def budget_limit_reached(meter: "SessionMeter", budget_usd: float,
                         stop_margin_usd: float) -> bool:
    return meter.cost_upper >= budget_usd - stop_margin_usd


class SessionMeter:
    def __init__(self, root: Path, parent_id: str, parent_model: str,
                 parent_effort: str | None = None, require_native_activity: bool = False):
        self.root = root
        self.parent_id = parent_id
        self.known_ids = {parent_id}
        self.paths: dict[Path, dict] = {}
        self.parent_model = parent_model
        self.parent_effort = parent_effort
        self.require_native_activity = require_native_activity
        self.cost = 0.0
        self.cost_upper = 0.0
        self.calls = 0
        self.tokens = {key: 0 for key in (
            "input_tokens", "cached_input_tokens", "cache_write_input_tokens",
            "output_tokens", "reasoning_output_tokens")}
        self.unknown_models: set[str] = set()
        self.unknown_usage: list[str] = []
        self.long_context_calls = 0

    def discover(self) -> None:
        # Child sessions may be created after the parent starts. Iterate once
        # more after discovering a child so grandchildren are not omitted.
        for _ in range(3):
            changed = False
            for path in self.root.rglob("rollout-*.jsonl"):
                if path in self.paths:
                    continue
                try:
                    with path.open(encoding="utf-8") as stream:
                        event = json.loads(stream.readline())
                    meta = event["payload"]
                    session_id = meta["id"]
                except (OSError, ValueError, KeyError):
                    continue
                if session_id != self.parent_id and meta.get("parent_thread_id") not in self.known_ids:
                    continue
                self.paths[path] = {
                    "id": session_id, "parent_id": meta.get("parent_thread_id"), "offset": 0,
                    "agent_path": meta.get("agent_path"),
                    "model": self.parent_model if session_id == self.parent_id else None,
                    "effort": self.parent_effort if session_id == self.parent_id else None,
                    "calls": 0, "cost": 0.0, "cost_upper": 0.0,
                    "tokens": {key: 0 for key in USAGE_KEYS},
                    "responses": [],
                    "terminal": None, "last_total_usage": None,
                    "last_response_usage": None, "turns": [],
                }
                self.known_ids.add(session_id)
                changed = True
            if not changed:
                break

    def refresh(self) -> None:
        self.discover()
        for path, state in self.paths.items():
            try:
                with path.open(encoding="utf-8") as stream:
                    stream.seek(state["offset"])
                    while True:
                        start = stream.tell()
                        line = stream.readline()
                        if not line:
                            break
                        if not line.endswith("\n"):
                            stream.seek(start)
                            break
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue
                        kind = event.get("type")
                        payload = event.get("payload") or {}
                        if kind == "turn_context" and payload.get("model"):
                            state["model"] = payload["model"]
                            state["effort"] = payload.get("effort")
                            state["turns"].append({"turn_id": payload.get("turn_id"),
                                                   "model": state["model"],
                                                   "effort": state["effort"]})
                        if kind == "event_msg" and payload.get("type") in (
                                "task_complete", "task_completed", "task_failed",
                                "task_cancelled", "task_cancel"):
                            state["terminal"] = payload["type"]
                        if kind != "event_msg" or payload.get("type") != "token_count":
                            continue
                        info = payload.get("info") or {}
                        total = info.get("total_token_usage")
                        usage = info.get("last_token_usage")
                        if not usage:
                            if total:
                                self.unknown_usage.append(
                                    f"{state['id']}: cumulative usage without response usage")
                            continue
                        previous = state["last_total_usage"]
                        if not isinstance(total, dict):
                            self.unknown_usage.append(
                                f"{state['id']}: response lacks cumulative usage")
                        else:
                            comparable = set(USAGE_KEYS) - {"cache_write_input_tokens"}
                            if ("cache_write_input_tokens" in total and
                                    "cache_write_input_tokens" in usage):
                                comparable.add("cache_write_input_tokens")
                            if "total_tokens" in total and "total_tokens" in usage:
                                comparable.add("total_tokens")
                            if (not all(type(total.get(key)) is int and type(usage.get(key)) is int
                                        for key in comparable) or
                                    (previous is not None and not all(
                                        type(previous.get(key)) is int for key in comparable)) or
                                    any(item.get("total_tokens") != item.get("input_tokens", 0) +
                                        item.get("output_tokens", 0)
                                        for item in (total, usage) if "total_tokens" in item)):
                                self.unknown_usage.append(
                                    f"{state['id']}: cumulative usage classes malformed")
                            elif previous is not None and all(
                                    total[key] == previous[key] for key in comparable):
                                if (total != previous or
                                        usage != state["last_response_usage"]):
                                    self.unknown_usage.append(
                                        f"{state['id']}: unchanged cumulative usage has conflicting response")
                                continue  # Repeated snapshot, not another billed response.
                            elif any(total[key] - (previous[key] if previous else 0) != usage[key]
                                     for key in comparable):
                                self.unknown_usage.append(
                                    f"{state['id']}: response and cumulative usage disagree")
                            state["last_total_usage"] = total
                            state["last_response_usage"] = usage
                        model = state["model"]
                        if model not in RATES:
                            self.unknown_models.add(str(model))
                            continue
                        try:
                            cost, cost_upper = response_cost_interval(usage, model)
                        except (ValueError, TypeError) as error:
                            self.unknown_usage.append(f"{state['id']}: {error}")
                            continue
                        self.cost += cost
                        self.cost_upper += cost_upper
                        state["cost"] += cost
                        state["cost_upper"] += cost_upper
                        self.calls += 1
                        state["calls"] += 1
                        state["responses"].append({
                            "model": model, "effort": state["effort"],
                            "token_classes": {key: usage.get(key) for key in USAGE_KEYS},
                            "estimated_usd": round(cost, 9) if cost == cost_upper else None,
                            "estimated_usd_lower_bound": round(cost, 9),
                            "estimated_usd_upper_bound": round(cost_upper, 9)})
                        if int(usage["input_tokens"]) > LONG_CONTEXT_THRESHOLD:
                            self.long_context_calls += 1
                        for key in self.tokens:
                            if key not in usage:
                                self.tokens[key] = None
                                state["tokens"][key] = None
                                continue
                            value = int(usage[key])
                            if self.tokens[key] is not None:
                                self.tokens[key] += value
                            if state["tokens"][key] is not None:
                                state["tokens"][key] += value
                    state["offset"] = stream.tell()
            except OSError:
                continue

    def summary(self) -> dict:
        return {
            "estimated_usd": round(self.cost, 9) if self.cost == self.cost_upper else None,
            "estimated_usd_lower_bound": round(self.cost, 9),
            "estimated_usd_upper_bound": round(self.cost_upper, 9),
            "model_calls": self.calls,
            "long_context_calls": self.long_context_calls,
            "token_classes": self.tokens,
            "unknown_models": sorted(self.unknown_models),
            "unknown_usage": self.unknown_usage,
            "sessions": [{"id": state["id"], "parent_id": state["parent_id"],
                          "agent_path": state["agent_path"],
                          "model": state["model"], "effort": state["effort"],
                          "turns": state["turns"],
                          "terminal": state["terminal"], "calls": state["calls"],
                          "responses": state["responses"],
                          "token_classes": state["tokens"],
                          "reported_total_usage": state["last_total_usage"],
                          "estimated_usd": (round(state["cost"], 9) if
                                            state["cost"] == state["cost_upper"] else None),
                          "estimated_usd_lower_bound": round(state["cost"], 9),
                          "estimated_usd_upper_bound": round(state["cost_upper"], 9)}
                         for state in self.paths.values()],
        }

    def integrity_issues(self) -> list[str]:
        issues = []
        if not self.paths:
            issues.append("no session rollout was discovered")
        if self.calls == 0:
            issues.append("no priced model response was observed")
        if self.unknown_models or self.unknown_usage:
            issues.append("unpriced model response or token class")
        for state in self.paths.values():
            if state["terminal"] not in ("task_complete", "task_completed", "task_failed",
                                         "task_cancelled", "task_cancel"):
                issues.append(f"session {state['id']} lacks terminal result")
            elif state["id"] == self.parent_id and state["terminal"] not in (
                    "task_complete", "task_completed"):
                issues.append("parent session lacks successful terminal result")
            if state["calls"] == 0:
                issues.append(f"session {state['id']} has no priced response")
            if not state["turns"] or any(not turn["model"] or not turn["effort"]
                                           for turn in state["turns"]):
                issues.append(f"session {state['id']} lacks per-turn model/effort")
            reported = state["last_total_usage"]
            if not isinstance(reported, dict):
                issues.append(f"session {state['id']} lacks final usage totals")
                continue
            for key, actual in state["tokens"].items():
                if key == "cache_write_input_tokens" and key not in reported:
                    continue
                if actual is None:
                    if key in reported:
                        issues.append(f"session {state['id']} {key} total cannot be reconciled")
                elif reported.get(key) != actual:
                    issues.append(f"session {state['id']} {key} total mismatch")
        issues.extend(self.dispatch_coverage_issues())
        if self.require_native_activity:
            parent_paths = [path for path, state in self.paths.items() if state["id"] == self.parent_id]
            if len(parent_paths) == 1:
                _, lineage_issues = native_lineage(parent_paths[0], self.paths, self.parent_id)
                issues.extend(lineage_issues)
        return issues

    def dispatch_coverage_issues(self) -> list[str]:
        issues = []
        parent_paths = [path for path, state in self.paths.items() if state["id"] == self.parent_id]
        if len(parent_paths) != 1:
            issues.append("parent rollout missing or duplicated")
        else:
            dispatches, parse_issues = native_dispatches(parent_paths[0])
            issues.extend(parse_issues)
            observed = Counter((state["agent_path"] or "").split("/")[-1]
                               for state in self.paths.values() if state["id"] != self.parent_id)
            if len(dispatches) != sum(observed.values()):
                issues.append("dispatched child count differs from discovered sessions")
            for name, count in Counter(dispatches).items():
                if name and observed[name] != count:
                    issues.append(f"dispatched child session missing or duplicated: {name}")
        return issues


def native_dispatches(parent_path: Path) -> tuple[list[str | None], list[str]]:
    """Read observable spawn calls from the parent rollout, including retries."""
    names: list[str | None] = []
    issues: list[str] = []
    try:
        with parent_path.open(encoding="utf-8") as source:
            for line in source:
                try:
                    event = json.loads(line)
                except ValueError:
                    issues.append("parent rollout has unreadable event")
                    continue
                payload = event.get("payload") or {}
                if (event.get("type") != "response_item" or
                        payload.get("type") != "function_call" or
                        payload.get("name") != "spawn_agent"):
                    continue
                try:
                    arguments = json.loads(payload["arguments"])
                    names.append(arguments.get("task_name"))
                except (KeyError, TypeError, ValueError):
                    names.append(None)
                    issues.append("native spawn arguments are unreadable")
    except OSError:
        issues.append("parent rollout cannot be read for native dispatches")
    return names, issues


def native_lineage(parent_path: Path, paths: dict[Path, dict],
                   parent_id: str) -> tuple[list[dict], list[str]]:
    """Join v2 calls and activities to their original parent turn and child."""
    calls: dict[str, dict] = {}
    issues: list[str] = []
    current_session_id: str | None = parent_id
    current_turn_id: str | None = None
    completion_ids: set[str] = set()
    with parent_path.open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError("native lineage event is not an object")
                payload = event.get("payload") or {}
                if not isinstance(payload, dict):
                    raise ValueError("native lineage payload is not an object")
                if event.get("type") == "session_meta":
                    current_session_id = payload.get("id")
                    if current_session_id != parent_id:
                        issues.append("native parent session context differs from expected parent")
                elif event.get("type") == "turn_context":
                    current_turn_id = payload.get("turn_id") or event.get("turn_id")
                if event.get("type") == "response_item" and payload.get("type") == "function_call" \
                        and payload.get("name") == "spawn_agent":
                    call_id = payload.get("call_id")
                    arguments = json.loads(payload["arguments"])
                    if not isinstance(call_id, str) or not call_id or call_id in calls:
                        issues.append("native spawn call ID missing or duplicated")
                        continue
                    if not isinstance(arguments, dict):
                        issues.append("native spawn arguments malformed")
                        continue
                    if payload.get("namespace") not in (None, "collaboration"):
                        issues.append("native spawn namespace differs from collaboration")
                    if not native_event_matches(event, payload, parent_id, current_turn_id,
                                                current_session_id, current_turn_id):
                        issues.append("native spawn identity differs from parent context")
                    calls[call_id] = {"call_id": call_id, "call_line": line_no,
                                      "task_name": arguments.get("task_name"),
                                      "model": arguments.get("model"),
                                      "effort": arguments.get("reasoning_effort"),
                                      "fork_turns": arguments.get("fork_turns"),
                                      "session_id": parent_id, "turn_id": current_turn_id}
                elif event.get("type") == "event_msg":
                    item = (payload.get("item") if payload.get("type") == "item_completed"
                            else payload if payload.get("type") == "sub_agent_activity" else None)
                    if not isinstance(item, dict) or item.get("type") not in (
                            "SubAgentActivity", "sub_agent_activity"):
                        continue
                    activity_id = item.get("id") or item.get("event_id")
                    child_id = item.get("agent_thread_id")
                    agent_path = item.get("agent_path")
                    if (not isinstance(activity_id, str) or not activity_id or
                            not isinstance(child_id, str) or not child_id or
                            not isinstance(agent_path, str) or not agent_path):
                        issues.append("native subagent activity identity malformed")
                        continue
                    if item.get("kind") == "started":
                        call = calls.get(activity_id)
                        if call is None or "start_line" in call or any(
                                row.get("child_id") == child_id for row in calls.values()):
                            issues.append("native child start lacks unique preceding spawn call")
                            continue
                        if not native_event_matches(event, payload, call["session_id"],
                                                    call["turn_id"], current_session_id,
                                                    current_turn_id, item):
                            issues.append("native child start differs from spawn context")
                            continue
                        call.update({"start_line": line_no, "child_id": child_id,
                                     "agent_path": agent_path})
                    elif item.get("kind") == "completed":
                        starts = [call for call in calls.values() if
                                  call.get("child_id") == child_id and
                                  call.get("agent_path") == agent_path and "start_line" in call]
                        if (len(starts) != 1 or activity_id in calls or
                                activity_id in completion_ids or
                                "completion_line" in starts[0]):
                            issues.append("native child completion lacks unique start")
                            continue
                        call = starts[0]
                        if not native_event_matches(event, payload, call["session_id"],
                                                    call["turn_id"], current_session_id,
                                                    current_turn_id, item):
                            issues.append("native child completion differs from spawn context")
                            continue
                        completion_ids.add(activity_id)
                        call["completion_line"] = line_no
                    elif item.get("kind") == "interacted":
                        starts = [call for call in calls.values() if
                                  call.get("child_id") == child_id and
                                  call.get("agent_path") == agent_path and
                                  "start_line" in call and "completion_line" not in call]
                        if (len(starts) != 1 or activity_id in calls or
                                not native_event_matches(event, payload, parent_id,
                                                         current_turn_id, current_session_id,
                                                         current_turn_id, item)):
                            issues.append("native child interaction lacks active start or parent context")
                    else:
                        issues.append("native subagent activity kind unsupported")
                elif event.get("type") == "response_item" and payload.get("type") == "function_call_output":
                    call = calls.get(payload.get("call_id"))
                    if call is None:
                        continue
                    if "result_line" in call:
                        issues.append("native spawn result duplicated")
                        continue
                    result = json.loads(payload["output"])
                    if not isinstance(result, dict) or set(result) != {"task_name"}:
                        issues.append("native spawn result malformed")
                        continue
                    call.update({"result_line": line_no, "result_path": result["task_name"]})
            except (TypeError, KeyError, ValueError):
                issues.append("native lineage event malformed")
    children = {state["id"]: state for state in paths.values()
                if state["id"] != parent_id and state.get("parent_id") == parent_id}
    for call in calls.values():
        name = call.get("task_name")
        child = children.get(call.get("child_id"))
        if (not isinstance(name, str) or not call.get("start_line") or
                not call.get("result_line") or
                not call["call_line"] < call["start_line"] < call["result_line"] or
                not isinstance(call.get("agent_path"), str) or
                call["agent_path"] != "/root/" + name or
                call.get("result_path") != call["agent_path"] or child is None or
                child.get("agent_path") != call["agent_path"] or
                not child.get("turns") or
                any((turn.get("model"), turn.get("effort")) !=
                    (call.get("model"), call.get("effort")) for turn in child["turns"])):
            issues.append("native call/start/result/child selectors do not join")
    if len(calls) != len(children):
        issues.append("native child lineage count differs from discovered sessions")
    return list(calls.values()), issues


def telemetry_unavailable(meter: SessionMeter | None, elapsed_seconds: float,
                          grace_seconds: float = 180) -> bool:
    return (elapsed_seconds > grace_seconds and
            (meter is None or not meter.paths or meter.calls == 0))


def parent_id_from_transcript(path: Path) -> str | None:
    try:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("type") == "thread.started":
                    return event.get("thread_id")
    except OSError:
        pass
    return None


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def capture_candidate_patch(workspace: Path, evidence: Path) -> tuple[dict | None, str | None]:
    add = subprocess.run(["git", "add", "-N", "--", ".", ":(exclude).codex-model-router"],
                         cwd=workspace, capture_output=True)
    if add.returncode:
        return None, add.stderr.decode(errors="replace").strip()
    diff = subprocess.run(["git", "diff", "--binary", "HEAD", "--", ".",
                           ":(exclude).codex-model-router"], cwd=workspace, capture_output=True)
    if diff.returncode:
        return None, diff.stderr.decode(errors="replace").strip()
    path = evidence / "candidate.patch"
    path.write_bytes(diff.stdout)
    return {"path": path.name, "sha256": sha256(path), "bytes": len(diff.stdout)}, None


def pinned_prompt(config_path: Path, config: dict) -> str:
    root = config_path.parent
    benchmark = config["benchmark"]
    prompt_spec = config["prompt"]
    for field, digest_field in (("source_layer", "source_layer_sha256"),
                                ("instruction", "instruction_sha256"),
                                ("task_spec", "task_spec_sha256"),
                                ("oracle_patch", "oracle_patch_sha256"),
                                ("oracle_config", "oracle_config_sha256")):
        path = root / benchmark[field]
        if not path.is_file() or sha256(path) != benchmark[digest_field]:
            raise ValueError(f"frozen benchmark asset mismatch: {field}")
    note = root / prompt_spec["environment_note"]
    if not note.is_file() or sha256(note) != prompt_spec["environment_note_sha256"]:
        raise ValueError("environment note mismatch")
    prompt = ((root / benchmark["instruction"]).read_text(encoding="utf-8").rstrip() +
              "\n\n" + note.read_text(encoding="utf-8").rstrip() + "\n")
    if hashlib.sha256(prompt.encode("utf-8")).hexdigest() != prompt_spec["combined_sha256"]:
        raise ValueError("combined prompt hash mismatch")
    return prompt


def treatment_authorization_suffix(config: dict) -> str:
    """Return the reviewed treatment user instruction or reject unknown versions."""
    execution = config["treatment_execution"]
    if (not isinstance(execution, dict) or
            execution.get("version") not in ("lifecycle-v1-dispatch-authorization-v3",
                                             "lifecycle-v1-selective-stage-plan-v4") or
            not isinstance(execution.get("authorization_suffix"), str) or
            not execution["authorization_suffix"].strip()):
        raise ValueError("unsupported treatment execution authorization")
    if execution["version"] == "lifecycle-v1-selective-stage-plan-v4" and (
            execution.get("authorization_suffix_sha256") != hashlib.sha256(
                execution["authorization_suffix"].encode("utf-8")).hexdigest()):
        raise ValueError("treatment execution authorization hash mismatch")
    return execution["authorization_suffix"].rstrip()


def submitted_prompt(config_path: Path, config: dict, arm: str) -> str:
    """Keep the frozen task intact while authorizing treatment dispatch explicitly."""
    task = pinned_prompt(config_path, config)
    if arm not in ("router", "treatment") or "treatment_execution" not in config:
        return task
    return task.rstrip("\n") + "\n\n" + treatment_authorization_suffix(config) + "\n"


CONTROLLER_CRITERIA = ("decomposition", "dependencies", "dispatch", "receipts",
                       "verification", "recovery")
SELECTOR_FEATURE = "features.multi_agent_v2={enabled=true,expose_spawn_agent_model_overrides=true}"


def pinned_cli(cli: Path, config: dict) -> dict:
    """Both arms must use the executable whose native schema was captured."""
    pin = config["dispatch_runtime"]
    if cli.resolve() != Path(pin["cli"]).resolve() or sha256(cli) != pin["cli_sha256"]:
        raise ValueError("paired CLI path or hash differs from reviewed executable")
    version = subprocess.check_output([str(cli), "--version"], text=True, timeout=10).strip()
    if version != pin["cli_version"]:
        raise ValueError("paired CLI version differs from reviewed executable")
    return {"path": str(cli.resolve()), "sha256": pin["cli_sha256"], "version": version}


def explicit_dispatch_preflight(config_path: Path, config: dict, cli: Path) -> dict:
    """Revalidate the zero-model capture and the ordinary explicit dispatch contract."""
    pin = config["dispatch_runtime"]
    root = config_path.resolve().parents[2]
    sources = {}
    for key in ("schema_capture", "spawn_schema", "model_catalog"):
        spec = pin[key]
        path = (root / spec["path"]).resolve()
        if not path.is_file() or sha256(path) != spec["sha256"]:
            raise ValueError(f"explicit dispatch {key} hash mismatch")
        sources[key] = (path, json.loads(path.read_text(encoding="utf-8")))
    capture = sources["schema_capture"][1]
    schema = sources["spawn_schema"][1]
    catalog = sources["model_catalog"][1]
    captures = capture.get("captures")
    endpoint = capture.get("provider_endpoint")
    parsed_endpoint = urlparse(endpoint) if isinstance(endpoint, str) else None
    if (capture.get("cli_sha256") != sha256(cli) or capture.get("model_calls") != 0 or
            capture.get("source") != "loopback_request_capture" or
            capture.get("provider") != "copilot-bridge" or
            parsed_endpoint is None or parsed_endpoint.scheme != "http" or
            parsed_endpoint.hostname != "127.0.0.1" or
            parsed_endpoint.path != "/v1" or
            f'model_providers.copilot-bridge.base_url="{endpoint}"' not in capture.get("command", []) or
            not isinstance(captures, list) or len(captures) != 1 or
            captures[0].get("request_path") != "/v1/responses" or
            captures[0].get("model") != "gpt-6-sol" or
            'model_provider="copilot-bridge"' not in capture.get("command", []) or
            SELECTOR_FEATURE not in capture.get("command", []) or
            capture.get("catalog") != catalog.get("models")):
        raise ValueError("explicit selector capture provenance mismatch")
    tools = captures[0].get("tools", [])
    if (len(tools) != 1 or tools[0].get("name") != "spawn_agent" or
            set(tools[0].get("parameters", {}).get("properties", {})) !=
            set(schema.get("supported_arguments", [])) or
            not {"task_name", "message", "fork_turns", "model", "reasoning_effort"} <=
            set(schema.get("supported_arguments", []))):
        raise ValueError("captured native spawn schema lacks explicit selectors")
    required = config["arms"]["treatment"]["required_hard_worker"]
    if not any(item.get("id") == required["model"] and
               required["effort"] in item.get("reasoning_efforts", [])
               for item in catalog.get("models", [])):
        raise ValueError("captured catalog lacks hard-worker selector")
    validator = root / "plugins" / "codex-model-router" / "hooks" / "dispatch_contract.py"
    if sha256(validator) != pin["dispatch_validator_sha256"]:
        raise ValueError("dispatch validator hash mismatch")
    now = datetime.now(timezone.utc).isoformat()
    name = "benchmark-hard-kernel-gpt-6-astra-xhigh"
    evidence = [{"id": key, "kind": value[1]["kind"], "source": str(value[0]),
                 "sha256": pin[key]["sha256"], "captured_at": now}
                for key, value in (("spawn_schema", sources["spawn_schema"]),
                                   ("model_catalog", sources["model_catalog"]))]
    contract = {"schema_version": 1, "dispatch_id": name, "purpose": "benchmark-hard-kernel",
                "canonical_name": name,
                "packet": {"worker_name": name, "task_id": name,
                           "native_task_name": name.replace("-", "_")},
                "native_dispatch": {"tool": "collaboration.spawn_agent", "naming_field": "task_name",
                                    "native_name": name.replace("-", "_"),
                                    "schema_evidence_ref": "spawn_schema"},
                "selection": {"mode": "explicit", "model": required["model"],
                              "reasoning_effort": required["effort"],
                              "evidence_refs": ["model_catalog"]},
                "capability_evidence": evidence}
    with tempfile.TemporaryDirectory() as directory:
        contract_path = Path(directory) / "contract.json"
        contract_path.write_text(json.dumps(contract), encoding="utf-8")
        result = subprocess.run([sys.executable, str(validator), "--input", str(contract_path)],
                                capture_output=True, text=True, timeout=15)
    try:
        receipt = json.loads(result.stdout)
    except ValueError as error:
        raise ValueError("explicit dispatch validator returned invalid JSON") from error
    if result.returncode or receipt.get("status") != "ready":
        raise ValueError("explicit dispatch validator did not authorize route")
    return {"status": "ready", "contract_sha256": receipt["contract_sha256"],
            "schema_capture_sha256": pin["schema_capture"]["sha256"],
            "spawn_schema_sha256": pin["spawn_schema"]["sha256"],
            "model_catalog_sha256": pin["model_catalog"]["sha256"],
            "validator_sha256": pin["dispatch_validator_sha256"]}


def treatment_options(config_path: Path, config: dict, plugin_root: Path,
                      dispatch_mode: str = "role") -> tuple[list[str], Path | None]:
    """Pin the installed plugin and choose the explicitly authorized route."""
    manifest = plugin_root / ".codex-plugin" / "plugin.json"
    hooks = plugin_root / "hooks" / "hooks.json"
    if not manifest.is_file() or sha256(manifest) != config["plugin"]["installed_manifest_sha256"]:
        raise ValueError("installed plugin manifest hash mismatch")
    plugin = json.loads(manifest.read_text(encoding="utf-8"))
    if (plugin.get("name"), plugin.get("version")) != (
            config["plugin"]["name"], config["plugin"]["version"]):
        raise ValueError("installed plugin identity mismatch")
    if not hooks.is_file() or sha256(hooks) != config["plugin"]["installed_hooks_sha256"]:
        raise ValueError("installed plugin hooks hash mismatch")
    options = ["--enable", "plugins", "--enable", "hooks", "--enable", "multi_agent"]
    role = None
    if dispatch_mode == "explicit":
        options += ["-c", SELECTOR_FEATURE,
                    "-c", "features.multi_agent_v2.max_concurrent_threads_per_session=3"]
    elif dispatch_mode == "role":
        required = config["arms"]["treatment"]["required_hard_worker"]
        role = (config_path.parent / required["role_file"]).resolve()
        if not role.is_file() or sha256(role) != required["role_sha256"]:
            raise ValueError("frozen hard-worker role hash mismatch")
        if role.read_text(encoding="utf-8").splitlines() != [
                f'model = "{required["model"]}"',
                f'model_reasoning_effort = "{required["effort"]}"']:
            raise ValueError("frozen hard-worker role content mismatch")
        role_value = json.dumps(str(role), ensure_ascii=True)
        options += ["-c", "agents.default={description=\"Benchmark hard-kernel worker\","  # legacy, blocked
                    f"config_file={role_value}}}"]
    else:
        raise ValueError("unsupported dispatch mode")
    trust = config["plugin"]["process_local_audit_trust"]
    expected_trust = {
        "preToolUse": "codex-model-router@personal:hooks/hooks.json:pre_tool_use:0:0",
        "postToolUse": "codex-model-router@personal:hooks/hooks.json:post_tool_use:0:0"}
    if (not isinstance(trust, list) or len(trust) != 2 or
            {item.get("event"): item.get("key") for item in trust} != expected_trust):
        raise ValueError("process-local audit hook identities mismatch")
    state_entries = []
    for item in trust:
        digest = item.get("current_hash")
        if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError("process-local audit hook hash invalid")
        state_entries.append(json.dumps(item["key"]) + "={enabled=true,trusted_hash=" +
                             json.dumps(digest) + "}")
    options += ["-c", "hooks.state={" + ",".join(state_entries) + "}"]
    return options, role


def probe_treatment_binding(cli: str, workspace: Path, child_env: dict,
                            options: list[str], plugin_root: Path, role: Path | None,
                            audit_trust: list[dict], dispatch_mode: str = "role") -> dict:
    """Inspect effective configuration in a fresh, zero-model CLI process."""
    command = [cli, *options, "--strict-config", "app-server", "--stdio"]
    process = subprocess.Popen(command, cwd=workspace, env=child_env,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
    assert process.stdin is not None and process.stdout is not None
    messages: queue.Queue[str] = queue.Queue()
    threading.Thread(target=lambda: [messages.put(line) for line in process.stdout],
                     daemon=True).start()

    def request(item: dict) -> dict:
        process.stdin.write(json.dumps(item) + "\n")
        process.stdin.flush()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                response = json.loads(messages.get(timeout=max(0.01, deadline - time.monotonic())))
            except (queue.Empty, ValueError) as error:
                raise ValueError("treatment binding probe timed out or returned invalid JSON") from error
            if response.get("id") == item["id"]:
                if "error" in response or not isinstance(response.get("result"), dict):
                    raise ValueError(f"treatment binding probe failed: {item['method']}")
                return response["result"]
        raise ValueError("treatment binding probe timed out")

    try:
        initialized = request({"id": 1, "method": "initialize", "params": {
            "clientInfo": {"name": "paired-treatment-binding", "version": "1"},
            "capabilities": {"experimentalApi": True}}})
        process.stdin.write('{"method":"initialized","params":{}}\n')
        process.stdin.flush()
        effective = request({"id": 2, "method": "config/read", "params": {
            "cwd": str(workspace.resolve()), "includeLayers": False}})
        inventory = request({"id": 3, "method": "hooks/list", "params": {
            "cwds": [str(workspace.resolve())]}})
    finally:
        process.kill()
        process.wait(timeout=5)

    return validate_treatment_binding(initialized, effective, inventory, cli, options,
                                      workspace, plugin_root, role, audit_trust, dispatch_mode)


def validate_treatment_binding(initialized: dict, effective: dict, inventory: dict,
                               cli: str, options: list[str], workspace: Path,
                               plugin_root: Path, role: Path | None,
                               audit_trust: list[dict], dispatch_mode: str = "role") -> dict:
    """Reject any dispatch capability or installed-hook mismatch observed by the CLI."""
    expected_role = str(role.resolve()) if role else None
    actual_role = (((effective.get("config") or {}).get("agents") or {}).get("default") or {})
    if dispatch_mode == "role":
        if actual_role.get("config_file") != expected_role:
            raise ValueError("fresh CLI did not load frozen [agents.default] role")
    else:
        if actual_role or SELECTOR_FEATURE not in options:
            raise ValueError("explicit route has default role or selector exposure missing")
        feature = ((effective.get("config") or {}).get("features") or {}).get("multi_agent_v2")
        if not isinstance(feature, dict) or feature.get("enabled") is not True or \
                feature.get("expose_spawn_agent_model_overrides") is not True:
            raise ValueError("fresh CLI did not load explicit selector feature")
    data = inventory.get("data")
    if not isinstance(data, list) or len(data) != 1 or data[0].get("warnings") or data[0].get("errors"):
        raise ValueError("fresh CLI hook inventory is incomplete")
    if os.path.normcase(os.path.normpath(data[0].get("cwd", ""))) != (
            os.path.normcase(os.path.normpath(str(workspace.resolve())))):
        raise ValueError("fresh CLI inspected the wrong workspace")
    plugin_id = "codex-model-router@personal"
    expected_events = {"sessionStart", "userPromptSubmit", "subagentStart",
                       "preToolUse", "postToolUse"}
    installed_hooks = plugin_root.resolve() / "hooks" / "hooks.json"
    plugin_hooks = [hook for hook in data[0].get("hooks", [])
                    if hook.get("pluginId") == plugin_id]
    if len(plugin_hooks) != len(expected_events) or (
            {hook.get("eventName") for hook in plugin_hooks} != expected_events):
        raise ValueError("fresh CLI did not load all installed router hooks")
    if any(os.path.normcase(os.path.normpath(hook.get("sourcePath", ""))) !=
           os.path.normcase(os.path.normpath(str(installed_hooks))) or
           hook.get("enabled") is not True or hook.get("trustStatus") != "trusted"
           for hook in plugin_hooks):
        statuses = ", ".join(f"{hook.get('eventName')}={hook.get('trustStatus')}"
                             for hook in plugin_hooks)
        raise ValueError("installed router hooks are not trusted and active in fresh CLI: " +
                         statuses)
    observed_audit = {hook["eventName"]: (hook.get("key"), hook.get("currentHash"))
                      for hook in plugin_hooks if hook["eventName"] in ("preToolUse", "postToolUse")}
    if observed_audit != {item["event"]: (item["key"], item["current_hash"])
                          for item in audit_trust}:
        raise ValueError("installed audit hook definition hash drifted")
    return {"status": "passed", "cli_home": initialized.get("codexHome"),
            "plugin_id": plugin_id, "installed_hooks_path": str(installed_hooks),
            "dispatch_mode": dispatch_mode, "role_path": expected_role,
            "hook_events": sorted(expected_events),
            "audit_hook_hashes": observed_audit,
            "command_prefix": [cli, *options]}


def validated_calibration(path: Path, config: dict) -> tuple[str, str] | None:
    """Validate both observed trials and return the cheapest passing selector."""
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("schema_version") != 1:
        raise ValueError("controller calibration schema mismatch")
    observations = record.get("observations")
    if not isinstance(observations, list) or len(observations) != 2:
        raise ValueError("both controller candidates require observed trials")
    indexed = {}
    for observation in observations:
        identity = (observation.get("model"), observation.get("effort"))
        if identity in indexed:
            raise ValueError("duplicate controller calibration identity")
        relative = Path(observation["evidence_path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("controller calibration evidence path is not local")
        source = path.parent / relative
        if not source.is_file() or sha256(source) != observation.get("evidence_sha256"):
            raise ValueError("controller calibration evidence mismatch")
        if observation.get("terminal") != "completed":
            raise ValueError("controller calibration did not complete")
        criteria = observation.get("criteria")
        if not isinstance(criteria, dict) or set(criteria) != set(CONTROLLER_CRITERIA):
            raise ValueError("controller calibration criteria incomplete")
        if any(type(value) is not bool for value in criteria.values()):
            raise ValueError("controller calibration criteria must be boolean")
        indexed[identity] = all(criteria.values())
    candidates = [(item["model"], item["effort"])
                  for item in config["arms"]["treatment"]["controller_candidates"]]
    if set(indexed) != set(candidates):
        raise ValueError("controller calibration selectors differ from frozen candidates")
    selected = next((identity for identity in candidates if indexed[identity]), None)
    recorded = record.get("selected", "missing")
    if selected is None:
        if recorded is not None:
            raise ValueError("failed controller calibration must select null")
    elif not isinstance(recorded, list) or tuple(recorded) != selected:
        raise ValueError("controller selection violates cheapest passing candidate")
    return selected


def calibrated_controller(path: Path, config: dict) -> tuple[str, str]:
    """Keep the normal treatment gate closed when no candidate passed."""
    selected = validated_calibration(path, config)
    if selected is None:
        raise ValueError("neither controller passed the capability calibration")
    return selected


def negative_treatment_controller(path: Path, config: dict, explicit: str) -> tuple[str, str]:
    """Permit only the named Sol/low diagnostic arm after evidenced failure."""
    if explicit != "gpt-6-sol/low":
        raise ValueError("negative treatment requires explicit gpt-6-sol/low")
    if validated_calibration(path, config) is not None:
        raise ValueError("negative treatment requires a failed calibration")
    candidate = ("gpt-6-sol", "low")
    if candidate not in {(item["model"], item["effort"]) for item in
                         config["arms"]["treatment"]["controller_candidates"]}:
        raise ValueError("Sol/low is not a frozen controller candidate")
    return candidate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", choices=("astra", "router", "baseline", "treatment"), required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--instruction", type=Path)
    parser.add_argument("--environment-note", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--negative-treatment-controller", choices=("gpt-6-sol/low",),
                        help="explicitly run Sol/low after evidenced calibration failure")
    parser.add_argument("--plugin-root", type=Path)
    parser.add_argument("--go-bin", type=Path)
    parser.add_argument("--live", action="store_true", help="explicitly permit paid Codex execution")
    parser.add_argument("--binding-probe", action="store_true",
                        help="check treatment plugin and selector route without a model call")
    parser.add_argument("--dispatch-mode", choices=("explicit", "role"), default="explicit",
                        help="configured treatment route; role remains unavailable for live runs")
    parser.add_argument("--dispatch-audit", action="store_true",
                        help="enable redacted hook output; trusted external stdout capture is still required")
    parser.add_argument("--codex-cli", type=Path)
    parser.add_argument("--go-cache-root", type=Path)
    parser.add_argument("--budget-usd", type=float, default=10.0)
    parser.add_argument("--stop-margin-usd", type=float, default=1.0)
    parser.add_argument("--timeout-seconds", type=int, default=3000)
    args = parser.parse_args()
    if args.negative_treatment_controller and (args.arm != "treatment" or not args.config or
                                               not args.calibration or args.binding_probe):
        parser.error("negative treatment requires configured treatment, calibration, and --live")
    if args.binding_probe and (args.arm not in ("router", "treatment") or not args.config):
        parser.error("binding probe requires a configured treatment arm")
    if not args.binding_probe and not args.live:
        parser.error("paid model execution requires --live")
    if not args.binding_probe and not args.evidence:
        parser.error("paid model execution requires --evidence")
    config = None
    binding_options: list[str] = []
    role_path = None
    dispatch_preflight = None
    if args.config:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        if config.get("schema_version") != 1:
            parser.error("unsupported paired configuration")
        task_prompt = pinned_prompt(args.config, config)
        prompt = submitted_prompt(args.config, config, args.arm)
        args.budget_usd = float(config["limits"]["astra_budget_usd"])
        args.stop_margin_usd = float(config["limits"]["stop_margin_usd"])
        args.timeout_seconds = int(config["limits"]["timeout_seconds"])
        model, effort = (config["arms"]["baseline"][key] for key in ("model", "effort"))
        if args.arm in ("router", "treatment"):
            if not args.plugin_root or (not args.binding_probe and not args.calibration):
                parser.error("treatment requires --plugin-root and --calibration")
            try:
                binding_options, role_path = treatment_options(args.config, config,
                                                                 args.plugin_root, args.dispatch_mode)
            except (OSError, ValueError, KeyError) as error:
                parser.error(str(error))
            if not args.binding_probe:
                try:
                    model, effort = (negative_treatment_controller(
                        args.calibration, config, args.negative_treatment_controller)
                        if args.negative_treatment_controller else
                        calibrated_controller(args.calibration, config))
                except (OSError, ValueError, KeyError, TypeError) as error:
                    parser.error(str(error))
    else:
        if not args.instruction or not args.environment_note:
            parser.error("legacy run requires --instruction and --environment-note")
        prompt = (args.instruction.read_text(encoding="utf-8").rstrip() + "\n\n" +
                  args.environment_note.read_text(encoding="utf-8").rstrip() + "\n")
        task_prompt = prompt
        model = "gpt-6-astra" if args.arm in ("astra", "baseline") else "gpt-5.6-sol"
        effort = "high"
    if args.budget_usd <= args.stop_margin_usd or args.stop_margin_usd < 0:
        parser.error("budget must exceed nonnegative stop margin")
    cli = str(args.codex_cli.resolve()) if args.codex_cli else shutil.which("codex")
    if not cli or not Path(cli).is_file():
        parser.error("codex CLI not found")
    cli_pin = None
    if config:
        if not args.codex_cli:
            parser.error("configured pair requires an explicit reviewed --codex-cli")
        try:
            cli_pin = pinned_cli(Path(cli), config)
            if args.arm in ("router", "treatment"):
                if args.dispatch_mode == "role" and not args.binding_probe:
                    raise ValueError("role route lacks runtime authorization; use explicit mode")
                if args.dispatch_mode == "explicit":
                    dispatch_preflight = explicit_dispatch_preflight(args.config, config, Path(cli))
        except (OSError, ValueError, KeyError, subprocess.CalledProcessError,
                subprocess.TimeoutExpired) as error:
            parser.error(str(error))
    if config:
        if not args.go_bin or not (args.go_bin / "go.exe").is_file():
            parser.error("configured run requires a Windows Go bin directory")
        if not args.go_cache_root or not all((args.go_cache_root / name).is_dir()
                                             for name in ("mod", "build")):
            parser.error("configured run requires a shared Go module/build cache")
    child_env = os.environ.copy()
    if args.dispatch_audit and args.arm in ("router", "treatment"):
        child_env["CODEX_MODEL_ROUTER_DISPATCH_AUDIT"] = "1"
    if config:
        child_env["PATH"] = str(args.go_bin.resolve()) + os.pathsep + child_env.get("PATH", "")
    if args.go_cache_root:
        cache_root = args.go_cache_root.resolve()
        child_env["GOMODCACHE"] = str(cache_root / "mod")
        child_env["GOCACHE"] = str(cache_root / "build")
    binding_receipt = None
    if binding_options:
        try:
            binding_receipt = probe_treatment_binding(cli, args.workspace.resolve(),
                                                      child_env, binding_options,
                                                      args.plugin_root, role_path,
                                                      config["plugin"]["process_local_audit_trust"],
                                                      args.dispatch_mode)
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            parser.error(f"treatment binding preflight failed: {error}")
    if args.binding_probe:
        binding_receipt.update({
            "model_calls": 0,
            "config_sha256": sha256(args.config),
            "cli_sha256": sha256(Path(cli)),
            "plugin_manifest_sha256": sha256(args.plugin_root / ".codex-plugin" / "plugin.json"),
            "installed_hooks_sha256": sha256(args.plugin_root / "hooks" / "hooks.json"),
            "role_sha256": sha256(role_path) if role_path else None,
            "dispatch_preflight": dispatch_preflight,
            "workspace": str(args.workspace.resolve()),
            "go_bin": str(args.go_bin.resolve()),
            "go_cache_root": str(args.go_cache_root.resolve()),
            "dispatch_audit_env": child_env.get("CODEX_MODEL_ROUTER_DISPATCH_AUDIT"),
        })
        if args.evidence:
            if args.evidence.exists():
                parser.error(f"evidence directory already exists: {args.evidence}")
            args.evidence.mkdir(parents=True)
            (args.evidence / "binding.json").write_text(
                json.dumps(binding_receipt, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(binding_receipt, indent=2), flush=True)
        return
    if args.evidence.exists():
        parser.error(f"evidence directory already exists: {args.evidence}")
    initial_tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=args.workspace, text=True).strip()
    if config and initial_tree != config["fixture"]["initial_tree"]:
        parser.error("initial tree does not match frozen fixture")
    if subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"],
                               cwd=args.workspace).strip():
        parser.error("arm workspace must start clean")
    initial_diff = subprocess.check_output(
        ["git", "diff", "--binary", "HEAD", "--"], cwd=args.workspace)
    args.evidence.mkdir(parents=True)
    calibration_ref = None
    if args.calibration:
        destination_root = args.evidence / "calibration"
        destination_root.mkdir()
        destination = destination_root / "calibration.json"
        shutil.copy2(args.calibration, destination)
        calibration_record = json.loads(args.calibration.read_text(encoding="utf-8"))
        for observation in calibration_record["observations"]:
            relative = Path(observation["evidence_path"])
            copy_target = destination_root / relative
            copy_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(args.calibration.parent / relative, copy_target)
        calibration_ref = {"path": destination.relative_to(args.evidence).as_posix(),
                           "sha256": sha256(destination)}
    (args.evidence / "initial.patch").write_bytes(initial_diff)
    (args.evidence / "prompt.md").write_text(prompt, encoding="utf-8", newline="\n")
    command = [cli, *binding_options, "exec", "--strict-config", "--json", "-m", model,
               "-c", f'model_reasoning_effort="{effort}"',
               "-C", str(args.workspace.resolve()), "-s", "workspace-write",
               "-o", str((args.evidence / "final.txt").resolve())]
    if args.go_cache_root:
        cache_root = args.go_cache_root.resolve()
        for name, variable in (("mod", "GOMODCACHE"), ("build", "GOCACHE")):
            directory = cache_root / name
            if not directory.is_dir():
                raise RuntimeError(f"Go cache directory missing: {directory}")
            command += ["--add-dir", str(directory)]
    if args.arm in ("astra", "baseline"):
        command += ["--disable", "plugins", "--disable", "hooks",
                    "--disable", "multi_agent"]
    command += ["-"]
    start = time.monotonic()
    last_progress = start
    started_at = datetime.now(timezone.utc).isoformat()
    reason = "completed"
    meter = None
    parent_id = None
    missing_child_since = None
    transcript = args.evidence / "codex.jsonl"
    with transcript.open("wb") as stdout, (args.evidence / "codex.stderr").open("wb") as stderr:
        process = subprocess.Popen(command, cwd=args.workspace, stdin=subprocess.PIPE,
                                   stdout=stdout, stderr=stderr, env=child_env)
        assert process.stdin is not None
        process.stdin.write(prompt.encode("utf-8"))
        process.stdin.close()
        while process.poll() is None:
            if meter is None:
                parent_id = parent_id_from_transcript(transcript)
                if parent_id:
                    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
                    meter = SessionMeter(codex_home / "sessions", parent_id, model, effort,
                                         bool(dispatch_preflight))
            if meter:
                meter.refresh()
                missing_child = any("dispatched child" in issue
                                    for issue in meter.dispatch_coverage_issues())
                if missing_child:
                    if missing_child_since is None:
                        missing_child_since = time.monotonic()
                    elif time.monotonic() - missing_child_since > 60:
                        reason = "child-session-evidence-unavailable"
                else:
                    missing_child_since = None
                if meter.unknown_models or meter.unknown_usage:
                    reason = "unknown-usage-or-model-rate"
                elif budget_limit_reached(meter, args.budget_usd, args.stop_margin_usd):
                    reason = "budget-safety-stop"
            if telemetry_unavailable(meter, time.monotonic() - start):
                reason = "usage-evidence-unavailable"
            if time.monotonic() - start >= args.timeout_seconds:
                reason = "time-limit"
            if time.monotonic() - last_progress >= 30:
                print(json.dumps({"arm": args.arm,
                                  "elapsed_seconds": round(time.monotonic() - start, 1),
                                  "estimated_usd_lower_bound": round(meter.cost, 6) if meter else None,
                                  "estimated_usd_upper_bound": round(meter.cost_upper, 6) if meter else None,
                                  "model_calls": meter.calls if meter else None,
                                  "sessions": len(meter.paths) if meter else None}), flush=True)
                last_progress = time.monotonic()
            if reason != "completed":
                process.terminate()
                break
            time.sleep(5)
        try:
            exit_code = process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            exit_code = process.wait()
        if meter:
            meter.refresh()
    if reason == "completed" and exit_code != 0:
        reason = "cli-failure-or-external-stop"
    usage_issues = meter.integrity_issues() if meter else ["no session meter"]
    if reason == "completed" and usage_issues:
        reason = "usage-reconciliation-failed"
    cost_status = "complete" if reason == "completed" and not usage_issues else "partial-or-unknown"
    session_files = []
    if meter:
        session_dir = args.evidence / "session-evidence"
        session_dir.mkdir()
        for source_path, state in sorted(meter.paths.items(), key=lambda item: item[1]["id"]):
            destination = session_dir / f"rollout-{state['id']}.jsonl"
            shutil.copy2(source_path, destination)
            session_files.append({"id": state["id"], "path": destination.relative_to(args.evidence).as_posix(),
                                  "sha256": sha256(destination)})
    stage_plan = args.workspace / ".codex-model-router" / "eval-stage-plan.json"
    stage_plan_ref = None
    if stage_plan.is_file():
        destination = args.evidence / "eval-stage-plan.json"
        shutil.copy2(stage_plan, destination)
        stage_plan_ref = {"path": destination.name, "sha256": sha256(destination)}
    candidate_patch, candidate_patch_error = capture_candidate_patch(args.workspace, args.evidence)
    if reason == "completed" and candidate_patch is None:
        reason = "artifact-capture-failed"
    result = {
        "arm": args.arm, "model": model, "effort": effort,
        "command": command,
        "cli": cli,
        "cli_version": cli_pin["version"] if cli_pin else subprocess.check_output([cli, "--version"], text=True).strip(),
        "cli_sha256": sha256(Path(cli)),
        "dispatch_mode": args.dispatch_mode if config and args.arm in ("router", "treatment") else None,
        "dispatch_preflight": dispatch_preflight,
        "config_sha256": sha256(args.config) if args.config else None,
        "calibration_sha256": sha256(args.calibration) if args.calibration else None,
        "calibration_ref": calibration_ref,
        "calibration_status": ("CALIBRATION_FAILED" if args.negative_treatment_controller
                               else "PASSED" if args.calibration else None),
        "controller_provenance": ({"source": "operator-explicit-negative-treatment"
                                   if args.negative_treatment_controller else "calibration",
                                   "model": model, "effort": effort}
                                  if args.calibration else None),
        "plugin_manifest_sha256": sha256(args.plugin_root / ".codex-plugin" / "plugin.json")
                                  if args.plugin_root else None,
        "treatment_binding": binding_receipt,
        "go_cache_root": str(args.go_cache_root.resolve()) if args.go_cache_root else None,
        "source": config["benchmark"]["suite"] + ", " + config["benchmark"]["task_id"]
                  if config else "legacy SWE-bench Pro V2 pilot",
        "workspace": str(args.workspace.resolve()),
        "initial_tree": initial_tree,
        "initial_diff_sha256": hashlib.sha256(initial_diff).hexdigest(),
        "task_prompt_sha256": hashlib.sha256(task_prompt.encode("utf-8")).hexdigest(),
        "treatment_execution_version": (config["treatment_execution"]["version"]
                                        if config and args.arm in ("router", "treatment")
                                        and "treatment_execution" in config else None),
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "started_at": started_at, "ended_at": datetime.now(timezone.utc).isoformat(),
        "wall_seconds": round(time.monotonic() - start, 3),
        "exit_code": exit_code, "stop_reason": reason,
        "budget_usd": args.budget_usd, "safety_margin_usd": args.stop_margin_usd,
        "budget_stop_limit": "Observed usage arrives after each billed response; in-flight overshoot is possible.",
        "cost_status": cost_status, "usage_integrity_issues": usage_issues,
        "pricing_source": "https://developers.openai.com/api/docs/pricing",
        "pricing_tier": "Standard",
        "parent_session_id": parent_id,
        "session_files": session_files,
        "stage_plan": stage_plan_ref,
        "dispatch_audit_enabled": bool(args.dispatch_audit and args.arm in ("router", "treatment")),
        "dispatch_audit_capture": "external-capture-required" if args.dispatch_audit else "disabled",
        "candidate_patch": candidate_patch,
        "candidate_patch_error": candidate_patch_error,
        "transcript_sha256": sha256(transcript),
        "final_sha256": sha256(args.evidence / "final.txt") if (args.evidence / "final.txt").is_file() else None,
        "usage": meter.summary() if meter else None,
    }
    (args.evidence / "run.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
