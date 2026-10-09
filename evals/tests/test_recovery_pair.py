"""Focused fail-closed checks for the pilot-15 matched empirical gate."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from evals.long_horizon_v1.common import TASK_ID, file_sha, sha
from evals.long_horizon_v1.collect import collect_recovery
from evals.long_horizon_v1.end_to_end import finalize_arm
from evals.long_horizon_v1.fork_policy import STRICT, preflight_packet_scope
from evals.long_horizon_v1.grade import REVIEW_REQUIREMENTS
from evals.long_horizon_v1.recovery_quality import verify_recovery_arm_quality


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


class RecoveryPairTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.spec = {"pilot": {"path": "pilot-plan-v15.json", "schema_version": 5},
                     "start_tree": "seed-tree"}
        write_json(self.root / "manifest.json", self.spec)
        write_json(self.root / "pilot-plan-v15.json", {"version": 15})
        self.plan = {"mode": "paired-recovery", "pilot_id": "pilot-15",
            "arm_order": ["baseline", "treatment"],
            "preparation_root": "prep", "evidence_root": "evidence",
            "run_outputs": {arm: f"evidence/{arm}-live" for arm in ("baseline", "treatment")},
            "end_to_end": {"receipts": {arm: f"evidence/{arm}-end-to-end.json"
                         for arm in ("baseline", "treatment")}}}
        self.prepared = self.root / "prep"
        write_json(self.prepared / "preparation.json", {
            "fixture_manifest_sha256": file_sha(self.root / "manifest.json"),
            "arms": {arm: {"start_tree": "seed-tree", "revealed": ["R0"]}
                     for arm in self.plan["arm_order"]}})
        self.runs = {arm: self.make_run(arm) for arm in self.plan["arm_order"]}
        self.pair = self.root / "pair"
        for arm, run in self.runs.items():
            path = self.root / self.plan["run_outputs"][arm] / "run.json"
            write_json(path, run)
            (path.parent / "candidate-g2-attempt0.patch").write_bytes(b"")
            review = {"schema_version": 1, "reviewer_role": "independent",
                "arm_blind": True, "reviewer_id": "reviewer", "verdict": "pass",
                "candidate_patch_sha256": sha(b""),
                "manifest_sha256": file_sha(self.root / "manifest.json"),
                "preparation_sha256": file_sha(self.prepared / "preparation.json"),
                "requirements": dict.fromkeys(REVIEW_REQUIREMENTS, True)}
            write_json(self.root / "evidence" / f"{arm}-review.json", review)
            for kind in ("hidden", "backend"):
                write_json(self.root / "evidence" / f"{arm}-{kind}-grade" / "grade.json",
                           {"kind": kind})
            destination = self.pair / arm / "run.json"
            destination.parent.mkdir(parents=True)
            shutil.copyfile(path, destination)
        with self.patches():
            for arm, run in self.runs.items():
                quality = verify_recovery_arm_quality(self.prepared, self.plan, run, arm,
                                                      require_wall=False)
                write_json(self.root / self.plan["end_to_end"]["receipts"][arm], {
                    "schema_version": 1, "kind": "arm-end-to-end-boundary",
                    "pilot_id": self.plan["pilot_id"], "task_id": TASK_ID, "arm": arm,
                    "manifest_sha256": file_sha(self.root / "manifest.json"),
                    "pilot_plan_sha256": file_sha(self.root / "pilot-plan-v15.json"),
                    "preparation_sha256": file_sha(self.prepared / "preparation.json"),
                    "run_sha256": file_sha(self.root / self.plan["run_outputs"][arm] / "run.json"),
                    "quality_files_sha256": quality["quality_files_sha256"],
                    "started_utc_ns": 1_000_000_000, "quality_finished_utc_ns": 6_000_000_000,
                    "run_wall_seconds": 2.0, "end_to_end_wall_seconds": 5.0})

    def patches(self):
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch("evals.long_horizon_v1.collect.HERE", self.root))
        stack.enter_context(patch("evals.long_horizon_v1.collect.manifest", return_value=self.spec))
        stack.enter_context(patch("evals.long_horizon_v1.run.pilot_plan", return_value=self.plan))
        stack.enter_context(patch("evals.long_horizon_v1.recovery_quality.HERE", self.root))
        stack.enter_context(patch("evals.long_horizon_v1.recovery_quality.manifest",
                                  return_value=self.spec))
        def grade(path, *_args, **_kwargs):
            receipt = path / "grade.json"
            if not receipt.is_file():
                raise ValueError("hidden or backend grade missing")
            return file_sha(receipt)
        stack.enter_context(patch("evals.long_horizon_v1.recovery_quality._grade",
                                  side_effect=grade))
        stack.enter_context(patch("evals.long_horizon_v1.collect._arm",
                                  side_effect=lambda _path, arm: self.runs[arm]))
        return stack

    def make_run(self, arm: str) -> dict:
        events = []
        previous = "0" * 64
        def event(kind: str, round_id: int, **fields):
            nonlocal previous
            payload = {"seq": len(events), "time_ns": len(events) + 1,
                       "kind": kind, "arm": arm, "round": round_id,
                       "thread_id": arm + "-parent", "previous_sha256": previous, **fields}
            previous = sha(json.dumps(payload, sort_keys=True,
                                      separators=(",", ":")).encode())
            events.append({**payload, "sha256": previous})
        attempts = []
        for round_id in range(3):
            start = len(events)
            event("submit", round_id, patch_sha256=sha(b""), repair=0)
            event("gate", round_id, patch_sha256=sha(b""), behavior_pass=True)
            if round_id < 2:
                event("reveal", round_id + 1, assets=[f"R{round_id + 1}"])
            else:
                event("accepted-final", round_id, patch_sha256=sha(b""))
            attempts.append({"round": round_id, "repair": 0,
                             "thread_id": arm + "-parent",
                             "stage_event_start": start, "stage_event_end": len(events)})
        return {"arm": arm, "task_id": TASK_ID, "stop_reason": "accepted-final",
            "cost_status": "complete", "usage_issues": [], "interruption_proof": None,
            "claim_class": "matched-empirical-scope-unknown", "benchmark_mode": None,
            "parent_thread_id": arm + "-parent", "started_utc_ns": 1_000_000_000,
            "wall_seconds": 2.0, "shared_initial_prompt_sha256": "prompt",
            "runtime_binding": {"preparation_sha256": file_sha(self.prepared / "preparation.json")},
            "usage": {"estimated_usd": 2.0 if arm == "baseline" else 1.0,
                      "estimated_usd_upper_bound": 2.0 if arm == "baseline" else 1.0,
                      "unknown_models": [], "unknown_usage": []},
            "packet_scope_mode": "diagnostic-feasibility" if arm == "treatment" else None,
            "fork_policy": {"packet_scope": "UNKNOWN"} if arm == "treatment" else None,
            "stage": {"complete": True, "round": 2,
                      "manifest_sha256": file_sha(self.root / "manifest.json"),
                      "events": events, "chain_sha256": previous}, "attempts": attempts}

    def test_both_arms_full_quality_and_matched_cost_pass(self) -> None:
        with self.patches():
            result = collect_recovery(self.pair, self.root / "result")
        self.assertEqual(result["status"], "matched-empirical-quality-pass-scope-unknown")
        self.assertEqual(result["treatment_to_baseline_cost_ratio"], 0.5)
        self.assertEqual(result["end_to_end_wall_seconds"],
                         {"baseline": 5.0, "treatment": 5.0})
        self.assertFalse(result["technical_context_isolation_claim"])
        self.assertFalse(result["stable_latency_claim"])

    def test_missing_backend_semantic_or_end_to_end_rejects_pair(self) -> None:
        for label, path in (
            ("hidden-race", self.root / "evidence/treatment-hidden-grade/grade.json"),
            ("backend", self.root / "evidence/baseline-backend-grade/grade.json"),
            ("semantic", self.root / "evidence/treatment-review.json"),
            ("end-to-end", self.root / "evidence/baseline-end-to-end.json")):
            original = path.read_bytes()
            path.unlink()
            with self.subTest(label=label), self.patches(), self.assertRaises((ValueError, OSError)):
                collect_recovery(self.pair, self.root / ("rejected-" + label))
            self.assertFalse((self.root / ("rejected-" + label)).exists())
            path.write_bytes(original)

    def test_prompt_reveals_and_arm_completion_must_match(self) -> None:
        for label, change in (
            ("prompt", lambda: self.runs["treatment"].update(shared_initial_prompt_sha256="other")),
            ("reveal", lambda: self.runs["treatment"]["stage"]["events"][2].update(assets=["other"])),
            ("completion", lambda: self.runs["baseline"].update(stop_reason="failed"))):
            before = json.loads(json.dumps(self.runs))
            change()
            with self.subTest(label=label), self.patches(), self.assertRaises(ValueError):
                collect_recovery(self.pair, self.root / ("rejected-" + label))
            self.runs = before

    def test_strict_scope_still_rejects_unknown_attestation(self) -> None:
        with self.assertRaisesRegex(ValueError, "trusted effective-plaintext attestation"):
            preflight_packet_scope(STRICT)

    def test_end_to_end_verifies_either_arm_before_writing(self) -> None:
        run = self.runs["baseline"]
        with patch("evals.long_horizon_v1.end_to_end.HERE", self.root), \
             patch("evals.long_horizon_v1.end_to_end._arm", return_value=run), \
             patch("evals.long_horizon_v1.end_to_end.manifest", return_value=self.spec), \
             patch("evals.long_horizon_v1.end_to_end.verify_recovery_arm_quality",
                   side_effect=ValueError("missing semantic review")), \
             self.assertRaisesRegex(ValueError, "missing semantic review"):
            finalize_arm(self.prepared, "baseline", self.plan)


if __name__ == "__main__":
    unittest.main()
