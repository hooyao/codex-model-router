"""Offline, hash-bound adjudication of a completed cancellation canary.

This never starts a model turn and never changes the source canary receipt.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re

from evals.long_horizon_v1.accounting import account
from evals.long_horizon_v1.cancellation_canary import (MAX_SECONDS, STOP_USD,
    READY_MARKER, RUNNING_MARKER, RUNNING_FILE, _baseline_runtime_abort)
from evals.long_horizon_v1.common import write_json_new


class AdjudicationError(ValueError):
    pass


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise AdjudicationError(reason)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_sha(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def _reconcile_usage(source: dict, current: dict, expected_turns: dict[str, str],
                     parent_turn: str, dispatches: dict[str, int]) -> dict:
    """Compare every old field and validate every newly replayed identity field."""
    _require(isinstance(source, dict) and isinstance(current, dict) and
             isinstance(source.get("sessions"), list) and
             isinstance(current.get("sessions"), list) and
             len(source["sessions"]) == len(current["sessions"]) == len(expected_turns),
             "source and current usage session sets differ")
    legacy = json.loads(json.dumps(current))
    response_ids: set[str] = set()
    seen_sessions: set[str] = set()
    for prior, fresh, compatible in zip(source["sessions"], current["sessions"],
                                         legacy["sessions"]):
        _require(isinstance(prior, dict) and isinstance(fresh, dict) and
                 prior.get("id") == fresh.get("id") and
                 fresh["id"] in expected_turns and
                 fresh["id"] not in seen_sessions,
                 "current session identity differs from source")
        session_id = fresh["id"]
        seen_sessions.add(session_id)
        turns, responses = fresh.get("turns"), fresh.get("responses")
        _require(isinstance(turns, list) and len(turns) == 1 and
                 isinstance(responses, list) and
                 isinstance(prior.get("responses"), list) and
                 len(responses) == len(prior["responses"]) == fresh.get("calls") and
                 type(fresh.get("calls")) is int and fresh["calls"] > 0,
                 "current turn or response count is ambiguous")
        turn = turns[0]
        _require(isinstance(turn, dict) and
                 turn.get("turn_id") == expected_turns[session_id] and
                 turn.get("model") == fresh.get("model") and
                 turn.get("effort") == fresh.get("effort") and
                 turn.get("terminal") == fresh.get("terminal") == "turn_aborted" and
                 turn.get("calls") == fresh["calls"] and
                 type(turn.get("terminal_timestamp")) is str and
                 _timestamp_ns(turn["terminal_timestamp"]) > dispatches[session_id],
                 "current turn identity, selector, usage, or terminal differs")
        for key in ("calls", "terminal", "terminal_timestamp"):
            compatible["turns"][0].pop(key, None)
        for response, prior_response, compatible_response in zip(
                responses, prior.get("responses", []), compatible["responses"]):
            digest = response.get("response_id_sha256")
            _require(isinstance(prior_response, dict) and
                     isinstance(digest, str) and
                     re.fullmatch(r"[0-9a-f]{64}", digest) is not None and
                     digest not in response_ids and
                     response.get("turn_id") == turn["turn_id"] and
                     response.get("root_turn_id") == parent_turn and
                     response.get("model") == fresh["model"] and
                     response.get("effort") == fresh["effort"],
                     "current response identity, root turn, or selector differs")
            response_ids.add(digest)
            for key in ("turn_id", "root_turn_id", "response_id_sha256"):
                compatible_response.pop(key, None)
    _require(seen_sessions == set(expected_turns), "current session set is incomplete")
    _require(legacy == source, "source usage core differs from fresh replay")
    return {"source_usage_sha256": _json_sha(source),
            "current_usage_sha256": _json_sha(current),
            "current_response_id_sha256": _json_sha(sorted(response_ids))}


def _rollout_path(sessions: Path, session_id: str) -> Path:
    matches = list(sessions.rglob(f"rollout-*{session_id}.jsonl"))
    _require(len(matches) == 1, f"rollout identity for {session_id} is not unique")
    return matches[0]


def _events(path: Path, session_id: str) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    _require(bool(rows) and rows[0].get("type") == "session_meta" and
             (rows[0].get("payload") or {}).get("id") == session_id,
             "rollout session identity differs from receipt")
    return rows


def _timestamp_ns(value: str) -> int:
    _require(isinstance(value, str) and value.endswith("Z"),
             "rollout timestamp is missing or ambiguous")
    return int(Decimal(str(datetime.fromisoformat(value[:-1] + "+00:00").timestamp()))
               * 1_000_000_000)


def _row(rows: list[dict], line: int) -> dict:
    _require(type(line) is int and 1 <= line <= len(rows), "recorded rollout line is unavailable")
    return rows[line - 1]


def _terminal(rows: list[dict], turn_id: str, dispatch_ns: int) -> bool:
    matches = [event for event in rows if event.get("type") == "event_msg" and
               (event.get("payload") or {}).get("type") == "turn_aborted" and
               (event.get("payload") or {}).get("turn_id") == turn_id and
               (event.get("payload") or {}).get("reason") == "interrupted"]
    return len(matches) == 1 and _timestamp_ns(matches[0].get("timestamp")) > dispatch_ns


def _judge_treatment(receipt: dict, sessions: Path, receipt_path: Path) -> dict:
    _require(receipt.get("kind") == "cancellation-capability" and
             receipt.get("arm") == "treatment" and receipt.get("status") == "UNKNOWN" and
             receipt.get("model") == "gpt-6-sol" and receipt.get("effort") == "low" and
             receipt.get("live_enabled") is False,
             "source is not an offline treatment cancellation receipt")
    _require(receipt.get("failure") ==
             "canary command completion or identity changed across interrupt",
             "source did not fail on the conservative post-interrupt completion check")
    _require(isinstance(receipt.get("wall_seconds"), (int, float)) and
             receipt["wall_seconds"] < MAX_SECONDS, "canary wall bound was not met")
    usage = receipt.get("usage") or {}
    _require(isinstance(usage.get("estimated_usd_upper_bound"), (int, float)) and
             usage["estimated_usd_upper_bound"] < STOP_USD,
             "canary spend bound was not met")
    parent_id, parent_turn = receipt.get("parent_thread_id"), receipt.get("turn_id")
    trigger = receipt.get("trigger_evidence") or {}
    child_id, child_turn = trigger.get("target_thread_id"), trigger.get("target_turn_id")
    attempt_id, second_id = trigger.get("attempt_id"), trigger.get("second_call_id")
    _require(all(isinstance(value, str) and value for value in
                 (parent_id, parent_turn, child_id, child_turn, attempt_id, second_id)) and
             parent_id != child_id, "attempt, parent, child, or turn identity is missing")
    attempts = receipt.get("native_attempts")
    _require(isinstance(attempts, list) and len(attempts) == 1 and
             attempts[0].get("session_id") == parent_id and
             attempts[0].get("turn_id") == parent_turn and
             attempts[0].get("child_id") == child_id and
             attempts[0].get("model") == "gpt-6-astra" and
             attempts[0].get("effort") == "xhigh", "native child lineage does not match")
    cancel = receipt.get("cancellation") or {}
    _require(cancel.get("status") == "verified-drained" and
             all(cancel.get(key) is True for key in
                 ("interrupt_ack", "turn_completed", "descendants_drained", "usage_drained")),
             "interrupt acknowledgement or drain is incomplete")
    requests = cancel.get("interrupt_requests")
    _require(isinstance(requests, list) and len(requests) == 2 and
             [(row.get("thread_id"), row.get("turn_id")) for row in requests] ==
                 [(child_id, child_turn), (parent_id, parent_turn)],
             "child-first interrupt identities do not match")
    for request in requests:
        proof = request.get("pre_dispatch_evidence") or {}
        dispatch = request.get("dispatch_time_ns")
        _require(type(dispatch) is int and
                 proof.get("attempt_id") == attempt_id and
                 proof.get("target_thread_id") == child_id and
                 proof.get("target_turn_id") == child_turn and
                 proof.get("second_call_id") == second_id and
                 type(proof.get("marker_observed_ns")) is int and
                 type(proof.get("completion_absent_checked_ns")) is int and
                 proof["marker_observed_ns"] <= proof["completion_absent_checked_ns"] < dispatch,
                 "pre-dispatch identity or completion absence is not proven")
    child_dispatch, parent_dispatch = (row["dispatch_time_ns"] for row in requests)
    _require(child_dispatch < parent_dispatch, "interrupt ordering is ambiguous")
    _require(trigger.get("target_thread_id") == child_id and
             trigger.get("target_turn_id") == child_turn and
             type(trigger.get("completion_absent_checked_ns")) is int and
             trigger["completion_absent_checked_ns"] < child_dispatch,
             "priced readiness was not established before dispatch")
    marker = receipt_path.parent / "workspace" / (RUNNING_FILE + attempt_id)
    _require(marker.is_file() and marker.read_text(encoding="utf-8") ==
             RUNNING_MARKER + attempt_id, "attempt-specific running marker differs")
    parent_path, child_path = (_rollout_path(sessions, sid) for sid in (parent_id, child_id))
    parent_rows, child_rows = _events(parent_path, parent_id), _events(child_path, child_id)
    first_call = (_row(child_rows, trigger["first_done_line"])
                  .get("payload") or {})
    priced = (_row(child_rows, trigger["priced_line"])
              .get("payload") or {})
    second = (_row(child_rows, trigger["second_call_line"])
              .get("payload") or {})
    _require(trigger["first_done_line"] < trigger["priced_line"] <
             trigger["second_call_line"] and
             first_call.get("type") == "custom_tool_call_output" and
             first_call.get("call_id") == trigger.get("first_call_id") and
             READY_MARKER + attempt_id in str(first_call.get("output", "")) and
             priced.get("type") == "token_count" and
             isinstance((priced.get("info") or {}).get("last_token_usage"), dict) and
             second.get("type") == "custom_tool_call" and
             second.get("name") == "exec" and second.get("call_id") == second_id and
             RUNNING_MARKER + attempt_id in str(second.get("input", "")) and
             RUNNING_FILE + attempt_id in str(second.get("input", "")) and
             "time.sleep(30)" in str(second.get("input", "")) and
             "aborted by user" not in str(second.get("input", "")),
             "raw child readiness or 30-second command differs")
    outputs = [(line_no, event) for line_no, event in enumerate(child_rows, 1)
               if event.get("type") == "response_item" and
               (event.get("payload") or {}).get("type") == "custom_tool_call_output" and
               (event.get("payload") or {}).get("call_id") == second_id]
    _require(len(outputs) == 1 and outputs[0][0] > trigger["second_call_line"],
             "matched second-command result is missing or duplicated")
    output_line, output_event = outputs[0]
    output = output_event.get("payload") or {}
    passthrough = output.get("internal_chat_message_metadata_passthrough") or {}
    output_time = passthrough.get("create_time")
    output_text = output.get("output")
    _require(passthrough.get("turn_id") == child_turn and
             isinstance(output_time, (int, float)) and
             type(output_text) is str and
             re.fullmatch(r"aborted by user after (\d+(?:\.\d+)?)s", output_text) is not None and
             Decimal(re.fullmatch(r"aborted by user after (\d+(?:\.\d+)?)s",
                                  output_text).group(1)) < 30 and
             int(Decimal(str(output_time)) * 1_000_000_000) > child_dispatch and
             _timestamp_ns(output_event.get("timestamp")) > child_dispatch,
             "matched output is not an ordered runtime cancellation result")
    _require(_terminal(child_rows, child_turn, child_dispatch) and
             _terminal(parent_rows, parent_turn, parent_dispatch),
             "interrupted terminal events do not match both turns")
    post = receipt.get("post_interrupt_evidence") or {}
    _require(post.get("second_call_id") == second_id and
             post.get("target_thread_id") == child_id and
             post.get("second_completed") is True,
             "post-close record does not bind the matched command")
    observed = account(sessions, parent_id, receipt["model"], receipt["effort"], True,
                       interruption_proof=receipt)
    current_attempts = observed["native_attempts"]
    _require(observed["status"] == "complete" and observed["issues"] == [] and
             observed["interruption_verified"] is True and
             isinstance(current_attempts, list) and len(current_attempts) == 1 and
             {key: value for key, value in current_attempts[0].items()
              if key != "turns"} == attempts[0] and
             observed["summary"]["model_calls"] > 0 and
             len(observed["summary"]["sessions"]) == 2 and
             all(session["calls"] > 0 and
                 isinstance(session["reported_total_usage"], dict) and
                 session["terminal"] == "turn_aborted"
                 for session in observed["summary"]["sessions"]),
             "final parent/child usage or terminal status does not reconcile")
    ledger = current_attempts[0].get("turns")
    _require(isinstance(ledger, list) and len(ledger) == 1 and
             ledger[0].get("turn_id") == child_turn and
             ledger[0].get("parent_turn_id") == parent_turn and
             ledger[0].get("root_turn_id") == parent_turn and
             ledger[0].get("model") == "gpt-6-astra" and
             ledger[0].get("effort") == "xhigh" and
             ledger[0].get("terminal") == "turn_aborted" and
             ledger[0].get("closure") == "interrupted" and
             type(ledger[0].get("terminal_line")) is int and
             "completion_line" not in ledger[0],
             "current native child turn ledger differs")
    usage_hashes = _reconcile_usage(usage, observed["summary"],
        {parent_id: parent_turn, child_id: child_turn}, parent_turn,
        {parent_id: parent_dispatch, child_id: child_dispatch})
    return {"parent_rollout": {"sha256": _sha(parent_path), "session_id": parent_id},
            "child_rollout": {"sha256": _sha(child_path), "session_id": child_id},
            "attempt_id": attempt_id, "child_turn_id": child_turn,
            "second_call_id": second_id, "second_call_line": trigger["second_call_line"],
            "cancellation_output_line": output_line,
            "child_interrupt_dispatch_ns": child_dispatch,
            "cancellation_output_create_ns": int(Decimal(str(output_time)) * 1_000_000_000),
            "parent_interrupt_dispatch_ns": parent_dispatch,
            "estimated_usd_upper_bound": usage["estimated_usd_upper_bound"],
            "model_calls": usage["model_calls"], "wall_seconds": receipt["wall_seconds"],
            **usage_hashes, "current_native_attempts_sha256": _json_sha(current_attempts)}


def _judge_baseline(receipt: dict, sessions: Path, receipt_path: Path) -> dict:
    _require(receipt.get("kind") == "cancellation-capability" and
             receipt.get("arm") == "baseline" and receipt.get("status") == "UNKNOWN" and
             receipt.get("model") == "gpt-6-astra" and receipt.get("effort") == "xhigh" and
             receipt.get("live_enabled") is False and
             receipt.get("real_child_observed") is False and
             receipt.get("native_attempts") == [] and
             receipt.get("failure") == "canary command order cannot be established after interrupt",
             "source is not the exact baseline parent-stop receipt")
    _require(type(receipt.get("wall_seconds")) in (int, float) and
             0 <= receipt["wall_seconds"] < MAX_SECONDS,
             "baseline wall bound was not met")
    usage = receipt.get("usage") or {}
    _require(type(usage.get("estimated_usd_upper_bound")) in (int, float) and
             0 <= usage["estimated_usd_upper_bound"] < STOP_USD,
             "baseline spend bound was not met")
    parent_id, turn_id = receipt.get("parent_thread_id"), receipt.get("turn_id")
    trigger = receipt.get("trigger_evidence") or {}
    attempt_id, second_id = trigger.get("attempt_id"), trigger.get("second_call_id")
    _require(all(isinstance(value, str) and value for value in
                 (parent_id, turn_id, attempt_id, second_id)) and
             trigger.get("target_thread_id") == parent_id and
             trigger.get("target_turn_id") == turn_id and
             type(trigger.get("first_done_line")) is int and
             type(trigger.get("priced_line")) is int and
             type(trigger.get("second_call_line")) is int and
             trigger["first_done_line"] < trigger["priced_line"] <
                 trigger["second_call_line"],
             "baseline parent, turn, attempt, or priced readiness identity differs")
    cancel = receipt.get("cancellation") or {}
    _require(cancel.get("status") == "verified-drained" and
             cancel.get("turn_id") == turn_id and
             all(cancel.get(key) is True for key in
                 ("interrupt_ack", "turn_completed", "descendants_drained", "usage_drained")) and
             cancel.get("interrupted_threads") == [parent_id],
             "baseline interrupt acknowledgement or drain is incomplete")
    requests = cancel.get("interrupt_requests")
    _require(isinstance(requests, list) and len(requests) == 1 and
             requests[0].get("thread_id") == parent_id and
             requests[0].get("turn_id") == turn_id and
             type(requests[0].get("dispatch_time_ns")) is int,
             "baseline parent interrupt identity is missing")
    request = requests[0]
    dispatch_ns = request["dispatch_time_ns"]
    pre = request.get("pre_dispatch_evidence") or {}
    _require(all(pre.get(key) == trigger.get(key) for key in
                 ("attempt_id", "first_call_id", "first_done_line", "priced_line",
                  "second_call_id", "second_call_line", "target_thread_id",
                  "target_turn_id")) and
             type(trigger.get("marker_observed_ns")) is int and
             type(trigger.get("completion_absent_checked_ns")) is int and
             type(pre.get("marker_observed_ns")) is int and
             type(pre.get("completion_absent_checked_ns")) is int and
             trigger["marker_observed_ns"] <= trigger["completion_absent_checked_ns"] <
                 pre["marker_observed_ns"] <= pre["completion_absent_checked_ns"] < dispatch_ns,
             "baseline pre-dispatch priced readiness or completion absence is missing")
    marker = receipt_path.parent / "workspace" / (RUNNING_FILE + attempt_id)
    _require(marker.is_file() and marker.read_text(encoding="utf-8") ==
             RUNNING_MARKER + attempt_id, "baseline attempt marker differs")
    post = receipt.get("post_interrupt_evidence") or {}
    _require(post.get("target_thread_id") == parent_id and
             post.get("second_call_id") == second_id and
             post.get("second_completed") is True and
             type(post.get("rollout_checked_ns")) is int and
             post["rollout_checked_ns"] > dispatch_ns,
             "baseline post-close command record is missing")
    parent_path = _rollout_path(sessions, parent_id)
    rows = _events(parent_path, parent_id)
    first = (_row(rows, trigger["first_done_line"]).get("payload") or {})
    priced = (_row(rows, trigger["priced_line"]).get("payload") or {})
    _require(first.get("type") == "custom_tool_call_output" and
             first.get("call_id") == trigger.get("first_call_id") and
             READY_MARKER + attempt_id in str(first.get("output", "")) and
             priced.get("type") == "token_count" and
             isinstance((priced.get("info") or {}).get("last_token_usage"), dict),
             "baseline first readiness was not followed by a priced response")
    try:
        abort = _baseline_runtime_abort(parent_path, parent_id, turn_id,
            attempt_id, second_id, trigger["second_call_line"], dispatch_ns)
    except (RuntimeError, ValueError, TypeError) as error:
        raise AdjudicationError(str(error)) from error
    observed = account(sessions, parent_id, "gpt-6-astra", "xhigh", False)
    _require(observed["status"] == "UNKNOWN" and
             observed["issues"] == ["parent session lacks successful terminal result"] and
             observed["native_attempts"] == [] and
             len(usage.get("sessions", [])) == 1 and
             usage["sessions"][0].get("id") == parent_id and
             usage["sessions"][0].get("model") == "gpt-6-astra" and
             usage["sessions"][0].get("effort") == "xhigh" and
             usage["sessions"][0].get("terminal") == "turn_aborted" and
             type(usage["sessions"][0].get("calls")) is int and
             usage["sessions"][0]["calls"] > 0 and
             usage.get("model_calls") == usage["sessions"][0]["calls"] and
             isinstance(usage["sessions"][0].get("reported_total_usage"), dict) and
             usage.get("unknown_models") == [] and usage.get("unknown_usage") == [],
             "baseline parent usage or terminal status does not reconcile")
    usage_hashes = _reconcile_usage(usage, observed["summary"],
        {parent_id: turn_id}, turn_id, {parent_id: dispatch_ns})
    return {"parent_rollout": {"sha256": _sha(parent_path), "session_id": parent_id},
            "marker_sha256": _sha(marker), "attempt_id": attempt_id,
            "parent_turn_id": turn_id, "second_call_id": second_id,
            "second_call_line": trigger["second_call_line"],
            "runtime_abort_output_line": abort["runtime_abort_output_line"],
            "runtime_abort_created_ns": abort["runtime_abort_created_ns"],
            "runtime_abort_event_window_ns": abort["runtime_abort_event_window_ns"],
            "interrupted_terminal_line": abort["interrupted_terminal_line"],
            "parent_interrupt_dispatch_ns": dispatch_ns,
            "estimated_usd_upper_bound": usage["estimated_usd_upper_bound"],
            "model_calls": usage["model_calls"], "wall_seconds": receipt["wall_seconds"],
            **usage_hashes, "current_native_attempts_sha256": _json_sha([])}


def _judge(receipt: dict, sessions: Path, receipt_path: Path) -> dict:
    if receipt.get("arm") == "baseline":
        return _judge_baseline(receipt, sessions, receipt_path)
    return _judge_treatment(receipt, sessions, receipt_path)


def prior_adjudication_bound(decision: dict, source_sha: str, proof: dict) -> bool:
    """Require the version-2 decision to preserve its reviewed version-1 source."""
    binding = decision.get("source_adjudication")
    if not isinstance(binding, dict) or set(binding) != {"path", "sha256"}:
        return False
    value = binding.get("path")
    path = Path(value) if isinstance(value, str) else Path()
    if not path.is_absolute() or not path.is_file():
        return False
    try:
        if binding.get("sha256") != _sha(path):
            return False
        prior = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    prior_proof = prior.get("proof") if isinstance(prior, dict) else None
    return (isinstance(prior_proof, dict) and
            prior.get("schema_version") == 1 and
            prior.get("kind") == "cancellation-adjudication" and
            prior.get("status") == "verified" and
            prior.get("source_receipt_status") == "UNKNOWN" and
            prior.get("source_receipt_sha256") == source_sha and
            prior.get("arm") == decision.get("arm") and
            prior.get("live_enabled") is False and prior.get("reason") is None and
            all(proof.get(key) == value for key, value in prior_proof.items()))


def adjudicate(receipt_path: Path, sessions: Path, output: Path,
               prior_adjudication: Path) -> dict:
    """Write a new decision bound to the immutable source and raw rollouts."""
    source_sha = _sha(receipt_path)
    source = json.loads(receipt_path.read_text(encoding="utf-8"))
    status, reason, proof = "UNKNOWN", None, None
    binding = {"path": str(prior_adjudication.resolve()),
               "sha256": _sha(prior_adjudication)}
    try:
        proof = _judge(source, sessions, receipt_path)
        candidate = {"arm": source.get("arm"), "source_adjudication": binding}
        _require(prior_adjudication_bound(candidate, source_sha, proof),
                 "prior adjudication does not match source or replay")
        status = "verified"
    except (AdjudicationError, OSError, ValueError, TypeError, KeyError,
            AttributeError) as error:
        reason = str(error)
    result = {"schema_version": 2, "kind": "cancellation-adjudication",
              "status": status, "source_receipt_sha256": source_sha,
              "source_receipt_status": source.get("status"),
              "arm": source.get("arm"), "live_enabled": False,
              "source_adjudication": binding, "proof": proof, "reason": reason}
    write_json_new(output, result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--sessions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prior-adjudication", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(adjudicate(args.receipt, args.sessions, args.output,
                                args.prior_adjudication), indent=2))
