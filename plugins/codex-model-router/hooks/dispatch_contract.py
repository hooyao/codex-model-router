#!/usr/bin/env python3
"""Fail-closed preflight for one native worker dispatch.

The contract deliberately keeps planned packet identity, native transport,
selector arguments, runtime capability evidence, and eventual worker/runtime
observations separate.  It authorizes a dispatch; it does not claim that the
runtime honored the request.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from subagent_naming import build_subagent_name, native_task_name


PLACEHOLDER = re.compile(
    r"(?:^|[-_])(unknown|unavailable|unexposed|unresolved|placeholder|default|auto|none|null)(?:$|[-_])",
    re.IGNORECASE,
)
EVIDENCE_KINDS = {"spawn_schema", "model_catalog", "inheritance_contract"}
SELECTION_MODES = {"explicit", "verified_inheritance"}


class DispatchContractError(ValueError):
    """The planned dispatch is not supported by captured runtime evidence."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DispatchContractError(message)


def exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    missing = expected - set(value)
    extra = set(value) - expected
    require(not missing and not extra, f"{label} keys differ: missing={sorted(missing)}, extra={sorted(extra)}")


def resolved(value: Any, field: str) -> str:
    require(isinstance(value, str) and bool(value.strip()), f"{field} must be a non-empty string")
    value = value.strip()
    require(not PLACEHOLDER.search(value), f"{field} contains an unresolved placeholder")
    return value


def validate_evidence(items: Any) -> dict[str, dict[str, Any]]:
    require(isinstance(items, list) and items, "capability_evidence must be a non-empty list")
    result: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(items):
        require(isinstance(item, dict), f"capability_evidence[{index}] must be an object")
        exact_keys(item, {"id", "kind", "source", "sha256", "captured_at"}, f"capability_evidence[{index}]")
        evidence_id = resolved(item["id"], "capability evidence id")
        require(evidence_id not in result, f"duplicate capability evidence id: {evidence_id}")
        require(item["kind"] in EVIDENCE_KINDS, f"unsupported capability evidence kind: {item['kind']}")
        resolved(item["source"], "capability evidence source")
        require(isinstance(item["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", item["sha256"]),
                f"capability evidence {evidence_id} needs a lowercase SHA-256")
        resolved(item["captured_at"], "capability evidence captured_at")
        result[evidence_id] = item
    return result


def validate_dispatch_contract(value: Any) -> dict[str, Any]:
    """Validate and return a normalized dispatch-contract-v1 record."""
    require(isinstance(value, dict), "dispatch contract must be an object")
    exact_keys(value, {
        "schema_version", "dispatch_id", "purpose", "canonical_name", "packet",
        "native_dispatch", "selection", "capability_evidence",
    }, "dispatch contract")
    require(value["schema_version"] == 1, "schema_version must be 1")
    resolved(value["dispatch_id"], "dispatch_id")
    purpose = resolved(value["purpose"], "purpose")
    evidence = validate_evidence(value["capability_evidence"])

    selection = value["selection"]
    require(isinstance(selection, dict), "selection must be an object")
    exact_keys(selection, {"mode", "model", "reasoning_effort", "evidence_refs"}, "selection")
    require(selection["mode"] in SELECTION_MODES, "selection.mode must be explicit or verified_inheritance")
    model = resolved(selection["model"], "selection.model")
    effort = resolved(selection["reasoning_effort"], "selection.reasoning_effort")
    refs = selection["evidence_refs"]
    require(isinstance(refs, list) and refs and all(isinstance(item, str) for item in refs),
            "selection.evidence_refs must be a non-empty string array")
    require(all(ref in evidence for ref in refs), "selection references missing capability evidence")
    referenced_kinds = {evidence[ref]["kind"] for ref in refs}

    native = value["native_dispatch"]
    require(isinstance(native, dict), "native_dispatch must be an object")
    exact_keys(native, {"tool", "naming_field", "supported_arguments", "native_name", "schema_evidence_ref"},
               "native_dispatch")
    resolved(native["tool"], "native_dispatch.tool")
    require(native["schema_evidence_ref"] in evidence and
            evidence[native["schema_evidence_ref"]]["kind"] == "spawn_schema",
            "native dispatch must reference spawn_schema evidence")
    arguments = native["supported_arguments"]
    require(isinstance(arguments, list) and len(arguments) == len(set(arguments)) and
            all(isinstance(item, str) and item for item in arguments),
            "supported_arguments must be a unique non-empty string array")
    require(native["naming_field"] in ("name", "task_name", None),
            "naming_field must be name, task_name, or null")
    if native["naming_field"] is None:
        require(native["native_name"] == "unavailable", "unnamed native transport must record unavailable")
    else:
        require(native["naming_field"] in arguments, "naming field is absent from captured spawn schema")

    if selection["mode"] == "explicit":
        require({"model", "reasoning_effort"}.issubset(arguments),
                "explicit selectors are absent from captured spawn schema")
        require("model_catalog" in referenced_kinds,
                "explicit selection requires model_catalog evidence")
    else:
        require("inheritance_contract" in referenced_kinds,
                "verified inheritance requires inheritance_contract evidence")

    canonical = build_subagent_name(purpose, model, effort)
    require(value["canonical_name"] == canonical, f"canonical_name must equal {canonical}")
    packet = value["packet"]
    require(isinstance(packet, dict), "packet must be an object")
    exact_keys(packet, {"worker_name", "task_id", "native_task_name"}, "packet")
    require(packet["worker_name"] == canonical and packet["task_id"] == canonical,
            "packet worker_name and task_id must equal canonical_name")
    expected_native = (canonical if native["naming_field"] == "name" else
                       native_task_name(canonical) if native["naming_field"] == "task_name" else
                       "unavailable")
    require(native["native_name"] == expected_native and packet["native_task_name"] == expected_native,
            f"native and packet names must equal {expected_native}")
    return value


def capability_blocker(dispatch_id: str, errors: list[str]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "dispatch_id": dispatch_id,
        "status": "blocked",
        "blocker_kind": "runtime-capability-evidence",
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="JSON file; stdin is used when omitted")
    args = parser.parse_args()
    try:
        text = args.input.read_text(encoding="utf-8") if args.input else __import__("sys").stdin.read()
        value = json.loads(text)
        validate_dispatch_contract(value)
        output = {"schema_version": 1, "dispatch_id": value["dispatch_id"], "status": "ready",
                  "contract_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
        code = 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        dispatch_id = value.get("dispatch_id", "unresolved") if isinstance(locals().get("value"), dict) else "unresolved"
        output = capability_blocker(dispatch_id, [str(error)])
        code = 1
    print(json.dumps(output, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
