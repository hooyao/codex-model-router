"""Check retained Terra diagnostic evidence against its captured inputs."""

import hashlib
import json
import unittest
from collections import Counter
from pathlib import Path


class TerraMatrixEvidenceTests(unittest.TestCase):
    def test_completed_campaign_matches_hash_bound_inputs_and_schedule(self) -> None:
        root = Path(__file__).resolve().parents[1] / "terra_matrix_smoke"
        lines = (root / "results.jsonl").read_text(encoding="utf-8").splitlines()
        manifest, *runs = (json.loads(line) for line in lines)
        for filename, key in (("run.py", "runner_sha256"), ("visual-match.png", "image_sha256")):
            with self.subTest(filename=filename):
                self.assertEqual(manifest[key], hashlib.sha256((root / filename).read_bytes()).hexdigest())
        self.assertEqual(48, len(runs))
        expected = {(case, model, effort, repetition)
                    for case in manifest["cases"] for model, effort in manifest["models"]
                    for repetition in range(1, manifest["repetitions"] + 1)}
        actual = Counter((run["case"], run["model"], run["effort"], run["repetition"])
                         for run in runs)
        self.assertEqual(expected, set(actual))
        self.assertTrue(all(count == 1 for count in actual.values()))
        self.assertTrue(all(run["complete"] and not run["tool_used"] for run in runs))


if __name__ == "__main__":
    unittest.main()
