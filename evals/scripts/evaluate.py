#!/usr/bin/env python3
"""Validate and summarize router scenario campaigns without performance claims."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Optional

try:
    from . import contract as c
    from .fixture import fixture_for_case, grade, load_fixture
except ImportError:
    import contract as c
    from fixture import fixture_for_case, grade, load_fixture


def validate_campaign(records_path: Path, manifest_path: Path) -> tuple[dict, list[dict], dict]:
    manifest, cases = c.load_manifest(manifest_path)
    records = c.load_records(records_path)
    case_map = {case["id"]: case for case in cases}
    expected = {(case["id"], repetition, treatment) for case in cases
                for repetition in range(1, manifest["repetitions"] + 1) for treatment in c.TREATMENTS}
    actual = {(record["case_id"], record["repetition"], record["treatment"]) for record in records}
    c.require(actual == expected,
              f"scheduled records mismatch: missing={len(expected - actual)}, extra={len(actual - expected)}")
    manifest_hash = c.sha256(manifest_path)
    pair_slots: dict[str, tuple[str, int]] = {}
    results = {}
    for record in records:
        c.require(record["benchmark_id"] == manifest["benchmark_id"], "benchmark_id mismatch")
        c.require(record["manifest_sha256"] == manifest_hash, "manifest hash mismatch")
        slot = (record["case_id"], record["repetition"])
        c.require(record["pair_id"] not in pair_slots or pair_slots[record["pair_id"]] == slot,
                  "pair_id reused across case/repetition")
        pair_slots[record["pair_id"]] = slot
        for evidence in record["evidence"]:
            evidence_path = c.hash_ref(manifest_path.parent, evidence, "run evidence")
            evidence_value = c.read_json(evidence_path)
            c.object_fields(evidence_value, "data_origin run_id note", "run evidence payload")
            c.require(evidence_value["data_origin"] == manifest["data_origin"], "evidence data_origin mismatch")
            c.require(evidence_value["run_id"] == record["run_id"], "evidence run_id mismatch")
            c.nonempty(evidence_value["note"], "run evidence note")
        case = case_map[record["case_id"]]
        actual_span_ids = {span["id"] for span in record["execution"]["spans"]}
        expected_span_ids = set(case["required_span_ids"][record["treatment"]])
        actual_dependency_edges = {f"{dependency}->{span['id']}"
                                   for span in record["execution"]["spans"]
                                   for dependency in span["depends_on"]}
        expected_dependency_edges = set(case["required_dependency_edges"][record["treatment"]])
        actual_receipt_edges = {f"{receipt['producer_span_id']}->{consumer}"
                                for receipt in record["receipts"]
                                for consumer in receipt["consumer_span_ids"]}
        expected_receipt_edges = set(case["required_receipt_edges"][record["treatment"]])
        if record["outcome"] == "completed":
            c.require(actual_span_ids == expected_span_ids, "completed span inventory does not match frozen case")
            c.require(actual_dependency_edges == expected_dependency_edges,
                      "record dependency edges do not match frozen case")
            c.require(actual_receipt_edges == expected_receipt_edges,
                      "record receipt edges do not match frozen case")
        else:
            c.require(actual_span_ids.issubset(expected_span_ids), "failed run contains impossible downstream span")
            c.require(actual_dependency_edges.issubset(expected_dependency_edges),
                      "failed run contains non-frozen dependency edge")
            c.require(actual_receipt_edges.issubset(expected_receipt_edges),
                      "failed run contains non-frozen receipt edge")
            for edge in expected_dependency_edges:
                producer, consumer = edge.split("->", 1)
                if consumer in actual_span_ids:
                    c.require(producer in actual_span_ids and edge in actual_dependency_edges,
                              "failed run span prefix omits prerequisite dependency")
            for edge in expected_receipt_edges:
                producer, consumer = edge.split("->", 1)
                if consumer in actual_span_ids:
                    c.require(producer in actual_span_ids and edge in actual_receipt_edges,
                              "failed run span prefix omits required receipt consumption")
        expected_route = case["route_expectations"][record["treatment"]]
        actual_route = {field: record["route_trace"][field] for field in expected_route}
        record["_route_adherent"] = actual_route == expected_route
        if record["result_tree"] is not None:
            candidate = c.safe_path(manifest_path.parent, record["result_tree"]["path"])
            fixture_path = fixture_for_case(manifest_path.parent, case)
            results[record["run_id"]] = grade(fixture_path, candidate, record["result_tree"]["sha256"])
        elif record["outcome"] == "completed":
            raise c.ContractError("completed run requires result tree")
        effective_quality, process_compliance = derive_results(case, record, results.get(record["run_id"]))
        record["_effective_quality"] = effective_quality
        record["_process_compliance"] = process_compliance
        record["_claimed_quality_disagrees"] = (
            record["quality"]["passed"] != record["_effective_quality"]["passed"]
            or record["quality"]["score"] != record["_effective_quality"]["score"]
            or {check["name"] for check in record["quality"]["checks"]} != set(case["required_quality_checks"])
        )
    c.require(all(set(c.TREATMENTS) == {record["treatment"] for record in records if record["pair_id"] == pair_id}
                  for pair_id in pair_slots), "pair treatment inventory mismatch")
    return manifest, records, results


def derive_results(case: dict, record: dict, grading: Optional[dict]) -> tuple[dict, dict]:
    spans = record["execution"]["spans"]
    by_id = {span["id"]: span for span in spans}
    artifact_writers = [span for span in spans if span["artifact_paths"]]
    artifact_paths = [path for span in artifact_writers for path in span["artifact_paths"]]
    linked_receipts = [receipt for receipt in record["receipts"] if receipt["consumer_span_ids"]]
    preserved_facts = {fact for receipt in linked_receipts for fact in receipt["content"]["facts"]}
    linked_consumer_tokens = sum(by_id[consumer]["input_tokens"] for receipt in linked_receipts
                                 for consumer in receipt["consumer_span_ids"])
    expected_route = case["route_expectations"][record["treatment"]]
    actual_route = {field: record["route_trace"][field] for field in expected_route}
    review = record["review"]
    quality_values = {"artifact-exact": grading is not None and grading["passed"]}
    process_values = {
        "route-adherent": actual_route == expected_route,
        "receipt-preserved-facts": set(case["required_receipt_facts"]).issubset(preserved_facts)
                                   and bool(linked_receipts) and linked_consumer_tokens > 0,
        "scope-transition-safe": record["route_trace"]["initial_ownership"] == "DELEGATE"
                                 or (record["route_trace"]["initial_ownership"] == "DIRECT"
                                     and record["route_trace"]["final_ownership"] == "DELEGATE"
                                     and record["route_trace"]["escalation_trigger"] == "scope-expanded"),
        "dependency-order": {f"{dependency}->{span['id']}" for span in spans
                             for dependency in span["depends_on"]}
                            == set(case["required_dependency_edges"][record["treatment"]]),
        "artifact-path-coverage": set(artifact_paths) == set(case["required_artifact_paths"]),
        "disjoint-write-ownership": len(artifact_paths) == len(set(artifact_paths)),
        "independent-review": review is not None and review["passed"],
    }
    quality_checks = [{"name": name, "passed": quality_values[name],
                       "evidence": "recomputed from frozen artifact acceptance"}
                      for name in case["required_quality_checks"]]
    process_checks = [{"name": name, "passed": process_values[name],
                       "evidence": "recomputed from frozen treatment process"}
                      for name in case["required_process_checks"][record["treatment"]]]
    quality_passed = record["outcome"] == "completed" and all(check["passed"] for check in quality_checks)
    quality_score = 0 if record["outcome"] != "completed" else round(
        100 * sum(check["passed"] for check in quality_checks) / len(quality_checks), 2)
    process_passed = all(check["passed"] for check in process_checks)
    return ({"passed": quality_passed, "score": quality_score, "checks": quality_checks},
            {"passed": process_passed, "checks": process_checks})


def mean(records: list[dict], getter) -> float:
    return statistics.mean(getter(record) for record in records)


def treatment_summary(records: list[dict], grades: dict) -> dict:
    def effective_pass(record: dict) -> bool:
        return record["_effective_quality"]["passed"]

    measured = [record["cost"]["usd"] for record in records
                if record["cost"]["kind"] == "measured" and record["cost"]["complete"]]
    estimated = [record["cost"]["usd"] for record in records
                 if record["cost"]["kind"] == "estimated" and record["cost"]["complete"]]
    return {
        "runs": len(records),
        "completion_rate": mean(records, lambda record: float(record["outcome"] == "completed")),
        "quality_pass_rate": mean(records, lambda record: float(effective_pass(record))),
        "mean_quality_score": mean(records, lambda record: record["_effective_quality"]["score"]),
        "process_compliance_rate": mean(records, lambda record: float(record["_process_compliance"]["passed"])),
        "route_adherence_rate": mean(records, lambda record: float(record["_route_adherent"])),
        "mean_wall_time_ms": mean(records, lambda record: record["execution"]["wall_time_ms"]),
        "mean_critical_path_ms": mean(records, lambda record: record["execution"]["critical_path_ms"]),
        "mean_controller_input_tokens": mean(records, lambda record: record["context"]["controller_input_tokens"]),
        "mean_worker_input_tokens": mean(records, lambda record: record["context"]["worker_input_tokens"]),
        "mean_receipt_tokens": mean(records, lambda record: record["context"]["receipt_tokens"]),
        "mean_receipt_links": mean(records, lambda record: sum(
            len(receipt["consumer_span_ids"]) for receipt in record["receipts"])),
        "mean_tool_calls": mean(records, lambda record: record["execution"]["tool_calls"]),
        "mean_raw_log_bytes": mean(records, lambda record: record["execution"]["raw_log_bytes"]),
        "retries": sum(record["retries"] for record in records),
        "conflicts": sum(record["conflicts"] for record in records),
        "measured_cost": {"complete_runs": len(measured),
                          "mean_usd": statistics.mean(measured) if measured else None},
        "estimated_cost": {"complete_runs": len(estimated),
                           "mean_usd": statistics.mean(estimated) if estimated else None},
    }


def analyze(manifest: dict, records: list[dict], grades: dict) -> dict:
    by_treatment = {treatment: [record for record in records if record["treatment"] == treatment]
                    for treatment in c.TREATMENTS}
    case_results = {}
    for case_id in sorted({record["case_id"] for record in records}):
        rows = {treatment: [record for record in records
                            if record["case_id"] == case_id and record["treatment"] == treatment]
                for treatment in c.TREATMENTS}
        summaries = {treatment: treatment_summary(values, grades) for treatment, values in rows.items()}
        mandatory = summaries["mandatory_delegate"]["mean_critical_path_ms"]
        selective = summaries["selective"]["mean_critical_path_ms"]
        selective_links = [(record, receipt, consumer)
                           for record in rows["selective"] for receipt in record["receipts"]
                           for consumer in receipt["consumer_span_ids"]]
        case_results[case_id] = {
            "treatments": summaries,
            "critical_path_ratio_selective_over_mandatory": selective / mandatory if mandatory else None,
            "selective_receipt_links": len(selective_links),
            "selective_linked_consumer_input_tokens": sum(
                next(span["input_tokens"] for span in record["execution"]["spans"] if span["id"] == consumer)
                for record, _receipt, consumer in selective_links),
        }
    route_failures = [record["run_id"] for record in records if not record["_route_adherent"]]
    artifact_failures = [run_id for run_id, result in grades.items() if not result["passed"]]
    return {
        "schema_version": c.VERSION,
        "benchmark_id": manifest["benchmark_id"],
        "data_origin": manifest["data_origin"],
        "release_claim_supported": False,
        "limitations": [
            "Synthetic observations do not establish live-model quality, savings, or latency.",
            "Estimated costs are reported separately from measured costs and are not interchangeable.",
            "Receipt links and linked-consumer context are synthetic harness observations, not live savings estimates.",
        ],
        "scheduled_runs": len(records),
        "failures_by_outcome": {outcome: sum(record["outcome"] == outcome for record in records)
                                for outcome in c.OUTCOMES if outcome != "completed"},
        "route_failures": route_failures,
        "process_failures": [record["run_id"] for record in records
                             if not record["_process_compliance"]["passed"]],
        "artifact_failures": artifact_failures,
        "claimed_quality_disagreements": [record["run_id"] for record in records
                                          if record["_claimed_quality_disagrees"]],
        "treatments": {treatment: treatment_summary(values, grades)
                       for treatment, values in by_treatment.items()},
        "cases": case_results,
    }


def compare(records_path: Path, manifest_path: Path) -> dict:
    return analyze(*validate_campaign(records_path, manifest_path))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    cases = sub.add_parser("validate-cases")
    cases.add_argument("--cases", type=Path, required=True)
    manifest = sub.add_parser("validate-manifest")
    manifest.add_argument("--manifest", type=Path, required=True)
    records = sub.add_parser("validate-records")
    records.add_argument("--records", type=Path, required=True)
    records.add_argument("--manifest", type=Path, required=True)
    comparison = sub.add_parser("compare")
    comparison.add_argument("--records", type=Path, required=True)
    comparison.add_argument("--manifest", type=Path, required=True)
    grader = sub.add_parser("grade")
    grader.add_argument("--fixture", type=Path, required=True)
    grader.add_argument("--candidate", type=Path, required=True)
    grader.add_argument("--sha256")
    args = parser.parse_args()
    try:
        if args.command == "validate-cases":
            loaded = c.load_cases(args.cases)
            for case in loaded:
                load_fixture(fixture_for_case(args.cases.parent, case))
            result = {"validated_cases": len(loaded)}
        elif args.command == "validate-manifest":
            loaded, case_list = c.load_manifest(args.manifest)
            for case in case_list:
                load_fixture(fixture_for_case(args.manifest.parent, case))
            result = {"validated_manifest": loaded["benchmark_id"]}
        elif args.command == "validate-records":
            _, loaded, _ = validate_campaign(args.records, args.manifest)
            result = {"validated_records": len(loaded)}
        elif args.command == "compare":
            result = compare(args.records, args.manifest)
        else:
            result = grade(args.fixture, args.candidate, args.sha256)
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
        return 0
    except (OSError, ValueError, OverflowError) as error:
        print(f"ERROR: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
