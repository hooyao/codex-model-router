import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from evals.scripts import evalplus_hooks as hooks
from evals.scripts import evalplus_isolation as isolation
from evals.scripts import evalplus_live as live
from evals.scripts import evalplus_runner as runner

try:
    import tomllib
except ImportError:
    tomllib = None


class HookConfigurationTests(unittest.TestCase):
    @unittest.skipIf(tomllib is None, "TOML parser verification requires Python 3.11+")
    def test_real_candidate_rendering_and_slot_relocation(self):
        with tempfile.TemporaryDirectory(prefix="hook profile '") as temp:
            home = Path(temp) / "router"
            installed = home / "plugins/cache/evalplus-candidate/codex-model-router/0.1.3"
            shutil.copytree(runner.ROOT / "plugins/codex-model-router", installed,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            digest = isolation.tree_hash(installed)
            config = isolation.clean_config({"provider_id": "offline", "provider": {
                "name": "offline", "wire_api": "responses", "base_url": "http://127.0.0.1:1"}})
            config += isolation.candidate_hooks_config(home, installed)
            parsed = tomllib.loads(config)
            source = runner.read_json(installed / "hooks/hooks.json")["hooks"]
            for event in ("SessionStart", "UserPromptSubmit", "SubagentStart"):
                group = parsed["hooks"][event][0]
                original = source[event][0]
                self.assertEqual(original.get("matcher"), group.get("matcher"))
                handler = group["hooks"][0]
                self.assertEqual(original["hooks"][0]["timeout"], handler["timeout"])
                self.assertEqual(original["hooks"][0]["additionalContextLimit"], handler["additionalContextLimit"])
                self.assertIn("$env:CODEX_HOME", handler["commandWindows"])
                self.assertIn("$CODEX_HOME", handler["command"])
                self.assertNotIn(str(home), handler["commandWindows"])
                self.assertNotIn("%PLUGIN_ROOT%", handler["commandWindows"])
            self.assertEqual(3, len(parsed["hooks"]["state"]))
            self.assertTrue(all(s == {"enabled": False} for s in parsed["hooks"]["state"].values()))
            (home / "config.toml").write_text(config, encoding="utf-8")
            (home / "auth.json").write_text('{"secret":"do not copy"}')
            identity = {"config_sha256": {"router": runner.sha256_file(home / "config.toml")},
                        "installed_path": str(installed), "candidate_sha256": digest}
            slot = live.clone_slot_home(home, Path(temp) / "slot", "router", identity, include_auth=False)
            self.assertFalse((slot / "auth.json").exists())
            self.assertEqual(config, (slot / "config.toml").read_text(encoding="utf-8"))
            self.assertEqual(digest, isolation.tree_hash(installed))

    @unittest.skipIf(tomllib is None, "TOML parser verification requires Python 3.11+")
    def test_recursive_toml_arrays_are_not_json_objects(self):
        value = {"SessionStart": [{"hooks": [{"type": "command", "command": 'echo "test"'}]}]}
        self.assertEqual(value, tomllib.loads("hooks=" + isolation._toml_value(value))["hooks"])

    def test_offline_provider_has_no_credentials_or_retries(self):
        args = hooks.offline_overrides(12345)
        self.assertIn('model_provider="hook-probe"', args)
        provider = args[3]
        self.assertIn('http://127.0.0.1:12345', provider)
        for field in ('"request_max_retries" = 0', '"stream_max_retries" = 0',
                      '"requires_openai_auth" = false', '"supports_websockets" = false'):
            self.assertIn(field, provider)
        self.assertNotIn("auth =", provider)


class HookDeliveryEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "router"
        relative = "plugins/cache/evalplus-candidate/codex-model-router/0.1.3"
        self.identity = {"homes": {"router": str(self.home)}, "installed_path": str(self.home / relative)}
        active = [{"enabled": True, "isManaged": False, "eventName": event, "source": "user",
                   "sourcePath": str(self.home / "config.toml"), "command": sys.executable + " " + relative + "/hooks/router_hook.py",
                   "currentHash": "sha256:" + "a" * 64, "key": event, "trustStatus": "untrusted"}
                  for event in ("sessionStart", "userPromptSubmit", "subagentStart")]
        self.registered = {"hooks": {"data": [{"hooks": active, "errors": [], "warnings": []}]}}
        messages = ["- codex-model-router:initialize-router: Init (file: fake)\n- codex-model-router:model-router: Route (file: fake)",
                    "Codex Model Router\nActive controller model: gpt-6-astra.\nCONTROLLER ROLE ONLY:\nROUTING_CONFIG_BEGIN\n{}\nROUTING_CONFIG_END",
                    "CONTROLLER ROLE ONLY:\nROUTING_CONFIG_BEGIN\n{}\nROUTING_CONFIG_END"]
        self.requests = [{"path": "/responses", "body": {
            "input": [{"role": "developer", "content": [{"text": text}]} for text in messages],
            "tools": [{"name": "multi_agent_v1", "tools": [{"name": "spawn_agent", "parameters": {
                "properties": {"model": {}, "reasoning_effort": {}}}}]}]}}]
        self.status = {"exit_code": 1, "argv": ["codex", "exec", "--dangerously-bypass-hook-trust"]}

    def validate(self, registered=None, requests=None, status=None):
        return hooks.validate_delivery("router", registered or self.registered, requests or self.requests,
                                       status or self.status, self.home, self.identity)

    def test_both_events_require_distinct_developer_messages_and_registry(self):
        self.assertEqual(["SessionStart", "UserPromptSubmit"], self.validate()["events"])
        for removed in (1, 2):
            missing = copy.deepcopy(self.requests)
            del missing[0]["body"]["input"][removed]
            with self.assertRaisesRegex(runner.HarnessError, "distinct"):
                self.validate(requests=missing)
        # A skill description, user prompt, or tool output cannot prove delivery.
        for role in ("user", "assistant", "tool"):
            spoofed = copy.deepcopy(self.requests)
            spoofed[0]["body"]["input"][1]["role"] = role
            with self.assertRaises(runner.HarnessError):
                self.validate(requests=spoofed)

    def test_registration_trust_and_clean_source_are_all_required(self):
        with self.assertRaisesRegex(runner.HarnessError, "trust"):
            self.validate(status={"exit_code": 1, "argv": ["codex", "exec"]})
        for key, value in (("enabled", False), ("source", "plugin"), ("command", "echo forged")):
            changed = copy.deepcopy(self.registered)
            changed["hooks"]["data"][0]["hooks"][0][key] = value
            with self.subTest(key=key), self.assertRaises(runner.HarnessError):
                self.validate(registered=changed)
        with self.assertRaises(runner.HarnessError):
            self.validate(requests=self.requests * 2)

    def test_baseline_rejects_router_or_ambient_skills(self):
        empty = {"hooks": {"data": [{"hooks": [], "errors": [], "warnings": []}]}}
        with self.assertRaisesRegex(runner.HarnessError, "baseline"):
            hooks.validate_delivery("baseline", empty, self.requests, self.status, self.home, self.identity)

    def test_missing_offline_receipt_blocks_paid_probe_and_both_formal_modes(self):
        root = Path(self.temp.name)
        homes = {arm: root / arm for arm in ("baseline", "router")}
        for home in homes.values():
            home.mkdir(exist_ok=True)
        with mock.patch.object(isolation, "validate_homes", return_value={}), \
                mock.patch.object(runner, "validate_codex_homes"), \
                mock.patch.object(runner, "_load_prepared", return_value=(runner.validate_manifest(), {})), \
                mock.patch.object(isolation, "capture") as capture, \
                mock.patch.object(runner, "invoke_provider_guard") as guard:
            for action in (lambda: isolation.run_preflight(runner.validate_manifest(), root, homes),
                           lambda: runner.execute_live(runner.DEFAULT_MANIFEST, root, homes["baseline"], homes["router"], None, 1),
                           lambda: live.execute_soft_campaign(runner.DEFAULT_MANIFEST, root, homes["baseline"], homes["router"], root / "pricing.json")):
                with self.assertRaisesRegex(runner.HarnessError, "hook-delivery.json"):
                    action()
            capture.assert_not_called()
            guard.assert_not_called()

    def test_receipt_rejects_stale_bindings_missing_files_and_changed_evidence(self):
        root = Path(self.temp.name)
        homes = {arm: root / arm for arm in ("baseline", "router")}
        evidence = root / "hook-delivery"
        evidence.mkdir()
        names = [arm + suffix for arm in homes for suffix in
                 (".registry.json", ".request.json", ".command.json", ".status.json", ".jsonl", ".stderr.txt")]
        for name in names:
            (evidence / name).write_text("{}", encoding="utf-8")
        receipt = {"passed": True, "model_invocations": 0, "bindings": {"candidate": "old"},
                   "reports": {"baseline": {}, "router": {}},
                   "evidence_sha256": {name: runner.sha256_file(evidence / name) for name in names}}
        with mock.patch.object(isolation, "validate_homes", return_value={}), \
                mock.patch.object(hooks, "bindings", return_value={"candidate": "current"}), \
                mock.patch.object(hooks, "validate_delivery", return_value={}) as validate:
            runner.write_json(root / "hook-delivery.json", receipt)
            with self.assertRaisesRegex(runner.HarnessError, "binding changed"):
                hooks.require_hook_receipt(root, homes)
            receipt["bindings"] = {"candidate": "current"}
            missing = copy.deepcopy(receipt)
            del missing["evidence_sha256"]["router.request.json"]
            runner.write_json(root / "hook-delivery.json", missing)
            with self.assertRaisesRegex(runner.HarnessError, "inventory incomplete"):
                hooks.require_hook_receipt(root, homes)
            runner.write_json(root / "hook-delivery.json", receipt)
            hooks.require_hook_receipt(root, homes)
            self.assertEqual(2, validate.call_count)
            (evidence / "router.request.json").write_text('{"changed":true}')
            with self.assertRaisesRegex(runner.HarnessError, "evidence changed"):
                hooks.require_hook_receipt(root, homes)


if __name__ == "__main__":
    unittest.main()
