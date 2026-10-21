"""Fail-closed checks for the explicit v17 fixed historical comparator."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from evals.long_horizon_v1.common import HERE, manifest
from evals.long_horizon_v1.fixed_baseline import (
    verify_fixed_baseline, verify_hard_stages)
from evals.long_horizon_v1.run import pilot_plan


class FixedBaselineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = manifest()
        if self.spec.get("pilot", {}).get("path") != "pilot-plan-v18.json":
            self.skipTest("v18 fixed baseline plan is not active")
        self.plan = pilot_plan(self.spec)
        if not (HERE / self.plan["fixed_baseline"]["run"]["path"]).is_file():
            self.skipTest("retained v17 baseline evidence unavailable")

    def test_original_admission_and_shared_core_verify(self) -> None:
        result = verify_fixed_baseline(self.spec, self.plan)
        self.assertEqual(result["status"], "verified-fixed-historical-baseline")
        self.assertEqual(result["estimated_usd"], 8.9516085)
        self.assertEqual(self.plan["arm_order"], ["treatment"])

    def test_artifact_task_policy_source_and_allowed_delta_drift_rejected(self) -> None:
        changes = (
            ("run-hash", lambda spec, plan: plan["fixed_baseline"]["run"].update(sha256="0" * 64)),
            ("admission-hash", lambda spec, plan: plan["fixed_baseline"]["admission"].update(sha256="0" * 64)),
            ("design-review", lambda spec, plan: plan["fixed_baseline"]["design_review"].update(sha256="0" * 64)),
            ("task-reveal", lambda spec, plan: spec["assets"].update({"round0.md": "0" * 64})),
            ("pricing", lambda spec, plan: spec["pricing"].update({"tier": "changed"})),
            ("baseline-suffix", lambda spec, plan: spec["arm_execution"]["prompt_suffix_sha256"].update(baseline="0" * 64)),
            ("accounting-source", lambda spec, plan: plan["execution_sources_sha256"].update({"accounting.py": "0" * 64})),
            ("unreviewed-delta", lambda spec, plan: plan["fixed_baseline"]["allowed_changed_sources"].append("accounting.py")),
        )
        for label, change in changes:
            spec, plan = copy.deepcopy(self.spec), copy.deepcopy(self.plan)
            change(spec, plan)
            with self.subTest(label=label), self.assertRaises(ValueError):
                verify_fixed_baseline(spec, plan)

    def test_hard_episode_receipts_join_completed_child_turns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            completed = "2026-10-09T04:00:00Z"
            submit_ns = int(datetime(2026, 10, 9, 4, 1, tzinfo=timezone.utc).timestamp() * 1e9)
            run = {"attempts": [
                {"round": 1, "turn_id": "parent-r1", "stage_event_start": 0, "stage_event_end": 2},
                {"round": 2, "turn_id": "parent-r2", "stage_event_start": 2, "stage_event_end": 4}],
                "stage": {"events": [
                    {"kind": "submit", "time_ns": submit_ns},
                    {"kind": "gate", "behavior_pass": True},
                    {"kind": "submit", "time_ns": submit_ns},
                    {"kind": "gate", "behavior_pass": True}]},
                "native_attempts": [{"child_id": "worker", "turns": [
                    {"turn_id": "worker-r1", "parent_turn_id": "parent-r1",
                     "model": "gpt-6-astra", "effort": "xhigh",
                     "terminal": "task_complete", "terminal_timestamp": completed},
                    {"turn_id": "worker-r2", "parent_turn_id": "parent-r2",
                     "model": "gpt-6-astra", "effort": "xhigh",
                     "terminal": "task_complete", "terminal_timestamp": completed}]}]}
            paths = {}
            for round_id in (1, 2):
                path = workspace / ".benchmark/hard-kernel" / f"round{round_id}.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                value = {"schema_version": 1, "round": round_id,
                    "worker_id": "worker", "worker_turn_id": f"worker-r{round_id}",
                    "parent_turn_id": f"parent-r{round_id}",
                    "model": "gpt-6-astra", "effort": "xhigh",
                    "implementation_owner": "controller",
                    "revealed_report_sha256": self.spec["assets"][f"round{round_id}.md"],
                    "invariants": ["publicly revealed invariant"],
                    "repair_plan": "bounded plan", "review": "independent review",
                    "self_check": "checked", "public_checks": ["go test"]}
                path.write_text(json.dumps(value), encoding="utf-8")
                paths[round_id] = (path, value)
            result = verify_hard_stages(self.spec, self.plan, run, workspace)
            self.assertEqual(set(result), {"1", "2"})
            for label, mutation in (
                ("wrong-worker-turn", lambda: paths[1][1].update(worker_turn_id="wrong")),
                ("empty-invariants", lambda: paths[1][1].update(invariants=[])),
                ("late-worker", lambda: run["native_attempts"][0]["turns"][0].update(
                    terminal_timestamp="2026-10-09T04:02:00Z"))):
                original = copy.deepcopy(paths[1][1])
                original_turn = copy.deepcopy(run["native_attempts"][0]["turns"][0])
                mutation()
                paths[1][0].write_text(json.dumps(paths[1][1]), encoding="utf-8")
                with self.subTest(label=label), self.assertRaises(ValueError):
                    verify_hard_stages(self.spec, self.plan, run, workspace)
                paths[1][1].clear(); paths[1][1].update(original)
                run["native_attempts"][0]["turns"][0] = original_turn
                paths[1][0].write_text(json.dumps(original), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
