"""Offline checks for benchmark-local bounded native dispatch."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from evals.long_horizon_v1.fork_policy import (
    DIAGNOSTIC, STRICT, observed_spawns, planned_commitment,
    preflight_packet_scope, validate_observed_spawns)
from evals.long_horizon_v1.run import ActiveTelemetryGuard, live_run
from evals.long_horizon_v1.collect import collect
from evals.long_horizon_v1.transport import TransportError


FIXTURE = Path(__file__).parent / "fixtures" / "native-multiturn-observed-sanitized.json"


class ForkPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.parent = copy.deepcopy(self.fixture["native"])
        # A sanitized native spawn still carries a real, aware event timestamp.
        self.parent[2]["timestamp"] = (
            datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
        self.parent_id = self.fixture["parent_id"]
        self.rollout = self.root / "rollout-parent.jsonl"
        self.plans = self.root / ".benchmark" / "dispatch-plans"
        self.plans.mkdir(parents=True)

    def write(self) -> None:
        self.rollout.write_text("".join(json.dumps(event) + "\n"
                                    for event in self.parent), encoding="utf-8")

    def plan(self) -> dict:
        args = json.loads(self.parent[2]["payload"]["arguments"])
        packet = {"stage_id": "stage-1", "owner": args["task_name"],
                  "dependencies": [], "write_scope": ["file.go"],
                  "context_budget": "one bounded stage", "acceptance_criteria": "tests pass",
                  "self_check": "go test", "receipt": "compact result"}
        receipt = planned_commitment(args, packet)
        (self.plans / f"{args['task_name']}.plan.json").write_text(
            json.dumps({"arguments": args, "packet": packet}), encoding="utf-8")
        (self.plans / f"{args['task_name']}.json").write_text(
            json.dumps(receipt), encoding="utf-8")
        return receipt

    def test_planned_arguments_require_exact_none(self) -> None:
        args = json.loads(self.parent[2]["payload"]["arguments"])
        packet = {"stage_id": "stage-1", "owner": args["task_name"],
                  "dependencies": [], "write_scope": [], "context_budget": "bounded",
                  "acceptance_criteria": "done", "self_check": "check", "receipt": "compact"}
        self.assertEqual(planned_commitment(args, packet)["fork_turns"], "none")
        for variant in (None, "all", "1", 1):
            with self.subTest(variant=variant):
                changed = dict(args)
                if variant is None:
                    changed.pop("fork_turns")
                else:
                    changed["fork_turns"] = variant
                with self.assertRaisesRegex(ValueError, "context-policy-failure"):
                    planned_commitment(changed, packet)

    def test_cli_writes_only_commitment(self) -> None:
        args = json.loads(self.parent[2]["payload"]["arguments"])
        packet = {"stage_id": "stage-1", "owner": args["task_name"],
                  "dependencies": [], "write_scope": [], "context_budget": "bounded",
                  "acceptance_criteria": "done", "self_check": "check", "receipt": "compact"}
        plan = self.root / "plan.json"
        output = self.root / "commitment.json"
        plan.write_text(json.dumps({"arguments": args, "packet": packet}), encoding="utf-8")
        helper = Path(__file__).parents[1] / "long_horizon_v1" / "fork_policy.py"
        completed = subprocess.run([sys.executable, str(helper), "--plan", str(plan),
                                    "--output", str(output)], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn(args["message"], output.read_text(encoding="utf-8"))
        self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["fork_turns"], "none")

    def test_raw_spawn_and_followup_are_distinct(self) -> None:
        self.write()
        receipt = self.plan()
        calls, issues = validate_observed_spawns(
            self.rollout, self.parent_id, closed_snapshot=True, plans=self.plans)
        self.assertEqual(issues, [])
        self.assertEqual(len(calls), 1)  # Later followup_task is not another spawn.
        self.assertEqual(calls[0]["planned_plaintext_sha256"],
                         receipt["planned_plaintext_sha256"])
        self.assertEqual(calls[0]["packet_scope"], "VERIFIED")
        changed_model = json.loads(self.parent[2]["payload"]["arguments"])
        changed_model["model"] = "gpt-6-astra"
        self.parent[2]["payload"]["arguments"] = json.dumps(changed_model)
        self.write()
        _, issues = validate_observed_spawns(self.rollout, self.parent_id,
                                              plans=self.plans)
        self.assertTrue(any("planned argument commitment" in issue for issue in issues))
        self.parent[2]["payload"]["arguments"] = json.dumps(
            json.loads(self.fixture["native"][2]["payload"]["arguments"]))
        for variant in (None, "all", "2", 2):
            with self.subTest(variant=variant):
                changed = json.loads(self.parent[2]["payload"]["arguments"])
                if variant is None:
                    changed.pop("fork_turns")
                else:
                    changed["fork_turns"] = variant
                self.parent[2]["payload"]["arguments"] = json.dumps(changed)
                self.write()
                _, issues = observed_spawns(self.rollout, self.parent_id)
                self.assertTrue(any("fork_turns=" in issue for issue in issues))

    def test_receipt_observable_arguments_must_bind_saved_source(self) -> None:
        receipt = self.plan()
        args = json.loads(self.parent[2]["payload"]["arguments"])
        plan_path = self.plans / f"{args['task_name']}.json"
        for field, value in (("model", "gpt-6-astra"),
                             ("reasoning_effort", "medium")):
            with self.subTest(field=field):
                changed = {**args, field: value}
                forged_receipt = copy.deepcopy(receipt)
                forged_receipt["observable_arguments"][field] = value
                plan_path.write_text(json.dumps(forged_receipt), encoding="utf-8")
                self.parent[2]["payload"]["arguments"] = json.dumps(changed)
                self.write()
                calls, issues = validate_observed_spawns(
                    self.rollout, self.parent_id, closed_snapshot=True,
                    plans=self.plans)
                self.assertTrue(any("planned argument commitment" in issue
                                    for issue in issues), issues)
                self.assertEqual(calls[0]["packet_scope"], "PENDING")
        plan_path.write_text(json.dumps(receipt), encoding="utf-8")
        self.parent[2]["payload"]["arguments"] = json.dumps(args)

    def test_active_monitor_rejects_before_startup_or_usage_grace(self) -> None:
        args = json.loads(self.parent[2]["payload"]["arguments"])
        args["fork_turns"] = "all"
        self.parent[2]["payload"]["arguments"] = json.dumps(args)
        self.write()

        class Meter:
            paths = {self.rollout: {"id": self.parent_id, "calls": 0,
                                   "turns": [{"turn_id": "parent-turn-1",
                                              "model": "gpt-6-sol", "effort": "low"}]}}
            calls = 0
            unknown_models = set()
            unknown_usage = []

            @staticmethod
            def dispatch_coverage_issues() -> list:
                return []

        with self.assertRaisesRegex(TransportError, "context-policy-failure"):
            ActiveTelemetryGuard("treatment", self.root).check(
                Meter(), self.parent_id, "gpt-6-sol", "low", 0, 0,
                now=1, turn_id="parent-turn-1")

    def test_planned_receipt_must_precede_timestamped_spawn(self) -> None:
        self.plan()
        self.parent[2]["timestamp"] = "2020-01-01T00:00:00.000Z"
        self.write()
        _, issues = validate_observed_spawns(self.rollout, self.parent_id,
                                              plans=self.plans)
        self.assertTrue(any("not saved before spawn" in issue for issue in issues))
        self.parent[2]["timestamp"] = "2030-01-01T00:00:00.000Z"
        self.write()
        _, issues = validate_observed_spawns(self.rollout, self.parent_id,
                                              plans=self.plans)
        self.assertEqual(issues, [])

    def test_missing_or_invalid_spawn_timestamp_fails_closed(self) -> None:
        self.plan()
        valid = self.parent[2]["timestamp"]
        for variant in ("omitted", None, 123, "not-a-date", "2026-10-01T00:00:00"):
            with self.subTest(variant=variant):
                if variant == "omitted":
                    self.parent[2].pop("timestamp", None)
                else:
                    self.parent[2]["timestamp"] = variant
                self.write()
                _, raw_issues = observed_spawns(self.rollout, self.parent_id)
                self.assertTrue(any("native spawn timestamp missing or invalid"
                                    in issue for issue in raw_issues), raw_issues)
                _, issues = validate_observed_spawns(self.rollout, self.parent_id,
                                                      plans=self.plans)
                self.assertTrue(any("native spawn timestamp missing or invalid"
                                    in issue for issue in issues), issues)
                self.parent[2]["timestamp"] = valid

    def test_final_scan_rejects_late_duplicate_wrong_child_and_ledger(self) -> None:
        self.write()
        self.plan()
        base = copy.deepcopy(self.parent)
        call = {"call_id": "spawn-1", "call_line": 3, "task_name": "worker",
                "turn_id": "parent-turn-1", "fork_turns": "none",
                "child_id": "child-session", "agent_path": "/root/worker"}
        _, issues = validate_observed_spawns(self.rollout, self.parent_id, [call],
                                              closed_snapshot=True, plans=self.plans)
        self.assertEqual(issues, [])
        for variant in ("duplicate", "wrong-child", "wrong-child-id", "late", "wrong-ledger"):
            with self.subTest(variant=variant):
                self.parent = copy.deepcopy(base)
                ledger = [call]
                if variant == "duplicate":
                    self.parent.append(copy.deepcopy(self.parent[2]))
                elif variant == "wrong-child":
                    self.parent[3]["payload"]["item"]["agent_path"] = "/root/other"
                elif variant == "wrong-child-id":
                    self.parent[3]["payload"]["item"]["agent_thread_id"] = "other-child"
                elif variant == "late":
                    late = copy.deepcopy(self.parent[2])
                    late["payload"]["call_id"] = "spawn-late"
                    late_args = json.loads(late["payload"]["arguments"])
                    late_args["fork_turns"] = "all"
                    late["payload"]["arguments"] = json.dumps(late_args)
                    self.parent.append(late)
                else:
                    ledger = []
                self.write()
                _, issues = validate_observed_spawns(
                    self.rollout, self.parent_id, ledger,
                    closed_snapshot=True, plans=self.plans)
                self.assertTrue(any("context-policy-failure" in issue for issue in issues))

    def test_pilot11_encrypted_shape_is_unknown_without_false_mismatch(self) -> None:
        # Sanitized replay of the pilot-11 event shapes: the saved plan commits
        # plaintext; the native function call and child input carry one cipher.
        self.plan()
        child = copy.deepcopy(self.fixture["child"])
        cipher = child[4]["payload"]["content"][1]["encrypted_content"]
        args = json.loads(self.parent[2]["payload"]["arguments"])
        planned_args = {**args, "message": "bounded plaintext stage packet"}
        packet = json.loads((self.plans / f"{args['task_name']}.json").read_text())["packet"]
        plan = planned_commitment(planned_args, packet)
        (self.plans / f"{args['task_name']}.plan.json").write_text(
            json.dumps({"arguments": planned_args, "packet": packet}), encoding="utf-8")
        (self.plans / f"{args['task_name']}.json").write_text(
            json.dumps(plan), encoding="utf-8")
        args["message"] = cipher
        self.parent[2]["payload"]["arguments"] = json.dumps(args)
        child_path = self.root / "child.jsonl"
        child_path.write_text("".join(json.dumps(e) + "\n" for e in child), encoding="utf-8")
        self.write()
        children = {self.fixture["child_id"]: child_path}
        calls, issues = validate_observed_spawns(
            self.rollout, self.parent_id, closed_snapshot=True,
            plans=self.plans, child_rollouts=children, mode=DIAGNOSTIC)
        self.assertEqual(issues, [])
        self.assertEqual(calls[0]["packet_scope"], "UNKNOWN")
        self.assertEqual(calls[0]["planned_plaintext_sha256"],
                         plan["planned_plaintext_sha256"])
        self.assertEqual(calls[0]["native_encrypted_sha256"],
                         calls[0]["child_encrypted_sha256"])
        self.assertNotEqual(calls[0]["native_encrypted_sha256"],
                            calls[0]["planned_plaintext_sha256"])
        child[4]["payload"]["content"][1]["encrypted_content"] = "wrong-cipher"
        child_path.write_text("".join(json.dumps(e) + "\n" for e in child), encoding="utf-8")
        _, issues = validate_observed_spawns(self.rollout, self.parent_id,
            closed_snapshot=True, plans=self.plans, child_rollouts=children)
        self.assertTrue(any("ciphertext differs" in issue for issue in issues), issues)
        child[4]["payload"]["content"][1]["encrypted_content"] = cipher
        child_path.write_text("".join(json.dumps(e) + "\n" for e in child), encoding="utf-8")
        for change in ("missing-plan", "nonmessage", "fork-all", "fork-omitted"):
            with self.subTest(change=change):
                original = self.parent[2]["payload"]["arguments"]
                plan_path = self.plans / f"{args['task_name']}.json"
                if change == "missing-plan":
                    plan_path.unlink()
                else:
                    damaged = json.loads(original)
                    if change == "nonmessage":
                        damaged["reasoning_effort"] = "medium"
                    elif change == "fork-all":
                        damaged["fork_turns"] = "all"
                    else:
                        damaged.pop("fork_turns")
                    self.parent[2]["payload"]["arguments"] = json.dumps(damaged)
                self.write()
                _, issues = validate_observed_spawns(self.rollout, self.parent_id,
                    closed_snapshot=True, plans=self.plans, child_rollouts=children)
                self.assertTrue(issues, change)
                self.parent[2]["payload"]["arguments"] = original
                if change == "missing-plan":
                    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    def test_packet_scope_preflight_rejects_strict_without_paid_turn(self) -> None:
        started = []
        with self.assertRaisesRegex(ValueError, "trusted effective-plaintext attestation"):
            preflight_packet_scope(STRICT)
            started.append("turn/start")
        self.assertEqual(started, [])
        preflight_packet_scope(DIAGNOSTIC)

    def test_strict_runner_rejects_before_transport_or_output(self) -> None:
        output = self.root / "strict-output"
        plan = {"schema_version": 3, "run_outputs": {"treatment": str(output)},
                "packet_scope_mode": STRICT}
        with patch("evals.long_horizon_v1.run.manifest",
                   return_value={"pilot": {"path": "test"}}), \
             patch("evals.long_horizon_v1.run.pilot_plan", return_value=plan), \
             patch("evals.long_horizon_v1.run.verify_diagnostic_reference"), \
             patch("evals.long_horizon_v1.run.live_preflight", return_value={}), \
             patch("evals.long_horizon_v1.run.AppServerTransport") as transport:
            with self.assertRaisesRegex(ValueError, "trusted effective-plaintext attestation"):
                live_run(self.root, output, "treatment", self.root / "cli",
                         self.root / "capability", self.root / "sessions")
            transport.assert_not_called()
        self.assertFalse(output.exists())

    def test_cleartext_child_exact_match_is_verified(self) -> None:
        receipt = self.plan()
        args = json.loads(self.parent[2]["payload"]["arguments"])
        child = copy.deepcopy(self.fixture["child"])
        child[4]["payload"]["content"] = [{"type": "input_text", "text": args["message"]}]
        child_path = self.root / "child.jsonl"
        child_path.write_text("".join(json.dumps(e) + "\n" for e in child), encoding="utf-8")
        self.write()
        calls, issues = validate_observed_spawns(self.rollout, self.parent_id,
            closed_snapshot=True, plans=self.plans,
            child_rollouts={self.fixture["child_id"]: child_path})
        self.assertEqual(issues, [])
        self.assertEqual(calls[0]["packet_scope"], "VERIFIED")
        self.assertEqual(calls[0]["planned_plaintext_bytes"],
                         receipt["planned_plaintext_bytes"])

    def test_collector_never_promotes_diagnostic_to_selective_success(self) -> None:
        pair = self.root / "pair"
        for name in ("baseline", "treatment"):
            (pair / name).mkdir(parents=True)
            (pair / name / "run.json").write_text("{}", encoding="utf-8")
        baseline = {"mode": "live", "parent_thread_id": "baseline",
                    "stop_reason": "accepted-final", "cost_status": "complete",
                    "usage": {"estimated_usd": 2.0}}
        treatment = {"mode": "live", "parent_thread_id": "treatment",
                     "stop_reason": "accepted-final", "cost_status": "complete",
                     "usage": {"estimated_usd": 1.0},
                     "packet_scope_mode": DIAGNOSTIC}
        quality = {"status": "reviewed", "quality_pass": True}
        with patch("evals.long_horizon_v1.collect._arm",
                   side_effect=[baseline, treatment]), \
             patch("evals.long_horizon_v1.collect.manifest",
                   return_value={"pilot": {"path": "pilot-plan-v9.json", "schema_version": 1}}), \
             patch("evals.long_horizon_v1.collect._quality", return_value=quality), \
             patch("evals.long_horizon_v1.collect.file_sha", return_value="sha"):
            result = collect(pair, self.root / "out")
        self.assertEqual(result["status"],
                         "diagnostic-feasibility-pending-independent-review")
        self.assertIsNone(result["cost_ratio_before_quality"])
        self.assertIsNone(result["quality_parity"])

    def test_paired_collector_rejects_standalone_before_baseline_read_or_output(self) -> None:
        pair = self.root / "standalone-pair"
        baseline = pair / "baseline" / "run.json"
        treatment = pair / "treatment" / "run.json"
        baseline.parent.mkdir(parents=True)
        treatment.parent.mkdir(parents=True)
        baseline.write_text("BASELINE MUST NOT BE READ", encoding="utf-8")
        treatment.write_text(json.dumps({"benchmark_mode": "standalone-feasibility",
            "claim_class": "standalone-treatment-feasibility"}), encoding="utf-8")
        original = Path.read_text

        def guarded(path: Path, *args, **kwargs):
            if path == baseline:
                raise AssertionError("baseline sentinel was read")
            return original(path, *args, **kwargs)

        with patch.object(Path, "read_text", guarded):
            with self.assertRaisesRegex(ValueError, "standalone plan"):
                collect(pair, self.root / "rejected-manifest")
            with patch("evals.long_horizon_v1.collect.manifest",
                       return_value={"pilot": {"path": "pilot-plan-v9.json", "schema_version": 1}}):
                with self.assertRaisesRegex(ValueError, "standalone treatment"):
                    collect(pair, self.root / "rejected-receipt")
        self.assertFalse((self.root / "rejected-manifest").exists())
        self.assertFalse((self.root / "rejected-receipt").exists())


if __name__ == "__main__":
    unittest.main()
