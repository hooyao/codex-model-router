import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from evals.scripts import evalplus_hooks as hooks
from evals.scripts import evalplus_isolation as isolation
from evals.scripts import evalplus_live as live
from evals.scripts import evalplus_runner as runner
from evals.scripts import evalplus_profile as profile
from evals.tests.profile_fixture import CATALOG, provision

try:
    import tomllib
except ImportError:
    tomllib = None


class HookConfigurationTests(unittest.TestCase):
    @unittest.skipIf(tomllib is None, "TOML parser verification requires Python 3.11+")
    def test_real_candidate_rendering_and_slot_relocation(self):
        with tempfile.TemporaryDirectory(prefix="hook profile '") as temp:
            home = Path(temp) / "router"
            identity = provision(home)
            installed = Path(identity["installed_path"])
            digest = isolation.tree_hash(installed)
            config = isolation.clean_config({"provider_id": "offline", "provider": {
                "name": "offline", "wire_api": "responses", "base_url": "http://127.0.0.1:1"}})
            config += isolation.candidate_hooks_config(home, installed)
            parsed = tomllib.loads(config)
            self.assertEqual("gpt-6-astra", parsed["model"])
            self.assertEqual("xhigh", parsed["model_reasoning_effort"])
            self.assertIs(True, parsed["model_supports_reasoning_summaries"])
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
            identity["config_sha256"] = {"router": runner.sha256_file(home / "config.toml")}
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
        self.assertIn('model_provider="copilot-bridge"', args)
        provider = args[3]
        self.assertIn('http://127.0.0.1:12345', provider)
        for field in ('"request_max_retries" = 0', '"stream_max_retries" = 0',
                      '"requires_openai_auth" = false', '"supports_websockets" = false'):
            self.assertIn(field, provider)
        self.assertNotIn("auth =", provider)
        self.assertTrue(provider.startswith("model_providers.copilot-bridge="))
        self.assertIn('"wire_api" = "responses"', provider)


class HookDeliveryEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "router"
        relative = "plugins/cache/evalplus-candidate/codex-model-router/0.1.3"
        self.identity = provision(self.home)
        active = [{"enabled": True, "isManaged": False, "eventName": event, "source": "user",
                   "sourcePath": str(self.home / "config.toml"), "command": sys.executable + " " + profile.PROFILE_DIRECTORY + "/hook.py",
                   "currentHash": "sha256:" + "a" * 64, "key": event, "trustStatus": "untrusted"}
                  for event in ("sessionStart", "userPromptSubmit", "subagentStart")]
        self.registered = {"hooks": {"data": [{"hooks": active, "errors": [], "warnings": []}]}}
        messages = ["- codex-model-router:initialize-router: Init (file: fake)\n- codex-model-router:model-router: Route (file: fake)"]
        messages += [profile.build_output(self.home / profile.PROFILE_DIRECTORY, {"hook_event_name": event})[
            "hookSpecificOutput"]["additionalContext"] for event in ("SessionStart", "UserPromptSubmit")]
        self.requests = [{"path": "/responses", "body": {
            "model": "gpt-6-astra", "reasoning": {"effort": "xhigh", "summary": "auto"},
            "input": [{"role": "developer", "content": [{"text": text}]} for text in messages],
            "tools": [{"name": "multi_agent_v1", "tools": [{"name": "spawn_agent", "description": "\n".join(
                "- `" + m["slug"] + "`: Test. Reasoning efforts: low, medium, high, xhigh." for m in CATALOG["models"]), "parameters": {
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

    def test_both_arms_refuse_missing_or_substituted_raw_primary_model_effort(self):
        empty = {"hooks": {"data": [{"hooks": [], "errors": [], "warnings": []}]}}
        for arm in ("baseline", "router"):
            for replacement in ({"reasoning": None}, {"reasoning": {}}, {"reasoning": "xhigh"},
                                {"reasoning": {"effort": "high"}}, {"reasoning": {"effort": "XHIGH"}},
                                {"model": "gpt-5.6-sol"}, {"model": "gpt-6-astra-latest"}):
                invalid = copy.deepcopy(self.requests)
                invalid[0]["body"].update(replacement)
                with self.subTest(arm=arm, replacement=replacement), self.assertRaisesRegex(
                        runner.HarnessError, "must explicitly carry gpt-6-astra"):
                    hooks.validate_delivery(arm, empty if arm == "baseline" else self.registered,
                                             invalid, self.status, self.home, self.identity)
        self.assertEqual({"model": "gpt-6-astra", "reasoning_effort": "xhigh"}, self.validate()["primary_request"])

    def test_baseline_receipt_records_actual_request_model_and_effort(self):
        empty = {"hooks": {"data": [{"hooks": [], "errors": [], "warnings": []}]}}
        requests = [{"path": "/responses", "body": {
            "model": "gpt-6-astra", "reasoning": {"effort": "xhigh"}, "input": []}}]
        report = hooks.validate_delivery("baseline", empty, requests, self.status, self.home, self.identity)
        self.assertEqual({"model": "gpt-6-astra", "reasoning_effort": "xhigh"}, report["primary_request"])

    def test_captured_json_corruption_or_contract_weakening_blocks_receipt(self):
        for before, after in (('"schema_version":1', '"schema_version":'),
                              ("pure orchestrator", "business executor")):
            corrupted = copy.deepcopy(self.requests)
            text = corrupted[0]["body"]["input"][1]["content"][0]
            text["text"] = text["text"].replace(before, after)
            with self.assertRaisesRegex(runner.HarnessError, "critical context gate"):
                self.validate(requests=corrupted)

    def test_resolved_pairs_must_be_exposed_by_actual_spawn_schema(self):
        missing = copy.deepcopy(self.requests)
        missing[0]["body"]["tools"][0]["tools"][0]["description"] = "no model overrides"
        with self.assertRaisesRegex(runner.HarnessError, "resolved route unavailable"):
            self.validate(requests=missing)

    def test_worker_capture_requires_exact_override_and_routing_json(self):
        evidence = Path(self.temp.name) / "worker-evidence"
        home = evidence / "homes/worker-simulation"
        provision(home)
        (home / "config.toml").write_text("# simulation-only config", encoding="utf-8")
        source = self.registered["hooks"]["data"][0]["hooks"][2]
        registered = copy.deepcopy(self.registered)
        registered["hooks"]["data"][0]["hooks"] = [dict(source, eventName="sessionStart",
                                                       command=source["command"] + " --simulate-subagent-start")]
        runner.write_json(evidence / "router.registry.json", self.registered)
        runner.write_json(evidence / "worker-simulation.registry.json", registered)
        runner.write_json(evidence / "worker-simulation.event.json", {
            "event": "SubagentStart", "carrier": "SessionStart", "actual_worker_spawned": False,
            "source_hook_key": source["key"], "source_hook_hash": source["currentHash"],
            "config_sha256": runner.sha256_file(home / "config.toml"),
        })
        text = profile.build_output(home / profile.PROFILE_DIRECTORY, {"hook_event_name": "SubagentStart"})[
            "hookSpecificOutput"]["additionalContext"]
        requests = [{"path": "/responses", "body": {"input": [{"role": "developer", "content": [{"text": text}]}]}}]
        runner.write_json(evidence / "worker-simulation.request.json", requests)
        runner.write_json(evidence / "worker-simulation.status.json", self.status)
        self.assertTrue(hooks.validate_worker_simulation(evidence, self.identity)["exact_contract"])
        for before, after in (("You are authorized and required to perform", "You may not perform"),
                              ('"schema_version":1', '"schema_version":')):
            corrupted = copy.deepcopy(requests)
            corrupted[0]["body"]["input"][0]["content"][0]["text"] = text.replace(before, after)
            runner.write_json(evidence / "worker-simulation.request.json", corrupted)
            with self.assertRaisesRegex(runner.HarnessError, "worker critical context gate"):
                hooks.validate_worker_simulation(evidence, self.identity)

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
        names = [arm + suffix for arm in (*homes, "worker-simulation") for suffix in
                 (".registry.json", ".request.json", ".command.json", ".status.json", ".jsonl", ".stderr.txt")]
        names += ["worker-simulation.event.json"]
        for name in names:
            (evidence / name).write_text("{}", encoding="utf-8")
        receipt = {"passed": True, "model_invocations": 0, "bindings": {"candidate": "old"},
                   "reports": {"baseline": {}, "router": {}, "worker-simulation": {}},
                   "evidence_sha256": {name: runner.sha256_file(evidence / name) for name in names}}
        with mock.patch.object(isolation, "validate_homes", return_value={}), \
                mock.patch.object(hooks, "bindings", return_value={"candidate": "current"}), \
                mock.patch.object(hooks, "validate_worker_simulation", return_value={}), \
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
            missing_worker = copy.deepcopy(receipt)
            del missing_worker["evidence_sha256"]["worker-simulation.event.json"]
            runner.write_json(root / "hook-delivery.json", missing_worker)
            with self.assertRaisesRegex(runner.HarnessError, "inventory incomplete"):
                hooks.require_hook_receipt(root, homes)
            runner.write_json(root / "hook-delivery.json", receipt)
            hooks.require_hook_receipt(root, homes)
            self.assertEqual(2, validate.call_count)
            (evidence / "router.request.json").write_text('{"changed":true}')
            with self.assertRaisesRegex(runner.HarnessError, "evidence changed"):
                hooks.require_hook_receipt(root, homes)


@unittest.skipUnless(os.environ.get("EVALPLUS_OFFLINE_CLI_TEST") == "1", "opt-in local CLI test; no model runs")
class PrimaryRequestIntegrationTests(unittest.TestCase):
    def test_cli_emits_astra_xhigh_and_support_override_controls_serialization(self):
        # Run the real CLI only against the rejecting sink. A deliberately false
        # metadata override reproduces the original omission without a paid call.
        # On Windows a CLI/background scanner can briefly retain a log handle.
        # Preserve that temporary evidence if cleanup fails, not a false failure
        # of the request assertions. Python 3.9 lacks this cleanup option.
        cleanup_options = {"ignore_cleanup_errors": True} if sys.version_info >= (3, 10) else {}
        with tempfile.TemporaryDirectory(**cleanup_options) as temp:
            root = Path(temp)
            for arm in ("baseline", "router"):
                home = root / arm
                if arm == "router":
                    identity = provision(home)
                else:
                    home.mkdir()
                config = isolation.clean_config({"provider_id": "copilot-bridge", "provider": {
                    "name": "Offline", "wire_api": "responses", "base_url": "http://127.0.0.1:1",
                    "requires_openai_auth": False}})
                if arm == "router":
                    config += isolation.candidate_hooks_config(home, Path(identity["installed_path"]))
                (home / "config.toml").write_text(config, encoding="utf-8")
                task = root / (arm + "-workspace")
                task.mkdir()
                subprocess.run(["git", "init", "--quiet"], cwd=task, check=True)
                status, requests = hooks.capture_request(runner.validate_manifest(), home, task, arm, root / "positive")
                self.assertEqual(1, status["exit_code"])
                self.assertEqual(1, len(requests))
                self.assertEqual({"model": "gpt-6-astra", "reasoning_effort": "xhigh"},
                                 hooks.validate_primary_request(requests[0]["body"]))
                original = runner.build_codex_command

                def unsupported(*args):
                    command = original(*args)
                    command[-1:-1] = ["--config", "model_supports_reasoning_summaries=false"]
                    return command

                with mock.patch.object(runner, "build_codex_command", side_effect=unsupported):
                    status, requests = hooks.capture_request(runner.validate_manifest(), home, task, arm, root / "negative")
                self.assertEqual(1, status["exit_code"])
                self.assertEqual(1, len(requests))
                self.assertEqual("gpt-6-astra", requests[0]["body"]["model"])
                self.assertIsNone(requests[0]["body"].get("reasoning"))
                with self.assertRaises(runner.HarnessError):
                    hooks.validate_primary_request(requests[0]["body"])


if __name__ == "__main__":
    unittest.main()
