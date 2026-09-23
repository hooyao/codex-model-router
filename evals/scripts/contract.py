"""Strict scenario benchmark contract; standard library only."""

from __future__ import annotations

import hashlib
import json
import math
import re
import stat
from pathlib import Path
from typing import Any, Optional

VERSION = 1
TREATMENTS = ("direct", "mandatory_delegate", "selective")
OUTCOMES = ("completed", "blocked", "cancelled", "timeout", "error")
OWNERSHIPS = ("DIRECT", "DELEGATE")
TOPOLOGIES = ("NONE", "ISOLATED_SERIAL", "PARALLEL")
VERIFICATIONS = ("SELF_CHECK", "INDEPENDENT_REVIEW")
CATEGORIES = ("direct-control", "investigation-reuse", "serial-escalation",
              "parallel-disjoint", "architecture-review")


class ContractError(ValueError):
    """Benchmark input cannot be interpreted under the frozen contract."""


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


def enum(value: Any, choices: tuple[str, ...], label: str) -> str:
    require(type(value) is str and value in choices, f"{label} must be one of {choices}")
    return value


def number(value: Any, label: str, low: float = 0, high: Optional[float] = None) -> float:
    require(type(value) in (int, float) and math.isfinite(value), f"{label} must be finite")
    require(value >= low and (high is None or value <= high), f"{label} must be in [{low}, {high}]")
    return value


def integer(value: Any, label: str, low: int = 0, high: Optional[int] = None) -> int:
    require(type(value) is int, f"{label} must be an integer")
    number(value, label, low, high)
    return value


def boolean(value: Any, label: str) -> bool:
    require(type(value) is bool, f"{label} must be boolean")
    return value


def string_list(value: Any, label: str, allow_empty: bool = False) -> list[str]:
    require(type(value) is list and (allow_empty or bool(value)),
            f"{label} must be a {'possibly empty' if allow_empty else 'non-empty'} list")
    for item in value:
        nonempty(item, label)
    require(len(value) == len(set(value)), f"{label} contains duplicates")
    return value


def unique_object(pairs: list[tuple[str, Any]]) -> dict:
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


def digest(value: Any, label: str) -> str:
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
            f"{label} must be a lowercase SHA-256")
    return value


def relative_name(value: Any) -> str:
    nonempty(value, "path")
    require("\\" not in value and not value.startswith("/") and not re.match(r"^[A-Za-z]:", value),
            f"unsafe relative path: {value}")
    parts = value.split("/")
    for part in parts:
        require(bool(re.fullmatch(r"[A-Za-z0-9_.-]+", part)) and part not in (".", "..")
                and not part.endswith("."), f"unsafe relative path: {value}")
        require(not re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part),
                f"reserved path: {value}")
    return value


def check_node(path: Path) -> None:
    info = path.lstat()
    require(not stat.S_ISLNK(info.st_mode) and not (getattr(info, "st_file_attributes", 0) & 0x400),
            f"symlink/reparse point is forbidden: {path}")
    require(stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode), f"non-regular filesystem node: {path}")


def safe_path(root: Path, value: Any) -> Path:
    relative_name(value)
    check_node(root)
    current = root
    for part in value.split("/"):
        current = current / part
        check_node(current)
    require(current.resolve().is_relative_to(root.resolve()), f"path escapes root: {value}")
    return current


def hash_ref(root: Path, value: Any, label: str, directory: bool = False) -> Path:
    ref = object_fields(value, "path sha256", label)
    digest(ref["sha256"], f"{label}.sha256")
    path = safe_path(root, ref["path"])
    require(path.is_dir() if directory else path.is_file(),
            f"{label} must reference a {'directory' if directory else 'file'}")
    if not directory:
        require(sha256(path) == ref["sha256"], f"{label} hash mismatch")
    return path


