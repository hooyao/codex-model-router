"""Deterministic tests for live paired-run cost accounting."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evals.scripts.run_paired_arm import (SessionMeter, budget_limit_reached,
                                          calibrated_controller,
                                          negative_treatment_controller,
                                          parent_id_from_transcript, response_cost,
                                          response_cost_interval, sha256,
                                          treatment_options,
                                          validate_treatment_binding)


def event(kind: str, payload: dict) -> str:
    return json.dumps({"type": kind, "payload": payload}) + "\n"


class PairedLiveTests(unittest.TestCase):
    def test_current_sol_usage_is_unknown_until_exact_model_pricing_is_verified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            usage = {"input_tokens": 1000, "cached_input_tokens": 500,
                     "cache_write_input_tokens": 100, "output_tokens": 100,
                     "reasoning_output_tokens": 20}
            (root / "rollout-parent.jsonl").write_text(
                event("session_meta", {"id": "parent"}) +
                event("turn_context", {"model": "gpt-6.1-sol", "effort": "low"}) +
                event("event_msg", {"type": "token_count", "info": {
                    "last_token_usage": usage, "total_token_usage": usage}}) +
                event("event_msg", {"type": "task_complete"}), encoding="utf-8")
            meter = SessionMeter(root, "parent", "gpt-6.1-sol", "low")
            meter.refresh()
            self.assertIn("gpt-6.1-sol", meter.unknown_models)
            self.assertTrue(meter.integrity_issues())
            self.assertEqual(0, meter.calls)
            self.assertEqual(0, meter.cost)

    def test_treatment_options_pin_plugin_role_and_only_audit_trust(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plugin_root = root / "installed"
            (plugin_root / ".codex-plugin").mkdir(parents=True)
            (plugin_root / "hooks").mkdir()
            manifest = plugin_root / ".codex-plugin" / "plugin.json"
            manifest.write_text('{"name":"codex-model-router","version":"pinned"}',
                                encoding="utf-8")
            hooks = plugin_root / "hooks" / "hooks.json"
            hooks.write_text('{}', encoding="utf-8")
            role = root / "astra-hard-kernel-role.toml"
            role.write_text('model = "gpt-6-astra"\nmodel_reasoning_effort = "xhigh"\n',
                            encoding="utf-8")
            config = {"plugin": {"name": "codex-model-router", "version": "pinned",
                "installed_manifest_sha256": sha256(manifest),
                "installed_hooks_sha256": sha256(hooks),
                "process_local_audit_trust": [
                    {"event": "preToolUse", "key":
                     "codex-model-router@personal:hooks/hooks.json:pre_tool_use:0:0",
                     "current_hash": "sha256:" + "a" * 64},
                    {"event": "postToolUse", "key":
                     "codex-model-router@personal:hooks/hooks.json:post_tool_use:0:0",
                     "current_hash": "sha256:" + "b" * 64}]},
                "arms": {"treatment": {"required_hard_worker": {
                    "model": "gpt-6-astra", "effort": "xhigh",
                    "role_file": role.name, "role_sha256": sha256(role)}}}}
            options, bound_role = treatment_options(root / "config.json", config,
                                                    plugin_root)
            self.assertEqual(role, bound_role)
            self.assertEqual(1, sum(value.startswith("hooks.state=") for value in options))
            self.assertIn("--enable", options)
            self.assertIn("agents.default=", " ".join(options))
            config["plugin"]["installed_hooks_sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "hooks hash mismatch"):
                treatment_options(root / "config.json", config, plugin_root)

    def test_treatment_binding_rejects_untrusted_or_drifted_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            plugin_root = root / "installed"
            role = root / "astra-hard-kernel-role.toml"
            role.write_text('model = "gpt-6-astra"\n', encoding="utf-8")
            events = {"sessionStart", "userPromptSubmit", "subagentStart",
                      "preToolUse", "postToolUse"}
            audit = [{"event": event, "key": event, "current_hash": "sha256:" + "a" * 64}
                     for event in ("preToolUse", "postToolUse")]
            hooks = [{"pluginId": "codex-model-router@personal", "eventName": event,
                      "key": event, "currentHash": "sha256:" + "a" * 64,
                      "sourcePath": str(plugin_root / "hooks" / "hooks.json"),
                      "enabled": True, "trustStatus": "trusted"}
                     for event in events]
            effective = {"config": {"agents": {"default": {
                "config_file": str(role.resolve())}}}}
            inventory = {"data": [{"cwd": str(workspace.resolve()), "warnings": [],
                                   "errors": [], "hooks": hooks}]}
            args = ({"codexHome": "home"}, effective, inventory, "codex", [], workspace,
                    plugin_root, role, audit)
            self.assertEqual("passed", validate_treatment_binding(*args)["status"])
            audit_hook = next(hook for hook in hooks if hook["eventName"] == "preToolUse")
            audit_hook["trustStatus"] = "modified"
            with self.assertRaisesRegex(ValueError, "not trusted"):
                validate_treatment_binding(*args)
            audit_hook["trustStatus"] = "trusted"
            audit_hook["currentHash"] = "sha256:" + "b" * 64
            with self.assertRaisesRegex(ValueError, "hash drifted"):
                validate_treatment_binding(*args)
            audit_hook["currentHash"] = "sha256:" + "a" * 64
            effective["config"]["agents"]["default"]["config_file"] = "wrong"
            with self.assertRaisesRegex(ValueError, "role"):
                validate_treatment_binding(*args)

    def test_meter_retains_every_turn_selector_after_model_switch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "rollout-parent.jsonl").write_text(
                event("session_meta", {"id": "parent"}) +
                event("turn_context", {"turn_id": "one", "model": "gpt-6-astra", "effort": "xhigh"}) +
                event("turn_context", {"turn_id": "two", "model": "gpt-6-sol", "effort": "low"}),
                encoding="utf-8")
            meter = SessionMeter(root, "parent", "gpt-6-astra", "xhigh")
            meter.refresh()
            self.assertEqual([("gpt-6-astra", "xhigh"), ("gpt-6-sol", "low")],
                             [(turn["model"], turn["effort"]) for turn in
                              meter.summary()["sessions"][0]["turns"]])

    def test_standard_price_classes(self) -> None:
        usage = {"input_tokens": 1_000, "cached_input_tokens": 500,
                 "cache_write_input_tokens": 100, "output_tokens": 100,
                 "reasoning_output_tokens": 20}
        self.assertAlmostEqual(0.01075, response_cost(usage, "gpt-6-astra"))
        with self.assertRaises(ValueError):
            response_cost({**usage, "cached_input_tokens": 1_001}, "gpt-6-astra")
        with self.assertRaisesRegex(ValueError, "missing token class"):
            response_cost({key: value for key, value in usage.items()
                           if key != "cache_write_input_tokens"}, "gpt-6-astra")

    def test_long_context_prices_entire_response_at_long_rates(self) -> None:
        usage = {"input_tokens": 272_001, "cached_input_tokens": 200_000,
                 "cache_write_input_tokens": 20_000, "output_tokens": 10_000,
                 "reasoning_output_tokens": 8_000}
        expected = ((52_001 * 20) + (200_000 * 2) + (20_000 * 25) +
                    (10_000 * 75)) / 1_000_000
        self.assertAlmostEqual(expected, response_cost(usage, "gpt-6-astra"))
        self.assertLess(response_cost({**usage, "input_tokens": 272_000}, "gpt-6-astra"),
                        expected)

    def test_missing_cache_writes_has_conservative_interval(self) -> None:
        usage = {"input_tokens": 14_908, "cached_input_tokens": 0,
                 "output_tokens": 95, "reasoning_output_tokens": 0}
        self.assertEqual((0.15383, 0.1911),
                         response_cost_interval(usage, "gpt-6-astra"))
        self.assertEqual((0.01075, 0.01075), response_cost_interval({
            "input_tokens": 1000, "cached_input_tokens": 500,
            "cache_write_input_tokens": 100, "output_tokens": 100,
            "reasoning_output_tokens": 20}, "gpt-6-astra"))
        long_usage = {"input_tokens": 272_001, "cached_input_tokens": 200_000,
                      "output_tokens": 10_000, "reasoning_output_tokens": 8_000}
        self.assertEqual((2.59002, 2.950025),
                         response_cost_interval(long_usage, "gpt-6-astra"))

    def test_interval_meter_aggregates_parent_and_child(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            usage = {"input_tokens": 1000, "cached_input_tokens": 500,
                     "output_tokens": 100, "reasoning_output_tokens": 20}
            exact = {**usage, "cache_write_input_tokens": 100}
            for identity, parent, model, effort, measured in (
                    ("parent", None, "gpt-6-astra", "xhigh", usage),
                    ("child", "parent", "gpt-6-luna", "medium", exact)):
                (root / f"rollout-{identity}.jsonl").write_text(
                    event("session_meta", {"id": identity, "parent_thread_id": parent,
                                           "agent_path": "/root" if parent is None else "/root/child"}) +
                    event("turn_context", {"model": model, "effort": effort}) +
                    (event("response_item", {"type": "function_call",
                                             "name": "spawn_agent",
                                             "arguments": json.dumps({"task_name": "child"})})
                     if identity == "parent" else "") +
                    event("event_msg", {"type": "token_count", "info": {
                        "last_token_usage": measured, "total_token_usage": usage}}) +
                    event("event_msg", {"type": "task_complete"}), encoding="utf-8")
            meter = SessionMeter(root, "parent", "gpt-6-astra", "xhigh")
            meter.refresh()
            self.assertEqual([], meter.integrity_issues())
            low, high = response_cost_interval(usage, "gpt-6-astra")
            child_cost = response_cost(exact, "gpt-6-luna")
            self.assertAlmostEqual(low + child_cost, meter.cost)
            self.assertAlmostEqual(high + child_cost, meter.cost_upper)
            summary = meter.summary()
            self.assertIsNone(summary["estimated_usd"])
            self.assertEqual(2, summary["model_calls"])
            self.assertEqual(2, len(summary["sessions"]))
            self.assertEqual(1, len(summary["sessions"][0]["responses"]))
            meter.cost, meter.cost_upper = 8.9, 9.1
            self.assertTrue(budget_limit_reached(meter, 10, 1))

    def test_repeated_child_snapshot_across_turns_is_not_double_charged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = {"input_tokens": 1000, "cached_input_tokens": 500,
                     "cache_write_input_tokens": 100, "output_tokens": 100,
                     "reasoning_output_tokens": 20}
            second = {"input_tokens": 1500, "cached_input_tokens": 1000,
                      "cache_write_input_tokens": 100, "output_tokens": 80,
                      "reasoning_output_tokens": 10}
            cumulative = {key: first[key] + second[key] for key in first}
            (root / "rollout-parent.jsonl").write_text(
                event("session_meta", {"id": "parent", "agent_path": "/root"}) +
                event("turn_context", {"turn_id": "parent-turn", "model": "gpt-6-sol",
                                       "effort": "low"}) +
                event("response_item", {"type": "function_call", "name": "spawn_agent",
                                        "arguments": json.dumps({"task_name": "child"})}) +
                event("event_msg", {"type": "token_count", "info": {
                    "last_token_usage": first, "total_token_usage": first}}) +
                event("event_msg", {"type": "task_complete"}), encoding="utf-8")
            (root / "rollout-child.jsonl").write_text(
                event("session_meta", {"id": "child", "parent_thread_id": "parent",
                                       "agent_path": "/root/child"}) +
                event("turn_context", {"turn_id": "child-one", "model": "gpt-6-astra",
                                       "effort": "xhigh"}) +
                event("event_msg", {"type": "token_count", "info": {
                    "last_token_usage": first, "total_token_usage": first}}) +
                event("event_msg", {"type": "token_count", "info": {
                    "last_token_usage": first, "total_token_usage": first}}) +
                event("turn_context", {"turn_id": "child-two", "model": "gpt-6-astra",
                                       "effort": "xhigh"}) +
                event("event_msg", {"type": "token_count", "info": {
                    "last_token_usage": second, "total_token_usage": cumulative}}) +
                event("event_msg", {"type": "task_complete"}), encoding="utf-8")
            meter = SessionMeter(root, "parent", "gpt-6-sol", "low")
            meter.refresh()
            meter.refresh()
            self.assertEqual([], meter.integrity_issues())
            self.assertEqual(3, meter.calls)
            child = next(item for item in meter.summary()["sessions"] if item["id"] == "child")
            self.assertEqual(cumulative, child["token_classes"])
            self.assertAlmostEqual(response_cost(first, "gpt-6-sol") +
                                   response_cost(first, "gpt-6-astra") +
                                   response_cost(second, "gpt-6-astra"), meter.cost)

    def test_cumulative_usage_contradictions_remain_unknown(self) -> None:
        first = {"input_tokens": 1000, "cached_input_tokens": 500,
                 "cache_write_input_tokens": 100, "output_tokens": 100,
                 "reasoning_output_tokens": 20}
        changed = {**first, "output_tokens": 90}
        for last, total in ((changed, first), (first, first),
                            (first, {**first, "input_tokens": 3000}),
                            (first, {**first, "total_tokens": 999})):
            with self.subTest(last=last, total=total), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "rollout-parent.jsonl").write_text(
                    event("session_meta", {"id": "parent"}) +
                    event("turn_context", {"model": "gpt-6-astra", "effort": "xhigh"}) +
                    event("event_msg", {"type": "token_count", "info": {
                        "last_token_usage": first, "total_token_usage": first}}) +
                    event("event_msg", {"type": "token_count", "info": {
                        "last_token_usage": last, "total_token_usage": total}}) +
                    event("event_msg", {"type": "task_complete"}), encoding="utf-8")
                meter = SessionMeter(root, "parent", "gpt-6-astra", "xhigh")
                meter.refresh()
                if last == first and total == first:
                    self.assertEqual([], meter.integrity_issues())
                    self.assertEqual(1, meter.calls)
                else:
                    self.assertTrue(meter.unknown_usage)
                    self.assertIn("unpriced model response or token class",
                                  meter.integrity_issues())

    def test_identical_response_usage_with_new_cumulative_total_is_billed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            usage = {"input_tokens": 1000, "cached_input_tokens": 500,
                     "cache_write_input_tokens": 100, "output_tokens": 100,
                     "reasoning_output_tokens": 20}
            doubled = {key: 2 * value for key, value in usage.items()}
            (root / "rollout-parent.jsonl").write_text(
                event("session_meta", {"id": "parent"}) +
                event("turn_context", {"model": "gpt-6-astra", "effort": "xhigh"}) +
                event("event_msg", {"type": "token_count", "info": {
                    "last_token_usage": usage, "total_token_usage": usage}}) +
                event("event_msg", {"type": "token_count", "info": {
                    "last_token_usage": usage, "total_token_usage": doubled}}) +
                event("event_msg", {"type": "task_complete"}), encoding="utf-8")
            meter = SessionMeter(root, "parent", "gpt-6-astra", "xhigh")
            meter.refresh()
            self.assertEqual([], meter.integrity_issues())
            self.assertEqual(2, meter.calls)
            self.assertAlmostEqual(2 * response_cost(usage, "gpt-6-astra"), meter.cost)

    def test_reconciled_usage_needs_terminal_and_successful_parent(self) -> None:
        usage = {"input_tokens": 14_908, "cached_input_tokens": 0,
                 "output_tokens": 95, "reasoning_output_tokens": 0}
        for terminal in (None, "task_failed"):
            with self.subTest(terminal=terminal), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "rollout-parent.jsonl").write_text(
                    event("session_meta", {"id": "parent"}) +
                    event("turn_context", {"model": "gpt-6-astra", "effort": "xhigh"}) +
                    event("event_msg", {"type": "token_count", "info": {
                        "last_token_usage": usage, "total_token_usage": usage}}) +
                    (event("event_msg", {"type": terminal}) if terminal else ""),
                    encoding="utf-8")
                meter = SessionMeter(root, "parent", "gpt-6-astra", "xhigh")
                meter.refresh()
                expected = ("session parent lacks terminal result" if terminal is None else
                            "parent session lacks successful terminal result")
                self.assertEqual([expected], meter.integrity_issues())
                self.assertEqual((0.15383, 0.1911), (meter.cost, meter.cost_upper))

    def test_child_terminal_is_required_with_reconciled_usage(self) -> None:
        usage = {"input_tokens": 1000, "cached_input_tokens": 500,
                 "output_tokens": 100, "reasoning_output_tokens": 20}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "rollout-parent.jsonl").write_text(
                event("session_meta", {"id": "parent", "agent_path": "/root"}) +
                event("turn_context", {"model": "gpt-6-astra", "effort": "xhigh"}) +
                event("response_item", {"type": "function_call", "name": "spawn_agent",
                                        "arguments": json.dumps({"task_name": "child"})}) +
                event("event_msg", {"type": "token_count", "info": {
                    "last_token_usage": usage, "total_token_usage": usage}}) +
                event("event_msg", {"type": "task_complete"}), encoding="utf-8")
            (root / "rollout-child.jsonl").write_text(
                event("session_meta", {"id": "child", "parent_thread_id": "parent",
                                       "agent_path": "/root/child"}) +
                event("turn_context", {"model": "gpt-6-luna", "effort": "medium"}) +
                event("event_msg", {"type": "token_count", "info": {
                    "last_token_usage": usage, "total_token_usage": usage}}), encoding="utf-8")
            meter = SessionMeter(root, "parent", "gpt-6-astra", "xhigh")
            meter.refresh()
            self.assertEqual(["session child lacks terminal result"], meter.integrity_issues())

    def test_parent_and_child_usage_are_counted_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent = root / "rollout-parent.jsonl"
            child = root / "rollout-child.jsonl"
            parent.write_text(
                event("session_meta", {"id": "parent"}) +
                event("turn_context", {"model": "gpt-5.6-sol"}) +
                event("event_msg", {"type": "token_count", "info": {
                    "last_token_usage": {"input_tokens": 1000, "cached_input_tokens": 500,
                                         "cache_write_input_tokens": 100,
                                         "output_tokens": 100,
                                         "reasoning_output_tokens": 20}}}), encoding="utf-8")
            child.write_text(
                event("session_meta", {"id": "child", "parent_thread_id": "parent"}) +
                event("turn_context", {"model": "gpt-6-luna"}) +
                event("event_msg", {"type": "token_count", "info": {
                    "last_token_usage": {"input_tokens": 1000, "cached_input_tokens": 500,
                                         "cache_write_input_tokens": 100,
                                         "output_tokens": 100,
                                         "reasoning_output_tokens": 20}}}), encoding="utf-8")
            meter = SessionMeter(root, "parent", "gpt-5.6-sol")
            meter.refresh()
            meter.refresh()
            self.assertEqual(2, meter.calls)
            self.assertEqual(2, len(meter.paths))
            self.assertEqual(2000, meter.tokens["input_tokens"])
            expected = response_cost({"input_tokens": 1000, "cached_input_tokens": 500,
                                      "cache_write_input_tokens": 100,
                                      "output_tokens": 100,
                                      "reasoning_output_tokens": 20}, "gpt-5.6-sol")
            expected += response_cost({"input_tokens": 1000, "cached_input_tokens": 500,
                                       "cache_write_input_tokens": 100,
                                       "output_tokens": 100,
                                       "reasoning_output_tokens": 20}, "gpt-6-luna")
            self.assertAlmostEqual(expected, meter.cost)

    def test_controller_selection_requires_both_evidenced_capability_trials(self) -> None:
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "trial.json"
            evidence.write_text("{}", encoding="utf-8")
            digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
            criteria = {key: True for key in ("decomposition", "dependencies", "dispatch",
                                              "receipts", "verification", "recovery")}
            record = {"schema_version": 1,
                      "observations": [
                          {"model": "gpt-6-luna", "effort": "medium", "terminal": "completed",
                           "evidence_path": "trial.json", "evidence_sha256": digest,
                           "criteria": {**criteria, "recovery": False}},
                          {"model": "gpt-6-sol", "effort": "low", "terminal": "completed",
                           "evidence_path": "trial.json", "evidence_sha256": digest,
                           "criteria": criteria}],
                      "selected": ["gpt-6-sol", "low"]}
            path = root / "calibration.json"
            path.write_text(json.dumps(record), encoding="utf-8")
            config = {"arms": {"treatment": {"controller_candidates": [
                {"model": "gpt-6-luna", "effort": "medium"},
                {"model": "gpt-6-sol", "effort": "low"}]}}}
            self.assertEqual(("gpt-6-sol", "low"), calibrated_controller(path, config))
            record["selected"] = ["gpt-6-luna", "medium"]
            path.write_text(json.dumps(record), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "cheapest passing"):
                calibrated_controller(path, config)

            record["selected"] = None
            record["observations"][1]["criteria"]["dispatch"] = False
            path.write_text(json.dumps(record), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "neither controller passed"):
                calibrated_controller(path, config)
            self.assertEqual(("gpt-6-sol", "low"),
                             negative_treatment_controller(path, config, "gpt-6-sol/low"))
            with self.assertRaisesRegex(ValueError, "explicit gpt-6-sol/low"):
                negative_treatment_controller(path, config, "gpt-6-luna/medium")
            record["selected"] = ["gpt-6-sol", "low"]
            path.write_text(json.dumps(record), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must select null"):
                negative_treatment_controller(path, config, "gpt-6-sol/low")
            record["selected"] = None
            record["observations"][1]["criteria"]["dispatch"] = True
            path.write_text(json.dumps(record), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "cheapest passing"):
                negative_treatment_controller(path, config, "gpt-6-sol/low")
            record["observations"][1]["criteria"]["dispatch"] = False
            record["observations"][1]["evidence_sha256"] = "0" * 64
            path.write_text(json.dumps(record), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "evidence mismatch"):
                negative_treatment_controller(path, config, "gpt-6-sol/low")

    def test_thread_id_from_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "codex.jsonl"
            transcript.write_text(
                json.dumps({"type": "turn.started"}) + "\n" +
                json.dumps({"type": "thread.started", "thread_id": "chosen"}) + "\n",
                encoding="utf-8")
            self.assertEqual("chosen", parent_id_from_transcript(transcript))


if __name__ == "__main__":
    unittest.main()
