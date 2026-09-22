import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from evals.scripts import evalplus_isolation as isolation
from evals.scripts import evalplus_runner as runner
from evals.scripts import evalplus_hooks as hooks


class ArmIsolationTests(unittest.TestCase):
    def setUp(self):
        self.manifest = runner.validate_manifest()
        self.transport = {"provider_id": "test-provider", "provider": {
            "name": "Test", "base_url": "http://localhost:8000", "wire_api": "responses",
        }}

    def test_environment_isolates_implicit_user_discovery_and_desktop_context(self):
        inherited = {"CODEX_HOME": "ambient", "CODEX_SQLITE_HOME": "ambient-db",
                     "CODEX_THREAD_ID": "desktop", "CODEX_APP_TOOLS_PIPE_PATH": "pipe",
                     "HOME": "personal", "USERPROFILE": "personal", "PATH": "python",
                     "APPDATA": "personal-roaming", "LOCALAPPDATA": "personal-local",
                     "PYTHONPATH": "ambient-modules", "RUST_LOG": "trace"}
        with tempfile.TemporaryDirectory() as temp:
            baseline = isolation.arm_environment(Path(temp) / "baseline", inherited)
            routed = isolation.arm_environment(Path(temp) / "router", inherited)
        self.assertTrue(routed["PATH"].endswith(os.pathsep + "python"))
        for key in ("CODEX_THREAD_ID", "CODEX_APP_TOOLS_PIPE_PATH", "PYTHONPATH", "RUST_LOG"):
            self.assertNotIn(key, routed)
        for key in ("CODEX_HOME", "CODEX_SQLITE_HOME", "HOME", "USERPROFILE", "XDG_CONFIG_HOME", "APPDATA", "LOCALAPPDATA"):
            self.assertNotEqual(baseline[key], routed[key])
            self.assertNotIn(routed[key], inherited.values())
        self.assertEqual("ambient", inherited["CODEX_HOME"])

    def test_config_only_imports_transport_and_disables_memory_and_retries(self):
        config = isolation.clean_config(self.transport)
        for line in ("use_memories = false", "generate_memories = false",
                     "request_max_retries = 0", "stream_max_retries = 0",
                     '[skills.bundled]\nenabled = false', 'max_threads = 2'):
            self.assertIn(line, config)
        self.assertNotIn("skip_host_skill_discovery", config)  # absent in CLI 0.144.1
        for key in ("plugins", "skills", "hooks", "developer_instructions", "memories"):
            bad = copy.deepcopy(self.transport)
            bad[key] = {}
            with self.assertRaises(runner.HarnessError):
                isolation.clean_config(bad)
            bad = copy.deepcopy(self.transport)
            bad["provider"][key] = {}
            with self.assertRaises(runner.HarnessError):
                isolation.clean_config(bad)

    def test_setup_installs_exact_candidate_only_in_router_home(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            candidate = root / "source/codex-model-router"
            shutil.copytree(runner.ROOT / "plugins/codex-model-router", candidate,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            transport = root / "transport.json"
            runner.write_json(transport, self.transport)
            calls = []

            def fake_capture(evidence, name, command, cwd, env):
                calls.append((name, command, env))
                if name == "plugin-add":
                    package = root / "state/router/plugins/cache/evalplus-candidate/codex-model-router/0.1.3"
                    shutil.copytree(root / "state/candidate-marketplace/plugins/codex-model-router", package)
                    runner.write_json(evidence / "plugin-add.jsonl", {"installedPath": str(package)})
                return {"exit_code": 0}

            with mock.patch.object(isolation, "capture", side_effect=fake_capture):
                record = isolation.prepare_homes(root / "state", candidate, transport)
            self.assertEqual(["marketplace-add", "plugin-add"], [call[0] for call in calls])
            self.assertTrue(all(call[2]["CODEX_HOME"].endswith("router") for call in calls))
            self.assertFalse((root / "state/baseline/plugins").exists())
            self.assertEqual(isolation.tree_hash(candidate), record["candidate_sha256"])
            homes = {arm: Path(path) for arm, path in record["homes"].items()}
            isolation.validate_homes(homes)
            (homes["router"] / "config.toml").write_text("# contamination")
            with self.assertRaisesRegex(runner.HarnessError, "config changed"):
                isolation.validate_homes(homes)

    def test_unknown_runtime_flag_fails_before_a_model_call(self):
        result = subprocess.CompletedProcess([], 0, stdout="hooks stable true\n", stderr="")
        with mock.patch.object(isolation.subprocess, "run", return_value=result) as run:
            with self.assertRaisesRegex(runner.HarnessError, "lacks flags"):
                isolation.validate_runtime_flags(self.manifest)
        self.assertEqual(["codex", "features", "list"], run.call_args.args[0])

    def test_no_third_party_or_bundled_package_allowed_in_either_arm(self):
        router = {"installed": [{"name": "codex-model-router", "version": "0.1.3", "enabled": True}]}
        for marketplace in ("personal", "openai-bundled", "openai-primary-runtime"):
            other = {"name": "unrelated", "marketplaceName": marketplace, "enabled": False}
            with self.assertRaises(runner.HarnessError):
                runner.validate_plugin_inventories(self.manifest, {"installed": [other]}, router)
            with self.assertRaises(runner.HarnessError):
                runner.validate_plugin_inventories(self.manifest, {"installed": []}, {"installed": router["installed"] + [other]})


class TreatmentEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.baseline = {"skills": [], "session_start_excerpt": None, "user_prompt_submit_excerpt": None,
                         "routing_config_present": False, "native_spawn_tool_name": None,
                         "native_model_effort_selection": False}
        self.router = {"skills": ["codex-model-router:initialize-router", "codex-model-router:model-router"],
                       "session_start_excerpt": "Codex Model Router\nActive controller model: gpt-6-astra.",
                       "user_prompt_submit_excerpt": "CONTROLLER ROLE ONLY: SessionStart/UserPromptSubmit policy",
                       "routing_config_present": True, "native_spawn_tool_name": "multi_agent_v1.spawn_agent",
                       "native_model_effort_selection": True}

    def test_only_router_observes_skill_and_both_hook_events(self):
        isolation.validate_probe_report("baseline", self.baseline)
        isolation.validate_probe_report("router", self.router)
        with self.assertRaises(runner.HarnessError):
            isolation.validate_probe_report("baseline", self.router)
        for key in ("skills", "session_start_excerpt", "user_prompt_submit_excerpt", "routing_config_present",
                    "native_model_effort_selection"):
            missing = copy.deepcopy(self.router)
            missing[key] = self.baseline[key]
            with self.subTest(key=key), self.assertRaises(runner.HarnessError):
                isolation.validate_probe_report("router", missing)

    def test_plugin_list_and_agent_prompt_are_not_treatment_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            raw = Path(temp) / "raw.jsonl"
            raw.write_text(json.dumps({"type": "thread.started", "thread_id": "one"}) + "\n")
            with self.assertRaises(runner.HarnessError):
                isolation.model_messages(raw)

    def test_failed_preflight_runs_each_arm_once_and_preserves_denial(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            homes = {arm: root / arm for arm in ("baseline", "router")}
            runner.write_json(root / "isolation.json", {})

            def failed_capture(evidence, arm, *args):
                (evidence / (arm + ".jsonl")).write_text("")
                return {"exit_code": 1}

            with mock.patch.object(isolation, "validate_homes", return_value={"candidate_sha256": "a" * 64}), \
                    mock.patch.object(hooks, "require_hook_receipt"), \
                    mock.patch.object(isolation, "validate_runtime_flags"), \
                    mock.patch.object(isolation, "capture", side_effect=failed_capture) as capture, \
                    mock.patch.object(isolation.subprocess, "run"):
                receipt = isolation.run_preflight(runner.validate_manifest(), root / "campaign", homes)
                self.assertFalse(receipt["passed"])
                self.assertEqual(2, capture.call_count)
                self.assertTrue((root / "campaign/treatment-preflight.json").exists())
                with self.assertRaises(FileExistsError):
                    isolation.run_preflight(runner.validate_manifest(), root / "campaign", homes)
                self.assertEqual(2, capture.call_count)

    def test_missing_grader_evidence_blocks_generation(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(runner.HarnessError):
                isolation.require_grader_receipt(Path(temp))

    def test_live_admission_cannot_start_a_slot_without_both_receipts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for arm in ("baseline", "router"):
                (root / arm).mkdir()
            for failed_gate in ("require_treatment_receipt", "require_grader_receipt"):
                with self.subTest(failed_gate=failed_gate), \
                        mock.patch.object(runner, "_load_prepared", return_value=(runner.validate_manifest(), {})), \
                        mock.patch.object(runner, "validate_codex_homes"), \
                        mock.patch.object(isolation, "require_treatment_receipt"), \
                        mock.patch.object(isolation, "require_grader_receipt"), \
                        mock.patch.object(isolation, failed_gate, side_effect=runner.HarnessError("gate failed")), \
                        mock.patch.object(runner, "invoke_provider_guard") as guard, \
                        mock.patch.object(runner.subprocess, "run") as execute:
                    with self.assertRaisesRegex(runner.HarnessError, "gate failed"):
                        runner.execute_live(runner.DEFAULT_MANIFEST, root / "campaign",
                                            root / "baseline", root / "router", None, 24)
                    guard.assert_not_called()
                    execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