def validate_route(value: Any, label: str) -> dict:
    route = object_fields(value, "initial_ownership final_ownership delegate_topology verification_requirement escalation_trigger first_delegation_ms", label)
    enum(route["initial_ownership"], OWNERSHIPS, f"{label}.initial_ownership")
    enum(route["final_ownership"], OWNERSHIPS, f"{label}.final_ownership")
    enum(route["delegate_topology"], TOPOLOGIES, f"{label}.delegate_topology")
    enum(route["verification_requirement"], VERIFICATIONS, f"{label}.verification_requirement")
    trigger = route["escalation_trigger"]
    require(trigger is None or (type(trigger) is str and bool(trigger)), f"{label}.escalation_trigger is invalid")
    require((route["final_ownership"] == "DIRECT") == (route["delegate_topology"] == "NONE"),
            f"{label} ownership/topology mismatch")
    require(route["final_ownership"] != "DIRECT" or route["verification_requirement"] == "SELF_CHECK",
            f"{label} DIRECT requires SELF_CHECK")
    escalated = route["initial_ownership"] == "DIRECT" and route["final_ownership"] == "DELEGATE"
    require(escalated == (trigger is not None), f"{label} escalation trigger mismatch")
    first_delegation = route["first_delegation_ms"]
    if route["final_ownership"] == "DELEGATE":
        number(first_delegation, f"{label}.first_delegation_ms")
        require((route["initial_ownership"] == "DELEGATE") == (first_delegation == 0),
                f"{label} initial ownership/delegation timestamp mismatch")
    else:
        require(first_delegation is None, f"{label} DIRECT cannot have delegation timestamp")
    return route


def load_cases(path: Path) -> list[dict]:
    cases = read_json(path)
    require(type(cases) is list and bool(cases), "cases must be a non-empty array")
    ids = set()
    for case in cases:
        object_fields(case, "id category prompt fixture treatments route_expectations required_quality_checks required_process_checks required_artifact_paths required_span_ids required_dependency_edges required_receipt_edges required_receipt_facts acceptance objectives", "case")
        case_id = nonempty(case["id"], "case.id")
        require(case_id not in ids, f"duplicate case id: {case_id}")
        ids.add(case_id)
        enum(case["category"], CATEGORIES, "case.category")
        nonempty(case["prompt"], "case.prompt")
        string_list(case["treatments"], "case.treatments")
        require(tuple(case["treatments"]) == TREATMENTS, "case treatments must use the frozen treatment order")
        routes = object_fields(case["route_expectations"], "direct mandatory_delegate selective", "route_expectations")
        for treatment in TREATMENTS:
            validate_route(routes[treatment], f"route_expectations.{treatment}")
        string_list(case["required_quality_checks"], "case.required_quality_checks")
        require(set(case["required_quality_checks"]) == {"artifact-exact"},
                "case.required_quality_checks must be artifact-exact")
        process = object_fields(case["required_process_checks"], "direct mandatory_delegate selective", "required_process_checks")
        allowed_checks = {"route-adherent", "receipt-preserved-facts", "scope-transition-safe",
                          "dependency-order", "artifact-path-coverage", "disjoint-write-ownership",
                          "independent-review"}
        for treatment in TREATMENTS:
            string_list(process[treatment], f"required_process_checks.{treatment}")
            require(set(process[treatment]).issubset(allowed_checks), "required_process_checks contains unknown check")
        string_list(case["required_artifact_paths"], "case.required_artifact_paths")
        for artifact_path in case["required_artifact_paths"]:
            relative_name(artifact_path)
        span_ids = object_fields(case["required_span_ids"], "direct mandatory_delegate selective", "required_span_ids")
        for treatment in TREATMENTS:
            string_list(span_ids[treatment], f"required_span_ids.{treatment}")
            require(all(re.fullmatch(r"[a-z0-9-]+", span_id) for span_id in span_ids[treatment]),
                    f"required_span_ids.{treatment} contains invalid ID")
        for edge_field in ("required_dependency_edges", "required_receipt_edges"):
            edges = object_fields(case[edge_field], "direct mandatory_delegate selective", edge_field)
            for treatment in TREATMENTS:
                string_list(edges[treatment], f"{edge_field}.{treatment}", allow_empty=True)
                require(all(re.fullmatch(r"[a-z0-9-]+->[a-z0-9-]+", edge) for edge in edges[treatment]),
                        f"{edge_field}.{treatment} contains invalid edge")
        string_list(case["required_receipt_facts"], "case.required_receipt_facts", allow_empty=True)
        string_list(case["acceptance"], "case.acceptance")
        string_list(case["objectives"], "case.objectives")
        hash_ref(path.parent, case["fixture"], "case.fixture")
    return cases


