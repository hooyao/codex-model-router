import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from evals.scripts import evalplus_workspace as editor
from evals.scripts import evalplus_write_gate as gate
from evals.scripts import evalplus_isolation as isolation
from evals.scripts import evalplus_runner as runner
from evals.scripts import evalplus_scoring as scoring
from evals.tests.profile_fixture import add_write_capability, provision

try:
    import tomllib
except ImportError:
    tomllib = None


class BoundedEditorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "task"
        self.workspace.mkdir()
        self.outside = self.root / "outside.txt"
        self.outside.write_text("untouched", encoding="utf-8")
        self.editor = editor.WorkspaceEditor(self.workspace)

    def test_write_replace_and_read_are_text_operations_only(self):
        content = "raise RuntimeError('this file must never be executed')\n"
        receipt = self.editor.write("solution.py", content)
        self.assertEqual(content, self.editor.read("solution.py"))
        self.assertEqual(runner.sha256_bytes(content.encode()), receipt["sha256"])
        self.editor.write("solution.py", "replacement\n")
        self.assertEqual("replacement\n", self.editor.read("solution.py"))
        self.assertEqual(["solution.py"], [p.name for p in self.workspace.iterdir()])

    def test_traversal_absolute_source_host_and_protected_paths_are_refused(self):
        denied = ["../outside.txt", str(self.outside), str(runner.ROOT / "AGENTS.md"),
                  r"C:\Windows\escape.txt", r"\\host\share\file", "/tmp/file", "file:stream",
                  "folder/file", "folder\\file", ".git", ".codex", ".agents", "NUL.txt", "file.", "file "]
        for path in denied:
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.editor.write(path, "overwrite")
        self.assertEqual("untouched", self.outside.read_text(encoding="utf-8"))
        self.assertFalse(list(self.workspace.iterdir()))

    def test_hard_links_cannot_read_or_overwrite_outside_files(self):
        os.link(self.outside, self.workspace / "alias.txt")
        for call in (lambda: self.editor.read("alias.txt"), lambda: self.editor.write("alias.txt", "overwrite")):
            with self.assertRaisesRegex(ValueError, "hard-linked"):
                call()
        self.assertEqual("untouched", self.outside.read_text(encoding="utf-8"))

    def test_reparse_points_and_nonregular_files_fail_closed(self):
        (self.workspace / "folder").mkdir()
        with self.assertRaises(ValueError):
            self.editor.write("folder", "overwrite")
        info = mock.Mock(st_mode=0o100644, st_file_attributes=0x400, st_nlink=1)
        with mock.patch.object(Path, "lstat", return_value=info), self.assertRaisesRegex(ValueError, "reparse"):
            editor.WorkspaceEditor._plain(self.workspace / "junction")

    def test_aliased_workspace_root_is_rejected_before_command_grants(self):
        with mock.patch.object(Path, "resolve", return_value=self.root), self.assertRaisesRegex(ValueError, "canonical"):
            editor.command_overrides(self.workspace)

    def test_size_limit_rejects_without_creating_an_artifact(self):
        with self.assertRaisesRegex(ValueError, "bounded"):
            self.editor.write("huge.txt", "x" * (editor.MAX_BYTES + 1))
        self.assertFalse((self.workspace / "huge.txt").exists())

    def test_real_stdio_mcp_write_and_boundary_fixture(self):
        path = self.root / "editor-proof.json"
        proof = gate.exercise_editor(self.workspace, path)
        self.assertTrue(proof["passed"])
        self.assertEqual(11, len(proof["responses"]) - 4)
        gate.require_editor_proof(path, self.workspace)
        (self.workspace / "editor-proof.txt").write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(runner.HarnessError, "changed"):
            gate.require_editor_proof(path, self.workspace)


