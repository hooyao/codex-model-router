"""Fresh local pair paths do not rewrite the historical v20 fixture."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evals.long_horizon_v1.common import HERE, local_pair_config, manifest
from evals.long_horizon_v1.run import pilot_plan


class LocalPairConfigTests(unittest.TestCase):
    def test_fresh_paths_leave_canonical_plan_unchanged(self):
        with patch.dict(os.environ):
            os.environ.pop("LONG_HORIZON_LOCAL_RUN_CONFIG", None)
            canonical = pilot_plan(manifest())
            self.assertIs(manifest()["live_enabled"], False)
            with tempfile.TemporaryDirectory(dir=HERE / "_scratch") as directory:
                config = Path(directory) / "run.json"
                config.write_text(json.dumps({
                    "schema_version": 1,
                    "pair_id": "fresh-test-case",
                    "live_enabled": True,
                    "preparation_root": "_scratch/fresh-test-case-prep",
                    "evidence_root": "_scratch/fresh-test-case-evidence",
                }), encoding="utf-8")
                os.environ["LONG_HORIZON_LOCAL_RUN_CONFIG"] = str(config)
                plan = pilot_plan(manifest())
                self.assertIs(manifest()["live_enabled"], True)
                self.assertEqual(plan["preparation_root"], "_scratch/fresh-test-case-prep")
                self.assertEqual(plan["run_outputs"]["baseline"],
                                 "_scratch/fresh-test-case-evidence/baseline-live")
                self.assertEqual(plan["end_to_end"]["receipts"]["treatment"],
                                 "_scratch/fresh-test-case-evidence/treatment-end-to-end.json")
                self.assertEqual(plan["limits_per_arm"], canonical["limits_per_arm"])
                self.assertEqual(plan["fixture"], canonical["fixture"])
            os.environ.pop("LONG_HORIZON_LOCAL_RUN_CONFIG")
            self.assertEqual(pilot_plan(manifest()), canonical)

    def test_historical_paths_rejected(self):
        with tempfile.TemporaryDirectory(dir=HERE / "_scratch") as directory:
            config = Path(directory) / "run.json"
            config.write_text(json.dumps({
                "schema_version": 1,
                "pair_id": "fresh-test-case",
                "live_enabled": True,
                "preparation_root": "_scratch/pilot-20-prep",
                "evidence_root": "_scratch/pilot-20-evidence",
            }), encoding="utf-8")
            with patch.dict(os.environ, {"LONG_HORIZON_LOCAL_RUN_CONFIG": str(config)}):
                with self.assertRaisesRegex(ValueError, "fresh paths invalid"):
                    local_pair_config()


if __name__ == "__main__":
    unittest.main()
