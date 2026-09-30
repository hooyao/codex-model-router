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
EVIDENCE_KINDS = {"spawn_schema", "model_catalog", "inheritance_contract",
                  "role_binding", "role_file", "role_runtime", "role_calibration"}
SELECTION_MODES = {"explicit", "verified_inheritance", "verified_role_config"}
REASONING_EFFORTS = {"low", "medium", "high", "xhigh", "max"}
SHA256 = re.compile(r"[0-9a-f]{64}")
MAX_EVIDENCE_BYTES = 1_048_576
ROLE_RUNTIME_VERSIONS = {"codex-cli 0.144.1": "collaborationspawn_agent"}
TOML_STRING = re.compile(r'([a-z_]+)\s*=\s*("(?:[^"\\]|\\["\\nrt])*")\Z')


class DispatchContractError(ValueError):
    """The planned dispatch is not supported by captured runtime evidence."""


class RoleAuthorizationUnavailable(DispatchContractError):
    """A role record is structurally sound but has no trusted runtime proof."""


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


def read_evidence(path: Path) -> bytes:
    """Read once so the hash and parsed capabilities bind to identical bytes."""
    with path.open("rb") as stream:
        raw = stream.read(MAX_EVIDENCE_BYTES + 1)
    require(0 < len(raw) <= MAX_EVIDENCE_BYTES,
            f"capability evidence source must be 1..{MAX_EVIDENCE_BYTES} bytes: {path}")
    return raw


def parse_frozen_role_toml(raw: bytes, kind: str) -> dict[str, Any]:
    """Accept only the small audited TOML subset used by this route."""
    lines = [line.strip() for line in raw.decode("utf-8").splitlines() if line.strip()]
    if kind == "role_binding":
        require(lines and lines.pop(0) == "[agents.default]",
                "role binding needs an exact [agents.default] section")
        expected = {"description", "config_file"}
    else:
        expected = {"model", "model_reasoning_effort"}
    result: dict[str, Any] = {"schema_version": 1, "kind": kind}
    for line in lines:
        match = TOML_STRING.fullmatch(line)
        require(match is not None, "role TOML contains unsupported syntax")
        key, encoded = match.groups()
        require(key in expected and key not in result, "role TOML has duplicate or unsupported key")
        result[key] = json.loads(encoded)
    require(set(result) == {"schema_version", "kind"} | expected,
            "role TOML is missing required fields")
    for key in expected:
        resolved(result[key], f"role TOML {key}")
    return result


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    elif kind == "inheritance_contract":
        exact_keys(value, {"schema_version", "kind", "tool", "inherits", "when_omitted"},
                   f"capability source {evidence_id}")
        resolved(value["tool"], f"capability source {evidence_id} tool")
        require(value["when_omitted"] is True,
                f"capability source {evidence_id} must explicitly apply when selectors are omitted")
        require(isinstance(value["inherits"], dict), f"capability source {evidence_id} inherits must be an object")
        exact_keys(value["inherits"], {"model", "reasoning_effort"},
                   f"capability source {evidence_id} inherits")
        resolved(value["inherits"]["model"], f"capability source {evidence_id} inherited model")
        inherited_effort = resolved(value["inherits"]["reasoning_effort"],
                                    f"capability source {evidence_id} inherited reasoning effort")
        require(inherited_effort in REASONING_EFFORTS,
                f"capability source {evidence_id} inherited reasoning effort is unsupported by the router")
    elif kind in ("role_binding", "role_file"):
        expected = ({"schema_version", "kind", "description", "config_file"}
                    if kind == "role_binding" else
                    {"schema_version", "kind", "model", "model_reasoning_effort"})
        exact_keys(value, expected, f"capability source {evidence_id}")
    elif kind == "role_runtime":
        exact_keys(value, {"schema_version", "kind", "cli_version", "tool",
                           "default_role", "role_file_precedence",
                           "agent_type_omitted_uses_default"}, f"capability source {evidence_id}")
        require(isinstance(value["cli_version"], str) and
                value["cli_version"] in ROLE_RUNTIME_VERSIONS and
                value["tool"] == ROLE_RUNTIME_VERSIONS[value["cli_version"]],
                "role runtime version/tool is not in the audited allowlist")
        require(value["default_role"] == "default" and
                value["role_file_precedence"] == "role_file_over_explicit_spawn_and_agents_defaults" and
                value["agent_type_omitted_uses_default"] is True,
                "role runtime precedence is unverified")
    else:
        exact_keys(value, {"schema_version", "kind", "cli_version", "tool", "role_name",
                           "binding_sha256", "role_file_sha256", "binary_sha256",
                           "spawn_arguments", "child", "review"}, f"capability source {evidence_id}")
        for field in ("binding_sha256", "role_file_sha256", "binary_sha256"):
            require(isinstance(value[field], str) and SHA256.fullmatch(value[field]) is not None,
                    f"role calibration {field} must be SHA-256")
        arguments = value["spawn_arguments"]
        require(isinstance(arguments, dict), "role calibration spawn_arguments must be an object")
        exact_keys(arguments, {"fork_turns", "selector_fields", "agent_type"},
                   "role calibration spawn_arguments")
        require(arguments == {"fork_turns": "none", "selector_fields": [], "agent_type": None},
                "role calibration did not observe omitted selectors and agent_type")
        child = value["child"]
        require(isinstance(child, dict), "role calibration child must be an object")
        exact_keys(child, {"model", "reasoning_effort"}, "role calibration child")
        review = value["review"]
        require(isinstance(review, dict), "role calibration review must be an object")
        exact_keys(review, {"status", "reviewer", "source", "sha256"}, "role calibration review")
        require(review["status"] == "passed", "role calibration needs independent review")
        resolved(review["reviewer"], "role calibration reviewer")
        resolved(review["source"], "role calibration review source")
        require(isinstance(review["sha256"], str) and SHA256.fullmatch(review["sha256"]) is not None and
                review["sha256"] != "0" * 64,
                "role calibration review needs SHA-256")
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
        raw = read_evidence(source)
        require(hashlib.sha256(raw).hexdigest() == expected_hash,
                f"capability evidence hash mismatch: {source}")
        parsed_source = (parse_frozen_role_toml(raw, kind) if kind in ("role_binding", "role_file")
                         else parse_json(raw.decode("utf-8"), str(source)))
        parsed = _validate_source(kind, parsed_source, evidence_id)
        result[evidence_id] = {"record": item, "path": source.resolve(), "value": parsed}
    return result


