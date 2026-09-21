#!/usr/bin/env python3
"""Strict schema-v2 offline evaluation; no live models or release claims."""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    from . import contract as c
    from .fixture import fixture_for_case, grade, load_fixture, tree_digest, tree_snapshot
except ImportError:
    import contract as c
    from fixture import fixture_for_case, grade, load_fixture, tree_digest, tree_snapshot


def bootstrap_interval(pairs: list[tuple[float, float]], statistic: Any,
                       seed: int = 17, samples: int = 2000) -> dict:
    """Resample whole pairs. Any undefined resample makes the interval undefined."""
    estimate, reason = statistic(pairs)
    if reason:
        return {"estimate": None, "bootstrap_95": None, "reason": reason}
    rng = random.Random(seed)
    estimates = []
    for _ in range(samples):
        value, reason = statistic([pairs[rng.randrange(len(pairs))] for _ in pairs])
        if reason:
            return {"estimate": estimate, "bootstrap_95": None, "reason": f"undefined bootstrap resample: {reason}"}
        estimates.append(value)
    estimates.sort()
    return {"estimate": estimate,
            "bootstrap_95": [estimates[int(samples * .025)], estimates[int(samples * .975)]],
            "reason": None}


def mean_difference(pairs: list[tuple[float, float]]) -> tuple[float, None]:
    return statistics.mean(r - b for b, r in pairs), None


def ratio_of_means(pairs: list[tuple[float, float]]) -> tuple[float | None, str | None]:
    baseline = statistics.mean(b for b, _ in pairs)
    if baseline == 0:
        return None, "baseline mean is zero"
    ratio = statistics.mean(r for _, r in pairs) / baseline
    if not math.isfinite(ratio):
        return None, "ratio is non-finite"
    return ratio, None


def load_experiment(manifest_path: Path) -> tuple[dict, list[dict], dict]:
    manifest, cases = c.load_manifest(manifest_path)
    cases_root = c.safe_path(manifest_path.parent, manifest["cases_file"]).parent
    fixtures = {case["id"]: fixture_for_case(cases_root, case) for case in cases}
    for fixture_path in fixtures.values():
        if fixture_path:
            initial = load_fixture(fixture_path)["initial"]["tree"]
            c.require(manifest["controls"]["repository_revision"] == tree_digest(initial),
                      "fixture initial tree/repository_revision provenance mismatch")
    return manifest, cases, fixtures


def validate_campaign(records_path: Path, manifest_path: Path) -> tuple[dict, list[dict], dict]:
    manifest, cases, fixtures = load_experiment(manifest_path)
    records = c.load_records(records_path)
    expected = {(case["id"], rep, variant) for case in cases
                for rep in range(1, manifest["repetitions_per_case"] + 1) for variant in c.VARIANTS}
    actual = {(r["case_id"], r["repetition"], r["variant"]) for r in records}
    c.require(actual == expected,
              f"scheduled records mismatch: missing={len(expected - actual)}, extra={len(actual - expected)}")
    manifest_hash, sessions, conventions, grades = c.sha256(manifest_path), set(), set(), {}
    for record in records:
        c.require(record["experiment_id"] == manifest["experiment_id"], "experiment_id mismatch")
        c.require(record["manifest_sha256"] == manifest_hash, "manifest hash mismatch")
        c.require(record["environment"] == manifest["controls"], "environment provenance mismatch")
        inventory = c.session_inventory(record["route_trace"])
        c.require(not sessions.intersection(inventory), "session reused across runs")
        sessions.update(inventory)
        for evidence in record["evidence"]:
            c.hash_ref(manifest_path.parent, evidence, "run evidence")
        convention = c.validate_accounting(manifest_path.parent, record, manifest)
        if convention:
            conventions.add(convention)
        fixture = fixtures[record["case_id"]]
        candidate = record["result_tree"]
        if fixture and record["outcome"] == "completed":
            c.require(candidate is not None, "completed fixture run requires result_tree")
        if candidate:
            path = c.safe_path(manifest_path.parent, candidate["path"])
            if fixture:
                grades[record["run_id"]] = grade(fixture, path, candidate["sha256"])
            else:
                c.require(tree_digest(tree_snapshot(path)) == candidate["sha256"], "candidate tree hash mismatch")
    c.require(len(conventions) <= 1, "incompatible accounting conventions")
    return manifest, records, grades


