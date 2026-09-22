"""MCP approval and actual native-worker tool schema regressions; no models."""
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from evals.scripts import evalplus_mcp_probe as probe
from evals.scripts import evalplus_runner as runner
from evals.scripts import evalplus_write_gate as gate
from evals.tests.profile_fixture import add_write_capability


DESCRIPTION = ("Runs raw JavaScript -- no Node, no file system, no network access. "
               "Discover nested tools in ALL_TOOLS. "
               "For example await tools.exec_command(...) is illustrative, not a declaration.")


def code_mode_body(workspace, catalog=None):
    body = add_write_capability({}, workspace)
    body["tools"] = []
    body["input"].append({"role": "developer", "type": "additional_tools", "tools": [
        {"type": "custom", "name": "exec", "description": DESCRIPTION},
        {"type": "function", "name": "wait"},
        {"type": "function", "name": "request_user_input"}]})
    if catalog is not None:
        body["input"].extend([
            {"type": "custom_tool_call", "name": "exec", "call_id": "discovery",
             "input": "text(ALL_TOOLS.filter(t => t.name.includes('write_file')));"},
            {"type": "custom_tool_call_output", "call_id": "discovery", "output": [
                {"type": "input_text", "text": "Script completed\nOutput:\n"},
                {"type": "input_text", "text": json.dumps(catalog)}]}])
    return body


class AdditionalToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_deferred_dispatcher_does_not_claim_editor_discovery(self):
        report = gate.validate_capability(code_mode_body(self.root), self.root)
        self.assertTrue(report["code_mode_dispatcher"])
        self.assertTrue(report["deferred_editor_catalog"])
        self.assertTrue(report["requires_write_artifact"])
        self.assertEqual([], report["editor_tools"])
        challenge = gate.create_challenge(self.root, "router")
        with self.assertRaisesRegex(runner.HarnessError, "write artifact missing"):
            gate.verify_artifact(challenge, self.root / "events.jsonl")

    def test_actual_partial_discovery_and_complete_catalog_are_distinguished(self):
        for names in (["write_file"], ["read_file", "write_file"]):
            catalog = [{"name": "mcp__evalplus_workspace__" + name, "description": "bounded editor"} for name in names]
            report = gate.validate_capability(code_mode_body(self.root, catalog), self.root)
            self.assertEqual(["mcp__evalplus_workspace." + name for name in names], report["editor_tools"])
            self.assertEqual(len(names) != 2, report["deferred_editor_catalog"])

    def test_structured_nested_declarations_are_parsed_but_examples_are_not(self):
        body = code_mode_body(self.root)
        dispatcher = body["input"][1]["tools"][0]
        for name in ("read_file", "write_file"):
            dispatcher["description"] += "\ndeclare const tools: { mcp__evalplus_workspace__" + name + "(args: {}): Promise<unknown>; };"
        self.assertFalse(gate.validate_capability(body, self.root)["deferred_editor_catalog"])

    def test_user_prose_and_unmatched_outputs_cannot_establish_tool_visibility(self):
        body = code_mode_body(self.root, [{"name": "mcp__evalplus_workspace__write_file"}])
        body["input"][-1]["call_id"] = "unrelated"
        self.assertEqual([], gate.validate_capability(body, self.root)["editor_tools"])
        # Copy the entire declaration into prose; it is not a runtime schema.
        body = add_write_capability({}, self.root)
        body["tools"] = []
        body["input"].append({"role": "user", "content": [{"text": json.dumps(code_mode_body(self.root))}]})
        with self.assertRaisesRegex(runner.HarnessError, "editor is absent"):
            gate.validate_capability(body, self.root)

    def test_untrusted_or_unrestricted_dispatcher_is_rejected(self):
        for mutation in ("user", "function", "missing-runtime", "missing-catalog"):
            body = code_mode_body(self.root)
            item = body["input"][1]
            if mutation == "user":
                item["role"] = "user"
            elif mutation == "function":
                item["tools"][0]["type"] = "function"
            else:
                item["tools"][0]["description"] = "ALL_TOOLS" if mutation == "missing-runtime" else DESCRIPTION.replace("ALL_TOOLS", "catalog")
            with self.subTest(mutation=mutation), self.assertRaises(runner.HarnessError):
                gate.validate_capability(body, self.root)

    def test_shell_network_and_unrelated_tools_rejected_in_every_representation(self):
        for name in ("exec_command", "shell", "write_stdin", "web_search", "mcp__unrelated__write_file"):
            for representation in ("structured", "declaration", "discovery"):
                body = code_mode_body(self.root)
                if representation == "structured":
                    body["input"][1]["tools"].append({"type": "function", "name": name})
                elif representation == "declaration":
                    body["input"][1]["tools"][0]["description"] += "\ndeclare const tools: { " + name + "(args: {}): Promise<unknown>; };"
                else:
                    body = code_mode_body(self.root, [{"name": name}])
                with self.subTest(name=name, representation=representation), self.assertRaises(runner.HarnessError):
                    gate.validate_capability(body, self.root)

    def test_flattened_direct_editor_names_are_normalized(self):
        body = add_write_capability({}, self.root)
        body["tools"] = [{"type": "function", "name": "mcp__evalplus_workspace__" + name} for name in ("read_file", "write_file")]
        report = gate.validate_capability(body, self.root)
        self.assertFalse(report["code_mode_dispatcher"])
        self.assertFalse(report["deferred_editor_catalog"])

    def test_malformed_tool_schemas_fail_closed(self):
        for tools in (None, {}, ["exec"], [{"name": 42}]):
            body = code_mode_body(self.root)
            body["input"][1]["tools"] = tools
            with self.subTest(tools=tools), self.assertRaisesRegex(runner.HarnessError, "malformed structured tool"):
                gate.validate_capability(body, self.root)
        body = code_mode_body(self.root, [])
        body["input"][-1]["output"] = "not structured output"
        with self.assertRaisesRegex(runner.HarnessError, "malformed tool discovery"):
            gate.validate_capability(body, self.root)


class FixtureReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        task = self.root / "task"
        task.mkdir()
        (task / "cli-proof.txt").write_bytes(probe.CONTENT.encode())
        names = ("cli.command.json", "cli.jsonl", "cli.status.json", "cli.stderr.txt", "outside.txt", "requests.json", "scripted-events.json")
        for name in names:
            (self.root / name).write_text("fixture", encoding="utf-8")
        self.receipt = {"passed": True, "approval": True, "code_mode": False, "model_invocations": 0,
                        "scripted_fixture": True, "inside_write_succeeded": True,
                        "outside_unchanged": True, "expected_tool_output_observed": True,
                        "delegations": 0, "fixture_sha256": runner.sha256_file(Path(probe.__file__)),
                        "inside_sha256": runner.sha256_file(task / "cli-proof.txt"),
                        "evidence_sha256": {name: runner.sha256_file(self.root / name) for name in names}}
        runner.write_json(self.root / "result.json", self.receipt)

    def test_requires_approved_current_zero_model_fixture(self):
        probe.require_fixture(self.root, False)
        for key, value in (("passed", False), ("approval", False), ("code_mode", True),
                           ("outside_unchanged", False), ("scripted_fixture", False),
                           ("model_invocations", 1), ("delegations", 1), ("fixture_sha256", "stale")):
            receipt = dict(self.receipt, **{key: value})
            runner.write_json(self.root / "result.json", receipt)
            with self.subTest(key=key), self.assertRaisesRegex(runner.HarnessError, "missing or stale"):
                probe.require_fixture(self.root, False)

    def test_requires_exact_evidence_inventory_and_unchanged_files(self):
        receipt = copy.deepcopy(self.receipt)
        del receipt["evidence_sha256"]["requests.json"]
        runner.write_json(self.root / "result.json", receipt)
        with self.assertRaisesRegex(runner.HarnessError, "inventory incomplete"):
            probe.require_fixture(self.root, False)
        runner.write_json(self.root / "result.json", self.receipt)
        (self.root / "outside.txt").write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(runner.HarnessError, "evidence changed"):
            probe.require_fixture(self.root, False)

    def test_artifact_bytes_are_rechecked_not_just_the_receipt_boolean(self):
        (self.root / "task/cli-proof.txt").write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(runner.HarnessError, "missing or stale"):
            probe.require_fixture(self.root, False)


@unittest.skipUnless(os.environ.get("EVALPLUS_OFFLINE_CLI_TEST") == "1", "opt-in local scripted CLI; no models")
class MCPApprovalIntegrationTests(unittest.TestCase):
    def run_case(self, code_mode, approval):
        cleanup = {"ignore_cleanup_errors": True} if sys.version_info >= (3, 10) else {}
        with tempfile.TemporaryDirectory(**cleanup) as temp:
            path = Path(temp) / "proof"
            result = probe.run_fixture(path, code_mode, approval)
            self.assertTrue(result["passed"], result)
            self.assertEqual(0, result["model_invocations"])
            self.assertTrue(result["outside_unchanged"])
            self.assertEqual(approval, result["inside_write_succeeded"])
            if approval:
                probe.require_fixture(path, code_mode)
                requests = runner.read_json(path / "requests.json")
                for request in requests:
                    report = gate.validate_capability(request["body"], path / "task")
                    self.assertEqual(code_mode, report["code_mode_dispatcher"])
                self.assertFalse(report["deferred_editor_catalog"])
            else:
                with self.assertRaises(runner.HarnessError):
                    probe.require_fixture(path, code_mode)

    def test_cli_scoped_approval_allows_only_inside_root(self):
        self.run_case(False, True)

    def test_cli_code_mode_scoped_approval_allows_only_inside_root(self):
        self.run_case(True, True)

    def test_cli_prompt_control_reproduces_cancellation_without_writing(self):
        self.run_case(False, False)


if __name__ == "__main__":
    unittest.main()
