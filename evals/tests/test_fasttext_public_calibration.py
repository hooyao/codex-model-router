"""Offline contract checks for the frozen public fastText calibration plan."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from evals.scripts.calibrate_fasttext_public import train_command, validate_plan


class FastTextPublicCalibrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan_path = Path(__file__).resolve().parents[1] / "paired_training" / "public-calibration-plan.json"
        self.plan = json.loads(self.plan_path.read_text(encoding="utf-8"))

    def test_plan_is_predeclared_and_bounded_to_four(self) -> None:
        configurations = validate_plan(self.plan)
        self.assertEqual(4, len(configurations))
        self.assertEqual(0.63, self.plan["minimum_public_accuracy"])
        self.assertEqual(157286400, self.plan["maximum_model_bytes_exclusive"])
        self.assertTrue(all(item["minimum_count"] >= 2 for item in configurations))

    def test_every_training_command_is_single_threaded(self) -> None:
        for config in validate_plan(self.plan):
            command = train_command(Path("/fasttext"), Path("/train"), Path("/model"), config)
            self.assertEqual("1", command[command.index("-thread") + 1])

    def test_frozen_public_result_has_no_private_selection(self) -> None:
        result_path = self.plan_path.with_name("public-calibration-result.json")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual("NO_ELIGIBLE_CANDIDATE", result["status"])
        self.assertEqual(4, len(result["results"]))
        self.assertIsNone(result["selected_candidate"])
        self.assertEqual(0, result["private_verifier_calls"])
        self.assertLess(max(item["public_accuracy"] for item in result["results"]), 0.63)
        self.assertTrue(all(item["model_bytes"] < 157286400 for item in result["results"]))


if __name__ == "__main__":
    unittest.main()
