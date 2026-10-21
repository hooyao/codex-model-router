"""One bounded paid cancellation probe; default invocation only prints the plan.

This is a capability test, never a benchmark arm or a live-enable switch.
Run it only after separate authorization for this exact paid canary.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
import queue
import re
import secrets
import shutil
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from evals.long_horizon_v1.accounting import CanarySessionMeter, account
from evals.long_horizon_v1.common import manifest, write_json_new
from evals.long_horizon_v1.prepare import verify_prepared
from evals.long_horizon_v1.run import _canonical_sha
from evals.long_horizon_v1.runtime_binding import (arm_execution, verify_arm_config,
    verify_arm_runtime, verify_cli)
from evals.long_horizon_v1.transport import AppServerTransport, TransportError
from evals.scripts.run_paired_arm import SessionMeter, native_lineage

MAX_SECONDS = 180
STOP_USD = 1.0
ACTIVE_WINDOW_SECONDS = 90
CLEANUP_RESERVE_SECONDS = 35
DRAIN_SECONDS = 20
RECEIPT_RESERVE_SECONDS = 15
RECEIPT_WRITE_RESERVE_SECONDS = 2
READY_MARKER = "CANARY_FIRST_COMMAND_READY_"
RUNNING_MARKER = "CANARY_SECOND_COMMAND_RUNNING_"
RUNNING_FILE = ".canary-second-command-running-"


class CompletedTargetError(TransportError):
    """The canary target can no longer establish an in-flight interrupt."""


def plan(arm: str) -> dict:
    policy = arm_execution(manifest(), arm)
    return {"schema_version": 1, "kind": "cancellation-capability-plan",
            "arm": arm, "parent_model": policy["model"],
            "parent_effort": policy["effort"],
            "child_required": arm == "treatment",
            "required_child_selector": {"model": "gpt-6-astra", "effort": "xhigh"}
                if arm == "treatment" else None,
            "maximum_wall_seconds": MAX_SECONDS, "stop_usd": STOP_USD,
            "active_child_wait_seconds": ACTIVE_WINDOW_SECONDS,
            "operational_stop_seconds": MAX_SECONDS - CLEANUP_RESERVE_SECONDS,
            "drain_seconds": DRAIN_SECONDS,
            "receipt_reserve_seconds": RECEIPT_RESERVE_SECONDS,
            "success": "turn/interrupt acknowledged, parent turn completed, and every observed descendant terminated with reconciled usage",
            "failure": "UNKNOWN receipt; no live pilot or quality/cost claim",
            "spending": "--run --approved-budget-usd 1.0 requires separate authorization"}


def _prompt(arm: str, attempt_id: str) -> str:
    ready = READY_MARKER + attempt_id
    running = RUNNING_MARKER + attempt_id
    marker_file = RUNNING_FILE + attempt_id
    task = ("This is a cancellation capability test. In your first foreground terminal "
            "command, run Python that prints '" + ready + "' with flush=True "
            "and exits successfully. Wait for that command to finish. In a separate "
            "second foreground terminal command, run Python that writes exactly '" +
            running + "' to '" + marker_file + "' in the current working "
            "directory, prints '" + running + "' with flush=True, then sleeps "
            "for 30 seconds. Do not end the second command early. Do not read or edit "
            "benchmark source files.")
    if arm == "baseline":
        return task
    return ("Call collaboration.spawn_agent exactly once with task_name "
            "cancellation_canary_gpt_6_astra_xhigh, model gpt-6-astra, "
            "reasoning_effort xhigh, fork_turns none. Give the child this task: " + task +
            " Wait for the child result. Do not spawn another child.")


def check_canary_budget(meter: SessionMeter, operational_deadline: float) -> None:
    """Reject a turn or continued wait before unaccounted spend can accumulate."""
    if time.monotonic() >= operational_deadline:
        raise TransportError("canary operational wall limit")
    meter.refresh()
    if meter.unknown_models or meter.unknown_usage:
        raise TransportError("canary usage integrity failure")
    if meter.cost_upper >= STOP_USD:
        raise TransportError("canary $1 stop threshold")


def _command_events(path: Path, attempt_id: str) -> dict:
    """Read one rollout in line order; never treat a partial tail as proof."""
    ready, running = READY_MARKER + attempt_id, RUNNING_MARKER + attempt_id
    evidence = {"first_call_id": None, "first_done_line": None,
                "priced_line": None, "second_call_id": None,
                "second_call_line": None, "second_completed": False,
                "target_terminal": False}
    with path.open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.endswith("\n"):
                break
            try:
                event = json.loads(line)
            except ValueError:
                raise TransportError("canary rollout contains invalid JSON")
            payload = event.get("payload") or {}
            if not isinstance(payload, dict):
                raise TransportError("canary rollout event payload is malformed")
            if event.get("type") == "event_msg":
                if payload.get("type") == "token_count" and evidence["first_done_line"]:
                    if isinstance((payload.get("info") or {}).get("last_token_usage"), dict):
                        evidence["priced_line"] = line_no
                if payload.get("type") in ("turn_aborted", "task_complete",
                                            "task_completed", "task_failed",
                                            "task_cancelled", "task_cancel"):
                    evidence["target_terminal"] = True
                if (payload.get("type") == "item_completed" and
                        (payload.get("item") or {}).get("type") == "CommandExecution" and
                        evidence["second_call_line"]):
                    evidence["second_completed"] = True
            if event.get("type") != "response_item":
                continue
            if payload.get("type") == "custom_tool_call" and payload.get("name") == "exec":
                if ready in str(payload.get("input", "")):
                    if evidence["first_call_id"]:
                        raise TransportError("duplicate canary readiness command")
                    evidence["first_call_id"] = payload.get("call_id")
                if running in str(payload.get("input", "")):
                    if not evidence["first_done_line"] or not evidence["priced_line"]:
                        raise TransportError("second canary command preceded priced readiness")
                    if evidence["second_call_id"]:
                        raise TransportError("duplicate second canary command")
                    evidence["second_call_id"] = payload.get("call_id")
                    evidence["second_call_line"] = line_no
            if payload.get("type") == "custom_tool_call_output":
                if (payload.get("call_id") == evidence["first_call_id"] and
                        ready in str(payload.get("output", ""))):
                    evidence["first_done_line"] = line_no
                if payload.get("call_id") == evidence["second_call_id"]:
                    evidence["second_completed"] = True
    if (evidence["first_call_id"] is not None and
            not isinstance(evidence["first_call_id"], str)) or (
            evidence["second_call_id"] is not None and
            not isinstance(evidence["second_call_id"], str)):
        raise TransportError("canary command call identity missing")
    return evidence


def _baseline_runtime_abort(path: Path, parent_id: str, turn_id: str,
                            attempt_id: str, second_call_id: str,
                            second_call_line: int, dispatch_ns: int) -> dict:
    """Accept only the runtime's result for the interrupted parent command."""
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    if (not rows or rows[0].get("type") != "session_meta" or
            (rows[0].get("payload") or {}).get("id") != parent_id or
            not 1 <= second_call_line <= len(rows)):
        raise TransportError("baseline parent rollout identity is uncertain")
    command = rows[second_call_line - 1].get("payload") or {}
    command_input = str(command.get("input", ""))
    if (command.get("type") != "custom_tool_call" or
            command.get("name") != "exec" or
            command.get("call_id") != second_call_id or
            RUNNING_MARKER + attempt_id not in command_input or
            RUNNING_FILE + attempt_id not in command_input or
            "time.sleep(30)" not in command_input or
            "aborted by user" in command_input):
        raise TransportError("baseline running command identity is uncertain")
    outputs = [(line_no, event) for line_no, event in enumerate(rows, 1) if
        event.get("type") == "response_item" and
        (event.get("payload") or {}).get("type") == "custom_tool_call_output" and
        (event.get("payload") or {}).get("call_id") == second_call_id]
    if len(outputs) != 1 or outputs[0][0] <= second_call_line:
        raise TransportError("baseline runtime abort result is missing or duplicated")
    output_line, output_event = outputs[0]
    payload = output_event.get("payload") or {}
    metadata = payload.get("internal_chat_message_metadata_passthrough") or {}
    match = re.fullmatch(r"aborted by user after (\d+(?:\.\d+)?)s",
                         payload.get("output") if isinstance(payload.get("output"), str) else "")
    created = metadata.get("create_time")
    if (metadata.get("turn_id") != turn_id or match is None or
            Decimal(match.group(1)) >= 30 or
            not isinstance(created, (int, float))):
        raise TransportError("baseline second command did not return a runtime abort")
    output_ns = int(Decimal(str(created)) * 1_000_000_000)
    def timestamp_window_ns(event: dict) -> tuple[int, int]:
        value = event.get("timestamp")
        if not isinstance(value, str) or not value.endswith("Z"):
            raise TransportError("baseline cancellation timestamp is ambiguous")
        whole, _, fraction = value[:-1].partition(".")
        if fraction and (not fraction.isdigit() or len(fraction) > 9):
            raise TransportError("baseline cancellation timestamp precision is invalid")
        seconds = int(datetime.fromisoformat(whole + "+00:00").timestamp())
        precision = 10 ** (9 - len(fraction))
        start = seconds * 1_000_000_000 + int(fraction.ljust(9, "0") or "0")
        return start, start + precision
    output_window = timestamp_window_ns(output_event)
    if (output_ns <= dispatch_ns or
            not output_window[0] <= output_ns < output_window[1] or
            dispatch_ns >= output_window[1]):
        raise TransportError("baseline command output preceded interrupt dispatch")
    terminals = [(line_no, event) for line_no, event in enumerate(rows, 1) if
        event.get("type") == "event_msg" and
        (event.get("payload") or {}).get("type") == "turn_aborted" and
        (event.get("payload") or {}).get("turn_id") == turn_id and
        (event.get("payload") or {}).get("reason") == "interrupted"]
    if (len(terminals) != 1 or terminals[0][0] <= output_line or
            timestamp_window_ns(terminals[0][1])[0] <= output_ns):
        raise TransportError("baseline interrupted terminal is missing or early")
    return {"parent_thread_id": parent_id, "turn_id": turn_id,
            "attempt_id": attempt_id, "second_call_id": second_call_id,
            "second_call_line": second_call_line, "runtime_abort_output_line": output_line,
            "runtime_abort_created_ns": output_ns,
            "runtime_abort_event_window_ns": list(output_window),
            "interrupted_terminal_line": terminals[0][0],
            "interrupt_dispatch_ns": dispatch_ns}


