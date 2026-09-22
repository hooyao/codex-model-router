"""Exact project-trust allowance, immutable seed homes, and admission failures."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from evals.scripts import evalplus_config_receipt as receipt
from evals.scripts import evalplus_isolation as isolation
from evals.scripts import evalplus_live as live
from evals.scripts import evalplus_runner as runner
from evals.tests.profile_fixture import provision


CONFIG = (b'model = "gpt-6-astra"\r\nmodel_reasoning_effort = "xhigh"\r\n'
          b'approval_policy = "never"\r\n[windows]\r\nsandbox = "unelevated"\r\n'
          b'[permissions.evalplus-task.network]\r\nenabled = false\r\n'
          b'[mcp_servers.evalplus_workspace.tools.write_file]\r\napproval_mode = "approve"\r\n'
          b'[plugins."codex-model-router@evalplus-candidate"]\r\nenabled = true\r\n'
          b'[hooks]\r\nSessionStart = []\r\n')


class ConfigReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.task = self.root / "task with spaces"
        self.home.mkdir(); self.task.mkdir()
        self.config = self.home / "config.toml"
        self.config.write_bytes(CONFIG)
        self.before = receipt.fingerprint(self.home, self.task)
        self.lf = CONFIG.replace(b"\r\n", b"\n")
        self.suffix = receipt.trust_suffixes(self.task)[0]

    def validate(self):
        return receipt.validate(self.before, self.home, self.task, runner.sha256_bytes(CONFIG))

    def test_unchanged_exact_config_is_valid(self):
        self.assertEqual("unchanged", self.validate()["mutation"])
        self.assertEqual(runner.sha256_bytes(CONFIG), self.before["config_sha256"])
        self.assertEqual(len(CONFIG), self.before["config_bytes"])

    def test_only_scoped_trust_append_and_cli_lf_normalization_are_allowed(self):
        for suffix in receipt.trust_suffixes(self.task):
            self.config.write_bytes(self.lf + suffix)
            result = self.validate()
            self.assertEqual("task-project-trust-added", result["mutation"])
            self.assertEqual(str(self.task.resolve()), result["workspace"])
        self.config.write_bytes(self.lf)  # No blanket newline/semantic bypass.
        with self.assertRaisesRegex(runner.HarnessError, "exact task-scoped"):
            self.validate()

    def test_unrelated_model_permission_mcp_plugin_hook_changes_fail(self):
        changes = [(b'gpt-6-astra', b'gpt-5.6-sol'), (b'xhigh', b'high'),
                   (b'never', b'on-request'), (b'unelevated', b'elevated'),
                   (b'enabled = false', b'enabled = true'), (b'approve', b'prompt'),
                   (b'evalplus-candidate', b'ambient'), (b'SessionStart = []', b'SessionStart = [1]')]
        for old, new in changes:
            self.config.write_bytes(self.lf.replace(old, new) + self.suffix)
            with self.subTest(field=old), self.assertRaisesRegex(runner.HarnessError, "exact task-scoped"):
                self.validate()

    def test_parent_sibling_other_arm_and_subdirectory_trust_are_rejected(self):
        for path in (self.task.parent, self.root / "router-task", self.task / "subdir", runner.ROOT):
            self.config.write_bytes(self.lf + receipt.trust_suffixes(path)[0])
            with self.subTest(path=path), self.assertRaises(runner.HarnessError):
                self.validate()

    def test_extra_project_entries_or_keys_values_and_formatting_fail(self):
        for suffix in (self.suffix * 2, self.suffix + receipt.trust_suffixes(self.root)[0],
                       self.suffix + b'enabled = true\n', self.suffix.replace(b'"trusted"', b'"untrusted"'),
                       self.suffix + b'# unrelated edit\n', self.suffix.replace(b'trust_level', b'other_key')):
            self.config.write_bytes(self.lf + suffix)
            with self.subTest(suffix=suffix), self.assertRaises(runner.HarnessError):
                self.validate()

    def test_existing_project_tables_are_not_stripped_or_ignored(self):
        prior = self.lf + receipt.trust_suffixes(self.root)[0]
        self.config.write_bytes(prior)
        before = receipt.fingerprint(self.home, self.task)
        self.config.write_bytes(prior.replace(b'"trusted"', b'"untrusted"') + self.suffix)
        with self.assertRaises(runner.HarnessError):
            receipt.validate(before, self.home, self.task, runner.sha256_bytes(prior))

    def test_baseline_scope_and_enrollment_hash_cannot_change(self):
        for key, value in (("home", str(self.root)), ("workspace", str(self.root)),
                           ("project_key", str(self.root)), ("schema_version", 2), ("config_sha256", "stale")):
            bad = dict(self.before, **{key: value})
            with self.subTest(key=key), self.assertRaisesRegex(runner.HarnessError, "binding changed"):
                receipt.validate(bad, self.home, self.task, self.before["config_sha256"])
        other = self.root / "other-task"; other.mkdir()
        with self.assertRaisesRegex(runner.HarnessError, "binding changed"):
            receipt.validate(self.before, self.home, other)

    def test_receipts_exclude_config_values_and_cannot_be_overwritten(self):
        self.config.write_bytes(b'api_key = "test-secret-not-a-real-key"\n')
        receipt.begin(self.root, "test", self.home, self.task)
        self.assertNotIn("test-secret", (self.root / "test.config-before.json").read_text())
        with self.assertRaisesRegex(runner.HarnessError, "overwrite"):
            receipt.begin(self.root, "test", self.home, self.task)

    def test_saved_evidence_and_current_config_are_both_revalidated(self):
        receipt.begin(self.root, "test", self.home, self.task)
        self.config.write_bytes(self.lf + self.suffix)
        receipt.finish(self.root, "test", self.home, self.task)
        receipt.require(self.root, "test", self.home, self.task, self.before["config_sha256"])
        saved = self.root / "test.config-after.json"
        evidence = runner.read_json(saved)
        runner.write_json(saved, dict(evidence, passed=False))
        with self.assertRaisesRegex(runner.HarnessError, "receipt changed"):
            receipt.require(self.root, "test", self.home, self.task)
        runner.write_json(saved, evidence)
        self.config.write_bytes(self.lf + self.suffix + b'extra = true\n')
        with self.assertRaisesRegex(runner.HarnessError, "exact task-scoped"):
            receipt.require(self.root, "test", self.home, self.task)
        self.assertFalse(receipt.finish(self.root, "test", self.home, self.task)["passed"])

    def test_quote_in_workspace_path_does_not_escape_the_trust_table(self):
        task = self.root / "task's folder"; task.mkdir()
        before = receipt.fingerprint(self.home, task)
        suffixes = receipt.trust_suffixes(task)
        self.assertEqual(1, len(suffixes))
        self.config.write_bytes(self.lf + suffixes[0])
        self.assertTrue(receipt.validate(before, self.home, task)["passed"])


class ExecutionHomeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.homes = {arm: self.root / arm for arm in ("baseline", "router")}
        self.homes["baseline"].mkdir()
        self.identity = provision(self.homes["router"])
        for home in self.homes.values():
            (home / "config.toml").write_bytes(CONFIG)
        self.identity["config_sha256"] = {arm: runner.sha256_bytes(CONFIG) for arm in self.homes}
        self.task = self.root / "task"; self.task.mkdir()

    def clone(self, arm, name):
        home = live.clone_slot_home(self.homes[arm], self.root / name, arm, self.identity)
        receipt.begin(self.root, name, home, self.task)
        config = home / "config.toml"
        config.write_bytes(CONFIG.replace(b"\r\n", b"\n") + receipt.trust_suffixes(self.task)[0])
        receipt.finish(self.root, name, home, self.task)
        return home

    def validate(self, arm, home):
        return isolation.validate_execution_home(self.homes[arm], home, arm, self.identity,
                                                 self.task, self.root, home.name)

    def test_both_clones_accept_trust_but_leave_seed_config_byte_identical(self):
        for arm in self.homes:
            home = self.clone(arm, arm + "-invocation")
            self.assertTrue(self.validate(arm, home)["passed"])
            self.assertEqual(CONFIG, (self.homes[arm] / "config.toml").read_bytes())

    def test_runtime_candidate_and_router_profile_mutations_fail(self):
        for relative in ("plugins/cache/evalplus-candidate/codex-model-router/0.1.3/hooks/router_hook.py",
                         "benchmark-router/routing.json"):
            home = self.clone("router", "candidate" if relative.startswith("plugins") else "profile")
            (home / relative).write_text("mutation", encoding="utf-8")
            with self.subTest(path=relative), self.assertRaises(runner.HarnessError):
                self.validate("router", home)

    def test_unrelated_plugin_and_baseline_router_contamination_fail(self):
        for arm in self.homes:
            home = self.clone(arm, arm + "-extra-plugin")
            runner.write_json(home / "plugins/cache/ambient/.codex-plugin/plugin.json", {"name": "ambient"})
            with self.assertRaises(runner.HarnessError):
                self.validate(arm, home)

    def test_legacy_runner_clones_and_gates_config_without_calling_a_model(self):
        manifest = runner.validate_manifest()
        slot = runner.build_schedule(manifest)[0]
        run_id = slot["run_id"]
        campaign = self.root / "campaign"
        task = campaign / "tasks" / run_id; task.mkdir(parents=True)
        (task / "TASK.md").write_text("offline fixture", encoding="utf-8")
        prepared = {"schedule": [slot], "state": {"attempted_run_ids": [], "completed_run_ids": [],
                                                  "reserved_token_total": 0, "reserved_cost_microusd": 0}}

        def captured(evidence, name, command, workspace, environment, prompt, timeout):
            home = Path(environment["CODEX_HOME"])
            self.assertNotEqual(self.homes["baseline"], home)
            receipt.begin(evidence, name, home, workspace)
            config = home / "config.toml"
            config.write_bytes(CONFIG.replace(b"\r\n", b"\n") + receipt.trust_suffixes(workspace)[0])
            proof = receipt.finish(evidence, name, home, workspace)
            for suffix in (".jsonl", ".stderr.txt"):
                (evidence / (name + suffix)).write_text("", encoding="utf-8")
            return {"exit_code": 0, "config_integrity": proof}

        with mock.patch.object(runner, "_load_prepared", return_value=(manifest, prepared)), \
                mock.patch.object(runner, "validate_codex_homes"), \
                mock.patch.object(isolation, "require_treatment_receipt"), \
                mock.patch.object(isolation, "require_grader_receipt"), \
                mock.patch.object(isolation, "validate_homes", return_value=self.identity), \
                mock.patch.object(runner, "preflight_plan", return_value={"selected_runs": [slot]}), \
                mock.patch.object(runner, "invoke_provider_guard", return_value={}), \
                mock.patch.object(isolation, "capture", side_effect=captured) as capture:
            result = runner.execute_live(runner.DEFAULT_MANIFEST, campaign, self.homes["baseline"], self.homes["router"], None, 1)
        self.assertEqual([run_id], result["completed_run_ids"])
        capture.assert_called_once()
        self.assertEqual(CONFIG, (self.homes["baseline"] / "config.toml").read_bytes())


if __name__ == "__main__":
    unittest.main()
