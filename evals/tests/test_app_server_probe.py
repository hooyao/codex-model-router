"""Synthetic App Server frames exercise filtering without paid model calls."""
from __future__ import annotations

import io
import base64
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from evals.scripts import app_server_probe as probe


SECRET = "PRIVATE_PACKET_MARKER"
HOOK_SOURCE = r"C:\installed-plugin\hooks\hooks.json"
SESSION_SOURCE = r"C:\<session-flags>\config.toml"


def frame(value: dict) -> bytes:
    return (json.dumps(value) + "\n").encode()


def base_reducer(*, requested_model: str | None = "gpt-6-sol",
                 requested_effort: str | None = "low", observed_model: str = "gpt-6-sol") -> probe.Reducer:
    reducer = probe.Reducer(SECRET, requested_parent_model=requested_model,
                            requested_parent_effort=requested_effort,
                            expected_hook_source_path=HOOK_SOURCE)
    reducer.feed(frame({"jsonrpc": "2.0", "id": 1, "result": {"userAgent": "test"}}))
    reducer.feed(frame({"jsonrpc": "2.0", "id": 2, "result": {
        "thread": {"id": "parent-1", "model": observed_model, "reasoningEffort": "low"}}}))
    reducer.feed(frame({"jsonrpc": "2.0", "id": 3, "result": {"turn": {"id": "turn-1"}}}))
    return reducer


def call_event(**changes) -> dict:
    item = {"type": "collabAgentToolCall", "tool": "spawnAgent", "id": "call-1",
            "senderThreadId": "parent-1", "receiverThreadIds": ["child-1"],
            "prompt": SECRET, "model": "gpt-6-luna", "reasoningEffort": "low",
            "status": "completed", "agentsStates": {}}
    item.update(changes)
    return {"jsonrpc": "2.0", "method": "item/completed", "params": {
        "threadId": "parent-1", "turnId": "turn-1", "item": item,
        "completedAtMs": 2}}


def hook_event(method: str, event_name: str = "preToolUse", hook_id: str = "hook-1",
               **changes) -> dict:
    run = {"id": hook_id, "eventName": event_name, "source": "plugin",
           "sourcePath": HOOK_SOURCE, "handlerType": "command", "scope": "turn",
           "status": "running" if method == "hook/started" else "completed",
           "executionMode": "sync", "displayOrder": 0, "startedAt": 1,
           "completedAt": None if method == "hook/started" else 2,
           "durationMs": None if method == "hook/started" else 1,
           "statusMessage": None, "entries": [{"kind": "context", "text": SECRET}]}
    run.update(changes)
    return {"jsonrpc": "2.0", "method": method, "params": {
        "threadId": "parent-1", "turnId": "turn-1", "run": run}}


def terminal_event(status: object = "completed") -> dict:
    return {"jsonrpc": "2.0", "method": "turn/completed", "params": {
        "threadId": "parent-1", "turn": {"id": "turn-1", "status": status}}}


def subagent_activity_event(*, kind: str = "started", agent_thread_id: str = "child-1") -> dict:
    return {"method": "item/completed", "params": {"threadId": "parent-1",
        "turnId": "turn-1", "completedAtMs": 2,
        "item": {"type": "subAgentActivity", "id": "activity-1", "kind": kind,
                 "agentThreadId": agent_thread_id, "agentPath": "/private/agent/path"}}}


def inventory_response(workspace: str, command: str, *, trusted: bool = False,
                       extra: list[dict] | None = None) -> dict:
    hooks = []
    for event, suffix, digest_char in (("preToolUse", "pre_tool_use", "a"),
                                       ("postToolUse", "post_tool_use", "b")):
        hooks.append({"key": SESSION_SOURCE + ":" + suffix + ":0:0",
                      "currentHash": "sha256:" + digest_char * 64,
                      "eventName": event, "handlerType": "command",
                      "matcher": probe.HOOK_PROBE_MATCHER, "command": command,
                      "timeoutSec": 5, "statusMessage": None,
                      "sourcePath": SESSION_SOURCE, "source": "sessionFlags",
                      "pluginId": None, "displayOrder": len(hooks),
                      "enabled": True, "isManaged": False,
                      "trustStatus": "trusted" if trusted else "untrusted"})
    return {"data": [{"cwd": workspace, "hooks": hooks + (extra or []),
                      "warnings": [], "errors": []}]}


def fixed_context_reducer(*, missing_native_name: bool = False,
                          native_name: str = probe.FIXED_SPAWN_NAME,
                          fork_turns: str = "none", second_child: bool = False,
                          conflicting_child: bool = False,
                          child_business: bool = False,
                          child_followup: bool = False,
                          parent_followup: bool = False,
                          contradictory_selector: bool = False) -> probe.FixedSentinelReducer:
    reducer = probe.FixedSentinelReducer(SECRET, SESSION_SOURCE)
    for event in ({"id": 1, "result": {"userAgent": "test"}},
                  {"id": 2, "result": {"thread": {"id": "parent-1",
                                              "model": "gpt-6-sol"}}},
                  {"id": 3, "result": {"turn": {"id": "turn-1"}}}):
        reducer.feed(frame(event))
    for child in (["child-1", "child-2"] if second_child else ["child-1"]):
        reducer.feed(frame({"method": "thread/started", "params": {"thread": {
            "id": child, "parentThreadId": "parent-1", "model": "gpt-6-astra",
            "reasoningEffort": "xhigh"}}}))
        activity = subagent_activity_event(agent_thread_id=child)
        activity["params"]["item"]["id"] = "call-1" if child == "child-1" else "call-2"
        reducer.feed(frame(activity))
    if conflicting_child:
        for child_model in ("gpt-6-sol", "gpt-6-astra"):
            reducer.feed(frame({"method": "thread/started", "params": {"thread": {
                "id": "child-1", "parentThreadId": "parent-1", "model": child_model,
                "reasoningEffort": "xhigh"}}}))
    if child_business:
        reducer.feed(frame({"method": "item/completed", "params": {
            "threadId": "child-1", "turnId": "child-turn-1", "item": {
                "type": "commandExecution", "id": "child-command-1", "command": SECRET}}}))
    if child_followup:
        for message_id in ("child-message-1", "child-message-2"):
            reducer.feed(frame({"method": "item/completed", "params": {
                "threadId": "child-1", "turnId": "child-turn-1", "item": {
                    "type": "userMessage", "id": message_id, "text": SECRET}}}))
    if parent_followup:
        reducer.feed(frame(call_event(id="call-followup", tool="sendInput",
                                      prompt=None, model=None, reasoningEffort=None)))
    digest = hashlib.sha256(SECRET.encode()).hexdigest()
    pre = {"schema_version": 1, "kind": "pre", "tool_name": "collaborationspawn_agent",
           "session_id": "parent-1", "turn_id": "turn-1", "attempt_id": "call-1",
           "native_name": native_name, "selector_state": "omitted",
           "fork_turns": fork_turns, "message_sha256": digest,
           "message_bytes": len(SECRET)}
    if missing_native_name:
        del pre["native_name"]
    if contradictory_selector:
        pre["model"] = "gpt-6-astra"
    post = {"schema_version": 1, "kind": "post", "tool_name": "collaborationspawn_agent",
            "session_id": "parent-1", "turn_id": "turn-1", "attempt_id": "call-1",
            "child_id": "child-1", "result_observed": True,
            "post_observed_input": {"message_sha256": digest,
                                    "message_bytes": len(SECRET)}}
    for event_name, hook_id, record in (("preToolUse", "opaque:pre/id", pre),
                                        ("postToolUse", "opaque:post/id", post)):
        for method in ("hook/started", "hook/completed"):
            reducer.feed(frame(hook_event(
                method, event_name, hook_id, source="sessionFlags",
                sourcePath=SESSION_SOURCE,
                entries=[{"kind": "context", "text":
                          probe.AUDIT_CONTEXT_PREFIX + json.dumps(record)}]
                if method == "hook/completed" else [])))
    reducer.feed(frame(terminal_event()))
    return reducer


def finish_success(reducer: probe.Reducer) -> dict:
    reducer.feed(frame(call_event()))
    for event_name, hook_id in (("preToolUse", "hook-1"), ("postToolUse", "hook-2")):
        reducer.feed(frame(hook_event("hook/started", event_name, hook_id)))
        reducer.feed(frame(hook_event("hook/completed", event_name, hook_id)))
    reducer.feed(frame(terminal_event()))
    return reducer.receipt(source="launched_app_server")


class FakeProcess:
    def __init__(self, frames: list[dict], *, exit_code: int | None = None,
                 wait_error: Exception | None = None):
        self.stdout = io.BytesIO(b"".join(frame(item) for item in frames))
        self.stdin = io.BytesIO()
        self.exit_code = exit_code
        self.wait_error = wait_error
        self.killed = False

    def poll(self) -> int | None:
        return self.exit_code

    def kill(self) -> None:
        self.killed = True
        self.exit_code = -9

    def wait(self, timeout: int | None = None) -> int:
        if self.wait_error:
            raise self.wait_error
        return self.exit_code if self.exit_code is not None else 0