def cancellation_target_ready(meter: SessionMeter, parent_id: str, turn_id: str,
                              arm: str, workspace: Path, attempt_id: str) -> dict | None:
    """Require exact lineage, priced response, and a live second command."""
    children = [(path, state) for path, state in meter.paths.items()
                if state["id"] != parent_id]
    if arm == "baseline":
        if children:
            raise TransportError("baseline child appeared during canary")
        candidates = [(path, state) for path, state in meter.paths.items()
                      if state["id"] == parent_id]
    else:
        if len(children) > 1:
            raise TransportError("canary child lineage is not unique")
        candidates = children
    if len(candidates) != 1:
        return False
    path, state = candidates[0]
    expected = ("gpt-6-astra", "xhigh") if arm == "treatment" else (
        meter.parent_model, meter.parent_effort)
    turns = state.get("turns") or []
    if not turns:
        return False
    if (state.get("model"), state.get("effort")) != expected or len(turns) != 1 or \
            (turns[0].get("model"), turns[0].get("effort")) != expected:
        raise TransportError("canary target selector or turn is uncertain")
    child_turn_id = turns[0].get("turn_id")
    if not isinstance(child_turn_id, str) or not child_turn_id:
        return False
    if arm == "baseline" and child_turn_id != turn_id:
        raise TransportError("canary parent turn identity changed")
    if arm == "treatment":
        parent_paths = [p for p, s in meter.paths.items() if s["id"] == parent_id]
        if len(parent_paths) != 1:
            return False
        attempts, issues = native_lineage(parent_paths[0], meter.paths, parent_id)
        if issues or len(attempts) != 1:
            return False
        attempt = attempts[0]
        if (attempt.get("child_id") != state["id"] or
                attempt.get("turn_id") != turn_id or
                attempt.get("model") != expected[0] or
                attempt.get("effort") != expected[1]):
            raise TransportError("canary child lineage differs from active turn")
    if state.get("terminal") is not None:
        raise CompletedTargetError("canary target finished before interruption")
    if state.get("calls", 0) < 1 or not isinstance(state.get("last_total_usage"), dict):
        return False
    before = _command_events(path, attempt_id)
    if before["second_completed"] or before["target_terminal"]:
        raise CompletedTargetError("second canary command completed before interruption")
    if not before["second_call_id"]:
        return None
    marker_path = workspace / (RUNNING_FILE + attempt_id)
    if not marker_path.is_file() or marker_path.read_text(encoding="utf-8") != RUNNING_MARKER + attempt_id:
        return None
    marker_observed_ns = time.time_ns()
    after = _command_events(path, attempt_id)
    if after["second_completed"] or after["target_terminal"]:
        raise CompletedTargetError("second canary command completed before interruption")
    if (after["first_call_id"] != before["first_call_id"] or
            after["second_call_id"] != before["second_call_id"] or
            after["second_call_line"] != before["second_call_line"]):
        raise TransportError("canary command identity changed during marker read")
    return {"attempt_id": attempt_id, "target_thread_id": state["id"],
            "target_turn_id": child_turn_id,
            "first_call_id": after["first_call_id"],
            "first_done_line": after["first_done_line"],
            "priced_line": after["priced_line"],
            "second_call_id": after["second_call_id"],
            "second_call_line": after["second_call_line"],
            "marker_observed_ns": marker_observed_ns,
            "completion_absent_checked_ns": time.time_ns()}