def load_manifest(path: Path) -> tuple[dict, list[dict]]:
    manifest = read_json(path)
    object_fields(manifest, "schema_version benchmark_id data_origin cases_file cases_sha256 treatments repetitions controls", "manifest")
    integer(manifest["schema_version"], "manifest.schema_version", VERSION, VERSION)
    nonempty(manifest["benchmark_id"], "manifest.benchmark_id")
    enum(manifest["data_origin"], ("synthetic", "observed"), "manifest.data_origin")
    string_list(manifest["treatments"], "manifest.treatments")
    require(tuple(manifest["treatments"]) == TREATMENTS, "manifest treatments mismatch")
    integer(manifest["repetitions"], "manifest.repetitions", 1, 1000)
    controls = object_fields(manifest["controls"], "harness_version policy environment_id", "controls")
    for field in controls:
        nonempty(controls[field], f"controls.{field}")
    require(controls["policy"] == "selective-execution-v1", "unknown controls.policy")
    cases_path = hash_ref(path.parent, {"path": manifest["cases_file"], "sha256": manifest["cases_sha256"]}, "cases")
    return manifest, load_cases(cases_path)


def _validate_span(value: Any, label: str) -> dict:
    span = object_fields(value, "id session_id role start_ms end_ms depends_on artifact_paths input_tokens receipt_tokens tool_calls log_bytes", label)
    nonempty(span["id"], f"{label}.id")
    nonempty(span["session_id"], f"{label}.session_id")
    enum(span["role"], ("controller", "worker", "reviewer"), f"{label}.role")
    number(span["start_ms"], f"{label}.start_ms")
    number(span["end_ms"], f"{label}.end_ms")
    require(span["end_ms"] > span["start_ms"], f"{label} must have positive duration")
    string_list(span["depends_on"], f"{label}.depends_on", allow_empty=True)
    string_list(span["artifact_paths"], f"{label}.artifact_paths", allow_empty=True)
    for path in span["artifact_paths"]:
        relative_name(path)
    for field in ("input_tokens", "receipt_tokens", "tool_calls", "log_bytes"):
        integer(span[field], f"{label}.{field}")
    require(span["role"] != "controller" or span["receipt_tokens"] == 0,
            "controller spans cannot emit worker receipts")
    return span


