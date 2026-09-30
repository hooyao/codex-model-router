#!/usr/bin/env python3
"""Deterministic offline runner for the router scenario benchmark."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

try:
    from . import contract as c
    from .fixture import fixture_for_case, grade, load_fixture, tree_digest, tree_snapshot
except ImportError:
    import contract as c
    from fixture import fixture_for_case, grade, load_fixture, tree_digest, tree_snapshot


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def hash_reference(root: Path, path: Path) -> dict:
    return {"path": path.relative_to(root).as_posix(), "sha256": c.sha256(path)}


def span(run_id: str, identity: str, role: str, start: int, end: int,
         artifacts: list[str], depends: list[str], input_tokens: int,
         receipt_tokens: int, tools: int, logs: int) -> dict:
    session = run_id + ("-controller" if role == "controller" else f"-{identity}-session")
    return {"id": identity, "session_id": session, "role": role,
            "start_ms": start, "end_ms": end, "depends_on": depends,
            "artifact_paths": artifacts, "input_tokens": input_tokens,
            "receipt_tokens": receipt_tokens, "tool_calls": tools, "log_bytes": logs}


def profile(case_id: str, treatment: str, run_id: str, changed: list[str]) -> tuple[list[dict], int]:
    """Return deterministic spans and retry count."""
    if treatment == "direct" or (case_id == "direct-small-control" and treatment == "selective"):
        settings = {
            "direct-small-control": (90, 900, 2, 300),
            "investigation-reuse": (420, 8200, 10, 32000),
            "serial-escalation": (480, 6200, 12, 18000),
            "parallel-disjoint": (330, 3600, 9, 9000),
            "architecture-review": (250, 5200, 7, 12000),
        }[case_id]
        duration, tokens, tools, logs = settings
        return [span(run_id, "controller-work", "controller", 0, duration, changed, [], tokens, 0, tools, logs)], 0

    if case_id == "direct-small-control":
        duration = 125 if treatment == "mandatory_delegate" else 105
        return [span(run_id, "edit", "worker", 0, duration, changed, [], 1100, 70, 2, 400)], 0
    if case_id == "investigation-reuse":
        if treatment == "mandatory_delegate":
            spans = [span(run_id, "investigate", "worker", 0, 180, [], [], 5600, 220, 8, 21000),
                     span(run_id, "implement", "worker", 190, 310, changed, ["investigate"], 1400, 80, 3, 1800)]
            return spans, 0
        spans = [span(run_id, "investigate", "worker", 0, 145, [], [], 4300, 180, 7, 15000),
                 span(run_id, "implement", "worker", 155, 245, changed, ["investigate"], 900, 60, 2, 900)]
        return spans, 0
    if case_id == "serial-escalation":
        if treatment == "selective":
            spans = [span(run_id, "bounded-discovery", "controller", 0, 30, [], [], 700, 0, 1, 500),
                     span(run_id, "plan", "worker", 40, 85, [], [], 1300, 100, 2, 1200),
                     span(run_id, "schema", "worker", 95, 145, ["src/schema.json"], ["plan"], 900, 50, 2, 700),
                     span(run_id, "api", "worker", 155, 205, ["src/api.json"], ["schema"], 700, 40, 2, 600),
                     span(run_id, "contract-test", "worker", 215, 265, ["tests/expected.json"], ["api"], 700, 40, 2, 600)]
            return spans, 0
        spans = [span(run_id, "plan", "worker", 0, 60, [], [], 1600, 110, 2, 1500),
                 span(run_id, "schema", "worker", 70, 135, ["src/schema.json"], ["plan"], 1000, 60, 2, 800),
                 span(run_id, "api", "worker", 145, 210, ["src/api.json"], ["schema"], 900, 50, 2, 700),
                 span(run_id, "contract-test", "worker", 220, 285, ["tests/expected.json"], ["api"], 900, 50, 2, 700)]
        return spans, 0
    if case_id == "parallel-disjoint":
        paths = ["reports/alpha.md", "reports/beta.md", "reports/gamma.md"]
        if treatment == "selective":
            spans = [span(run_id, name, "worker", 0, 95, [path], [], 850, 45, 2, 650)
                     for name, path in zip(("alpha", "beta", "gamma"), paths)]
            return spans, 0
        spans = [span(run_id, name, "worker", start, start + 95, [path], [], 900, 45, 2, 700)
                 for name, path, start in zip(("alpha", "beta", "gamma"), paths, (0, 105, 210))]
        return spans, 0
    if case_id == "architecture-review":
        author_end, review_end = ((180, 265) if treatment == "mandatory_delegate" else (155, 225))
        spans = [span(run_id, "author", "worker", 0, author_end, changed, [], 4100, 160, 5, 7000),
                 span(run_id, "independent-review", "reviewer", author_end + 10, review_end, [], ["author"], 1900, 90, 3, 2500)]
        return spans, 0
    raise c.ContractError(f"unknown profile: {case_id}/{treatment}")


def route_events(expectation: dict) -> list[dict]:
    if expectation["initial_ownership"] != expectation["final_ownership"]:
        return [
            {"sequence": 1, "at_ms": 0, "ownership": expectation["initial_ownership"], "topology": "NONE", "trigger": None},
            {"sequence": 2, "at_ms": expectation["first_delegation_ms"], "ownership": expectation["final_ownership"],
             "topology": expectation["delegate_topology"], "trigger": expectation["escalation_trigger"]},
        ]
    return [{"sequence": 1, "at_ms": 0, "ownership": expectation["final_ownership"],
             "topology": expectation["delegate_topology"], "trigger": None}]


def build_receipts(case: dict, spans: list[dict]) -> list[dict]:
    receipts = []
    for producer in spans:
        if producer["receipt_tokens"] == 0:
            continue
        consumers = [span_["id"] for span_ in spans if producer["id"] in span_["depends_on"]]
        if case["id"] == "investigation-reuse" and producer["id"] == "investigate":
            facts = list(case["required_receipt_facts"])
            artifact_refs = ["docs/ops.md"]
        else:
            facts = [f"case={case['id']}", f"producer={producer['id']}"]
            artifact_refs = list(producer["artifact_paths"])
        content = {"facts": facts, "artifact_refs": artifact_refs}
        receipts.append({"id": producer["id"] + "-receipt",
                         "producer_span_id": producer["id"],
                         "consumer_span_ids": consumers,
                         "token_count": producer["receipt_tokens"],
                         "content": content,
                         "sha256": c.receipt_sha256(content)})
    return receipts


def quality_claims(case: dict, artifact_passed: bool) -> list[dict]:
    values = {"artifact-exact": artifact_passed}
    return [{"name": name, "passed": values[name], "evidence": "exact-tree-v2"}
            for name in case["required_quality_checks"]]


def generate(root: Path, manifest_path: Path) -> tuple[Path, Path]:
    raw_manifest = c.read_json(manifest_path)
    c.require(type(raw_manifest) is dict and raw_manifest.get("data_origin") == "synthetic",
              "deterministic runner requires synthetic data_origin")
    manifest, cases = c.load_manifest(manifest_path)
    c.require(manifest["controls"]["environment_id"] == "deterministic-offline",
              "deterministic runner requires deterministic-offline environment")
    root.mkdir(parents=True, exist_ok=False)
    manifest_copy = root / "benchmark.json"
    cases_copy = root / "cases.json"
    shutil.copyfile(manifest_path, manifest_copy)
    shutil.copyfile(manifest_path.parent / manifest["cases_file"], cases_copy)
    shutil.copytree(manifest_path.parent / "fixtures", root / "fixtures")
    manifest_hash = c.sha256(manifest_copy)
    records = []
    for case in cases:
        fixture_path = fixture_for_case(manifest_path.parent, case)
        fixture = load_fixture(fixture_path)
        initial_files = fixture["initial"]["tree"]["files"]
        reference_files = fixture["reference"]["tree"]["files"]
        changed = sorted(path for path in set(initial_files) | set(reference_files)
                         if initial_files.get(path) != reference_files.get(path))
        for repetition in range(1, manifest["repetitions"] + 1):
            pair_id = f"{case['id']}-{repetition}"
            for treatment in c.TREATMENTS:
                run_id = f"{pair_id}-{treatment}"
                candidate = root / "results" / case["id"] / str(repetition) / treatment
                shutil.copytree(fixture_path.parent / fixture["reference"]["path"], candidate)
                grading = grade(fixture_path, candidate)
                spans, retries = profile(case["id"], treatment, run_id, changed)
                expectation = case["route_expectations"][treatment]
                is_delegate = expectation["final_ownership"] == "DELEGATE"
                controller_actions = 1 if treatment == "direct" or expectation["initial_ownership"] == "DIRECT" else 0
                worker_actions = sum(span_["role"] == "worker" for span_ in spans)
                context = {
                    "controller_input_tokens": sum(s["input_tokens"] for s in spans if s["role"] == "controller"),
                    "worker_input_tokens": sum(s["input_tokens"] for s in spans if s["role"] != "controller"),
                    "receipt_tokens": sum(s["receipt_tokens"] for s in spans if s["role"] != "controller"),
                }
                receipts = build_receipts(case, spans)
                review = None
                if expectation["verification_requirement"] == "INDEPENDENT_REVIEW":
                    reviewer = next(s for s in spans if s["role"] == "reviewer")
                    review = {"session_id": reviewer["session_id"],
                              "author_session_ids": [s["session_id"] for s in spans if s["role"] == "worker"],
                              "passed": True, "findings": 1}
                checks = quality_claims(case, grading["passed"])
                passed = all(check["passed"] for check in checks)
                score = round(100 * sum(check["passed"] for check in checks) / len(checks), 2)
                evidence_path = root / "evidence" / f"{run_id}.json"
                write_json(evidence_path, {"data_origin": "synthetic", "run_id": run_id,
                                           "note": "Deterministic fixture observation; no model was called."})
                total_tokens = context["controller_input_tokens"] + context["worker_input_tokens"] + context["receipt_tokens"]
                critical = max(s["end_ms"] for s in spans) - min(s["start_ms"] for s in spans)
                record = {
                    "schema_version": c.VERSION, "benchmark_id": manifest["benchmark_id"],
                    "manifest_sha256": manifest_hash, "run_id": run_id, "pair_id": pair_id,
                    "case_id": case["id"], "treatment": treatment, "repetition": repetition,
                    "outcome": "completed",
                    "route_trace": {"controller_session": run_id + "-controller", **expectation,
                                    "route_events": route_events(expectation)},
                    "execution": {"controller_business_actions": controller_actions,
                                  "worker_business_actions": worker_actions,
                                  "wall_time_ms": critical + (20 if is_delegate else 10),
                                  "critical_path_ms": critical,
                                  "tool_calls": sum(s["tool_calls"] for s in spans),
                                  "raw_log_bytes": sum(s["log_bytes"] for s in spans), "spans": spans},
                    "context": context, "receipts": receipts,
                    "quality": {"passed": passed, "score": score, "checks": checks},
                    "review": review, "cost": {"kind": "estimated", "usd": round(total_tokens * 0.0000005, 8), "complete": True},
                    "retries": retries, "conflicts": 0,
                    "evidence": [hash_reference(root, evidence_path)],
                    "result_tree": {"path": candidate.relative_to(root).as_posix(),
                                    "sha256": tree_digest(tree_snapshot(candidate))},
                }
                c.validate_record(record)
                records.append(record)
    records_path = root / "runs.jsonl"
    with records_path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
    return manifest_copy, records_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path(__file__).resolve().parents[1] / "benchmark.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest, records = generate(args.output.resolve(), args.manifest.resolve())
        print(json.dumps({"manifest": str(manifest), "records": str(records)}, sort_keys=True))
        return 0
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
