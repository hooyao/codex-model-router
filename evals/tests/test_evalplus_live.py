import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from evals.scripts import evalplus_live as live
from evals.scripts import evalplus_runner as runner


PRICING = {"pricing_version": "astra-standard-frozen-test", "rate_model": "gpt-6-astra", "soft_cap_usd": "50.00",
           "rates": {"input_per_million_usd": "10.00", "cached_input_per_million_usd": "1.00",
                     "output_per_million_usd": "50.00"}}


class SoftCampaignTests(unittest.TestCase):
    def stream(self, path, identity, usage=None, child=None):
        events = [{"type": "thread.started", "thread_id": identity}]
        if child:
            events.append({"type": "item.completed", "item": {"type": "collab_tool_call", "receiver_thread_ids": [child]}})
        if usage:
            events.append({"type": "turn.completed", "usage": usage})
        path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
        return path

    def test_cap_stops_at_fifty_without_reserving_future_runs(self):
        for amount, admitted in (("0", True), ("49.999999", True), ("50", False), ("51.1", False)):
            ledger = {"observed_cost_usd": amount, "conflicting_usage_sessions": []}
            self.assertEqual(admitted, live.admit_next(ledger, PRICING))

    def test_distinct_threads_count_once_with_cached_and_reasoning_tokens(self):
        usage = {"input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 10, "reasoning_output_tokens": 5}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            parent = self.stream(root / "parent.jsonl", "p", usage, "c")
            mirror = self.stream(root / "mirror.jsonl", "p", usage, "c")
            child = self.stream(root / "child.jsonl", "c", usage)
            ledger = live.collect_usage([parent, child, mirror, parent], PRICING)
        self.assertEqual(2, len(ledger["sessions"]))
        self.assertEqual("0.00264", ledger["estimated_cost_usd"])
        self.assertTrue(ledger["cost_complete"])

    def test_missing_child_usage_is_never_reported_as_complete(self):
        usage = {"input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0}
        with tempfile.TemporaryDirectory() as temp:
            path = self.stream(Path(temp) / "parent.jsonl", "p", usage, "c")
            ledger = live.collect_usage([path], PRICING)
        self.assertFalse(ledger["cost_complete"])
        self.assertIsNone(ledger["estimated_cost_usd"])
        self.assertEqual("0.001", ledger["observed_cost_usd"])
        self.assertEqual(["c"], ledger["missing_usage_sessions"])

    def test_conflicting_thread_totals_stop_admission(self):
        usage = {"input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = self.stream(root / "first.jsonl", "p", usage)
            second = self.stream(root / "second.jsonl", "p", dict(usage, input_tokens=200))
            ledger = live.collect_usage([first, second], PRICING)
        with self.assertRaisesRegex(runner.HarnessError, "ambiguous"):
            live.admit_next(ledger, PRICING)

    def test_soft_cli_path_does_not_use_hard_provider_guard(self):
        args = runner.build_parser().parse_args(["run", "--campaign-root", "unused", "--live",
                                                "--soft-budget-pricing", "pricing.json"])
        with mock.patch.object(runner, "_load_prepared", return_value=({}, {})), \
                mock.patch.object(live, "execute_soft_campaign", return_value={"mode": "soft"}) as soft, \
                mock.patch.object(runner, "execute_live") as hard:
            self.assertEqual({"mode": "soft"}, runner.command_run(args))
        soft.assert_called_once()
        hard.assert_not_called()

    def test_fresh_slot_does_not_copy_sessions_memory_or_user_skills(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            template = root / "template"
            template.mkdir()
            (template / "config.toml").write_text("# isolated config")
            (template / "sessions").mkdir()
            (template / "sessions/prior.jsonl").write_text("private")
            (template / "AGENTS.md").write_text("ambient")
            identity = {"config_sha256": {"baseline": runner.sha256_file(template / "config.toml")}}
            destination = live.clone_slot_home(template, root / "slot", "baseline", identity)
            self.assertEqual(["config.toml"], [path.name for path in destination.iterdir()])


if __name__ == "__main__":
    unittest.main()
