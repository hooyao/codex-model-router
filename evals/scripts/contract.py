"""Executable strict schema v2. All artifact paths are relative to a declared root."""

from __future__ import annotations

import hashlib
import json
import math
import re
import stat
from pathlib import Path
from typing import Any

VERSION = 2
VARIANTS = ("baseline", "router")
SAFETY_FIELDS = (
    "recursive_delegation", "write_conflicts", "unrecovered_partial_failure",
    "scope_leak", "prompt_injection_violation",
)
SESSION_LISTS = ("worker_sessions", "retry_sessions", "verification_sessions", "abandoned_sessions")


class ContractError(ValueError):
    """An input cannot be interpreted under the declared contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def object_fields(value: Any, fields: str, label: str) -> dict:
    require(type(value) is dict, f"{label} must be an object")
    expected = set(fields.split())
    require(set(value) == expected,
            f"{label} fields: missing={sorted(expected - set(value))}, extra={sorted(set(value) - expected)}")
    return value


def nonempty(value: Any, label: str) -> str:
    require(type(value) is str and bool(value.strip()), f"{label} must be a non-empty string")
    return value


def enum(value: Any, choices: tuple, label: str) -> None:
    require(type(value) is str and value in choices, f"{label} must be one of {choices}")


def number(value: Any, label: str, low: float = 0, high: float | None = None) -> None:
    try:
        finite = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        finite = False
    require(finite and value >= low and (high is None or value <= high), f"{label} must be finite in [{low}, {high}]")


def integer(value: Any, label: str, low: int = 0, high: int | None = None) -> None:
    require(type(value) is int, f"{label} must be an integer")
    number(value, label, low, high)


def boolean(value: Any, label: str) -> None:
    require(type(value) is bool, f"{label} must be boolean")


def string_list(value: Any, label: str, allow_empty: bool = False) -> list:
    require(type(value) is list and (allow_empty or bool(value)), f"{label} must be a {'possibly empty' if allow_empty else 'non-empty'} list")
    for item in value:
        nonempty(item, label)
    require(len(value) == len(set(value)), f"{label} contains duplicates")
    return value


def unique_object(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    raise ContractError(f"non-finite JSON constant: {value}")


def parse_json(text: str) -> Any:
    return json.loads(text, object_pairs_hook=unique_object, parse_constant=reject_constant)


def read_json(path: Path) -> Any:
    return parse_json(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value: Any, label: str) -> None:
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None, f"{label} must be a lowercase SHA-256")


def check_node(path: Path) -> None:
    info = path.lstat()
    require(not stat.S_ISLNK(info.st_mode) and not (getattr(info, "st_file_attributes", 0) & 0x400),
            f"symlink/reparse point is forbidden: {path}")
    require(stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode), f"non-regular filesystem node: {path}")


def relative_name(value: Any) -> str:
    nonempty(value, "path")
    parts = value.split("/")
    for part in parts:
        require(bool(re.fullmatch(r"[A-Za-z0-9_.-]+", part)) and part not in (".", "..")
                and not part.endswith("."), f"unsafe relative path: {value}")
        require(not re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part),
                f"reserved path: {value}")
    return value


def safe_path(root: Path, value: Any) -> Path:
    relative_name(value)
    check_node(root)
    current = root
    for part in value.split("/"):
        current = current / part
        check_node(current)
    require(current.resolve().is_relative_to(root.resolve()), f"path escapes root: {value}")
    return current


def hash_ref(root: Path, value: Any, label: str) -> Path:
    object_fields(value, "path sha256", label)
    digest(value["sha256"], f"{label}.sha256")
    path = safe_path(root, value["path"])
    require(path.is_file(), f"{label} must reference a file")
    require(sha256(path) == value["sha256"], f"{label} hash mismatch: {value['path']}")
    return path


def load_cases(path: Path) -> list[dict]:
    cases = read_json(path)
    require(type(cases) is list and bool(cases), "cases must be a non-empty array")
    ids = set()
    for case in cases:
        object_fields(case, "id status category prompt expected_mode acceptance failure_signals fixture", "case")
        for field in ("id", "category", "prompt", "expected_mode"):
            nonempty(case[field], f"case.{field}")
        require(case["id"] not in ids, f"duplicate case id: {case['id']}")
        ids.add(case["id"])
        enum(case["status"], ("draft", "release"), "case.status")
        string_list(case["acceptance"], "case.acceptance")
        string_list(case["failure_signals"], "case.failure_signals")
        if case["fixture"] is not None:
            hash_ref(path.parent, case["fixture"], "case.fixture")
        require(case["status"] != "release" or case["fixture"] is not None,
                "release case requires a fixture")
        require(case["expected_mode"] != "direct", "router cases must expect worker execution")
    return cases


def load_manifest(path: Path) -> tuple[dict, list[dict]]:
    manifest = read_json(path)
    object_fields(manifest, "schema_version experiment_id status data_origin cases_file cases_sha256 variants repetitions_per_case analysis controls", "manifest")
    integer(manifest["schema_version"], "manifest.schema_version", VERSION, VERSION)
    nonempty(manifest["experiment_id"], "experiment_id")
    enum(manifest["status"], ("draft", "release"), "manifest.status")
    enum(manifest["data_origin"], ("synthetic", "observed"), "data_origin")
    string_list(manifest["variants"], "variants")
    require(set(manifest["variants"]) == set(VARIANTS), "variants must be exactly baseline and router")
    integer(manifest["repetitions_per_case"], "repetitions_per_case", 1, 10000)
    cases_path = hash_ref(path.parent, {"path": manifest["cases_file"], "sha256": manifest["cases_sha256"]}, "cases")
    cases = load_cases(cases_path)
    if manifest["status"] == "release":
        require(all(case["status"] == "release" for case in cases), "release manifest contains draft cases")
    analysis = object_fields(manifest["analysis"], "quality_margin minimum_router_quality maximum_cost_ratio maximum_latency_ratio", "analysis")
    number(analysis["quality_margin"], "quality_margin", -1, 0)
    number(analysis["minimum_router_quality"], "minimum_router_quality", 0, 1)
    number(analysis["maximum_cost_ratio"], "maximum_cost_ratio", 0, 1)
    number(analysis["maximum_latency_ratio"], "maximum_latency_ratio", 1, 100)
    controls = object_fields(manifest["controls"], "repository_revision harness_version environment_id policy", "controls")
    for key, value in controls.items():
        nonempty(value, f"controls.{key}")
    require(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", controls["repository_revision"]) is not None,
            "repository_revision must be a full Git object ID or tree SHA-256")
    require(controls["policy"] == "pure-orchestrator-v1", "unknown controls.policy")
    return manifest, cases


def session_inventory(trace: dict) -> set[str]:
    return {trace["controller_session"]}.union(*(set(trace[field]) for field in SESSION_LISTS))


def validate_record(record: Any) -> None:
    object_fields(record, "schema_version experiment_id manifest_sha256 run_id pair_id case_id variant repetition outcome passed quality_score duration_ms cost delegated_tasks retries recursive_delegation write_conflicts unrecovered_partial_failure scope_leak prompt_injection_violation evidence route_trace environment result_tree", "record")
    integer(record["schema_version"], "record.schema_version", VERSION, VERSION)
    for field in ("experiment_id", "run_id", "pair_id", "case_id"):
        nonempty(record[field], field)
    digest(record["manifest_sha256"], "manifest_sha256")
    enum(record["variant"], VARIANTS, "variant")
    enum(record["outcome"], ("completed", "timeout", "cancelled", "error", "blocked"), "outcome")
    integer(record["repetition"], "repetition", 1)
    boolean(record["passed"], "passed")
    number(record["quality_score"], "quality_score", 0, 4)
    number(record["duration_ms"], "duration_ms")
    for field in ("delegated_tasks", "retries"):
        integer(record[field], field)
    for field in SAFETY_FIELDS:
        boolean(record[field], field)
    require(record["outcome"] == "completed" or (not record["passed"] and record["quality_score"] == 0),
            "non-completed runs must fail quality with score zero")
    require(type(record["evidence"]) is list and bool(record["evidence"]), "evidence must be a non-empty list")
    for ref in record["evidence"]:
        object_fields(ref, "path sha256", "evidence")
        relative_name(ref["path"])
        digest(ref["sha256"], "evidence.sha256")
    require(len({ref["path"] for ref in record["evidence"]}) == len(record["evidence"]), "duplicate evidence reference")
    trace = object_fields(record["route_trace"], "controller_session worker_sessions retry_sessions verification_sessions abandoned_sessions controller_business_actions worker_business_actions", "route_trace")
    nonempty(trace["controller_session"], "controller_session")
    for field in SESSION_LISTS:
        string_list(trace[field], field, allow_empty=True)
    require(trace["controller_session"] not in trace["worker_sessions"], "controller cannot also be a worker")
    for field in ("controller_business_actions", "worker_business_actions"):
        integer(trace[field], field)
    require(record["delegated_tasks"] == len(trace["worker_sessions"]), "delegated_tasks differs from worker inventory")
    controls = object_fields(record["environment"], "repository_revision harness_version environment_id policy", "environment")
    for key, value in controls.items():
        nonempty(value, f"environment.{key}")
    if record["result_tree"] is not None:
        object_fields(record["result_tree"], "path sha256", "result_tree")
        relative_name(record["result_tree"]["path"])
        digest(record["result_tree"]["sha256"], "result_tree.sha256")
    cost = object_fields(record["cost"], "source complete cost_usd ledger", "cost")
    enum(cost["source"], ("unavailable", "ccusage", "synthetic", "harness-ledger-v1"), "cost.source")
    boolean(cost["complete"], "cost.complete")
    if cost["cost_usd"] is not None:
        number(cost["cost_usd"], "cost_usd")
    if cost["complete"]:
        require(cost["source"] in ("synthetic", "harness-ledger-v1"), "unverified source cannot establish complete accounting")
        require(cost["cost_usd"] is not None, "complete cost requires cost_usd")
        object_fields(cost["ledger"], "path sha256", "cost.ledger")
        relative_name(cost["ledger"]["path"])
        digest(cost["ledger"]["sha256"], "cost.ledger.sha256")
    else:
        require(cost["ledger"] is None, "incomplete accounting must not carry a verified ledger")


def load_records(path: Path) -> list[dict]:
    records = []
    run_ids, slots, pair_slots, slot_pairs = set(), set(), {}, {}
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = parse_json(line)
            validate_record(record)
            require(record["run_id"] not in run_ids, "globally duplicate run_id")
            run_ids.add(record["run_id"])
            slot = (record["case_id"], record["repetition"], record["variant"])
            require(slot not in slots, "duplicate scheduled slot")
            slots.add(slot)
            pair_slot, pair_id = slot[:2], record["pair_id"]
            require(pair_id not in pair_slots or pair_slots[pair_id] == pair_slot, "pair_id reused across slots")
            require(pair_slot not in slot_pairs or slot_pairs[pair_slot] == pair_id, "mismatched pair IDs")
            pair_slots[pair_id], slot_pairs[pair_slot] = pair_slot, pair_id
            records.append(record)
        except (ValueError, OverflowError) as error:
            raise ContractError(f"line {index}: {error}") from error
    require(bool(records), "records must not be empty")
    return records


def validate_accounting(root: Path, record: dict, manifest: dict) -> tuple | None:
    cost = record["cost"]
    require(cost["source"] != "synthetic" or manifest["data_origin"] == "synthetic", "synthetic accounting in observed campaign")
    if not cost["complete"]:
        return None
    ledger = read_json(hash_ref(root, cost["ledger"], "cost ledger"))
    object_fields(ledger, "schema_version experiment_id run_id source provider currency method pricing_version sessions", "ledger")
    integer(ledger["schema_version"], "ledger.schema_version", VERSION, VERSION)
    for field in ("experiment_id", "run_id"):
        require(ledger[field] == record[field], f"ledger {field} provenance mismatch")
    require(ledger["source"] == cost["source"], "ledger source provenance mismatch")
    nonempty(ledger["provider"], "ledger.provider")
    nonempty(ledger["pricing_version"], "ledger.pricing_version")
    require(ledger["currency"] == "USD", "ledger currency must be USD")
    enum(ledger["method"], ("billed", "api-price-estimate", "synthetic"), "ledger.method")
    require((ledger["method"] == "synthetic") == (cost["source"] == "synthetic"), "ledger method/source mismatch")
    require(type(ledger["sessions"]) is list and bool(ledger["sessions"]), "ledger.sessions must be non-empty")
    ids, totals = set(), []
    for session in ledger["sessions"]:
        object_fields(session, "session_id exclusive_cost_usd evidence", "ledger session")
        nonempty(session["session_id"], "session_id")
        require(session["session_id"] not in ids, "duplicate accounting session")
        ids.add(session["session_id"])
        number(session["exclusive_cost_usd"], "exclusive_cost_usd")
        totals.append(session["exclusive_cost_usd"])
        hash_ref(root, session["evidence"], "session evidence")
    require(ids == session_inventory(record["route_trace"]), "ledger session inventory mismatch")
    try:
        total = math.fsum(totals)
    except OverflowError as error:
        raise ContractError("ledger total overflows") from error
    require(math.isclose(total, cost["cost_usd"], rel_tol=1e-9, abs_tol=1e-12), "ledger total mismatch")
    return ledger["provider"], ledger["currency"], ledger["method"], ledger["pricing_version"]
