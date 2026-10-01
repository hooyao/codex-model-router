"""Offline checks for selective-Astra paired evidence gates."""
from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from evals.scripts.collect_selective_pair import (check_stage_plan, inspect_arm, native_spawns,
                                                  pair_status,
                                                  paired_cost_ratio, paired_cost_ratio_interval,
                                                  require_allowed_selectors, validated_grade,
                                                  structural_role_attempts,
                                                  validate_prompt_evidence)
from evals.scripts.run_paired_arm import (SessionMeter, explicit_dispatch_preflight,
                                          native_lineage, pinned_cli, pinned_prompt,
                                          submitted_prompt, sha256,
                                          telemetry_unavailable)


class SelectivePairTests(unittest.TestCase):
    def test_versioned_treatment_authorization_preserves_task_and_fails_closed(self) -> None:
        config_path = (Path(__file__).resolve().parents[1] /
                       "paired_selective_lifecycle_v1" / "config.json")
        config = json.loads(config_path.read_text(encoding="utf-8"))
        task = pinned_prompt(config_path, config)
        treatment = submitted_prompt(config_path, config, "treatment")
        self.assertEqual(config["prompt"]["combined_sha256"],
                         hashlib.sha256(task.encode()).hexdigest())
        self.assertEqual(task, submitted_prompt(config_path, config, "baseline"))
        self.assertTrue(treatment.startswith(task.rstrip("\n") + "\n\n"))
        self.assertIn("I authorize native subagent delegation", treatment)
        self.assertIn("benchmark-hard-kernel-gpt-6-astra-xhigh", treatment)
        self.assertEqual("lifecycle-v1-selective-stage-plan-v4",
                         config["treatment_execution"]["version"])
        self.assertEqual(config["treatment_execution"]["authorization_suffix_sha256"],
                         hashlib.sha256(config["treatment_execution"][
                             "authorization_suffix"].encode()).hexdigest())
        self.assertIn("maintainer-docs", treatment)
        self.assertIn("verify-and-synthesize", treatment)
        self.assertIn("exclusive write scope", treatment)
        tampered = json.loads(json.dumps(config))
        tampered["treatment_execution"]["authorization_suffix"] += " extra"
        with self.assertRaisesRegex(ValueError, "authorization hash mismatch"):
            submitted_prompt(config_path, tampered, "treatment")
        unsupported = json.loads(json.dumps(config))
        unsupported["treatment_execution"]["version"] = "unknown"
        with self.assertRaisesRegex(ValueError, "unsupported treatment execution"):
            submitted_prompt(config_path, unsupported, "treatment")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "prompt.md").write_text(treatment, encoding="utf-8", newline="\n")
            record = {"task_prompt_sha256": config["prompt"]["combined_sha256"],
                      "prompt_sha256": hashlib.sha256(treatment.encode()).hexdigest(),
                      "treatment_execution_version": config["treatment_execution"]["version"]}
            validate_prompt_evidence(config_path, config, root, record, "treatment")
            for field, value, error in (("task_prompt_sha256", "0" * 64, "frozen task"),
                                        ("prompt_sha256", "0" * 64, "submitted prompt"),
                                        ("treatment_execution_version", "other", "version")):
                damaged = dict(record, **{field: value})
                with self.assertRaisesRegex(ValueError, error):
                    validate_prompt_evidence(config_path, config, root, damaged, "treatment")
            (root / "prompt.md").write_text(task, encoding="utf-8", newline="\n")
            with self.assertRaisesRegex(ValueError, "saved prompt"):
                validate_prompt_evidence(config_path, config, root, record, "treatment")

    def test_explicit_route_requires_reviewed_cli_and_source_hashes(self) -> None:
        config_path = Path(__file__).resolve().parents[1] / "paired_selective" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        cli = Path(config["dispatch_runtime"]["cli"])
        if not cli.is_file():
            self.skipTest("reviewed desktop CLI is unavailable on this host")
        evidence_root = config_path.resolve().parents[2]
        if any(not (evidence_root / config["dispatch_runtime"][key]["path"]).is_file()
               for key in ("schema_capture", "spawn_schema", "model_catalog")):
            self.skipTest("reviewed schema capture artifacts are unavailable in this checkout")
        self.assertEqual(config["dispatch_runtime"]["cli_sha256"],
                         pinned_cli(cli, config)["sha256"])
        self.assertEqual("ready", explicit_dispatch_preflight(config_path, config, cli)["status"])
        damaged = json.loads(json.dumps(config))
        damaged["dispatch_runtime"]["spawn_schema"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "spawn_schema hash mismatch"):
            explicit_dispatch_preflight(config_path, damaged, cli)
        damaged = json.loads(json.dumps(config))
        damaged["dispatch_runtime"]["cli_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "CLI path or hash"):
            pinned_cli(cli, damaged)

    def test_nested_native_activity_joins_exact_child_selectors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent = root / "parent.jsonl"
            child = {"id": "child", "parent_id": "parent", "agent_path": "/root/hard_worker",
                     "turns": [{"model": "gpt-6-astra", "effort": "xhigh"}]}
            events = [
                {"type": "turn_context", "payload": {"turn_id": "turn-one"}},
                {"type": "response_item", "payload": {"type": "function_call",
                    "name": "spawn_agent", "call_id": "call_one",
                    "arguments": json.dumps({"task_name": "hard_worker", "model": "gpt-6-astra",
                                             "reasoning_effort": "xhigh", "fork_turns": "none",
                                             "message": "fixture-spawn"})}},
                {"type": "event_msg", "payload": {"type": "item_completed", "item": {
                    "type": "SubAgentActivity", "kind": "started", "id": "call_one",
                    "agent_thread_id": "child", "agent_path": "/root/hard_worker"}}},
                {"type": "response_item", "payload": {"type": "function_call_output",
                    "call_id": "call_one", "output": json.dumps({"task_name": "/root/hard_worker"})}},
            ]
            parent.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
            paths = {parent: {"id": "parent", "parent_id": None}, root / "child.jsonl": child}
            calls, issues = native_lineage(parent, paths, "parent")
            self.assertEqual([], issues)
            self.assertEqual("child", calls[0]["child_id"])
            child["turns"][0]["effort"] = "high"
            _, issues = native_lineage(parent, paths, "parent")
            self.assertIn("native call/start/result/child selectors do not join", issues)

    def test_native_lineage_rejects_adversarial_activity_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent = root / "parent.jsonl"
            child = {"id": "child", "parent_id": "parent", "agent_path": "/root/hard_worker",
                     "turns": [{"model": "gpt-6-astra", "effort": "xhigh"}]}
            fixture = [
                {"type": "session_meta", "payload": {"id": "parent"}},
                {"type": "turn_context", "payload": {"turn_id": "turn-one"}},
                {"type": "response_item", "payload": {"type": "function_call",
                    "name": "spawn_agent", "namespace": "collaboration", "call_id": "call-one",
                    "arguments": json.dumps({"task_name": "hard_worker", "model": "gpt-6-astra",
                                             "reasoning_effort": "xhigh", "fork_turns": "none",
                                             "message": "fixture-spawn"})}},
                {"type": "event_msg", "payload": {"type": "item_completed",
                    "thread_id": "parent", "turn_id": "turn-one", "item": {
                    "type": "SubAgentActivity", "kind": "started", "id": "call-one",
                    "agent_thread_id": "child", "agent_path": "/root/hard_worker"}}},
                {"type": "response_item", "payload": {"type": "function_call_output",
                    "call_id": "call-one", "output": json.dumps({"task_name": "/root/hard_worker"})}},
                {"type": "event_msg", "payload": {"type": "item_completed",
                    "thread_id": "parent", "turn_id": "turn-one", "item": {
                    "type": "SubAgentActivity", "kind": "completed", "id": "completion-one",
                    "agent_thread_id": "child", "agent_path": "/root/hard_worker"}}},
            ]
            paths = {parent: {"id": "parent", "parent_id": None}, root / "child.jsonl": child}
            def run(events: list[dict]) -> list[str]:
                parent.write_text("".join(json.dumps(item) + "\n" for item in events),
                                  encoding="utf-8")
                return native_lineage(parent, paths, "parent")[1]
            self.assertEqual([], run(fixture))
            interacted = json.loads(json.dumps(fixture))
            interacted.insert(5, {"type": "response_item", "payload": {
                "type": "function_call", "name": "send_message", "namespace": "collaboration",
                "call_id": "interaction-one", "arguments": json.dumps({
                    "target": "hard_worker", "message": "fixture-send"})}})
            interacted.insert(6, {"type": "event_msg", "payload": {
                "type": "item_completed", "thread_id": "parent", "turn_id": "turn-one",
                "item": {"type": "SubAgentActivity", "kind": "interacted",
                         "id": "interaction-one", "agent_thread_id": "child",
                         "agent_path": "/root/hard_worker"}}})
            interacted.insert(7, {"type": "response_item", "payload": {
                "type": "function_call_output", "call_id": "interaction-one", "output": ""}})
            self.assertEqual([], run(interacted))
            interacted[6]["payload"]["item"]["agent_thread_id"] = "unknown"
            self.assertIn("native child activity differs from call context", run(interacted))
            for mutation in ("spawn_parent", "spawn_turn", "start_parent", "start_turn",
                             "start_item_parent", "completion_parent", "completion_turn",
                             "completion_item_turn", "namespace", "unknown_child",
                             "wrong_path", "duplicate_completion", "later_turn_start",
                             "later_turn_completion", "later_parent_start",
                             "later_parent_completion"):
                with self.subTest(mutation=mutation):
                    events = json.loads(json.dumps(fixture))
                    if mutation == "spawn_parent":
                        events[2]["payload"]["session_id"] = "other-parent"
                    elif mutation == "spawn_turn":
                        events[2]["payload"]["turn_id"] = "other-turn"
                    elif mutation == "start_parent":
                        events[3]["payload"]["thread_id"] = "other-parent"
                    elif mutation == "start_turn":
                        events[3]["payload"]["turn_id"] = "other-turn"
                    elif mutation == "start_item_parent":
                        events[3]["payload"]["item"]["thread_id"] = "other-parent"
                    elif mutation == "completion_parent":
                        events[5]["payload"]["thread_id"] = "other-parent"
                    elif mutation == "completion_turn":
                        events[5]["payload"]["turn_id"] = "other-turn"
                    elif mutation == "completion_item_turn":
                        events[5]["payload"]["item"]["turn_id"] = "other-turn"
                    elif mutation == "namespace":
                        events[2]["payload"]["namespace"] = "other"
                    elif mutation == "unknown_child":
                        events[5]["payload"]["item"]["agent_thread_id"] = "unknown"
                    elif mutation == "wrong_path":
                        events[5]["payload"]["item"]["agent_path"] = "/root/other"
                    elif mutation == "duplicate_completion":
                        events.append(json.loads(json.dumps(events[5])))
                        events[6]["payload"]["item"]["id"] = "completion-two"
                    elif mutation.startswith("later_turn"):
                        index = 3 if mutation.endswith("start") else 5
                        events.insert(index, {"type": "turn_context",
                            "payload": {"turn_id": "other-turn"}})
                        events[index + 1]["payload"]["turn_id"] = "other-turn"
                    else:
                        index = 3 if mutation.endswith("start") else 5
                        events.insert(index, {"type": "session_meta",
                            "payload": {"id": "other-parent"}})
                        events[index + 1]["payload"]["thread_id"] = "other-parent"
                    self.assertTrue(run(events), mutation)

    def test_observed_v2_smoke_lineage_remains_valid(self) -> None:
        fixture = json.loads((Path(__file__).parent / "fixtures" /
                              "v2-dispatch-observed.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent = root / "parent.jsonl"
            parent.write_text("".join(json.dumps(item) + "\n" for item in fixture["native"]),
                              encoding="utf-8")
            child = fixture["children"][0]
            paths = {parent: {"id": fixture["parent_id"], "parent_id": None},
                     root / "child.jsonl": child}
            calls, issues = native_lineage(parent, paths, fixture["parent_id"])
            self.assertEqual([], issues)
            self.assertEqual(child["id"], calls[0]["child_id"])
            self.assertEqual(6, calls[0]["completion_line"])

    def test_sanitized_observed_multiturn_lineage(self) -> None:
        fixture = json.loads((Path(__file__).parent / "fixtures" /
                              "native-multiturn-observed-sanitized.json").read_text(
                                  encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent = root / "parent.jsonl"
            child = root / "child.jsonl"
            paths = {parent: {"id": fixture["parent_id"], "parent_id": None},
                     child: {"id": fixture["child_id"], "parent_id": fixture["parent_id"],
                             "agent_path": fixture["agent_path"]}}

            def run(rows: dict, *, closed: bool = True) -> tuple[list[dict], list[str]]:
                parent.write_text("".join(json.dumps(item) + "\n" for item in rows["native"]),
                                  encoding="utf-8")
                child.write_text("".join(json.dumps(item) + "\n" for item in rows["child"]),
                                 encoding="utf-8")
                return native_lineage(parent, paths, fixture["parent_id"],
                                      closed_snapshot=closed)

            sessions, issues = run(fixture)
            self.assertEqual([], issues)
            self.assertEqual(1, len(sessions))
            self.assertEqual(["child-turn-1", "child-turn-2"],
                             [turn["turn_id"] for turn in sessions[0]["turns"]])
            self.assertEqual(["spawn_agent", "followup_task"],
                             [turn["trigger_kind"] for turn in sessions[0]["turns"]])
            self.assertEqual(["task_complete", "task_complete"],
                             [turn["terminal"] for turn in sessions[0]["turns"]])
            self.assertEqual(1, len(sessions[0]["turns"][0]["interactions"]))
            serialized = json.dumps(sessions)
            self.assertNotIn("fixture-only-encrypted", serialized)

            later_parent = json.loads(json.dumps(fixture))
            later_parent["native"].insert(9, {"type": "turn_context",
                                                "payload": {"turn_id": "parent-turn-2"}})
            for index in (10, 12):
                later_parent["native"][index]["payload"][
                    "internal_chat_message_metadata_passthrough"]["turn_id"] = "parent-turn-2"
            for index in (11, 13):
                later_parent["native"][index]["payload"]["turn_id"] = "parent-turn-2"
            for index in (8, 9):
                later_parent["child"][index]["payload"]["root_turn_id"] = "parent-turn-2"
            sessions, issues = run(later_parent)
            self.assertEqual([], issues)
            self.assertEqual(["parent-turn-1", "parent-turn-2"],
                             [turn["parent_turn_id"] for turn in sessions[0]["turns"]])
            stale_root = json.loads(json.dumps(later_parent))
            for index in (8, 9):
                stale_root["child"][index]["payload"]["root_turn_id"] = "parent-turn-1"
            self.assertIn("native child root turn differs from trigger parent turn",
                          run(stale_root)[1])
            for field, value in (("thread_id", "other-parent"),
                                 ("turn_id", "other-parent-turn")):
                with self.subTest(result_field=field):
                    wrong_result = json.loads(json.dumps(later_parent))
                    wrong_result["native"][12]["payload"][field] = value
                    self.assertIn("native call result missing activity or parent context",
                                  run(wrong_result)[1])

            for mutation in (
                "missing_spawn_start", "missing_spawn_result", "missing_send_activity",
                "missing_followup", "missing_followup_result", "missing_first_completion",
                "missing_second_completion", "duplicate_completion", "wrong_completion_suffix",
                "wrong_completion_parent_turn", "wrong_followup_metadata",
                "wrong_child_parent", "wrong_child_path", "wrong_child_root_turn",
                "wrong_child_context_turn", "wrong_child_message_turn", "wrong_selector",
                "wrong_spawn_digest", "wrong_followup_digest", "missing_trigger",
                "false_followup_trigger", "missing_child_context", "missing_child_terminal",
                "duplicate_child_terminal", "extra_child_turn", "duplicate_call_id",
                "wrong_followup_target", "cross_child_activity", "orphan_followup",
                "missing_parent_meta", "missing_call_metadata", "missing_result_metadata",
                "wrong_namespace", "unsupported_activity", "unsupported_trigger",
                "wrong_child_spawn_source", "duplicate_parent_call_id",
            ):
                with self.subTest(mutation=mutation):
                    rows = json.loads(json.dumps(fixture))
                    p, c = rows["native"], rows["child"]
                    if mutation == "missing_spawn_start": p.pop(3)
                    elif mutation == "missing_spawn_result": p.pop(4)
                    elif mutation == "missing_send_activity": p.pop(6)
                    elif mutation == "missing_followup": p.pop(9)
                    elif mutation == "missing_followup_result": p.pop(11)
                    elif mutation == "missing_first_completion": p.pop(8)
                    elif mutation == "missing_second_completion": p.pop(12)
                    elif mutation == "duplicate_completion": p.append(json.loads(json.dumps(p[12])))
                    elif mutation == "wrong_completion_suffix":
                        p[12]["payload"]["item"]["id"] = "subagent-completed-other-turn"
                    elif mutation == "wrong_completion_parent_turn":
                        p[12]["payload"]["turn_id"] = "other-parent-turn"
                    elif mutation == "wrong_followup_metadata":
                        p[9]["payload"]["internal_chat_message_metadata_passthrough"][
                            "turn_id"] = "other-parent-turn"
                    elif mutation == "wrong_child_parent":
                        c[0]["payload"]["parent_thread_id"] = "other-parent"
                    elif mutation == "wrong_child_path":
                        c[0]["payload"]["agent_path"] = "/root/other"
                    elif mutation == "wrong_child_root_turn":
                        c[8]["payload"]["root_turn_id"] = "other-parent-turn"
                    elif mutation == "wrong_child_context_turn":
                        c[9]["payload"]["turn_id"] = "other-child-turn"
                    elif mutation == "wrong_child_message_turn":
                        c[11]["payload"]["internal_chat_message_metadata_passthrough"][
                            "turn_id"] = "other-child-turn"
                    elif mutation == "wrong_selector": c[9]["payload"]["effort"] = "low"
                    elif mutation == "wrong_spawn_digest":
                        c[4]["payload"]["content"][1]["encrypted_content"] = "other-input"
                    elif mutation == "wrong_followup_digest":
                        c[11]["payload"]["content"][1]["encrypted_content"] = "other-input"
                    elif mutation == "missing_trigger": c.pop(10)
                    elif mutation == "false_followup_trigger":
                        c[10]["payload"]["trigger_turn"] = False
                    elif mutation == "missing_child_context": c.pop(9)
                    elif mutation == "missing_child_terminal": c.pop(12)
                    elif mutation == "duplicate_child_terminal": c.append(json.loads(json.dumps(c[12])))
                    elif mutation == "extra_child_turn":
                        c.append({"type": "event_msg", "payload": {"type": "task_started",
                            "turn_id": "orphan-turn", "root_turn_id": "parent-turn-1"}})
                    elif mutation == "duplicate_call_id": p[9]["payload"]["call_id"] = "spawn-1"
                    elif mutation == "wrong_followup_target":
                        args = json.loads(p[9]["payload"]["arguments"])
                        args["target"] = "other"
                        p[9]["payload"]["arguments"] = json.dumps(args)
                    elif mutation == "cross_child_activity":
                        p[10]["payload"]["item"]["agent_thread_id"] = "other-child"
                    elif mutation == "orphan_followup": p.insert(9, p.pop(12))
                    elif mutation == "missing_parent_meta": p.pop(0)
                    elif mutation == "missing_call_metadata":
                        del p[9]["payload"]["internal_chat_message_metadata_passthrough"]
                    elif mutation == "missing_result_metadata":
                        del p[11]["payload"]["internal_chat_message_metadata_passthrough"]
                    elif mutation == "wrong_namespace":
                        p[9]["payload"]["namespace"] = "other"
                    elif mutation == "unsupported_activity":
                        p[10]["payload"]["item"]["kind"] = "resumed"
                    elif mutation == "unsupported_trigger":
                        c[10]["payload"]["trigger_turn"] = "true"
                    elif mutation == "wrong_child_spawn_source":
                        c[0]["payload"]["source"]["subagent"]["thread_spawn"][
                            "parent_thread_id"] = "other-parent"
                    elif mutation == "duplicate_parent_call_id":
                        p.insert(9, json.loads(json.dumps(p[5])))
                    self.assertTrue(run(rows)[1], mutation)

            prefix = json.loads(json.dumps(fixture))
            prefix["native"].pop(12)
            prefix["child"].pop(12)
            self.assertEqual([], run(prefix, closed=False)[1])
            self.assertTrue(run(prefix, closed=True)[1])
            early_prefix = json.loads(json.dumps(fixture))
            early_prefix["native"] = early_prefix["native"][:5]
            early_prefix["child"] = early_prefix["child"][:4]
            self.assertEqual([], run(early_prefix, closed=False)[1])
            racing_prefix = json.loads(json.dumps(fixture))
            racing_prefix["native"] = racing_prefix["native"][:9]
            racing_prefix["child"] = racing_prefix["child"][:9]
            self.assertEqual([], run(racing_prefix, closed=False)[1])
            self.assertTrue(run(racing_prefix, closed=True)[1])
            child.unlink()
            self.assertIn("native child rollout missing", native_lineage(
                parent, paths, fixture["parent_id"], closed_snapshot=True)[1])

    def test_sanitized_observed_abort_requires_exact_drain_proof(self) -> None:
        fixture = json.loads((Path(__file__).parent / "fixtures" /
                              "native-abort-observed-sanitized.json").read_text(
                                  encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent, child = root / "parent.jsonl", root / "child.jsonl"
            def run(rows: dict, proof: dict | None) -> tuple[list[dict], list[str]]:
                parent.write_text("".join(json.dumps(item) + "\n" for item in rows["native"]),
                                  encoding="utf-8")
                child.write_text("".join(json.dumps(item) + "\n" for item in rows["child"]),
                                 encoding="utf-8")
                states = rows["states"]
                paths = {parent: {"id": rows["parent_id"], "parent_id": None,
                                  **states["parent"]},
                         child: {"id": rows["child_id"], "parent_id": rows["parent_id"],
                                 "agent_path": rows["agent_path"], **states["child"]}}
                return native_lineage(parent, paths, rows["parent_id"],
                                      closed_snapshot=True, interruption_proof=proof)

            sessions, issues = run(fixture, fixture["interruption_proof"])
            self.assertEqual([], issues)
            self.assertEqual("interrupted", sessions[0]["turns"][0]["closure"])
            self.assertNotIn("completion_line", sessions[0]["turns"][0])
            self.assertIn("native aborted turn lacks verified interrupt and usage proof",
                          run(fixture, None)[1])
            for mutation in (
                "missing_ack", "missing_request", "wrong_child_request", "wrong_parent_request",
                "wrong_request_order", "wrong_child_terminal_reason", "missing_child_terminal",
                "wrong_parent_terminal_reason", "missing_parent_terminal", "missing_usage_drain",
                "wrong_usage_calls", "missing_usage_session", "wrong_usage_selector",
                "unreconciled_usage", "normal_completion_on_abort", "early_child_terminal",
                "wrong_cancellation_turn", "late_marker", "negative_completion_check",
            ):
                with self.subTest(mutation=mutation):
                    rows = json.loads(json.dumps(fixture))
                    proof = rows["interruption_proof"]
                    cancel = proof["cancellation"]
                    if mutation == "missing_ack": cancel["interrupt_ack"] = False
                    elif mutation == "missing_request": cancel["interrupt_requests"].pop()
                    elif mutation == "wrong_child_request":
                        cancel["interrupt_requests"][0]["turn_id"] = "other-turn"
                    elif mutation == "wrong_parent_request":
                        cancel["interrupt_requests"][1]["thread_id"] = "other-parent"
                    elif mutation == "wrong_request_order":
                        cancel["interrupt_requests"].reverse()
                    elif mutation == "wrong_child_terminal_reason":
                        rows["child"][5]["payload"]["reason"] = "other"
                    elif mutation == "missing_child_terminal": rows["child"].pop(5)
                    elif mutation == "wrong_parent_terminal_reason":
                        rows["native"][5]["payload"]["reason"] = "other"
                    elif mutation == "missing_parent_terminal": rows["native"].pop(5)
                    elif mutation == "missing_usage_drain": cancel["usage_drained"] = False
                    elif mutation == "wrong_usage_calls":
                        proof["usage"]["sessions"][1]["calls"] = 2
                    elif mutation == "missing_usage_session":
                        proof["usage"]["sessions"].pop()
                    elif mutation == "wrong_usage_selector":
                        proof["usage"]["sessions"][1]["effort"] = "low"
                    elif mutation == "unreconciled_usage":
                        rows["states"]["child"]["last_total_usage"]["input_tokens"] = 99
                    elif mutation == "normal_completion_on_abort":
                        rows["native"].insert(5, {"type": "event_msg", "payload": {
                            "type": "item_completed", "thread_id": rows["parent_id"],
                            "turn_id": "abort-parent-turn", "item": {"type": "SubAgentActivity",
                                "kind": "completed", "id": "subagent-completed-abort-child-turn",
                                "agent_thread_id": rows["child_id"],
                                "agent_path": rows["agent_path"]}}})
                    elif mutation == "early_child_terminal":
                        rows["child"][5]["timestamp"] = "2030-01-01T00:00:00.900000Z"
                    elif mutation == "wrong_cancellation_turn":
                        cancel["turn_id"] = "wrong-turn"
                    elif mutation == "late_marker":
                        cancel["interrupt_requests"][0]["pre_dispatch_evidence"][
                            "marker_observed_ns"] = (
                            cancel["interrupt_requests"][0]["dispatch_time_ns"] + 1)
                    elif mutation == "negative_completion_check":
                        cancel["interrupt_requests"][0]["pre_dispatch_evidence"][
                            "completion_absent_checked_ns"] = -1
                    self.assertTrue(run(rows, proof)[1], mutation)

    def test_retained_real_canary_abort_replay_when_available(self) -> None:
        receipt = (Path(__file__).resolve().parents[1] / "long_horizon_v1" /
                   "_scratch" / "cancellation-canary" / "retry-20260930120001-96e403dd" /
                   "canary" / "cancellation.json")
        root = Path.home() / ".codex" / "sessions" / "2026" / "09" / "30"
        parent_id = "01a0f231-1446-7130-8a69-4c3aa87b17f7"
        if not receipt.is_file() or not list(root.glob(f"rollout-*{parent_id}.jsonl")):
            self.skipTest("retained canary evidence is unavailable on this host")
        proof = json.loads(receipt.read_text(encoding="utf-8"))
        meter = SessionMeter(root, parent_id, "gpt-6-sol", "low", True)
        meter.refresh()
        parent = next(path for path, state in meter.paths.items()
                      if state["id"] == parent_id)
        sessions, issues = native_lineage(parent, meter.paths, parent_id,
                                          closed_snapshot=True, interruption_proof=proof)
        self.assertEqual([], issues)
        self.assertEqual(1, len(sessions))
        self.assertEqual("interrupted", sessions[0]["turns"][0]["closure"])

    def test_negative_treatment_is_hash_bound_and_never_selective_success(self) -> None:
        config_path = Path(__file__).resolve().parents[1] / "paired_selective" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calibration_root = root / "calibration"
            calibration_root.mkdir()
            trial = calibration_root / "trial.json"
            trial.write_text("{}", encoding="utf-8")
            criteria = {key: False for key in ("decomposition", "dependencies", "dispatch",
                                               "receipts", "verification", "recovery")}
            calibration = {"schema_version": 1, "observations": [
                {"model": item["model"], "effort": item["effort"], "terminal": "completed",
                 "evidence_path": "trial.json", "evidence_sha256": sha256(trial),
                 "criteria": criteria}
                for item in config["arms"]["treatment"]["controller_candidates"]],
                "selected": None}
            calibration_path = calibration_root / "calibration.json"
            calibration_path.write_text(json.dumps(calibration), encoding="utf-8")
            prompt = root / "prompt.md"
            prompt.write_text(pinned_prompt(config_path, config), encoding="utf-8", newline="\n")
            transcript = root / "codex.jsonl"
            transcript.write_text('{"type":"turn.failed"}\n', encoding="utf-8")
            record = {"arm": "treatment", "config_sha256": sha256(config_path),
                      "initial_tree": config["fixture"]["initial_tree"],
                      "initial_diff_sha256": hashlib.sha256(b"").hexdigest(),
                      "prompt_sha256": config["prompt"]["combined_sha256"],
                      "transcript_sha256": sha256(transcript), "wall_seconds": 10,
                      "started_at": "2026-09-24T00:00:00+00:00",
                      "ended_at": "2026-09-24T00:00:10+00:00",
                      "command": ["codex", "exec", "-m", "gpt-6-sol", "-c",
                                  'model_reasoning_effort="low"'],
                      "model": "gpt-6-sol", "effort": "low",
                      "stop_reason": "budget-safety-stop", "exit_code": 1,
                      "cost_status": "partial-or-unknown", "parent_session_id": None,
                      "session_files": [], "workspace": str(root / "workspace"),
                      "cli_version": "codex-cli 0.144.1",
                      "pricing_source": config["pricing"]["source"],
                      "pricing_tier": config["pricing"]["tier"],
                      "plugin_manifest_sha256": config["plugin"]["installed_manifest_sha256"],
                      "calibration_status": "CALIBRATION_FAILED",
                      "controller_provenance": {"source": "operator-explicit-negative-treatment",
                                                "model": "gpt-6-sol", "effort": "low"},
                      "calibration_ref": {"path": "calibration/calibration.json",
                                          "sha256": sha256(calibration_path)},
                      "calibration_sha256": sha256(calibration_path)}
            run = root / "run.json"
            run.write_text(json.dumps(record), encoding="utf-8")
            report = inspect_arm(config_path, config, root, "treatment")
            self.assertEqual("CALIBRATION_FAILED", report["calibration_status"])
            self.assertEqual("failed", report["outcome"])
            self.assertEqual("CALIBRATION_FAILED", pair_status(True, report))
            record["calibration_sha256"] = "0" * 64
            run.write_text(json.dumps(record), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "original calibration hash mismatch"):
                inspect_arm(config_path, config, root, "treatment")
            record["calibration_sha256"] = sha256(calibration_path)
            record["controller_provenance"]["model"] = "gpt-6-luna"
            run.write_text(json.dumps(record), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "provenance mismatch"):
                inspect_arm(config_path, config, root, "treatment")

    def test_frozen_prompt_and_assets_are_bound(self) -> None:
        config_path = Path(__file__).resolve().parents[1] / "paired_selective" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        prompt = pinned_prompt(config_path, config)
        self.assertIn("OCI storage cannot authenticate against AWS ECR registries", prompt)
        self.assertIn("SHA-256 commitment", prompt)
        self.assertEqual("20121a7049c58571664b87989d1fc7bab8562884",
                         config["fixture"]["initial_tree"])

    def test_scope_audit_requires_one_bounded_astra_stage_and_disjoint_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            hard = "design-ecr-gpt-6-astra-xhigh"
            packet = f"Worker name: {hard}\nTask ID: {hard}\n" + "Implement the bounded auth kernel. " * 5
            digest = hashlib.sha256(packet.encode()).hexdigest()
            plan = {"schema_version": 1, "stages": [
                {"id": "fixtures", "owner": "controller", "dependencies": [],
                 "write_scope": ["internal/config/testdata/storage"],
                 "acceptance": "examples load", "packet_sha256": None, "packet_bytes": None},
                {"id": "kernel", "owner": hard, "dependencies": ["fixtures"],
                 "write_scope": ["internal/oci/ecr", "internal/oci/options.go"],
                 "acceptance": "AWS auth semantics pass", "packet_sha256": digest,
                 "packet_bytes": len(packet.encode())}]}
            path.write_text(json.dumps(plan), encoding="utf-8")
            child_path = Path(directory) / "child.jsonl"
            child_path.write_text(json.dumps({"type": "event_msg", "payload": {
                "type": "task_complete", "last_agent_message": "Packet SHA-256: " +
                digest}}) + "\n",
                encoding="utf-8")
            sessions = [{"id": "parent", "model": "gpt-6-sol", "effort": "low",
                         "agent_path": "/root"},
                        {"id": "child", "model": "gpt-6-astra", "effort": "xhigh",
                         "agent_path": "/root/design_ecr_gpt_6_astra_xhigh",
                         "start_timestamp": "2026-09-24T00:00:00+00:00",
                         "session_path": child_path}]
            report = check_stage_plan(path, sessions, "parent")
            self.assertEqual("kernel", report["hard_kernel_stage_id"])
            self.assertEqual("UNKNOWN", report["status"])
            plan["stages"][1]["packet"] = packet
            path.write_text(json.dumps(plan), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "plaintext packet metadata"):
                check_stage_plan(path, sessions, "parent")
            del plan["stages"][1]["packet"]
            plan["stages"][0]["write_scope"] = ["internal/oci"]
            path.write_text(json.dumps(plan), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "overlapping"):
                check_stage_plan(path, sessions, "parent")

    def test_v4_stage_plan_requires_separate_easy_work_and_self_checks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "plan.json"
            hard = "benchmark-hard-kernel-gpt-6-astra-xhigh"
            digest = hashlib.sha256(b"bounded hard packet").hexdigest()
            plan = {"schema_version": 1, "stages": [
                {"id": "hard-kernel", "owner": hard, "dependencies": [],
                 "write_scope": ["internal/oci/ecr/ecr.go", "internal/oci/ecr/ecr_test.go",
                                 "internal/oci/options.go", "internal/oci/options_test.go"],
                 "acceptance": "lifecycle code and tests pass", "self_check": "focused Go tests",
                 "context_budget": 18000, "packet_sha256": digest, "packet_bytes": 19},
                {"id": "maintainer-docs", "owner": "controller", "dependencies": [],
                 "write_scope": ["internal/oci/ecr/README.md"],
                 "acceptance": "document observable behavior", "self_check": "compare with task",
                 "context_budget": 4000, "packet_sha256": None, "packet_bytes": None},
                {"id": "verify-and-synthesize", "owner": "controller",
                 "dependencies": ["hard-kernel", "maintainer-docs"], "write_scope": [],
                 "acceptance": "run prescribed checks and report", "self_check": "check outputs",
                 "context_budget": 4000, "packet_sha256": None, "packet_bytes": None}]}
            path.write_text(json.dumps(plan), encoding="utf-8")
            child_path = root / "child.jsonl"
            child_path.write_text(json.dumps({"type": "event_msg", "payload": {
                "type": "task_complete", "last_agent_message": "Packet SHA-256: " + digest}}) +
                "\n", encoding="utf-8")
            sessions = [{"id": "parent", "agent_path": "/root"},
                        {"id": "child", "model": "gpt-6-astra", "effort": "xhigh",
                         "agent_path": "/root/benchmark_hard_kernel_gpt_6_astra_xhigh",
                         "start_timestamp": "2026-09-24T00:00:00+00:00",
                         "session_path": child_path}]
            self.assertEqual("hard-kernel",
                             check_stage_plan(path, sessions, "parent",
                                              require_v4_split=True)["hard_kernel_stage_id"])
            plan["stages"][0]["write_scope"].append("internal/oci/ecr/README.md")
            path.write_text(json.dumps(plan), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "v4 stage write scopes"):
                check_stage_plan(path, sessions, "parent", require_v4_split=True)
            plan["stages"][0]["write_scope"].pop()
            plan["stages"][1]["self_check"] = ""
            path.write_text(json.dumps(plan), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "self-check missing"):
                check_stage_plan(path, sessions, "parent", require_v4_split=True)

    def test_undeclared_worker_and_none_effort_do_not_pass(self) -> None:
        config_path = Path(__file__).resolve().parents[1] / "paired_selective" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(ValueError, "unapproved"):
            require_allowed_selectors([{"model": "gpt-6-luna", "effort": "none"}], [], config)
        with self.assertRaisesRegex(ValueError, "unapproved"):
            require_allowed_selectors([], [{"model": "gpt-6-sol", "effort": "ultra"}], config)
        with self.assertRaisesRegex(ValueError, "unapproved"):
            require_allowed_selectors([{"model": None, "effort": None}],
                                      [{"model": "gpt-6-astra", "effort": "xhigh"}], config)
        report = {"status": "structurally-matched", "role_contract_sha256": "a" * 64,
                  "attempts": [{"selector_state": "omitted"}]}
        self.assertTrue(structural_role_attempts(report, Path("role-contract.json")))
        self.assertFalse(structural_role_attempts(report, None))
        self.assertFalse(structural_role_attempts({**report, "status": "UNKNOWN"},
                                                Path("role-contract.json")))
        self.assertFalse(structural_role_attempts({**report, "role_contract_sha256": None},
                                                Path("role-contract.json")))

    def test_grade_is_bound_to_captured_patch_and_exact_oracle_command(self) -> None:
        config_path = Path(__file__).resolve().parents[1] / "paired_selective" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "grader-go.stdout").write_bytes(b"PASS\n")
            (root / "grader-go.stderr").write_bytes(b"")
            patch = b"candidate patch"
            run_arm = {"candidate_patch": {"sha256": hashlib.sha256(patch).hexdigest(),
                                           "bytes": len(patch)}}
            grade = {"base_tree": config["fixture"]["initial_tree"],
                     "test_patch_sha256": config["benchmark"]["oracle_patch_sha256"],
                     "oracle_config_sha256": config["benchmark"]["oracle_config_sha256"],
                     "grader": "local-hidden-test-replay-not-official-harbor",
                     "quality_pass": True, "go_test_exit_code": 0,
                     "packages": config["benchmark"]["oracle_packages"],
                     "test_command": ["wsl", "-d", "Ubuntu", "--", "/go/bin/go", "test",
                                      "-count=1", *config["benchmark"]["oracle_packages"]],
                     "candidate_patch_sha256": run_arm["candidate_patch"]["sha256"],
                     "candidate_patch_bytes": len(patch),
                     "stdout_sha256": hashlib.sha256(b"PASS\n").hexdigest(),
                     "stderr_sha256": hashlib.sha256(b"").hexdigest()}
            path = root / "grade.json"
            path.write_text(json.dumps(grade), encoding="utf-8")
            self.assertTrue(validated_grade(path, run_arm, config)["quality_pass"])
            grade["packages"] = ["./unrelated"]
            path.write_text(json.dumps(grade), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unrelated package"):
                validated_grade(path, run_arm, config)
            grade["packages"] = config["benchmark"]["oracle_packages"]
            grade["test_command"].insert(6, "-run=Unrelated")
            path.write_text(json.dumps(grade), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "command differs"):
                validated_grade(path, run_arm, config)
            grade["test_command"].pop(6)
            grade["candidate_patch_sha256"] = "0" * 64
            path.write_text(json.dumps(grade), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "patch differs"):
                validated_grade(path, run_arm, config)

    def test_zero_discovered_rollouts_are_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            meter = SessionMeter(Path(directory), "parent", "gpt-6-astra", "xhigh")
            meter.refresh()
            self.assertEqual(0, meter.calls)
            self.assertIn("no session rollout was discovered", meter.integrity_issues())
            self.assertTrue(telemetry_unavailable(meter, 181))
            self.assertTrue(telemetry_unavailable(None, 181))
            self.assertFalse(telemetry_unavailable(meter, 179))

    def test_completed_baseline_accepts_cost_interval_without_exact_claim(self) -> None:
        config_path = Path(__file__).resolve().parents[1] / "paired_selective" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session_dir = root / "session-evidence"
            session_dir.mkdir()
            usage = {"input_tokens": 14_908, "cached_input_tokens": 0,
                     "output_tokens": 95, "reasoning_output_tokens": 0}
            session = session_dir / "rollout-parent.jsonl"
            session.write_text("".join(json.dumps(item) + "\n" for item in (
                {"type": "session_meta", "payload": {"id": "parent", "agent_path": "/root"}},
                {"type": "turn_context", "payload": {"model": "gpt-6-astra", "effort": "xhigh"}},
                {"type": "event_msg", "payload": {"type": "token_count", "info": {
                    "last_token_usage": usage, "total_token_usage": usage}}},
                {"type": "event_msg", "payload": {"type": "task_complete"}})), encoding="utf-8")
            transcript = root / "codex.jsonl"
            transcript.write_text('{"type":"thread.started","thread_id":"parent"}\n'
                                  '{"type":"turn.completed"}\n', encoding="utf-8")
            (root / "prompt.md").write_text(pinned_prompt(config_path, config),
                                            encoding="utf-8", newline="\n")
            (root / "final.txt").write_text("Done", encoding="utf-8")
            (root / "candidate.patch").write_bytes(b"")
            meter = SessionMeter(session_dir, "parent", "gpt-6-astra", "xhigh")
            meter.refresh()
            self.assertEqual([], meter.integrity_issues())
            record = {"arm": "baseline", "config_sha256": sha256(config_path),
                      "initial_tree": config["fixture"]["initial_tree"],
                      "initial_diff_sha256": hashlib.sha256(b"").hexdigest(),
                      "prompt_sha256": config["prompt"]["combined_sha256"],
                      "transcript_sha256": sha256(transcript),
                      "final_sha256": sha256(root / "final.txt"),
                      "candidate_patch": {"path": "candidate.patch",
                                          "sha256": sha256(root / "candidate.patch"), "bytes": 0},
                      "wall_seconds": 10, "started_at": "2026-09-24T00:00:00+00:00",
                      "ended_at": "2026-09-24T00:00:10+00:00",
                      "command": ["codex", "exec", "-m", "gpt-6-astra", "-c",
                                  'model_reasoning_effort="xhigh"', "--disable", "plugins",
                                  "--disable", "hooks", "--disable", "multi_agent"],
                      "model": "gpt-6-astra", "effort": "xhigh",
                      "stop_reason": "completed", "exit_code": 0, "cost_status": "complete",
                      "parent_session_id": "parent",
                      "session_files": [{"id": "parent", "path": "session-evidence/rollout-parent.jsonl",
                                         "sha256": sha256(session)}],
                      "usage": meter.summary(), "workspace": str(root / "workspace"),
                      "cli_version": "codex-cli 0.144.1",
                      "pricing_source": config["pricing"]["source"],
                      "pricing_tier": config["pricing"]["tier"]}
            (root / "run.json").write_text(json.dumps(record), encoding="utf-8")
            report = inspect_arm(config_path, config, root, "baseline")
            self.assertEqual("completed", report["outcome"])
            self.assertEqual("complete", report["cost_status"])
            self.assertEqual("interval", report["cost_estimate_status"])
            self.assertIsNone(report["estimated_usd"])
            self.assertEqual(0.15383, report["estimated_usd_lower_bound"])
            self.assertEqual(0.1911, report["estimated_usd_upper_bound"])
            self.assertIsNone(paired_cost_ratio(report, report))
            self.assertEqual([0.15383 / 0.1911, 0.1911 / 0.15383],
                             paired_cost_ratio_interval(report, report))

    def test_budget_stopped_arm_retains_observed_cost_in_denominator(self) -> None:
        config_path = Path(__file__).resolve().parents[1] / "paired_selective" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session_dir = root / "session-evidence"
            session_dir.mkdir()
            usage = {"input_tokens": 1000, "cached_input_tokens": 500,
                     "cache_write_input_tokens": 100, "output_tokens": 100,
                     "reasoning_output_tokens": 20}
            session = session_dir / "rollout-parent.jsonl"
            session.write_text("\n".join(json.dumps(item) for item in (
                {"type": "session_meta", "payload": {"id": "parent", "agent_path": "/root"}},
                {"type": "turn_context", "payload": {"model": "gpt-6-astra", "effort": "xhigh"}},
                {"type": "event_msg", "payload": {"type": "token_count", "info": {
                    "last_token_usage": usage, "total_token_usage": usage}}},
                {"type": "event_msg", "payload": {"type": "task_failed"}})) + "\n",
                encoding="utf-8")
            transcript = root / "codex.jsonl"
            transcript.write_text('{"type":"thread.started","thread_id":"parent"}\n'
                                  '{"type":"turn.failed"}\n', encoding="utf-8")
            prompt = root / "prompt.md"
            prompt.write_text(pinned_prompt(config_path, config), encoding="utf-8", newline="\n")
            meter = SessionMeter(session_dir, "parent", "gpt-6-astra", "xhigh")
            meter.refresh()
            record = {"arm": "baseline", "config_sha256": sha256(config_path),
                      "initial_tree": config["fixture"]["initial_tree"],
                      "initial_diff_sha256": hashlib.sha256(b"").hexdigest(),
                      "prompt_sha256": config["prompt"]["combined_sha256"],
                      "transcript_sha256": sha256(transcript),
                      "wall_seconds": 10, "started_at": "2026-09-24T00:00:00+00:00",
                      "ended_at": "2026-09-24T00:00:10+00:00",
                      "command": ["codex", "exec", "-m", "gpt-6-astra", "-c",
                                  'model_reasoning_effort="xhigh"', "--disable", "plugins",
                                  "--disable", "hooks", "--disable", "multi_agent"],
                      "model": "gpt-6-astra", "effort": "xhigh",
                      "stop_reason": "budget-safety-stop", "exit_code": 1,
                      "cost_status": "partial-or-unknown", "parent_session_id": "parent",
                      "session_files": [{"id": "parent", "path": "session-evidence/rollout-parent.jsonl",
                                         "sha256": sha256(session)}],
                      "usage": meter.summary(), "workspace": str(root / "workspace"),
                      "cli_version": "codex-cli 0.155.0",
                      "pricing_source": config["pricing"]["source"],
                      "pricing_tier": config["pricing"]["tier"]}
            (root / "run.json").write_text(json.dumps(record), encoding="utf-8")
            report = inspect_arm(config_path, config, root, "baseline")
            self.assertEqual("failed", report["outcome"])
            self.assertEqual("partial-or-unknown", report["cost_status"])
            self.assertEqual("UNKNOWN", report["cost_estimate_status"])
            self.assertGreater(report["observed_usd_lower_bound"], 0)
            self.assertIsNone(report["estimated_usd"])
            self.assertIsNone(report["estimated_usd_upper_bound"])

    def test_dispatched_child_without_rollout_makes_total_cost_unknown(self) -> None:
        config_path = Path(__file__).resolve().parents[1] / "paired_selective" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sessions = root / "session-evidence"
            sessions.mkdir()
            usage = {"input_tokens": 1000, "cached_input_tokens": 500,
                     "cache_write_input_tokens": 100, "output_tokens": 100,
                     "reasoning_output_tokens": 20}
            dispatch = {"task_name": "design_ecr_gpt_6_astra_xhigh", "model": "gpt-6-astra",
                        "reasoning_effort": "xhigh", "fork_turns": "none", "message": "encrypted"}
            events = [
                {"type": "session_meta", "payload": {"id": "parent", "agent_path": "/root"}},
                {"type": "turn_context", "payload": {"model": "gpt-6-luna", "effort": "medium"}},
                {"type": "response_item", "payload": {"type": "message", "role": "developer",
                    "content": [{"text": "ROUTING_CONFIG_BEGIN " + config["plugin"]["version"] +
                                 " ROUTING_CONFIG_END"}]}},
                {"type": "response_item", "payload": {"type": "function_call",
                    "name": "spawn_agent", "arguments": json.dumps(dispatch)}},
                {"type": "event_msg", "payload": {"type": "token_count", "info": {
                    "last_token_usage": usage, "total_token_usage": usage}}},
                {"type": "event_msg", "payload": {"type": "task_complete"}},
            ]
            parent = sessions / "rollout-parent.jsonl"
            parent.write_text("".join(json.dumps(item) + "\n" for item in events), encoding="utf-8")
            meter = SessionMeter(sessions, "parent", "gpt-6-luna", "medium")
            meter.refresh()
            self.assertTrue(any("dispatched child" in item
                                for item in meter.dispatch_coverage_issues()))
            self.assertTrue(any("dispatched child" in item for item in meter.integrity_issues()))
            transcript = root / "codex.jsonl"
            transcript.write_text('{"type":"thread.started","thread_id":"parent"}\n'
                                  '{"type":"turn.completed"}\n', encoding="utf-8")
            (root / "prompt.md").write_text(pinned_prompt(config_path, config),
                                            encoding="utf-8", newline="\n")
            (root / "final.txt").write_text("Done", encoding="utf-8")
            (root / "candidate.patch").write_bytes(b"")
            record = {"arm": "treatment", "config_sha256": sha256(config_path),
                      "initial_tree": config["fixture"]["initial_tree"],
                      "initial_diff_sha256": hashlib.sha256(b"").hexdigest(),
                      "prompt_sha256": config["prompt"]["combined_sha256"],
                      "transcript_sha256": sha256(transcript),
                      "final_sha256": sha256(root / "final.txt"),
                      "candidate_patch": {"path": "candidate.patch",
                                          "sha256": sha256(root / "candidate.patch"), "bytes": 0},
                      "wall_seconds": 10, "started_at": "2026-09-24T00:00:00+00:00",
                      "ended_at": "2026-09-24T00:00:10+00:00",
                      "command": ["codex", "exec", "-m", "gpt-6-luna", "-c",
                                  'model_reasoning_effort="medium"'],
                      "model": "gpt-6-luna", "effort": "medium",
                      "stop_reason": "completed", "exit_code": 0, "cost_status": "complete",
                      "parent_session_id": "parent",
                      "session_files": [{"id": "parent", "path": "session-evidence/rollout-parent.jsonl",
                                         "sha256": sha256(parent)}],
                      "usage": meter.summary(), "workspace": str(root / "workspace"),
                      "cli_version": "codex-cli 0.155.0",
                      "pricing_source": config["pricing"]["source"],
                      "pricing_tier": config["pricing"]["tier"],
                      "plugin_manifest_sha256": config["plugin"]["installed_manifest_sha256"]}
            (root / "run.json").write_text(json.dumps(record), encoding="utf-8")
            report = inspect_arm(config_path, config, root, "treatment")
            self.assertEqual("failed", report["outcome"])
            self.assertEqual("partial-or-unknown", report["cost_status"])
            self.assertIsNone(report["estimated_usd"])
            self.assertGreater(report["observed_usd_lower_bound"], 0)
            self.assertTrue(any("dispatched child" in item for item in report["usage_issues"]))
            self.assertIsNone(paired_cost_ratio(
                {"cost_status": "complete", "estimated_usd": 0.02}, report))

    def test_native_spawn_selector_is_read_from_parent_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "parent.jsonl"
            path.write_text(json.dumps({"type": "response_item", "payload": {
                "type": "function_call", "name": "spawn_agent",
                "arguments": json.dumps({"task_name": "design_ecr_gpt_6_astra_xhigh",
                                         "fork_turns": "none", "model": "gpt-6-astra",
                                         "reasoning_effort": "xhigh", "message": "encrypted"})}}) + "\n",
                encoding="utf-8")
            self.assertEqual([{"task_name": "design_ecr_gpt_6_astra_xhigh",
                               "model": "gpt-6-astra", "effort": "xhigh",
                               "fork_turns": "none"}], native_spawns(path))


if __name__ == "__main__":
    unittest.main()