def _validate_spans(spans: Any, execution: dict, context: dict, route: dict,
                    trace: dict, outcome: str) -> list[dict]:
    require(type(spans) is list, "execution.spans must be a list")
    validated = [_validate_span(span, f"execution.spans[{index}]") for index, span in enumerate(spans)]
    ids = [span["id"] for span in validated]
    require(len(ids) == len(set(ids)), "span IDs must be unique")
    known = set(ids)
    for span in validated:
        require(set(span["depends_on"]).issubset(known), "span dependency is unknown")
        require(span["id"] not in span["depends_on"], "span cannot depend on itself")
    visiting: set[str] = set()
    visited: set[str] = set()
    by_id = {span["id"]: span for span in validated}
    for span in validated:
        for dependency in span["depends_on"]:
            require(by_id[dependency]["end_ms"] <= span["start_ms"],
                    f"dependency timing violation: {dependency} -> {span['id']}")

    def visit(span_id: str) -> None:
        require(span_id not in visiting, "span dependency cycle")
        if span_id in visited:
            return
        visiting.add(span_id)
        for dependency in by_id[span_id]["depends_on"]:
            visit(dependency)
        visiting.remove(span_id)
        visited.add(span_id)

    for span_id in ids:
        visit(span_id)
    owners: dict[str, str] = {}
    for span in validated:
        for artifact in span["artifact_paths"]:
            require(artifact not in owners, f"artifact has overlapping write ownership: {artifact}")
            owners[artifact] = span["id"]

    active = [span for span in validated if span["role"] != "controller"]
    session_roles: dict[str, str] = {}
    for span in validated:
        require(span["session_id"] not in session_roles or session_roles[span["session_id"]] == span["role"],
                "session cannot have multiple span roles")
        session_roles[span["session_id"]] = span["role"]
        if span["role"] == "controller":
            require(span["session_id"] == trace["controller_session"],
                    "controller span session does not match route controller")
        else:
            require(span["session_id"] != trace["controller_session"],
                    "worker/reviewer span cannot use controller session")
    if trace["first_delegation_ms"] is not None:
        require(all(span["end_ms"] <= trace["first_delegation_ms"]
                    for span in validated if span["role"] == "controller"),
                "controller business span occurs after delegation")
    events = trace["route_events"]
    for span in validated:
        prior_events = [event for event in events if event["at_ms"] <= span["start_ms"]]
        require(bool(prior_events), f"business span starts before ownership decision: {span['id']}")
        state = prior_events[-1]["ownership"]
        required_state = "DIRECT" if span["role"] == "controller" else "DELEGATE"
        require(state == required_state,
                f"business span role is not covered by recorded ownership: {span['id']}")
        later_events = [event for event in events if event["at_ms"] > span["start_ms"]]
        if later_events:
            require(span["end_ms"] <= later_events[0]["at_ms"],
                    f"business span crosses route transition: {span['id']}")
    require(execution["controller_business_actions"] == sum(span["role"] == "controller" for span in validated),
            "controller business actions do not match controller spans")
    require(execution["worker_business_actions"] == sum(span["role"] == "worker" for span in validated),
            "worker business actions do not match worker spans")
    if route["final_ownership"] == "DIRECT":
        require(not active, "DIRECT cannot contain worker or reviewer spans")
    if outcome == "completed" and route["final_ownership"] == "DELEGATE":
        require(any(span["role"] == "worker" for span in active), "completed delegation requires worker execution")
    if route["delegate_topology"] == "PARALLEL":
        workers = [span for span in active if span["role"] == "worker"]
        overlapping_pairs = [(left, right) for index, left in enumerate(workers)
                             for right in workers[index + 1:]
                             if left["start_ms"] < right["end_ms"] and right["start_ms"] < left["end_ms"]]
        require(all(left["session_id"] != right["session_id"] for left, right in overlapping_pairs),
                "PARALLEL overlap requires distinct worker sessions")
        if outcome == "completed":
            require(len(workers) >= 2 and overlapping_pairs, "PARALLEL requires overlapping worker spans")
    if route["delegate_topology"] == "ISOLATED_SERIAL":
        ordered = sorted(active, key=lambda span: (span["start_ms"], span["end_ms"]))
        require(all(left["end_ms"] <= right["start_ms"] for left, right in zip(ordered, ordered[1:])),
                "ISOLATED_SERIAL spans must not overlap")

    critical = 0 if not validated else max(span["end_ms"] for span in validated) - min(span["start_ms"] for span in validated)
    require(math.isclose(critical, execution["critical_path_ms"], rel_tol=0, abs_tol=1e-9),
            "critical_path_ms does not match span schedule")
    require(execution["wall_time_ms"] >= execution["critical_path_ms"], "wall time is below critical path")
    require(execution["tool_calls"] == sum(span["tool_calls"] for span in validated), "tool-call total mismatch")
    require(execution["raw_log_bytes"] == sum(span["log_bytes"] for span in validated), "log-byte total mismatch")
    require(context["controller_input_tokens"] == sum(span["input_tokens"] for span in validated if span["role"] == "controller"),
            "controller context total mismatch")
    require(context["worker_input_tokens"] == sum(span["input_tokens"] for span in active),
            "worker context total mismatch")
    return validated