class AppServerProbeTests(unittest.TestCase):
    def test_canary_interpreter_pin_and_public_only_environment(self) -> None:
        if Path(probe.sys.executable).resolve() == probe.BUNDLED_PYTHON.resolve():
            if probe._file_sha256(Path(probe.sys.executable)) != probe.BUNDLED_PYTHON_SHA256:
                # An installed-runtime update must fail the live gate. Do not
                # silently repin production code just to satisfy a unit test.
                with self.assertRaisesRegex(probe.ProbeFailure, "canary_python_hash_mismatch"):
                    probe._validated_canary_interpreter(None)
            with mock.patch.object(probe, "_file_sha256", return_value=probe.BUNDLED_PYTHON_SHA256):
                self.assertEqual(Path(probe.sys.executable).resolve(),
                                 probe._validated_canary_interpreter(None))
        else:
            with self.assertRaisesRegex(probe.ProbeFailure,
                                        "canary_interpreter_not_bundled"):
                probe._validated_canary_interpreter(None)
        with self.assertRaisesRegex(probe.ProbeFailure, "canary_python_hash_mismatch"):
            probe._validated_canary_interpreter("0" * 64)
        env = probe._canary_environment({
            "PATH": "safe", "CODEX_MODEL_ROUTER_PACKET_CANARY_PRIVATE_KEY": SECRET,
            "CODEX_MODEL_ROUTER_PACKET_CANARY_RUN_ID": "stale"},
            b"public key bytes", "run-1-launch-1")
        self.assertEqual("safe", env["PATH"])
        self.assertNotIn("CODEX_MODEL_ROUTER_PACKET_CANARY_PRIVATE_KEY", env)
        self.assertEqual("run-1-launch-1", env[
            "CODEX_MODEL_ROUTER_PACKET_CANARY_RUN_ID"])
        self.assertEqual("1", env["CODEX_MODEL_ROUTER_DISPATCH_AUDIT"])
        self.assertNotIn(SECRET, json.dumps(env))

    def _encrypted_canary_stream(self, *, packet: str = SECRET,
                                 hook_call: str = "call-1",
                                 post_call: str = "call-1",
                                 hook_run: str = "run-1-launch-1",
                                 reducer_run: str = "run-1-launch-1",
                                 warning_mutation=None,
                                 extra_pre_entries: list[dict] | None = None,
                                 pre_source: str = SESSION_SOURCE,
                                 omit_pre_warning: bool = False,
                                 duplicate_post: bool = False,
                                 assistant_text: str | None = None,
                                 wrong_key: bool = False) -> tuple[probe.FixedSentinelReducer, str]:
        if importlib.util.find_spec("cryptography") is None:
            self.skipTest("encrypted canary requires the pinned bundled Python runtime")
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        public_der = private_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        audit = Path(__file__).resolve().parents[2] / "plugins" / "codex-model-router" / \
            "hooks" / "dispatch_audit.py"
        event = {"session_id": "parent-1", "turn_id": "turn-1",
                 "hook_event_name": "PreToolUse", "tool_name": "collaborationspawn_agent",
                 "tool_use_id": hook_call,
                 "tool_input": {"task_name": probe.FIXED_SPAWN_NAME, "message": packet,
                                "fork_turns": "none"}}
        env = {**os.environ, "CODEX_MODEL_ROUTER_DISPATCH_AUDIT": "1",
               "CODEX_MODEL_ROUTER_PACKET_CANARY": "1",
               "CODEX_MODEL_ROUTER_PACKET_CANARY_RUN_ID": hook_run,
               "CODEX_MODEL_ROUTER_PACKET_CANARY_PUBLIC_KEY_B64": base64.b64encode(
                   public_der).decode("ascii")}
        output = probe.subprocess.run([probe.sys.executable, str(audit)],
                                      input=json.dumps(event).encode("utf-8"),
                                      capture_output=True, env=env, check=True)
        warning = json.loads(output.stdout)["systemMessage"]
        if warning_mutation is not None:
            warning = warning_mutation(warning)
        if wrong_key:
            private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        reducer = probe.FixedSentinelReducer(SECRET, SESSION_SOURCE,
            canary_private_key=private_key, canary_run_id=reducer_run)
        for response in ({"id": 1, "result": {"userAgent": "test"}},
                         {"id": 2, "result": {"thread": {"id": "parent-1",
                                                      "model": "gpt-6-sol"}}},
                         {"id": 3, "result": {"turn": {"id": "turn-1"}}}):
            reducer.feed(frame(response))
        reducer.feed(frame({"method": "thread/started", "params": {"thread": {
            "id": "child-1", "parentThreadId": "parent-1", "model": "gpt-6-astra",
            "reasoningEffort": "xhigh"}}}))
        activity = subagent_activity_event()
        activity["params"]["item"]["id"] = "call-1"
        reducer.feed(frame(activity))
        if assistant_text is not None:
            reducer.feed(frame({"method": "item/completed", "params": {
                "threadId": "parent-1", "turnId": "turn-1", "item": {
                    "type": "agentMessage", "id": "message-1", "text": assistant_text}}}))
        pre_entries = ([] if omit_pre_warning else [{"kind": "warning", "text": warning}]) + \
            (extra_pre_entries or [])
        for method in ("hook/started", "hook/completed"):
            reducer.feed(frame(hook_event(method, "preToolUse", "opaque:pre:" + hook_call,
                source="sessionFlags", sourcePath=pre_source,
                entries=pre_entries if method == "hook/completed" else [])))
        raw = packet.encode("utf-8")
        post = {"schema_version": 1, "kind": "post",
                "tool_name": "collaborationspawn_agent", "session_id": "parent-1",
                "turn_id": "turn-1", "attempt_id": post_call, "child_id": "child-1",
                "result_observed": True, "post_observed_input": {
                    "message_sha256": hashlib.sha256(raw).hexdigest(),
                    "message_bytes": len(raw)}}
        for method in ("hook/started", "hook/completed"):
            post_entries = [{"kind": "context", "text": probe.AUDIT_CONTEXT_PREFIX +
                             json.dumps(post)}]
            if duplicate_post:
                post_entries.append(dict(post_entries[0]))
            reducer.feed(frame(hook_event(method, "postToolUse", "opaque:post:" + post_call,
                source="sessionFlags", sourcePath=SESSION_SOURCE,
                entries=post_entries if method == "hook/completed" else [])))
        reducer.feed(frame(terminal_event()))
        return reducer, warning

    def test_encrypted_pre_packet_from_trusted_warning_matches_post_and_activity(self) -> None:
        reducer, warning = self._encrypted_canary_stream()
        receipt = reducer.receipt(source="launched_app_server")
        self.assertEqual("OBSERVED", receipt["capabilities"]["encrypted_pre_packet"])
        self.assertEqual("UNKNOWN", receipt["capabilities"]["effective_dispatch_proof"])
        self.assertTrue(receipt["capabilities"]["system_message_warning_only"])
        self.assertTrue(receipt["audit_attempts"][0]["encrypted_pre"]["aad_verified"])
        self.assertTrue(receipt["audit_attempts"][0]["encrypted_pre"][
            "contains_expected_sentinel"])
        self.assertNotIn(SECRET, warning + json.dumps(receipt))
        self.assertNotIn("ciphertext_b64", json.dumps(receipt))
        self.assertNotIn("wrapped_key_b64", json.dumps(receipt))

    def test_generated_run_launch_id_passes_real_hook_validator_and_encryption(self) -> None:
        first = probe._new_canary_run_id()
        second = probe._new_canary_run_id()
        self.assertNotEqual(first, second)
        self.assertLessEqual(len(first), 64)
        run, launch = first.split("-")
        self.assertEqual(30, len(run))
        self.assertEqual(32, len(launch))
        self.assertIsNotNone(probe.re.fullmatch(r"[A-Za-z0-9_-]{1,64}", first))
        reducer, warning = self._encrypted_canary_stream(hook_run=first,
                                                         reducer_run=first)
        self.assertTrue(warning.startswith(probe.CANARY_PREFIX))
        receipt = reducer.receipt(source="launched_app_server")
        self.assertEqual("OBSERVED", receipt["capabilities"]["encrypted_pre_packet"])
        self.assertNotIn(SECRET, warning + json.dumps(receipt))

    def test_encrypted_pre_packet_mutations_remain_unknown(self) -> None:
        cases = ({"wrong_key": True}, {"reducer_run": "other-run"},
                 {"hook_call": "call-2"}, {"post_call": "call-2"},
                 {"warning_mutation": lambda value: value[:-20]},
                 {"warning_mutation": lambda value: value.replace('"phase":"pre"',
                                                                    '"phase":"post"')},
                 {"extra_pre_entries": [{"kind": "warning", "text": "duplicate"}]},
                 {"pre_source": r"C:\wrong\config.toml"},
                 {"duplicate_post": True})
        for changes in cases:
            with self.subTest(changes=tuple(changes)):
                reducer, _ = self._encrypted_canary_stream(**changes)
                receipt = reducer.receipt(source="launched_app_server")
                self.assertEqual("UNKNOWN", receipt["capabilities"]["encrypted_pre_packet"])
                self.assertNotIn(SECRET, json.dumps(receipt))

    def test_encrypted_pre_packet_replay_and_context_leak_remain_unknown(self) -> None:
        reducer, warning = self._encrypted_canary_stream()
        reducer.feed(frame(hook_event("hook/completed", "preToolUse", "opaque:pre:call-1",
            source="sessionFlags", sourcePath=SESSION_SOURCE,
            entries=[{"kind": "warning", "text": warning}])))
        self.assertEqual("UNKNOWN", reducer.receipt(
            source="launched_app_server")["capabilities"]["encrypted_pre_packet"])
        reducer, warning = self._encrypted_canary_stream(
            extra_pre_entries=[{"kind": "context", "text": probe.CANARY_PREFIX + "leak"}])
        receipt = reducer.receipt(source="launched_app_server")
        self.assertTrue(receipt["diagnostics"]["packet_canary_context_entry_seen"])
        self.assertEqual("UNKNOWN", receipt["capabilities"]["encrypted_pre_packet"])
        reducer, warning = self._encrypted_canary_stream(
            assistant_text=probe.CANARY_PREFIX + "forged assistant text")
        receipt = reducer.receipt(source="launched_app_server")
        self.assertEqual("UNKNOWN", receipt["capabilities"]["encrypted_pre_packet"])
        reducer, _ = self._encrypted_canary_stream(
            omit_pre_warning=True,
            assistant_text=probe.CANARY_PREFIX + "forged assistant text")
        self.assertEqual("UNKNOWN", reducer.receipt(
            source="launched_app_server")["capabilities"]["encrypted_pre_packet"])

    def test_unscannable_non_audit_hook_entries_fail_context_exclusion_closed(self) -> None:
        for malformed in ("oversized", "not_a_list", "malformed_entry"):
            with self.subTest(malformed=malformed):
                reducer, warning = self._encrypted_canary_stream()
                self.assertEqual("OBSERVED", reducer.receipt(
                    source="launched_app_server")["capabilities"]["encrypted_pre_packet"])
                self.assertEqual("OBSERVED", reducer.receipt(
                    source="launched_app_server")["capabilities"]["context_exclusion"])
                entries = {
                    "oversized": [{"kind": "warning", "text": "other"}] * 16 +
                        [{"kind": "context", "text": warning}],
                    "not_a_list": {"kind": "context", "text": warning},
                    "malformed_entry": [{"kind": "context", "text": None}],
                }[malformed]
                reducer.feed(frame(hook_event("hook/completed", "sessionStart", "managed-1",
                    source="system", sourcePath=r"C:\managed\hooks.json", entries=entries)))
                receipt = reducer.receipt(source="launched_app_server")
                self.assertTrue(receipt["diagnostics"]["context_exclusion_unknown"])
                self.assertEqual("UNKNOWN", receipt["capabilities"]["context_exclusion"])
                self.assertEqual("UNKNOWN", receipt["capabilities"]["encrypted_pre_packet"])
                self.assertFalse(receipt["capabilities"]["system_message_warning_only"])
                self.assertNotIn(SECRET, json.dumps(receipt))

    def test_terminal_item_truncation_and_event_limit_fail_context_exclusion_closed(self) -> None:
        reducer, _ = self._encrypted_canary_stream()
        terminal = terminal_event()
        terminal["params"]["turn"]["items"] = [{}] * (probe.MAX_EVENTS + 1)
        reducer.feed(frame(terminal))
        receipt = reducer.receipt(source="launched_app_server")
        self.assertEqual("UNKNOWN", receipt["capabilities"]["context_exclusion"])
        self.assertEqual("UNKNOWN", receipt["capabilities"]["encrypted_pre_packet"])
        reducer, _ = self._encrypted_canary_stream()
        reducer.events = probe.MAX_EVENTS
        with self.assertRaisesRegex(probe.ProbeFailure, "event_limit"):
            reducer.feed(frame({"method": "other/notification", "params": {}}))
        receipt = reducer.receipt(source="launched_app_server")
        self.assertEqual("UNKNOWN", receipt["capabilities"]["context_exclusion"])
        self.assertEqual("UNKNOWN", receipt["capabilities"]["encrypted_pre_packet"])

    def test_large_unicode_crlf_packet_decrypts_without_claiming_fixed_sentinel(self) -> None:
        packet = SECRET + "\r\n" + ("雪🌙\r\n" * 4096)
        reducer, warning = self._encrypted_canary_stream(packet=packet)
        receipt = reducer.receipt(source="launched_app_server")
        encrypted_pre = receipt["audit_attempts"][0]["encrypted_pre"]
        self.assertTrue(encrypted_pre["aad_verified"])
        self.assertTrue(encrypted_pre["contains_expected_sentinel"])
        self.assertFalse(encrypted_pre["sentinel_exact_match"])
        self.assertEqual(len(packet.encode("utf-8")), encrypted_pre["message_bytes"])
        self.assertEqual("UNKNOWN", receipt["capabilities"]["encrypted_pre_packet"])
        self.assertNotIn(packet, warning + json.dumps(receipt))

    def test_dry_run_is_synthetic_and_reduced(self) -> None:
        receipt = probe.dry_run()
        encoded = json.dumps(receipt)
        self.assertEqual("synthetic", receipt["source"])
        self.assertFalse(receipt["capabilities"]["usable_for_dispatch_provenance"])
        self.assertTrue(receipt["capabilities"]["typed_spawn_prompt_and_selectors"])
        self.assertEqual(["child-1"], receipt["calls"][0]["receiver_thread_ids"])
        self.assertNotIn("capability sentinel", encoded)
        self.assertEqual("gpt-6.1-sol", receipt["requested_turn_model"])
        self.assertEqual("gpt-6.1-sol", receipt["thread_model_at_start"])

    def test_current_and_legacy_sol_probe_selectors_require_exact_observation(self) -> None:
        for model in ("gpt-6.1-sol", "gpt-6-sol"):
            with self.subTest(model=model):
                receipt = finish_success(base_reducer(requested_model=model, observed_model=model))
                self.assertTrue(receipt["capabilities"]["usable_for_dispatch_provenance"])
                self.assertEqual(model, receipt["requested_turn_model"])
                self.assertEqual(model, receipt["thread_model_at_start"])
        receipt = finish_success(base_reducer(requested_model="gpt-6.1-sol"))
        self.assertTrue(receipt["capabilities"]["approved_parent_selector_request"])
        self.assertFalse(receipt["capabilities"]["thread_model_matches_requested"])
        self.assertFalse(receipt["capabilities"]["usable_for_dispatch_provenance"])
        for effort in ("medium", "ultra"):
            with self.subTest(effort=effort):
                receipt = finish_success(base_reducer(requested_model="gpt-6.1-sol",
                    requested_effort=effort, observed_model="gpt-6.1-sol"))
                self.assertFalse(receipt["capabilities"]["approved_parent_selector_request"])

    def test_live_probe_defaults_to_current_sol_without_changing_explicit_legacy_selector(self) -> None:
        arguments = ["--live", "--codex-cli", "C:\\codex.exe",
                     "--router-hook-config", "C:\\hooks.json",
                     "--router-audit-script", "C:\\dispatch_audit.py"]
        receipt = {"capabilities": {"usable_for_dispatch_provenance": True}}
        for extra, expected in (([], "gpt-6.1-sol"),
                                (["--model", "gpt-6-sol"], "gpt-6-sol")):
            with self.subTest(model=expected), mock.patch.object(probe, "run_live", return_value=receipt) as run:
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(0, probe.main(arguments + extra))
                self.assertEqual((expected, "low"), run.call_args.args[3:5])

    def test_fake_successful_stream_sends_exact_protocol_requests(self) -> None:
        events = [
            {"jsonrpc": "2.0", "id": 1, "result": {"userAgent": "test"}},
            {"jsonrpc": "2.0", "id": 2, "result": {"thread": {
                "id": "parent-1", "model": "gpt-6-sol", "reasoningEffort": "low"}}},
            {"jsonrpc": "2.0", "id": 4, "result": {"data": [
                {"name": "multi_agent", "enabled": True}]}},
            {"jsonrpc": "2.0", "id": 3, "result": {"turn": {"id": "turn-1"}}},
            call_event(), hook_event("hook/started"), hook_event("hook/completed"),
            hook_event("hook/started", "postToolUse", "hook-2"),
            hook_event("hook/completed", "postToolUse", "hook-2"),
            {"method": "item/completed", "params": {"threadId": "parent-1",
                "turnId": "turn-1", "item": {"type": "agentMessage", "id": "message-1",
                "phase": "final_answer", "text": SECRET}}},
            terminal_event(),
        ]
        # Installed App Server stdio omits the optional JSON-RPC header.
        events = [{key: value for key, value in event.items() if key != "jsonrpc"}
                  for event in events]
        process = FakeProcess(events)
        reducer = probe.Reducer(SECRET, requested_parent_model="gpt-6-sol",
                                requested_parent_effort="low",
                                expected_hook_source_path=HOOK_SOURCE)
        probe.drive_protocol(process, reducer, "parent prompt", "gpt-6-sol", "low", 1, "C:\\temp")
        receipt = reducer.receipt(source="launched_app_server")
        self.assertTrue(receipt["capabilities"]["usable_for_dispatch_provenance"])
        requests = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
        self.assertEqual(["initialize", "initialized", "thread/start",
                          "experimentalFeature/list", "turn/start"],
                         [request["method"] for request in requests])
        self.assertEqual("read-only", requests[2]["params"]["sandbox"])
        self.assertEqual("readOnly", requests[4]["params"]["sandboxPolicy"]["type"])
        self.assertTrue(all("jsonrpc" not in request for request in requests))
        self.assertEqual("complete_capability", receipt["capabilities"]["classification"])
        self.assertEqual("low", receipt["requested_turn_effort"])
        self.assertEqual("low", receipt["calls"][0]["spawn_requested_effort"])
        self.assertTrue(receipt["diagnostics"]["multi_agent_feature_enabled"])
        self.assertTrue(receipt["diagnostics"]["terminal_agent_message_observed"])
        self.assertEqual(hashlib.sha256(SECRET.encode()).hexdigest(),
                         receipt["diagnostics"]["terminal_agent_message_digest"]["sha256"])
        self.assertEqual(1, receipt["diagnostics"]["item_event_type_counts"]["agentMessage"])
        self.assertNotIn(SECRET, json.dumps(receipt))

    def test_malicious_text_is_not_reproduced(self) -> None:
        reducer = base_reducer()
        reducer.feed(frame({"jsonrpc": "2.0", "method": "item/completed", "params": {
            "threadId": "parent-1", "turnId": "turn-1", "item": {
                "type": "agentMessage", "text": SECRET, "id": "call-1"}}}))
        reducer.feed(frame(call_event()))
        reducer.feed(frame(hook_event("hook/completed")))
        receipt = json.dumps(reducer.receipt(source="launched_app_server"))
        self.assertNotIn(SECRET, receipt)
        self.assertNotIn("entries", receipt)

    def test_diagnostics_bucket_unknown_types_without_leaking_text(self) -> None:
        reducer = base_reducer()
        reducer.feed(frame({"method": SECRET, "params": {"text": SECRET}}))
        reducer.feed(frame({"method": "item/completed", "params": {
            "threadId": "parent-1", "turnId": "turn-1", "item": {
                "type": SECRET, "text": SECRET}}}))
        reducer.feed(frame(terminal_event()))
        receipt = reducer.receipt(source="launched_app_server")
        self.assertEqual(1, receipt["diagnostics"]["notification_counts"]["other"])
        self.assertEqual(1, receipt["diagnostics"]["item_event_type_counts"]["other"])
        self.assertFalse(receipt["diagnostics"]["terminal_agent_message_observed"])
        self.assertNotIn(SECRET, json.dumps(receipt))

    def test_schema_shapes_report_rejection_without_losing_safe_lineage(self) -> None:
        reducer = base_reducer()
        reducer.feed(frame(call_event(tool="wait", prompt=None, model=None,
                                      reasoningEffort=None)))
        reducer.feed(frame(call_event(senderThreadId="other-parent")))
        reducer.feed(frame(subagent_activity_event()))
        unrelated = hook_event("hook/started", "sessionStart", "hook-session")
        unrelated["params"]["turnId"] = None
        reducer.feed(frame(unrelated))
        for method in ("hook/started", "hook/completed"):
            unlinked = hook_event(method, "preToolUse", "hook-pre")
            unlinked["params"]["turnId"] = None
            reducer.feed(frame(unlinked))
        reducer.feed(frame(terminal_event()))
        receipt = reducer.receipt(source="launched_app_server")
        diagnostics = receipt["diagnostics"]
        self.assertEqual(1, diagnostics["collab_tool_counts"]["wait"])
        self.assertEqual(1, diagnostics["collab_tool_counts"]["spawnAgent"])
        self.assertEqual(1, diagnostics["collab_shape_counts"]["sender_mismatch"])
        self.assertEqual(1, diagnostics["hook_event_counts"]["sessionStart"])
        self.assertEqual(2, diagnostics["hook_shape_counts"]["unlinked_turn"])
        self.assertEqual("child-1", receipt["subagent_lineage"][0]["agent_thread_id"])
        self.assertFalse(receipt["calls"][0]["sender_matches_parent"])
        self.assertFalse(receipt["hooks"][0]["turn_matches_parent"])
        self.assertFalse(receipt["capabilities"]["usable_for_dispatch_provenance"])
        self.assertNotIn(SECRET, json.dumps(receipt))
        self.assertNotIn("/private/agent/path", json.dumps(receipt))

    def test_v2_completed_activity_is_lifecycle_not_another_spawn(self) -> None:
        reducer, _ = self._encrypted_canary_stream()
        # V2 emits a new activity ID when the same child completes, with both
        # item/started and item/completed notifications for that activity.
        for method in ("item/started", "item/completed"):
            event = subagent_activity_event(kind="completed")
            event["method"] = method
            event["params"]["item"]["id"] = "completion-1"
            reducer.feed(frame(event))
        receipt = reducer.receipt(source="launched_app_server")
        self.assertEqual(1, len(receipt["subagent_lineage"]))
        self.assertEqual("call-1", receipt["subagent_lineage"][0]["activity_id"])
        self.assertEqual(1, receipt["diagnostics"]["native_activity_marker_count"])
        self.assertFalse(receipt["diagnostics"]["subagent_activity_conflict"])
        self.assertEqual("OBSERVED", receipt["capabilities"]["encrypted_pre_packet"])
        self.assertEqual("UNKNOWN", receipt["capabilities"]["effective_dispatch_proof"])

    def test_observed_v2_activity_fixture_has_one_spawn(self) -> None:
        fixture = json.loads((Path(__file__).parent / "fixtures" /
                              "v2-dispatch-observed.json").read_text(encoding="utf-8"))
        reducer = base_reducer()
        for row in fixture["native"]:
            native = row["payload"].get("item", {})
            if native.get("type") != "SubAgentActivity":
                continue
            for method in ("item/started", "item/completed"):
                reducer.feed(frame({"method": method, "params": {
                    "threadId": "parent-1", "turnId": "turn-1", "item": {
                        "type": "subAgentActivity", "id": native["id"],
                        "kind": native["kind"], "agentThreadId": native["agent_thread_id"]}}}))
        result = reducer.receipt(source="synthetic")
        self.assertEqual(1, len(result["subagent_lineage"]))
        self.assertEqual(fixture["audit"][0]["attempt_id"],
                         result["subagent_lineage"][0]["activity_id"])
        self.assertEqual(2, result["diagnostics"]["subagent_kind_counts"]["completed"])
        self.assertFalse(result["diagnostics"]["subagent_activity_conflict"])
        self.assertFalse(result["capabilities"]["usable_for_dispatch_provenance"])

    def test_v2_lifecycle_conflicts_and_followups_fail_closed(self) -> None:
        for kind, activity_id, child in (("completed", "completion-1", "unknown-child"),
                                          ("completed", "call-1", "child-1"),
                                          ("started", "call-1", "other-child"),
                                          ("interacted", "followup-1", "child-1"),
                                          ("interrupted", "interrupt-1", "child-1"),
                                          ("unsupported", "other-1", "child-1")):
            with self.subTest(kind=kind, activity=activity_id, child=child):
                reducer, _ = self._encrypted_canary_stream()
                event = subagent_activity_event(kind=kind, agent_thread_id=child)
                event["params"]["item"]["id"] = activity_id
                reducer.feed(frame(event))
                receipt = reducer.receipt(source="launched_app_server")
                self.assertEqual("UNKNOWN", receipt["capabilities"]["encrypted_pre_packet"])
                self.assertFalse(receipt["capabilities"]["usable_for_dispatch_provenance"])

    def test_v2_activity_wrong_parent_turn_and_second_completion_fail_closed(self) -> None:
        for mutation in ("parent", "turn", "second_completion"):
            with self.subTest(mutation=mutation):
                reducer, _ = self._encrypted_canary_stream()
                event = subagent_activity_event(kind="completed")
                event["params"]["item"]["id"] = "completion-1"
                reducer.feed(frame(event))
                event = subagent_activity_event(kind="completed")
                event["params"]["item"]["id"] = "completion-2"
                if mutation == "parent":
                    event["params"]["threadId"] = "unrelated-parent"
                elif mutation == "turn":
                    event["params"]["turnId"] = "unrelated-turn"
                reducer.feed(frame(event))
                receipt = reducer.receipt(source="launched_app_server")
                self.assertTrue(receipt["diagnostics"]["subagent_activity_conflict"])
                self.assertEqual("UNKNOWN", receipt["capabilities"]["encrypted_pre_packet"])
                self.assertFalse(receipt["capabilities"]["usable_for_dispatch_provenance"])

    def test_invalid_schema_ids_are_counted_without_echo(self) -> None:
        reducer = base_reducer()
        reducer.feed(frame(call_event(id=SECRET + "!")))
        reducer.feed(frame(hook_event("hook/started", hook_id="")))
        reducer.feed(frame(subagent_activity_event(agent_thread_id=SECRET + "!")))
        reducer.feed(frame(terminal_event()))
        receipt = reducer.receipt(source="launched_app_server")
        self.assertEqual(1, receipt["diagnostics"]["collab_shape_counts"]["invalid_call_id"])
        self.assertEqual(1, receipt["diagnostics"]["hook_shape_counts"]["invalid_hook_id"])
        self.assertEqual(1, receipt["diagnostics"]["subagent_shape_counts"]
                         ["invalid_agent_thread_id"])
        self.assertEqual([], receipt["calls"])
        self.assertEqual([], receipt["hooks"])
        self.assertEqual([], receipt["subagent_lineage"])
        self.assertNotIn(SECRET, json.dumps(receipt))

    def test_opaque_hook_run_ids_bind_without_raw_id_output(self) -> None:
        reducer = base_reducer()
        reducer.feed(frame(call_event()))
        hook_ids = {"preToolUse": "plugin:pre/tool@probe",
                    "postToolUse": "plugin:post/tool@probe"}
        for event_name, hook_id in hook_ids.items():
            reducer.feed(frame(hook_event("hook/started", event_name, hook_id)))
            reducer.feed(frame(hook_event("hook/completed", event_name, hook_id)))
        reducer.feed(frame(terminal_event()))
        receipt = reducer.receipt(source="launched_app_server")
        self.assertTrue(receipt["capabilities"]["hook_lifecycle"])
        self.assertTrue(receipt["capabilities"]["usable_for_dispatch_provenance"])
        digests = {hook["hook_id_sha256"] for hook in receipt["hooks"]}
        self.assertEqual({hashlib.sha256(value.encode()).hexdigest()
                          for value in hook_ids.values()}, digests)
        encoded = json.dumps(receipt)
        self.assertNotIn("plugin:pre/tool@probe", encoded)
        self.assertNotIn("plugin:post/tool@probe", encoded)

    def test_nullable_thread_effort_is_not_reported_as_turn_effort(self) -> None:
        reducer = base_reducer()
        reducer.parent_effort = None
        receipt = finish_success(reducer)
        self.assertIsNone(receipt["thread_configured_effort_at_start"])
        self.assertEqual("low", receipt["requested_turn_effort"])
        self.assertNotIn("parent_effort", receipt)
        self.assertTrue(receipt["capabilities"]["usable_for_dispatch_provenance"])

    def test_terminal_turn_items_supply_message_digest_without_item_notification(self) -> None:
        reducer = base_reducer()
        completed = terminal_event()
        completed["params"]["turn"]["items"] = [
            {"type": "agentMessage", "id": "message-1", "phase": "final_answer",
             "text": SECRET}]
        reducer.feed(frame(completed))
        receipt = reducer.receipt(source="launched_app_server")
        self.assertTrue(receipt["diagnostics"]["terminal_agent_message_observed"])
        self.assertEqual(hashlib.sha256(SECRET.encode()).hexdigest(),
                         receipt["diagnostics"]["terminal_agent_message_digest"]["sha256"])
        self.assertNotIn(SECRET, json.dumps(receipt))

    def test_feature_query_error_keeps_turn_diagnostic_available(self) -> None:
        events = [
            {"id": 1, "result": {"userAgent": "test"}},
            {"id": 2, "result": {"thread": {"id": "parent-1", "model": "gpt-6-sol"}}},
            {"id": 4, "error": {"code": -32601, "message": SECRET}},
            {"id": 3, "result": {"turn": {"id": "turn-1"}}},
            terminal_event(),
        ]
        process = FakeProcess(events)
        reducer = probe.Reducer(SECRET, requested_parent_model="gpt-6-sol",
                                requested_parent_effort="low")
        probe.drive_protocol(process, reducer, "parent prompt", "gpt-6-sol", "low", 1, "C:\\temp")
        receipt = reducer.receipt(source="launched_app_server")
        self.assertEqual("unavailable", receipt["diagnostics"]["multi_agent_feature_query_status"])
        self.assertIsNone(receipt["diagnostics"]["multi_agent_feature_enabled"])
        self.assertEqual("completed", receipt["turn_terminal"])
        self.assertNotIn(SECRET, json.dumps(receipt))

    def test_spoofed_assistant_messages_cannot_supply_call_or_hook(self) -> None:
        reducer = base_reducer()
        for method in ("item/completed", "agentMessage/delta"):
            reducer.feed(frame({"jsonrpc": "2.0", "method": method, "params": {
                "threadId": "parent-1", "turnId": "turn-1", "item": {
                    "type": "agentMessage", "text": json.dumps(call_event())}}}))
        receipt = reducer.receipt(source="launched_app_server")
        self.assertEqual([], receipt["calls"])
        self.assertFalse(receipt["capabilities"]["usable_for_dispatch_provenance"])

    def test_missing_prompt_hook_ids_and_selectors_fail_closed(self) -> None:
        for changes in ({"prompt": None}, {"id": SECRET + "!"}, {"model": None},
                        {"model": "gpt-6-astra"}, {"reasoningEffort": None},
                        {"reasoningEffort": "medium"}, {"receiverThreadIds": []},
                        {"senderThreadId": "other"}, {"status": "failed"}):
            with self.subTest(changes=changes):
                reducer = base_reducer()
                reducer.feed(frame(call_event(**changes)))
                reducer.feed(frame(hook_event("hook/started")))
                reducer.feed(frame(hook_event("hook/completed")))
                reducer.feed(frame(hook_event("hook/started", "postToolUse", "hook-2")))
                reducer.feed(frame(hook_event("hook/completed", "postToolUse", "hook-2")))
                reducer.feed(frame(terminal_event()))
                self.assertFalse(reducer.receipt(source="launched_app_server")
                                 ["capabilities"]["usable_for_dispatch_provenance"])
        reducer = base_reducer()
        reducer.feed(frame(call_event()))
        reducer.feed(frame(terminal_event()))
        self.assertFalse(reducer.receipt(source="launched_app_server")
                         ["capabilities"]["usable_for_dispatch_provenance"])
        reducer = base_reducer()
        reducer.feed(frame(call_event()))
        reducer.feed(frame(hook_event("hook/started")))
        wrong = hook_event("hook/completed", hook_id="other-hook")
        reducer.feed(frame(wrong))
        reducer.feed(frame(hook_event("hook/started", "postToolUse", "hook-2")))
        reducer.feed(frame(hook_event("hook/completed", "postToolUse", "hook-2")))
        reducer.feed(frame(terminal_event()))
        self.assertFalse(reducer.receipt(source="launched_app_server")
                         ["capabilities"]["usable_for_dispatch_provenance"])

    def test_wrong_hook_identity_and_missing_parent_selectors_are_partial(self) -> None:
        self.assertTrue(finish_success(base_reducer())["capabilities"]["usable_for_dispatch_provenance"])
        for requested_model, requested_effort in ((None, "low"), ("gpt-6-sol", None),
                                                   ("gpt-6-sol", "medium"),
                                                   ("gpt-6-astra", "xhigh")):
            with self.subTest(model=requested_model, effort=requested_effort):
                receipt = finish_success(base_reducer(requested_model=requested_model,
                                                       requested_effort=requested_effort))
                self.assertTrue(receipt["capabilities"]["typed_spawn_prompt_and_selectors"])
                self.assertEqual("partial_capability", receipt["capabilities"]["classification"])
                self.assertFalse(receipt["capabilities"]["usable_for_dispatch_provenance"])
        reducer = base_reducer(requested_model="gpt-6-luna", requested_effort="medium")
        receipt = finish_success(reducer)
        self.assertTrue(receipt["capabilities"]["approved_parent_selector_request"])
        self.assertFalse(receipt["capabilities"]["thread_model_matches_requested"])
        self.assertEqual("partial_capability", receipt["capabilities"]["classification"])
        for changes in ({"source": "user"}, {"source": []},
                        {"handlerType": "prompt"}, {"handlerType": {}},
                        {"sourcePath": r"C:\other\hooks.json"},
                        {"sourcePath": []}, {"scope": "thread"}):
            with self.subTest(changes=changes):
                reducer = base_reducer()
                reducer.feed(frame(call_event()))
                for event_name, hook_id in (("preToolUse", "hook-1"),
                                            ("postToolUse", "hook-2")):
                    reducer.feed(frame(hook_event("hook/started", event_name, hook_id,
                                                  **changes)))
                    reducer.feed(frame(hook_event("hook/completed", event_name, hook_id,
                                                  **changes)))
                reducer.feed(frame(terminal_event()))
                receipt = reducer.receipt(source="launched_app_server")
                self.assertFalse(receipt["capabilities"]["hook_lifecycle"])
                self.assertEqual("partial_capability", receipt["capabilities"]["classification"])
        reducer = base_reducer()
        reducer.feed(frame(call_event()))
        reducer.feed(frame(hook_event("hook/started", "subagentStop")))
        reducer.feed(frame(hook_event("hook/completed", "subagentStop")))
        reducer.feed(frame(terminal_event()))
        self.assertFalse(reducer.receipt(source="launched_app_server")
                         ["capabilities"]["usable_for_dispatch_provenance"])

    def test_malformed_typed_fields_are_sanitized_with_valid_terminal(self) -> None:
        for invalid in ([], {"text": SECRET}):
            for field in ("item_status", "hook_event", "hook_status", "terminal_status"):
                with self.subTest(field=field, invalid=type(invalid).__name__):
                    reducer = base_reducer()
                    item = call_event(status=invalid) if field == "item_status" else call_event()
                    reducer.feed(frame(item))
                    for event_name, hook_id in (("preToolUse", "hook-1"),
                                                ("postToolUse", "hook-2")):
                        changes = {}
                        if event_name == "preToolUse" and field == "hook_event":
                            changes["eventName"] = invalid
                        if event_name == "preToolUse" and field == "hook_status":
                            changes["status"] = invalid
                        reducer.feed(frame(hook_event("hook/started", event_name, hook_id,
                                                      **changes)))
                        reducer.feed(frame(hook_event("hook/completed", event_name, hook_id,
                                                      **changes)))
                    reducer.feed(frame(terminal_event(invalid if field == "terminal_status"
                                                     else "completed")))
                    receipt = reducer.receipt(source="launched_app_server")
                    self.assertEqual("partial_capability", receipt["capabilities"]["classification"])
                    self.assertFalse(receipt["capabilities"]["usable_for_dispatch_provenance"])
                    self.assertNotIn(SECRET, json.dumps(receipt))
                    self.assertGreater(receipt["malformed_typed_fields"], 0)
        reducer = base_reducer()
        self.assertTrue(finish_success(reducer)["capabilities"]["usable_for_dispatch_provenance"])
        reducer.feed(frame(call_event(status=[])))
        receipt = reducer.receipt(source="launched_app_server")
        self.assertEqual("partial_capability", receipt["capabilities"]["classification"])

    def test_failed_audit_hook_maps_only_allowlisted_failure_categories(self) -> None:
        cases = (
            ("hook timed out after 5s", "command_start_shell_or_timeout"),
            ("failed to write hook stdin: " + SECRET,
             "command_start_shell_or_timeout"),
            ("hook exited with code 7", "non_2_exit"),
            ("hook returned invalid pre-tool-use JSON output",
             "invalid_hook_json_output"),
            ("failed to serialize pre tool use hook input: " + SECRET,
             "serialization"),
            ("PreToolUse hook exited with code 2 but did not write a blocking reason to stderr",
             "other_unknown"),
            (SECRET, "other_unknown"),
        )
        for error_text, category in cases:
            with self.subTest(category=category, text=error_text[:20]):
                reducer = base_reducer()
                reducer.feed(frame(hook_event("hook/started", entries=[])))
                reducer.feed(frame(hook_event(
                    "hook/completed", status="failed", statusMessage=SECRET,
                    entries=[{"kind": "error", "text": error_text},
                             {"kind": "warning", "text": SECRET}])))
                receipt = reducer.receipt(source="launched_app_server")
                self.assertEqual(category, receipt["hooks"][0]["failure_category"])
                self.assertEqual({"error": 1, "warning": 1},
                                 receipt["hooks"][0]["entry_kind_counts"])
                self.assertEqual(1, receipt["diagnostics"]
                                 ["audit_hook_failure_category_counts"][category])
                self.assertNotIn(SECRET, json.dumps(receipt))

    def test_failed_audit_hook_malformed_entries_and_unrelated_hook_fail_closed(self) -> None:
        reducer = base_reducer()
        reducer.feed(frame(hook_event("hook/started", entries=[])))
        reducer.feed(frame(hook_event("hook/completed", status="failed",
                                      statusMessage={"secret": SECRET},
                                      entries=[{"kind": "error",
                                                "text": {"secret": SECRET}}])))
        receipt = reducer.receipt(source="launched_app_server")
        self.assertEqual("other_unknown", receipt["hooks"][0]["failure_category"])
        self.assertGreater(receipt["malformed_typed_fields"], 0)
        self.assertNotIn(SECRET, json.dumps(receipt))
        unrelated = base_reducer()
        unrelated.feed(frame(hook_event("hook/completed", status="failed",
                                        source="system", statusMessage=SECRET,
                                        entries=[{"kind": "error", "text": SECRET}])))
        other_receipt = unrelated.receipt(source="launched_app_server")
        self.assertIsNone(other_receipt["hooks"][0]["failure_category"])
        self.assertEqual({}, other_receipt["diagnostics"]
                         ["audit_hook_failure_category_counts"])
        self.assertNotIn(SECRET, json.dumps(other_receipt))
        status_only = base_reducer()
        status_only.feed(frame(hook_event("hook/completed", status="failed",
                                          statusMessage="hook timed out after 5s",
                                          entries=[])))
        status_receipt = status_only.receipt(source="launched_app_server")
        self.assertEqual("command_start_shell_or_timeout",
                         status_receipt["hooks"][0]["failure_category"])

    def test_main_sanitizes_unexpected_protocol_exception(self) -> None:
        output = io.StringIO()
        with mock.patch.object(probe, "dry_run", side_effect=TypeError(SECRET)):
            with redirect_stdout(output):
                self.assertEqual(2, probe.main([]))
        self.assertNotIn(SECRET, output.getvalue())
        self.assertEqual("probe_internal_error", json.loads(output.getvalue())["failure"])
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(2, probe.main(["--timeout", SECRET]))
        self.assertNotIn(SECRET, output.getvalue())
        self.assertEqual("invalid_arguments", json.loads(output.getvalue())["failure"])
        output = io.StringIO()
        with mock.patch.object(probe, "run_live",
                               side_effect=probe.ProbeFailure("spawn_failed", "spawn")):
            with redirect_stdout(output):
                self.assertEqual(2, probe.main(["--live", "--codex-cli", "C:\\codex.exe",
                                                "--router-hook-config", "C:\\hooks.json",
                                                "--router-audit-script", "C:\\dispatch_audit.py"]))
        parsed = json.loads(output.getvalue())
        self.assertEqual(("spawn_failed", "spawn"),
                         (parsed["failure"], parsed["failure_stage"]))

    def test_multiple_or_inconsistent_spawn_items_fail_closed(self) -> None:
        reducer = base_reducer()
        reducer.feed(frame(call_event()))
        reducer.feed(frame(call_event(id="call-2")))
        self.assertFalse(reducer.receipt(source="launched_app_server")
                         ["capabilities"]["typed_spawn_prompt_and_selectors"])
        reducer = base_reducer()
        reducer.feed(frame(call_event()))
        reducer.feed(frame(call_event(prompt="different message")))
        self.assertFalse(reducer.receipt(source="launched_app_server")
                         ["capabilities"]["typed_spawn_prompt_and_selectors"])

    def test_malformed_oversized_and_duplicate_frames(self) -> None:
        for bad in (b"{malformed}\n", b"{}\n", b'{"jsonrpc":"2.0","id":1,"id":2}\n',
                    b'{"jsonrpc":"1.0","id":1,"result":{}}\n',
                    b"x" * (probe.MAX_FRAME + 1) + b"\n", b"{}"):
            with self.subTest(size=len(bad)):
                with self.assertRaises(probe.ProbeFailure):
                    probe.decode_frame(bad)

    def test_event_budget_and_timeout(self) -> None:
        reducer = base_reducer()
        reducer.events = probe.MAX_EVENTS
        with self.assertRaisesRegex(probe.ProbeFailure, "event_limit"):
            reducer.feed(frame({"jsonrpc": "2.0", "method": "noise", "params": {}}))

        class SlowStream:
            def __init__(self):
                self.release = threading.Event()

            def readline(self, _: int) -> bytes:
                self.release.wait(1)
                return b""

        stream = SlowStream()
        process = FakeProcess([])
        process.stdout = stream
        try:
            with self.assertRaisesRegex(probe.ProbeFailure, "timeout"):
                probe.drive_protocol(process, probe.Reducer(
                    SECRET, requested_parent_model="gpt-6-sol", requested_parent_effort="low"), "prompt",
                                     "gpt-6-sol", "low", 0.02, "C:\\temp")
        finally:
            stream.release.set()

    def test_protocol_write_read_and_process_exit_have_fixed_stages(self) -> None:
        responses = [
            {"id": 1, "result": {"userAgent": "test"}},
            {"id": 2, "result": {"thread": {"id": "parent-1", "model": "gpt-6-sol"}}},
            {"id": 4, "result": {"data": [{"name": "multi_agent", "enabled": True}]}},
        ]

        class FailingOutput:
            def __init__(self, failure_write: int):
                self.failure_write = failure_write
                self.writes = 0

            def write(self, _: bytes) -> None:
                self.writes += 1
                if self.writes == self.failure_write:
                    raise BrokenPipeError(SECRET)

            def flush(self) -> None:
                pass

        for failure_write, stage in ((1, "initialize"), (3, "thread_start"),
                                     (5, "turn_start")):
            with self.subTest(stage=stage):
                process = FakeProcess(responses)
                process.stdin = FailingOutput(failure_write)
                reducer = probe.Reducer(SECRET, requested_parent_model="gpt-6-sol",
                                        requested_parent_effort="low")
                with self.assertRaises(probe.ProbeFailure) as raised:
                    probe.drive_protocol(process, reducer, "prompt", "gpt-6-sol", "low",
                                         1, "C:\\temp")
                self.assertEqual("protocol_write_failed", raised.exception.code)
                self.assertEqual(stage, raised.exception.stage)
                self.assertNotIn(SECRET, str(raised.exception))

        class BadStream:
            def readline(self, _: int) -> bytes:
                raise OSError(SECRET)

        process = FakeProcess([])
        process.stdout = BadStream()
        with self.assertRaises(probe.ProbeFailure) as raised:
            probe.drive_protocol(process, probe.Reducer(
                SECRET, requested_parent_model="gpt-6-sol", requested_parent_effort="low"),
                "prompt", "gpt-6-sol", "low", 1, "C:\\temp")
        self.assertEqual(("protocol_read_failed", "initialize"),
                         (raised.exception.code, raised.exception.stage))

        process = FakeProcess(responses[:1], exit_code=1)
        with self.assertRaises(probe.ProbeFailure) as raised:
            probe.drive_protocol(process, probe.Reducer(
                SECRET, requested_parent_model="gpt-6-sol", requested_parent_effort="low"),
                "prompt", "gpt-6-sol", "low", 1, "C:\\temp")
        self.assertEqual(("process_exited", "thread_start"),
                         (raised.exception.code, raised.exception.stage))

    @unittest.skipUnless(os.name == "nt", "run_live requires Windows")
    def test_post_start_failure_and_post_terminal_cleanup_keep_reduced_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as fixture:
            cli = Path(fixture) / "codex.exe"
            cli.write_bytes(b"fake executable")
            hook_config = Path(fixture) / "hooks.json"
            audit_script = Path(fixture) / "dispatch_audit.py"
            version = mock.Mock(returncode=0, stdout=b"codex-cli 0.144.1")

            def partial_drive(_process, reducer, *_args):
                for event in ({"id": 1, "result": {"userAgent": "test"}},
                              {"id": 2, "result": {"thread": {"id": "parent-1",
                                                          "model": "gpt-6-sol"}}},
                              {"id": 3, "result": {"turn": {"id": "turn-1"}}},
                              {"method": "item/completed", "params": {
                                  "threadId": "parent-1", "turnId": "turn-1",
                                  "item": {"type": "agentMessage", "id": "message-1",
                                           "phase": "final_answer", "text": SECRET}}}):
                    reducer.feed(frame(event))
                raise probe.ProbeFailure("protocol_read_failed", "turn_execution")

            process = FakeProcess([], exit_code=1)
            with (mock.patch.object(probe, "_validated_router_hook_source", return_value=HOOK_SOURCE),
                  mock.patch.object(probe, "_file_sha256", return_value="a" * 64),
                  mock.patch.object(probe.subprocess, "run", return_value=version),
                  mock.patch.object(probe.subprocess, "Popen", return_value=process),
                  mock.patch.object(probe, "drive_protocol", side_effect=partial_drive)):
                receipt = probe.run_live(cli, "parent prompt", SECRET, "gpt-6-sol", "low",
                                         1, None, hook_config, audit_script, [])
            self.assertEqual(("protocol_read_failed", "turn_execution"),
                             (receipt["failure"], receipt["failure_stage"]))
            self.assertEqual("parent-1", receipt["parent_thread_id"])
            self.assertTrue(receipt["diagnostics"]["item_event_type_counts"]["agentMessage"])
            self.assertFalse(receipt["capabilities"]["usable_for_dispatch_provenance"])
            self.assertNotIn(SECRET, json.dumps(receipt))

            process = FakeProcess([], exit_code=0, wait_error=OSError(SECRET))

            def terminal_drive(_process, reducer, *_args):
                for event in ({"id": 1, "result": {"userAgent": "test"}},
                              {"id": 2, "result": {"thread": {"id": "parent-1",
                                                          "model": "gpt-6-sol"}}},
                              {"id": 3, "result": {"turn": {"id": "turn-1"}}},
                              terminal_event()):
                    reducer.feed(frame(event))

            with (mock.patch.object(probe, "_validated_router_hook_source", return_value=HOOK_SOURCE),
                  mock.patch.object(probe, "_file_sha256", return_value="a" * 64),
                  mock.patch.object(probe.subprocess, "run", return_value=version),
                  mock.patch.object(probe.subprocess, "Popen", return_value=process),
                  mock.patch.object(probe, "drive_protocol", side_effect=terminal_drive)):
                receipt = probe.run_live(cli, "parent prompt", SECRET, "gpt-6-sol", "low",
                                         1, None, hook_config, audit_script, [])
            self.assertEqual(("teardown_failed", "teardown"),
                             (receipt["failure"], receipt["failure_stage"]))
            self.assertEqual("completed", receipt["turn_terminal"])
            self.assertEqual("parent-1", receipt["parent_thread_id"])
            self.assertFalse(receipt["capabilities"]["usable_for_dispatch_provenance"])
            self.assertNotIn(SECRET, json.dumps(receipt))

            with (mock.patch.object(probe, "_validated_router_hook_source", return_value=HOOK_SOURCE),
                  mock.patch.object(probe, "_file_sha256", return_value="a" * 64),
                  mock.patch.object(probe.subprocess, "run", return_value=version),
                  mock.patch.object(probe.subprocess, "Popen", side_effect=OSError(SECRET))):
                with self.assertRaises(probe.ProbeFailure) as raised:
                    probe.run_live(cli, "parent prompt", SECRET, "gpt-6-sol", "low",
                                   1, None, hook_config, audit_script, [])
            self.assertEqual(("spawn_failed", "spawn"),
                             (raised.exception.code, raised.exception.stage))
            self.assertNotIn(SECRET, str(raised.exception))

    def test_router_hook_config_requires_exact_audit_handlers(self) -> None:
        hook_dir = Path(__file__).resolve().parents[2] / "plugins" / "codex-model-router" / "hooks"
        source = probe._validated_router_hook_source(hook_dir / "hooks.json",
                                                     hook_dir / "dispatch_audit.py")
        self.assertEqual(str((hook_dir / "hooks.json").resolve()), source)
        with self.assertRaisesRegex(probe.ProbeFailure, "invalid_router_hook_identity"):
            probe._validated_router_hook_source(hook_dir / "hooks.json",
                                                 hook_dir / "router_hook.py")
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory)
            script = copied / "dispatch_audit.py"
            script.write_bytes((hook_dir / "dispatch_audit.py").read_bytes())
            config = json.loads((hook_dir / "hooks.json").read_text(encoding="utf-8"))
            for matcher in ("Agent", ".*spawn_agent.*", "Bash"):
                changed = json.loads(json.dumps(config))
                changed["hooks"]["PreToolUse"][0]["matcher"] = matcher
                config_path = copied / "hooks.json"
                config_path.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaisesRegex(probe.ProbeFailure, "invalid_router_hook_identity"):
                    probe._validated_router_hook_source(config_path, script)

    def test_hook_inventory_extra_managed_hook_is_reduced_and_blocks_live(self) -> None:
        workspace = r"C:\temp\hook-inventory"
        command = r'"C:\Python\python.exe" "C:\audit\dispatch_audit.py"'
        extra = {"key": r"C:\managed:pre_tool_use:0:0",
                 "currentHash": "sha256:" + "c" * 64, "eventName": "preToolUse",
                 "handlerType": "command", "matcher": ".*",
                 "command": "PRIVATE_PACKET_MARKER managed command",
                 "timeoutSec": 5, "sourcePath": r"C:\managed\hooks.json",
                 "source": "system", "pluginId": None, "displayOrder": 2,
                 "enabled": True, "isManaged": True, "trustStatus": "managed"}
        handlers, extras = probe._partition_inventory(
            inventory_response(workspace, command, extra=[extra]),
            workspace, command, trusted=False)
        self.assertEqual(2, len(handlers))
        self.assertEqual("yes", extras[0]["matcher_covers_spawn"])
        self.assertEqual("may_rewrite_or_deny", extras[0]["tool_effect"])
        self.assertTrue(extras[0]["is_managed"])
        self.assertNotIn(SECRET, json.dumps(extras))

    def test_fixed_managed_inventory_pin_rejects_drift_and_unknown_extras(self) -> None:
        pinned = []
        for event, (suffix, digest, matcher) in probe.DEFENDER_HOOK_PINS.items():
            pinned.append({"key": f"{probe.DEFENDER_HOOK_ROOT}:{suffix}:0:0",
                           "current_hash": "sha256:" + digest, "source": "system",
                           "source_path_sha256": probe.DEFENDER_SOURCE_PATH_SHA256,
                           "event": event, "matcher": matcher,
                           "handler_type": "command",
                           "command_sha256": probe.DEFENDER_COMMAND_SHA256,
                           "is_managed": True, "enabled": True,
                           "trust_status": "managed"})
        self.assertTrue(probe._fixed_defender_inventory_matches(pinned))
        for field, value in (("current_hash", "sha256:" + "0" * 64),
                             ("command_sha256", "0" * 64),
                             ("enabled", False), ("matcher", "Bash"),
                             ("source", "user")):
            changed = [dict(item) for item in pinned]
            changed[0][field] = value
            with self.subTest(field=field):
                self.assertFalse(probe._fixed_defender_inventory_matches(changed))
        self.assertFalse(probe._fixed_defender_inventory_matches(pinned + [dict(pinned[0])]))

    def test_fixed_sentinel_reduces_hook_context_without_claiming_effective_dispatch(self) -> None:
        reducer = probe.FixedSentinelReducer(SECRET, SESSION_SOURCE)
        for event in ({"id": 1, "result": {"userAgent": "test"}},
                      {"id": 2, "result": {"thread": {"id": "parent-1",
                                                  "model": "gpt-6-sol"}}},
                      {"id": 3, "result": {"turn": {"id": "turn-1"}}}):
            reducer.feed(frame(event))
        reducer.feed(frame({"method": "thread/started", "params": {"thread": {
            "id": "child-1", "parentThreadId": "parent-1", "model": "gpt-6-astra",
            "reasoningEffort": "xhigh"}}}))
        activity = subagent_activity_event()
        activity["params"]["item"]["id"] = "call-1"
        reducer.feed(frame(activity))
        digest = hashlib.sha256(SECRET.encode()).hexdigest()
        pre = {"schema_version": 1, "kind": "pre", "tool_name": "collaborationspawn_agent",
               "session_id": "parent-1", "turn_id": "turn-1", "attempt_id": "call-1",
               "native_name": probe.FIXED_SPAWN_NAME, "selector_state": "omitted",
               "model": None, "effort": None, "fork_turns": "none",
               "message_sha256": digest, "message_bytes": len(SECRET)}
        post = {"schema_version": 1, "kind": "post", "tool_name": "collaborationspawn_agent",
                "session_id": "parent-1", "turn_id": "turn-1", "attempt_id": "call-1",
                "child_id": "child-1", "result_observed": True,
                "post_observed_input": {"message_sha256": digest,
                                        "message_bytes": len(SECRET)}}
        for event_name, hook_id, record in (("preToolUse", "opaque:pre/id", pre),
                                            ("postToolUse", "opaque:post/id", post)):
            for method in ("hook/started", "hook/completed"):
                event = hook_event(method, event_name, hook_id,
                                   source="sessionFlags", sourcePath=SESSION_SOURCE,
                                   entries=[{"kind": "context", "text":
                                             probe.AUDIT_CONTEXT_PREFIX + json.dumps(record)}]
                                   if method == "hook/completed" else [])
                reducer.feed(frame(event))
        reducer.feed(frame(terminal_event()))
        receipt = reducer.receipt(source="launched_app_server")
        self.assertEqual("OBSERVED", receipt["capabilities"]["hook_context"])
        self.assertEqual("UNKNOWN", receipt["capabilities"]["effective_dispatch_proof"])
        self.assertTrue(receipt["audit_attempts"][0]["post_observed_input_match"])
        self.assertTrue(receipt["audit_attempts"][0]["native_activity_child_match"])
        self.assertNotIn(SECRET, json.dumps(receipt))
        malformed = hook_event("hook/completed", "preToolUse", "opaque:pre/id",
                               source="sessionFlags", sourcePath=SESSION_SOURCE,
                               entries=[{"kind": "context", "text":
                                         probe.AUDIT_CONTEXT_PREFIX + "{" + SECRET}])
        reducer.feed(frame(malformed))
        malformed_receipt = reducer.receipt(source="launched_app_server")
        self.assertGreater(malformed_receipt["diagnostics"]["audit_context_malformed"], 0)
        self.assertEqual("UNKNOWN", malformed_receipt["capabilities"]["hook_context"])

    def test_fixed_sentinel_reviewer_mutations_remain_unknown(self) -> None:
        baseline = fixed_context_reducer().receipt(source="launched_app_server")
        self.assertEqual("OBSERVED", baseline["capabilities"]["hook_context"])
        cases = (
            {"missing_native_name": True},
            {"native_name": "different_probe"},
            {"fork_turns": "all"},
            {"second_child": True},
            {"conflicting_child": True},
            {"child_business": True},
            {"child_followup": True},
            {"parent_followup": True},
            {"contradictory_selector": True},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                receipt = fixed_context_reducer(**changes).receipt(
                    source="launched_app_server")
                self.assertEqual("UNKNOWN", receipt["capabilities"]["hook_context"])
                self.assertEqual("UNKNOWN", receipt["capabilities"]["effective_dispatch_proof"])
                self.assertNotIn(SECRET, json.dumps(receipt))

    def test_fixed_sentinel_gate_and_live_process_drift_never_start_turn(self) -> None:
        with mock.patch.object(probe, "run_hook_inventory", return_value={
                "gates": {"ready_for_fixed_sentinel_probe": False}}), \
                mock.patch.object(probe, "_validated_canary_interpreter",
                                  return_value=Path(probe.sys.executable)), \
                mock.patch.object(probe.subprocess, "Popen") as popen:
            receipt = probe.run_fixed_sentinel_probe(Path("C:/codex.exe"), "a" * 64,
                Path("C:/dispatch_audit.py"), "b" * 64,
                Path("C:/astra-hard-kernel-role.toml"), "c" * 64, 1)
            self.assertEqual("fixed_inventory_gate_closed", receipt["failure"])
            popen.assert_not_called()
        process = FakeProcess([{"id": 1, "result": {"userAgent": "test"}},
                               {"id": 5, "result": {"data": []}}])
        reducer = probe.FixedSentinelReducer(SECRET, SESSION_SOURCE)
        with self.assertRaisesRegex(probe.ProbeFailure, "fixed_inventory_drift"):
            probe.drive_protocol(process, reducer, "parent prompt", "gpt-6-sol", "low",
                                 1, "C:\\temp", pre_thread_check=lambda _:
                                 (_ for _ in ()).throw(probe.ProbeFailure(
                                     "fixed_inventory_drift", "hook_live_preflight")))
        sent = [json.loads(line)["method"] for line in process.stdin.getvalue().splitlines()]
        self.assertEqual(["initialize", "initialized", "hooks/list"], sent)

    def test_fixed_sentinel_missing_hook_context_remains_unknown(self) -> None:
        reducer = probe.FixedSentinelReducer(SECRET, SESSION_SOURCE)
        for event in ({"id": 1, "result": {"userAgent": "test"}},
                      {"id": 2, "result": {"thread": {"id": "parent-1",
                                                  "model": "gpt-6-sol"}}},
                      {"id": 3, "result": {"turn": {"id": "turn-1"}}}):
            reducer.feed(frame(event))
        for event_name, hook_id in (("preToolUse", "opaque:pre/id"),
                                    ("postToolUse", "opaque:post/id")):
            reducer.feed(frame(hook_event("hook/started", event_name, hook_id,
                                          source="sessionFlags", sourcePath=SESSION_SOURCE,
                                          entries=[])))
            reducer.feed(frame(hook_event("hook/completed", event_name, hook_id,
                                          source="sessionFlags", sourcePath=SESSION_SOURCE,
                                          entries=[])))
        reducer.feed(frame(terminal_event()))
        receipt = reducer.receipt(source="launched_app_server")
        self.assertEqual("UNKNOWN", receipt["capabilities"]["hook_context"])
        self.assertEqual("UNKNOWN", receipt["capabilities"]["effective_dispatch_proof"])
        self.assertEqual([], receipt["audit_attempts"])

    def test_hook_inventory_rejects_unrelated_and_malformed_handlers(self) -> None:
        workspace = r"C:\temp\hook-inventory"
        command = r'"C:\Python\python.exe" "C:\audit\dispatch_audit.py"'
        wrong = inventory_response(workspace, command)
        wrong["data"][0]["hooks"][1]["command"] = "different command " + SECRET
        with self.assertRaisesRegex(probe.ProbeFailure, "inventory_extra_or_missing_hooks"):
            probe._partition_inventory(wrong, workspace, command, trusted=False)
        malformed = inventory_response(workspace, command)
        malformed["data"][0]["hooks"][0]["currentHash"] = "invalid " + SECRET
        with self.assertRaisesRegex(probe.ProbeFailure, "inventory_invalid_handler"):
            probe._partition_inventory(malformed, workspace, command, trusted=False)
        malformed = inventory_response(workspace, command)
        malformed["data"][0]["errors"] = [{"message": SECRET}]
        with self.assertRaisesRegex(probe.ProbeFailure, "inventory_load_warning"):
            probe._partition_inventory(malformed, workspace, command, trusted=False)

    @unittest.skipUnless(os.name == "nt", "inventory requires Windows")
    def test_hook_inventory_uses_discovered_keys_and_hashes_for_local_trust(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cli = root / "codex.exe"
            audit = root / "dispatch_audit.py"
            role = root / "astra-hard-kernel-role.toml"
            cli.write_bytes(b"fake cli")
            audit.write_text("# audited hook\n", encoding="utf-8")
            role.write_text('model = "gpt-6-astra"\nmodel_reasoning_effort = "xhigh"\n',
                            encoding="utf-8")
            digests = [hashlib.sha256(path.read_bytes()).hexdigest()
                       for path in (cli, audit, role)]
            command = probe._audit_hook_command(probe._windows_cmd_exe(),
                                                Path(probe.sys.executable).resolve(), audit)
            seen_overrides = []

            def fake_request(_cli, overrides, workspace, _timeout):
                seen_overrides.append(overrides)
                trusted = any(value.startswith("hooks.state=") for value in overrides)
                return inventory_response(workspace, command, trusted=trusted)

            version = mock.Mock(returncode=0, stdout=b"codex-cli 0.144.1")
            with (mock.patch.object(probe, "_inventory_request", side_effect=fake_request),
                  mock.patch.object(probe.subprocess, "run", return_value=version)):
                receipt = probe.run_hook_inventory(cli, digests[0], audit, digests[1], role,
                                                   digests[2], 2)
            self.assertTrue(receipt["gates"]["ready_for_hook_live"])
            self.assertEqual(probe._file_sha256(probe._windows_cmd_exe()),
                             receipt["cmd_sha256"])
            self.assertEqual(2, len(seen_overrides))
            self.assertFalse(any(value.startswith("hooks.state=")
                                 for value in seen_overrides[0]))
            state_values = [value for value in seen_overrides[1]
                            if value.startswith("hooks.state=")]
            self.assertEqual(1, len(state_values))
            self.assertIn("sha256:" + "a" * 64, state_values[0])
            self.assertIn("sha256:" + "b" * 64, state_values[0])
            self.assertTrue(all(handler["trust_status"] == "trusted"
                                for handler in receipt["handlers"]))
            self.assertNotIn("# audited hook", json.dumps(receipt))
            for field, replacement in (("key", "changed-key"),
                                       ("currentHash", "sha256:" + "c" * 64)):
                attempts = [0]

                def changed_request(_cli, _overrides, workspace, _timeout):
                    attempts[0] += 1
                    result = inventory_response(workspace, command,
                                                trusted=attempts[0] == 2)
                    if attempts[0] == 2:
                        result["data"][0]["hooks"][0][field] = replacement
                    return result

                with (mock.patch.object(probe, "_inventory_request",
                                        side_effect=changed_request),
                      mock.patch.object(probe.subprocess, "run", return_value=version)):
                    with self.subTest(field=field):
                        with self.assertRaisesRegex(probe.ProbeFailure,
                                                    "inventory_identity_changed"):
                            probe.run_hook_inventory(cli, digests[0], audit, digests[1], role,
                                                     digests[2], 2)

            managed_extra = {"key": r"C:\managed:pre_tool_use:0:0",
                             "currentHash": "sha256:" + "c" * 64,
                             "eventName": "preToolUse", "handlerType": "command",
                             "matcher": ".*", "command": "managed " + SECRET,
                             "timeoutSec": 5, "sourcePath": r"C:\managed\hooks.json",
                             "source": "system", "pluginId": None, "displayOrder": 2,
                             "enabled": True, "isManaged": True, "trustStatus": "managed"}

            def extra_request(_cli, overrides, workspace, _timeout):
                return inventory_response(workspace, command,
                                          trusted=any(value.startswith("hooks.state=")
                                                      for value in overrides),
                                          extra=[managed_extra])

            with (mock.patch.object(probe, "_inventory_request", side_effect=extra_request),
                  mock.patch.object(probe.subprocess, "run", return_value=version)):
                blocked = probe.run_hook_inventory(cli, digests[0], audit, digests[1], role,
                                                   digests[2], 2)
            self.assertFalse(blocked["gates"]["ready_for_hook_live"])
            self.assertFalse(blocked["gates"]["no_extra_tool_hooks"])
            self.assertNotIn(SECRET, json.dumps(blocked))

    @unittest.skipUnless(os.name == "nt", "Windows hook command requires Windows")
    def test_audit_hook_command_runs_synthetic_input_in_both_shells(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-hook-cmd-") as directory:
            root = Path(directory)
            script_dir = root / "audit-script"
            script_dir.mkdir()
            script = script_dir / "dispatch_audit.py"
            source = Path(__file__).resolve().parents[2] / "plugins" / "codex-model-router" / "hooks" / "dispatch_audit.py"
            script.write_bytes(source.read_bytes())
            cmd = probe._windows_cmd_exe()
            python = Path(probe.sys.executable).resolve()
            command = probe._audit_hook_command(cmd, python, script)
            powershell = shutil.which("pwsh.exe")
            self.assertIsNotNone(powershell)
            for phase in ("PreToolUse", "PostToolUse"):
                event = {"session_id": "parent-synthetic", "turn_id": "turn-synthetic",
                         "hook_event_name": phase, "tool_name": "collaborationspawn_agent",
                         "tool_use_id": "call-synthetic",
                         "tool_input": {"task_name": "capability_probe",
                                        "message": "synthetic packet", "fork_turns": "none"}}
                if phase == "PostToolUse":
                    event["tool_response"] = {"task_name": "/root/capability_probe"}
                for shell in ([str(cmd), "/d", "/c", command],
                              [powershell, "-NoProfile", "-NonInteractive", "-Command", command]):
                    with self.subTest(phase=phase, shell=Path(shell[0]).name):
                        result = probe.subprocess.run(shell, input=json.dumps(event),
                            capture_output=True, text=True,
                            env={**os.environ, "CODEX_MODEL_ROUTER_DISPATCH_AUDIT": "1"})
                        self.assertEqual(0, result.returncode)
                        self.assertIn(probe.AUDIT_CONTEXT_PREFIX, result.stdout)
                        self.assertNotIn("synthetic packet", result.stdout + result.stderr)

            for unsafe_name in ("path with spaces", "bad%name"):
                unsafe_dir = root / unsafe_name
                unsafe_dir.mkdir()
                unsafe_script = unsafe_dir / "dispatch_audit.py"
                unsafe_script.write_bytes(script.read_bytes())
                with self.assertRaisesRegex(probe.ProbeFailure, "inventory_invalid_hook_command"):
                    probe._audit_hook_command(cmd, python, unsafe_script)
            with self.assertRaisesRegex(probe.ProbeFailure, "inventory_invalid_hook_command"):
                probe._audit_hook_command(cmd, python, root / "missing" / "dispatch_audit.py")


if __name__ == "__main__":
    unittest.main()
