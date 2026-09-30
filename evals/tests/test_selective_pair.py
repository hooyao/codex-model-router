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
                {"type": "response_item", "payload": {"type": "function_call",
                    "name": "spawn_agent", "call_id": "call_one",
                    "arguments": json.dumps({"task_name": "hard_worker", "model": "gpt-6-astra",
                                             "reasoning_effort": "xhigh", "fork_turns": "none"})}},
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
                                             "reasoning_effort": "xhigh", "fork_turns": "none"})}},
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
            interacted.insert(5, {"type": "event_msg", "payload": {
                "type": "item_completed", "thread_id": "parent", "turn_id": "turn-one",
                "item": {"type": "SubAgentActivity", "kind": "interacted",
                         "id": "interaction-one", "agent_thread_id": "child",
                         "agent_path": "/root/hard_worker"}}})
            self.assertEqual([], run(interacted))
            interacted[5]["payload"]["item"]["agent_thread_id"] = "unknown"
            self.assertIn("native child interaction lacks active start or parent context",
                          run(interacted))
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