class WriteCapabilityTests(unittest.TestCase):
    @unittest.skipIf(tomllib is None, "TOML parsing requires Python 3.11+")
    def test_both_arms_grant_only_task_root_and_share_editor_config(self):
        with tempfile.TemporaryDirectory() as temp:
            task = Path(temp) / "task"
            task.mkdir()
            base = tomllib.loads(isolation.clean_config({"provider_id": "test", "provider": {"name": "Test", "wire_api": "responses"}}))
            self.assertNotIn("sandbox_mode", base)
            self.assertEqual("unelevated", base["windows"]["sandbox"])
            configurations = []
            for arm in ("baseline", "router"):
                command = runner.build_codex_command(runner.validate_manifest(), {"variant": arm}, task)
                self.assertNotIn("--sandbox", command)
                self.assertNotIn("--add-dir", command)
                self.assertEqual("--disable", command[command.index("shell_tool") - 1])
                pairs = [command[n + 1] for n, key in enumerate(command[:-1]) if key == "--config"]
                values = tomllib.loads("\n".join(pairs))
                permissions = values["permissions"][editor.PROFILE_NAME]
                self.assertEqual([str(task)], [k for k, v in permissions["filesystem"].items() if v == "write"])
                self.assertIs(False, permissions["network"]["enabled"])
                server = values["mcp_servers"][editor.SERVER_NAME]
                self.assertEqual(str(task), server["args"][-1])
                self.assertTrue(server["required"])
                self.assertEqual(["read_file", "write_file"], server["enabled_tools"])
                self.assertEqual({name: {"approval_mode": "approve"} for name in ("read_file", "write_file")},
                                 server["tools"])
                self.assertNotIn("default_tools_approval_mode", server)
                self.assertEqual("never", base["approval_policy"])
                configurations.append(server)
            self.assertEqual(configurations[0], configurations[1])

    def test_editor_tool_schema_has_only_bounded_text_operations(self):
        tools = editor.tools_schema()
        self.assertEqual(["read_file", "write_file"], [t["name"] for t in tools])
        for tool, required in zip(tools, (["path"], ["path", "content"])):
            schema = tool["inputSchema"]
            self.assertEqual(required, schema["required"])
            self.assertEqual(set(required), set(schema["properties"]))
            self.assertIs(False, schema["additionalProperties"])
            self.assertTrue(all(p["type"] == "string" for p in schema["properties"].values()))

    def test_captured_capability_requires_editor_no_execution_and_confined_policy(self):
        with tempfile.TemporaryDirectory() as temp:
            task = Path(temp)
            good = add_write_capability({}, task)
            self.assertEqual([str(task)], gate.validate_capability(good, task)["write_roots"])
            mutations = []
            missing = copy.deepcopy(good); missing["tools"] = []; mutations.append(missing)
            shell = copy.deepcopy(good); shell["tools"].append({"name": "exec_command"}); mutations.append(shell)
            network = copy.deepcopy(good); network["input"][0]["content"][0]["text"] = network["input"][0]["content"][0]["text"].replace("restricted", "enabled"); mutations.append(network)
            outside = copy.deepcopy(good)
            text = outside["input"][0]["content"][0]
            text["text"] = text["text"].replace("</file_system>", '<entry access="write"><path>' + str(task.parent) + '</path></entry></file_system>')
            mutations.append(outside)
            for body in mutations:
                with self.assertRaises(runner.HarnessError):
                    gate.validate_capability(body, task)