def receipt_sha256(content: dict) -> str:
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _validate_receipts(receipts: Any, spans: list[dict], context: dict) -> list[dict]:
    require(type(receipts) is list, "receipts must be a list")
    by_id = {span["id"]: span for span in spans}
    ids = set()
    producer_tokens = {span["id"]: 0 for span in spans}
    for index, receipt in enumerate(receipts):
        label = f"receipts[{index}]"
        object_fields(receipt, "id producer_span_id consumer_span_ids token_count content sha256", label)
        receipt_id = nonempty(receipt["id"], f"{label}.id")
        require(receipt_id not in ids, "receipt IDs must be unique")
        ids.add(receipt_id)
        producer = nonempty(receipt["producer_span_id"], f"{label}.producer_span_id")
        require(producer in by_id, "receipt producer span is unknown")
        consumers = string_list(receipt["consumer_span_ids"], f"{label}.consumer_span_ids", allow_empty=True)
        require(set(consumers).issubset(by_id), "receipt consumer span is unknown")
        for consumer in consumers:
            require(producer in by_id[consumer]["depends_on"],
                    "receipt consumer must depend on its producer")
        integer(receipt["token_count"], f"{label}.token_count", 1)
        content = object_fields(receipt["content"], "facts artifact_refs", f"{label}.content")
        string_list(content["facts"], f"{label}.content.facts", allow_empty=True)
        string_list(content["artifact_refs"], f"{label}.content.artifact_refs", allow_empty=True)
        for path in content["artifact_refs"]:
            relative_name(path)
        digest(receipt["sha256"], f"{label}.sha256")
        require(receipt["sha256"] == receipt_sha256(content), "receipt content hash mismatch")
        producer_tokens[producer] += receipt["token_count"]
    for span in spans:
        require(producer_tokens[span["id"]] == span["receipt_tokens"],
                f"span receipt tokens lack matching receipt evidence: {span['id']}")
    require(context["receipt_tokens"] == sum(receipt["token_count"] for receipt in receipts),
            "receipt-token total mismatch")
    return receipts


