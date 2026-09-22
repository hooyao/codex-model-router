import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from evals.scripts import evalplus_profile as profile
from evals.scripts import evalplus_isolation as isolation
from evals.scripts import evalplus_runner as runner
from evals.tests.profile_fixture import CATALOG, provision


class CompactProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "router"
        self.identity = provision(self.home)
        self.directory = self.home / profile.PROFILE_DIRECTORY

    def context(self, event):
        return profile.build_output(self.directory, {"hook_event_name": event})["hookSpecificOutput"]["additionalContext"]

    def test_all_events_keep_full_original_contract_and_exact_valid_json(self):
        _, candidate, config = profile.load_profile(self.directory)
        for event in profile.EVENTS:
            text = self.context(event)
            self.assertLessEqual(len(text.encode()), profile.SAFE_BYTES)
            report = profile.parse_context(text, self.directory, event)
            self.assertTrue(report["routing_json_valid"])
            self.assertTrue(report["exact_contract"])
            block = text.split("ROUTING_CONFIG_BEGIN\n")[1].split("\nROUTING_CONFIG_END")[0]
            self.assertEqual(config, json.loads(block))
            if event != "SubagentStart":
                self.assertIn(candidate.CONTROLLER_CONTRACT, text)
                for required in ("pure orchestrator", "dispatch bounded workers for ALL business work",
                                 "purpose-model-effort", "Unicode NFKD", "48/48/24", "128 characters",
                                 "If the discovered native spawn schema supports name", "business-result validation"):
                    self.assertIn(required, text)
            else:
                self.assertIn("WORKER ROLE OVERRIDE (SubagentStart)", text)
                self.assertIn("You are authorized and required to perform", text)
                self.assertIn("file edits, command execution, testing", text)
                self.assertIn("Do not dispatch subworkers", text)
                self.assertIn(candidate.worker_context(""), text)

    def test_exact_parser_rejects_partial_json_extra_json_and_missing_contract(self):
        text = self.context("SessionStart")
        variants = [
            text.replace('"schema_version":1', '"schema_version":1,"schema_version":1'),
            text.replace('"schema_version":1', '"schema_version":NaN'),
            text.replace('"schema_version":1', '"schema_version":2'),
            text.replace('"schema_version":1', '"schema_version":'),
            text.replace("\nROUTING_CONFIG_END", '{}\nROUTING_CONFIG_END'),
            text.replace("pure orchestrator", "business executor"),
            "Warning: truncated output\n" + text,
            text + "\nFull hook output saved to: fake",
            text.replace("ROUTING_CONFIG_BEGIN", "ROUTING_CONFIG_BEGIN\nROUTING_CONFIG_BEGIN"),
        ]
        for index, altered in enumerate(variants):
            with self.subTest(index=index), self.assertRaises(ValueError):
                profile.parse_context(altered, self.directory, "SessionStart")

    def test_byte_ceiling_fails_before_emitting_oversize_context(self):
        metadata, candidate, config = profile.load_profile(self.directory)
        # UTF-8 byte length, not character count, is the bound.
        with mock.patch.object(profile, "load_profile", return_value=(metadata, candidate, config)), \
                mock.patch.object(candidate, "additional_context", return_value="\u00e9" * 4100):
            with self.assertRaisesRegex(ValueError, "safe byte ceiling"):
                profile.build_output(self.directory, {"hook_event_name": "SessionStart"})
        with mock.patch.object(profile, "load_profile", return_value=(metadata, candidate, config)), \
                mock.patch.object(candidate, "additional_context", return_value='"' * 4500):
            with self.assertRaisesRegex(ValueError, "safe byte ceiling"):
                profile.build_output(self.directory, {"hook_event_name": "SessionStart"})

    def test_ambient_workspace_config_is_neither_read_nor_modified(self):
        workspace = self.root / "workspace"
        normal = workspace / ".codex-model-router/routing.json"
        normal.parent.mkdir(parents=True)
        normal.write_text("invalid ambient configuration", encoding="utf-8")
        original = normal.read_bytes()
        expected = self.context("UserPromptSubmit")
        output = profile.build_output(self.directory, {"hook_event_name": "UserPromptSubmit", "cwd": str(workspace)})
        self.assertEqual(expected, output["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(original, normal.read_bytes())
        self.assertEqual(self.identity["candidate_sha256"], isolation.tree_hash(Path(self.identity["installed_path"])))

    def test_resolution_uses_supported_catalog_pairs_and_is_order_independent(self):
        routes = profile.resolve_routes(CATALOG)
        shuffled = copy.deepcopy(CATALOG)
        shuffled["models"].reverse()
        self.assertEqual(routes, profile.resolve_routes(shuffled))
        self.assertNotIn("Astra", routes)
        self.assertEqual("gpt-test-luna", routes["Luna"]["model"])
        missing = copy.deepcopy(CATALOG)
        missing["models"][0]["supported_reasoning_levels"] = [{"effort": "high"}]
        with self.assertRaisesRegex(ValueError, "lacks"):
            profile.resolve_routes(missing)

    def test_subagent_event_and_cli_carrier_simulation_emit_identical_worker_context(self):
        program = self.directory / "hook.py"
        outputs = []
        for event, extra in (("SubagentStart", []), ("SessionStart", ["--simulate-subagent-start"])):
            result = subprocess.run([sys.executable, str(program)] + extra,
                                    input=json.dumps({"hook_event_name": event}), capture_output=True,
                                    text=True, encoding="utf-8", env=isolation.arm_environment(self.home), timeout=10)
            self.assertEqual(0, result.returncode, result.stderr)
            output = json.loads(result.stdout)["hookSpecificOutput"]
            self.assertEqual(event, output["hookEventName"])
            profile.parse_context(output["additionalContext"], self.directory, "SubagentStart")
            outputs.append(output["additionalContext"])
        self.assertEqual(outputs[0], outputs[1])


if __name__ == "__main__":
    unittest.main()
