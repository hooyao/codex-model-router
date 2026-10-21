"""The v20 launch gate accepts local harness fixes without relaxing task assets."""
from __future__ import annotations

from copy import deepcopy
import unittest
from unittest.mock import patch

from evals.long_horizon_v1.common import HERE, file_sha, manifest
from evals.long_horizon_v1.run import pilot_plan


class V20RuntimeCodeTests(unittest.TestCase):
    def test_launch_accepts_python_hotfix_but_rejects_task_asset_drift(self) -> None:
        spec = manifest()
        self.assertEqual(spec["pilot"]["path"], "pilot-plan-v20.json")
        frozen = pilot_plan(spec)
        self.assertNotEqual(frozen["execution_sources_sha256"]["quality_adapter.py"],
                            file_sha(HERE / "quality_adapter.py"))
        with patch("evals.long_horizon_v1.run._standalone_source_hashes",
                   side_effect=AssertionError("source admission called")), \
             patch("evals.long_horizon_v1.run._runner_source_sha256",
                   side_effect=AssertionError("runner admission called")), \
             patch("evals.long_horizon_v1.run._verify_v20_smoke",
                   side_effect=AssertionError("smoke admission called")):
            self.assertEqual(pilot_plan(spec)["arm_order"], ["baseline", "treatment"])
            changed = deepcopy(spec)
            changed["assets"]["round0.md"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "fixture or runtime drift"):
                pilot_plan(changed)


if __name__ == "__main__":
    unittest.main()
