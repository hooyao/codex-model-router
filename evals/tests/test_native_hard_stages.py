"""Join model-visible worker paths to authoritative paid native stage turns."""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from evals.long_horizon_v1.common import file_sha
from evals.long_horizon_v1.end_to_end import finalize_arm
from evals.long_horizon_v1.fixed_baseline import verify_hard_stages


class NativeHardStageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.spec = {"assets": {f"round{i}.md": str(i) * 64 for i in (1, 2)}}
        self.plan = {"mode": "common-quality-recovery"}
        self.run = {"attempts": [], "stage": {"events": []},
                    "native_attempts": [{"child_id": "native-worker", "agent_path": "/root/worker",
                                         "turns": []}], "per_turn_responses": []}
        for number in (1, 2):
            start = len(self.run["stage"]["events"])
            base = 1_700_000_000_000_000_000 + number * 120_000_000_000
            self.run["stage"]["events"].extend([
                {"kind": "reveal", "round": number, "time_ns": base},
                {"kind": "submit", "time_ns": base + 60_000_000_000},
                {"kind": "gate", "behavior_pass": True}])
            self.run["attempts"].append({"round": number, "turn_id": f"parent-{number}",
                "stage_event_start": start + 1, "stage_event_end": start + 3})
            self.run["native_attempts"][0]["turns"].append({
                "turn_id": f"native-turn-{number}", "parent_turn_id": f"parent-{number}",
                "root_turn_id": f"parent-{number}", "model": "gpt-6-astra", "effort": "xhigh",
                "terminal": "task_complete", "closure": "completed",
                "terminal_timestamp": datetime.fromtimestamp(
                    (base + 30_000_000_000) / 1e9, timezone.utc).isoformat()})
            self.run["per_turn_responses"].append({"session_id": "native-worker",
                "turn_id": f"native-turn-{number}", "root_turn_id": f"parent-{number}",
                "model": "gpt-6-astra", "effort": "xhigh", "estimated_usd_lower_bound": .1})
            receipt = {"schema_version": 1, "round": number, "worker_id": "/root/worker",
                "worker_turn_id": f"worker:round-{number}", "parent_turn_id": f"controller:round-{number}",
                "model": "gpt-6-astra", "effort": "xhigh", "implementation_owner": "controller",
                "revealed_report_sha256": self.spec["assets"][f"round{number}.md"],
                "invariants": ["revealed invariant"], "repair_plan": "bounded repair",
                "review": "reviewed", "self_check": "checked", "public_checks": ["go test"]}
            path = self.root / f".benchmark/hard-kernel/round{number}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(receipt), encoding="utf-8")

    def test_paths_and_descriptive_labels_bind_native_priced_rounds(self):
        observed = verify_hard_stages(self.spec, self.plan, self.run, self.root)
        self.assertEqual(observed["1"]["worker_id"], "native-worker")
        self.assertEqual(observed["1"]["worker_turn_id"], "native-turn-1")
        self.assertEqual(observed["2"]["worker_turn_id"], "native-turn-2")

    def test_wrong_round_selector_missing_price_and_ambiguous_turn_are_rejected(self):
        for variant in ("parent", "root", "model", "effort", "failed", "open", "unpriced", "late", "early", "duplicate"):
            run = copy.deepcopy(self.run)
            turn = run["native_attempts"][0]["turns"][0]
            if variant == "parent": turn["parent_turn_id"] = "parent-2"
            elif variant == "root": turn["root_turn_id"] = "parent-2"
            elif variant == "model": turn["model"] = "gpt-6-sol"
            elif variant == "effort": turn["effort"] = "low"
            elif variant == "failed": turn["terminal"] = "task_failed"
            elif variant == "open": turn["closure"] = "pending"
            elif variant == "unpriced": run["per_turn_responses"].pop(0)
            elif variant == "late": turn["terminal_timestamp"] = "2030-01-01T00:00:00Z"
            elif variant == "early": turn["terminal_timestamp"] = "2020-01-01T00:00:00Z"
            elif variant == "duplicate": run["native_attempts"][0]["turns"].append(copy.deepcopy(turn))
            with self.subTest(variant=variant), self.assertRaises(ValueError):
                verify_hard_stages(self.spec, self.plan, run, self.root)

    def test_common_finalization_uses_retained_quality_completion_not_repair_clock(self):
        prepared = self.root / "prepared"
        prepared.mkdir()
        (prepared / "preparation.json").write_text("{}", encoding="utf-8")
        for name in ("manifest.json", "plan.json", "run.json"):
            (self.root / name).write_text("{}", encoding="utf-8")
        spec = {"pilot": {"path": "plan.json"}}
        plan = {"mode": "common-quality-recovery", "evidence_root": ".",
                "run_outputs": {"treatment": "."}, "pilot_id": "test",
                "end_to_end": {"receipts": {"treatment": "wall.json"}}}
        run = {"stop_reason": "accepted-final", "cost_status": "complete",
               "session_root": str(self.root),
               "started_utc_ns": 1_000_000_000, "wall_seconds": 10.25,
               "runtime_binding": {"preparation_sha256": file_sha(prepared / "preparation.json")},
               "stage": {"events": [{"kind": "final-quality", "time_ns": 11_000_000_000}]}}
        with patch("evals.long_horizon_v1.end_to_end.HERE", self.root), \
             patch("evals.long_horizon_v1.end_to_end.manifest", return_value=spec), \
             patch("evals.long_horizon_v1.end_to_end._arm", return_value=run), \
             patch("evals.long_horizon_v1.end_to_end.verify_common_arm_quality", return_value={"quality_files_sha256": {}}), \
             patch("evals.long_horizon_v1.end_to_end.time.time_ns", return_value=100_000_000_000):
            result = finalize_arm(prepared, "treatment", plan)
        self.assertEqual(result["quality_finished_utc_ns"], 11_000_000_000)
        self.assertEqual(result["end_to_end_wall_seconds"], 10.0)
