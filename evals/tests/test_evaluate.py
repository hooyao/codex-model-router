from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evals.scripts import campaigns, contract as c, evaluate, fixture

ROOT = Path(__file__).resolve().parents[2]


class CampaignTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "campaign"
        campaigns.generate(self.root, ("complete-draft",))
        self.manifest_path = self.root / "complete-draft.manifest.json"
        self.records_path = self.root / "complete-draft.runs.jsonl"
        self.manifest = c.read_json(self.manifest_path)
        self.records = c.load_records(self.records_path)

    def save(self, update_manifest: bool = False) -> None:
        if update_manifest:
            campaigns.write_json(self.manifest_path, self.manifest)
            for record in self.records:
                record["manifest_sha256"] = c.sha256(self.manifest_path)
        campaigns.write_records(self.records_path, self.records)

    def compare(self) -> dict:
        return evaluate.compare(self.records_path, self.manifest_path)

    def invalid(self, message: str) -> None:
        self.save()
        with self.assertRaisesRegex(c.ContractError, message):
            self.compare()

    def rewrite_ledger(self, record: dict, mutate) -> None:
        path = self.root / record["cost"]["ledger"]["path"]
        ledger = c.read_json(path)
        mutate(ledger)
        campaigns.write_json(path, ledger)
        record["cost"]["ledger"]["sha256"] = c.sha256(path)

    def test_repository_matrix_and_manifest_validate(self) -> None:
        manifest, cases = c.load_manifest(ROOT / "evals" / "experiment.json")
        self.assertEqual(2, manifest["schema_version"])
        backed = [case for case in cases if case["fixture"] is not None]
        self.assertEqual(["small-edit"], [case["id"] for case in backed])
        for case in cases:
            fixture.fixture_for_case(ROOT / "evals", case)
        self.assertEqual(1, len(list((ROOT / "evals" / "fixtures").glob("*/fixture.json"))))

    def test_draft_cannot_pass_even_with_all_gates_true(self) -> None:
        result = self.compare()
        self.assertTrue(all(result["gates"].values()))
        self.assertEqual("inconclusive", result["status"])
        self.assertFalse(result["release_pass"])

    def test_release_label_does_not_enable_unsupported_release_gate(self) -> None:
        self.manifest["status"] = "release"
        self.save(update_manifest=True)
        self.assertEqual("inconclusive", self.compare()["status"])

    def test_global_run_ids_across_pairs_and_variants(self) -> None:
        self.records[3]["run_id"] = self.records[0]["run_id"]
        self.invalid("globally duplicate run_id")

    def test_duplicate_slot_with_new_pair_and_run_id(self) -> None:
        duplicate = copy.deepcopy(self.records[0])
        duplicate.update(run_id="another", pair_id="another")
        self.records.append(duplicate)
        self.invalid("duplicate scheduled slot")

    def test_pair_id_cannot_span_repetitions(self) -> None:
        self.records[2]["pair_id"] = self.records[0]["pair_id"]
        self.invalid("pair_id reused")

    def test_mismatched_pair_ids(self) -> None:
        self.records[1]["pair_id"] = "different"
        self.invalid("mismatched pair IDs")

    def test_missing_terminal_record(self) -> None:
        self.records.pop()
        self.invalid("scheduled records mismatch: missing=1, extra=0")

    def test_extra_repetition(self) -> None:
        for record in self.records[2:]:
            record["repetition"] = 3
        self.invalid("scheduled records mismatch: missing=2, extra=2")

    def test_extra_unknown_case(self) -> None:
        for record in self.records[2:]:
            record["case_id"] = "unknown"
        self.invalid("scheduled records mismatch")

    def test_empty_records(self) -> None:
        self.records = []
        self.invalid("records must not be empty")

    def test_empty_required_objects_and_lists(self) -> None:
        original = copy.deepcopy(self.records)
        for field, value in (("environment", {}), ("route_trace", {}), ("evidence", []),
                             ("cost", {}), ("run_id", " "), ("passed", "false")):
            with self.subTest(field=field):
                self.records = copy.deepcopy(original)
                self.records[0][field] = value
                self.invalid(field)

    def test_unknown_fields_and_old_schema(self) -> None:
        self.records[0]["cost_complete"] = True
        self.invalid("extra=")
        del self.records[0]["cost_complete"]
        self.records[0]["schema_version"] = 1
        self.invalid("schema_version")

    def test_manifest_identity_and_provenance(self) -> None:
        original = copy.deepcopy(self.records)
        for field, value, message in (("manifest_sha256", "0" * 64, "manifest hash mismatch"),
                                      ("experiment_id", "other", "experiment_id mismatch"),
                                      ("environment", {**self.records[0]["environment"], "harness_version": "other"}, "environment provenance mismatch")):
            with self.subTest(field=field):
                self.records = copy.deepcopy(original)
                self.records[0][field] = value
                self.invalid(message)

    def test_cases_and_fixture_hashes(self) -> None:
        path = self.root / "cases.json"
        original = path.read_bytes()
        path.write_bytes(original + b"\n")
        with self.assertRaisesRegex(c.ContractError, "cases hash mismatch"):
            self.compare()
        path.write_bytes(original)
        fixture_path = self.root / "fixtures/small-edit/fixture.json"
        fixture_path.write_bytes(fixture_path.read_bytes() + b"\n")
        with self.assertRaisesRegex(c.ContractError, "fixture hash mismatch"):
            self.compare()

    def test_case_prompt_provenance(self) -> None:
        cases_path = self.root / "cases.json"
        cases = c.read_json(cases_path)
        cases[0]["prompt"] = "Unrelated task"
        campaigns.write_json(cases_path, cases)
        self.manifest["cases_sha256"] = c.sha256(cases_path)
        self.save(update_manifest=True)
        with self.assertRaisesRegex(c.ContractError, "case/fixture provenance mismatch"):
            self.compare()

    def test_fixture_revision_cannot_be_replaced_in_manifest_and_records(self) -> None:
        self.manifest["controls"]["repository_revision"] = "0" * 64
        for record in self.records:
            record["environment"] = copy.deepcopy(self.manifest["controls"])
        self.save(update_manifest=True)
        with self.assertRaisesRegex(c.ContractError, "initial tree/repository_revision provenance mismatch"):
            self.compare()

    def test_pair_id_cannot_span_cases(self) -> None:
        self.records[2]["case_id"] = "other-case"
        self.records[2]["pair_id"] = self.records[0]["pair_id"]
        self.invalid("pair_id reused")

    def test_empty_cases_and_ledger_sessions(self) -> None:
        self.rewrite_ledger(self.records[0], lambda ledger: ledger.update(sessions=[]))
        self.invalid("ledger.sessions must be non-empty")
        campaigns.write_json(self.root / "cases.json", [])
        with self.assertRaisesRegex(c.ContractError, "cases must be a non-empty array"):
            c.load_cases(self.root / "cases.json")

    def test_release_manifest_cannot_include_draft_cases(self) -> None:
        cases_path = self.root / "cases.json"
        cases = c.read_json(cases_path)
        cases[0]["status"] = "draft"
        campaigns.write_json(cases_path, cases)
        self.manifest["cases_sha256"] = c.sha256(cases_path)
        self.manifest["status"] = "release"
        self.save(update_manifest=True)
        with self.assertRaisesRegex(c.ContractError, "release manifest contains draft cases"):
            self.compare()

    def test_release_case_cannot_omit_fixture(self) -> None:
        cases_path = self.root / "cases.json"
        cases = c.read_json(cases_path)
        cases[0]["fixture"] = None
        campaigns.write_json(cases_path, cases)
        with self.assertRaisesRegex(c.ContractError, "release case requires a fixture"):
            c.load_cases(cases_path)

    def test_declared_synthetic_cost_failure_survives_undefined_latency(self) -> None:
        for record in self.records:
            if record["variant"] == "baseline":
                record["duration_ms"] = 0
            else:
                record["cost"]["cost_usd"] = .02
                self.rewrite_ledger(record, lambda ledger: [session.update(exclusive_cost_usd=.01) for session in ledger["sessions"]])
        self.save()
        result = self.compare()
        self.assertEqual("fail", result["status"])
        self.assertFalse(result["gates"]["cost_threshold"])
        self.assertIsNone(result["gates"]["latency_not_regressed"])

    def test_evidence_and_candidate_hashes(self) -> None:
        self.records[0]["evidence"][0]["sha256"] = "0" * 64
        self.invalid("run evidence hash mismatch")
        self.records[0]["evidence"][0]["sha256"] = c.sha256(self.root / self.records[0]["evidence"][0]["path"])
        self.records[0]["result_tree"]["sha256"] = "0" * 64
        self.invalid("candidate tree hash mismatch")

    def test_path_traversal_absolute_backslash_and_reserved_names(self) -> None:
        for path in ("../escape.json", "/etc/passwd", "C:/escape", "C:\\escape", "fixtures/../cases.json",
                     "./cases.json", "fixtures//file", "NUL.txt", "cases.json."):
            with self.subTest(path=path):
                self.records[0]["evidence"][0]["path"] = path
                self.invalid("path")

    def test_manifest_and_candidate_paths_are_confined(self) -> None:
        self.manifest["cases_file"] = "../cases.json"
        self.save(update_manifest=True)
        with self.assertRaisesRegex(c.ContractError, "unsafe relative path"):
            self.compare()
        self.manifest["cases_file"] = "cases.json"
        self.records[0]["result_tree"]["path"] = "../reference"
        self.save(update_manifest=True)
        self.invalid("unsafe relative path")

    def test_malformed_thresholds(self) -> None:
        initial = copy.deepcopy(self.manifest)
        values = {
            "quality_margin": [True, "-0.02", None, [], {}, -2, .1, float("inf")],
            "minimum_router_quality": [False, "0.95", -1, 2, float("nan")],
            "maximum_cost_ratio": [True, "0.9", -1, 2, float("-inf")],
            "maximum_latency_ratio": [False, "1.1", 0, -1, 101],
        }
        for key, invalids in values.items():
            for value in invalids:
                with self.subTest(key=key, value=value):
                    data = copy.deepcopy(initial)
                    data["analysis"][key] = value
                    self.manifest_path.write_text(json.dumps(data), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        c.load_manifest(self.manifest_path)

    def test_numeric_record_fields_reject_booleans_nan_negative_and_fractional_counts(self) -> None:
        for field, value in (("duration_ms", True), ("duration_ms", -1), ("duration_ms", float("inf")),
                             ("quality_score", float("nan")), ("delegated_tasks", .5), ("retries", False),
                             ("repetition", []), ("repetition", 0)):
            with self.subTest(field=field, value=value):
                record = copy.deepcopy(self.records[0])
                record[field] = value
                with self.assertRaises(c.ContractError):
                    c.validate_record(record)

    def test_duplicate_keys_in_records_and_nested_objects(self) -> None:
        for raw in ('{"run_id":"a","run_id":"b"}', '{"cost":{"complete":true,"complete":false}}'):
            self.records_path.write_text(raw + "\n", encoding="utf-8")
            with self.assertRaisesRegex(c.ContractError, "duplicate JSON key"):
                c.load_records(self.records_path)

    def test_duplicate_keys_in_every_json_document_loader(self) -> None:
        for path in (self.manifest_path, self.root / "cases.json", self.root / "fixtures/small-edit/fixture.json",
                     self.root / self.records[0]["cost"]["ledger"]["path"]):
            with self.subTest(path=path):
                path.write_text('{"x":1,"x":2}', encoding="utf-8")
                with self.assertRaisesRegex(c.ContractError, "duplicate JSON key"):
                    c.read_json(path)

    def test_malformed_manifest_shapes_do_not_crash_with_type_error(self) -> None:
        for key, value in (("variants", [{}]), ("variants", ["baseline", "router", "router"]),
                           ("analysis", []), ("controls", {}), ("schema_version", True),
                           ("repetitions_per_case", False), ("cases_file", [])):
            with self.subTest(key=key):
                data = copy.deepcopy(self.manifest)
                data[key] = value
                campaigns.write_json(self.manifest_path, data)
                with self.assertRaises(c.ContractError):
                    c.load_manifest(self.manifest_path)

    def test_ccusage_cannot_be_complete(self) -> None:
        self.records[0]["cost"]["source"] = "ccusage"
        self.invalid("unverified source cannot establish complete accounting")

    def test_complete_cost_requires_ledger_and_total(self) -> None:
        self.records[0]["cost"]["ledger"] = None
        self.invalid("cost.ledger must be an object")

    def test_session_ids_cannot_be_reused_across_runs(self) -> None:
        self.records[1]["route_trace"]["controller_session"] = self.records[0]["route_trace"]["controller_session"]
        self.invalid("session reused across runs")

    def test_accounting_inventory_includes_abandoned_and_retry_sessions(self) -> None:
        self.records[0]["route_trace"]["abandoned_sessions"] = ["abandoned"]
        self.records[0]["route_trace"]["retry_sessions"] = ["retry"]
        self.invalid("ledger session inventory mismatch")

    def test_ledger_hash_mismatch(self) -> None:
        self.records[0]["cost"]["ledger"]["sha256"] = "0" * 64
        self.invalid("cost ledger hash mismatch")

    def test_ledger_reference_cannot_traverse(self) -> None:
        self.records[0]["cost"]["ledger"]["path"] = "../ledger.json"
        self.invalid("unsafe relative path")

    def test_negative_incomplete_cost_is_invalid(self) -> None:
        self.records[0]["cost"] = {"source": "ccusage", "complete": False, "cost_usd": -1, "ledger": None}
        self.invalid("cost_usd must be finite")

    def test_ledger_provenance_mismatch(self) -> None:
        self.rewrite_ledger(self.records[0], lambda ledger: ledger.update(run_id="other"))
        self.invalid("ledger run_id provenance mismatch")

    def test_ledger_duplicate_session(self) -> None:
        self.rewrite_ledger(self.records[0], lambda ledger: ledger["sessions"].append(ledger["sessions"][0]))
        self.invalid("duplicate accounting session")

    def test_ledger_total_mismatch(self) -> None:
        self.records[0]["cost"]["cost_usd"] = 100
        self.invalid("ledger total mismatch")

    def test_incompatible_accounting_conventions(self) -> None:
        self.rewrite_ledger(self.records[0], lambda ledger: ledger.update(pricing_version="different"))
        self.invalid("incompatible accounting conventions")

    def test_synthetic_accounting_cannot_be_relabelled_observed(self) -> None:
        self.manifest["data_origin"] = "observed"
        self.save(update_manifest=True)
        with self.assertRaisesRegex(c.ContractError, "synthetic accounting in observed"):
            self.compare()

    def test_all_safety_categories_fail_with_missing_cost(self) -> None:
        for record in self.records:
            record["cost"] = {"source": "unavailable", "complete": False, "cost_usd": None, "ledger": None}
        for field in c.SAFETY_FIELDS:
            with self.subTest(field=field):
                self.records[1][field] = True
                self.save()
                result = self.compare()
                self.assertEqual("fail", result["status"])
                self.assertIsNone(result["gates"]["cost_threshold"])
                self.records[1][field] = False

    def test_terminal_timeouts_cancellations_errors_and_blockers_are_retained(self) -> None:
        for outcome in ("timeout", "cancelled", "error", "blocked"):
            with self.subTest(outcome=outcome):
                self.records[1].update(outcome=outcome, passed=False, quality_score=0, result_tree=None)
                self.save()
                self.assertEqual("fail", self.compare()["status"])

    def test_noncompleted_run_cannot_claim_success(self) -> None:
        self.records[1]["outcome"] = "timeout"
        self.invalid("non-completed runs must fail quality")

    def test_fixture_completed_run_requires_candidate(self) -> None:
        self.records[1]["result_tree"] = None
        self.invalid("completed fixture run requires result_tree")

    def test_oracle_overrides_reported_success(self) -> None:
        candidate = self.root / "fixtures/small-edit/initial"
        for record in self.records:
            if record["variant"] == "router":
                record["result_tree"] = {"path": candidate.relative_to(self.root).as_posix(),
                                         "sha256": fixture.tree_digest(fixture.tree_snapshot(candidate))}
        self.save()
        result = self.compare()
        self.assertEqual("fail", result["status"])
        self.assertEqual(0, result["quality_pass_rate"]["router"])

    def test_baseline_safety_cannot_offset_router_incident(self) -> None:
        self.records[0]["scope_leak"] = True
        self.records[1]["scope_leak"] = True
        self.save()
        result = self.compare()
        self.assertEqual("fail", result["status"])
        self.assertEqual({"baseline": 1, "router": 1}, result["safety_by_category"]["scope_leak"])

    def test_cli_validation_and_comparison_exit_codes(self) -> None:
        command = [sys.executable, str(ROOT / "evals/scripts/evaluate.py")]
        args = ["--records", str(self.records_path), "--manifest", str(self.manifest_path)]
        valid = subprocess.run(command + ["validate-records"] + args, capture_output=True, text=True)
        self.assertEqual(0, valid.returncode, valid.stderr)
        compared = subprocess.run(command + ["compare"] + args, capture_output=True, text=True)
        self.assertEqual(2, compared.returncode, compared.stderr)
        self.assertEqual("inconclusive", json.loads(compared.stdout)["status"])
        self.records_path.write_text("{}\n", encoding="utf-8")
        invalid = subprocess.run(command + ["compare"] + args, capture_output=True, text=True)
        self.assertEqual(1, invalid.returncode)
        self.assertIn("ERROR:", invalid.stderr)
        self.assertNotIn("Traceback", invalid.stderr)


class FixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.definition = campaigns.FIXTURE_ROOT / "fixture.json"
        self.candidate = self.root / "candidate"
        shutil.copytree(campaigns.FIXTURE_ROOT / "reference", self.candidate)

    def test_reference_passes_and_unchanged_fails(self) -> None:
        self.assertTrue(fixture.grade(self.definition, self.candidate)["passed"])
        self.assertFalse(fixture.grade(self.definition, campaigns.FIXTURE_ROOT / "initial")["passed"])

    def test_unrelated_edit_fails(self) -> None:
        (self.candidate / "settings.json").write_text('{"enabled": false}\n', encoding="utf-8")
        self.assertEqual(["settings.json"], fixture.grade(self.definition, self.candidate)["hash_mismatches"])

    def test_extra_file_fails(self) -> None:
        (self.candidate / "extra.txt").write_text("extra", encoding="utf-8")
        self.assertEqual(["extra.txt"], fixture.grade(self.definition, self.candidate)["extra_files"])

    def test_missing_file_fails(self) -> None:
        (self.candidate / "settings.json").unlink()
        self.assertEqual(["settings.json"], fixture.grade(self.definition, self.candidate)["missing_files"])

    def test_extra_empty_directory_fails(self) -> None:
        (self.candidate / "extra").mkdir()
        result = fixture.grade(self.definition, self.candidate)
        self.assertTrue(result["directory_mismatch"])
        self.assertFalse(result["passed"])

    def test_hash_mismatch_rejects_even_reference(self) -> None:
        with self.assertRaisesRegex(c.ContractError, "candidate tree hash mismatch"):
            fixture.grade(self.definition, self.candidate, "0" * 64)

    def test_fixture_reference_tampering_is_invalid(self) -> None:
        copied = self.root / "definition"
        shutil.copytree(campaigns.FIXTURE_ROOT, copied)
        (copied / "reference/README.md").write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(c.ContractError, "fixture reference hash/tree mismatch"):
            fixture.grade(copied / "fixture.json", self.candidate)

    def test_symlink_candidate_and_symlink_path_rejected(self) -> None:
        target = self.root / "target.txt"
        target.write_text("unrelated", encoding="utf-8")
        try:
            os.symlink(target, self.candidate / "link.txt")
        except OSError as error:
            self.skipTest(f"Host cannot create symlinks: {error}")
        with self.assertRaisesRegex(c.ContractError, "symlink/reparse"):
            fixture.grade(self.definition, self.candidate)
        with self.assertRaisesRegex(c.ContractError, "symlink/reparse"):
            c.safe_path(self.root, "candidate/link.txt")

    def test_root_directory_symlink_rejected(self) -> None:
        link = self.root / "link"
        try:
            os.symlink(self.candidate, link, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"Host cannot create symlinks: {error}")
        with self.assertRaisesRegex(c.ContractError, "symlink/reparse"):
            fixture.grade(self.definition, link)


class AnalysisAndImporterTests(unittest.TestCase):
    def test_deterministic_campaigns_match_expected_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "one"
            campaigns.generate(root)
            for name, expected in c.read_json(root / "expected.json").items():
                with self.subTest(name=name):
                    result = evaluate.compare(root / f"{name}.runs.jsonl", root / f"{name}.manifest.json")
                    self.assertEqual(expected["status"], result["status"])
                    self.assertEqual(expected["release_pass"], result["release_pass"])
                    self.assertEqual(2, result["pairs"])
                    self.assertNotIn("NaN", json.dumps(result, allow_nan=False))
            other = Path(temp) / "two"
            campaigns.generate(other)
            self.assertEqual(fixture.tree_snapshot(root), fixture.tree_snapshot(other))
            with self.assertRaises(FileExistsError):
                campaigns.generate(root)

    def test_ratio_of_means_not_mean_of_ratios(self) -> None:
        estimate, reason = evaluate.ratio_of_means([(1, 1), (100, 10)])
        self.assertAlmostEqual(11 / 101, estimate)
        self.assertIsNone(reason)

    def test_zero_and_overflow_ratios_have_explicit_nulls(self) -> None:
        for pairs in ([(0, 0)], [(0, 1)], [(1e-308, 1e308)]):
            with self.subTest(pairs=pairs):
                result = evaluate.bootstrap_interval(pairs, evaluate.ratio_of_means)
                self.assertIsNone(result["estimate"])
                self.assertIsNone(result["bootstrap_95"])
                self.assertTrue(result["reason"])
                json.dumps(result, allow_nan=False)

    def test_undefined_resamples_are_not_silently_discarded(self) -> None:
        result = evaluate.bootstrap_interval([(0, 1), (1, 1)], evaluate.ratio_of_means)
        self.assertEqual(2, result["estimate"])
        self.assertIsNone(result["bootstrap_95"])
        self.assertIn("undefined bootstrap resample", result["reason"])

    def test_ccusage_stays_unverified_even_for_exact_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            completed = subprocess.CompletedProcess([], 0, '{"sessions":[{"sessionId":"a","totalCost":0.1}]}', "")
            with patch.object(evaluate.subprocess, "run", return_value=completed):
                result = evaluate.collect_ccusage(["a"], Path(temp) / "cost.json")
            self.assertFalse(result["verified"])
            self.assertFalse(result["cost_complete"])
            self.assertIsNone(result["cost_usd"])
            self.assertEqual(.1, result["observations"][0]["observed_cost_usd"])

    def test_ccusage_rejects_wrong_duplicate_and_malformed_observations(self) -> None:
        for raw in ('{"sessions":[]}', '{"sessions":[{"sessionId":"b","totalCost":1}]}',
                    '{"sessions":[{"sessionId":"a","totalCost":true}]}',
                    '{"sessions":[{"sessionId":"a","totalCost":-1}]}',
                    '{"sessions":[{"sessionId":"a","totalCost":1,"totalCost":2}]}',
                    '{"sessions":[{"sessionId":"a","totalCost":1},{"sessionId":"a","totalCost":1}]}'):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as temp:
                completed = subprocess.CompletedProcess([], 0, raw, "")
                with patch.object(evaluate.subprocess, "run", return_value=completed):
                    with self.assertRaises(ValueError):
                        evaluate.collect_ccusage(["a"], Path(temp) / "cost.json")
                    self.assertFalse((Path(temp) / "cost.json").exists())

    def test_provider_verified_override_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run([sys.executable, str(ROOT / "evals/scripts/evaluate.py"), "ccusage",
                                     "--thread-id", "a", "--output", str(Path(temp) / "cost.json"),
                                     "--provider-verified"], capture_output=True, text=True)
        self.assertEqual(2, result.returncode)
        self.assertIn("unrecognized arguments: --provider-verified", result.stderr)


if __name__ == "__main__":
    unittest.main()