def start_canary_turn(transport: AppServerTransport, meter: SessionMeter,
                      parent_id: str, arm: str, operational_deadline: float,
                      workspace: Path, model: str, effort: str,
                      attempt_id: str = "local-test") -> str:
    """Use transport's preturn and in-flight guards for the one paid turn."""
    def budget() -> None:
        check_canary_budget(meter, operational_deadline)
    transport._guard_turn(budget)
    transport.turn_started_at = time.monotonic()
    transport.active_turn_complete = False
    request_id = transport._send("turn/start", {"threadId": parent_id,
        "input": [{"type": "text", "text": _prompt(arm, attempt_id)}],
        "model": model, "effort": effort,
        "cwd": str(workspace), "sandboxPolicy": {"type": "dangerFullAccess"},
        "approvalPolicy": "never"})
    response = transport._response(request_id, budget)
    transport._guard_turn(budget)
    turn = response.get("turn") or {}
    turn_id = turn.get("id")
    if not isinstance(turn_id, str) or not turn_id:
        raise TransportError("canary turn identity missing")
    transport.active_turn_id = turn_id
    return turn_id


def account_bounded(sessions: Path, parent_id: str, model: str, effort: str,
                    treatment: bool, absolute_deadline: float) -> dict:
    """Give reconciliation the remaining cleanup time and fail closed on timeout."""
    remaining = absolute_deadline - time.monotonic() - RECEIPT_WRITE_RESERVE_SECONDS
    if remaining <= 0:
        raise TimeoutError("canary accounting deadline exhausted")
    result: queue.Queue[tuple[dict | None, Exception | None]] = queue.Queue(maxsize=1)
    def reconcile() -> None:
        try:
            result.put((account(sessions, parent_id, model, effort, treatment), None))
        except Exception as error:
            result.put((None, error))
    worker = threading.Thread(target=reconcile, daemon=True)
    worker.start()
    worker.join(remaining)
    if worker.is_alive():
        raise TimeoutError("canary accounting deadline exhausted")
    if result.empty():
        raise RuntimeError("canary accounting ended without a result")
    observed, error = result.get_nowait()
    if error is not None:
        raise error
    return observed


