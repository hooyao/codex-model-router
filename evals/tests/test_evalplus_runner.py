import copy
import datetime as dt
import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from evals.scripts import evalplus_runner as runner  # noqa: E402


class EvalPlusManifestAndScheduleTests(unittest.TestCase):
    def setUp(self):
        self.manifest = runner.validate_manifest()

    def test_manifest_pins_exact_benchmarks_and_tasks(self):
        self.assertEqual(
            ["v0.1.10", "v0.2.0"],
            [dataset["version"] for dataset in self.manifest["datasets"]],
        )
        self.assertEqual(
            ["HumanEval/0", "HumanEval/63", "HumanEval/90", "Mbpp/11", "Mbpp/12", "Mbpp/67"],
            [task["task_id"] for task in self.manifest["tasks"]],
        )
        self.assertEqual("44.24", self.manifest["execution"]["campaign_cost_ceiling_usd"])

    def test_schedule_is_exact_and_counterbalanced(self):
        schedule = runner.build_schedule(self.manifest)
        self.assertEqual(24, len(schedule))
        self.assertEqual(list(range(1, 25)), [item["run_index"] for item in schedule])
        first_pair = [item["variant"] for item in schedule if item["pair_id"] == "r01-humaneval-0"]
        second_pair = [item["variant"] for item in schedule if item["pair_id"] == "r02-humaneval-0"]
        self.assertEqual(["baseline", "router"], first_pair)
        self.assertEqual(["router", "baseline"], second_pair)
        self.assertEqual(12, sum(item["variant"] == "baseline" for item in schedule))
        self.assertEqual(12, sum(item["variant"] == "router" for item in schedule))

    def test_cost_reservation_uses_every_microdollar_once(self):
        reservations = runner.cost_reservations_microusd("44.24", 24)
        self.assertEqual(24, len(reservations))
        self.assertEqual(44_240_000, sum(reservations))
        self.assertEqual([1_843_334] * 8, reservations[:8])
        self.assertEqual([1_843_333] * 16, reservations[8:])

    def test_token_reservations_are_checked_before_runs(self):
        schedule = runner.build_schedule(self.manifest)
        prepared = {
            "schedule": schedule,
            "state": {
                "attempted_run_ids": [],
                "completed_run_ids": [],
                "reserved_token_total": 0,
                "reserved_cost_microusd": 0,
            },
        }
        with tempfile.TemporaryDirectory() as temp:
            plan = runner.preflight_plan(self.manifest, prepared, Path(temp), 2)
            self.assertEqual(400_000, plan["requested_token_reservation"])
            prepared["state"]["reserved_token_total"] = 4_700_000
            with self.assertRaisesRegex(runner.HarnessError, "token reservation ceiling"):
                runner.preflight_plan(self.manifest, prepared, Path(temp), 2)

    def test_prepared_state_reservations_cannot_be_reset(self):
        schedule = runner.build_schedule(self.manifest)
        state = {
            "attempted_run_ids": [schedule[0]["run_id"]],
            "completed_run_ids": [],
            "reserved_token_total": 0,
            "reserved_cost_microusd": 0,
        }
        with self.assertRaisesRegex(runner.HarnessError, "token reservation state mismatch"):
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                manifest_path = root / "manifest.json"
                runner.write_json(manifest_path, self.manifest)
                campaign = root / "campaign"
                campaign.mkdir()
                (campaign / "tasks").mkdir()
                for run in schedule:
                    task = campaign / "tasks" / run["run_id"]
                    task.mkdir()
                    (task / ".git").mkdir()
                runner.write_json(
                    campaign / "campaign.json",
                    {
                        "campaign_id": self.manifest["campaign_id"],
                        "manifest_sha256": runner.sha256_file(manifest_path),
                        "schedule": schedule,
                        "state": state,
                    },
                )
                runner._load_prepared(campaign, manifest_path)

    def test_command_is_clean_explicit_and_arm_isolated(self):
        run = runner.build_schedule(self.manifest)[0]
        with tempfile.TemporaryDirectory() as temp:
            task_root = Path(temp) / "task"
            task_root.mkdir()
            baseline = runner.build_codex_command(self.manifest, run, task_root)
            router_run = dict(run, variant="router")
            routed = runner.build_codex_command(self.manifest, router_run, task_root)
        for required in ("--ephemeral", "--strict-config", "--ignore-rules", "--json"):
            self.assertIn(required, baseline)
            self.assertIn(required, routed)
        for command in (baseline, routed):
            self.assertNotIn("--ignore-user-config", command)
            self.assertNotIn("skip_host_skill_discovery", command)
            self.assertIn("memories.use_memories=false", command)
            self.assertEqual("gpt-6-astra", command[command.index("--model") + 1])
            self.assertIn('model_reasoning_effort="xhigh"', command)
            self.assertIn("model_supports_reasoning_summaries=true", command)
        self.assertEqual("gpt-6-astra", baseline[baseline.index("--model") + 1])
        self.assertIn('model_reasoning_effort="xhigh"', baseline)
        for command in (baseline, routed):
            self.assertNotIn("--sandbox", command)
            self.assertIn('default_permissions="evalplus-task"', command)
            self.assertIn("permissions.evalplus-task.network.enabled=false", command)
            self.assertEqual("--disable", command[command.index("shell_tool") - 1])
        self.assertEqual("-", baseline[-1])
        self.assertEqual("disable", baseline[baseline.index("plugins") - 1].lstrip("-"))
        self.assertEqual("enable", routed[routed.index("plugins") - 1].lstrip("-"))
        self.assertIn("--dangerously-bypass-hook-trust", routed)


class EvalPlusDatasetIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = copy.deepcopy(runner.validate_manifest())
        self.paths = {}
        tasks_by_dataset = {}
        for task in self.manifest["tasks"]:
            tasks_by_dataset.setdefault(task["dataset_id"], []).append(task)
        for dataset in self.manifest["datasets"]:
            lines = []
            for task in tasks_by_dataset[dataset["id"]]:
                record = {
                    "task_id": task["task_id"],
                    "entry_point": task["entry_point"],
                    "prompt": "def %s():\n    pass\n" % task["entry_point"],
                }
                raw = json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
                lines.append(raw + b"\n")
                task["record_sha256"] = runner.sha256_bytes(raw)
                task["prompt_sha256"] = runner.sha256_bytes(record["prompt"].encode("utf-8"))
            path = self.root / dataset["filename"]
            with path.open("wb") as output:
                with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as archive:
                    archive.write(b"".join(lines))
            dataset["size_bytes"] = path.stat().st_size
            dataset["sha256"] = runner.sha256_file(path)
            self.paths[dataset["id"]] = path

    def test_selected_subset_matches_all_hashes(self):
        selected = runner.select_records(self.manifest, self.paths)
        self.assertEqual(
            {task["task_id"] for task in self.manifest["tasks"]}, set(selected)
        )

    def test_mutated_selected_record_is_rejected(self):
        task = self.manifest["tasks"][0]
        task["record_sha256"] = "0" * 64
        with self.assertRaisesRegex(runner.HarnessError, "selected record hash mismatch"):
            runner.select_records(self.manifest, self.paths)

    def test_missing_selected_task_is_rejected(self):
        only_mbpp = {"mbpp-plus": self.paths["mbpp-plus"], "humaneval-plus": self.paths["mbpp-plus"]}
        with self.assertRaises(runner.HarnessError):
            runner.select_records(self.manifest, only_mbpp)


class EvalPlusBudgetGuardTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 9, 22, 12, 0, tzinfo=dt.timezone.utc)
        self.receipt = {
            "schema_version": 1,
            "source": "provider-budget-guard",
            "campaign_id": "evalplus-clean-codex-cli-v1",
            "guard_id": "guard-1",
            "provider": "dedicated-test-provider",
            "enforcement": "provider-side-hard-limit",
            "dedicated": True,
            "status": "active",
            "currency": "USD",
            "hard_limit_usd": "44.24",
            "remaining_usd": "44.24",
            "checked_at": "2026-09-22T11:59:00Z",
            "expires_at": "2026-09-22T12:10:00Z",
        }

    def test_live_refuses_without_guard_adapter(self):
        with self.assertRaisesRegex(runner.HarnessError, "live mode refused"):
            runner.invoke_provider_guard(
                None,
                {
                    "campaign_id": "evalplus-clean-codex-cli-v1",
                    "required_microusd": 1_843_334,
                },
            )

    def test_active_provider_side_guard_covers_reservation(self):
        verified = runner.verify_provider_guard(
            self.receipt, "evalplus-clean-codex-cli-v1", 1_843_334, self.now
        )
        self.assertEqual("guard-1", verified["guard_id"])

    def test_local_or_stale_guard_is_rejected(self):
        local = dict(self.receipt, enforcement="local-warning-only")
        with self.assertRaisesRegex(runner.HarnessError, "provider-side"):
            runner.verify_provider_guard(local, "evalplus-clean-codex-cli-v1", 1, self.now)
        stale = dict(self.receipt, checked_at="2026-09-22T10:00:00Z")
        with self.assertRaisesRegex(runner.HarnessError, "stale"):
            runner.verify_provider_guard(stale, "evalplus-clean-codex-cli-v1", 1, self.now)

    def test_router_plugin_is_absent_from_baseline_and_pinned_in_router(self):
        manifest = runner.validate_manifest()
        baseline = {"installed": []}
        routed = {
            "installed": [
                {
                    "name": "codex-model-router",
                    "version": "0.1.3",
                    "enabled": True,
                    "marketplaceName": "personal",
                }
            ]
        }
        runner.validate_plugin_inventories(manifest, baseline, routed)
        with self.assertRaisesRegex(runner.HarnessError, "must not install"):
            runner.validate_plugin_inventories(manifest, routed, routed)
        wrong_version = copy.deepcopy(routed)
        wrong_version["installed"][0]["version"] = "0.1.2"
        with self.assertRaisesRegex(runner.HarnessError, "wrong router plugin"):
            runner.validate_plugin_inventories(manifest, baseline, wrong_version)
        contaminated = copy.deepcopy(routed)
        contaminated["installed"].append(
            {"name": "unrelated", "enabled": True, "marketplaceName": "third-party"}
        )
        with self.assertRaisesRegex(runner.HarnessError, "unrelated enabled"):
            runner.validate_plugin_inventories(manifest, baseline, contaminated)


class EvalPlusUsageLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pricing = {
            "pricing_version": "synthetic-test-v1",
            "models": {
                "gpt-6-astra": {
                    "input_per_million_usd": "2",
                    "cached_input_per_million_usd": "1",
                    "output_per_million_usd": "8",
                }
            },
        }
        self.sessions = [
            {"session_id": "controller", "role": "controller", "model": "gpt-6-astra"},
            {"session_id": "worker", "role": "worker", "model": "gpt-6-astra"},
        ]

    def write_stream(self, name, session_id, usage=True, discovered_worker=False):
        path = self.root / name
        events = [{"type": "thread.started", "thread_id": session_id}]
        if discovered_worker:
            events.append(
                {
                    "type": "item.completed",
                    "item": {"type": "collab_tool_call", "receiver_thread_ids": ["worker"]},
                }
            )
        if usage:
            events.append(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 25,
                        "output_tokens": 10,
                        "reasoning_output_tokens": 4,
                    },
                }
            )
        path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
        return path

    def test_complete_cost_requires_all_declared_final_usage(self):
        controller = self.write_stream("controller.jsonl", "controller", discovered_worker=True)
        worker = self.write_stream("worker.jsonl", "worker")
        ledger = runner.parse_usage_events([controller, worker], self.sessions, self.pricing)
        self.assertTrue(ledger["cost_complete"])
        self.assertEqual([], ledger["missing_terminal_usage_sessions"])
        self.assertEqual("0.000510", ledger["estimated_cost_usd"])

    def test_missing_worker_usage_never_claims_complete_cost(self):
        controller = self.write_stream("controller.jsonl", "controller", discovered_worker=True)
        ledger = runner.parse_usage_events([controller], self.sessions, self.pricing)
        self.assertFalse(ledger["cost_complete"])
        self.assertIsNone(ledger["estimated_cost_usd"])
        self.assertEqual(["worker"], ledger["missing_terminal_usage_sessions"])

    def test_undeclared_discovered_session_never_claims_complete_cost(self):
        controller = self.write_stream("controller.jsonl", "controller", discovered_worker=True)
        ledger = runner.parse_usage_events([controller], self.sessions[:1], self.pricing)
        self.assertFalse(ledger["cost_complete"])
        self.assertEqual(["worker"], ledger["undeclared_sessions"])


class EvalPlusSafetyRefusalTests(unittest.TestCase):
    def test_workspace_paths_are_refused(self):
        with self.assertRaisesRegex(runner.HarnessError, "outside the agent workspace"):
            runner.require_outside_workspace(ROOT / "evals" / "generated")

    def test_direct_host_grading_is_refused_before_file_access(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(runner.HarnessError, "direct host execution"):
                runner.make_grader_request(
                    runner.DEFAULT_MANIFEST, Path(temp) / "campaign", "host", Path(temp) / "request.json"
                )

    def test_grader_result_requires_exact_hash_bound_inventory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request_path = root / "request.json"
            request = {
                "backend": "docker",
                "campaign_state_sha256": "b" * 64,
                "attempted_run_ids": ["run-a", "run-b"],
                "samples": [
                    {"run_id": "run-a", "task_id": "HumanEval/0"},
                    {"run_id": "run-b", "task_id": "Mbpp/11"},
                ],
            }
            runner.write_json(request_path, request)
            result = {
                "schema_version": 1,
                "contract": "evalplus-isolated-subset-v1",
                "request_sha256": runner.sha256_file(request_path),
                "backend": "docker",
                "runtime_identity": "evalplus@sha256:" + "a" * 64,
                "results": [
                    {
                        "run_id": "run-a",
                        "task_id": "HumanEval/0",
                        "base_pass": True,
                        "plus_pass": True,
                    },
                    {
                        "run_id": "run-b",
                        "task_id": "Mbpp/11",
                        "base_pass": True,
                        "plus_pass": False,
                    },
                ],
            }
            result_path = root / "result.json"
            runner.write_json(result_path, result)
            score = runner.verify_grader_result(request_path, result_path)
            self.assertEqual(1, score["passed_runs"])
            result["results"].pop()
            runner.write_json(result_path, result)
            with self.assertRaisesRegex(runner.HarnessError, "exact scheduled"):
                runner.verify_grader_result(request_path, result_path)


if __name__ == "__main__":
    unittest.main()
