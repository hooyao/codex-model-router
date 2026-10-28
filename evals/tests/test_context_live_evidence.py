"""Recompute published live token/cost arithmetic without making model calls."""

import json
import unittest
from pathlib import Path

from evals.scripts.run_paired_arm import USAGE_KEYS, response_cost_interval


class ContextLiveEvidenceTests(unittest.TestCase):
    def test_published_response_classes_reconcile_for_all_scored_sessions(self):
        root = Path(__file__).resolve().parents[1] / "live/context-regression-20261028"
        campaign = json.loads((root / "scored/campaign.json").read_text(encoding="utf-8"))
        rates = campaign["rate_card"]["per_million"]
        records = [json.loads(p.read_text(encoding="utf-8")) for p in (root / "scored").glob("scored-*.json")]
        self.assertEqual(30, len(records))
        for record in records:
            with self.subTest(run=record["run_id"]):
                total = {key: 0 for key in USAGE_KEYS}
                cost, calls = 0.0, 0
                for session in record["usage"]["sessions"]:
                    for response in session["responses"]:
                        usage = response["token_classes"]
                        low, high = response_cost_interval(usage, response["model"], rates)
                        self.assertEqual(low, high)
                        cost += low
                        calls += 1
                        for key in USAGE_KEYS:
                            total[key] += usage[key]
                self.assertEqual(total, record["usage"]["token_classes"])
                self.assertEqual(calls, record["usage"]["model_calls"])
                self.assertAlmostEqual(cost, record["usage"]["estimated_usd"], places=8)
                self.assertEqual([], record["usage_issues"])

    def test_lost_timer_and_failed_strict_scores_are_not_rewritten(self):
        root = Path(__file__).resolve().parents[1] / "live/context-regression-20261028"
        missing = json.loads((root / "scored/scored-architecture-review-r1-before.json").read_text(encoding="utf-8"))
        self.assertIsNone(missing["wall_seconds"])
        analysis = json.loads((root / "analysis.json").read_text(encoding="utf-8"))
        self.assertEqual(24, analysis["strict_artifact_passes"])
        self.assertEqual(6, analysis["strict_artifact_failures"])
        self.assertTrue(analysis["verdict"].startswith("NOT_PASSED"))


if __name__ == "__main__":
    unittest.main()