def validate_dispatch_structure(value: Any, source_root: Path | None = None) -> dict[str, Any]:
    """Check bounded evidence structure; this does not authorize role dispatch."""
    require(isinstance(value, dict), "dispatch contract must be an object")
    exact_keys(value, {"schema_version", "dispatch_id", "purpose", "canonical_name", "packet",
                       "native_dispatch", "selection", "capability_evidence"}, "dispatch contract")
    require(value["schema_version"] == 1, "schema_version must be 1")
    resolved(value["dispatch_id"], "dispatch_id")
    purpose = resolved(value["purpose"], "purpose")
    evidence = validate_evidence(value["capability_evidence"], source_root)

    selection = value["selection"]
    require(isinstance(selection, dict), "selection must be an object")
    require(selection.get("mode") in SELECTION_MODES, "unsupported selection.mode")
    role_mode = selection["mode"] == "verified_role_config"
    exact_keys(selection, {"mode", "model", "reasoning_effort", "evidence_refs"} |
               ({"role"} if role_mode else set()), "selection")
    model = resolved(selection["model"], "selection.model")
    effort = resolved(selection["reasoning_effort"], "selection.reasoning_effort")
    require(effort in REASONING_EFFORTS, f"reasoning effort {effort} is unsupported by the router")
    refs = _string_array(selection["evidence_refs"], "selection.evidence_refs")
    require(all(ref in evidence for ref in refs), "selection references missing capability evidence")

    native = value["native_dispatch"]
    require(isinstance(native, dict), "native_dispatch must be an object")
    exact_keys(native, {"tool", "naming_field", "native_name", "schema_evidence_ref"} |
               ({"planned_arguments"} if role_mode else set()), "native_dispatch")
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
    elif selection["mode"] == "verified_inheritance":
        contracts = [item["value"] for item in selected if item["record"]["kind"] == "inheritance_contract"]
        require(contracts, "verified inheritance requires inheritance_contract evidence")
        require(all(item["tool"] == tool for item in contracts), "inheritance contract is for a different tool")
        require(any(item["inherits"] == {"model": model, "reasoning_effort": effort} for item in contracts),
                "selected model/effort contradict verified inheritance evidence")
    else:
        role = selection["role"]
        require(isinstance(role, dict), "selection.role must be an object")
        exact_keys(role, {"name", "binding_ref", "file_ref", "runtime_ref", "calibration_ref",
                          "binary_path", "binary_sha256"}, "selection.role")
        require(role["name"] == "default", "verified role must be agents.default")
        require(all(isinstance(role[field], str) for field in
                    ("binding_ref", "file_ref", "runtime_ref", "calibration_ref")),
                "role evidence references must be strings")
        role_refs = {role[field] for field in ("binding_ref", "file_ref", "runtime_ref", "calibration_ref")}
        require(set(refs) == role_refs and len(role_refs) == 4,
                "role selection needs exactly four distinct evidence references")
        for field, kind in (("binding_ref", "role_binding"), ("file_ref", "role_file"),
                            ("runtime_ref", "role_runtime"), ("calibration_ref", "role_calibration")):
            require(role[field] in evidence and evidence[role[field]]["record"]["kind"] == kind,
                    f"role {field} needs {kind} evidence")
        binding = evidence[role["binding_ref"]]
        role_file = evidence[role["file_ref"]]
        runtime = evidence[role["runtime_ref"]]["value"]
        calibration = evidence[role["calibration_ref"]]["value"]
        configured_path = Path(binding["value"]["config_file"])
        require(configured_path.is_absolute() and configured_path.resolve() == role_file["path"],
                "active agents.default binding differs from frozen role file")
        require(role_file["value"]["model"] == model and
                role_file["value"]["model_reasoning_effort"] == effort,
                "role file model/effort differs from selection")
        require(tool == runtime["tool"] and runtime["cli_version"] in ROLE_RUNTIME_VERSIONS,
                "role runtime tool/version mismatch")
        require({"task_name", "message", "fork_turns"}.issubset(arguments) and
                not {"model", "reasoning_effort"}.intersection(arguments),
                "role runtime spawn schema does not hide explicit selectors")
        planned = native["planned_arguments"]
        require(isinstance(planned, dict), "role planned_arguments must be an object")
        exact_keys(planned, {"fork_turns", "model", "reasoning_effort", "agent_type",
                             "message_sha256", "message_bytes"},
                   "role planned_arguments")
        require(all(planned[field] is None for field in ("model", "reasoning_effort", "agent_type")) and
                planned["fork_turns"] == "none",
                "role dispatch requires fork_turns none and omitted selectors/agent_type")
        require(isinstance(planned["message_sha256"], str) and
                SHA256.fullmatch(planned["message_sha256"]) is not None and
                type(planned["message_bytes"]) is int and 0 < planned["message_bytes"] <= 65_536,
                "role dispatch needs bounded exact packet commitment")
        binary_path = Path(resolved(role["binary_path"], "role binary_path"))
        binary_hash = role["binary_sha256"]
        require(binary_path.is_absolute() and binary_path.is_file() and
                isinstance(binary_hash, str) and SHA256.fullmatch(binary_hash) is not None and
                file_hash(binary_path) == binary_hash,
                "role runtime binary is missing or changed")
        require(calibration["cli_version"] == runtime["cli_version"] and
                calibration["tool"] == tool and calibration["role_name"] == "default" and
                calibration["binding_sha256"] == binding["record"]["sha256"] and
                calibration["role_file_sha256"] == role_file["record"]["sha256"] and
                calibration["binary_sha256"] == binary_hash and
                calibration["child"] == {"model": model, "reasoning_effort": effort},
                "role calibration does not bind exact runtime/config/child selectors")
        review = calibration["review"]
        review_path = Path(review["source"])
        if not review_path.is_absolute():
            require(source_root is not None, "relative review source needs source root")
            review_path = source_root / review_path
        require(review_path.is_file() and
                hashlib.sha256(read_evidence(review_path)).hexdigest() == review["sha256"],
                "role calibration review evidence is missing or changed")

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


def validate_dispatch_contract(value: Any, source_root: Path | None = None) -> dict[str, Any]:
    """Authorize only routes for which this preflight has a trusted proof."""
    checked = validate_dispatch_structure(value, source_root)
    if checked["selection"]["mode"] == "verified_role_config":
        raise RoleAuthorizationUnavailable(
            "verified_role_config has structural evidence only; independent CLI calibration "
            "and trustworthy hook capture are not yet established")
    return checked


def capability_blocker(dispatch_id: str, errors: list[str],
                       kind: str = "runtime-capability-evidence") -> dict[str, Any]:
    return {"schema_version": 1, "dispatch_id": dispatch_id, "status": "blocked",
            "blocker_kind": kind, "errors": errors}


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
        kind = ("role-runtime-authorization-unverified" if isinstance(error, RoleAuthorizationUnavailable)
                else "runtime-capability-evidence")
        output = capability_blocker(dispatch_id, [str(error)], kind)
        code = 1
    print(json.dumps(output, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
