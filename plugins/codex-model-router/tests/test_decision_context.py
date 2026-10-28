"""Compact context is read-only and preserves the existing ownership gate."""

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "hooks"))
from describe_router import describe_context
from execution_decision import SIGNAL_FIELDS, resolve_routing_decision
from routing_config import load_config, serialized_config


class DecisionContextTests(unittest.TestCase):
    def test_description_keeps_every_validated_workspace_value(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "routing.json"
            config = load_config(PLUGIN / "defaults/default-routing.json")
            config["examples"][0]["rationale"] = "Keep this local customization."
            path.write_text(json.dumps(config), encoding="utf-8")
            before = path.read_bytes()
            result = subprocess.run([sys.executable, str(PLUGIN / "hooks/describe_router.py"),
                                     "--config", str(path)], capture_output=True, text=True, timeout=10)
            self.assertEqual(0, result.returncode, result.stderr)
            context = json.loads(result.stdout)
            self.assertEqual(config, context["config"])
            self.assertEqual(hashlib.sha256(serialized_config(config).encode()).hexdigest(), context["config_sha256"])
            self.assertEqual(before, path.read_bytes())
            self.assertEqual([path], list(Path(directory).iterdir()))

    def test_unknown_template_cannot_take_direct_path(self):
        config = load_config(PLUGIN / "defaults/default-routing.json")
        context = describe_context(Path("routing.json"), config)
        request = context["request_template"]
        self.assertFalse(context["evaluated_request"])
        self.assertNotIn("ownership", context)
        self.assertEqual(SIGNAL_FIELDS, set(request["signals"]))
        self.assertTrue(all(value is None for value in request["signals"].values()))
        self.assertEqual("DELEGATE", resolve_routing_decision(config, request)["ownership"])

    def test_bounded_request_still_uses_original_direct_predicates(self):
        config = load_config(PLUGIN / "defaults/default-routing.json")
        request = describe_context(Path("routing.json"), config)["request_template"]
        request["signals"] = {key: False for key in SIGNAL_FIELDS}
        for key in ("one_local_scope", "bounded_known_outcome", "permissions_confirmed",
                    "safety_constraints_known", "verification_plan_present", "write_scope_known"):
            request["signals"][key] = True
        self.assertEqual("DIRECT", resolve_routing_decision(config, request)["ownership"])
        for field in ("high_risk", "independent_review_required", "failure_or_recovery"):
            changed = copy.deepcopy(request)
            changed["signals"][field] = True
            self.assertEqual("DELEGATE", resolve_routing_decision(config, changed)["ownership"])

    def test_malformed_config_is_not_described_or_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "routing.json"
            path.write_text("{", encoding="utf-8")
            result = subprocess.run([sys.executable, str(PLUGIN / "hooks/describe_router.py"),
                                     "--config", str(path)], capture_output=True, text=True, timeout=10)
            self.assertEqual(2, result.returncode)
            self.assertEqual("", result.stdout)
            self.assertEqual("{", path.read_text())

    def test_description_roundtrips_unicode_on_ascii_stdout(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "routing.json"
            config = load_config(PLUGIN / "defaults/default-routing.json")
            config["examples"][0]["rationale"] = "用户自定义值"
            path.write_text(json.dumps(config), encoding="utf-8")
            environment = os.environ.copy()
            environment["PYTHONIOENCODING"] = "ascii"
            result = subprocess.run([sys.executable, str(PLUGIN / "hooks/describe_router.py"),
                                     "--config", str(path)], env=environment,
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(config, json.loads(result.stdout)["config"])


if __name__ == "__main__":
    unittest.main()
