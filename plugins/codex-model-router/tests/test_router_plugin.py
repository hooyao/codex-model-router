from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator = load_module("router_validator", PLUGIN_ROOT / "scripts" / "validate_plugin.py")
hook = load_module("router_hook", PLUGIN_ROOT / "hooks" / "router_hook.py")


class RouterPluginTests(unittest.TestCase):
    def test_complete_package_validates_offline(self) -> None:
        self.assertEqual([], validator.validate_package(PLUGIN_ROOT))

    def test_all_fixtures_emit_valid_context(self) -> None:
        for event_name, fixture_name in validator.REQUIRED_HOOK_EVENTS.items():
            with self.subTest(event=event_name):
                self.assertEqual([], validator.run_hook_fixture(PLUGIN_ROOT, event_name, fixture_name))

    def test_controller_context_contains_policy_roles(self) -> None:
        fixture = json.loads((PLUGIN_ROOT / "tests" / "fixtures" / "session-start.json").read_text(encoding="utf-8"))
        context = hook.build_hook_output(fixture)["hookSpecificOutput"]["additionalContext"]
        for expected in ("Active controller model", "Luna", "Terra", "Sol", "Astra", "verification"):
            self.assertIn(expected, context)

    def test_prompt_context_requires_delegation_gate(self) -> None:
        fixture = json.loads((PLUGIN_ROOT / "tests" / "fixtures" / "user-prompt-submit.json").read_text(encoding="utf-8"))
        context = hook.build_hook_output(fixture)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("delegation gate", context)
        self.assertIn("overlapping write scopes", context)

    def test_worker_context_requires_structured_result(self) -> None:
        fixture = json.loads((PLUGIN_ROOT / "tests" / "fixtures" / "subagent-start.json").read_text(encoding="utf-8"))
        context = hook.build_hook_output(fixture)["hookSpecificOutput"]["additionalContext"]
        for expected in ("Do not start subagents", "Outcome:", "Evidence:", "Validation:", "Risks or blockers:"):
            self.assertIn(expected, context)

    def test_missing_manifest_reference_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            copied_root = Path(temporary_directory) / "codex-model-router"
            shutil.copytree(PLUGIN_ROOT, copied_root)
            manifest_path = copied_root / ".codex-plugin" / "plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["skills"] = "./missing-skills/"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            errors = validator.validate_package(copied_root, run_fixtures=False)
        self.assertTrue(any("manifest path 'skills' does not exist" in error for error in errors))

    def test_malformed_hook_output_is_reported(self) -> None:
        errors = validator.validate_hook_output("not json", "SessionStart", "malformed fixture")
        self.assertTrue(any("malformed fixture: invalid JSON hook output" in error for error in errors))

    def test_unsupported_hook_event_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            hook.build_hook_output({"hook_event_name": "Stop"})


if __name__ == "__main__":
    unittest.main()
