"""Persistent App Server JSON-RPC transport; no model calls in module import."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time


class TransportError(RuntimeError):
    pass


DRAIN_REFRESH_SECONDS = 0.25
DRAIN_QUIET_SECONDS = 2.0
TERMINAL_STATES = frozenset(("task_complete", "task_completed", "task_failed",
                             "task_cancelled", "task_cancel", "turn_aborted"))


def meter_relevant_frame(event: dict) -> bool:
    """Notifications that may add priced usage or change session lineage."""
    method = event.get("method")
    if method in ("thread/tokenUsage/updated", "thread/started",
                  "thread/status/changed", "turn/started", "turn/completed"):
        return True
    if method not in ("item/started", "item/completed"):
        return False
    params = event.get("params")
    item = params.get("item") if isinstance(params, dict) else None
    return isinstance(item, dict) and (bool(item.get("agentThreadId")) or
                                        item.get("type") == "collabToolCall")


class AppServerTransport:
    """Keep one process and thread across all commissioning turns.

    This adapter is deliberately fail-closed at the runner preflight. A fresh
    runtime probe must validate these method shapes, selector echo, multi-turn
    completion, and child usage lineage before `run.py --live` will call it.
    """

    def __init__(self, cli: Path, workspace: Path, options: list[str],
                 model: str, effort: str, deadline: float,
                 env: dict[str, str] | None = None):
        self.model, self.effort = model, effort
        self.workspace, self.deadline = workspace, deadline
        self.process = subprocess.Popen([str(cli), *options, "app-server", "--stdio"],
                                        cwd=workspace, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        env=env if env is not None else os.environ.copy())
        self.messages: queue.Queue[bytes] = queue.Queue(maxsize=1024)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self.request_id = 0
        self.thread_id: str | None = None
        self.events: list[dict] = []
        self.active_turn_id: str | None = None
        self.turn_started_at: float | None = None
        self.active_turn_complete = True

    def _read(self) -> None:
        while True:
            line = self.process.stdout.readline(1024 * 1024 + 1)
            self.messages.put(line)
            if not line or len(line) > 1024 * 1024:
                return

    def _frame(self, *, drain_deadline: float | None = None,
               max_wait: float = 1.0) -> dict:
        remaining = (drain_deadline if drain_deadline is not None else self.deadline) - time.monotonic()
        if remaining <= 0:
            raise TransportError("global wall limit")
        try:
            line = self.messages.get(timeout=min(remaining, max_wait))
        except queue.Empty:
            if self.process.poll() is not None:
                raise TransportError("app-server exited")
            return {}
        if not line:
            raise TransportError("app-server stream ended")
        if len(line) > 1024 * 1024:
            raise TransportError("oversized app-server frame")
        try:
            value = json.loads(line)
        except ValueError as error:
            raise TransportError("invalid app-server frame") from error
        if not isinstance(value, dict):
            raise TransportError("invalid app-server message")
        # Retain shape and digest only. Raw tool output and prompts may contain
        # private context and do not belong in the published receipt.
        params = value.get("params") if isinstance(value.get("params"), dict) else {}
        turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
        item = params.get("item") if isinstance(params.get("item"), dict) else {}
        self.events.append({"method": value.get("method"), "id": value.get("id"),
                            "thread_id": params.get("threadId"),
                            "turn_id": turn.get("id") or params.get("turnId"),
                            "item_id": item.get("id"), "item_type": item.get("type"),
                            "child_thread_id": item.get("agentThreadId"),
                            "sha256": hashlib.sha256(line).hexdigest(),
                            "bytes": len(line), "time_ns": time.time_ns()})
        if (value.get("method") == "turn/started" and
                params.get("threadId") == self.thread_id and
                isinstance(turn.get("id"), str)):
            self.active_turn_id = turn["id"]
        if (value.get("method") == "turn/completed" and
                params.get("threadId") == self.thread_id and
                turn.get("id") == self.active_turn_id):
            self.active_turn_complete = True
        return value

    def _send(self, method: str, params: dict, *, notification: bool = False) -> int | None:
        request_id = None
        if not notification:
            self.request_id += 1
            request_id = self.request_id
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        if request_id is not None:
            payload["id"] = request_id
        line = (json.dumps(payload, separators=(",", ":")) + "\n").encode()
        self.process.stdin.write(line)
        self.process.stdin.flush()
        return request_id

    def _response(self, request_id: int, budget_check=None, frame_check=None) -> dict:
        while True:
            if budget_check is not None:
                self._guard_turn(budget_check)
            value = self._frame()
            if not value:
                continue
            if frame_check is not None:
                frame_check(value)
            if value.get("id") != request_id:
                continue
            if "error" in value or not isinstance(value.get("result"), dict):
                raise TransportError("app-server request failed")
            return value["result"]

    def _guard_turn(self, budget_check) -> None:
        if time.monotonic() >= self.deadline:
            raise TransportError("global wall limit")
        budget_check()
        # A usage refresh can take time; check again immediately before work.
        if time.monotonic() >= self.deadline:
            raise TransportError("global wall limit")

    def start(self) -> str:
        init = self._send("initialize", {"clientInfo": {"name": "long-horizon-evaluator", "version": "1"}})
        self._response(init)
        self._send("initialized", {}, notification=True)
        start = self._send("thread/start", {"cwd": str(self.workspace), "model": self.model,
                                            "sandbox": "danger-full-access",
                                            "approvalPolicy": "never", "ephemeral": False})
        result = self._response(start)
        if result.get("model") not in (None, self.model):
            raise TransportError("parent model selector changed at thread start")
        thread = result.get("thread", {})
        self.thread_id = thread.get("id") if isinstance(thread, dict) else result.get("threadId")
        if not isinstance(self.thread_id, str) or not self.thread_id:
            raise TransportError("parent thread identity missing")
        return self.thread_id

    def turn(self, prompt: str, budget_check, frame_check=None) -> dict:
        if self.thread_id is None:
            raise TransportError("thread not started")
        self._guard_turn(budget_check)
        self.turn_started_at = time.monotonic()
        self.active_turn_id = None
        self.active_turn_complete = False
        request_id = self._send("turn/start", {"threadId": self.thread_id,
                                               "input": [{"type": "text", "text": prompt}],
                                               "model": self.model, "effort": self.effort,
                                               "cwd": str(self.workspace),
                                               "sandboxPolicy": {"type": "dangerFullAccess"},
                                               "approvalPolicy": "never"})
        result = (self._response(request_id, budget_check, frame_check)
                  if frame_check is not None else self._response(request_id, budget_check))
        turn = result.get("turn", {})
        turn_id = turn.get("id") if isinstance(turn, dict) else result.get("turnId")
        if not isinstance(turn_id, str) or not turn_id:
            raise TransportError("turn identity missing")
        self.active_turn_id = turn_id
        while True:
            self._guard_turn(budget_check)
            event = self._frame()
            if not event:
                continue
            if frame_check is not None:
                frame_check(event)
            if event.get("method") == "turn/completed":
                params = event.get("params") or {}
                observed = params.get("turn") or {}
                if isinstance(observed, dict) and observed.get("id") == turn_id:
                    if params.get("threadId") != self.thread_id:
                        raise TransportError("completed turn parent identity missing or changed")
                    self.active_turn_complete = True
                    return {"turn_id": turn_id, "thread_id": self.thread_id,
                            "status": observed.get("status")}

    def cancel_and_drain(self, meter=None, timeout: float = 15.0,
                         absolute_deadline: float | None = None,
                         before_interrupt=None, descendants_first: bool = False) -> dict:
        """Attempt supported turn interruption and retain proof of descendant drain.

        This does not assert that a parent interrupt cancels native descendants.
        Only a separate real-child capability test can establish that behavior.
        """
        end = time.monotonic() + timeout
        if absolute_deadline is not None:
            end = min(end, absolute_deadline)
        receipt = {"status": "UNKNOWN", "turn_id": self.active_turn_id,
                   "interrupt_ack": False, "turn_completed": self.active_turn_complete,
                   "descendants_drained": False, "usage_drained": False,
                   "interrupted_threads": [], "interrupt_requests": []}
        if end <= time.monotonic():
            receipt["reason"] = "cancellation deadline exhausted"
            return receipt
        if not self.thread_id or (not self.active_turn_complete and not self.active_turn_id):
            receipt["reason"] = "active turn identity unavailable"
            return receipt
        try:
            pending: dict[int, tuple[str, str]] = {}
            request_rows: dict[int, dict] = {}
            acknowledged = set()
            interrupted: set[tuple[str, str]] = set()
            next_refresh = time.monotonic()
            quiet_since: float | None = None
            identity_issue: str | None = None
            children: list[dict] = []
            while time.monotonic() < end:
                now = time.monotonic()
                if meter is not None and now >= next_refresh:
                    meter.refresh()
                    states = list(meter.paths.values())
                    children = [state for state in states
                                if state.get("id") != self.thread_id]
                    by_id = {state.get("id"): state for state in states}
                    counts = Counter(state.get("id") for state in states)
                    duplicate_ids = {session_id for session_id, count in counts.items()
                                     if count > 1}
                    active_children = []
                    identity_issue = ("descendant identity duplicated"
                                      if duplicate_ids else None)
                    for state in children:
                        if state.get("id") in duplicate_ids:
                            continue
                        turns = state.get("turns") or []
                        current = turns[-1] if turns else {}
                        parent_id = state.get("parent_id")
                        if parent_id is not None:
                            seen = {state.get("id")}
                            lineage_valid = True
                            while parent_id != self.thread_id:
                                if (parent_id in seen or parent_id in duplicate_ids or
                                        parent_id not in by_id):
                                    identity_issue = "descendant lineage differs from parent"
                                    lineage_valid = False
                                    break
                                seen.add(parent_id)
                                parent_id = by_id[parent_id].get("parent_id")
                            if not lineage_valid:
                                continue
                        closed = (state.get("terminal") in TERMINAL_STATES and
                                  ("terminal" not in current or
                                   current["terminal"] == state["terminal"]))
                        if closed:
                            continue
                        child_id, turn_id = state.get("id"), current.get("turn_id")
                        if (not isinstance(child_id, str) or not child_id or
                                not isinstance(turn_id, str) or not turn_id):
                            identity_issue = "active descendant turn identity unavailable"
                            continue
                        active_children.append((child_id, turn_id))
                    # Initial child-first ordering is preserved. A child that
                    # starts after the parent interrupt is still targeted on
                    # its exact current turn at the next meter refresh.
                    targets = ([] if self.active_turn_complete else
                               [(self.thread_id, self.active_turn_id)])
                    if descendants_first:
                        targets = active_children + targets
                    else:
                        targets.extend(active_children)
                    for thread, turn in targets:
                        if (thread, turn) in interrupted or time.monotonic() >= end:
                            continue
                        evidence = before_interrupt(thread, turn) if before_interrupt else None
                        if (thread != self.thread_id and before_interrupt is not None and
                                evidence is None):
                            identity_issue = "active descendant pre-dispatch identity unavailable"
                            continue
                        if evidence is not None and not isinstance(evidence, dict):
                            raise TransportError("canary interrupt order cannot be established")
                        if (thread != self.thread_id and evidence is not None and
                                (evidence.get("target_thread_id"),
                                 evidence.get("target_turn_id")) != (thread, turn)):
                            raise TransportError("descendant pre-dispatch identity changed")
                        dispatch_ns = time.time_ns()
                        if evidence is not None and (
                                type(evidence.get("marker_observed_ns")) is not int or
                                type(evidence.get("completion_absent_checked_ns")) is not int or
                                not evidence["marker_observed_ns"] <=
                                    evidence["completion_absent_checked_ns"] < dispatch_ns):
                            raise TransportError("canary interrupt order cannot be established")
                        if time.monotonic() >= end:
                            break
                        request_id = self._send("turn/interrupt", {"threadId": thread,
                                                                    "turnId": turn})
                        if type(request_id) is not int or request_id in pending:
                            raise TransportError("interrupt request identity unavailable")
                        interrupted.add((thread, turn))
                        pending[request_id] = (thread, turn)
                        receipt["interrupted_threads"].append(thread)
                        row = {"thread_id": thread,
                            "turn_id": turn, "dispatch_time_ns": dispatch_ns,
                            "pre_dispatch_evidence": evidence, "acknowledged": False}
                        request_rows[request_id] = row
                        receipt["interrupt_requests"].append(row)
                    coverage = meter.dispatch_coverage_issues()
                    receipt["descendants_drained"] = (not coverage and not identity_issue and
                        all(state.get("terminal") in TERMINAL_STATES and
                            (not state.get("turns") or
                             "terminal" not in state["turns"][-1] or
                             state["turns"][-1]["terminal"] == state["terminal"])
                            for state in children))
                    for thread, turn in interrupted:
                        state = by_id.get(thread)
                        turns = (state.get("turns") or []) if state else []
                        matching = [row for row in turns if row.get("turn_id") == turn]
                        if (state is None or
                                (matching and (len(matching) != 1 or
                                 matching[0].get("terminal") not in TERMINAL_STATES and
                                 not (matching[0] is turns[-1] and
                                      state.get("terminal") in TERMINAL_STATES) or
                                 ("calls" in matching[0] and
                                  (type(matching[0]["calls"]) is not int or
                                   matching[0]["calls"] <= 0)))) or
                                (not matching and thread != self.thread_id)):
                            receipt["descendants_drained"] = False
                    receipt["usage_drained"] = (bool(states) and not meter.unknown_models and
                        not meter.unknown_usage and all(state.get("calls", 0) > 0 and
                        isinstance(state.get("last_total_usage"), dict) and
                        (not state.get("turns") or "calls" not in state["turns"][-1] or
                         state["turns"][-1]["calls"] > 0) and
                        state.get("pending_usage_record") is None and
                        (state["id"] != self.thread_id or not state.get("turns") or
                         (state["turns"][-1].get("turn_id") == self.active_turn_id and
                          state.get("terminal") in TERMINAL_STATES))
                        for state in states))
                    next_refresh = time.monotonic() + DRAIN_REFRESH_SECONDS
                receipt["interrupt_ack"] = bool(pending) and acknowledged == set(pending)
                receipt["turn_completed"] = self.active_turn_complete
                idle = (not pending and self.active_turn_complete and
                        receipt["descendants_drained"] and not children)
                closed = (receipt["interrupt_ack"] and receipt["turn_completed"] and
                          receipt["descendants_drained"] and receipt["usage_drained"])
                quiet_since = (quiet_since if (closed or idle) and quiet_since is not None else
                               time.monotonic() if closed or idle else None)
                if quiet_since is not None and time.monotonic() - quiet_since >= DRAIN_QUIET_SECONDS:
                    receipt["status"] = "verified-drained" if closed else "no-active-turn"
                    break
                try:
                    frame = self._frame(drain_deadline=end,
                        max_wait=min(DRAIN_REFRESH_SECONDS,
                                     max(0.001, next_refresh - time.monotonic())))
                except TransportError as error:
                    receipt["reason"] = str(error)
                    break
                if frame.get("id") in pending and isinstance(frame.get("result"), dict):
                    acknowledged.add(frame["id"])
                    request_rows[frame["id"]]["acknowledged"] = True
                if frame and meter_relevant_frame(frame):
                    next_refresh = time.monotonic()
        except (OSError, RuntimeError) as error:
            receipt["reason"] = str(error)
        if receipt["status"] == "UNKNOWN" and "reason" not in receipt:
            receipt["reason"] = identity_issue or "cancellation drain deadline reached"
        return receipt

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