def analyze(manifest: dict, records: list[dict], grades: dict) -> dict:
    pairs = {}
    for record in records:
        pairs.setdefault((record["case_id"], record["repetition"]), {})[record["variant"]] = record
    paired = [pairs[key] for key in sorted(pairs)]
    baseline = [pair["baseline"] for pair in paired]
    router = [pair["router"] for pair in paired]

    def passed(record: dict) -> bool:
        return record["passed"] and grades.get(record["run_id"], {"passed": True})["passed"]

    quality = bootstrap_interval([(float(passed(b)), float(passed(r))) for b, r in zip(baseline, router)], mean_difference)
    latency = bootstrap_interval([(b["duration_ms"], r["duration_ms"]) for b, r in zip(baseline, router)], ratio_of_means)
    complete = all(r["cost"]["complete"] for r in records)
    cost = (bootstrap_interval([(b["cost"]["cost_usd"], r["cost"]["cost_usd"]) for b, r in zip(baseline, router)], ratio_of_means)
            if complete else {"estimate": None, "bootstrap_95": None, "reason": "incomplete accounting"})
    safety = {field: {variant: sum(r[field] for r in records if r["variant"] == variant)
                      for variant in c.VARIANTS} for field in c.SAFETY_FIELDS}
    policy_failures = sum(r["route_trace"]["controller_business_actions"] > 0 or
                         (r["outcome"] == "completed" and
                          (not r["route_trace"]["worker_sessions"] or r["route_trace"]["worker_business_actions"] == 0))
                         for r in router)
    pass_rates = {variant: statistics.mean(float(passed(r)) for r in records if r["variant"] == variant)
                  for variant in c.VARIANTS}
    analysis = manifest["analysis"]

    def upper_gate(measurement: dict, threshold: float) -> bool | None:
        return None if measurement["bootstrap_95"] is None else measurement["bootstrap_95"][1] <= threshold

    gates = {
        "complete_planned_pairs": True,
        "quality_non_inferior": quality["bootstrap_95"][0] >= analysis["quality_margin"],
        "critical_quality_floor": pass_rates["router"] >= analysis["minimum_router_quality"],
        "no_critical_safety_failure": all(safety[field]["router"] == 0 for field in c.SAFETY_FIELDS),
        "pure_orchestrator": policy_failures == 0,
        "cost_threshold": upper_gate(cost, analysis["maximum_cost_ratio"]),
        "latency_not_regressed": upper_gate(latency, analysis["maximum_latency_ratio"]),
    }
    failed = [name for name, value in gates.items() if value is False]
    missing = [name for name, value in gates.items() if value is None]
    release_reasons = ["statistical release gate is not implemented in this integrity slice"]
    if manifest["status"] == "draft":
        release_reasons.append("draft campaign")
    if manifest["data_origin"] == "synthetic":
        release_reasons.append("synthetic observations")
    return {
        "schema_version": c.VERSION, "experiment_id": manifest["experiment_id"],
        "status": "fail" if failed else "inconclusive", "release_pass": False,
        "release_blockers": release_reasons, "failed_gates": failed, "missing_gates": missing,
        "pairs": len(paired), "quality_pass_rate": pass_rates,
        "quality_difference_router_minus_baseline": quality,
        "latency_ratio_router_over_baseline": latency,
        "cost_ratio_router_over_baseline": cost, "cost_complete": complete,
        "safety_by_category": safety, "router_policy_failures": policy_failures,
        "oracle_results": grades, "gates": gates,
    }


def compare(records_path: Path, manifest_path: Path) -> dict:
    manifest, records, grades = validate_campaign(records_path, manifest_path)
    return analyze(manifest, records, grades)


def collect_ccusage(thread_ids: list[str], output: Path) -> dict:
    c.string_list(thread_ids, "thread_ids")
    sessions = []
    for thread_id in thread_ids:
        completed = subprocess.run(["ccusage", "session", "--id", thread_id, "--json", "--offline"],
                                   capture_output=True, text=True, timeout=30, check=False)
        c.require(completed.returncode == 0, f"ccusage failed for {thread_id}")
        raw = c.parse_json(completed.stdout)
        c.require(type(raw) is dict and type(raw.get("sessions")) is list, "ccusage sessions must be an array")
        matches = [row for row in raw["sessions"] if type(row) is dict and row.get("sessionId") == thread_id]
        c.require(len(matches) == 1, f"ccusage must return exactly one matching session for {thread_id}")
        c.number(matches[0].get("totalCost"), "observed totalCost")
        sessions.append({"session_id": thread_id, "observed_cost_usd": matches[0]["totalCost"]})
    result = {"schema_version": c.VERSION, "source": "ccusage", "verified": False,
              "cost_complete": False, "cost_usd": None, "observations": sessions,
              "reason": "provider identity and complete descendant accounting are unverified"}
    output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    cases = sub.add_parser("validate-cases", aliases=["validate"])
    cases.add_argument("--cases", type=Path, required=True)
    manifest = sub.add_parser("validate-manifest")
    manifest.add_argument("--manifest", type=Path, required=True)
    records = sub.add_parser("validate-records")
    records.add_argument("--records", type=Path, required=True)
    records.add_argument("--manifest", type=Path, required=True, help="Required to check schedule, hashes, and provenance.")
    comparison = sub.add_parser("compare")
    comparison.add_argument("--records", type=Path, required=True)
    comparison.add_argument("--manifest", type=Path, required=True)
    grader = sub.add_parser("grade")
    grader.add_argument("--fixture", type=Path, required=True)
    grader.add_argument("--candidate", type=Path, required=True)
    grader.add_argument("--sha256", required=True, help="Expected canonical candidate tree digest.")
    usage = sub.add_parser("ccusage", aliases=["collect-cost"])
    usage.add_argument("--thread-id", action="append", required=True)
    usage.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command in ("validate-cases", "validate"):
            cases = c.load_cases(args.cases)
            for case in cases:
                fixture_for_case(args.cases.parent, case)
            result, code = {"validated_cases": len(cases)}, 0
        elif args.command == "validate-manifest":
            manifest, _, _ = load_experiment(args.manifest)
            result, code = {"validated_manifest": manifest["experiment_id"]}, 0
        elif args.command == "validate-records":
            _, records, _ = validate_campaign(args.records, args.manifest)
            result, code = {"validated_records": len(records)}, 0
        elif args.command == "compare":
            result = compare(args.records, args.manifest)
            code = 2
        elif args.command == "grade":
            result = grade(args.fixture, args.candidate, args.sha256)
            code = 0 if result["passed"] else 2
        else:
            result, code = collect_ccusage(args.thread_id, args.output), 2
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
        return code
    except (OSError, ValueError, OverflowError, subprocess.TimeoutExpired) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
