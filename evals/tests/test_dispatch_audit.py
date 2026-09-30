"""Dispatch audit must not turn incomplete or agent-authored evidence into proof."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from evals.scripts.dispatch_audit_reconcile import native_attempts, read_audit, reconcile
from evals.scripts import activation_preflight


ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "plugins" / "codex-model-router" / "hooks" / "dispatch_audit.py"


def event(kind: str, attempt: str = "call-1", child: str | None = "child-1") -> dict:
    common = {"hook_event_name": kind, "tool_name": "Agent", "session_id": "parent-1",
              "turn_id": "turn-1", "tool_use_id": attempt}
    if kind == "PreToolUse":
        common["tool_input"] = {"task_name": "kernel_gpt_6_astra_xhigh",
                                "model": "gpt-6-astra", "reasoning_effort": "xhigh",
                                "fork_turns": "none", "message": "secret packet text"}
    else:
        common["tool_response"] = {"child_thread_id": child} if child else {}
    return common


def hook_output(payload: dict) -> str:
    result = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                            capture_output=True, text=True,
                            env={**os.environ, "CODEX_MODEL_ROUTER_DISPATCH_AUDIT": "1"})
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def role_contract(root: Path) -> Path:
    role_file = ROOT / "evals" / "paired_selective" / "astra-hard-kernel-role.toml"
    binding = root / "binding.toml"
    binding.write_text('[agents.default]\ndescription = "Benchmark hard-kernel worker."\n'
                       f'config_file = "{role_file.as_posix()}"\n', encoding="utf-8")
    binary = root / "codex.exe"
    binary.write_bytes(b"synthetic-cli")
    runtime = root / "runtime.json"
    runtime.write_text(json.dumps({"schema_version": 1, "kind": "role_runtime",
        "cli_version": "codex-cli 0.144.1", "tool": "collaborationspawn_agent",
        "default_role": "default",
        "role_file_precedence": "role_file_over_explicit_spawn_and_agents_defaults",
        "agent_type_omitted_uses_default": True}), encoding="utf-8")
    schema = root / "schema.json"
    schema.write_text(json.dumps({"schema_version": 1, "kind": "spawn_schema",
        "tool": "collaborationspawn_agent",
        "supported_arguments": ["task_name", "message", "fork_turns"]}), encoding="utf-8")
    review = root / "review.json"
    review.write_text('{"synthetic_test_only":true}', encoding="utf-8")
    binary_hash = hashlib.sha256(binary.read_bytes()).hexdigest()
    calibration = root / "calibration.json"
    calibration.write_text(json.dumps({"schema_version": 1, "kind": "role_calibration",
        "cli_version": "codex-cli 0.144.1", "tool": "collaborationspawn_agent",
        "role_name": "default", "binding_sha256": hashlib.sha256(binding.read_bytes()).hexdigest(),
        "role_file_sha256": hashlib.sha256(role_file.read_bytes()).hexdigest(),
        "binary_sha256": binary_hash,
        "spawn_arguments": {"fork_turns": "none", "selector_fields": [], "agent_type": None},
        "child": {"model": "gpt-6-astra", "reasoning_effort": "xhigh"},
        "review": {"status": "passed", "reviewer": "synthetic-test-reviewer",
                   "source": str(review), "sha256": hashlib.sha256(review.read_bytes()).hexdigest()}}),
        encoding="utf-8")
    paths = {"schema": ("spawn_schema", schema), "binding": ("role_binding", binding),
             "role": ("role_file", role_file), "runtime": ("role_runtime", runtime),
             "calibration": ("role_calibration", calibration)}
    evidence = [{"id": key, "kind": kind, "source": str(path),
                 "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                 "captured_at": "2026-09-24T00:00:00Z"}
                for key, (kind, path) in paths.items()]
    canonical = "kernel-gpt-6-astra-xhigh"
    contract = {"schema_version": 1, "dispatch_id": "kernel", "purpose": "kernel",
        "canonical_name": canonical,
        "packet": {"worker_name": canonical, "task_id": canonical,
                   "native_task_name": "kernel_gpt_6_astra_xhigh"},
        "native_dispatch": {"tool": "collaborationspawn_agent", "naming_field": "task_name",
            "native_name": "kernel_gpt_6_astra_xhigh", "schema_evidence_ref": "schema",
            "planned_arguments": {"fork_turns": "none", "model": None,
                                  "reasoning_effort": None, "agent_type": None,
                                  "message_sha256": hashlib.sha256(b"secret packet text").hexdigest(),
                                  "message_bytes": len(b"secret packet text")}},
        "selection": {"mode": "verified_role_config", "model": "gpt-6-astra",
            "reasoning_effort": "xhigh", "evidence_refs": ["binding", "role", "runtime", "calibration"],
            "role": {"name": "default", "binding_ref": "binding", "file_ref": "role",
                     "runtime_ref": "runtime", "calibration_ref": "calibration",
                     "binary_path": str(binary), "binary_sha256": binary_hash}},
        "capability_evidence": evidence}
    path = root / "dispatch-contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    return path


class DispatchAuditTests(unittest.TestCase):
    def test_observed_fixture_provenance_hashes_named_receipt(self) -> None:
        fixture = json.loads((Path(__file__).parent / "fixtures" /
                              "v2-dispatch-observed.json").read_text(encoding="utf-8"))
        source = ROOT / fixture["provenance"]["source_receipt"]
        if not source.is_file():
            self.skipTest("optional live dispatch receipt is unavailable")
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),
                         fixture["provenance"]["source_sha256"])

    def test_observed_v2_result_path_binds_explicit_child_without_hook_result(self) -> None:
        fixture = json.loads((Path(__file__).parent / "fixtures" /
                              "v2-dispatch-observed.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def report(value: dict) -> dict:
                parent = root / "parent.jsonl"
                audit = root / "audit.jsonl"
                parent.write_text("".join(json.dumps(row) + "\n" for row in value["native"]),
                                  encoding="utf-8")
                audit.write_text("".join(json.dumps({"hookSpecificOutput": {
                    "hookEventName": "PreToolUse" if row["kind"] == "pre" else "PostToolUse",
                    "additionalContext": "CODEX_DISPATCH_AUDIT_V1 " + json.dumps(row)}}) + "\n"
                    for row in value["audit"]), encoding="utf-8")
                return reconcile(audit, parent, value["parent_id"], value["children"])

            result = report(fixture)
            self.assertEqual("structurally-matched", result["status"], result["issues"])
            self.assertEqual("UNKNOWN", result["provenance"])
            self.assertEqual("native_sub_agent_activity", result["attempts"][0]["child_id_source"])
            self.assertEqual("native_result_path_and_activity", result["attempts"][0]["result_source"])

            for mutation in ("missing_result", "missing_started", "wrong_path", "error_result",
                             "wrong_child", "wrong_pre_digest", "wrong_post_digest",
                             "wrong_parent_thread", "wrong_parent_turn", "wrong_namespace",
                             "unknown_completion", "wrong_completion_path",
                             "wrong_completion_parent", "wrong_completion_turn",
                             "reused_start_id", "duplicate_completion", "unsupported_activity",
                             "later_turn_start", "later_turn_completion",
                             "later_parent_start", "later_parent_completion",
                             "wrong_start_item_parent", "wrong_completion_item_turn"):
                with self.subTest(mutation=mutation):
                    value = json.loads(json.dumps(fixture))
                    output = next(row["payload"] for row in value["native"]
                                  if row["payload"].get("type") == "function_call_output")
                    if mutation == "missing_result":
                        value["native"] = [row for row in value["native"]
                                           if row["payload"] is not output]
                    elif mutation == "missing_started":
                        value["native"] = [row for row in value["native"] if
                            row["payload"].get("item", {}).get("kind") != "started"]
                    elif mutation == "wrong_path":
                        output["output"] = json.dumps({"task_name": "/root/wrong"})
                    elif mutation == "error_result":
                        output["output"] = json.dumps(json.loads(output["output"]) | {"error": "failed"})
                    elif mutation == "wrong_child":
                        value["children"][0]["id"] = "other-child"
                    elif mutation == "wrong_pre_digest":
                        value["audit"][0]["message_sha256"] = "0" * 64
                    elif mutation == "wrong_post_digest":
                        value["audit"][1]["post_observed_input"]["message_sha256"] = "0" * 64
                    elif mutation == "wrong_parent_thread":
                        value["native"][3]["payload"]["thread_id"] = "unrelated-parent"
                    elif mutation == "wrong_parent_turn":
                        value["native"][3]["payload"]["turn_id"] = "unrelated-turn"
                    elif mutation == "wrong_namespace":
                        value["native"][2]["payload"]["namespace"] = "unrelated"
                    elif mutation == "unknown_completion":
                        value["native"][5]["payload"]["item"]["agent_thread_id"] = "unknown-child"
                    elif mutation == "wrong_completion_path":
                        value["native"][5]["payload"]["item"]["agent_path"] = "/root/wrong"
                    elif mutation == "wrong_completion_parent":
                        value["native"][5]["payload"]["thread_id"] = "unrelated-parent"
                    elif mutation == "wrong_completion_turn":
                        value["native"][5]["payload"]["turn_id"] = "unrelated-turn"
                    elif mutation == "reused_start_id":
                        value["native"][5]["payload"]["item"]["id"] = \
                            value["audit"][0]["attempt_id"]
                    elif mutation == "duplicate_completion":
                        duplicate = json.loads(json.dumps(value["native"][5]))
                        duplicate["payload"]["item"]["id"] = "another-completion"
                        value["native"].append(duplicate)
                    elif mutation in ("later_turn_start", "later_turn_completion"):
                        index = 3 if mutation.endswith("start") else 5
                        value["native"].insert(index, {"type": "turn_context",
                            "payload": {"turn_id": "later-turn"}})
                        value["native"][index + 1]["payload"]["turn_id"] = "later-turn"
                    elif mutation in ("later_parent_start", "later_parent_completion"):
                        index = 3 if mutation.endswith("start") else 5
                        value["native"].insert(index, {"type": "session_meta",
                            "payload": {"id": "later-parent"}})
                        value["native"][index + 1]["payload"]["thread_id"] = "later-parent"
                    elif mutation == "wrong_start_item_parent":
                        value["native"][3]["payload"]["item"]["thread_id"] = "unrelated-parent"
                    elif mutation == "wrong_completion_item_turn":
                        value["native"][5]["payload"]["item"]["turn_id"] = "unrelated-turn"
                    else:
                        value["native"][5]["payload"]["item"]["kind"] = "unknown"
                    self.assertEqual("UNKNOWN", report(value)["status"])

    def test_nested_v2_activity_links_spawn_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "parent.jsonl"
            events = [
                {"type": "session_meta", "payload": {"id": "parent-1"}},
                {"type": "turn_context", "payload": {"turn_id": "turn-1"}},
                {"type": "response_item", "payload": {"type": "function_call",
                    "name": "spawn_agent", "call_id": "call-1", "arguments": json.dumps({
                        "task_name": "kernel_gpt_6_astra_xhigh", "model": "gpt-6-astra",
                        "reasoning_effort": "xhigh", "fork_turns": "none", "message": "opaque"})}},
                {"type": "event_msg", "payload": {"type": "item_completed", "item": {
                    "type": "SubAgentActivity", "kind": "started", "id": "call-1",
                    "agent_thread_id": "child-1",
                    "agent_path": "/root/kernel_gpt_6_astra_xhigh"}}},
                {"type": "response_item", "payload": {"type": "function_call_output",
                    "call_id": "call-1", "output": json.dumps({
                        "task_name": "/root/kernel_gpt_6_astra_xhigh"})}},
            ]
            path.write_text("".join(json.dumps(item) + "\n" for item in events), encoding="utf-8")
            calls, followups = native_attempts(path)
            self.assertFalse(followups)
            self.assertTrue(calls[0]["activity_observed"])
            self.assertEqual("child-1", calls[0]["child_id"])

    def test_activation_preflight_accepts_only_event_specific_hook_commands(self) -> None:
        config_path = ROOT / "plugins" / "codex-model-router" / "hooks" / "hooks.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hooks.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with mock.patch.object(activation_preflight, "HOOK_CONFIG", path):
                self.assertIn("router_hook.py", activation_preflight.configured_command("win32"))
                self.assertIn("router_hook.py", activation_preflight.configured_command("linux"))
                config["hooks"]["PreToolUse"][0]["hooks"][0]["commandWindows"] = \
                    config["hooks"]["SessionStart"][0]["hooks"][0]["commandWindows"]
                path.write_text(json.dumps(config), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "hook commands differ"):
                    activation_preflight.configured_command("win32")
                config["hooks"]["PreToolUse"][0]["hooks"][0]["commandWindows"] = \
                    json.loads(config_path.read_text(encoding="utf-8"))["hooks"]["PreToolUse"][0]["hooks"][0]["commandWindows"]
                del config["hooks"]["SubagentStart"]
                path.write_text(json.dumps(config), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "hook commands differ"):
                    activation_preflight.configured_command("win32")

    def test_hook_emits_bounded_digest_without_plaintext_or_paths(self) -> None:
        output = hook_output(event("PreToolUse"))
        self.assertNotIn("secret packet text", output)
        self.assertNotIn("tool_input", output)
        self.assertNotIn("path", output)
        record = json.loads(json.loads(output)["hookSpecificOutput"]["additionalContext"].split(" ", 1)[1])
        self.assertEqual(hashlib.sha256(b"secret packet text").hexdigest(), record["message_sha256"])
        self.assertEqual(18, record["message_bytes"])

    def test_v2_spawn_name_is_allowlisted_but_other_tools_are_ignored(self) -> None:
        v2 = event("PreToolUse")
        v2["tool_name"] = "collaborationspawn_agent"
        self.assertIn("CODEX_DISPATCH_AUDIT_V1", hook_output(v2))
        for unrelated in ("collaborationfollowup_task", "foo_spawn_agent", "Bash"):
            v2["tool_name"] = unrelated
            output = hook_output(v2)
            self.assertEqual("{}", output)
            self.assertNotIn("secret packet text", output)

    def test_malformed_duplicate_and_oversized_hook_input_do_not_leak(self) -> None:
        for raw in ('{"hook_event_name":"PreToolUse","hook_event_name":"PreToolUse"}',
                    json.dumps(event("PreToolUse"))[:-1],
                    json.dumps(event("PreToolUse") | {"padding": "private-secret" * 20_000}),
                    "[" * 1_200 + "0" + "]" * 1_200):
            result = subprocess.run([sys.executable, str(HOOK)], input=raw,
                                    capture_output=True, text=True,
                                    env={**os.environ, "CODEX_MODEL_ROUTER_DISPATCH_AUDIT": "1"})
            self.assertNotEqual(0, result.returncode)
            self.assertNotIn("private-secret", result.stdout + result.stderr)
            self.assertNotIn("secret packet text", result.stdout + result.stderr)

    def test_attempt_order_identity_and_selector_failures_remain_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent = root / "parent.jsonl"
            parent_events = [
                {"type": "session_meta", "payload": {"id": "parent-1"}},
                {"type": "turn_context", "payload": {"turn_id": "turn-1",
                                                      "model": "gpt-6-sol", "effort": "low"}},
                {"type": "response_item", "payload": {
                    "type": "function_call", "name": "spawn_agent", "call_id": "call-1",
                    "arguments": json.dumps(event("PreToolUse")["tool_input"])}},
                {"type": "response_item", "payload": {"type": "function_call_output",
                                                    "call_id": "call-1",
                                                    "output": json.dumps({"child_thread_id": "child-1"})}},
            ]

            def write_parent(events: list[dict]) -> None:
                parent.write_text("".join(json.dumps(item) + "\n" for item in events),
                                  encoding="utf-8")

            write_parent(parent_events)
            audit = root / "audit.jsonl"
            children = [{"id": "child-1", "parent_id": "parent-1",
                         "agent_path": "/root/kernel_gpt_6_astra_xhigh",
                         "turns": [{"model": "gpt-6-astra", "effort": "xhigh"}]}]

            def report(lines: list[str]) -> dict:
                audit.write_text("\n".join(lines) + "\n", encoding="utf-8")
                return reconcile(audit, parent, "parent-1", children)

            before, after = hook_output(event("PreToolUse")), hook_output(event("PostToolUse"))
            def with_record(line: str, **changes) -> str:
                envelope = json.loads(line)
                context = envelope["hookSpecificOutput"]["additionalContext"]
                marker, value = context.split(" ", 1)
                record = json.loads(value)
                record.update(changes)
                envelope["hookSpecificOutput"]["additionalContext"] = marker + " " + json.dumps(record)
                return json.dumps(envelope)

            self.assertEqual("structurally-matched", report([before, after])["status"])
            self.assertEqual("UNKNOWN", report([before, after])["provenance"])
            self.assertEqual({"child_id": "child-1", "child_id_source": "hook_post"},
                             {key: report([before, after])["attempts"][0][key]
                              for key in ("child_id", "child_id_source")})
            self.assertIn("post attempt precedes pre attempt", report([after, before])["issues"])
            self.assertIn("duplicate audit attempt ID", report([before, before, after])["issues"])
            self.assertIn("post attempt lacks observed child identity",
                          report([before, hook_output(event("PostToolUse", child=None))])["issues"])
            self.assertIn("post attempt lacks observed result",
                          report([before, with_record(after, result_observed=False)])["issues"])
            self.assertIn("pre/post turn differs from native parent context",
                          report([with_record(before, turn_id="turn-2"), after])["issues"])
            parent_events[1]["payload"].pop("turn_id")
            write_parent(parent_events)
            self.assertIn("pre/post turn differs from native parent context",
                          report([before, after])["issues"])
            parent_events[1]["payload"]["turn_id"] = "turn-1"
            parent_events[3]["payload"]["output"] = json.dumps({"child_thread_id": "child-2"})
            write_parent(parent_events)
            self.assertIn("post child identity differs from native result",
                          report([before, after])["issues"])
            parent_events[3]["payload"]["output"] = json.dumps({"task_name": "/root/kernel_gpt_6_astra_xhigh"})
            write_parent(parent_events)
            self.assertIn("native spawn result lacks child identity", report([before, after])["issues"])
            parent_events[3]["payload"]["output"] = json.dumps({"child_thread_id": "child-1"})
            write_parent(parent_events)
            children[0]["turns"].append({"model": "gpt-6-sol", "effort": "low"})
            self.assertIn("child turn model/effort missing or switched",
                          report([before, after])["issues"])
            children[0]["turns"].pop()
            children[0]["turns"].append({"model": "gpt-6-astra", "effort": "xhigh"})
            self.assertIn("child follow-up turn lacks attempt audit",
                          report([before, after])["issues"])
            children[0]["turns"].pop()
            parent_events[2]["payload"]["call_id"] = "call-2"
            write_parent(parent_events)
            self.assertIn("native attempt lacks matching pre/post event",
                          report([before, after])["issues"])
            parent_events[2]["payload"]["call_id"] = "call-1"
            parent_events.append({"type": "response_item", "payload": {
                "type": "custom_tool_call", "name": "followup_task"}})
            write_parent(parent_events)
            self.assertIn("follow-up or retry operation lacks attempt audit",
                          report([before, after])["issues"])
            parent_events.pop()
            parent_events[2]["payload"]["turn_id"] = "turn-2"
            write_parent(parent_events)
            self.assertEqual("UNKNOWN", report([before, after])["status"])
            del parent_events[2]["payload"]["turn_id"]
            write_parent(parent_events)
            self.assertEqual("UNKNOWN", reconcile(audit, parent, "parent-1",
                                                   [{"id": "child-1", "agent_path": 7,
                                                     "turns": []}])["status"])

            for malformed in (["not-an-object"], [{"type": "response_item", "payload": []}]):
                write_parent(malformed)
                self.assertEqual("UNKNOWN", report([before, after])["status"])

    def test_audit_source_rejects_duplicate_keys_extra_fields_and_oversize(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            valid = hook_output(event("PreToolUse"))
            path.write_text(valid + "\n", encoding="utf-8")
            self.assertEqual(1, len(read_audit(path)))
            path.write_text('{"hookSpecificOutput":{},"hookSpecificOutput":{}}\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                read_audit(path)
            path.write_bytes(b"x" * 2_097_153)
            with self.assertRaisesRegex(ValueError, "oversized"):
                read_audit(path)
            path.write_text("[" * 1_200 + "0" + "]" * 1_200 + "\n", encoding="utf-8")
            self.assertEqual("UNKNOWN", reconcile(path, path, "parent-1", [])["status"])

    def test_omitted_selectors_need_bound_role_and_observed_child(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract_path = role_contract(root)
            parent = root / "parent.jsonl"
            arguments = {"task_name": "kernel_gpt_6_astra_xhigh", "fork_turns": "none",
                         "message": "secret packet text"}
            events = [
                {"type": "session_meta", "payload": {"id": "parent-1"}},
                {"type": "turn_context", "payload": {"turn_id": "turn-1"}},
                {"type": "response_item", "payload": {"type": "function_call",
                    "name": "spawn_agent", "call_id": "call-1", "arguments": json.dumps(arguments)}},
                {"type": "event_msg", "payload": {"type": "sub_agent_activity",
                    "kind": "started", "event_id": "call-1", "agent_thread_id": "child-1",
                    "agent_path": "/root/kernel_gpt_6_astra_xhigh"}},
                {"type": "response_item", "payload": {"type": "function_call_output",
                    "call_id": "call-1", "output": json.dumps({"task_name": "/root/kernel_gpt_6_astra_xhigh"})}},
            ]
            def write_parent() -> None:
                parent.write_text("".join(json.dumps(item) + "\n" for item in events),
                                  encoding="utf-8")
            write_parent()
            before = event("PreToolUse")
            before["tool_name"] = "collaborationspawn_agent"
            del before["tool_input"]["model"]
            del before["tool_input"]["reasoning_effort"]
            after = event("PostToolUse", child=None)
            after["tool_name"] = "collaborationspawn_agent"
            after["tool_input"] = {"message": "secret packet text"}
            audit = root / "audit.jsonl"
            audit.write_text(hook_output(before) + "\n" + hook_output(after) + "\n",
                             encoding="utf-8")
            self.assertNotIn("secret packet text", audit.read_text(encoding="utf-8"))
            children = [{"id": "child-1", "parent_id": "parent-1",
                         "agent_path": "/root/kernel_gpt_6_astra_xhigh",
                         "turns": [{"model": "gpt-6-astra", "effort": "xhigh"}]}]
            def report(role_path=contract_path):
                return reconcile(audit, parent, "parent-1", children, role_path)
            self.assertEqual("structurally-matched", report()["status"])
            self.assertEqual("UNKNOWN", report()["provenance"])
            self.assertEqual("UNVERIFIED", report()["role_authorization"])
            self.assertEqual({"child_id": "child-1", "child_id_source": "native_sub_agent_activity"},
                             {key: report()["attempts"][0][key]
                              for key in ("child_id", "child_id_source")})
            self.assertEqual({"message_sha256": hashlib.sha256(b"secret packet text").hexdigest(),
                              "message_bytes": len(b"secret packet text")},
                             report()["attempts"][0]["post_observed_input"])
            malformed = json.loads(hook_output(after))
            context = malformed["hookSpecificOutput"]["additionalContext"]
            marker, payload = context.split(" ", 1)
            record = json.loads(payload)
            del record["post_observed_input"]
            malformed["hookSpecificOutput"]["additionalContext"] = marker + " " + json.dumps(record)
            audit.write_text(hook_output(before) + "\n" + json.dumps(malformed) + "\n",
                             encoding="utf-8")
            self.assertEqual("UNKNOWN", report()["status"])
            record["post_observed_input"] = {"message_sha256": "not-a-hash",
                                             "message_bytes": 18}
            malformed["hookSpecificOutput"]["additionalContext"] = marker + " " + json.dumps(record)
            audit.write_text(hook_output(before) + "\n" + json.dumps(malformed) + "\n",
                             encoding="utf-8")
            self.assertEqual("UNKNOWN", report()["status"])
            audit.write_text(hook_output(before) + "\n" + hook_output(after) + "\n",
                             encoding="utf-8")
            self.assertIn("omitted selectors lack verified role contract",
                          report(None)["issues"])
            arguments["model"] = "gpt-6-astra"
            events[2]["payload"]["arguments"] = json.dumps(arguments)
            write_parent()
            self.assertIn("omitted-selector attempt differs from verified role commitment",
                          report()["issues"])
            del arguments["model"]
            events[2]["payload"]["arguments"] = json.dumps(arguments)
            write_parent()
            explicit = event("PreToolUse")
            explicit["tool_name"] = "collaborationspawn_agent"
            audit.write_text(hook_output(explicit) + "\n" + hook_output(after) + "\n",
                             encoding="utf-8")
            self.assertIn("verified role contract requires omitted native selectors",
                          report()["issues"])
            audit.write_text(hook_output(before) + "\n" + hook_output(after) + "\n",
                             encoding="utf-8")
            children[0]["turns"].append({"model": "gpt-6-sol", "effort": "low"})
            self.assertIn("child turn model/effort missing or switched", report()["issues"])
            children[0]["turns"].pop()
            children[0]["turns"].clear()
            self.assertIn("child turn model/effort missing or switched", report()["issues"])
            children[0]["turns"].append({"model": "gpt-6-astra", "effort": "xhigh"})
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            role_entry = next(item for item in contract["capability_evidence"]
                              if item["kind"] == "role_file")
            role_entry["sha256"] = "0" * 64
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            self.assertEqual("UNKNOWN", report()["status"])


if __name__ == "__main__":
    unittest.main()