def run(arm: str, prepared: Path, output: Path, sessions: Path,
        approved_budget_usd: float) -> dict:
    if approved_budget_usd != STOP_USD:
        raise ValueError("exact $1.00 canary budget acknowledgement required")
    spec = manifest()
    cli = Path(spec["runtime"]["cli"])
    verify_cli(cli, spec)
    verify_prepared(prepared, arm, spec)
    if output.exists():
        raise ValueError("canary output already exists; no automatic retry")
    output.mkdir(parents=True)
    workspace = output / "workspace"
    shutil.copytree(prepared / arm, workspace)
    if arm == "treatment":
        verify_arm_config(workspace, spec)
    policy = arm_execution(spec, arm)
    runtime = verify_arm_runtime(cli, workspace, spec, arm)
    start = time.monotonic()
    absolute_deadline = start + MAX_SECONDS
    operational_deadline = absolute_deadline - CLEANUP_RESERVE_SECONDS
    drain_deadline = absolute_deadline - RECEIPT_RESERVE_SECONDS
    transport = None
    meter = None
    parent_id = None
    turn_id = None
    attempt_id = secrets.token_hex(12)
    trigger_evidence = None
    unsafe_to_interrupt = False
    child_seen = False
    post_interrupt_evidence = None
    cancellation = {"status": "UNKNOWN", "reason": "turn not started"}
    cancellation_attempted = False
    failure = None
    try:
        transport = AppServerTransport(cli, workspace, policy["cli_options"],
                                       policy["model"], policy["effort"],
                                       operational_deadline)
        parent_id = transport.start()
        meter = CanarySessionMeter(sessions, parent_id, policy["model"], policy["effort"],
                             require_native_activity=(arm == "treatment"))
        turn_id = start_canary_turn(transport, meter, parent_id, arm,
                                    operational_deadline, workspace,
                                    policy["model"], policy["effort"], attempt_id)
        while time.monotonic() - start < ACTIVE_WINDOW_SECONDS:
            transport._guard_turn(lambda: check_canary_budget(meter, operational_deadline))
            trigger_evidence = cancellation_target_ready(
                meter, parent_id, turn_id, arm, workspace, attempt_id)
            if trigger_evidence:
                child_seen = arm == "treatment"
                break
            transport._frame()
            transport._guard_turn(lambda: check_canary_budget(meter, operational_deadline))
            if transport.active_turn_complete:
                raise TransportError("canary turn finished before cancellation target")
        else:
            raise TransportError("ready priced cancellation target not observed within 90 seconds")
        target_interrupt_evidence = None
        def before_interrupt(thread: str, target_turn: str) -> dict:
            nonlocal target_interrupt_evidence
            if (arm == "treatment" and thread == parent_id and
                    target_interrupt_evidence is not None):
                if target_turn != turn_id or transport.active_turn_complete:
                    raise TransportError("canary parent turn ended before interrupt")
                return target_interrupt_evidence
            meter.refresh()
            current = cancellation_target_ready(
                meter, parent_id, turn_id, arm, workspace, attempt_id)
            if not current or current["second_call_id"] != trigger_evidence["second_call_id"]:
                raise TransportError("canary target lost exact command identity before interrupt")
            expected_target = (parent_id if arm == "baseline" else current["target_thread_id"])
            if (thread, target_turn) not in ((parent_id, turn_id),
                                              (expected_target, current["target_turn_id"])):
                raise TransportError("canary interrupt target identity changed")
            if arm == "treatment" and thread == expected_target:
                target_interrupt_evidence = current
            return current
        cancellation_attempted = True
        cancellation = transport.cancel_and_drain(meter, timeout=DRAIN_SECONDS,
            absolute_deadline=drain_deadline, before_interrupt=before_interrupt,
            descendants_first=(arm == "treatment"))
    except (TransportError, RuntimeError, OSError, ValueError) as error:
        failure = str(error)
        unsafe_to_interrupt = isinstance(error, CompletedTargetError)
        if cancellation_attempted and cancellation.get("status") == "UNKNOWN":
            cancellation = {"status": "UNKNOWN", "reason": f"cancellation failed: {error}"}
    finally:
        if transport is not None:
            try:
                if (not transport.active_turn_complete and not cancellation_attempted and
                        not unsafe_to_interrupt):
                    cancellation_attempted = True
                    cancellation = transport.cancel_and_drain(meter, timeout=DRAIN_SECONDS,
                                                               absolute_deadline=drain_deadline)
            except (RuntimeError, OSError, ValueError) as error:
                cancellation = {"status": "UNKNOWN", "reason": f"cancellation failed: {error}"}
                failure = failure or str(error)
            try:
                transport.close()
            except (RuntimeError, OSError) as error:
                failure = failure or f"transport close failed: {error}"
    observed = None
    if meter is not None and parent_id is not None:
        try:
            observed = account_bounded(sessions, parent_id, policy["model"],
                                       policy["effort"], arm == "treatment",
                                       absolute_deadline)
        except (RuntimeError, OSError, ValueError, TimeoutError) as error:
            failure = failure or f"accounting failed: {error}"
    if time.monotonic() >= absolute_deadline:
        failure = failure or "canary 180-second total wall limit exceeded during cleanup"
    if (trigger_evidence and cancellation.get("interrupt_requests") and
            meter is not None and parent_id is not None and turn_id is not None):
        # The target is expected to be terminal now. Re-read command completion
        # after transport close; any completed second command invalidates ordering.
        paths = [path for path, state in meter.paths.items()
                 if state["id"] == trigger_evidence["target_thread_id"]]
        if len(paths) != 1:
            failure = failure or "canary target rollout identity unavailable after interrupt"
        else:
            try:
                final_events = _command_events(paths[0], attempt_id)
                post_interrupt_evidence = {"target_thread_id": trigger_evidence["target_thread_id"],
                    "second_call_id": final_events["second_call_id"],
                    "second_completed": final_events["second_completed"],
                    "rollout_checked_ns": time.time_ns()}
                if final_events["second_call_id"] != trigger_evidence["second_call_id"]:
                    failure = failure or "canary command completion or identity changed across interrupt"
                elif final_events["second_completed"] and arm == "baseline":
                    requests = cancellation.get("interrupt_requests") or []
                    if (len(requests) != 1 or
                            requests[0].get("thread_id") != parent_id or
                            requests[0].get("turn_id") != turn_id or
                            type(requests[0].get("dispatch_time_ns")) is not int):
                        failure = failure or "baseline interrupt dispatch identity is uncertain"
                    else:
                        post_interrupt_evidence["runtime_abort"] = _baseline_runtime_abort(
                            paths[0], parent_id, turn_id, attempt_id,
                            trigger_evidence["second_call_id"],
                            trigger_evidence["second_call_line"],
                            requests[0]["dispatch_time_ns"])
                elif final_events["second_completed"]:
                    failure = failure or "canary command completion or identity changed across interrupt"
            except (OSError, TransportError, ValueError, TypeError, ArithmeticError):
                failure = failure or "canary command order cannot be established after interrupt"
    usage = observed["summary"] if observed else meter.summary() if meter else None
    child_sessions = [entry for entry in (usage or {}).get("sessions", [])
                      if entry["id"] != parent_id]
    native_attempts = observed["native_attempts"] if observed else None
    lineage_ok = (native_attempts == [] if arm == "baseline" else
        isinstance(native_attempts, list) and len(native_attempts) == 1 and
        isinstance(native_attempts[0], dict) and
        len(child_sessions) == 1 and
        native_attempts[0].get("child_id") == child_sessions[0]["id"] and
        native_attempts[0].get("turn_id") == turn_id and
        native_attempts[0].get("model") == "gpt-6-astra" and
        native_attempts[0].get("effort") == "xhigh")
    accounting_issues = observed["issues"] if observed else ["accounting unavailable"]
    expected_cancel_issue = {"parent session lacks successful terminal result"}
    baseline_usage_ok = (arm != "baseline" or (
        accounting_issues == ["parent session lacks successful terminal result"] and
        isinstance(usage, dict) and len(usage.get("sessions", [])) == 1 and
        usage.get("unknown_models") == [] and usage.get("unknown_usage") == [] and
        usage.get("model_calls") == usage["sessions"][0].get("calls") and
        usage["sessions"][0].get("id") == parent_id and
        usage["sessions"][0].get("model") == "gpt-6-astra" and
        usage["sessions"][0].get("effort") == "xhigh" and
        usage["sessions"][0].get("terminal") == "turn_aborted" and
        type(usage["sessions"][0].get("calls")) is int and
        usage["sessions"][0]["calls"] > 0 and
        isinstance(usage["sessions"][0].get("reported_total_usage"), dict)))
    requests = cancellation.get("interrupt_requests")
    request_order_ok = (isinstance(requests, list) and
        len(requests) == (2 if arm == "treatment" else 1) and
        trigger_evidence is not None and all(
            isinstance(row.get("pre_dispatch_evidence"), dict) and
            row["pre_dispatch_evidence"].get("attempt_id") == attempt_id and
            row["pre_dispatch_evidence"].get("second_call_id") ==
                trigger_evidence["second_call_id"] and
            row["pre_dispatch_evidence"].get("marker_observed_ns", 0) <=
                row["pre_dispatch_evidence"].get("completion_absent_checked_ns", 0) <
                row.get("dispatch_time_ns", 0) for row in requests))
    verified = (failure is None and
        not (set(accounting_issues) - expected_cancel_issue) and
        lineage_ok and
        baseline_usage_ok and
        (arm != "baseline" or (policy["model"] == "gpt-6-astra" and
                                policy["effort"] == "xhigh" and
                                isinstance((post_interrupt_evidence or {}).get("runtime_abort"), dict))) and
        request_order_ok and
        isinstance((usage or {}).get("estimated_usd_upper_bound"), (float, int)) and
        usage["estimated_usd_upper_bound"] < STOP_USD and
        cancellation.get("status") == "verified-drained" and
        (child_seen and len(child_sessions) == 1 if arm == "treatment" else not child_sessions))
    receipt = {"schema_version": 1, "kind": "cancellation-capability",
        "status": "verified" if verified else "UNKNOWN", "arm": arm,
        "model": policy["model"], "effort": policy["effort"],
        "parent_thread_id": parent_id, "turn_id": turn_id,
        "runtime_binding_sha256": _canonical_sha(runtime),
        "runtime_binding": runtime, "cancellation": cancellation,
        "trigger_evidence": trigger_evidence,
        "post_interrupt_evidence": post_interrupt_evidence,
        "real_child_observed": child_seen, "usage": usage,
        "native_attempts": native_attempts,
        "accounting_issues": accounting_issues,
        "failure": failure, "wall_seconds": round(time.monotonic() - start, 3),
        "live_enabled": spec["live_enabled"]}
    write_json_new(output / "cancellation.json", receipt)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", required=True, choices=("baseline", "treatment"))
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--approved-budget-usd", type=float)
    parser.add_argument("--prepared", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--sessions", type=Path)
    args = parser.parse_args()
    if not args.run:
        print(json.dumps(plan(args.arm), indent=2))
    else:
        if args.prepared is None or args.output is None or args.sessions is None:
            parser.error("--run requires --prepared, --output, and --sessions")
        print(json.dumps(run(args.arm, args.prepared.resolve(), args.output.resolve(),
                             args.sessions.resolve(), args.approved_budget_usd), indent=2))
