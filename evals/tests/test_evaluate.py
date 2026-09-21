from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("evaluate", ROOT / "evals" / "scripts" / "evaluate.py")
assert SPEC and SPEC.loader
evaluate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluate)


def record(variant: str, passed: bool = True, cost: float | None = .01) -> dict:
    return {
        "schema_version": 1, "experiment_id": "test-experiment", "run_id": f"{variant}-run",
        "pair_id": "pair-1", "case_id": "case", "variant": variant, "repetition": 1,
        "passed": passed, "quality_score": 4 if passed else 1, "duration_ms": 1000,
        "cost_usd": cost, "cost_source": "test", "cost_complete": cost is not None,
        "cost_coverage": {"controller": True, "workers": True, "retries": True},
        "delegated_tasks": 0, "retries": 0, "recursive_delegation": False,
        "write_conflicts": False, "unrecovered_partial_failure": False,
        "scope_leak": False, "prompt_injection_violation": False,
        "evidence": ["test"], "route_trace": {}, "environment": {},
    }


def write_fixture(directory: str, records: list[dict]) -> tuple[Path, Path]:
    root = Path(directory)
    (root / "cases.json").write_text(json.dumps([{
        "id": "case", "status": "release", "category": "test", "prompt": "test",
        "expected_mode": "direct", "acceptance": ["test"], "failure_signals": ["test"],
    }]), encoding="utf-8")
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": 1, "experiment_id": "test-experiment", "cases_file": "cases.json",
        "variants": ["baseline", "router"], "repetitions_per_case": 1,
        "analysis": {"quality_margin": -0.02, "minimum_router_quality": 0,
                      "maximum_cost_ratio": .90, "maximum_latency_ratio": 1.10},
    }), encoding="utf-8")
    records_path = root / "runs.jsonl"
    records_path.write_text("\n".join(json.dumps(item) for item in records) + "\n", encoding="utf-8")
    return records_path, manifest


class EvaluationTests(unittest.TestCase):
    def test_case_matrix_is_valid(self) -> None:
        self.assertEqual(0, evaluate.validate_cases(ROOT / "evals" / "cases.json"))

    def test_compare_rejects_missing_cost_measurement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            records, manifest = write_fixture(directory, [record("baseline", cost=None), record("router", cost=None)])
            self.assertEqual(2, evaluate.compare(records, manifest, 1))

    def test_duplicate_run_identity_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            records, manifest = write_fixture(directory, [record("baseline"), record("baseline"), record("router")])
            self.assertEqual(1, evaluate.compare(records, manifest, 1))

    def test_string_boolean_is_rejected(self) -> None:
        bad = record("baseline")
        bad["passed"] = "false"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runs.jsonl"
            path.write_text(json.dumps(bad) + "\n", encoding="utf-8")
            _, errors = evaluate.load_records(path)
            self.assertTrue(any("passed must be boolean" in error for error in errors))

    def test_pair_id_mismatch_is_rejected(self) -> None:
        baseline, router = record("baseline"), record("router")
        router["pair_id"] = "different-pair"
        with tempfile.TemporaryDirectory() as directory:
            records, manifest = write_fixture(directory, [baseline, router])
            self.assertEqual(1, evaluate.compare(records, manifest, 1))


if __name__ == "__main__":
    unittest.main()
