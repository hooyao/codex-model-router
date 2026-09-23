#!/usr/bin/env python3
"""Versioned routing-decision contract and deterministic policy resolver."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


CONTRACT_VERSION = 1
OWNERSHIPS = {"DIRECT", "DELEGATE"}
TOPOLOGIES = {"NONE", "ISOLATED_SERIAL", "PARALLEL"}
VERIFICATION_REQUIREMENTS = {"SELF_CHECK", "INDEPENDENT_REVIEW"}
PHASES = {"initial", "reclassification"}
CONFIGURED_MODES = {"direct", "delegate", "evaluate"}
ESCALATION_TRIGGERS = {
    "scope-expanded",
    "outcome-became-unbounded",
    "network-or-sync-required",
    "monitoring-required",
    "failure-or-recovery-required",
    "research-required",
    "independent-review-required",
    "write-scope-overlap-discovered",
    "dependency-discovered",
    "validation-failed",
    "permission-changed",
    "safety-constraint-changed",
}
SIGNAL_FIELDS = {
    "one_local_scope",
    "bounded_known_outcome",
    "network_or_sync",
    "long_running_or_monitoring",
    "failure_or_recovery",
    "named_multistep_runbook",
    "substantive_research_or_investigation",
    "independent_review_required",
    "high_risk",
    "multiple_bounded_tasks",
    "tasks_independent",
    "dependencies_absent",
    "write_scopes_disjoint",
    "permissions_confirmed",
    "safety_constraints_known",
    "verification_plan_present",
    "write_scope_known",
}
REQUEST_FIELDS = {
    "schema_version",
    "decision_id",
    "phase",
    "prior_ownership",
    "escalation_trigger",
    "matched_example_ids",
    "signals",
}
RESULT_FIELDS = {
    "schema_version",
    "decision_id",
    "ownership",
    "delegate_topology",
    "verification_requirement",
    "configured_mode",
    "matched_rule",
    "reasons",
    "reclassified_from",
    "escalation_trigger",
    "constraints",
}
CONSTRAINT_FIELDS = {
    "max_depth",
    "max_concurrency",
    "max_retries",
    "write_policy",
    "context_policy",
}


class DecisionContractError(ValueError):
    """Structured routing input or output violates the decision contract."""


def _exact_fields(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise DecisionContractError(f"{label} must be an object")
    missing = sorted(expected - set(value))
    extra = sorted(set(value) - expected)
    if missing or extra:
        raise DecisionContractError(f"{label} fields: missing={missing}, extra={extra}")
    return value


def _version(value: Any, label: str) -> None:
    if type(value) is not int or value != CONTRACT_VERSION:
        raise DecisionContractError(f"{label} must be integer {CONTRACT_VERSION}")


def _nonempty(value: Any, label: str) -> str:
    if type(value) is not str or not value.strip():
        raise DecisionContractError(f"{label} must be a non-empty string")
    return value


def _nullable_bool(value: Any, label: str) -> None:
    if value is not None and type(value) is not bool:
        raise DecisionContractError(f"{label} must be boolean or null")


def validate_decision_request(value: Any) -> dict[str, Any]:
    request = _exact_fields(value, REQUEST_FIELDS, "decision request")
    _version(request["schema_version"], "decision request.schema_version")
    decision_id = _nonempty(request["decision_id"], "decision request.decision_id")
    if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", decision_id) is None:
        raise DecisionContractError("decision request.decision_id must be lowercase hyphenated ASCII")
    if request["phase"] not in PHASES:
        raise DecisionContractError("decision request.phase must be initial or reclassification")
    if request["prior_ownership"] is not None and request["prior_ownership"] not in OWNERSHIPS:
        raise DecisionContractError("decision request.prior_ownership must be DIRECT, DELEGATE, or null")
    trigger = request["escalation_trigger"]
    if trigger is not None and trigger not in ESCALATION_TRIGGERS:
        raise DecisionContractError("decision request.escalation_trigger is unknown")
    if request["phase"] == "initial":
        if request["prior_ownership"] is not None or trigger is not None:
            raise DecisionContractError("initial decisions cannot declare prior ownership or escalation")
    elif request["prior_ownership"] != "DIRECT" or trigger is None:
        raise DecisionContractError("reclassification requires prior DIRECT ownership and an escalation trigger")

    route_ids = request["matched_example_ids"]
    if type(route_ids) is not list or any(type(item) is not str for item in route_ids):
        raise DecisionContractError("decision request.matched_example_ids must be a string array")
    if len(route_ids) != len(set(route_ids)):
        raise DecisionContractError("decision request.matched_example_ids must not contain duplicates")

    signals = _exact_fields(request["signals"], SIGNAL_FIELDS, "decision request.signals")
    for name, signal in signals.items():
        _nullable_bool(signal, f"decision request.signals.{name}")
    return request


def _positive_integer(value: Any, label: str) -> None:
    if type(value) is not int or value < 1:
        raise DecisionContractError(f"{label} must be a positive integer")


def _constraints(policy: dict[str, Any]) -> dict[str, Any]:
    limits = policy["limits"]
    return {
        "max_depth": limits["max_depth"],
        "max_concurrency": limits["max_concurrency"],
        "max_retries": limits["max_retries"],
        "write_policy": "exclusive-ownership-serialize-overlap",
        "context_policy": "minimal-packet-compact-receipt",
    }


def validate_decision_result(value: Any) -> dict[str, Any]:
    result = _exact_fields(value, RESULT_FIELDS, "decision result")
    _version(result["schema_version"], "decision result.schema_version")
    decision_id = _nonempty(result["decision_id"], "decision result.decision_id")
    if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", decision_id) is None:
        raise DecisionContractError("decision result.decision_id must be lowercase hyphenated ASCII")
    if result["ownership"] not in OWNERSHIPS:
        raise DecisionContractError("decision result.ownership is invalid")
    if result["delegate_topology"] not in TOPOLOGIES:
        raise DecisionContractError("decision result.delegate_topology is invalid")
    if result["verification_requirement"] not in VERIFICATION_REQUIREMENTS:
        raise DecisionContractError("decision result.verification_requirement is invalid")
    if result["configured_mode"] not in CONFIGURED_MODES:
        raise DecisionContractError("decision result.configured_mode is invalid")
    _nonempty(result["matched_rule"], "decision result.matched_rule")
    reasons = result["reasons"]
    if type(reasons) is not list or not reasons or any(type(reason) is not str or not reason for reason in reasons):
        raise DecisionContractError("decision result.reasons must be a non-empty string array")
    if result["reclassified_from"] is not None and result["reclassified_from"] != "DIRECT":
        raise DecisionContractError("decision result.reclassified_from must be DIRECT or null")
    if result["escalation_trigger"] is not None and result["escalation_trigger"] not in ESCALATION_TRIGGERS:
        raise DecisionContractError("decision result.escalation_trigger is unknown")
    constraints = _exact_fields(result["constraints"], CONSTRAINT_FIELDS, "decision result.constraints")
    for field in ("max_depth", "max_concurrency", "max_retries"):
        _positive_integer(constraints[field], f"decision result.constraints.{field}")
    if constraints["write_policy"] != "exclusive-ownership-serialize-overlap":
        raise DecisionContractError("decision result.constraints.write_policy is invalid")
    if constraints["context_policy"] != "minimal-packet-compact-receipt":
        raise DecisionContractError("decision result.constraints.context_policy is invalid")
    if result["ownership"] == "DIRECT" and result["delegate_topology"] != "NONE":
        raise DecisionContractError("DIRECT ownership requires NONE topology")
    if result["ownership"] == "DIRECT" and result["verification_requirement"] != "SELF_CHECK":
        raise DecisionContractError("DIRECT ownership requires SELF_CHECK verification")
    if result["ownership"] == "DELEGATE" and result["delegate_topology"] == "NONE":
        raise DecisionContractError("DELEGATE ownership requires a worker topology")
    if result["delegate_topology"] == "PARALLEL" and constraints["max_concurrency"] < 2:
        raise DecisionContractError("PARALLEL topology requires max_concurrency of at least 2")
    if result["reclassified_from"] == "DIRECT" and result["ownership"] != "DELEGATE":
        raise DecisionContractError("direct reclassification must resolve to DELEGATE")
    if (result["escalation_trigger"] is None) != (result["reclassified_from"] is None):
        raise DecisionContractError("reclassification and escalation trigger must be recorded together")
    return result


def resolve_routing_decision(config: dict[str, Any], value: Any) -> dict[str, Any]:
    """Resolve a validated decision without I/O, clocks, randomness, or model calls."""

    request = validate_decision_request(value)
    # Local import avoids a module cycle while keeping this module directly testable.
    from routing_config import configured_execution_mode, effective_execution_policy, validate_config

    validate_config(config, "decision config")
    policy = effective_execution_policy(config)
    configured_mode = configured_execution_mode(config, request["matched_example_ids"])
    signals = request["signals"]
    reasons: list[str] = []

    unsafe_when_true = {
        "network_or_sync": "network or synchronization is required",
        "long_running_or_monitoring": "long-running work or monitoring is required",
        "failure_or_recovery": "failure or recovery work is required",
        "named_multistep_runbook": "a named multi-step runbook is required",
        "substantive_research_or_investigation": "substantive research or investigation is required",
        "independent_review_required": "independent review is required",
        "high_risk": "high-risk work requires delegated verification",
    }
    required_true = {
        "one_local_scope": "local scope is not known to be singular",
        "bounded_known_outcome": "outcome is not known to be bounded",
        "permissions_confirmed": "permissions are not confirmed",
        "safety_constraints_known": "safety constraints are not fully known",
        "verification_plan_present": "verification plan is not present",
        "write_scope_known": "write scope is not known",
    }
    hard_delegate = False
    for field, reason in unsafe_when_true.items():
        if signals[field] is True:
            hard_delegate = True
            reasons.append(reason)
        elif signals[field] is None:
            hard_delegate = True
            reasons.append(f"{field} is unknown")
    for field, reason in required_true.items():
        if signals[field] is not True:
            hard_delegate = True
            reasons.append(reason)

    trigger = request["escalation_trigger"]
    if request["phase"] == "reclassification":
        hard_delegate = True
        reasons.insert(0, f"approved direct bounds exceeded: {trigger}")

    if hard_delegate:
        ownership, matched_rule = "DELEGATE", "hard-delegate-or-unknown-signal"
    elif configured_mode == "delegate":
        ownership, matched_rule = "DELEGATE", "configured-delegate"
        reasons.append("effective configuration mandates delegation")
    else:
        ownership, matched_rule = "DIRECT", "bounded-direct-fast-path"
        reasons.append("all bounded direct requirements are explicitly satisfied")

    if ownership == "DIRECT":
        topology = "NONE"
    else:
        parallel_fields = (
            "multiple_bounded_tasks",
            "tasks_independent",
            "dependencies_absent",
            "write_scopes_disjoint",
        )
        if (
            policy["delegate_topology"]["parallel_enabled"]
            and policy["limits"]["max_concurrency"] > 1
            and all(
                signals[field] is True for field in parallel_fields
            )
        ):
            topology = "PARALLEL"
            reasons.append("tasks are bounded, independent, dependency-free, and write-disjoint")
        else:
            topology = "ISOLATED_SERIAL"
            reasons.append("parallel safety requirements are not all explicitly satisfied")

    verification = (
        "INDEPENDENT_REVIEW"
        if signals["independent_review_required"] is not False or signals["high_risk"] is not False
        else "SELF_CHECK"
    )
    result = {
        "schema_version": CONTRACT_VERSION,
        "decision_id": request["decision_id"],
        "ownership": ownership,
        "delegate_topology": topology,
        "verification_requirement": verification,
        "configured_mode": configured_mode,
        "matched_rule": matched_rule,
        "reasons": reasons,
        "reclassified_from": request["prior_ownership"] if request["phase"] == "reclassification" else None,
        "escalation_trigger": trigger,
        "constraints": _constraints(policy),
    }
    return validate_decision_result(result)


def main(argv: list[str] | None = None) -> int:
    """Resolve one request from stdin using a validated workspace config."""

    parser = argparse.ArgumentParser(
        description="Validate and resolve one Codex Model Router decision-request-v1 JSON object."
    )
    parser.add_argument("--config", type=Path, required=True, help="Validated schema-v3 routing.json path.")
    args = parser.parse_args(argv)
    try:
        from routing_config import RoutingConfigError, load_config

        request = json.load(sys.stdin)
        config = load_config(args.config.resolve(strict=True))
        result = resolve_routing_decision(config, request)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return 0
    except (OSError, json.JSONDecodeError, DecisionContractError, RoutingConfigError, ValueError) as error:
        print(f"codex-model-router decision error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
