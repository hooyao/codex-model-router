from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from evals.scripts import contract as c, evaluate, fixture, live_evidence, runner


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "evals" / "benchmark.json"


class ScenarioBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base_temp = tempfile.TemporaryDirectory()
        cls.base_root = Path(cls.base_temp.name) / "campaign"
        runner.generate(cls.base_root, MANIFEST)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.base_temp.cleanup()

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "campaign"
        shutil.copytree(self.base_root, self.root)
        self.manifest_path = self.root / "benchmark.json"
        self.records_path = self.root / "runs.jsonl"
        self.records = c.load_records(self.records_path)

    def write_records(self) -> None:
        with self.records_path.open("w", encoding="utf-8", newline="\n") as stream:
            for record in self.records:
                stream.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")

    def record(self, case_id: str, treatment: str, repetition: int = 1) -> dict:
        return next(record for record in self.records if record["case_id"] == case_id
                    and record["treatment"] == treatment and record["repetition"] == repetition)

    def blocked_prefix(self, source: dict, keep_ids: set[str]) -> dict:
        record = copy.deepcopy(source)
        spans = [span for span in record["execution"]["spans"] if span["id"] in keep_ids]
        receipts = [receipt for receipt in record["receipts"] if receipt["producer_span_id"] in keep_ids]
        for receipt in receipts:
            receipt["consumer_span_ids"] = [consumer for consumer in receipt["consumer_span_ids"]
                                            if consumer in keep_ids]
        record.update(outcome="blocked", result_tree=None, review=None)
        record["execution"].update(
            spans=spans,
            controller_business_actions=sum(span["role"] == "controller" for span in spans),
            worker_business_actions=sum(span["role"] == "worker" for span in spans),
            critical_path_ms=max(span["end_ms"] for span in spans) - min(span["start_ms"] for span in spans),
            wall_time_ms=max(span["end_ms"] for span in spans) - min(span["start_ms"] for span in spans) + 20,
            tool_calls=sum(span["tool_calls"] for span in spans),
            raw_log_bytes=sum(span["log_bytes"] for span in spans),
        )
        record["context"] = {
            "controller_input_tokens": sum(span["input_tokens"] for span in spans if span["role"] == "controller"),
            "worker_input_tokens": sum(span["input_tokens"] for span in spans if span["role"] != "controller"),
            "receipt_tokens": sum(receipt["token_count"] for receipt in receipts),
        }
        record["receipts"] = receipts
        record["quality"] = {"passed": False, "score": 0,
                             "checks": [{"name": "terminal", "passed": False, "evidence": "blocked"}]}
        return record

    def test_frozen_matrix_targets_router_value_not_function_generation(self) -> None:
        manifest, cases = c.load_manifest(MANIFEST)
        self.assertEqual("router-scenarios-v1", manifest["benchmark_id"])
        self.assertEqual(2, manifest["repetitions"])
        self.assertEqual(list(c.TREATMENTS), manifest["treatments"])
        self.assertEqual(
            ["direct-small-control", "investigation-reuse", "serial-escalation",
             "parallel-disjoint", "architecture-review"],
            [case["id"] for case in cases],
        )
        self.assertTrue(all("HumanEval" not in case["prompt"] and "MBPP" not in case["prompt"] for case in cases))
        for case in cases:
            fixture.load_fixture(fixture.fixture_for_case(MANIFEST.parent, case))

    def test_runner_is_deterministic_and_campaign_is_complete(self) -> None:
        other = Path(self.temp.name) / "other"
        runner.generate(other, MANIFEST)
        self.assertEqual(fixture.tree_snapshot(self.root), fixture.tree_snapshot(other))
        manifest, records, grades = evaluate.validate_campaign(self.records_path, self.manifest_path)
        self.assertEqual(30, len(records))
        self.assertEqual(30, len(grades))
        self.assertTrue(all(result["passed"] for result in grades.values()))
        self.assertEqual("synthetic", manifest["data_origin"])

    def test_investigation_uses_compact_receipt_downstream(self) -> None:
        selective = self.record("investigation-reuse", "selective")
        direct = self.record("investigation-reuse", "direct")
        receipt = next(receipt for receipt in selective["receipts"]
                       if receipt["producer_span_id"] == "investigate")
        self.assertEqual(["implement"], receipt["consumer_span_ids"])
        self.assertEqual({"cache_ttl_seconds=45", "source=docs/ops.md"},
                         set(receipt["content"]["facts"]))
        self.assertEqual(receipt["sha256"], c.receipt_sha256(receipt["content"]))
        selective_context = selective["context"]["controller_input_tokens"] + selective["context"]["worker_input_tokens"]
        self.assertLess(selective_context, direct["context"]["controller_input_tokens"])
        self.assertLess(selective["execution"]["raw_log_bytes"], direct["execution"]["raw_log_bytes"])
        report = evaluate.compare(self.records_path, self.manifest_path)
        self.assertEqual(2, report["cases"]["investigation-reuse"]["selective_receipt_links"])
        self.assertGreater(report["cases"]["investigation-reuse"]["selective_linked_consumer_input_tokens"], 0)

    def test_serial_case_records_direct_to_delegate_escalation_and_dependencies(self) -> None:
        record = self.record("serial-escalation", "selective")
        self.assertEqual("DIRECT", record["route_trace"]["initial_ownership"])
        self.assertEqual("DELEGATE", record["route_trace"]["final_ownership"])
        self.assertEqual("scope-expanded", record["route_trace"]["escalation_trigger"])
        self.assertEqual(2, len(record["route_trace"]["route_events"]))
        self.assertEqual(30, record["route_trace"]["first_delegation_ms"])
        controller = next(span for span in record["execution"]["spans"] if span["role"] == "controller")
        self.assertLessEqual(controller["end_ms"], record["route_trace"]["first_delegation_ms"])
        worker_spans = [span for span in record["execution"]["spans"] if span["role"] == "worker"]
        self.assertTrue(all(left["end_ms"] <= right["start_ms"]
                            for left, right in zip(worker_spans, worker_spans[1:])))

    def test_parallel_case_has_disjoint_overlapping_work_and_critical_path_benefit(self) -> None:
        selective = self.record("parallel-disjoint", "selective")
        mandatory = self.record("parallel-disjoint", "mandatory_delegate")
        spans = selective["execution"]["spans"]
        artifacts = [path for span in spans for path in span["artifact_paths"]]
        self.assertEqual(len(artifacts), len(set(artifacts)))
        self.assertEqual("PARALLEL", selective["route_trace"]["delegate_topology"])
        self.assertTrue(all(span["start_ms"] == 0 for span in spans))
        self.assertLess(selective["execution"]["critical_path_ms"],
                        mandatory["execution"]["critical_path_ms"])

    def test_parallel_overlap_requires_distinct_worker_sessions(self) -> None:
        record = copy.deepcopy(self.record("parallel-disjoint", "selective"))
        record["execution"]["spans"][1]["session_id"] = record["execution"]["spans"][0]["session_id"]
        with self.assertRaisesRegex(c.ContractError, "requires distinct worker sessions"):
            c.validate_record(record)

    def test_late_parallel_transition_cannot_retroactively_authorize_overlap(self) -> None:
        record = copy.deepcopy(self.record("parallel-disjoint", "selective"))
        record["route_trace"]["route_events"] = [
            {"sequence": 1, "at_ms": 0, "ownership": "DELEGATE",
             "topology": "ISOLATED_SERIAL", "trigger": None},
            {"sequence": 2, "at_ms": 50, "ownership": "DELEGATE",
             "topology": "PARALLEL", "trigger": None},
        ]
        with self.assertRaisesRegex(c.ContractError, "topology transitions are unsupported"):
            c.validate_record(record)

    def test_architecture_requires_distinct_review_without_polluting_artifact_quality(self) -> None:
        direct = self.record("architecture-review", "direct")
        selective = self.record("architecture-review", "selective")
        self.assertTrue(direct["quality"]["passed"])
        self.assertTrue(selective["quality"]["passed"])
        self.assertEqual("INDEPENDENT_REVIEW", selective["route_trace"]["verification_requirement"])
        self.assertNotEqual(selective["review"]["session_id"], selective["route_trace"]["controller_session"])
        self.assertNotIn(selective["review"]["session_id"], selective["review"]["author_session_ids"])
        decision = json.loads((self.root / selective["result_tree"]["path"] / "decision.json").read_text(encoding="utf-8"))
        self.assertEqual("separate-ownership-and-topology", decision["decision"])
        self.assertEqual(1, decision["schema_version"])

    def test_forced_direct_artifact_quality_is_treatment_fair(self) -> None:
        report = evaluate.compare(self.records_path, self.manifest_path)
        for case_id in ("investigation-reuse", "serial-escalation", "parallel-disjoint"):
            with self.subTest(case=case_id):
                direct = report["cases"][case_id]["treatments"]["direct"]
                self.assertEqual(1.0, direct["quality_pass_rate"])
                self.assertEqual(100, direct["mean_quality_score"])
                self.assertEqual(1.0, direct["process_compliance_rate"])

    def test_direct_small_case_is_only_calibration_control(self) -> None:
        report = evaluate.compare(self.records_path, self.manifest_path)
        control = report["cases"]["direct-small-control"]
        self.assertEqual(1.0, control["treatments"]["selective"]["quality_pass_rate"])
        self.assertLess(control["treatments"]["selective"]["mean_wall_time_ms"],
                        control["treatments"]["mandatory_delegate"]["mean_wall_time_ms"])
        self.assertEqual(5, len(report["cases"]))

    def test_report_separates_estimated_and_measured_costs_and_disclaims_claims(self) -> None:
        report = evaluate.compare(self.records_path, self.manifest_path)
        self.assertFalse(report["release_claim_supported"])
        self.assertEqual("synthetic", report["data_origin"])
        for treatment in c.TREATMENTS:
            summary = report["treatments"][treatment]
            self.assertEqual(0, summary["measured_cost"]["complete_runs"])
            self.assertIsNone(summary["measured_cost"]["mean_usd"])
            self.assertEqual(10, summary["estimated_cost"]["complete_runs"])
            self.assertIsNotNone(summary["estimated_cost"]["mean_usd"])
        self.assertTrue(any("estimated" in limitation.lower() for limitation in report["limitations"]))

        self.records[0]["cost"]["kind"] = "measured"
        self.write_records()
        mixed = evaluate.compare(self.records_path, self.manifest_path)
        treatment = self.records[0]["treatment"]
        self.assertEqual(1, mixed["treatments"][treatment]["measured_cost"]["complete_runs"])
        self.assertEqual(9, mixed["treatments"][treatment]["estimated_cost"]["complete_runs"])

    def test_failures_remain_in_denominator(self) -> None:
        record = self.record("serial-escalation", "selective")
        record.update(outcome="blocked", result_tree=None)
        record["quality"] = {"passed": False, "score": 0,
                             "checks": [{"name": "terminal", "passed": False, "evidence": "blocked"}]}
        self.write_records()
        report = evaluate.compare(self.records_path, self.manifest_path)
        self.assertEqual(1, report["failures_by_outcome"]["blocked"])
        self.assertLess(report["treatments"]["selective"]["completion_rate"], 1)

    def test_early_blocked_prefix_is_valid_but_inconsistent_partial_graphs_fail(self) -> None:
        original = self.record("serial-escalation", "selective")
        blocked = self.blocked_prefix(original, {"bounded-discovery", "plan"})
        c.validate_record(blocked)
        self.records[self.records.index(original)] = blocked
        self.write_records()
        report = evaluate.compare(self.records_path, self.manifest_path)
        self.assertEqual(1, report["failures_by_outcome"]["blocked"])
        self.assertEqual(0, blocked["quality"]["score"])

        impossible = self.blocked_prefix(original, {"bounded-discovery", "schema"})
        schema = next(span for span in impossible["execution"]["spans"] if span["id"] == "schema")
        schema["depends_on"] = []
        c.validate_record(impossible)
        self.records[self.records.index(blocked)] = impossible
        self.write_records()
        with self.assertRaisesRegex(c.ContractError, "omits prerequisite dependency"):
            evaluate.compare(self.records_path, self.manifest_path)

        missing_receipt = self.blocked_prefix(original, {"bounded-discovery", "plan", "schema"})
        plan_receipt = next(receipt for receipt in missing_receipt["receipts"]
                            if receipt["producer_span_id"] == "plan")
        plan_receipt["consumer_span_ids"] = []
        c.validate_record(missing_receipt)
        self.records[self.records.index(impossible)] = missing_receipt
        self.write_records()
        with self.assertRaisesRegex(c.ContractError, "omits required receipt consumption"):
            evaluate.compare(self.records_path, self.manifest_path)

    def test_route_adherence_is_reported_without_hiding_quality(self) -> None:
        record = self.record("serial-escalation", "selective")
        record["route_trace"]["escalation_trigger"] = "validation-failed"
        record["route_trace"]["route_events"][-1]["trigger"] = "validation-failed"
        c.validate_record(record)
        self.write_records()
        report = evaluate.compare(self.records_path, self.manifest_path)
        self.assertIn(record["run_id"], report["route_failures"])
        self.assertEqual(30, report["scheduled_runs"])

    def test_contract_rejects_overlapping_writes_and_fake_review(self) -> None:
        parallel = copy.deepcopy(self.record("parallel-disjoint", "selective"))
        parallel["execution"]["spans"][1]["artifact_paths"] = parallel["execution"]["spans"][0]["artifact_paths"]
        with self.assertRaisesRegex(c.ContractError, "overlapping write ownership"):
            c.validate_record(parallel)

        reviewed = copy.deepcopy(self.record("architecture-review", "selective"))
        reviewed["review"]["session_id"] = reviewed["route_trace"]["controller_session"]
        with self.assertRaisesRegex(c.ContractError, "controller cannot be independent reviewer"):
            c.validate_record(reviewed)

        forged_authors = copy.deepcopy(self.record("architecture-review", "selective"))
        forged_authors["review"]["author_session_ids"] = ["claimed-author"]
        with self.assertRaisesRegex(c.ContractError, "must match artifact-writing spans"):
            c.validate_record(forged_authors)

    def test_dependency_timing_and_span_identity_are_derived(self) -> None:
        serial = copy.deepcopy(self.record("serial-escalation", "selective"))
        api = next(span for span in serial["execution"]["spans"] if span["id"] == "api")
        api["start_ms"], api["end_ms"] = 120, 170
        with self.assertRaisesRegex(c.ContractError, "dependency timing violation"):
            c.validate_record(serial)

        missing_time = copy.deepcopy(self.record("serial-escalation", "selective"))
        del missing_time["execution"]["spans"][1]["start_ms"]
        with self.assertRaisesRegex(c.ContractError, "missing=.*start_ms"):
            c.validate_record(missing_time)
        invalid_time = copy.deepcopy(self.record("serial-escalation", "selective"))
        invalid_time["execution"]["spans"][1]["start_ms"] = "early"
        with self.assertRaisesRegex(c.ContractError, "start_ms must be finite"):
            c.validate_record(invalid_time)

        delegated = copy.deepcopy(self.record("investigation-reuse", "selective"))
        delegated["execution"]["spans"][0]["session_id"] = delegated["route_trace"]["controller_session"]
        with self.assertRaisesRegex(c.ContractError, "cannot use controller session"):
            c.validate_record(delegated)

        direct = copy.deepcopy(self.record("direct-small-control", "direct"))
        injected = copy.deepcopy(self.record("direct-small-control", "mandatory_delegate")["execution"]["spans"][0])
        injected["id"] = "injected-worker"
        injected["artifact_paths"] = []
        direct["execution"]["spans"].append(injected)
        direct["execution"]["worker_business_actions"] = 1
        direct["execution"]["critical_path_ms"] = max(
            span["end_ms"] for span in direct["execution"]["spans"]
        )
        direct["execution"]["wall_time_ms"] = direct["execution"]["critical_path_ms"] + 10
        direct["execution"]["tool_calls"] += injected["tool_calls"]
        direct["execution"]["raw_log_bytes"] += injected["log_bytes"]
        direct["context"]["worker_input_tokens"] += injected["input_tokens"]
        direct["context"]["receipt_tokens"] += injected["receipt_tokens"]
        injected_content = {"facts": ["forged"], "artifact_refs": []}
        direct["receipts"].append({"id": "injected-receipt", "producer_span_id": "injected-worker",
                                   "consumer_span_ids": [], "token_count": injected["receipt_tokens"],
                                   "content": injected_content, "sha256": c.receipt_sha256(injected_content)})
        with self.assertRaisesRegex(c.ContractError, "not covered by recorded ownership"):
            c.validate_record(direct)

        moved = copy.deepcopy(self.record("serial-escalation", "selective"))
        discovery = next(span for span in moved["execution"]["spans"] if span["role"] == "controller")
        discovery["start_ms"], discovery["end_ms"] = 40, 70
        with self.assertRaisesRegex(c.ContractError, "controller business span occurs after delegation"):
            c.validate_record(moved)

    def test_route_decisions_precede_and_cover_business_spans(self) -> None:
        worker_early = copy.deepcopy(self.record("serial-escalation", "selective"))
        plan = next(span for span in worker_early["execution"]["spans"] if span["id"] == "plan")
        plan["start_ms"] = 20
        with self.assertRaisesRegex(c.ContractError, "not covered by recorded ownership"):
            c.validate_record(worker_early)

        late_direct = copy.deepcopy(self.record("direct-small-control", "direct"))
        late_direct["route_trace"]["route_events"][0]["at_ms"] = 100
        with self.assertRaisesRegex(c.ContractError, "starts before ownership decision"):
            c.validate_record(late_direct)

    def test_scaled_elapsed_time_preserves_semantic_route_adherence(self) -> None:
        record = self.record("serial-escalation", "selective")
        factor = 10
        for span in record["execution"]["spans"]:
            span["start_ms"] *= factor
            span["end_ms"] *= factor
        for event in record["route_trace"]["route_events"]:
            event["at_ms"] *= factor
        record["route_trace"]["first_delegation_ms"] *= factor
        record["execution"]["critical_path_ms"] *= factor
        record["execution"]["wall_time_ms"] *= factor
        c.validate_record(record)
        self.write_records()
        report = evaluate.compare(self.records_path, self.manifest_path)
        self.assertNotIn(record["run_id"], report["route_failures"])
        self.assertEqual(1.0, report["cases"]["serial-escalation"]["treatments"]["selective"]["route_adherence_rate"])

    def test_quality_is_recomputed_from_frozen_requirements(self) -> None:
        architecture = self.record("architecture-review", "direct")
        architecture["quality"] = {"passed": True, "score": 100,
                                   "checks": [{"name": "route-adherent", "passed": True, "evidence": "claim"}]}
        investigation = self.record("investigation-reuse", "selective")
        candidate = self.root / investigation["result_tree"]["path"] / "app" / "settings.json"
        candidate.write_text('{"cache_ttl_seconds":999,"region":"primary"}\n', encoding="utf-8")
        investigation["result_tree"]["sha256"] = fixture.tree_digest(
            fixture.tree_snapshot(self.root / investigation["result_tree"]["path"])
        )
        investigation["quality"] = {"passed": True, "score": 100,
                                    "checks": [{"name": "route-adherent", "passed": True, "evidence": "claim"}]}
        self.write_records()
        report = evaluate.compare(self.records_path, self.manifest_path)
        self.assertIn(architecture["run_id"], report["claimed_quality_disagreements"])
        self.assertIn(investigation["run_id"], report["claimed_quality_disagreements"])
        self.assertIn(investigation["run_id"], report["artifact_failures"])
        self.assertEqual(1, report["treatments"]["direct"]["quality_pass_rate"])
        self.assertLess(report["treatments"]["selective"]["quality_pass_rate"], 1)

        missing_review = copy.deepcopy(self.record("architecture-review", "selective"))
        missing_review["review"] = None
        with self.assertRaisesRegex(c.ContractError, "requires passing distinct review"):
            c.validate_record(missing_review)

    def test_receipt_hash_fact_and_consumer_links_cannot_be_forged(self) -> None:
        record = copy.deepcopy(self.record("investigation-reuse", "selective"))
        receipt = next(item for item in record["receipts"] if item["producer_span_id"] == "investigate")
        receipt["content"]["facts"] = ["cache_ttl_seconds=999"]
        with self.assertRaisesRegex(c.ContractError, "receipt content hash mismatch"):
            c.validate_record(record)

        receipt["sha256"] = c.receipt_sha256(receipt["content"])
        receipt["consumer_span_ids"] = ["investigate"]
        with self.assertRaisesRegex(c.ContractError, "consumer must depend"):
            c.validate_record(record)

        record = self.record("investigation-reuse", "selective")
        receipt = next(item for item in record["receipts"] if item["producer_span_id"] == "investigate")
        receipt["content"]["facts"] = ["cache_ttl_seconds=999", "source=forged"]
        receipt["sha256"] = c.receipt_sha256(receipt["content"])
        receipt["consumer_span_ids"] = []
        self.write_records()
        with self.assertRaisesRegex(c.ContractError, "receipt edges do not match frozen case"):
            evaluate.compare(self.records_path, self.manifest_path)

    def test_frozen_dependency_and_receipt_edges_are_required(self) -> None:
        record = self.record("serial-escalation", "selective")
        for span in record["execution"]["spans"]:
            span["depends_on"] = []
        for receipt in record["receipts"]:
            receipt["consumer_span_ids"] = []
        self.write_records()
        with self.assertRaisesRegex(c.ContractError, "dependency edges do not match frozen case"):
            evaluate.compare(self.records_path, self.manifest_path)

        record = copy.deepcopy(self.record("serial-escalation", "mandatory_delegate"))
        contract_test = next(span for span in record["execution"]["spans"] if span["id"] == "contract-test")
        contract_test["start_ms"], contract_test["end_ms"] = 180, 245
        with self.assertRaisesRegex(c.ContractError, "dependency timing violation"):
            c.validate_record(record)

    def test_runner_and_evidence_provenance_are_synthetic(self) -> None:
        observed = c.read_json(MANIFEST)
        observed["data_origin"] = "observed"
        observed_path = Path(self.temp.name) / "observed.json"
        runner.write_json(observed_path, observed)
        with self.assertRaisesRegex(c.ContractError, "requires synthetic data_origin"):
            runner.generate(Path(self.temp.name) / "observed-run", observed_path)

        record = self.records[0]
        evidence_path = self.root / record["evidence"][0]["path"]
        evidence = c.read_json(evidence_path)
        evidence["data_origin"] = "observed"
        runner.write_json(evidence_path, evidence)
        record["evidence"][0]["sha256"] = c.sha256(evidence_path)
        self.write_records()
        with self.assertRaisesRegex(c.ContractError, "evidence data_origin mismatch"):
            evaluate.compare(self.records_path, self.manifest_path)

    def test_duplicate_json_unsafe_paths_and_tampering_fail(self) -> None:
        with self.assertRaisesRegex(c.ContractError, "duplicate JSON key"):
            c.parse_json('{"a":1,"a":2}')
        with self.assertRaisesRegex(c.ContractError, "unsafe relative path"):
            c.relative_name("../escape")
        cases = c.read_json(self.root / "cases.json")
        cases[0]["prompt"] = "tampered"
        runner.write_json(self.root / "cases.json", cases)
        with self.assertRaisesRegex(c.ContractError, "cases hash mismatch"):
            c.load_manifest(self.manifest_path)

    def test_cli_validation_and_report(self) -> None:
        validate = subprocess.run(
            [sys.executable, str(ROOT / "evals/scripts/evaluate.py"), "validate-records",
             "--records", str(self.records_path), "--manifest", str(self.manifest_path)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, validate.returncode, validate.stdout + validate.stderr)
        compare = subprocess.run(
            [sys.executable, str(ROOT / "evals/scripts/evaluate.py"), "compare",
             "--records", str(self.records_path), "--manifest", str(self.manifest_path)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, compare.returncode, compare.stdout + compare.stderr)
        self.assertFalse(json.loads(compare.stdout)["release_claim_supported"])

    def test_live_evidence_uses_observed_intervals_and_ignores_router_state(self) -> None:
        workspace = Path(self.temp.name) / "live-workspace"
        (workspace / ".codex-model-router").mkdir(parents=True)
        (workspace / ".codex-model-router" / "routing.json").write_text("{}", encoding="utf-8")
        (workspace / "reports").mkdir()
        (workspace / "reports" / "alpha.md").write_text("verified-alpha\n", encoding="utf-8")
        self.assertEqual({"reports/alpha.md": c.sha256(workspace / "reports" / "alpha.md")},
                         live_evidence.snapshot(workspace))

        sessions = [
            {"thread_id": "alpha", "start_at": "2026-09-23T03:00:00Z", "end_at": "2026-09-23T03:00:10Z"},
            {"thread_id": "beta", "start_at": "2026-09-23T03:00:02Z", "end_at": "2026-09-23T03:00:08Z"},
            {"thread_id": "gamma", "start_at": "2026-09-23T03:00:04Z", "end_at": "2026-09-23T03:00:12Z"},
        ]
        self.assertEqual(4000, live_evidence.overlap_ms(sessions))
        self.assertFalse(live_evidence.ordered_without_overlap(sessions))

    def test_live_identity_contract_rejects_placeholder_model_names(self) -> None:
        valid = {"thread_id": "worker-1", "model": "gpt-5.6-sol", "reasoning_effort": "high", "identity": {
            "worker_name": "edit-gpt-5-6-sol-high", "task_id": "edit-gpt-5-6-sol-high",
            "native_task_name": "edit_gpt_5_6_sol_high",
        }}
        placeholder = {"thread_id": "worker-2", "model": "gpt-5.6-sol", "reasoning_effort": "high", "identity": {
            "worker_name": "edit-model-unexposed-effort-unexposed",
            "task_id": "edit-model-unexposed-effort-unexposed",
            "native_task_name": "edit_model_unexposed_effort_unexposed",
        }}
        self.assertTrue(live_evidence.identity_checks([valid])[0]["passed"])
        self.assertFalse(live_evidence.identity_checks([placeholder])[0]["passed"])


if __name__ == "__main__":
    unittest.main()
