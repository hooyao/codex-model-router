"""Five offline prospective-quality paths without paid model turns."""
from __future__ import annotations

import json
from contextlib import ExitStack
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from evals.long_horizon_v1.common import TASK_ID, file_sha, sha
from evals.long_horizon_v1.grade import REVIEW_REQUIREMENTS
from evals.long_horizon_v1.protocol import StageMachine
from evals.long_horizon_v1.quality_bridge import QualityBridge, InfrastructureIncomplete
from evals.long_horizon_v1.quality_bridge import correction_astra_turns, verify_common_arm_quality
from evals.long_horizon_v1.run import live_run
from evals.long_horizon_v1.quality_adapter import (
    _functional_diagnostics, EvaluatorIncomplete, product_snapshot,
    require_complete_assessment, run_once, watch)


def save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


class QualityWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.patch0 = self.root / "candidate-0.patch"
        self.patch1 = self.root / "candidate-1.patch"
        self.patch0.write_bytes(b"revision zero")
        self.patch1.write_bytes(b"revision one")
        self.bridge = QualityBridge(self.root / "quality", manifest_sha256="a" * 64,
            preparation_sha256="b" * 64, workflow_id="one-workflow",
            parent_thread_id="same-parent", session_root=self.root / "sessions",
            operational_deadline=time.monotonic() + 30,
            original_deadline_utc_ns=time.time_ns() + 45_000_000_000)

    def respond(self, revision: int, candidate: Path, *, passed: bool,
                mismatch: dict | None = None) -> dict:
        request = self.bridge.request(revision, file_sha(candidate), candidate, 0.4)
        requirements = dict.fromkeys(REVIEW_REQUIREMENTS, passed)
        review = {"candidate_patch_sha256": file_sha(candidate),
            "manifest_sha256": "a" * 64, "assessment_status": "COMPLETE",
            "reviewer_role": "independent",
            "arm_blind": True, "reviewer_id": "independent-evaluator",
            "requirements": requirements,
            "verdict": "pass" if passed else "fail"}
        hidden = {"candidate_patch_sha256": file_sha(candidate),
            "manifest_sha256": "a" * 64, "behavior_pass": passed,
            "quality_pass": passed}
        backend = dict(hidden)
        for name, value in (("hidden-grade", hidden), ("backend-grade", backend),
                            ("semantic-review", review)):
            save(self.bridge.root / f"r{revision}-{name}.json", value)
        diagnostics = [] if passed else [{"public_requirement": "R2",
            "observed_behavior": "The operation returned before cleanup completed",
            "expected_behavior": "Cleanup should finish before the operation returns"}]
        response = {"schema_version": 1, "kind": "quality-response",
            "assessment_status": "COMPLETE",
            "workflow_id": "one-workflow", "request_id": request["request_id"],
            "request_sha256": request["request_sha256"], "revision": revision,
            "candidate_patch_sha256": file_sha(candidate),
            "verdict": "PASS" if passed else "FAIL",
            "hidden_grade_sha256": file_sha(self.bridge.root / f"r{revision}-hidden-grade.json"),
            "backend_grade_sha256": file_sha(self.bridge.root / f"r{revision}-backend-grade.json"),
            "semantic_review_sha256": file_sha(self.bridge.root / f"r{revision}-semantic-review.json"),
            "diagnostics": diagnostics,
            "evaluation_usage": {"status": "complete", "estimated_usd": 0.2,
                "estimated_usd_upper_bound": 0.2, "model_calls": 1}}
        response.update(mismatch or {})
        save(self.bridge.root / f"response-r{revision}.json", response)
        return request

    def wait(self, revision: int) -> tuple[dict, list[float]]:
        observed = []
        def meter(_revision):
            self.bridge.evaluator_cost_upper = 0.2 * (_revision + 1)
        def usage(_revision, response):
            self.bridge.evaluator_cost += 0.2
            return response["evaluation_usage"]
        with patch.object(self.bridge, "_meter", side_effect=meter), \
             patch.object(self.bridge, "_usage", side_effect=usage):
            result = self.bridge.wait(revision, lambda value: observed.append(value))
        return result, observed

    def machine(self) -> StageMachine:
        workspace = self.root / "arm"
        (workspace / ".benchmark").mkdir(parents=True)
        machine = StageMachine(workspace, "baseline", "same-parent",
            lambda patch_bytes, round_id, repair: {"round": round_id,
                "candidate_patch_sha256": sha(patch_bytes), "behavior_pass": True})
        machine.round = 2
        machine.complete = True
        machine.event("accepted-final", patch_sha256=file_sha(self.patch0))
        return machine

    def test_immediate_quality_pass_keeps_revision_zero_and_counts_evaluator(self) -> None:
        self.respond(0, self.patch0, passed=True)
        response, costs = self.wait(0)
        self.assertEqual(response["verdict"], "PASS")
        self.assertEqual(costs, [0.2])
        self.assertEqual(self.bridge.evaluator_cost, 0.2)
        self.assertEqual(list(self.bridge.requests), [0])

    def test_failure_one_same_parent_correction_then_pass_preserves_checkpoint(self) -> None:
        self.respond(0, self.patch0, passed=False)
        failure, _ = self.wait(0)
        self.assertEqual(failure["verdict"], "FAIL")
        self.assertNotIn("benchmark_oracle", self.bridge.correction_message(failure))
        machine = self.machine()
        original = machine.workspace / ".benchmark/checkpoint.json"
        original.write_text("original public checkpoint", encoding="utf-8")
        submission = machine.workspace / ".benchmark/quality-revision1.json"
        save(submission, {"schema_version": 1, "revision": 1,
            "candidate_patch_sha256": file_sha(self.patch1), "summary": "bounded correction"})
        with patch("evals.long_horizon_v1.protocol.capture_patch",
                   return_value=self.patch1.read_bytes()):
            correction = machine.submit_quality_correction("same-parent", submission)
        self.assertEqual(correction["action"], "quality-final")
        self.assertEqual(original.read_text(), "original public checkpoint")
        self.respond(1, self.patch1, passed=True)
        response, costs = self.wait(1)
        self.assertEqual(response["verdict"], "PASS")
        self.assertEqual(costs, [0.4])
        self.assertEqual(machine.thread_id, "same-parent")
        self.assertEqual(self.bridge.evaluator_cost, 0.4)
        self.assertEqual([event["kind"] for event in machine.events].count(
                         "correction-submission"), 1)

        prepared = self.root / "prepared"
        (prepared / "preparation.json").parent.mkdir(parents=True)
        (prepared / "preparation.json").write_text("{}", encoding="utf-8")
        workspace = prepared / "baseline" / ".benchmark"
        workspace.mkdir(parents=True)
        for name in ("checkpoint.json", "round1.md", "round2.md"):
            (workspace / name).write_text("original public checkpoint" if
                                           name == "checkpoint.json" else "public report")
        output = self.root / "runner-output"
        parent = "one-parent-for-four-turns"
        holder = {}
        policy = {"model": "gpt-6-astra", "effort": "xhigh", "cli_options": [],
                  "prompt_suffix_text": "", "policy_sha256": "policy",
                  "prompt_suffix_sha256": "suffix"}
        plan = {"mode": "common-quality-recovery", "pilot_id": "pilot-19",
                "preparation_root": str(prepared),
                "run_outputs": {"baseline": str(output)},
                "packet_scope_mode": "diagnostic-feasibility"}
        proof = {"cli_sha256": "cli", "code_mode_host_sha256": "host",
                 "routing_config_canonical_sha256": None,
                 "preparation": {"preparation_sha256": "prep"},
                 "zero_model_runtime_binding": {}}

        class Transport:
            def __init__(self, *_args):
                self.thread_id = parent
                self.active_turn_id = None
                self.active_turn_complete = True
                self.events = []
                self.turns = 0
                holder["transport"] = self

            def start(self): return parent

            def turn(self, _prompt, _budget, _frame):
                self.turns += 1
                self.active_turn_id = f"turn-{self.turns}"
                if self.turns == 4:
                    save(workspace / "quality-revision1.json", {
                        "schema_version": 1, "revision": 1,
                        "candidate_patch_sha256": sha(b"revision one"),
                        "summary": "one bounded correction"})
                self.events.append({"method": "turn/completed", "thread_id": parent,
                    "turn_id": self.active_turn_id, "time_ns": time.time_ns()})
                return {"thread_id": parent, "turn_id": self.active_turn_id,
                        "status": "completed"}

            def close(self): pass

            def cancel_and_drain(self, *_args, **_kwargs):
                return {"status": "verified-drained"}

        class Meter:
            def __init__(self, *_args, **_kwargs):
                self.paths = {}
                self.unknown_models = set()
                self.unknown_usage = []
                self.cost_upper = 0.1
                self.calls = 1

            def refresh(self):
                turn = holder["transport"].active_turn_id or "turn-0"
                self.paths = {Path("parent-rollout"): {"id": parent,
                    "terminal": "task_complete", "turns": [{"turn_id": turn,
                    "model": "gpt-6-astra", "effort": "xhigh"}]}}

            def dispatch_coverage_issues(self): return []

            def summary(self):
                return {"sessions": [{"id": parent, "calls": 1}],
                    "unknown_models": [], "unknown_usage": [],
                    "estimated_usd": 0.1, "estimated_usd_upper_bound": 0.1,
                    "model_calls": 1}

        class Bridge:
            def __init__(self, root, **_kwargs):
                self.root = root
                self.requests = {}
                self.responses = {}
                self.failures = {}
                self.evaluator_cost = 0.0
                self.evaluator_cost_upper = 0.0
                self.evaluation_usage_complete = True
                holder["bridge"] = self

            def request(self, revision, candidate_hash, _path, _solver_cost):
                self.requests[revision] = {"request_sha256": f"request-{revision}"}
                return self.requests[revision]

            def wait(self, revision, check_budget):
                self.evaluator_cost += 0.2
                self.evaluator_cost_upper += 0.2
                check_budget(self.evaluator_cost_upper)
                if holder.get("infra"):
                    self.failures[revision] = {"failure_sha256": "bound-failure"}
                    raise InfrastructureIncomplete("evaluator-infrastructure-incomplete")
                value = {"verdict": "FAIL" if revision == 0 else "PASS",
                    "candidate_patch_sha256": sha(b"revision zero" if revision == 0
                                                      else b"revision one"),
                    "response_sha256": f"response-{revision}",
                    "evaluation_usage": {"status": "complete"},
                    "diagnostics": [{"public_requirement": "R2",
                                     "observed_behavior": "behavior differed",
                                     "expected_behavior": "contract behavior"}]}
                self.responses[revision] = value
                return value

            @staticmethod
            def correction_message(_response): return "sanitized public correction"

        def account_stub(*_args, **_kwargs):
            return {"summary": holder["meter"].summary(), "issues": [],
                "responses": [], "by_model": {}, "failed_child_attempts": 0,
                "native_attempts": [], "interruption_verified": False}

        def gate_stub(candidate, _prepared, _output, round_id, _deadline):
            return {"round": round_id, "candidate_patch_sha256": file_sha(candidate),
                    "behavior_pass": True}

        with ExitStack() as stack:
            stack.enter_context(patch("evals.long_horizon_v1.run.manifest",
                                      return_value={"pilot": {"path": "pilot-plan-v19.json"}}))
            stack.enter_context(patch("evals.long_horizon_v1.run.pilot_plan", return_value=plan))
            stack.enter_context(patch("evals.long_horizon_v1.run.live_preflight", return_value=proof))
            stack.enter_context(patch("evals.long_horizon_v1.run.arm_execution", return_value=policy))
            stack.enter_context(patch("evals.long_horizon_v1.run.verify_cli"))
            stack.enter_context(patch("evals.long_horizon_v1.run.AppServerTransport", Transport))
            stack.enter_context(patch("evals.long_horizon_v1.run.SessionMeter",
                side_effect=lambda *_args, **_kwargs: holder.setdefault("meter", Meter())))
            stack.enter_context(patch("evals.long_horizon_v1.run.QualityBridge", Bridge))
            stack.enter_context(patch("evals.long_horizon_v1.run.grade_before_deadline",
                                      side_effect=gate_stub))
            stack.enter_context(patch("evals.long_horizon_v1.run.account",
                                      side_effect=account_stub))
            stack.enter_context(patch("evals.long_horizon_v1.protocol.manifest",
                                      return_value={}))
            stack.enter_context(patch("evals.long_horizon_v1.protocol.capture_patch",
                side_effect=lambda *_args: b"revision one" if
                    holder["transport"].turns == 4 else b"revision zero"))
            stack.enter_context(patch("evals.long_horizon_v1.protocol.checkpoint",
                                      return_value={}))
            stack.enter_context(patch("evals.long_horizon_v1.protocol.copy_assets",
                                      return_value=[]))
            result = live_run(prepared, output, "baseline", self.root / "cli",
                              self.root / "capability", self.root / "sessions")
        self.assertEqual(result["stop_reason"], "accepted-final")
        self.assertEqual(result["quality_status"], "PASS")
        self.assertEqual(len(result["attempts"]), 4)
        self.assertEqual({row["thread_id"] for row in result["attempts"]}, {parent})
        self.assertEqual(result["workflow_estimated_usd"], 0.5)
        self.assertEqual((workspace / "checkpoint.json").read_text(),
                         "original public checkpoint")
        holder["infra"] = True
        infra_output = self.root / "infra-runner-output"
        plan["run_outputs"]["baseline"] = str(infra_output)
        with ExitStack() as stack:
            stack.enter_context(patch("evals.long_horizon_v1.run.manifest",
                                      return_value={"pilot": {"path": "pilot-plan-v19.json"}}))
            stack.enter_context(patch("evals.long_horizon_v1.run.pilot_plan", return_value=plan))
            stack.enter_context(patch("evals.long_horizon_v1.run.live_preflight", return_value=proof))
            stack.enter_context(patch("evals.long_horizon_v1.run.arm_execution", return_value=policy))
            stack.enter_context(patch("evals.long_horizon_v1.run.verify_cli"))
            stack.enter_context(patch("evals.long_horizon_v1.run.AppServerTransport", Transport))
            stack.enter_context(patch("evals.long_horizon_v1.run.SessionMeter",
                side_effect=lambda *_args, **_kwargs: holder.setdefault("meter", Meter())))
            stack.enter_context(patch("evals.long_horizon_v1.run.QualityBridge", Bridge))
            stack.enter_context(patch("evals.long_horizon_v1.run.grade_before_deadline",
                                      side_effect=gate_stub))
            stack.enter_context(patch("evals.long_horizon_v1.run.account",
                                      side_effect=account_stub))
            stack.enter_context(patch("evals.long_horizon_v1.protocol.manifest",
                                      return_value={}))
            stack.enter_context(patch("evals.long_horizon_v1.protocol.capture_patch",
                                      return_value=b"revision zero"))
            stack.enter_context(patch("evals.long_horizon_v1.protocol.checkpoint",
                                      return_value={}))
            stack.enter_context(patch("evals.long_horizon_v1.protocol.copy_assets",
                                      return_value=[]))
            incomplete = live_run(prepared, infra_output, "baseline", self.root / "cli",
                                  self.root / "capability", self.root / "sessions")
        self.assertEqual(incomplete["quality_status"], "INFRA")
        self.assertEqual(incomplete["stop_reason"], "evaluator-infrastructure-incomplete")
        self.assertEqual(len(incomplete["attempts"]), 3)
        self.assertNotIn("quality-feedback",
                         [event["kind"] for event in incomplete["stage"]["events"]])

    def test_second_quality_failure_stops_without_third_revision(self) -> None:
        self.respond(0, self.patch0, passed=False)
        self.wait(0)
        self.respond(1, self.patch1, passed=False)
        second, _ = self.wait(1)
        self.assertEqual(second["verdict"], "FAIL")
        with self.assertRaisesRegex(ValueError, "invalid, or unbound"):
            self.bridge.request(2, file_sha(self.patch1), self.patch1, 0.4)
        self.assertEqual(self.bridge.evaluator_cost, 0.4)

    def test_stale_or_mismatched_response_rejected_before_feedback(self) -> None:
        self.respond(0, self.patch0, passed=False,
                     mismatch={"request_sha256": "0" * 64})
        with patch.object(self.bridge, "_meter", return_value=None), \
             self.assertRaisesRegex(ValueError, "stale, duplicate, or mismatched"):
            self.bridge.wait(0, lambda _: None)
        self.assertEqual(self.bridge.responses, {})

    def test_deadline_while_waiting_requests_cleanup_with_original_boundary(self) -> None:
        self.bridge.request(0, file_sha(self.patch0), self.patch0, 0.4)
        self.bridge.operational_deadline = time.monotonic() - 0.01
        with self.assertRaisesRegex(TimeoutError, "quality-wait-deadline"):
            self.bridge.wait(0, lambda _: self.fail("no budget after deadline"))
        cancel = json.loads((self.bridge.root / "cancel-r0.json").read_text())
        self.assertEqual(cancel["request_sha256"],
                         self.bridge.requests[0]["request_sha256"])
        self.assertFalse(self.bridge.evaluation_usage_complete)

    def test_request_publication_is_atomic_and_setup_failure_is_bound(self) -> None:
        entered, release = threading.Event(), threading.Event()
        seen, errors = [], []
        import os
        original_fsync = os.fsync
        def delayed_fsync(descriptor):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test writer not released")
            return original_fsync(descriptor)
        def reader(_request, *_args):
            seen.append(json.loads(_request.read_text(encoding="utf-8")))
            return {"verdict": "PASS"}
        def writer():
            try:
                self.bridge.request(0, file_sha(self.patch0), self.patch0, 0.4)
            except Exception as error:
                errors.append(error)
        with patch("evals.long_horizon_v1.quality_bridge.os.fsync",
                   side_effect=delayed_fsync), \
             patch("evals.long_horizon_v1.quality_adapter.run_once", side_effect=reader):
            writing = threading.Thread(target=writer)
            reading = threading.Thread(target=lambda: watch(self.bridge.root,
                self.root, self.root / "cli", self.root / "sessions"))
            reading.start(); writing.start()
            self.assertTrue(entered.wait(5))
            self.assertFalse((self.bridge.root / "request-r0.json").exists())
            self.assertEqual(seen, [])
            release.set(); writing.join(5); reading.join(5)
        self.assertEqual(errors, [])
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["candidate_patch_sha256"], file_sha(self.patch0))
        self.assertFalse((self.bridge.root / "failure-r0.json").exists())

        other = self.root / "setup-error"
        request = other / "request-r0.json"
        save(request, {"schema_version": 1, "broken": True})
        with patch("evals.long_horizon_v1.quality_adapter.verify_cli",
                   side_effect=ValueError("setup unavailable")), \
             self.assertRaisesRegex(ValueError, "setup unavailable"):
            run_once(request, self.root, self.root / "cli", self.root / "sessions")
        failure = json.loads((other / "failure-r0.json").read_text())
        self.assertEqual(failure["request_sha256"], file_sha(request))

    def test_grade_only_failure_gets_specific_sanitized_followup(self) -> None:
        class Evaluator:
            calls = []
            def turn(self, prompt, _guard):
                self.calls.append(prompt)
                return {"status": "completed", "thread_id": "evaluator"}
        class Meter:
            def refresh(self): pass
        expected = [{"public_requirement": "R2 resource ownership",
            "observed_behavior": "A supplied stream remained open after an earlier failure",
            "expected_behavior": "Every supplied stream is closed when construction fails"}]
        hidden = {"behavior_pass": False, "quality_pass": False,
            "checks": [{"exit_code": 1, "stdout_tail":
                "--- FAIL: TestBenchmarkPrivateStream observed later close count zero"}]}
        backend = {"behavior_pass": True, "quality_pass": True, "checks": []}
        evaluator = Evaluator()
        with patch("evals.long_horizon_v1.quality_adapter._last_message",
                   return_value=json.dumps({"diagnostics": expected})):
            actual = _functional_diagnostics(evaluator, Meter(), "evaluator",
                                             hidden, backend, [], lambda: None)
        self.assertEqual(actual, expected)
        self.assertEqual(len(evaluator.calls), 1)
        self.assertIn("failed_components", evaluator.calls[0])
        self.assertNotIn("TestBenchmark", json.dumps(actual))
        with patch("evals.long_horizon_v1.quality_adapter._last_message",
                   return_value=json.dumps({"diagnostics": [{**expected[0],
                       "observed_behavior": "TestBenchmarkPrivateStream failed"}]})), \
             self.assertRaisesRegex(ValueError, "private"):
            _functional_diagnostics(Evaluator(), Meter(), "evaluator",
                                    hidden, backend, [], lambda: None)

    def test_multiple_astra_followups_share_one_correction_parent(self) -> None:
        submit_ns = 1791519000000000000
        done = "2026-10-09T04:09:00Z"
        run = {"attempts": [{"quality_revision": 1, "turn_id": "correction-parent",
                             "stage_event_start": 0}],
               "native_attempts": [{"child_id": "expert", "turns": [
                   {"turn_id": "analysis", "parent_turn_id": "correction-parent",
                    "model": "gpt-6-astra", "effort": "xhigh",
                    "terminal": "task_complete", "terminal_timestamp": done},
                   {"turn_id": "review", "parent_turn_id": "correction-parent",
                    "model": "gpt-6-astra", "effort": "xhigh",
                    "terminal": "task_complete", "terminal_timestamp": done}]}]}
        events = [{"kind": "correction-submission", "time_ns": submit_ns}]
        self.assertEqual(correction_astra_turns(run, events), ["analysis", "review"])
        run["native_attempts"][0]["turns"][1]["terminal_timestamp"] = "2026-10-09T04:11:00Z"
        with self.assertRaisesRegex(ValueError, "timing"):
            correction_astra_turns(run, events)
        run["native_attempts"][0]["turns"][1]["terminal_timestamp"] = done
        for turn in run["native_attempts"][0]["turns"]:
            turn["parent_turn_id"] = "wrong-parent"
        with self.assertRaisesRegex(ValueError, "missing"):
            correction_astra_turns(run, events)

    def test_incomplete_assessment_is_infrastructure_and_retains_known_cost(self) -> None:
        report = {"assessment_status": "INCOMPLETE",
            "requirements": dict.fromkeys(REVIEW_REQUIREMENTS, False),
            "diagnostics": [{"public_requirement": "all",
                "observed_behavior": "The evaluator could not read the candidate",
                "expected_behavior": "A complete product assessment is required"}]}
        with self.assertRaises(EvaluatorIncomplete):
            require_complete_assessment(report)
        request = self.bridge.request(0, file_sha(self.patch0), self.patch0, 0.4)
        save(self.bridge.root / "evaluator-r0.json", {
            "schema_version": 1, "request_sha256": request["request_sha256"],
            "thread_id": "evaluator-thread", "session_root": str(self.bridge.session_root),
            "model": "gpt-6-astra", "effort": "xhigh"})
        save(self.bridge.root / "r0-incomplete-model-report.json", report)
        save(self.bridge.root / "failure-r0.json", {
            "schema_version": 1, "request_sha256": request["request_sha256"],
            "assessment_status": "INCOMPLETE", "quality_status": "INFRA",
            "evaluator_thread_id": "evaluator-thread",
            "session_root": str(self.bridge.session_root),
            "model_report_sha256": file_sha(
                self.bridge.root / "r0-incomplete-model-report.json"),
            "evaluation_usage": {"status": "complete", "estimated_usd": 0.3775635,
                "estimated_usd_upper_bound": 0.3775635, "model_calls": 8},
            "candidate_unchanged": True, "reason": "helper setup failed"})
        def meter(_revision):
            self.bridge.evaluator_cost_upper = 0.3775635
        def usage(_revision, failure):
            self.bridge.evaluator_cost += failure["evaluation_usage"]["estimated_usd"]
            return failure["evaluation_usage"]
        with patch.object(self.bridge, "_meter", side_effect=meter), \
             patch.object(self.bridge, "_usage", side_effect=usage), \
             self.assertRaises(InfrastructureIncomplete):
            self.bridge.wait(0, lambda _: None)
        self.assertEqual(self.bridge.evaluator_cost, 0.3775635)
        self.assertEqual(self.bridge.responses, {})
        self.assertEqual(list(self.bridge.failures), [0])
        self.assertTrue(self.bridge.evaluation_usage_complete)

    def test_product_content_and_file_set_changes_are_detected(self) -> None:
        product = self.root / "product"
        product.mkdir()
        (product / "main.go").write_text("package main\n", encoding="utf-8")
        original = product_snapshot(product)
        (product / "main.go").write_text("package changed\n", encoding="utf-8")
        changed = product_snapshot(product)
        self.assertEqual(changed["fileset_sha256"], original["fileset_sha256"])
        self.assertNotEqual(changed["content_sha256"], original["content_sha256"])
        (product / "main.go").write_text("package main\n", encoding="utf-8")
        (product / "extra.txt").write_text("new", encoding="utf-8")
        added = product_snapshot(product)
        self.assertNotEqual(added["fileset_sha256"], original["fileset_sha256"])

    def test_baseline_full_quality_chain_checks_wall_receipt(self) -> None:
        prepared = self.root / "prepared"
        save(prepared / "preparation.json", {"fresh": True})
        root = self.root / "baseline-live"
        quality_root = root / "quality"
        save(quality_root / "r0-semantic-review.json", {"verdict": "pass"})
        save(self.root / "manifest.json", {"pilot": {"path": "pilot-plan-v20.json"}})
        plan = {"mode": "common-quality-recovery", "pilot_id": "pilot-20",
            "run_outputs": {"baseline": "baseline-live"},
            "end_to_end": {"receipts": {"baseline": "baseline-wall.json"}}}
        save(self.root / "pilot-plan-v20.json", plan)
        patch_hash = "a" * 64
        events, previous = [], "0" * 64
        for kind, round_id in (("reveal", 1), ("reveal", 2),
                               ("accepted-final", 2), ("final-quality", 2)):
            event = {"seq": len(events), "previous_sha256": previous,
                "arm": "baseline", "thread_id": "baseline-parent",
                "kind": kind, "round": round_id}
            if kind == "final-quality":
                event.update(revision=0, patch_sha256=patch_hash)
            event["sha256"] = sha(json.dumps(event, sort_keys=True,
                separators=(",", ":")).encode())
            events.append(event)
            previous = event["sha256"]
        started = 1_000_000_000_000
        run = {"stop_reason": "accepted-final", "quality_status": "PASS",
            "workflow_cost_status": "complete", "cost_status": "complete",
            "usage_issues": [], "runtime_binding": {
                "preparation_sha256": file_sha(prepared / "preparation.json")},
            "stage": {"complete": True, "round": 2,
                "manifest_sha256": file_sha(self.root / "manifest.json"),
                "thread_id": "baseline-parent", "events": events,
                "chain_sha256": previous},
            "parent_thread_id": "baseline-parent",
            "quality_bridge": {"root": str(quality_root.resolve()),
                "request_sha256": {"0": "request"},
                "response_sha256": {"0": "response"},
                "evaluator_cost_status": "complete", "evaluator_estimated_usd": 0.2},
            "started_utc_ns": started, "wall_seconds": 10.0,
            "usage": {"estimated_usd": 0.1, "estimated_usd_upper_bound": 0.1},
            "workflow_estimated_usd": 0.1 + 0.2,
            "workflow_estimated_usd_upper_bound": 0.1 + 0.2}
        save(root / "run.json", run)
        files = {"hidden_race_grade": "hidden", "backend_grade": "backend",
                 "semantic_review": file_sha(quality_root / "r0-semantic-review.json")}
        save(self.root / "baseline-wall.json", {"schema_version": 1,
            "kind": "arm-end-to-end-boundary", "pilot_id": plan["pilot_id"],
            "task_id": TASK_ID, "arm": "baseline",
            "manifest_sha256": file_sha(self.root / "manifest.json"),
            "pilot_plan_sha256": file_sha(self.root / "pilot-plan-v20.json"),
            "preparation_sha256": file_sha(prepared / "preparation.json"),
            "run_sha256": file_sha(root / "run.json"),
            "quality_files_sha256": files, "started_utc_ns": started,
            "run_wall_seconds": 10.0, "quality_finished_utc_ns": started + 12_000_000_000,
            "end_to_end_wall_seconds": 12.0})
        checked_quality = {"revision": 0,
            "response": {"candidate_patch_sha256": patch_hash, **{
                "hidden_grade_sha256": files["hidden_race_grade"],
                "backend_grade_sha256": files["backend_grade"],
                "semantic_review_sha256": files["semantic_review"]}},
            "evaluator_estimated_usd": 0.2, "evaluator_cost_upper": 0.2}
        with patch("evals.long_horizon_v1.quality_bridge.HERE", self.root), \
             patch("evals.long_horizon_v1.quality_bridge.manifest",
                   return_value={"pilot": {"path": "pilot-plan-v20.json"}}), \
             patch.object(QualityBridge, "recheck", return_value=checked_quality), \
             patch("evals.long_horizon_v1.standalone_quality._grade",
                   side_effect=lambda *_args, hidden, **_kwargs:
                       "hidden" if hidden else "backend"):
            result = verify_common_arm_quality(prepared, plan, run, "baseline",
                                                self.root / "sessions", require_wall=True)
        self.assertEqual(result["end_to_end_wall_seconds"], 12.0)
        self.assertEqual(result["end_to_end_sha256"],
                         file_sha(self.root / "baseline-wall.json"))


if __name__ == "__main__":
    unittest.main()
