"""Replay current cancellation receipts without changing their saved bytes."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from evals.long_horizon_v1.cancellation_adjudication import (
    _judge_current, verify_current_decision)
from evals.long_horizon_v1.common import HERE


ROOT = HERE / "_scratch/pilot-16-evidence"
REVIEW_SHA = "c5b862d8f73785a389cdc94f4aca85b6082e5a4acd6c9505d1b0091201591098"


class CurrentCancellationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sessions = Path.home() / ".codex/sessions"
        self.review = ROOT / "current-cancellation-independent-review.json"
        self.paths = {arm: ROOT / f"canary-{arm}/cancellation.json"
                      for arm in ("baseline", "treatment")}
        self.decisions = {arm: ROOT / f"canary-{arm}/current-adjudication-v3.json"
                          for arm in self.paths}
        if not self.sessions.is_dir() or not self.review.is_file() or any(
                not path.is_file() or not self.decisions[arm].is_file()
                for arm, path in self.paths.items()):
            self.skipTest("retained current canary raw evidence unavailable")

    def receipt(self, arm: str) -> dict:
        return json.loads(self.paths[arm].read_text(encoding="utf-8"))

    def test_both_real_receipts_replay_and_match_bound_decisions(self) -> None:
        for arm in self.paths:
            with self.subTest(arm=arm):
                source = self.receipt(arm)
                proof = _judge_current(source, self.sessions, self.paths[arm])
                self.assertEqual(proof["source_usage_sha256"],
                                 proof["current_usage_sha256"])
                decision = verify_current_decision(self.paths[arm], self.sessions,
                    self.decisions[arm], self.review, REVIEW_SHA)
                self.assertEqual(decision["proof"], proof)

    def test_missing_extra_and_wrong_response_usage_rejected(self) -> None:
        for mutation in ("missing", "extra", "wrong-cost", "wrong-turn"):
            source = copy.deepcopy(self.receipt("treatment"))
            responses = source["usage"]["sessions"][0]["responses"]
            if mutation == "missing":
                responses.pop()
            elif mutation == "extra":
                responses.append(copy.deepcopy(responses[0]))
            elif mutation == "wrong-cost":
                responses[0]["estimated_usd"] += 0.01
            else:
                responses[0]["turn_id"] = "wrong-turn"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                _judge_current(source, self.sessions, self.paths["treatment"])

    def test_saved_native_turn_count_and_identity_rejected(self) -> None:
        for mutation in ("missing", "extra", "wrong-id", "invented-closure"):
            source = copy.deepcopy(self.receipt("treatment"))
            turns = source["native_attempts"][0]["turns"]
            if mutation == "missing":
                turns.clear()
            elif mutation == "extra":
                turns.append(copy.deepcopy(turns[0]))
            elif mutation == "wrong-id":
                turns[0]["turn_id"] = "wrong-turn"
            else:
                turns[0]["closure"] = "interrupted"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                _judge_current(source, self.sessions, self.paths["treatment"])

    def test_mutated_decision_rejected(self) -> None:
        decision = json.loads(self.decisions["baseline"].read_text(encoding="utf-8"))
        decision["source_receipt_sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "decision.json"
            path.write_text(json.dumps(decision), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "differs from raw replay"):
                verify_current_decision(self.paths["baseline"], self.sessions,
                    path, self.review, REVIEW_SHA)


if __name__ == "__main__":
    unittest.main()