class PaidWriteGateTests(unittest.TestCase):
    def test_fresh_marker_and_native_worker_required_before_admission(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw = root / "events.jsonl"
            raw.write_text("", encoding="utf-8")
            for arm in ("baseline", "router"):
                task = root / arm; task.mkdir()
                challenge = gate.create_challenge(task, arm)
                self.assertIn("write_file", gate.write_prompt(challenge))
                with self.assertRaisesRegex(runner.HarnessError, "missing"):
                    gate.verify_artifact(challenge, raw)
                editor.WorkspaceEditor(task).write(gate.MARKER, challenge["content"])
                if arm == "router":
                    with self.assertRaisesRegex(runner.HarnessError, "native worker"):
                        gate.verify_artifact(challenge, raw)
                    raw.write_text(json.dumps({"type": "item.completed", "item": {"type": "collab_tool_call", "tool": "spawn_agent", "receiver_thread_ids": ["child"]}}) + "\n", encoding="utf-8")
                proof = gate.verify_artifact(challenge, raw)
                self.assertEqual(challenge["sha256"], proof["sha256"])
                with self.assertRaisesRegex(runner.HarnessError, "already exists"):
                    gate.create_challenge(task, arm)
                editor.WorkspaceEditor(task).write(gate.MARKER, "wrong")
                with self.assertRaisesRegex(runner.HarnessError, "match fresh"):
                    gate.verify_artifact(challenge, raw)

    def test_presence_only_paid_receipt_cannot_admit_formal_runs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runner.write_json(root / "treatment-preflight.json", {"passed": True})
            with mock.patch.object(isolation, "validate_homes", return_value={}), \
                    mock.patch("evals.scripts.evalplus_hooks.require_hook_receipt"):
                with self.assertRaisesRegex(runner.HarnessError, "write-artifact proof missing"):
                    isolation.require_treatment_receipt(root, {}, {"baseline": root / "baseline", "router": root / "router"})

    def test_paid_receipt_revalidates_both_real_artifacts_and_refuses_tampering(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            campaign = root / "campaign"
            homes = {arm: root / arm for arm in ("baseline", "router")}
            runner.write_json(root / "isolation.json", {})
            homes["baseline"].mkdir()
            identity = provision(homes["router"])
            for home in homes.values():
                (home / "config.toml").write_text('# isolated config\nmodel = "gpt-6-astra"\n', encoding="utf-8")
            identity["config_sha256"] = {arm: runner.sha256_file(home / "config.toml") for arm, home in homes.items()}

            def captured(evidence, arm, command, workspace, environment, prompt, timeout):
                home = Path(environment["CODEX_HOME"])
                self.assertNotEqual(homes[arm], home)
                isolation.config_receipt.begin(evidence, arm, home, workspace)
                config = home / "config.toml"
                config.write_bytes(config.read_bytes().replace(b"\r\n", b"\n") + isolation.config_receipt.trust_suffixes(workspace)[0])
                isolation.config_receipt.finish(evidence, arm, home, workspace)
                challenge = runner.read_json(evidence / (arm + ".write-challenge.json"))
                editor.WorkspaceEditor(workspace).write(gate.MARKER, challenge["content"])
                report = {"skills": [], "session_start_excerpt": None, "user_prompt_submit_excerpt": None,
                          "routing_config_present": False, "native_spawn_tool_name": None, "native_model_effort_selection": False}
                events = []
                if arm == "router":
                    report.update(skills=["codex-model-router:model-router"], session_start_excerpt="Active controller model: gpt-6-astra",
                                  user_prompt_submit_excerpt="CONTROLLER ROLE ONLY", routing_config_present=True,
                                  native_spawn_tool_name="spawn_agent", native_model_effort_selection=True)
                    events.append({"type": "item.completed", "item": {"type": "collab_tool_call", "tool": "spawn_agent", "receiver_thread_ids": ["child"]}})
                events.append({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(report)}})
                (evidence / (arm + ".jsonl")).write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
                return {"exit_code": 0}

            with mock.patch.object(isolation, "validate_homes", return_value=identity), \
                    mock.patch.object(isolation, "validate_runtime_flags"), \
                    mock.patch("evals.scripts.evalplus_hooks.require_hook_receipt"), \
                    mock.patch.object(isolation, "capture", side_effect=captured):
                receipt = isolation.run_preflight(runner.validate_manifest(), campaign, homes)
                self.assertTrue(receipt["passed"])
                for arm, home in homes.items():
                    self.assertEqual(identity["config_sha256"][arm], runner.sha256_file(home / "config.toml"))
                isolation.require_treatment_receipt(campaign, runner.validate_manifest(), homes)
                missing = copy.deepcopy(receipt)
                del missing["config_proofs_sha256"]
                runner.write_json(campaign / "treatment-preflight.json", missing)
                with self.assertRaisesRegex(runner.HarnessError, "invocation config proof missing"):
                    isolation.require_treatment_receipt(campaign, runner.validate_manifest(), homes)
                runner.write_json(campaign / "treatment-preflight.json", receipt)
                runtime_config = campaign / "treatment-preflight/homes/router/config.toml"
                original_config = runtime_config.read_bytes()
                runtime_config.write_bytes(original_config.replace(b'gpt-6-astra', b'gpt-5.6-sol'))
                with self.assertRaisesRegex(runner.HarnessError, "exact task-scoped"):
                    isolation.require_treatment_receipt(campaign, runner.validate_manifest(), homes)
                runtime_config.write_bytes(original_config)
                (campaign / "treatment-preflight/router-workspace" / gate.MARKER).write_text("tampered", encoding="utf-8")
                with self.assertRaisesRegex(runner.HarnessError, "match fresh"):
                    isolation.require_treatment_receipt(campaign, runner.validate_manifest(), homes)


class InfrastructureExclusionTests(unittest.TestCase):
    def test_historical_failures_do_not_count_even_if_grader_marks_them_pass(self):
        registry = json.loads(scoring.EXCLUSIONS.read_text(encoding="utf-8"))["campaigns"][0]
        rows = [{"run_id": rid, "base_pass": True, "plus_pass": True, "cost_usd": "1.00"} for rid in registry["run_ids"]]
        original = copy.deepcopy(rows)
        score = scoring.score_rows(rows, registry["campaign_state_sha256"], registry["run_ids"])
        self.assertEqual(0, score["scored_runs"])
        self.assertEqual(0, score["passed_runs"])
        self.assertIsNone(score["pass_rate"])
        self.assertEqual(original, rows)  # No ledger/cost mutation or dropped rows.
        fresh = scoring.score_rows(rows, "a" * 64, registry["run_ids"])
        self.assertEqual(4, fresh["scored_runs"])
        self.assertEqual(1.0, fresh["pass_rate"])

    def test_unstarted_slots_never_count_toward_pass_at_one(self):
        result = scoring.score_rows([{"run_id": "unstarted", "base_pass": False, "plus_pass": False}], "a" * 64, [])
        self.assertIsNone(result["pass_rate"])
        self.assertFalse(result["complete"])


if __name__ == "__main__":
    unittest.main()
