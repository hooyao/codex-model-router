"""Bounded evaluator metering and exact closed-turn reuse without model calls."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from evals.long_horizon_v1.grade import REVIEW_REQUIREMENTS
from evals.long_horizon_v1.quality_adapter import (
    EvaluatorBudgetGuard, _completed_native_review, _review_prompt)
from evals.long_horizon_v1.transport import TransportError


class QualityAdapterRecoveryTests(unittest.TestCase):
    def test_budget_guard_batches_frames_and_forces_terminal_refresh(self) -> None:
        class Meter:
            def __init__(self):
                self.refreshes = 0
                self.cost_upper = 0.4
                self.unknown_models = set()
                self.unknown_usage = []
            def refresh(self): self.refreshes += 1
        meter = Meter()
        with tempfile.TemporaryDirectory() as directory, patch(
                "evals.long_horizon_v1.quality_adapter.time.monotonic",
                side_effect=[100, 100, 100.2, 100.2, 100.8, 100.8,
                             101.1, 101.1, 101.2, 101.2]):
            guard = EvaluatorBudgetGuard(meter, 200, Path(directory) / "cancel", 1, 0)
            for _ in range(4):
                guard()
            self.assertEqual(meter.refreshes, 2)
            guard(force=True)
            self.assertEqual(meter.refreshes, 3)
        meter.cost_upper = 14.0
        guard.deadline = float("inf")
        with self.assertRaises(TransportError):
            guard(force=True)

    def test_existing_native_review_requires_exact_closed_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root, sessions, neutral = (base / name for name in ("quality", "sessions", "neutral"))
            root.mkdir(); sessions.mkdir()
            scratch = neutral / "review-one" / "scratch"
            product = scratch.parent / "product"
            scratch.mkdir(parents=True); product.mkdir()
            thread, turn = "evaluator-thread", "evaluator-turn"
            request_sha, candidate = "request-sha", b"candidate"
            candidate_sha = hashlib.sha256(candidate).hexdigest()
            registration = {"schema_version": 1, "request_sha256": request_sha,
                "thread_id": thread, "session_root": str(sessions.resolve()),
                "model": "gpt-6-astra", "effort": "xhigh"}
            (root / "evaluator-r0.json").write_text(json.dumps(registration), encoding="utf-8")
            rollout = sessions / "rollout.jsonl"
            rows = [
                {"type": "session_meta", "payload": {"id": thread, "cwd": str(scratch)}},
                {"type": "response_item", "payload": {"type": "message", "role": "user",
                    "content": [{"text": _review_prompt(product)}]}},
                {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": turn}},
            ]
            rollout.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            usage = {"status": "complete", "issues": [], "summary": {"model_calls": 2,
                "sessions": [{"id": thread, "model": "gpt-6-astra", "effort": "xhigh",
                    "terminal": "task_complete", "turns": [{"turn_id": turn,
                        "terminal": "task_complete"}]}]}}
            report = {"assessment_status": "COMPLETE",
                "requirements": dict.fromkeys(REVIEW_REQUIREMENTS, True), "diagnostics": []}
            class Meter:
                def __init__(self, *_args):
                    self.paths = {rollout: {"id": thread}}
                    self.calls = 2
                def refresh(self): pass
            with patch("evals.long_horizon_v1.quality_adapter.account", return_value=usage), \
                 patch("evals.long_horizon_v1.quality_adapter.SessionMeter", Meter), \
                 patch("evals.long_horizon_v1.quality_adapter.capture_patch", return_value=candidate), \
                 patch("evals.long_horizon_v1.quality_adapter.product_snapshot", return_value={"fileset": "same"}), \
                 patch("evals.long_horizon_v1.quality_adapter._last_message",
                       return_value=json.dumps(report)):
                self.assertEqual(_completed_native_review(root, 0, request_sha, sessions,
                    neutral, candidate_sha, {"fileset": "same"}, candidate)[2], report)
                with self.assertRaisesRegex(ValueError, "registration"):
                    _completed_native_review(root, 0, "other-request", sessions,
                        neutral, candidate_sha, {"fileset": "same"}, candidate)
                usage["summary"]["sessions"][0]["terminal"] = "turn_aborted"
                with self.assertRaisesRegex(ValueError, "incomplete"):
                    _completed_native_review(root, 0, request_sha, sessions,
                        neutral, candidate_sha, {"fileset": "same"}, candidate)


if __name__ == "__main__":
    unittest.main()
