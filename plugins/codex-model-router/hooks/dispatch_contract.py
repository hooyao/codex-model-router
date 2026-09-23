#!/usr/bin/env python3
"""Fail-closed, source-backed preflight for one native worker dispatch."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from subagent_naming import build_subagent_name, native_task_name

PLACEHOLDER = re.compile(
    r"(?:^|[-_])(unknown|unavailable|unexposed|unresolved|placeholder|default|auto|none|null)(?:$|[-_])",
    re.IGNORECASE,
)
EVIDENCE_KINDS = {"spawn_schema", "model_catalog", "inheritance_contract"}
SELECTION_MODES = {"explicit", "verified_inheritance"}
SHA256 = re.compile(r"[0-9a-f]{64}")


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


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        require(key not in value, f"duplicate JSON key: {key}")
        value[key] = item
    return value


def parse_json(text: str, label: str) -> Any:
    try:
        return json.loads(text, object_pairs_hook=_pairs)
    except json.JSONDecodeError as error:
        raise DispatchContractError(f"invalid JSON in {label}: {error}") from error


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _captured_at(value: Any, label: str) -> None:
    timestamp = resolved(value, label)
    try:
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as error:
        raise DispatchContractError(f"{label} must be an ISO-8601 timestamp") from error


def _string_array(value: Any, label: str) -> list[str]:
    require(isinstance(value, list) and value and all(isinstance(item, str) and item for item in value),
            f"{label} must be a non-empty string array")
    require(len(value) == len(set(value)), f"{label} must not contain duplicates")
    return value


def _validate_source(kind: str, value: Any, evidence_id: str) -> dict[str, Any]:
    require(isinstance(value, dict), f"capability source {evidence_id} must be an object")
    require(value.get("schema_version") == 1 and value.get("kind") == kind,
            f"capability source {evidence_id} has the wrong schema or kind")
    if kind == "spawn_schema":
        exact_keys(value, {"schema_version", "kind", "tool", "supported_arguments"},
                   f"capability source {evidence_id}")
        resolved(value["tool"], f"capability source {evidence_id} tool")
        _string_array(value["supported_arguments"], f"capability source {evidence_id} supported_arguments")
    elif kind == "model_catalog":
        exact_keys(value, {"schema_version", "kind", "models"}, f"capability source {evidence_id}")
        require(isinstance(value["models"], list) and value["models"],
                f"capability source {evidence_id} models must be a non-empty array")
        ids: set[str] = set()
        for index, model in enumerate(value["models"]):
            require(isinstance(model, dict), f"model catalog entry {index} must be an object")
            exact_keys(model, {"id", "reasoning_efforts"}, f"model catalog entry {index}")
            model_id = resolved(model["id"], f"model catalog entry {index} id")
            require(model_id not in ids, f"duplicate model catalog id: {model_id}")
            ids.add(model_id)
            _string_array(model["reasoning_efforts"], f"model catalog entry {index} reasoning_efforts")
    else:
        exact_keys(value, {"schema_version", "kind", "tool", "inherits", "when_omitted"},
                   f"capability source {evidence_id}")
        resolved(value["tool"], f"capability source {evidence_id} tool")
        require(value["when_omitted"] is True,
                f"capability source {evidence_id} must explicitly apply when selectors are omitted")
        require(isinstance(value["inherits"], dict), f"capability source {evidence_id} inherits must be an object")
        exact_keys(value["inherits"], {"model", "reasoning_effort"},
                   f"capability source {evidence_id} inherits")
        resolved(value["inherits"]["model"], f"capability source {evidence_id} inherited model")
        resolved(value["inherits"]["reasoning_effort"],
                 f"capability source {evidence_id} inherited reasoning effort")
    return value


def validate_evidence(items: Any, source_root: Path | None = None) -> dict[str, dict[str, Any]]:
    """Open, hash, parse, and validate every claimed capability source."""
    require(isinstance(items, list) and items, "capability_evidence must be a non-empty list")
    result: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(items):
        require(isinstance(item, dict), f"capability_evidence[{index}] must be an object")
        exact_keys(item, {"id", "kind", "source", "sha256", "captured_at"}, f"capability_evidence[{index}]")
        evidence_id = resolved(item["id"], "capability evidence id")
        require(evidence_id not in result, f"duplicate capability evidence id: {evidence_id}")
        kind = item["kind"]
        require(kind in EVIDENCE_KINDS, f"unsupported capability evidence kind: {kind}")
        source = Path(resolved(item["source"], "capability evidence source"))
        if not source.is_absolute():
            require(source_root is not None, f"relative capability evidence source requires a source root: {source}")
            source = source_root / source
        expected_hash = item["sha256"]
        require(isinstance(expected_hash, str) and SHA256.fullmatch(expected_hash) is not None and
                expected_hash != "0" * 64, f"capability evidence {evidence_id} needs a nonzero lowercase SHA-256")
        _captured_at(item["captured_at"], f"capability evidence {evidence_id} captured_at")
        require(source.is_file(), f"capability evidence source does not exist: {source}")
        require(source.stat().st_size > 0, f"capability evidence source is empty: {source}")
        require(file_sha256(source) == expected_hash, f"capability evidence hash mismatch: {source}")
        parsed = _validate_source(kind, parse_json(source.read_text(encoding="utf-8"), str(source)), evidence_id)
        result[evidence_id] = {"record": item, "path": source.resolve(), "value": parsed}
    return result


def validate_dispatch_contract(value: Any, source_root: Path | None = None) -> dict[str, Any]:
    """Validate a dispatch-contract-v1 using facts derived from source evidence."""
    require(isinstance(value, dict), "dispatch contract must be an object")
    exact_keys(value, {"schema_version", "dispatch_id", "purpose", "canonical_name", "packet",
                       "native_dispatch", "selection", "capability_evidence"}, "dispatch contract")
    require(value["schema_version"] == 1, "schema_version must be 1")
    resolved(value["dispatch_id"], "dispatch_id")
    purpose = resolved(value["purpose"], "purpose")
    evidence = validate_evidence(value["capability_evidence"], source_root)

    selection = value["selection"]
    require(isinstance(selection, dict), "selection must be an object")
    exact_keys(selection, {"mode", "model", "reasoning_effort", "evidence_refs"}, "selection")
    require(selection["mode"] in SELECTION_MODES, "selection.mode must be explicit or verified_inheritance")
    model = resolved(selection["model"], "selection.model")
    effort = resolved(selection["reasoning_effort"], "selection.reasoning_effort")
    refs = _string_array(selection["evidence_refs"], "selection.evidence_refs")
    require(all(ref in evidence for ref in refs), "selection references missing capability evidence")

    native = value["native_dispatch"]
    require(isinstance(native, dict), "native_dispatch must be an object")
    exact_keys(native, {"tool", "naming_field", "native_name", "schema_evidence_ref"}, "native_dispatch")
    tool = resolved(native["tool"], "native_dispatch.tool")
    schema_ref = native["schema_evidence_ref"]
    require(schema_ref in evidence and evidence[schema_ref]["record"]["kind"] == "spawn_schema",
            "native dispatch must reference spawn_schema evidence")
    schema = evidence[schema_ref]["value"]
    require(schema["tool"] == tool, "native tool contradicts spawn-schema evidence")
    arguments = schema["supported_arguments"]
    require(native["naming_field"] in ("name", "task_name", None), "naming_field must be name, task_name, or null")
    if native["naming_field"] is None:
        require(native["native_name"] == "unavailable", "unnamed native transport must record unavailable")
    else:
        require(native["naming_field"] in arguments, "naming field is absent from captured spawn schema")

    selected = [evidence[ref] for ref in refs]
    if selection["mode"] == "explicit":
        require({"model", "reasoning_effort"}.issubset(arguments),
                "explicit selectors are absent from captured spawn schema")
        catalogs = [item["value"] for item in selected if item["record"]["kind"] == "model_catalog"]
        require(catalogs, "explicit selection requires model_catalog evidence")
        matches = [entry for catalog in catalogs for entry in catalog["models"] if entry["id"] == model]
        require(matches, f"selected model is absent from model catalog: {model}")
        require(any(effort in entry["reasoning_efforts"] for entry in matches),
                f"reasoning effort {effort} is unsupported for model {model}")
    else:
        contracts = [item["value"] for item in selected if item["record"]["kind"] == "inheritance_contract"]
        require(contracts, "verified inheritance requires inheritance_contract evidence")
        require(all(item["tool"] == tool for item in contracts), "inheritance contract is for a different tool")
        require(any(item["inherits"] == {"model": model, "reasoning_effort": effort} for item in contracts),
                "selected model/effort contradict verified inheritance evidence")

    canonical = build_subagent_name(purpose, model, effort)
    require(value["canonical_name"] == canonical, f"canonical_name must equal {canonical}")
    packet = value["packet"]
    require(isinstance(packet, dict), "packet must be an object")
    exact_keys(packet, {"worker_name", "task_id", "native_task_name"}, "packet")
    require(packet["worker_name"] == canonical and packet["task_id"] == canonical,
            "packet worker_name and task_id must equal canonical_name")
    expected_native = (canonical if native["naming_field"] == "name" else
                       native_task_name(canonical) if native["naming_field"] == "task_name" else "unavailable")
    require(native["native_name"] == expected_native and packet["native_task_name"] == expected_native,
            f"native and packet names must equal {expected_native}")
    return value


def capability_blocker(dispatch_id: str, errors: list[str]) -> dict[str, Any]:
    return {"schema_version": 1, "dispatch_id": dispatch_id, "status": "blocked",
            "blocker_kind": "runtime-capability-evidence", "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="JSON file; stdin is used when omitted")
    args = parser.parse_args()
    try:
        text = args.input.read_text(encoding="utf-8") if args.input else sys.stdin.read()
        value = parse_json(text, str(args.input or "stdin"))
        validate_dispatch_contract(value, args.input.resolve().parent if args.input else Path.cwd())
        output = {"schema_version": 1, "dispatch_id": value["dispatch_id"], "status": "ready",
                  "contract_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
        code = 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        dispatch_id = value.get("dispatch_id", "unresolved") if isinstance(locals().get("value"), dict) else "unresolved"
        output = capability_blocker(dispatch_id, [str(error)])
        code = 1
    print(json.dumps(output, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