def validate_record(record: Any) -> dict:
    root = object_fields(record, "schema_version benchmark_id manifest_sha256 run_id pair_id case_id treatment repetition outcome route_trace execution context receipts quality review cost retries conflicts evidence result_tree", "record")
    integer(root["schema_version"], "record.schema_version", VERSION, VERSION)
    for field in ("benchmark_id", "run_id", "pair_id", "case_id"):
        nonempty(root[field], field)
    digest(root["manifest_sha256"], "manifest_sha256")
    enum(root["treatment"], TREATMENTS, "treatment")
    integer(root["repetition"], "repetition", 1)
    outcome = enum(root["outcome"], OUTCOMES, "outcome")

    trace = object_fields(root["route_trace"], "controller_session initial_ownership final_ownership delegate_topology verification_requirement escalation_trigger first_delegation_ms route_events", "route_trace")
    nonempty(trace["controller_session"], "route_trace.controller_session")
    route = validate_route({field: trace[field] for field in (
        "initial_ownership", "final_ownership", "delegate_topology",
        "verification_requirement", "escalation_trigger", "first_delegation_ms")}, "route_trace")
    events = trace["route_events"]
    require(type(events) is list and bool(events), "route_events must be non-empty")
    for index, event in enumerate(events):
        object_fields(event, "sequence at_ms ownership topology trigger", "route event")
        integer(event["sequence"], "route event.sequence", 1)
        require(event["sequence"] == index + 1, "route event sequence is not contiguous")
        number(event["at_ms"], "route event.at_ms")
        enum(event["ownership"], OWNERSHIPS, "route event.ownership")
        enum(event["topology"], TOPOLOGIES, "route event.topology")
        require((event["ownership"] == "DIRECT") == (event["topology"] == "NONE"),
                "route event ownership/topology mismatch")
        require(event["trigger"] is None or (type(event["trigger"]) is str and bool(event["trigger"])),
                "route event trigger is invalid")
    require(all(left["at_ms"] < right["at_ms"] for left, right in zip(events, events[1:])),
            "route event timestamps must be strictly increasing")
    require(events[0]["ownership"] == route["initial_ownership"] and
            events[-1]["ownership"] == route["final_ownership"] and
            events[-1]["topology"] == route["delegate_topology"], "route events do not match route summary")
    require(events[-1]["trigger"] == route["escalation_trigger"], "route event escalation mismatch")
    delegation_events = [event for event in events if event["ownership"] == "DELEGATE"]
    require((delegation_events[0]["at_ms"] if delegation_events else None) == route["first_delegation_ms"],
            "first delegation timestamp does not match route events")

    execution = object_fields(root["execution"], "controller_business_actions worker_business_actions wall_time_ms critical_path_ms tool_calls raw_log_bytes spans", "execution")
    for field in ("controller_business_actions", "worker_business_actions", "tool_calls", "raw_log_bytes"):
        integer(execution[field], f"execution.{field}")
    number(execution["wall_time_ms"], "execution.wall_time_ms")
    number(execution["critical_path_ms"], "execution.critical_path_ms")
    context = object_fields(root["context"], "controller_input_tokens worker_input_tokens receipt_tokens", "context")
    for field in context:
        integer(context[field], f"context.{field}")
    spans = _validate_spans(execution["spans"], execution, context, route, trace, outcome)
    _validate_receipts(root["receipts"], spans, context)

    quality = object_fields(root["quality"], "passed score checks", "quality")
    boolean(quality["passed"], "quality.passed")
    number(quality["score"], "quality.score", 0, 100)
    require(type(quality["checks"]) is list and bool(quality["checks"]), "quality.checks must be non-empty")
    for check in quality["checks"]:
        object_fields(check, "name passed evidence", "quality check")
        nonempty(check["name"], "quality check.name")
        boolean(check["passed"], "quality check.passed")
        nonempty(check["evidence"], "quality check.evidence")
    require(quality["passed"] == all(check["passed"] for check in quality["checks"]),
            "quality pass differs from checks")
    if outcome != "completed":
        require(not quality["passed"] and quality["score"] == 0, "failed outcome must have zero failed quality")

    review = root["review"]
    if review is not None:
        object_fields(review, "session_id author_session_ids passed findings", "review")
        nonempty(review["session_id"], "review.session_id")
        string_list(review["author_session_ids"], "review.author_session_ids")
        boolean(review["passed"], "review.passed")
        integer(review["findings"], "review.findings")
        require(review["session_id"] != trace["controller_session"], "controller cannot be independent reviewer")
        artifact_authors = sorted({span["session_id"] for span in spans if span["artifact_paths"]})
        require(sorted(review["author_session_ids"]) == artifact_authors,
                "review author IDs must match artifact-writing spans")
        require(review["session_id"] not in artifact_authors, "artifact author cannot be independent reviewer")
        require(any(span["role"] == "reviewer" and span["session_id"] == review["session_id"]
                    for span in execution["spans"]), "reviewer session has no reviewer span")
    if outcome == "completed" and route["verification_requirement"] == "INDEPENDENT_REVIEW":
        require(review is not None and review["passed"], "completed independent review requires passing distinct review")
    if route["verification_requirement"] == "SELF_CHECK":
        require(review is None, "SELF_CHECK must not claim independent review")

    cost = object_fields(root["cost"], "kind usd complete", "cost")
    enum(cost["kind"], ("measured", "estimated", "unavailable"), "cost.kind")
    boolean(cost["complete"], "cost.complete")
    if cost["kind"] == "unavailable":
        require(cost["usd"] is None and not cost["complete"], "unavailable cost must be null and incomplete")
    else:
        number(cost["usd"], "cost.usd")
    integer(root["retries"], "retries")
    integer(root["conflicts"], "conflicts")
    require(type(root["evidence"]) is list and bool(root["evidence"]), "evidence must be non-empty")
    for ref in root["evidence"]:
        object_fields(ref, "path sha256", "evidence")
        relative_name(ref["path"])
        digest(ref["sha256"], "evidence.sha256")
    if root["result_tree"] is not None:
        object_fields(root["result_tree"], "path sha256", "result_tree")
        relative_name(root["result_tree"]["path"])
        digest(root["result_tree"]["sha256"], "result_tree.sha256")
    require(outcome != "completed" or root["result_tree"] is not None, "completed run requires result tree")
    return root


def load_records(path: Path) -> list[dict]:
    records = []
    run_ids, slots = set(), set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = validate_record(parse_json(line))
            require(record["run_id"] not in run_ids, "duplicate run_id")
            run_ids.add(record["run_id"])
            slot = (record["case_id"], record["repetition"], record["treatment"])
            require(slot not in slots, "duplicate scheduled slot")
            slots.add(slot)
            records.append(record)
        except (ValueError, OverflowError) as error:
            raise ContractError(f"line {line_number}: {error}") from error
    require(bool(records), "records must not be empty")
    return records
