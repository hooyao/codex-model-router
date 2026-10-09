"""Conservative paired-result reducer; unknown usage cannot imply a saving."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from evals.long_horizon_v1.common import HERE, TASK_ID, file_sha, fresh_directory, manifest, write_json_new
from evals.long_horizon_v1.fork_policy import DIAGNOSTIC, STRICT, validate_observed_spawns


def _timestamp_ns(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        return None
    try:
        return int(parsed.timestamp() * 1_000_000_000)
    except (OSError, OverflowError, ValueError):
        return None


def _interruption_closed(data: dict, parent: str, sessions: list,
                         native_attempts: list) -> bool:
    """Independently check receipt identities and ordering for priced aborts."""
    proof = data.get("interruption_proof")
    if not isinstance(proof, dict):
        return False
    parent_rows = [row for row in sessions if row["id"] == parent]
    aborts = [(attempt, turn) for attempt in native_attempts
              for turn in attempt.get("turns", [])
              if isinstance(turn, dict) and turn.get("terminal") == "turn_aborted"]
    if len(parent_rows) != 1 or len(aborts) != 1:
        return False
    attempt, child_turn = aborts[0]
    child_id, child_turn_id = attempt.get("child_id"), child_turn.get("turn_id")
    parent_turn_id = child_turn.get("parent_turn_id")
    parent_turns = [turn for turn in parent_rows[0]["turns"]
                    if turn.get("turn_id") == parent_turn_id and
                    turn.get("terminal") == "turn_aborted"]
    trigger = proof.get("trigger_evidence")
    cancel = proof.get("cancellation")
    if (len(parent_turns) != 1 or child_turn.get("closure") != "interrupted" or
            "completion_line" in child_turn or
            proof.get("parent_thread_id") != parent or
            proof.get("turn_id") != parent_turn_id or
            not isinstance(trigger, dict) or
            (trigger.get("target_thread_id"), trigger.get("target_turn_id")) !=
            (child_id, child_turn_id) or not isinstance(cancel, dict) or
            data.get("cancellation") != cancel or
            cancel.get("turn_id") != parent_turn_id or
            cancel.get("status") != "verified-drained" or
            any(cancel.get(key) is not True for key in (
                "interrupt_ack", "turn_completed", "descendants_drained",
                "usage_drained")) or
            cancel.get("interrupted_threads") != [child_id, parent]):
        return False
    requests = cancel.get("interrupt_requests")
    if (not isinstance(requests, list) or len(requests) != 2 or
            any(not isinstance(row, dict) for row in requests) or
            [(row.get("thread_id"), row.get("turn_id")) for row in requests] !=
            [(child_id, child_turn_id), (parent, parent_turn_id)]):
        return False
    times = []
    for request in requests:
        pre = request.get("pre_dispatch_evidence")
        stamp = request.get("dispatch_time_ns")
        if (not isinstance(pre, dict) or type(stamp) is not int or stamp <= 0 or
                type(pre.get("marker_observed_ns")) is not int or
                type(pre.get("completion_absent_checked_ns")) is not int or
                (pre.get("target_thread_id"), pre.get("target_turn_id")) !=
                (child_id, child_turn_id) or
                not 0 < pre["marker_observed_ns"] <=
                    pre["completion_absent_checked_ns"] < stamp):
            return False
        times.append(stamp)
    child_abort = _timestamp_ns(child_turn.get("terminal_timestamp"))
    parent_abort = _timestamp_ns(parent_turns[0].get("terminal_timestamp"))
    if (not times[0] < times[1] or child_abort is None or parent_abort is None or
            child_abort <= times[0] or parent_abort <= times[1]):
        return False
    proof_usage = proof.get("usage")
    usage = data["usage"]
    top = ("model_calls", "estimated_usd_lower_bound",
           "estimated_usd_upper_bound", "token_classes", "unknown_models",
           "unknown_usage")
    if (not isinstance(proof_usage, dict) or
            any(proof_usage.get(key) != usage.get(key) for key in top)):
        return False
    proof_sessions = proof_usage.get("sessions")
    if not isinstance(proof_sessions, list) or len(proof_sessions) != len(sessions):
        return False
    by_id = {row.get("id"): row for row in proof_sessions if isinstance(row, dict)}
    keys = ("calls", "terminal", "reported_total_usage", "model", "effort",
            "token_classes", "estimated_usd_lower_bound",
            "estimated_usd_upper_bound")
    return len(by_id) == len(sessions) and all(
        isinstance(by_id.get(row["id"]), dict) and
        all(by_id[row["id"]].get(key) == row.get(key) for key in keys)
        for row in sessions)


def _arm(path: Path, expected: str) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("arm") != expected or data.get("task_id") != TASK_ID:
        raise ValueError("arm identity mismatch")
    stage = data.get("stage") if isinstance(data.get("stage"), dict) else {}
    if data.get("manifest_sha256", stage.get("manifest_sha256")) != file_sha(HERE / "manifest.json"):
        raise ValueError("manifest drift")
    if data.get("mode") != "mock-dry-run":
        parent = data.get("parent_thread_id")
        if not isinstance(parent, str) or not parent or stage.get("thread_id") != parent:
            raise ValueError("live parent identity missing or inconsistent")
        usage = data.get("usage")
        if not isinstance(usage, dict):
            raise ValueError("live usage evidence missing")
        sessions = usage.get("sessions")
        responses = data.get("per_turn_responses")
        if not isinstance(sessions, list) or not isinstance(responses, list):
            raise ValueError("live session or response identities missing")
        ids = [item.get("id") for item in sessions if isinstance(item, dict)]
        if (len(ids) != len(sessions) or
                any(not isinstance(item, str) or not item for item in ids) or
                len(ids) != len(set(ids)) or ids.count(parent) != 1):
            raise ValueError("live session identity invalid")
        # Enforce the baseline arm independently of the live runner. A forged
        # complete receipt cannot turn a delegated Astra run into "Astra alone".
        if expected == "baseline" and any(session_id != parent for session_id in ids):
            raise ValueError("baseline child session forbidden")
        if expected == "baseline" and data.get("native_attempts"):
            raise ValueError("baseline native child attempt forbidden")
        if expected == "treatment":
            raw = data.get("parent_rollout_path")
            plans = data.get("dispatch_plans_path")
            mode = data.get("packet_scope_mode")
            paths = data.get("child_rollout_paths")
            if not isinstance(raw, str) or not raw or not isinstance(plans, str) or not plans:
                raise ValueError("context-policy-failure: raw spawn or plan evidence missing")
            if mode not in (DIAGNOSTIC, STRICT) or not isinstance(paths, dict) or any(
                    not isinstance(key, str) or not isinstance(value, str) or not value
                    for key, value in paths.items()):
                raise ValueError("context-policy-failure: packet mode or child paths missing")
            calls, fork_issues = validate_observed_spawns(
                Path(raw), parent, data.get("native_attempts"),
                closed_snapshot=True, plans=Path(plans),
                child_rollouts={key: Path(value) for key, value in paths.items()}, mode=mode)
            if fork_issues:
                raise ValueError("; ".join(fork_issues))
            if set(paths) != {call.get("child_id") for call in calls}:
                raise ValueError("context-policy-failure: child rollout inventory differs from spawns")
            scope = "VERIFIED" if calls and all(call["packet_scope"] == "VERIFIED"
                for call in calls) else "UNKNOWN"
            fork_evidence = data.get("fork_policy")
            standalone = data.get("benchmark_mode") == "standalone-feasibility"
            recovery = (manifest().get("pilot", {}).get("schema_version") == 5 and
                        manifest()["pilot"].get("path") == "pilot-plan-v15.json")
            if (not isinstance(fork_evidence, dict) or
                    fork_evidence.get("packet_scope") != scope or
                    data.get("claim_class") != ("standalone-treatment-feasibility" if standalone
                        else "matched-empirical-scope-unknown" if recovery and mode == DIAGNOSTIC
                        else "non-matched-non-interleaved-diagnostic" if mode == DIAGNOSTIC
                        else "strict-selective") or
                    standalone and mode != DIAGNOSTIC or
                    mode == STRICT and scope != "VERIFIED"):
                raise ValueError("context-policy-failure: packet scope claim invalid")
        if any(not isinstance(item.get("turns"), list) or
               not item["turns"] or any(not isinstance(turn, dict) or
               not isinstance(turn.get("turn_id"), str) or not turn["turn_id"]
               for turn in item["turns"]) for item in sessions):
            raise ValueError("live turn identity invalid")
        turn_pairs = {(item["id"], turn.get("turn_id"))
                      for item in sessions for turn in item["turns"]}
        if (any(item.get("parent_id") not in ids for item in sessions
                    if item["id"] != parent) or
                any(not isinstance(item, dict) or item.get("session_id") not in ids or
                    not isinstance(item.get("turn_id"), str) or not item["turn_id"]
                    or (item["session_id"], item["turn_id"]) not in turn_pairs
                    for item in responses)):
            raise ValueError("live session or response lineage invalid")
        if data.get("cost_status") == "complete" and (
                data.get("usage_issues") or
                usage.get("unknown_usage") or usage.get("unknown_models") or
                len(responses) != usage.get("model_calls")):
            raise ValueError("complete cost claim lacks reconciled responses")
        if data.get("cost_status") == "complete":
            attempts = data.get("attempts")
            native_attempts = data.get("native_attempts")
            events = stage.get("events")
            interrupted = any(turn.get("terminal") == "turn_aborted"
                              for session in sessions for turn in session["turns"])
            if interrupted and expected != "treatment":
                raise ValueError("complete baseline interrupted cost lacks supported proof")
            if interrupted and data.get("stop_reason") == "accepted-final":
                raise ValueError("interrupted arm cannot claim an accepted final result")
            if (not isinstance(attempts, list) or
                    (not attempts and not interrupted) or
                    not isinstance(native_attempts, list) or
                    not isinstance(events, list) or
                    len(attempts) != sum(event.get("kind") in ("submit", "protocol_failure") for event in events
                                         if isinstance(event, dict)) or
                    any(not isinstance(event, dict) or event.get("thread_id") != parent or
                        event.get("seq") != index or type(event.get("time_ns")) is not int
                        for index, event in enumerate(events))):
                raise ValueError("complete cost claim lacks stage attempt identities")
            parent_turns = [attempt.get("turn_id") for attempt in attempts
                            if isinstance(attempt, dict)]
            if interrupted:
                proof = data.get("interruption_proof")
                if not isinstance(proof, dict) or not isinstance(proof.get("turn_id"), str):
                    raise ValueError("complete interrupted cost lacks proof identity")
                parent_turns.append(proof["turn_id"])
            if (len(parent_turns) != len(attempts) + int(interrupted) or
                    any(not isinstance(turn, str) or not turn for turn in parent_turns) or
                    len(parent_turns) != len(set(parent_turns)) or
                    any(attempt.get("thread_id") != parent for attempt in attempts) or
                    any(item["turn_id"] not in parent_turns for item in responses
                        if item["session_id"] == parent)):
                raise ValueError("complete cost claim lacks parent attempt linkage")
            for attempt in attempts:
                start, end = attempt.get("stage_event_start"), attempt.get("stage_event_end")
                if (type(start) is not int or type(end) is not int or
                        not 0 <= start < end <= len(events) or
                        len([event for event in events[start:end] if
                             event["kind"] in ("submit", "protocol_failure")]) != 1 or
                        events[start].get("kind") not in ("submit", "protocol_failure") or
                        events[start].get("round") != attempt.get("round")):
                    raise ValueError("complete cost claim lacks stage event span")
            child_ids = {item["id"] for item in sessions if item["id"] != parent}
            linked_children = [item.get("child_id") for item in native_attempts
                               if isinstance(item, dict) and
                               item.get("turn_id") in parent_turns]
            if (len(linked_children) != len(native_attempts) or
                    any(not isinstance(child, str) or not child for child in linked_children) or
                    len(linked_children) != len(set(linked_children)) or
                    set(linked_children) != child_ids):
                raise ValueError("complete cost claim lacks native child attempt linkage")
            if expected == "treatment":
                ledger = {item["child_id"]: item.get("turns") for item in native_attempts}
                for session in sessions:
                    if session["id"] == parent:
                        continue
                    native_turns = ledger.get(session["id"])
                    observed_turns = session["turns"]
                    if (not isinstance(native_turns, list) or not native_turns or
                            len(native_turns) != len(observed_turns)):
                        raise ValueError("complete cost claim lacks closed child turn ledger")
                    for native, observed in zip(native_turns, observed_turns):
                        closure = isinstance(native, dict) and (
                                   native.get("closure") == "completed" and
                                   type(native.get("completion_line")) is int or
                                   native.get("closure") == "interrupted" and
                                   native.get("terminal") == "turn_aborted" and
                                   "completion_line" not in native)
                        if (not isinstance(native, dict) or
                                type(native.get("terminal_line")) is not int or
                                native["terminal_line"] < 1 or not closure or
                                native.get("terminal") not in (
                                    "task_complete", "task_completed", "task_failed",
                                    "task_cancelled", "task_cancel", "turn_aborted") or
                                any(native.get(key) != observed.get(key) for key in (
                                    "turn_id", "model", "effort", "terminal")) or
                                not isinstance(observed.get("calls"), int) or
                                observed["calls"] < 1 or
                                sum(item.get("session_id") == session["id"] and
                                    item.get("turn_id") == observed["turn_id"]
                                    for item in responses) != observed["calls"]):
                            raise ValueError("complete cost claim lacks closed child turn usage")
                if interrupted and not _interruption_closed(
                        data, parent, sessions, native_attempts):
                    raise ValueError("complete interrupted cost lacks verified closure proof")
                if not interrupted and data.get("interruption_proof") is not None:
                    raise ValueError("complete cost claim has unrelated interruption proof")
            digests = [item.get("response_id_sha256") for item in responses]
            if (any(digests) and (any(not isinstance(value, str) or len(value) != 64
                                      for value in digests) or
                                 len(set(digests)) != len(digests))):
                raise ValueError("complete cost claim has missing or duplicate response identity")
            if any(digests):
                child_turns = {(attempt["child_id"], turn["turn_id"]): turn
                               for attempt in native_attempts
                               for turn in attempt.get("turns", [])}
                if any(item.get("root_turn_id") != (
                        item["turn_id"] if item["session_id"] == parent else
                        child_turns.get((item["session_id"], item["turn_id"]), {}).get(
                            "parent_turn_id")) for item in responses):
                    raise ValueError("complete cost claim has wrong raw root turn identity")
            for session in sessions:
                if (isinstance(session.get("calls"), int) and
                        session["calls"] != sum(item.get("session_id") == session["id"]
                                                for item in responses)):
                    raise ValueError("complete cost claim has inconsistent session responses")
            for bound in ("estimated_usd_lower_bound", "estimated_usd_upper_bound"):
                total = usage.get(bound)
                values = [item.get(bound) for item in responses]
                if (type(total) in (float, int) and all(type(v) in (float, int)
                                                       for v in values) and
                        abs(sum(values) - total) > 1e-6):
                    raise ValueError("complete cost claim has inconsistent response cost")
    return data


def _quality(path: Path, run: dict) -> dict:
    if not path.is_file():
        return {"status": "missing", "quality_pass": None}
    value = json.loads(path.read_text(encoding="utf-8"))
    final_events = [event for event in (run.get("stage") or {}).get("events", [])
                    if event.get("kind") == "accepted-final"]
    if (value.get("kind") != "hidden-final" or value.get("round") != 2 or
            value.get("manifest_sha256") != file_sha(HERE / "manifest.json") or
            len(final_events) != 1 or
            value.get("candidate_patch_sha256") != final_events[0].get("patch_sha256")):
        raise ValueError("final quality receipt does not bind accepted patch")
    return {"status": "reviewed" if type(value.get("quality_pass")) is bool else "pending-review",
            "quality_pass": value.get("quality_pass"), "grade_sha256": file_sha(path)}


def collect(pair: Path, output: Path) -> dict:
    spec = manifest()
    descriptor = spec.get("pilot") if isinstance(spec.get("pilot"), dict) else {}
    if descriptor.get("schema_version") == 4:
        raise ValueError("standalone plan cannot enter paired collector")
    if descriptor.get("schema_version") == 5 and descriptor.get("path") == "pilot-plan-v15.json":
        return collect_recovery(pair, output)
    treatment_path = pair / "treatment" / "run.json"
    treatment_receipt = json.loads(treatment_path.read_text(encoding="utf-8"))
    if (not isinstance(treatment_receipt, dict) or
            treatment_receipt.get("benchmark_mode") == "standalone-feasibility" or
            treatment_receipt.get("mode") == "standalone-feasibility" or
            treatment_receipt.get("claim_class") == "standalone-treatment-feasibility"):
        raise ValueError("standalone treatment cannot enter paired collector")
    fresh_directory(output)
    baseline = _arm(pair / "baseline" / "run.json", "baseline")
    treatment = _arm(pair / "treatment" / "run.json", "treatment")
    if baseline.get("mode") == "mock-dry-run" or treatment.get("mode") == "mock-dry-run":
        b_reveals = [event["assets"] for event in baseline["events"] if event["kind"] == "reveal"]
        t_reveals = [event["assets"] for event in treatment["events"] if event["kind"] == "reveal"]
        if b_reveals != t_reveals:
            raise ValueError("dry-run reveal drift")
        status = "mock-protocol-only"
        ratio = None
    else:
        if baseline.get("parent_thread_id") == treatment.get("parent_thread_id"):
            raise ValueError("arms share parent lineage")
        qualities = {"baseline": _quality(pair / "baseline" / "grade" / "grade.json", baseline),
                     "treatment": _quality(pair / "treatment" / "grade" / "grade.json", treatment)}
        if (any(arm.get("interruption_proof") is not None for arm in (baseline, treatment)) or
                not all(arm.get("stop_reason") == "accepted-final"
                        for arm in (baseline, treatment))):
            status = "incomplete"
        elif not all(arm.get("cost_status") == "complete" for arm in (baseline, treatment)):
            status = "usage-unknown"
        elif not all(value["status"] == "reviewed" for value in qualities.values()):
            status = "quality-pending"
        elif not all(value["quality_pass"] for value in qualities.values()):
            status = "quality-failed"
        else:
            status = "quality-parity-pending-independent-review"
        if treatment.get("packet_scope_mode") == DIAGNOSTIC and status == \
                "quality-parity-pending-independent-review":
            status = "diagnostic-feasibility-pending-independent-review"
        b_cost = (baseline.get("usage") or {}).get("estimated_usd")
        t_cost = (treatment.get("usage") or {}).get("estimated_usd")
        ratio = (t_cost / b_cost if status == "quality-parity-pending-independent-review"
                 and type(b_cost) in (float, int) and b_cost > 0 and
                 type(t_cost) in (float, int) else None)
    result = {"schema_version": 1, "task_id": TASK_ID, "status": status,
              "manifest_sha256": file_sha(HERE / "manifest.json"),
              "baseline_run_sha256": file_sha(pair / "baseline" / "run.json"),
              "treatment_run_sha256": file_sha(pair / "treatment" / "run.json"),
              "cost_ratio_before_quality": ratio,
              "quality_parity": (True if status == "quality-parity-pending-independent-review"
                                 else None), "stable_latency_claim": False,
              "critical_path": "UNKNOWN without complete trusted parent/child tool spans",
              "caveat": "Mock transcripts prove only protocol mechanics; live capability and complete controls remain separate gates."}
    write_json_new(output / "pair.json", result)
    return result


def collect_recovery(pair: Path, output: Path) -> dict:
    """Emit descriptive matched costs only after both complete quality passes."""
    from evals.long_horizon_v1.run import pilot_plan
    from evals.long_horizon_v1.recovery_quality import verify_recovery_arm_quality
    spec = manifest()
    plan = pilot_plan(spec)
    prepared = HERE / plan["preparation_root"]
    preparation = json.loads((prepared / "preparation.json").read_text(encoding="utf-8"))
    preparation_sha = file_sha(prepared / "preparation.json")
    if (preparation.get("fixture_manifest_sha256") != file_sha(HERE / "manifest.json") or
            list(preparation.get("arms", {})) != plan["arm_order"] or
            len({preparation["arms"][arm].get("start_tree") for arm in plan["arm_order"]}) != 1 or
            preparation["arms"]["baseline"].get("revealed") !=
                preparation["arms"]["treatment"].get("revealed") or
            any(preparation["arms"][arm].get("start_tree") != spec["start_tree"]
                for arm in plan["arm_order"])):
        raise ValueError("paired recovery fresh preparation missing or mismatched")
    runs, quality = {}, {}
    for arm in plan["arm_order"]:
        expected_run = HERE / plan["run_outputs"][arm] / "run.json"
        supplied_run = pair / arm / "run.json"
        if file_sha(supplied_run) != file_sha(expected_run):
            raise ValueError(f"{arm} paired run differs from frozen output")
        run = _arm(expected_run, arm)
        if (run.get("claim_class") != "matched-empirical-scope-unknown" or
                run.get("runtime_binding", {}).get("preparation_sha256") != preparation_sha or
                run.get("benchmark_mode") is not None or
                run.get("stop_reason") != "accepted-final" or
                run.get("cost_status") != "complete" or run.get("usage_issues") != [] or
                run.get("interruption_proof") is not None):
            raise ValueError(f"{arm} paired recovery completion or usage incomplete")
        usage = run.get("usage") if isinstance(run.get("usage"), dict) else {}
        if (type(usage.get("estimated_usd")) not in (int, float) or
                type(usage.get("estimated_usd_upper_bound")) not in (int, float) or
                not 0 <= usage["estimated_usd"] <= usage["estimated_usd_upper_bound"] or
                usage.get("unknown_models") or usage.get("unknown_usage")):
            raise ValueError(f"{arm} reconciled observed cost missing")
        runs[arm] = run
        quality[arm] = verify_recovery_arm_quality(prepared, plan, run, arm,
                                                  require_wall=True)
    baseline, treatment = runs["baseline"], runs["treatment"]
    if (baseline.get("parent_thread_id") == treatment.get("parent_thread_id") or
            not baseline.get("shared_initial_prompt_sha256") or
            baseline["shared_initial_prompt_sha256"] != treatment.get("shared_initial_prompt_sha256") or
            [event.get("assets") for event in baseline["stage"]["events"]
             if event.get("kind") == "reveal"] !=
            [event.get("assets") for event in treatment["stage"]["events"]
             if event.get("kind") == "reveal"] or
            treatment.get("packet_scope_mode") != DIAGNOSTIC or
            (treatment.get("fork_policy") or {}).get("packet_scope") != "UNKNOWN"):
        raise ValueError("paired recovery prompt, reveal, lineage, or packet scope mismatch")
    b_cost, t_cost = (runs[arm]["usage"]["estimated_usd"] for arm in plan["arm_order"])
    result = {"schema_version": 2, "mode": "paired-recovery", "task_id": TASK_ID,
        "pilot_id": plan["pilot_id"], "status": "matched-empirical-quality-pass-scope-unknown",
        "manifest_sha256": file_sha(HERE / "manifest.json"),
        "pilot_plan_sha256": file_sha(HERE / spec["pilot"]["path"]),
        "preparation_sha256": preparation_sha,
        "run_sha256": {arm: file_sha(HERE / plan["run_outputs"][arm] / "run.json")
                       for arm in plan["arm_order"]},
        "quality_evidence_sha256": {arm: quality[arm]["end_to_end_sha256"]
                                    for arm in plan["arm_order"]},
        "estimated_usd": {"baseline": b_cost, "treatment": t_cost},
        "treatment_to_baseline_cost_ratio": t_cost / b_cost if b_cost > 0 else None,
        "end_to_end_wall_seconds": {arm: quality[arm]["end_to_end_wall_seconds"]
                                    for arm in plan["arm_order"]},
        "quality_parity": True, "packet_scope": "UNKNOWN",
        "technical_context_isolation_claim": False,
        "stable_latency_claim": False, "general_savings_claim": False,
        "interpretation": "One fresh pair; quality and total observed estimated USD are descriptive. "
                          "Effective child plaintext scope is unverified."}
    fresh_directory(output)
    write_json_new(output / "pair.json", result)
    return result


def collect_standalone(prepared: Path, output: Path) -> dict:
    """Reduce one treatment without constructing comparative metrics."""
    from evals.long_horizon_v1.run import pilot_plan
    from evals.long_horizon_v1.standalone_quality import verify_standalone_quality
    spec = manifest()
    plan = pilot_plan(spec)
    if plan.get("mode") != "standalone-feasibility":
        raise ValueError("standalone collector requires explicit standalone plan")
    if prepared.resolve() != (HERE / plan["preparation_root"]).resolve():
        raise ValueError("standalone preparation path drift")
    evidence = HERE / plan["evidence_root"]
    run_path = HERE / plan["run_outputs"]["treatment"] / "run.json"
    run = _arm(run_path, "treatment")
    if (run.get("benchmark_mode") != "standalone-feasibility" or
            run.get("claim_class") != "standalone-treatment-feasibility"):
        raise ValueError("standalone run mode missing or forged")
    usage = run.get("usage") if isinstance(run.get("usage"), dict) else {}
    cost_complete = (run.get("cost_status") == "complete" and
        run.get("usage_issues") == [] and
        type(usage.get("estimated_usd")) in (int, float) and
        type(usage.get("estimated_usd_upper_bound")) in (int, float) and
        0 <= usage["estimated_usd"] <= usage["estimated_usd_upper_bound"] and
        not usage.get("unknown_models") and not usage.get("unknown_usage"))
    accepted = (run.get("stop_reason") == "accepted-final" and
                run.get("interruption_proof") is None)
    quality = None
    if accepted and cost_complete:
        try:
            quality = verify_standalone_quality(prepared, plan, run)
        except (ValueError, OSError, KeyError, TypeError):
            quality = None
    status = ("PASS" if quality is not None else "UNKNOWN")
    result = {"schema_version": 1, "mode": "standalone-feasibility",
        "task_id": TASK_ID, "pilot_id": plan["pilot_id"], "status": status,
        "completion": "accepted-final" if accepted else "UNKNOWN",
        "quality": "PASS" if quality is not None else "UNKNOWN",
        "packet_scope": (run.get("fork_policy") or {}).get("packet_scope", "UNKNOWN"),
        "cost_status": "complete" if cost_complete else "UNKNOWN",
        "estimated_usd": usage.get("estimated_usd") if cost_complete else None,
        "runner_wall_seconds": run.get("wall_seconds"),
        "end_to_end_wall_seconds": quality.get("end_to_end_wall_seconds") if quality else None,
        "manifest_sha256": file_sha(HERE / "manifest.json"),
        "pilot_plan_sha256": file_sha(HERE / spec["pilot"]["path"]),
        "treatment_run_sha256": file_sha(run_path),
        "quality_evidence_sha256": quality.get("sha256") if quality else None}
    fresh_directory(output)
    write_json_new(output / "standalone.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--pair", type=Path)
    source.add_argument("--standalone-prepared", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = (collect_standalone(args.standalone_prepared.resolve(), args.output.resolve())
              if args.standalone_prepared else collect(args.pair.resolve(), args.output.resolve()))
    print(json.dumps(result, indent=2))
