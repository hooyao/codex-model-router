"""Capability-based admission and isolated current-rate accounting."""

import json
import subprocess
import tempfile
try:
    import tomllib
except ImportError:
    tomllib = None
import unittest
from pathlib import Path
from unittest import mock

from evals.scripts import codex_runtime as runtime
from evals.scripts.run_paired_arm import RATES, SessionMeter, response_cost_interval


class RuntimeCapabilityTests(unittest.TestCase):
    def capability_probe(self, version, methods):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        cli = Path(temporary.name) / "codex"
        cli.write_bytes(b"fixture CLI")

        def fake_run(arguments, **_kwargs):
            if arguments[-1] == "--version":
                return subprocess.CompletedProcess(arguments, 0, stdout=version)
            destination = Path(arguments[arguments.index("--out") + 1])
            document = {"oneOf": [{"properties": {"method": {"enum": [method]}}}
                                  for method in methods]}
            (destination / "ClientRequest.json").write_text(json.dumps(document))
            return subprocess.CompletedProcess(arguments, 0)

        with mock.patch.object(runtime.subprocess, "run", side_effect=fake_run):
            return runtime.runtime_capabilities(cli)

    def test_version_is_metadata_not_a_whitelist(self):
        for version in ("codex-cli 0.1.0", "codex-cli future-build"):
            with self.subTest(version=version):
                receipt = self.capability_probe(version, runtime.REQUIRED_METHODS)
                self.assertEqual(version, receipt["version"])
                self.assertFalse(receipt["version_allowlist"])

    def test_missing_method_fails_for_capability_not_version(self):
        with self.assertRaisesRegex(ValueError, "turn/interrupt"):
            self.capability_probe("codex-cli arbitrary", runtime.REQUIRED_METHODS - {"turn/interrupt"})

    def test_equivalent_exported_schema_forms_are_supported(self):
        for keyword in ("oneOf", "anyOf", "$defs"):
            variants = [{"properties": {"method": {"const": method}}} for method in runtime.REQUIRED_METHODS]
            value = {keyword: {str(i): item for i, item in enumerate(variants)}} if keyword == "$defs" else {keyword: variants}
            self.assertEqual(runtime.REQUIRED_METHODS, runtime.exported_methods(value))

    def test_hook_trust_keeps_declaration_and_content_separate(self):
        declaration, content = "sha256:" + "a" * 64, "sha256:" + "b" * 64
        self.assertEqual({"enabled": True, "trusted_hash": declaration},
                         runtime.hook_trust_fields(declaration))
        self.assertEqual({"enabled": True, "trusted_hash": declaration, "trusted_content_hash": content},
                         runtime.hook_trust_fields(declaration + "/" + content))
        with self.assertRaises(ValueError):
            runtime.hook_trust_fields("unknown format")

    @unittest.skipIf(tomllib is None, "TOML roundtrip check requires the optional Python 3.11 parser")
    def test_private_provider_values_roundtrip_without_shell_interpolation(self):
        value = {"auth": {"command": "C:\\example path\\helper.exe", "args": ['a"b', "$value"]},
                 "timeout_ms": 3000, "enabled": True}
        self.assertEqual(value, tomllib.loads("item=" + runtime.toml_value(value))["item"])

    def test_campaign_rate_card_does_not_change_historical_defaults(self):
        self.assertNotIn("gpt-6.1-sol", RATES)
        card = {"gpt-6.1-sol": (2.0, 0.1, 2.5, 10.0)}
        usage = {"input_tokens": 1000, "cached_input_tokens": 500, "cache_write_input_tokens": 100,
                 "output_tokens": 100, "reasoning_output_tokens": 20}
        low, high = response_cost_interval(usage, "gpt-6.1-sol", card)
        self.assertAlmostEqual(0.0021, low)
        self.assertEqual(low, high)
        with tempfile.TemporaryDirectory() as directory:
            meter = SessionMeter(Path(directory), "parent", "gpt-6.1-sol", rate_card=card)
            self.assertEqual(card, meter.rate_card)
        self.assertNotIn("gpt-6.1-sol", RATES)

    def raw_usage_fixture(self, directory, *, close=True, foreign=False):
        usage = {"input_tokens": 1000, "cached_input_tokens": 500, "cache_write_input_tokens": 100,
                 "output_tokens": 100, "reasoning_output_tokens": 20, "total_tokens": 1100}
        rows = [
            {"type": "session_meta", "payload": {"id": "parent"}},
            {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn"}},
            {"type": "turn_context", "payload": {"turn_id": "turn", "model": "gpt-6.1-sol", "effort": "low"}},
            {"type": "token_usage_record", "payload": {"thread_id": "other" if foreign else "parent",
             "session_id": "parent", "turn_id": "turn", "root_turn_id": "turn", "response_id": "response-one",
             "usage": usage, "turn_token_usage": usage, "thread_token_usage": usage}},
        ]
        if close:
            rows.append({"type": "event_msg", "payload": {"type": "turn_aborted", "turn_id": "turn",
                                                           "reason": "interrupted"}})
        path = Path(directory) / "rollout-parent.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        return path, usage

    def test_interrupted_complete_native_receipt_is_priced_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path, usage = self.raw_usage_fixture(directory)
            card = {"gpt-6.1-sol": (2.0, .1, 2.5, 10.0)}
            meter = runtime.RuntimeSessionMeter(Path(directory), "parent", "gpt-6.1-sol", "low", rate_card=card)
            meter.refresh()
            self.assertEqual([], meter.integrity_issues())
            self.assertEqual(1, meter.calls)
            self.assertAlmostEqual(.0021, meter.cost)
            meter.refresh()
            self.assertEqual(1, meter.calls)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": {
                    "last_token_usage": usage, "total_token_usage": usage}}}) + "\n")
            meter.refresh()
            self.assertEqual(1, meter.calls)
            self.assertEqual([], meter.integrity_issues())

    def test_open_or_foreign_native_receipt_is_not_promoted(self):
        for variant in ("open", "foreign"):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as directory:
                self.raw_usage_fixture(directory, close=variant != "open", foreign=variant == "foreign")
                meter = runtime.RuntimeSessionMeter(Path(directory), "parent", "gpt-6.1-sol", "low",
                                                   rate_card={"gpt-6.1-sol": (2, .1, 2.5, 10)})
                meter.refresh()
                self.assertEqual(0, meter.calls)
                self.assertTrue(meter.integrity_issues())



if __name__ == "__main__":
    unittest.main()
