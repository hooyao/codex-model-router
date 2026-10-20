"""Fresh local pair paths do not rewrite the historical v20 fixture."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evals.long_horizon_v1.common import HERE, local_pair_config, manifest
from evals.long_horizon_v1.run import _canary_runtime_equivalent, observed_dispatch_stop, pilot_plan


class LocalPairConfigTests(unittest.TestCase):
    def setUp(self):
        (HERE / "_scratch").mkdir(parents=True, exist_ok=True)

    def test_local_current_cli_schema_growth_keeps_dispatch_and_hooks_checked(self):
        previous = {"protocol_contract": {"client_request_sha256": "1" * 64,
            "dispatch_contract_sha256": "2" * 64,
            "installed_hook_closure_sha256": "3" * 64,
            "interrupt_method": "turn/interrupt"}, "effective_features": {"hooks": True}}
        current = {**previous, "protocol_contract": {
            **previous["protocol_contract"], "client_request_sha256": "4" * 64}}
        plan = pilot_plan(manifest())
        with patch("evals.long_horizon_v1.run.local_pair_config", return_value=None):
            self.assertFalse(_canary_runtime_equivalent(previous, current, plan))
        with patch("evals.long_horizon_v1.run.local_pair_config", return_value={}):
            self.assertTrue(_canary_runtime_equivalent(previous, current, plan))
            for field in ("dispatch_contract_sha256", "installed_hook_closure_sha256",
                          "interrupt_method"):
                altered = {**current, "protocol_contract": {
                    **current["protocol_contract"], field: "changed"}}
                self.assertFalse(_canary_runtime_equivalent(previous, altered, plan))
            self.assertFalse(_canary_runtime_equivalent(previous,
                {**current, "effective_features": {"hooks": False}}, plan))

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

    def test_explicit_observed_budget_is_local_and_bounded(self):
        with patch.dict(os.environ):
            os.environ.pop("LONG_HORIZON_LOCAL_RUN_CONFIG", None)
            canonical = pilot_plan(manifest())
            with tempfile.TemporaryDirectory(dir=HERE / "_scratch") as directory:
                config = Path(directory) / "run.json"
                value = {"schema_version": 1, "pair_id": "fresh-budget-test",
                    "live_enabled": True,
                    "preparation_root": "_scratch/fresh-budget-test-prep",
                    "evidence_root": "_scratch/fresh-budget-test-evidence",
                    "dispatch_stop_usd": 30.0}
                config.write_text(json.dumps(value), encoding="utf-8")
                os.environ["LONG_HORIZON_LOCAL_RUN_CONFIG"] = str(config)
                plan = pilot_plan(manifest())
                self.assertEqual(plan["limits_per_arm"]["dispatch_stop_usd"], 30.0)
                self.assertEqual(plan["limits_per_arm"]["planning_envelope_usd"], 30.0)
                self.assertEqual(plan["limits_per_arm"]["wall_seconds"], 4500)
                self.assertEqual(observed_dispatch_stop(plan), 30.0)
                with self.assertRaisesRegex(ValueError, "differs from validated plan"):
                    observed_dispatch_stop(canonical)
                for bad in (0, 31, True, float("inf")):
                    with self.subTest(bad=bad):
                        value["dispatch_stop_usd"] = bad
                        config.write_text(json.dumps(value), encoding="utf-8")
                        with self.assertRaisesRegex(ValueError, "observed dispatch stop"):
                            local_pair_config()
            os.environ.pop("LONG_HORIZON_LOCAL_RUN_CONFIG")
            self.assertEqual(pilot_plan(manifest()), canonical)
            self.assertEqual(observed_dispatch_stop(canonical), 15.0)


if __name__ == "__main__":
    unittest.main()
