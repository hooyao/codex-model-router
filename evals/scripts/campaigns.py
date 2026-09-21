#!/usr/bin/env python3
"""Generate deterministic, explicitly synthetic campaigns in a new directory."""

from __future__ import annotations

import argparse
import copy
import json
import shutil
from pathlib import Path

try:
    from . import contract as c
    from .fixture import load_fixture, tree_digest, tree_snapshot
except ImportError:
    import contract as c
    from fixture import load_fixture, tree_digest, tree_snapshot

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "small-edit"
SCENARIOS = {
    "complete-draft": "inconclusive",
    "missing-cost": "inconclusive",
    "safety-missing-cost": "fail",
    "quality-missing-cost": "fail",
    "unchanged-candidate": "fail",
    "controller-edit": "fail",
    "cost-regression": "fail",
    "latency-regression": "fail",
    "zero-baseline-cost": "inconclusive",
    "zero-baseline-latency": "inconclusive",
}


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def reference(root: Path, path: Path) -> dict:
    return {"path": path.relative_to(root).as_posix(), "sha256": c.sha256(path)}


def write_records(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")


def generate(root: Path, scenarios: tuple[str, ...] | None = None) -> None:
    """Refuse existing output; generated fixtures are copies of the sole task."""
    fixture = load_fixture(FIXTURE_ROOT / "fixture.json")
    root.mkdir(parents=True, exist_ok=False)
    shutil.copytree(FIXTURE_ROOT, root / "fixtures" / "small-edit")
    fixture_path = root / "fixtures" / "small-edit" / "fixture.json"
    cases = [{"id": "small-edit", "status": "release", "category": "small-edit",
              "prompt": fixture["prompt"], "expected_mode": "worker",
              "acceptance": ["Exact reference tree", "Router uses a bounded worker"],
              "failure_signals": ["Unrelated edit", "Controller performs business work"],
              "fixture": reference(root, fixture_path)}]
    write_json(root / "cases.json", cases)
    expected = {}
    for scenario in scenarios or tuple(SCENARIOS):
        c.require(scenario in SCENARIOS, f"unknown scenario: {scenario}")
        manifest = {
            "schema_version": 2, "experiment_id": f"synthetic-{scenario}",
            "status": "draft", "data_origin": "synthetic", "cases_file": "cases.json",
            "cases_sha256": c.sha256(root / "cases.json"), "variants": list(c.VARIANTS),
            "repetitions_per_case": 2,
            "analysis": {"quality_margin": -.02, "minimum_router_quality": .95,
                         "maximum_cost_ratio": .90, "maximum_latency_ratio": 1.10},
            "controls": {"repository_revision": tree_digest(fixture["initial"]["tree"]),
                         "harness_version": "offline-fixture-v2", "environment_id": "synthetic-only",
                         "policy": "pure-orchestrator-v1"},
        }
        manifest_path = root / f"{scenario}.manifest.json"
        write_json(manifest_path, manifest)
        records = []
        for repetition in range(1, 3):
            for variant in c.VARIANTS:
                run_id = f"{scenario}-{repetition}-{variant}"
                routed = variant == "router"
                candidate = root / "fixtures" / "small-edit" / ("initial" if routed and scenario == "unchanged-candidate" else "reference")
                record = {
                    "schema_version": 2, "experiment_id": manifest["experiment_id"],
                    "manifest_sha256": c.sha256(manifest_path), "run_id": run_id,
                    "pair_id": f"{scenario}-small-edit-{repetition}", "case_id": "small-edit",
                    "variant": variant, "repetition": repetition, "outcome": "completed",
                    "passed": not (routed and scenario == "quality-missing-cost"),
                    "quality_score": 0 if routed and scenario == "quality-missing-cost" else 4,
                    "duration_ms": 900 if routed else 1000,
                    "delegated_tasks": int(routed), "retries": 0,
                    "route_trace": {"controller_session": run_id + "-controller",
                                    "worker_sessions": [run_id + "-worker"] if routed else [],
                                    "retry_sessions": [], "verification_sessions": [], "abandoned_sessions": [],
                                    "controller_business_actions": int(not routed),
                                    "worker_business_actions": int(routed)},
                    "environment": copy.deepcopy(manifest["controls"]),
                    "result_tree": {"path": candidate.relative_to(root).as_posix(),
                                    "sha256": tree_digest(tree_snapshot(candidate))},
                    **{field: False for field in c.SAFETY_FIELDS},
                }
                evidence_path = root / "evidence" / f"{run_id}.json"
                write_json(evidence_path, {"data_origin": "synthetic", "run_id": run_id,
                                           "note": "Deterministic test evidence; no model or provider was called."})
                evidence = reference(root, evidence_path)
                record["evidence"] = [evidence]
                if scenario == "safety-missing-cost" and routed:
                    record["scope_leak"] = True
                if scenario == "controller-edit" and routed:
                    record["route_trace"]["controller_business_actions"] = 1
                if scenario == "latency-regression" and routed:
                    record["duration_ms"] = 2000
                if scenario == "zero-baseline-latency" and not routed:
                    record["duration_ms"] = 0
                if "missing-cost" in scenario:
                    record["cost"] = {"source": "unavailable", "complete": False, "cost_usd": None, "ledger": None}
                else:
                    total = .008 if routed else .01
                    if scenario == "cost-regression" and routed:
                        total = .02
                    if scenario == "zero-baseline-cost" and not routed:
                        total = 0
                    sessions = sorted(c.session_inventory(record["route_trace"]))
                    ledger = {"schema_version": 2, "experiment_id": manifest["experiment_id"],
                              "run_id": run_id, "source": "synthetic", "provider": "synthetic",
                              "currency": "USD", "method": "synthetic", "pricing_version": "test-v1",
                              "sessions": [{"session_id": session, "exclusive_cost_usd": total / len(sessions),
                                            "evidence": evidence} for session in sessions]}
                    ledger_path = root / "ledgers" / f"{run_id}.json"
                    write_json(ledger_path, ledger)
                    record["cost"] = {"source": "synthetic", "complete": True, "cost_usd": total,
                                      "ledger": reference(root, ledger_path)}
                records.append(record)
        write_records(root / f"{scenario}.runs.jsonl", records)
        expected[scenario] = {"status": SCENARIOS[scenario], "release_pass": False, "comparison_exit": 2}
    write_json(root / "expected.json", expected)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Must not already exist.")
    args = parser.parse_args()
    try:
        generate(args.output)
    except (OSError, ValueError) as error:
        parser.exit(1, f"ERROR: {error}\n")
    print(f"Generated {len(SCENARIOS)} synthetic campaigns in {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
