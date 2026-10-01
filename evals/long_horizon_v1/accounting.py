"""Reconcile every parent/child response into per-turn and per-model costs."""
from __future__ import annotations

from pathlib import Path

from evals.scripts.run_paired_arm import RATES, SessionMeter, native_lineage
from .common import manifest
from .fork_policy import validate_observed_spawns


class CanarySessionMeter(SessionMeter):
    """Retain the canary meter API; base parsing binds abort to current turn."""


def _proof_usage_matches(proof: dict | None, summary: dict) -> bool:
    """Bind an interrupt receipt to this fresh meter pass, not a saved estimate."""
    if not isinstance(proof, dict) or not isinstance(proof.get("usage"), dict):
        return False
    usage = proof["usage"]
    top = ("model_calls", "estimated_usd_lower_bound",
           "estimated_usd_upper_bound", "token_classes", "unknown_models",
           "unknown_usage")
    if any(usage.get(key) != summary.get(key) for key in top):
        return False
    rows = usage.get("sessions")
    if not isinstance(rows, list) or len(rows) != len(summary["sessions"]):
        return False
    observed = {row.get("id"): row for row in rows if isinstance(row, dict)}
    if len(observed) != len(rows):
        return False
    for current in summary["sessions"]:
        prior = observed.get(current["id"])
        if not isinstance(prior, dict) or any(prior.get(key) != current.get(key)
                for key in ("calls", "terminal", "reported_total_usage", "model",
                            "effort", "token_classes", "estimated_usd_lower_bound",
                            "estimated_usd_upper_bound")):
            return False
    return True


def account(session_root: Path, parent_id: str, model: str, effort: str,
            treatment: bool, interruption_proof: dict | None = None,
            fork_policy_plans: Path | None = None,
            packet_scope_mode: str = "diagnostic-feasibility") -> dict:
    price = manifest()["pricing"]["per_million"]
    if any(tuple(values) != RATES.get(name) for name, values in price.items()):
        raise ValueError("rate card differs from meter")
    meter = CanarySessionMeter(session_root, parent_id, model, effort,
                         require_native_activity=treatment)
    meter.refresh()
    summary = meter.summary()
    issues = meter.integrity_issues()
    for state in meter.paths.values():
        if state["terminal"] == "turn_aborted":
            missing = f"session {state['id']} lacks terminal result"
            issues = [issue for issue in issues if issue != missing]
            if state["id"] == parent_id:
                issues.append("parent session lacks successful terminal result")
    parent_paths = [path for path, state in meter.paths.items() if state["id"] == parent_id]
    native_attempts = []
    lineage_issues = []
    if treatment and len(parent_paths) == 1:
        native_attempts, lineage_issues = native_lineage(
            parent_paths[0], meter.paths, parent_id, closed_snapshot=True,
            interruption_proof=interruption_proof)
        issues.extend(lineage_issues)
        if fork_policy_plans is not None:
            _, fork_issues = validate_observed_spawns(
                parent_paths[0], parent_id, native_attempts,
                closed_snapshot=True, plans=fork_policy_plans,
                child_rollouts={state["id"]: path for path, state in meter.paths.items()
                                if state["id"] != parent_id}, mode=packet_scope_mode)
            issues.extend(fork_issues)
    child_ledger = {item.get("child_id"): item.get("turns", [])
                    for item in native_attempts if isinstance(item.get("child_id"), str)}
    if treatment and len(child_ledger) != len(native_attempts):
        issues.append("native child session identity missing or duplicated")
    responses = []
    for state in meter.paths.values():
        turns = state["turns"]
        if state["id"] != parent_id and treatment:
            ledger = child_ledger.get(state["id"])
            if ledger is None or len(ledger) != len(turns):
                issues.append(f"child {state['id']} closed turn ledger incomplete")
            else:
                for observed, native in zip(turns, ledger):
                    closure = (native.get("closure") == "completed" and
                               "completion_line" in native or
                               native.get("closure") == "interrupted" and
                               native.get("terminal") == "turn_aborted" and
                               "completion_line" not in native)
                    if ((observed.get("turn_id"), observed.get("model"),
                            observed.get("effort"), observed.get("terminal")) != (
                            native.get("turn_id"), native.get("model"),
                            native.get("effort"), native.get("terminal")) or
                            "terminal_line" not in native or not closure):
                        issues.append(f"child {state['id']} turn differs from native ledger")
                    if observed.get("calls", 0) == 0:
                        issues.append(f"child {state['id']} turn lacks priced response")
        for response in state["responses"]:
            turn_id = response.get("turn_id")
            if (not isinstance(turn_id, str) or not turn_id or
                    sum(turn.get("turn_id") == turn_id for turn in turns) != 1 or
                    response.get("model") not in RATES or not response.get("effort")):
                issues.append(f"{state['id']}: response missing turn or selector")
            if meter.response_ids and not response.get("response_id_sha256"):
                issues.append(f"{state['id']}: response lacks raw identity")
            if state["id"] != parent_id and treatment and meter.response_ids:
                linked = [turn for turn in child_ledger.get(state["id"], [])
                          if turn.get("turn_id") == turn_id]
                if (len(linked) != 1 or response.get("root_turn_id") !=
                        linked[0].get("parent_turn_id")):
                    issues.append(f"{state['id']}: raw response root turn differs from ledger")
            responses.append({"session_id": state["id"], "parent_id": state["parent_id"],
                              **response})
    interrupted = any(turn.get("terminal") == "turn_aborted"
                      for state in meter.paths.values() for turn in state["turns"])
    proof_matches = (_proof_usage_matches(interruption_proof, summary)
                     if interrupted and treatment else False)
    if interrupted and treatment and interruption_proof is not None and not proof_matches:
        issues.append("interruption proof usage differs from fresh meter")
    interruption_verified = (interrupted and treatment and proof_matches and
            not lineage_issues and
            any(turn.get("closure") == "interrupted"
                for attempt in native_attempts for turn in attempt.get("turns", [])) and
            all(turn.get("terminal") != "turn_aborted" or
                turn.get("closure") == "interrupted"
                for attempt in native_attempts for turn in attempt.get("turns", [])))
    if interruption_verified:
        issues = [issue for issue in issues
                  if issue != "parent session lacks successful terminal result"]
    if len(responses) != summary["model_calls"]:
        issues.append("per-turn response count differs from cumulative meter")
    if abs(sum(item["estimated_usd_lower_bound"] for item in responses) -
           summary["estimated_usd_lower_bound"]) > 1e-6:
        issues.append("per-turn cost differs from cumulative meter")
    by_model = {}
    for item in responses:
        group = by_model.setdefault(item["model"], {"responses": 0,
            "estimated_usd_lower_bound": 0.0, "estimated_usd_upper_bound": 0.0})
        group["responses"] += 1
        group["estimated_usd_lower_bound"] += item["estimated_usd_lower_bound"]
        group["estimated_usd_upper_bound"] += item["estimated_usd_upper_bound"]
    for group in by_model.values():
        for key in ("estimated_usd_lower_bound", "estimated_usd_upper_bound"):
            group[key] = round(group[key], 9)
    failed_children = sum(turn.get("terminal") not in ("task_complete", "task_completed")
                          for state in meter.paths.values() if state["id"] != parent_id
                          for turn in state["turns"])
    return {"status": "complete" if not issues else "UNKNOWN",
            "summary": summary, "responses": responses, "by_model": by_model,
            "native_attempts": native_attempts,
            "interruption_verified": interruption_verified and not issues,
            "failed_child_attempts": failed_children, "issues": sorted(set(issues))}
