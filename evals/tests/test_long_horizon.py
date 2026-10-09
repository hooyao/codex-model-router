"""Offline protocol checks for the staged OCI benchmark."""
from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import copy
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch as mock_patch

from evals.long_horizon_v1.common import (ASSET_ROOT, capture_patch, copy_assets,
    file_sha, manifest, sha)
from evals.long_horizon_v1.assets.submit_checkpoint import (
    public_overlay, submit as submit_checkpoint)
from evals.long_horizon_v1.controls import controls
from evals.long_horizon_v1.accounting import CanarySessionMeter, account
from evals.long_horizon_v1.grade import (checked_go_status, go_test,
    pinned_go_paths, run_wsl_go, stop_wsl_grader, wsl_path)
from evals.long_horizon_v1.grade import BACKEND_PACKAGES, PACKAGES, REVIEW_REQUIREMENTS
from evals.long_horizon_v1.quality_admission import (RACE_PACKAGES, _tests,
    create_quality_admission, verify_quality_admission)
from evals.long_horizon_v1.end_to_end import finalize_arm
from evals.long_horizon_v1.diagnostic_reference import verify_diagnostic_reference
from evals.long_horizon_v1.live_preflight import (
    bind_capabilities, forbid_model_turn_start)
from evals.long_horizon_v1.cancellation_adjudication import (_judge,
    _reconcile_usage, AdjudicationError, adjudicate, prior_adjudication_bound)
from evals.long_horizon_v1.cancellation_canary import (account_bounded,
    READY_MARKER, RUNNING_MARKER, RUNNING_FILE, CompletedTargetError,
    _baseline_runtime_abort,
    cancellation_target_ready,
    run as run_cancellation_canary, start_canary_turn)
from evals.long_horizon_v1.collect import _arm, collect_standalone
from evals.long_horizon_v1.fork_policy import planned_commitment
from evals.long_horizon_v1.protocol import ProtocolError, StageMachine, checkpoint as validate_checkpoint
from evals.long_horizon_v1.run import (ActiveTelemetryGuard, MeterRefreshGate, _canonical_sha,
    MAX_SECONDS, RECEIPT_RESERVE_SECONDS, baseline_has_child,
    grade_before_deadline, live_preflight, live_run,
    parent_selector, pilot_plan, submitted_prompt, final_fork_observation)
from evals.long_horizon_v1.prepare import _product_files, prepare, verify_prepared
from evals.long_horizon_v1.runtime_binding import (bind_arm_config, fixture_config,
    arm_execution, verify_arm_config, verify_arm_runtime, verify_cli)
from evals.long_horizon_v1.transport import (AppServerTransport, TransportError,
    meter_relevant_frame)


def expanded_cancel_usage(source: dict, root_turn: str, turns: dict[str, str],
                          terminal_timestamp: dict[str, str]) -> dict:
    """Simulate the current meter fields appended to a legacy usage receipt."""
    current = copy.deepcopy(source)
    for session in current["sessions"]:
        session_id = session["id"]
        session["turns"][0].update({"calls": session["calls"],
            "terminal": "turn_aborted",
            "terminal_timestamp": terminal_timestamp[session_id]})
        for index, response in enumerate(session["responses"]):
            response.update({"turn_id": turns[session_id],
                "root_turn_id": root_turn,
                "response_id_sha256": f"{session_id}-{index}".encode().hex().ljust(64, "0")[:64]})
    return current


def write_legacy_cancel_decision(path: Path, source_path: Path,
                                 arm: str, proof: dict) -> None:
    prior_proof = {key: value for key, value in proof.items()
                   if not key.endswith("_sha256") or key in
                   ("parent_rollout", "child_rollout", "marker_sha256")}
    path.write_text(json.dumps({"schema_version": 1,
        "kind": "cancellation-adjudication", "status": "verified",
        "source_receipt_status": "UNKNOWN",
        "source_receipt_sha256": file_sha(source_path), "arm": arm,
        "live_enabled": False, "reason": None, "proof": prior_proof}),
        encoding="utf-8")


def git(workspace: Path, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=workspace, check=True,
                          capture_output=True).stdout


class LongHorizonProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        git(self.workspace, "init", "-q")
        git(self.workspace, "config", "user.name", "Fixture")
        git(self.workspace, "config", "user.email", "fixture@example.invalid")
        (self.workspace / "README.md").write_text("seed\n", encoding="utf-8")
        git(self.workspace, "add", "--all")
        git(self.workspace, "commit", "-q", "-m", "seed")
        self.seed_tree = git(self.workspace, "rev-parse", "HEAD^{tree}").decode().strip()
        copy_assets(0, self.workspace)

    def local_plugin_spec(self, spec: dict) -> dict:
        """Bind an installed-plugin copy to test-local hashes, independent of CI host."""
        source = Path(__file__).resolve().parents[2] / "plugins" / "codex-model-router"
        plugin = self.workspace / "installed-plugin"
        for relative in (".codex-plugin/plugin.json", "hooks/router_hook.py",
                         "hooks/routing_config.py", "hooks/hooks.json"):
            destination = plugin / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative, destination)
        spec = copy.deepcopy(spec)
        runtime = spec["runtime"]
        runtime["plugin_root"] = str(plugin)
        for key, relative in (("plugin_manifest_sha256", ".codex-plugin/plugin.json"),
                              ("router_hook_sha256", "hooks/router_hook.py"),
                              ("routing_validator_sha256", "hooks/routing_config.py"),
                              ("plugin_hooks_sha256", "hooks/hooks.json")):
            runtime[key] = file_sha(plugin / relative)
        return spec

    def local_cli(self, spec: dict) -> Path:
        """Create pinned CLI and sidecar bytes for offline path and hash checks."""
        cli = self.workspace / "desktop" / "codex.exe"
        cli.parent.mkdir(parents=True, exist_ok=True)
        cli.write_bytes(b"test desktop CLI")
        host = cli.with_name("codex-code-mode-host.exe")
        host.write_bytes(b"test code-mode host")
        spec["runtime"].update(cli=str(cli), cli_sha256=file_sha(cli),
                               code_mode_host_sha256=file_sha(host))
        return cli

    def require_retained_adjudication(self) -> None:
        path = (ASSET_ROOT.parent / "_scratch" / "cancellation-canary" /
                "retry-20260930120001-96e403dd" / "adjudication-v2.json")
        if not path.is_file():
            self.skipTest("historical cancellation adjudication is retained locally")

    def test_go_path_overrides_keep_binary_pins(self) -> None:
        specification = manifest()["toolchain"]
        binary = self.workspace / "go.exe"
        binary.write_bytes(b"unapproved-go-binary")
        with mock_patch.dict(os.environ, {
                "LONG_HORIZON_GO_WINDOWS": str(binary.resolve()),
                "LONG_HORIZON_GO_LINUX": "/opt/pinned/go"}):
            self.assertEqual(pinned_go_paths(specification),
                             (binary.resolve(), "/opt/pinned/go"))
            with self.assertRaisesRegex(RuntimeError, "pinned Go binary unavailable or changed"):
                go_test(self.workspace, self.workspace / "grade-output")
        with mock_patch.dict(os.environ, {"LONG_HORIZON_GO_LINUX": "relative/go"}):
            with self.assertRaisesRegex(ValueError, "absolute paths"):
                pinned_go_paths(specification)

    def submit(self, machine: StageMachine, round_id: int, *, thread: str = "parent") -> dict:
        submit_checkpoint(self.workspace, round_id, "checked", selected=("diff",),
                          base_tree=self.seed_tree)
        path = self.workspace / ".benchmark" / "checkpoint.json"
        return machine.submit(thread, path)

    def test_frozen_inputs_and_no_future_reveal(self) -> None:
        spec = manifest()
        self.assertEqual(file_sha(self.workspace / ".benchmark" / "round0.md"),
                         spec["assets"]["round0.md"])
        self.assertEqual(file_sha(self.workspace / ".benchmark" / "checkpoint_hash.py"),
                         spec["assets"]["checkpoint_hash.py"])
        self.assertEqual(file_sha(self.workspace / ".benchmark" / "submit_checkpoint.py"),
                         spec["assets"]["submit_checkpoint.py"])
        self.assertFalse((self.workspace / ".benchmark" / "round1.md").exists())
        self.assertFalse((self.workspace / ".benchmark" / "round2.md").exists())
        progress = self.workspace / ".benchmark" / "public" / "internal" / "storage" / "fs" / "oci" / "benchmark_g1_progress_test.go"
        self.assertFalse(progress.exists())
        machine = StageMachine(self.workspace, "baseline", "parent",
            lambda patch, round_id, repair: {"round": round_id,
                "candidate_patch_sha256": sha(patch), "behavior_pass": True},
            base_tree=self.seed_tree)
        self.assertEqual(self.submit(machine, 0)["action"], "continue")
        self.assertTrue((self.workspace / ".benchmark" / "round1.md").exists())
        self.assertTrue(progress.exists())
        self.assertEqual(file_sha(progress),
                         spec["assets"]["public/g1/internal/storage/fs/oci/benchmark_g1_progress_test.go"])
        g1_overlay = json.loads(public_overlay(self.workspace, 1).read_text(encoding="utf-8"))
        self.assertEqual(len(g1_overlay["Replace"]), 3)
        self.assertFalse((self.workspace / ".benchmark" / "round2.md").exists())
        self.assertEqual(self.submit(machine, 1)["action"], "continue")
        self.assertTrue((self.workspace / ".benchmark" / "round2.md").exists())
        g2_overlay = json.loads(public_overlay(self.workspace, 2).read_text(encoding="utf-8"))
        self.assertEqual(len(g2_overlay["Replace"]), 6)
        self.assertEqual(self.submit(machine, 2)["action"], "final")
        self.assertFalse((self.workspace / ".benchmark" / "oracle").exists())
        self.assertFalse(any("benchmark_oracle" in str(path)
                             for path in (self.workspace / ".benchmark").rglob("*")))
        with self.assertRaises(ProtocolError):
            self.submit(machine, 2)

    def test_oracle_erratum_is_offline_and_preserves_historical_grade(self) -> None:
        spec = manifest()
        erratum = spec["oracle_erratum"]
        old_grade_path = (ASSET_ROOT.parent / "_scratch" / "pilot-06-evidence" /
                          "baseline-hidden-grade" / "grade.json")
        if not old_grade_path.is_file():
            self.skipTest("historical pilot-06 raw grade is retained locally")
        self.assertEqual(erratum["version"], "r9e-20261001")
        self.assertEqual(set(erratum["changed_assets"]), {
            "oracle/internal/oci/benchmark_oracle_test.go",
            "oracle/internal/storage/fs/oci/benchmark_oracle_schedule_test.go",
        })
        self.assertEqual(file_sha(old_grade_path), erratum["historical_hidden_grade_sha256"])
        old_tests = dict(item.split(":", 1) for item in
                         json.loads(old_grade_path.read_text(encoding="utf-8"))["tests"])
        for name, digest in spec["assets"].items():
            if name == spec["shutdown_join_oracle"]["new_asset"]:
                continue
            if name.startswith("oracle/"):
                relative = name[len("oracle/"):]
            elif name.startswith("public/"):
                relative = name.split("/", 2)[2]
            else:
                continue
            with self.subTest(asset=name):
                self.assertIn(relative, old_tests)
                self.assertEqual(digest != old_tests[relative],
                                 name in erratum["changed_assets"])
        with mock_patch("evals.long_horizon_v1.run.manifest",
                        return_value={**spec, "live_enabled": False}):
            with self.assertRaisesRegex(ValueError, "not live-enabled"):
                live_preflight(None, None, "baseline")

    def test_shutdown_join_oracle_is_offline_and_preserves_pilot13(self) -> None:
        spec = manifest()
        binding = spec["shutdown_join_oracle"]
        self.assertFalse(spec["live_enabled"])
        self.assertEqual(spec["status"], "offline-shutdown-join-r12e")
        self.assertEqual(binding["version"], "r12e-20261001")
        self.assertEqual(file_sha(ASSET_ROOT.parent / "controls" /
                         "control-matrix-r12d-20261001.json"),
                         binding["prior_matrix_sha256"])
        self.assertEqual(file_sha(ASSET_ROOT.parent / "controls" /
                         "shutdown-join-r12d-receipt.json"),
                         binding["prior_receipt_sha256"])
        self.assertEqual(binding["test"],
                         "TestBenchmarkOracleCloseWaitsForAcquisitionCleanup")
        self.assertEqual(file_sha(ASSET_ROOT / binding["new_asset"]),
                         spec["assets"][binding["new_asset"]])
        self.assertNotIn("ErrClosed", (ASSET_ROOT / binding["new_asset"]).read_text(
            encoding="utf-8"))
        historical = ASSET_ROOT.parent / "_scratch" / "pilot-13-evidence"
        if not (historical / "blind-review" / "candidate.patch").is_file():
            self.skipTest("historical pilot-13 raw evidence is retained locally")
        self.assertEqual(file_sha(historical / "blind-review" / "candidate.patch"),
                         binding["historical_candidate_patch_sha256"])
        self.assertEqual(file_sha(historical / "blind-review" / "semantic-review.json"),
                         binding["historical_semantic_review_sha256"])
        self.assertEqual(file_sha(historical / "quality-diagnostic" /
                         "hidden-race" / "grade.json"),
                         binding["historical_hidden_grade_sha256"])
        self.assertFalse(any("benchmark_oracle" in str(path)
                             for path in (self.workspace / ".benchmark").rglob("*")))
        with self.assertRaisesRegex(ValueError, "not live-enabled"):
            live_preflight(None, None, "treatment")

    def test_one_repair_and_parent_lineage(self) -> None:
        machine = StageMachine(self.workspace, "treatment", "parent",
            lambda patch, round_id, repair: {"round": round_id,
                "candidate_patch_sha256": sha(patch), "behavior_pass": False},
            base_tree=self.seed_tree)
        with self.assertRaises(ProtocolError):
            self.submit(machine, 0, thread="different")
        self.assertEqual(self.submit(machine, 0)["action"], "repair")
        self.assertEqual(self.submit(machine, 0)["action"], "stop")
        self.assertFalse((self.workspace / ".benchmark" / "round1.md").exists())

    def test_patch_capture_includes_untracked_product_and_excludes_overlay(self) -> None:
        self.assertEqual(capture_patch(self.workspace, self.seed_tree), b"")
        (self.workspace / "new.go").write_text("package main\n", encoding="utf-8")
        patch = capture_patch(self.workspace, self.seed_tree)
        self.assertIn(b"new.go", patch)
        self.assertNotIn(b".benchmark", patch)
        self.assertEqual(capture_patch(self.workspace, self.seed_tree), patch)

    def test_checkpoint_helper_includes_commits_and_untracked_files(self) -> None:
        (self.workspace / "README.md").write_text("committed\n", encoding="utf-8")
        git(self.workspace, "add", "README.md")
        git(self.workspace, "commit", "-q", "-m", "candidate")
        (self.workspace / "new.go").write_text("package main\n", encoding="utf-8")
        before = git(self.workspace, "status", "--porcelain")
        patch = capture_patch(self.workspace, self.seed_tree)
        self.assertIn(b"+committed", patch)
        self.assertIn(b"new.go", patch)
        self.assertNotIn(b".benchmark", patch)
        self.assertEqual(git(self.workspace, "status", "--porcelain"), before)
        output = subprocess.check_output([
            sys.executable, str(ASSET_ROOT / "checkpoint_hash.py"),
            "--workspace", str(self.workspace), "--base-tree", self.seed_tree])
        self.assertEqual(output.decode().strip(), sha(patch))
        submitted = submit_checkpoint(self.workspace, 0, "Committed and untracked changes",
            selected=("diff",), base_tree=self.seed_tree)
        self.assertEqual(submitted["candidate_patch_sha256"], sha(patch))
        self.assertEqual(git(self.workspace, "status", "--porcelain"), before)

    def test_submit_helper_records_real_failure_and_hashes(self) -> None:
        (self.workspace / "README.md").write_text("bad trailing space \n", encoding="utf-8")
        authored = submit_checkpoint(self.workspace, 0, "Whitespace check failed",
            selected=("diff",), base_tree=self.seed_tree)
        check = authored["checks"][0]
        self.assertNotEqual(check["exit_code"], 0)
        self.assertEqual(check["stdout_sha256"],
                         file_sha(self.workspace / check["stdout_path"]))
        self.assertEqual(check["stderr_sha256"],
                         file_sha(self.workspace / check["stderr_path"]))
        path = self.workspace / ".benchmark" / "checkpoint.json"
        self.assertEqual(validate_checkpoint(path, 0, authored["candidate_patch_sha256"]),
                         authored)

    def test_checkpoint_rejects_missing_or_tampered_exit_and_output(self) -> None:
        authored = submit_checkpoint(self.workspace, 0, "Checked",
            selected=("diff",), base_tree=self.seed_tree)
        path = self.workspace / ".benchmark" / "checkpoint.json"
        for replacement in (None, "0", True):
            damaged = json.loads(json.dumps(authored))
            if replacement is None:
                del damaged["checks"][0]["exit_code"]
            else:
                damaged["checks"][0]["exit_code"] = replacement
            path.write_text(json.dumps(damaged), encoding="utf-8")
            with self.assertRaisesRegex(ProtocolError,
                                        r"checkpoint\.checks\[0\]\.exit_code: expected integer"):
                validate_checkpoint(path, 0, authored["candidate_patch_sha256"])
        damaged = json.loads(json.dumps(authored))
        damaged["checks"][0]["exit_code"] = 7
        path.write_text(json.dumps(damaged), encoding="utf-8")
        with self.assertRaisesRegex(ProtocolError, "exit_code: expected integer matching"):
            validate_checkpoint(path, 0, authored["candidate_patch_sha256"])
        path.write_text(json.dumps(authored), encoding="utf-8")
        (self.workspace / authored["checks"][0]["stdout_path"]).write_text("tampered",
                                                              encoding="utf-8")
        with self.assertRaisesRegex(ProtocolError, "stdout_sha256"):
            validate_checkpoint(path, 0, authored["candidate_patch_sha256"])

    def test_protocol_repair_names_field_and_submission_helper(self) -> None:
        authored = submit_checkpoint(self.workspace, 0, "Checked",
            selected=("diff",), base_tree=self.seed_tree)
        del authored["checks"][0]["exit_code"]
        path = self.workspace / ".benchmark" / "checkpoint.json"
        path.write_text(json.dumps(authored), encoding="utf-8")
        machine = StageMachine(self.workspace, "baseline", "parent",
            lambda patch, round_id, repair: self.fail("gate must not run"),
            base_tree=self.seed_tree)
        action = machine.submit("parent", path)
        self.assertEqual(action["action"], "repair")
        self.assertIn("checks[0].exit_code: expected integer", action["message"])
        self.assertIn("python .benchmark/submit_checkpoint.py --round 0", action["message"])
        self.assertLess(len(action["message"]), 700)

    def test_public_overlay_uses_only_revealed_rounds(self) -> None:
        overlay = public_overlay(self.workspace, 0)
        mapping = json.loads(overlay.read_text(encoding="utf-8"))["Replace"]
        self.assertEqual(len(mapping), 1)
        self.assertTrue(all("benchmark_g0" in target for target in mapping.values()))
        with self.assertRaisesRegex(ValueError, "round 1 has not been revealed"):
            submit_checkpoint(self.workspace, 1, "Too early", selected=("diff",),
                              base_tree=self.seed_tree)

    def test_repair_feedback_has_failed_case_and_bounded_output(self) -> None:
        failure = {"round": 0, "candidate_patch_sha256": sha(b""),
                   "behavior_pass": False, "checks": [{"exit_code": 1,
                   "stdout_tail": "x" * 4000 + "\n--- FAIL: TestBenchmarkG0ReferenceSelection\n",
                   "stderr_tail": "compiler detail"}]}
        machine = StageMachine(self.workspace, "baseline", "parent",
            lambda patch, round_id, repair: failure, base_tree=self.seed_tree)
        action = self.submit(machine, 0)
        self.assertEqual(action["action"], "repair")
        self.assertIn("TestBenchmarkG0ReferenceSelection", action["message"])
        self.assertIn("compiler detail", action["message"])
        self.assertLess(len(action["message"]), 2500)

    def test_final_public_recovery_feedback_is_equal_and_bounded_for_both_arms(self) -> None:
        messages = []
        for arm in ("baseline", "treatment"):
            with tempfile.TemporaryDirectory() as directory:
                workspace = Path(directory) / "arm"
                shutil.copytree(self.workspace, workspace)
                machine = StageMachine(workspace, arm, "parent",
                    lambda patch, round_id, repair: {
                        "round": round_id, "candidate_patch_sha256": sha(patch),
                        "behavior_pass": round_id < 2,
                        "checks": [{"exit_code": 1, "stdout_tail":
                            "--- FAIL: TestBenchmarkG2CloseJoinsAcquisitionCleanup\n",
                            "stderr_tail": ""}]}, base_tree=self.seed_tree)
                for round_id in (0, 1):
                    submit_checkpoint(workspace, round_id, "checked", selected=("diff",),
                                      base_tree=self.seed_tree)
                    self.assertEqual(machine.submit("parent", workspace / ".benchmark" /
                                                    "checkpoint.json")["action"], "continue")
                submit_checkpoint(workspace, 2, "checked", selected=("diff",),
                                  base_tree=self.seed_tree)
                first = machine.submit("parent", workspace / ".benchmark" /
                                       "checkpoint.json")
                self.assertEqual(first["action"], "repair")
                self.assertIn("write a short repair plan", first["message"])
                self.assertIn("TestBenchmarkG2CloseJoinsAcquisitionCleanup", first["message"])
                messages.append(first["message"])
                second = machine.submit("parent", workspace / ".benchmark" /
                                        "checkpoint.json")
                self.assertEqual(second["action"], "stop")
                self.assertEqual([event["kind"] for event in machine.events].count(
                                 "repair-allowed"), 1)
        self.assertEqual(messages[0], messages[1])

    def test_recovery_plan_binds_fresh_fixture(self) -> None:
        spec = manifest()
        plan = pilot_plan(spec)
        expected = (["treatment"] if plan["mode"] == "fixed-baseline-recovery"
                    else ["baseline", "treatment"])
        self.assertEqual(plan["arm_order"], expected)
        self.assertEqual(plan["limits_per_arm"]["automatic_retries"], 0)
        if plan["mode"] == "common-quality-recovery":
            self.assertEqual(plan["limits_per_arm"]["quality_correction_episodes"], 1)
            self.assertEqual(plan["feedback"]["quality_revisions"], [0, 1])
            self.assertTrue(plan["feedback"]["hidden_assets_private"])
        else:
            self.assertEqual(plan["feedback"]["repair_checkpoints"], 1)
            self.assertFalse(plan["feedback"]["hidden_oracle_disclosed"])
        self.assertEqual(plan["fixture"]["assets_sha256"], spec["assets"])
        from evals.long_horizon_v1.prepare import _arms_for_plan
        self.assertEqual(_arms_for_plan(spec), tuple(expected))

    def test_mutation_controls_use_hidden_oracle_at_g2(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifest.json").write_text("{}", encoding="utf-8")
            for name in ("positive", "wrong-manifest-digest", "leaked-stream"):
                (root / f"{name}.patch").write_bytes(name.encode())
            specs = {name: {"path": f"{name}.patch",
                            "sha256": file_sha(root / f"{name}.patch")}
                     for name in ("positive", "wrong-manifest-digest", "leaked-stream")}
            calls = []
            def fake_grade(candidate, prepared, output, round_id, hidden=False,
                           run_race=False):
                calls.append((candidate.stem, round_id, hidden, run_race))
                output.mkdir()
                (output / "grade.json").write_text("{}", encoding="utf-8")
                (output / "go-test").mkdir()
                marker = ("TestBenchmarkOracleManifestIdentityViews"
                          if candidate.stem == "wrong-manifest-digest" else
                          "TestBenchmarkOracleAllSuppliedStreamsCloseOnFailure")
                (output / "go-test" / "stdout.txt").write_text(
                    "--- FAIL: " + marker + " (0.01s)\n", encoding="utf-8")
                return {"behavior_pass": candidate.stem == "positive",
                        "checks": [{"stdout_tail": marker, "exit_code": 1}]}
            with mock_patch("evals.long_horizon_v1.controls.HERE", root), \
                 mock_patch("evals.long_horizon_v1.controls.manifest",
                            return_value={"controls": specs}), \
                 mock_patch("evals.long_horizon_v1.grade.wsl_path",
                            side_effect=lambda path: path.as_posix()), \
                 mock_patch("evals.long_horizon_v1.controls.grade", fake_grade):
                result = controls(root, root / "results")
            self.assertTrue(result["controls"]["wrong-manifest-digest"]["status"] == "matched")
            self.assertTrue(result["controls"]["leaked-stream"]["status"] == "matched")
            self.assertIn(("wrong-manifest-digest", 2, True, False), calls)
            self.assertIn(("leaked-stream", 2, True, False), calls)

    def test_control_markers_require_only_named_failure(self) -> None:
        from evals.long_horizon_v1.controls import FAILURE_LINE, expected_failure_names
        output = "--- FAIL: TestBenchmarkG1AliasAndMove (0.01s)\n"
        self.assertEqual(FAILURE_LINE.findall(output), ["TestBenchmarkG1AliasAndMove"])
        output += "--- FAIL: TestUnrelated (0.01s)\n"
        self.assertNotEqual(sorted(set(FAILURE_LINE.findall(output))),
                            ["TestBenchmarkG1AliasAndMove"])
        self.assertEqual(expected_failure_names("round1-only", 2, False),
                         ["TestBenchmarkG2CancelledAndClosed",
                          "TestBenchmarkOracleHeldCallbackPublicationAndShutdown"])

    def test_frozen_control_patches_exclude_unrelated_and_oracle_files(self) -> None:
        fixture = ASSET_ROOT.parent
        for name, descriptor in manifest()["controls"].items():
            with self.subTest(control=name):
                patch = fixture / descriptor["path"]
                self.assertEqual(file_sha(patch), descriptor["sha256"])
                payload = patch.read_bytes()
                self.assertNotIn(b"__pycache__", payload)
                self.assertNotIn(b".benchmark/", payload)
                self.assertNotIn(b"benchmark_oracle", payload)

    def test_live_fails_closed_before_any_model_call(self) -> None:
        with mock_patch("evals.long_horizon_v1.run.manifest",
                        return_value={**manifest(), "live_enabled": False}):
            with self.assertRaisesRegex(ValueError, "not live-enabled"):
                live_preflight(None, None, "baseline")
        self.assertEqual(parent_selector("baseline"), ("gpt-6-astra", "xhigh"))
        self.assertEqual(parent_selector("treatment"), ("gpt-6-sol", "low"))

    def test_both_submitted_initial_prompts_state_current_limits(self) -> None:
        spec = manifest()
        shared = ((ASSET_ROOT / "round0.md").read_text(encoding="utf-8") + "\n\n" +
                  (ASSET_ROOT / "environment.md").read_text(encoding="utf-8"))
        for arm in ("baseline", "treatment"):
            with self.subTest(arm=arm):
                prompt = submitted_prompt(shared, arm, spec)
                self.assertIn("4,500 seconds per arm", prompt)
                self.assertIn("25 seconds reserved for cancellation and cleanup", prompt)
                self.assertIn("observed estimated upper-bound", prompt)
                self.assertIn("$15 per arm", prompt)
                self.assertIn("$18 empirical planning envelope", prompt)
                self.assertIn("not a spending target or a guaranteed billed ceiling", prompt)
                for stale in ("3,000 seconds", "3000 seconds", "$10", "$9"):
                    self.assertNotIn(stale, prompt)
        for name in ("round0.md", "round1.md", "round2.md",
                     "environment.md"):
            visible = (ASSET_ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn("3,000 seconds", visible)
            self.assertNotIn("$10", visible)

    def test_diagnostic_treatment_plan_and_reference_fail_closed(self) -> None:
        self.require_retained_adjudication()
        spec = manifest()
        self.assertIs(spec["live_enabled"], False)
        plan = json.loads((ASSET_ROOT.parent / "pilot-plan-v11.json").read_text(
            encoding="utf-8"))
        self.assertEqual(plan["schema_version"], 3)
        self.assertEqual(plan["live_rebind"], {
            "mode": "no-model-rebind-v1",
            "expected_final_manifest_sha256_required": True,
            "turn_start_forbidden": True})
        self.assertEqual(plan["arm_order"], ["treatment"])
        self.assertEqual(plan["matched_pairs"], 0)
        self.assertEqual(plan["limits_per_arm"]["automatic_retries"], 0)
        self.assertEqual(plan["limits_per_arm"]["dispatch_stop_usd"], 15.0)
        self.assertEqual(plan["limits_per_arm"]["wall_seconds"], 4500)
        self.assertEqual(plan["fixture"]["treatment_suffix_sha256"],
                         spec["arm_execution"]["prompt_suffix_sha256"]["treatment"])
        self.assertEqual(plan["claim"], "treatment-only-exploratory-feasibility")
        self.assertNotIn("packet_scope_mode", plan)
        spec["pilot"] = {"path": "pilot-plan-v11.json", "schema_version": 3,
                         "sha256": file_sha(ASSET_ROOT.parent / "pilot-plan-v11.json")}
        with self.assertRaisesRegex(ValueError, "fixture or runtime drift"):
            pilot_plan(spec)
        with self.assertRaisesRegex(ValueError, "historical baseline-effective manifest no longer matches"):
            verify_diagnostic_reference(spec, plan)

    def test_standalone_plan_rejects_comparator_fields_and_requires_explicit_mode(self) -> None:
        self.require_retained_adjudication()
        spec = manifest()
        plan = json.loads((ASSET_ROOT.parent / "pilot-plan-v12.json").read_text(encoding="utf-8"))
        self.assertEqual(plan["mode"], "standalone-feasibility")
        self.assertEqual(plan["arm_order"], ["treatment"])
        self.assertEqual(plan["matched_pairs"], 0)
        self.assertEqual(plan["packet_scope_mode"], "diagnostic-feasibility")
        self.assertIs(spec["live_enabled"], False)
        with self.assertRaisesRegex(ValueError, "drift"):
            pilot_plan(spec)
        stale = copy.deepcopy(spec)
        stale["pilot"] = {"path": "pilot-plan-v12.json", "schema_version": 4,
            "sha256": file_sha(ASSET_ROOT.parent / "pilot-plan-v12.json")}
        # The R12d manifest is offline, so this historical plan fails its
        # fixture binding before any later source pin can be considered.
        with self.assertRaisesRegex(ValueError, "standalone fixture or runtime drift"):
            pilot_plan(stale)
        from evals.long_horizon_v1.run import _reject_comparator_fields
        for field in ("baseline", "comparator", "diagnostic_reference", "quality_admission"):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "comparator field"):
                _reject_comparator_fields({**plan, field: {"forged": True}})
        with mock_patch("evals.long_horizon_v1.run.manifest", return_value={**spec, "live_enabled": True}), \
             mock_patch("evals.long_horizon_v1.run.pilot_plan", return_value=plan), \
             mock_patch("evals.long_horizon_v1.run.verify_cli") as cli:
            with self.assertRaisesRegex(ValueError, "explicit --mode"):
                live_preflight(None, None, "treatment", self.workspace)
            cli.assert_not_called()

    def test_standalone_validation_source_drift_blocks_preflight(self) -> None:
        self.require_retained_adjudication()
        from evals.long_horizon_v1.common import HERE
        from evals.long_horizon_v1.run import (_runner_source_sha256,
            _standalone_plan_version, _standalone_source_hashes,
            _verify_standalone_sources)
        pins = _standalone_source_hashes()
        plan = {"runner_normalized_sha256": _runner_source_sha256(),
                "execution_sources_sha256": pins}
        _verify_standalone_sources(plan)
        self.assertEqual(_standalone_plan_version("pilot-plan-v13.json"), 13)
        self.assertIsNone(_standalone_plan_version("pilot-plan-v11.json"))
        original = file_sha
        for name in pins:
            target = HERE.parent / "scripts/run_paired_arm.py" if name.startswith("evals/") else HERE / name

            def tampered(path: Path, target: Path = target) -> str:
                return "0" * 64 if path == target else original(path)

            with self.subTest(source=name), \
                 mock_patch("evals.long_horizon_v1.run.file_sha", side_effect=tampered), \
                 self.assertRaisesRegex(ValueError, "standalone execution source drift"):
                _verify_standalone_sources(plan)
        spec = {**manifest(), "live_enabled": True}
        with mock_patch("evals.long_horizon_v1.run.manifest", return_value=spec), \
             mock_patch("evals.long_horizon_v1.run._runner_source_sha256",
                        return_value="0" * 64), \
             mock_patch("evals.long_horizon_v1.run.verify_cli") as cli:
            # The historical v13 fixture pin rejects R12d before later
            # execution-source checks; those checks are exercised above.
            with self.assertRaisesRegex(ValueError, "standalone fixture or runtime drift"):
                live_preflight(None, None, "treatment", self.workspace,
                               benchmark_mode="standalone-feasibility")
            cli.assert_not_called()

    def test_v13_mode_driven_preparation_creates_treatment_r0_only(self) -> None:
        from evals.long_horizon_v1.prepare import _arms_for_plan
        root = self.workspace / "v13-freeze"
        root.mkdir()
        (root / "manifest.json").write_bytes((ASSET_ROOT.parent / "manifest.json").read_bytes())
        plan = json.loads((ASSET_ROOT.parent / "pilot-plan-v12.json").read_text(encoding="utf-8"))
        plan["pilot_id"] = "flipt-oci-long-horizon-pilot-13"
        plan_path = root / "pilot-plan-v13.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        spec = self.local_plugin_spec(manifest())
        spec["pilot"] = {"path": plan_path.name, "schema_version": 4,
                         "sha256": file_sha(plan_path)}
        with mock_patch("evals.long_horizon_v1.prepare.HERE", root), \
             mock_patch("evals.long_horizon_v1.prepare.manifest", return_value=spec), \
             mock_patch("evals.long_horizon_v1.common.manifest", return_value=spec):
            result = prepare(root / "prepared")
        self.assertEqual(set(result["arms"]), {"treatment"})
        self.assertFalse((root / "prepared" / "baseline").exists())
        treatment = root / "prepared" / "treatment" / ".benchmark"
        self.assertTrue((treatment / "round0.md").is_file())
        self.assertFalse((treatment / "round1.md").exists())
        self.assertFalse((treatment / "round2.md").exists())
        self.assertFalse((treatment / "oracle").exists())
        paired = {**spec, "pilot": {"path": "pilot-plan-v9.json", "schema_version": 1}}
        self.assertEqual(_arms_for_plan(paired), ("baseline", "treatment"))

    def test_product_scan_prunes_git_and_hashes_untracked_files(self) -> None:
        nested = self.workspace / "a" / "nested.go"
        nested.parent.mkdir()
        nested.write_text("package a\n", encoding="utf-8")
        untracked = self.workspace / "new.go"
        untracked.write_text("package main\n", encoding="utf-8")
        linked = self.workspace / "linked"
        linked.mkdir()
        (linked / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
        linked_product = linked / "untracked.go"
        linked_product.write_text("package linked\n", encoding="utf-8")
        router = self.workspace / ".codex-model-router"
        router.mkdir()
        (router / "routing.json").write_text("{}\n", encoding="utf-8")
        real_scandir = os.scandir
        git_object_scans = []

        def guarded_scandir(path):
            if Path(path) == self.workspace / ".git" / "objects":
                git_object_scans.append(path)
                raise FileNotFoundError("Git objects changed during traversal")
            return real_scandir(path)

        with mock_patch("os.scandir", side_effect=guarded_scandir):
            files = _product_files(self.workspace)
        self.assertEqual(git_object_scans, [])
        self.assertEqual(list(files), sorted(files))
        self.assertEqual(files["README.md"], file_sha(self.workspace / "README.md"))
        self.assertEqual(files["a/nested.go"], file_sha(nested))
        self.assertEqual(files["new.go"], file_sha(untracked))
        self.assertEqual(files["linked/untracked.go"], file_sha(linked_product))
        self.assertNotIn("linked/.git", files)
        self.assertFalse(any(name.startswith((".git/", ".benchmark/",
                                              ".codex-model-router/")) for name in files))

        original_sha = file_sha

        def disappearing_product(path):
            if path == untracked:
                untracked.unlink()
            return original_sha(path)

        with mock_patch("evals.long_horizon_v1.prepare.file_sha",
                        side_effect=disappearing_product), \
             self.assertRaises(FileNotFoundError):
            _product_files(self.workspace)

        def missing_product_directory(path):
            if Path(path) == nested.parent:
                raise FileNotFoundError("Product directory changed during traversal")
            return real_scandir(path)

        with mock_patch("os.scandir", side_effect=missing_product_directory), \
             self.assertRaises(FileNotFoundError):
            _product_files(self.workspace)

    def test_v13_plan_validation_preserves_standalone_mode(self) -> None:
        self.require_retained_adjudication()
        from evals.long_horizon_v1.common import HERE
        from evals.long_horizon_v1.run import _runner_source_sha256, _standalone_source_hashes
        candidate = json.loads((HERE / "pilot-plan-v12.json").read_text(encoding="utf-8"))
        candidate.update(pilot_id="flipt-oci-long-horizon-pilot-13",
                         preparation_root="_scratch/pilot-13-prep",
                         evidence_root="_scratch/pilot-13-evidence",
                         capabilities={"treatment": "_scratch/pilot-13-evidence/treatment-capability.json"},
                         run_outputs={"treatment": "_scratch/pilot-13-evidence/treatment-live"},
                         runner_normalized_sha256=_runner_source_sha256(),
                         execution_sources_sha256=_standalone_source_hashes())
        candidate["end_to_end"]["receipts"] = {
            "treatment": "_scratch/pilot-13-evidence/treatment-end-to-end.json"}
        synthetic_path = HERE / "pilot-plan-v13.json"
        spec = copy.deepcopy(manifest())
        spec["pilot"] = {"path": synthetic_path.name, "schema_version": 4,
                         "sha256": "f" * 64}
        original_read = Path.read_text
        original_sha = file_sha

        def read(path: Path, *args, **kwargs):
            return json.dumps(candidate) if path == synthetic_path else original_read(path, *args, **kwargs)

        def digest(path: Path):
            return "f" * 64 if path == synthetic_path else original_sha(path)

        with mock_patch.object(Path, "read_text", read), \
             mock_patch("evals.long_horizon_v1.run.file_sha", side_effect=digest):
            with self.assertRaisesRegex(ValueError, "standalone fixture or runtime drift"):
                pilot_plan(spec)
            candidate["mode"] = "diagnostic-feasibility"
            with self.assertRaisesRegex(ValueError, "standalone plan schema"):
                pilot_plan(spec)

    def test_standalone_collector_pass_and_unknown_have_no_comparison_metrics(self) -> None:
        from evals.long_horizon_v1.common import HERE
        plan = json.loads((HERE / "pilot-plan-v13.json").read_text(encoding="utf-8"))
        prepared = HERE / plan["preparation_root"]
        run = {"benchmark_mode": "standalone-feasibility",
            "claim_class": "standalone-treatment-feasibility",
            "stop_reason": "accepted-final", "cost_status": "complete",
            "usage_issues": [], "interruption_proof": None,
            "wall_seconds": 120.0, "fork_policy": {"packet_scope": "UNKNOWN"},
            "usage": {"estimated_usd": 1.25, "estimated_usd_upper_bound": 1.25,
                      "unknown_models": [], "unknown_usage": []}}
        with mock_patch("evals.long_horizon_v1.run.pilot_plan", return_value=plan), \
             mock_patch("evals.long_horizon_v1.collect._arm", return_value=run), \
             mock_patch("evals.long_horizon_v1.collect.file_sha", return_value="a" * 64), \
             mock_patch("evals.long_horizon_v1.standalone_quality.verify_standalone_quality",
                        return_value={"sha256": "a" * 64, "end_to_end_wall_seconds": 180.0}):
            result = collect_standalone(prepared, self.workspace / "standalone-pass")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["packet_scope"], "UNKNOWN")
        self.assertEqual(result["estimated_usd"], 1.25)
        self.assertFalse(any(token in key for key in result for token in
            ("ratio", "savings", "speedup", "parity", "baseline", "reference")))
        unknown = copy.deepcopy(run)
        unknown["cost_status"] = "UNKNOWN"
        with mock_patch("evals.long_horizon_v1.run.pilot_plan", return_value=plan), \
             mock_patch("evals.long_horizon_v1.collect._arm", return_value=unknown), \
             mock_patch("evals.long_horizon_v1.collect.file_sha", return_value="a" * 64):
            result = collect_standalone(prepared, self.workspace / "standalone-unknown")
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertIsNone(result["estimated_usd"])

    def test_standalone_quality_accepts_bound_synthetic_three_round_receipts(self) -> None:
        from evals.long_horizon_v1.common import HERE
        from evals.long_horizon_v1.standalone_quality import verify_standalone_quality
        plan = json.loads((HERE / "pilot-plan-v13.json").read_text(encoding="utf-8"))
        machine = StageMachine(self.workspace, "treatment", "parent",
            lambda patch, round_id, repair: {"round": round_id,
                "candidate_patch_sha256": sha(patch), "behavior_pass": True},
            base_tree=self.seed_tree)
        attempts = []
        for round_id in range(3):
            start = len(machine.events)
            action = self.submit(machine, round_id)
            attempts.append({"round": round_id, "repair": 0, "thread_id": "parent",
                "stage_event_start": start, "stage_event_end": len(machine.events)})
            self.assertEqual(action["action"], "final" if round_id == 2 else "continue")
        root = self.workspace / "standalone-fixture"
        root.mkdir()
        for name in ("manifest.json", "pilot-plan-v13.json"):
            (root / name).write_bytes((HERE / name).read_bytes())
        prepared = root / "prepared"
        prepared.mkdir()
        (prepared / "preparation.json").write_text("{}", encoding="utf-8")
        manifest_sha = file_sha(root / "manifest.json")
        prep_sha = file_sha(prepared / "preparation.json")
        run_path = root / plan["run_outputs"]["treatment"] / "run.json"
        run_path.parent.mkdir(parents=True)
        patch_sha = machine.events[-1]["patch_sha256"]
        (run_path.parent / "candidate-g2-attempt0.patch").write_bytes(b"")
        self.assertEqual(file_sha(run_path.parent / "candidate-g2-attempt0.patch"), patch_sha)
        started = 1_700_000_000_000_000_000
        run = {"stop_reason": "accepted-final", "cost_status": "complete",
            "usage_issues": [], "stage": machine.receipt(), "attempts": attempts,
            "parent_thread_id": "parent", "runtime_binding": {"preparation_sha256": prep_sha},
            "started_utc_ns": started, "wall_seconds": 5.0}
        run_path.write_text(json.dumps(run), encoding="utf-8")
        evidence = root / plan["evidence_root"]
        evidence.mkdir(parents=True, exist_ok=True)
        review = {"schema_version": 1, "reviewer_role": "independent", "arm_blind": True,
            "reviewer_id": "reviewer", "candidate_patch_sha256": patch_sha,
            "manifest_sha256": manifest_sha, "preparation_sha256": prep_sha,
            "requirements": {key: True for key in REVIEW_REQUIREMENTS}, "verdict": "pass"}
        review_path = evidence / "treatment-review.json"
        review_path.write_text(json.dumps(review), encoding="utf-8")
        wall_path = root / plan["end_to_end"]["receipts"]["treatment"]
        wall = {"schema_version": 1, "kind": "arm-end-to-end-boundary",
            "pilot_id": plan["pilot_id"], "task_id": manifest()["task_id"], "arm": "treatment",
            "manifest_sha256": manifest_sha, "pilot_plan_sha256": file_sha(root / "pilot-plan-v13.json"),
            "preparation_sha256": prep_sha, "run_sha256": file_sha(run_path),
            "quality_files_sha256": {"hidden_race_grade": "h" * 64,
                "backend_grade": "b" * 64, "semantic_review": file_sha(review_path)},
            "started_utc_ns": started, "quality_finished_utc_ns": started + 10_000_000_000,
            "run_wall_seconds": 5.0, "end_to_end_wall_seconds": 10.0}
        wall_path.parent.mkdir(parents=True, exist_ok=True)
        wall_path.write_text(json.dumps(wall), encoding="utf-8")
        with mock_patch("evals.long_horizon_v1.standalone_quality.HERE", root), \
             mock_patch("evals.long_horizon_v1.standalone_quality._grade",
                        side_effect=["h" * 64, "b" * 64]):
            receipt = verify_standalone_quality(prepared, plan, run)
        self.assertEqual(receipt["end_to_end_wall_seconds"], 10.0)
    def test_no_model_rebind_rejects_wrong_hash_turn_start_and_stale_prep(self) -> None:
        with mock_patch("evals.long_horizon_v1.live_preflight.verify_arm_runtime") as runtime:
            with self.assertRaisesRegex(ValueError, "final manifest SHA-256 mismatch"):
                bind_capabilities(expected_final_manifest_sha256="0" * 64)
            runtime.assert_not_called()
        with forbid_model_turn_start():
            with self.assertRaisesRegex(ValueError, "forbids turn/start"):
                AppServerTransport._send(object(), "turn/start", {})
        stale = self.workspace / "stale-prepared"
        stale.mkdir()
        (stale / "preparation.json").write_text(json.dumps({
            "schema_version": 2, "task_id": manifest()["task_id"],
            "fixture_manifest_sha256": "0" * 64}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "fresh preparation receipt drift"):
            verify_prepared(stale, "treatment", manifest())

    def test_standalone_capability_rebind_requires_exact_live_manifest(self) -> None:
        spec = manifest()
        with mock_patch("evals.long_horizon_v1.live_preflight.verify_cli") as cli:
            with self.assertRaisesRegex(ValueError, "standalone feasibility requires no-model rebind"):
                bind_capabilities()
            with self.assertRaisesRegex(ValueError, "exact final manifest SHA-256 required"):
                bind_capabilities(expected_final_manifest_sha256="not-a-hash")
            with self.assertRaisesRegex(ValueError, "final manifest SHA-256 mismatch"):
                bind_capabilities(expected_final_manifest_sha256="0" * 64)
            from evals.long_horizon_v1.live_preflight import _final_manifest
            with mock_patch("evals.long_horizon_v1.live_preflight.manifest",
                            return_value={**spec, "live_enabled": False}), \
                 self.assertRaisesRegex(ValueError, "final manifest is not live-enabled"):
                _final_manifest(file_sha(ASSET_ROOT.parent / "manifest.json"))
            cli.assert_not_called()
        self.assertEqual(spec["pilot"]["schema_version"], 4)

    def test_standalone_capability_rebind_rejects_cross_mode_and_comparator(self) -> None:
        spec = {**manifest(), "live_enabled": True}
        plan = json.loads((ASSET_ROOT.parent / "pilot-plan-v12.json").read_text(encoding="utf-8"))
        cases = (("cross-mode", {**plan, "mode": "diagnostic-feasibility"}),
                 ("baseline", {**plan, "baseline": {"forged": True}}),
                 ("admission", {**plan, "quality_admission": "forged"}),
                 ("scope", {**plan, "arm_order": ["baseline", "treatment"]}))
        for name, candidate in cases:
            with self.subTest(name=name), \
                 mock_patch("evals.long_horizon_v1.live_preflight._final_manifest",
                            return_value=spec), \
                 mock_patch("evals.long_horizon_v1.live_preflight.pilot_plan",
                            return_value=candidate), \
                 mock_patch("evals.long_horizon_v1.live_preflight.verify_cli") as cli:
                with self.assertRaisesRegex(ValueError, "comparator field|rebind scope drift"):
                    bind_capabilities(expected_final_manifest_sha256="a" * 64)
                cli.assert_not_called()

    def test_v11_no_model_rebind_entry_guard_unchanged(self) -> None:
        spec = manifest()
        spec["pilot"] = {"path": "pilot-plan-v11.json", "schema_version": 3,
                         "sha256": file_sha(ASSET_ROOT.parent / "pilot-plan-v11.json")}
        with mock_patch("evals.long_horizon_v1.live_preflight.manifest", return_value=spec):
            with self.assertRaisesRegex(ValueError, "pilot-11 requires no-model rebind"):
                bind_capabilities()

    def test_standalone_rebind_rejects_stale_capability_and_manifest_swap(self) -> None:
        from evals.long_horizon_v1.common import write_json_new
        spec = {**manifest(), "live_enabled": True}
        frozen = json.loads((ASSET_ROOT.parent / "pilot-plan-v13.json").read_text(encoding="utf-8"))
        for scenario in ("stale-capability", "manifest-swap"):
            with self.subTest(scenario=scenario):
                root = self.workspace / scenario
                root.mkdir()
                (root / "manifest.json").write_text("frozen\n", encoding="utf-8")
                expected = file_sha(root / "manifest.json")
                (root / ".codex" / "sessions").mkdir(parents=True)
                source = root / "cancellation.json"
                source.write_text(json.dumps({"status": "UNKNOWN", "arm": "treatment",
                    "parent_thread_id": "canary", "runtime_binding": {},
                    "runtime_binding_sha256": _canonical_sha({})}), encoding="utf-8")
                source_sha = file_sha(source)
                decision = root / "adjudication.json"
                decision.write_text(json.dumps({"status": "verified",
                    "source_receipt_sha256": source_sha}), encoding="utf-8")
                plan = {**frozen, "preparation_root": "prepared",
                    "capabilities": {"treatment": "capability.json"}}
                destination = root / "capability.json"
                if scenario == "stale-capability":
                    destination.write_text("previous\n", encoding="utf-8")

                def write_then_swap(path: Path, proof: dict) -> None:
                    write_json_new(path, proof)
                    (root / "manifest.json").write_text("changed\n", encoding="utf-8")

                with ExitStack() as stack:
                    stack.enter_context(mock_patch("evals.long_horizon_v1.live_preflight.HERE", root))
                    stack.enter_context(mock_patch("evals.long_horizon_v1.live_preflight.Path.home", return_value=root))
                    stack.enter_context(mock_patch("evals.long_horizon_v1.live_preflight.manifest", return_value=spec))
                    stack.enter_context(mock_patch("evals.long_horizon_v1.live_preflight.pilot_plan", return_value=plan))
                    stack.enter_context(mock_patch("evals.long_horizon_v1.live_preflight.EVIDENCE",
                        {"treatment": ("cancellation.json", source_sha, "adjudication.json", file_sha(decision))}))
                    stack.enter_context(mock_patch("evals.long_horizon_v1.live_preflight.verify_cli",
                        return_value={"cli_sha256": "c", "code_mode_host_sha256": "h"}))
                    stack.enter_context(mock_patch("evals.long_horizon_v1.live_preflight.verify_prepared",
                        return_value={"preparation_sha256": "p"}))
                    stack.enter_context(mock_patch("evals.long_horizon_v1.live_preflight.arm_execution",
                        return_value={"cli_options": [], "policy_sha256": "e",
                            "prompt_suffix_sha256": "s", "model": "gpt-6-sol", "effort": "low"}))
                    stack.enter_context(mock_patch("evals.long_horizon_v1.live_preflight.verify_arm_config",
                        return_value={"canonical_sha256": "r"}))
                    stack.enter_context(mock_patch("evals.long_horizon_v1.live_preflight.verify_arm_runtime",
                        return_value={}))
                    stack.enter_context(mock_patch("evals.long_horizon_v1.live_preflight._canary_runtime_equivalent",
                        return_value=True))
                    if scenario == "manifest-swap":
                        stack.enter_context(mock_patch("evals.long_horizon_v1.live_preflight.write_json_new",
                            side_effect=write_then_swap))
                    with self.assertRaisesRegex((FileExistsError, ValueError),
                        "File exists|final manifest SHA-256 mismatch"):
                        bind_capabilities(expected_final_manifest_sha256=expected)
                if scenario == "stale-capability":
                    self.assertEqual(destination.read_text(encoding="utf-8"), "previous\n")

    def test_baseline_quality_admission_requires_all_bound_evidence(self) -> None:
        root = self.workspace / "admission-fixture"
        root.mkdir()
        evidence = root / "evidence"
        baseline = evidence / "baseline-live"
        baseline.mkdir(parents=True)
        prepared = root / "prepared"
        prepared.mkdir()
        (prepared / "preparation.json").write_text("{}\n", encoding="utf-8")
        (root / "manifest.json").write_text("{}\n", encoding="utf-8")
        (root / "pilot-plan-v2.json").write_text("{}\n", encoding="utf-8")
        patch = b"accepted patch\n"
        patch_sha = sha(patch)
        (baseline / "candidate-g2-attempt0.patch").write_bytes(patch)
        (baseline / "run.json").write_text("{}\n", encoding="utf-8")
        previous = "0" * 64
        events = []
        for index, (kind, extra) in enumerate((
                ("submit", {"patch_sha256": patch_sha, "repair": 0}),
                ("gate", {"patch_sha256": patch_sha, "behavior_pass": True}),
                ("accepted-final", {"patch_sha256": patch_sha}))):
            event = {"seq": index, "kind": kind, "round": 2, "arm": "baseline",
                     "thread_id": "baseline-parent", "previous_sha256": previous, **extra}
            event["sha256"] = sha(json.dumps(event, sort_keys=True,
                                               separators=(",", ":")).encode())
            previous = event["sha256"]
            events.append(event)
        run = {"stop_reason": "accepted-final", "cost_status": "complete",
               "parent_thread_id": "baseline-parent", "usage_issues": [],
               "native_attempts": [], "usage": {"estimated_usd": 1.0,
                   "estimated_usd_upper_bound": 1.1, "model_calls": 1},
               "runtime_binding": {"preparation_sha256": file_sha(prepared / "preparation.json")},
               "stage": {"complete": True, "round": 2, "events": events,
                         "chain_sha256": previous,
                         "manifest_sha256": file_sha(root / "manifest.json")},
               "attempts": [{"round": 2, "repair": 0, "thread_id": "baseline-parent",
                             "turn_id": "turn-final", "stage_event_start": 0,
                             "stage_event_end": 3}]}
        review = {"schema_version": 1, "reviewer_role": "independent",
                  "arm_blind": True, "reviewer_id": "reviewer-thread",
                  "candidate_patch_sha256": patch_sha,
                  "manifest_sha256": file_sha(root / "manifest.json"),
                  "preparation_sha256": file_sha(prepared / "preparation.json"),
                  "requirements": {name: True for name in REVIEW_REQUIREMENTS},
                  "verdict": "pass"}
        review_path = evidence / "baseline-review.json"
        review_path.write_text(json.dumps(review), encoding="utf-8")
        for name, hidden in (("baseline-hidden-grade", True),
                             ("baseline-backend-grade", False)):
            grade_root = evidence / name
            grade_root.mkdir()
            checks = []
            for kind, packages in (("go-test", PACKAGES if hidden else BACKEND_PACKAGES),
                                   *(([("race", RACE_PACKAGES)] if hidden else []))):
                check_root = grade_root / kind
                check_root.mkdir()
                for stream in ("stdout", "stderr"):
                    (check_root / f"{stream}.txt").write_bytes(b"")
                checks.append({"command": ["go", "test", "-count=1", "-timeout=90s"] +
                               (["-race"] if kind == "race" else []) + packages,
                               "exit_code": 0, "stdout_sha256": sha(b""),
                               "stderr_sha256": sha(b"")})
            grade = {"schema_version": 1, "task_id": "flipt-oci-reference-rollout-v1",
                     "kind": "hidden-final" if hidden else "backend-regression", "round": 2,
                     "start_tree": "3354dd97a389bac41536cc1cfa4ffe4861912bc8",
                     "manifest_sha256": file_sha(root / "manifest.json"),
                     "candidate_patch_sha256": patch_sha, "tests": _tests(hidden),
                     "checks": checks, "behavior_pass": True, "quality_pass": True,
                     "semantic_review": review if hidden else "not-applicable"}
            (grade_root / "grade.json").write_text(json.dumps(grade), encoding="utf-8")
        plan = {"pilot_id": "pilot-02", "evidence_root": "evidence",
                "run_outputs": {"baseline": "evidence/baseline-live"},
                "quality_admission": "evidence/baseline-quality-admission.json"}
        with mock_patch("evals.long_horizon_v1.quality_admission.HERE", root), \
             mock_patch("evals.long_horizon_v1.quality_admission.manifest",
                        return_value={"pilot": {"path": "pilot-plan-v2.json"},
                                      "toolchain": {"linux_path": "go"}}), \
             mock_patch("evals.long_horizon_v1.quality_admission._arm", return_value=run):
            receipt = create_quality_admission(prepared, plan)
            self.assertEqual(verify_quality_admission(prepared, plan), receipt)
            run["usage"].update(estimated_usd=16.0,
                                estimated_usd_upper_bound=19.0)
            self.assertEqual(verify_quality_admission(prepared, plan), receipt)
            run["usage"].update(estimated_usd=1.0,
                                estimated_usd_upper_bound=1.1)
            mutations = [
                (lambda: run.update(cost_status="UNKNOWN"), "accounting"),
                (lambda: run.update(stop_reason="public-gate-failed"), "public-gate"),
                (lambda: run["usage"].update(model_calls=0), "usage-incomplete"),
                (lambda: run.update(native_attempts=[{"child_id": "child"}]), "wrong-arm-lineage"),
                (lambda: run["runtime_binding"].update(preparation_sha256="0" * 64), "preparation"),
                (lambda: run["attempts"][-1].update(turn_id="other"), "attempt"),
                (lambda: review.update(verdict="fail"), "review"),
                (lambda: review["requirements"].update({REVIEW_REQUIREMENTS[0]: False}),
                 "semantic-requirement"),
            ]
            for mutate, label in mutations:
                with self.subTest(case=label):
                    import copy
                    run_before, review_before = copy.deepcopy(run), copy.deepcopy(review)
                    mutate()
                    if label in ("review", "semantic-requirement"):
                        review_path.write_text(json.dumps(review), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        verify_quality_admission(prepared, plan)
                    run.clear(); run.update(run_before)
                    review.clear(); review.update(review_before)
                    review_path.write_text(json.dumps(review), encoding="utf-8")
            self.assertEqual(verify_quality_admission(prepared, plan), receipt)
            for name, mutate in (
                    ("hidden-failed", lambda grade: grade.update(behavior_pass=False)),
                    ("race-failed", lambda grade: grade["checks"][1].update(exit_code=1)),
                    ("hidden-wrong-patch", lambda grade: grade.update(candidate_patch_sha256="0" * 64)),
                    ("hidden-wrong-manifest", lambda grade: grade.update(manifest_sha256="0" * 64))):
                target = evidence / "baseline-hidden-grade" / "grade.json"
                before = target.read_bytes()
                grade = json.loads(before)
                mutate(grade)
                target.write_text(json.dumps(grade), encoding="utf-8")
                with self.subTest(case=name), self.assertRaises(ValueError):
                    verify_quality_admission(prepared, plan)
                target.write_bytes(before)
            target = evidence / "baseline-backend-grade" / "grade.json"
            before = target.read_bytes()
            grade = json.loads(before)
            grade["checks"][0]["exit_code"] = 1
            target.write_text(json.dumps(grade), encoding="utf-8")
            with self.assertRaises(ValueError):
                verify_quality_admission(prepared, plan)
            target.write_bytes(before)
            admission_path = evidence / "baseline-quality-admission.json"
            before = admission_path.read_bytes()
            for field, replacement in (("arm", "treatment"),
                                       ("pilot_plan_sha256", "0" * 64),
                                       ("accepted_patch_sha256", "0" * 64),
                                       ("baseline_run_sha256", "0" * 64)):
                changed = json.loads(before)
                changed[field] = replacement
                admission_path.write_text(json.dumps(changed), encoding="utf-8")
                with self.subTest(case=f"receipt-{field}"), self.assertRaises(ValueError):
                    verify_quality_admission(prepared, plan)
            admission_path.write_bytes(before)
            for relative in ("baseline-live/candidate-g2-attempt0.patch",
                             "baseline-hidden-grade/race/stdout.txt",
                             "baseline-backend-grade/grade.json"):
                target = evidence / relative
                before = target.read_bytes()
                target.write_bytes(before + b"tampered")
                with self.assertRaises((ValueError, json.JSONDecodeError)):
                    verify_quality_admission(prepared, plan)
                target.write_bytes(before)
            self.assertEqual(verify_quality_admission(prepared, plan), receipt)
            (evidence / "baseline-quality-admission.json").unlink()
            with self.assertRaisesRegex(ValueError, "admission receipt missing"):
                verify_quality_admission(prepared, plan)

    def test_end_to_end_boundary_includes_post_run_quality_wall(self) -> None:
        prepared = self.workspace / "prepared"
        run = {"stop_reason": "accepted-final", "cost_status": "complete",
               "runtime_binding": {"preparation_sha256": "1" * 64},
               "started_utc_ns": 1_000_000_000, "wall_seconds": 2.0}
        plan = {"pilot_id": "pilot-05", "evidence_root": "evidence",
                "run_outputs": {"baseline": "evidence/baseline-live"},
                "quality_admission": "evidence/baseline-quality-admission.json",
                "end_to_end": {"receipts": {
                    "baseline": "evidence/baseline-end-to-end.json"}}}
        admission = {"verdict": "pass", "hidden_race_grade_sha256": "2" * 64,
                     "backend_grade_sha256": "3" * 64,
                     "semantic_review_sha256": "4" * 64}
        with mock_patch("evals.long_horizon_v1.end_to_end.HERE", self.workspace), \
             mock_patch("evals.long_horizon_v1.end_to_end._arm", return_value=run), \
             mock_patch("evals.long_horizon_v1.end_to_end.file_sha", return_value="1" * 64), \
             mock_patch("evals.long_horizon_v1.end_to_end.verify_quality_admission",
                        return_value=admission) as quality, \
             mock_patch("evals.long_horizon_v1.end_to_end.manifest",
                        return_value={"pilot": {"path": "pilot-plan-v5.json"}}), \
             mock_patch("evals.long_horizon_v1.end_to_end.time.time_ns",
                        return_value=5_000_000_000), \
             mock_patch("evals.long_horizon_v1.end_to_end.write_json_new") as write:
            result = finalize_arm(prepared, "baseline", plan)
            self.assertEqual(result["end_to_end_wall_seconds"], 4.0)
            self.assertEqual(result["post_run_quality_wall_seconds"], 2.0)
            self.assertEqual(result["quality_files_sha256"]["semantic_review"], "4" * 64)
            write.assert_called_once()
            quality.side_effect = ValueError("failed review")
            with self.assertRaisesRegex(ValueError, "failed review"):
                finalize_arm(prepared, "baseline", plan)
            write.assert_called_once()

    def test_treatment_checks_quality_before_output_or_transport_start(self) -> None:
        plan = {"run_outputs": {"treatment": str(self.workspace / "treatment-live")}}
        output = self.workspace / "treatment-live"
        with mock_patch("evals.long_horizon_v1.run.live_preflight", return_value={}) as preflight, \
             mock_patch("evals.long_horizon_v1.run.manifest",
                        return_value={"pilot": {"path": "pilot-plan-v2.json"}}), \
             mock_patch("evals.long_horizon_v1.run.pilot_plan", return_value=plan), \
             mock_patch("evals.long_horizon_v1.run.verify_quality_admission",
                        side_effect=ValueError("baseline quality missing")) as admission, \
             mock_patch("evals.long_horizon_v1.run.AppServerTransport") as transport:
            with self.assertRaisesRegex(ValueError, "baseline quality missing"):
                live_run(self.workspace, output, "treatment", self.workspace / "cli",
                         self.workspace / "capability", self.workspace / "sessions")
        admission.assert_called_once_with(self.workspace, plan)
        preflight.assert_not_called()
        transport.assert_not_called()
        self.assertFalse(output.exists())

    def test_versioned_routing_fixture_binds_hook_without_overwrite(self) -> None:
        spec = self.local_plugin_spec(manifest())
        descriptor = fixture_config(spec)
        before = capture_patch(self.workspace, self.seed_tree)
        binding = bind_arm_config(self.workspace, spec)
        self.assertEqual(binding["canonical_sha256"], descriptor["canonical_sha256"])
        self.assertEqual(binding["path"], str((self.workspace / ".codex-model-router" /
                                               "routing.json").resolve()))
        self.assertTrue(binding["hook_verified"])
        self.assertEqual(capture_patch(self.workspace, self.seed_tree), before)
        with self.assertRaisesRegex(ValueError, "already exists"):
            bind_arm_config(self.workspace, spec)
        target = self.workspace / ".codex-model-router" / "routing.json"
        target.write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            verify_arm_config(self.workspace, spec)
        drift = copy.deepcopy(spec)
        drift["runtime"]["plugin_manifest_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "pinned plugin manifest.*SHA-256 mismatch"):
            fixture_config(drift)
        (Path(spec["runtime"]["plugin_root"]) / "hooks" / "router_hook.py").unlink()
        with self.assertRaisesRegex(ValueError, "pinned router hook missing"):
            fixture_config(spec)

    def test_cli_binding_requires_pinned_install_and_sidecar(self) -> None:
        spec = manifest()
        cli = self.local_cli(spec)
        with mock_patch("evals.long_horizon_v1.runtime_binding.subprocess.run",
                        return_value=subprocess.CompletedProcess([], 0,
                            stdout=spec["runtime"]["cli_version"])) as version:
            binding = verify_cli(cli, spec)
        version.assert_called_once()
        self.assertEqual(binding["code_mode_host_sha256"],
                         spec["runtime"]["code_mode_host_sha256"])
        with self.assertRaisesRegex(ValueError, "pinned complete desktop installation"):
            verify_cli(self.workspace / "codex.exe", spec)
        drift = json.loads(json.dumps(spec))
        drift["runtime"]["code_mode_host_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "code-mode host.*SHA-256 mismatch"):
            verify_cli(cli, drift)
        fake_cli = self.workspace / "incomplete" / "codex.exe"
        fake_cli.parent.mkdir()
        fake_cli.write_bytes(b"fake CLI")
        missing = json.loads(json.dumps(spec))
        missing["runtime"]["cli"] = str(fake_cli)
        missing["runtime"]["cli_sha256"] = file_sha(fake_cli)
        with self.assertRaisesRegex(ValueError, "code-mode host missing"):
            verify_cli(fake_cli, missing)

    def test_live_proof_binds_arm_policy_and_preparation(self) -> None:
        spec = manifest()
        spec["live_enabled"] = True
        spec.pop("pilot", None)
        arm = self.workspace / "baseline"
        arm.mkdir()
        cli = self.local_cli(spec)
        policy = arm_execution(spec, "baseline")
        proof = {"status": "verified", "cli_sha256": spec["runtime"]["cli_sha256"],
                 "code_mode_host_sha256": spec["runtime"]["code_mode_host_sha256"],
                 "routing_config_canonical_sha256": None,
                 "cli_options": policy["cli_options"],
                 "arm_execution_policy_sha256": policy["policy_sha256"],
                 "prompt_suffix_sha256": policy["prompt_suffix_sha256"],
                 "native_spawn_available": False, "router_hooks_verified": False,
                 "preparation_sha256": "prepared-digest",
                 "multi_turn_same_thread": True, "child_usage_complete": True,
                 "cancellation_verified": True, "selector_verified": True,
                 "model": "gpt-6-astra", "effort": "xhigh"}
        runtime = {"arm": "baseline", "cli_options": policy["cli_options"],
                   "policy_sha256": policy["policy_sha256"],
                   "prompt_suffix_sha256": policy["prompt_suffix_sha256"],
                   "router_hook_count": 0, "model_turns": 0,
                   "thread_started_without_turn": True,
                   "protocol_contract": {"client_request_sha256": "1" * 64,
                       "dispatch_contract_sha256": "2" * 64,
                       "installed_hook_closure_sha256": "3" * 64,
                       "interrupt_method": "turn/interrupt"},
                   "model_catalog": {"gpt-6-astra": "xhigh"}}
        proof["capability"] = {"status": "verified", "arm": "baseline",
            "model": "gpt-6-astra", "effort": "xhigh", "canary_id": "baseline-cancel-1",
            "runtime_binding_sha256": _canonical_sha(runtime),
            "cancellation": {"status": "verified", "interrupt_ack": True,
                "turn_completed": True, "usage_drained": True,
                "descendants_drained": True, "real_child_observed": False}}
        evidence_path = self.workspace / "cancellation.json"
        evidence_path.write_text(json.dumps({"kind": "cancellation-capability",
            "status": "UNKNOWN", "arm": "baseline", "model": "gpt-6-astra",
            "effort": "xhigh", "runtime_binding_sha256": _canonical_sha(runtime),
            "runtime_binding": runtime,
            "real_child_observed": False,
            "native_attempts": [],
            "accounting_issues": ["parent session lacks successful terminal result"],
            "parent_thread_id": "parent", "turn_id": "parent-turn", "wall_seconds": 20.0,
            "usage": {"estimated_usd_upper_bound": 0.05,
                "sessions": [{"id": "parent", "model": "gpt-6-astra",
                    "effort": "xhigh", "calls": 1, "terminal": "turn_aborted",
                    "reported_total_usage": {"input_tokens": 1}}]},
            "cancellation": {"status": "verified-drained", "interrupt_ack": True,
                "turn_completed": True, "usage_drained": True,
                "descendants_drained": True, "turn_id": "parent-turn",
                "interrupt_requests": [{"thread_id": "parent", "turn_id": "parent-turn",
                    "dispatch_time_ns": 1}]}}), encoding="utf-8")
        proof["capability"]["cancellation_receipt_path"] = str(evidence_path)
        proof["capability"]["cancellation_receipt_sha256"] = file_sha(evidence_path)
        sessions = self.workspace / "sessions"
        sessions.mkdir()
        adjudication_proof = {"parent_rollout": {"session_id": "parent",
            "sha256": "a" * 64}, "parent_turn_id": "parent-turn"}
        decision_path = self.workspace / "adjudication.json"
        prior_path = self.workspace / "adjudication-v1.json"
        write_legacy_cancel_decision(prior_path, evidence_path, "baseline",
                                     adjudication_proof)
        decision = {"schema_version": 2, "kind": "cancellation-adjudication",
            "status": "verified", "source_receipt_status": "UNKNOWN",
            "source_receipt_sha256": file_sha(evidence_path), "arm": "baseline",
            "live_enabled": False, "reason": None,
            "source_adjudication": {"path": str(prior_path.resolve()),
                "sha256": file_sha(prior_path)},
            "proof": json.loads(json.dumps(adjudication_proof))}
        decision_path.write_text(json.dumps(decision), encoding="utf-8")
        proof["capability"]["cancellation_adjudication_path"] = str(decision_path)
        proof["capability"]["cancellation_adjudication_sha256"] = file_sha(decision_path)
        proof["capability"]["cancellation_sessions_path"] = str(sessions)
        path = self.workspace / "capability.json"
        path.write_text(json.dumps(proof), encoding="utf-8")
        with ExitStack() as stack:
            stack.enter_context(mock_patch("evals.long_horizon_v1.run.manifest", return_value=spec))
            stack.enter_context(mock_patch("evals.long_horizon_v1.run.verify_cli", return_value={
                "cli_sha256": spec["runtime"]["cli_sha256"],
                "code_mode_host_sha256": spec["runtime"]["code_mode_host_sha256"]}))
            stack.enter_context(mock_patch("evals.long_horizon_v1.run.verify_prepared", return_value={
                "preparation_sha256": "prepared-digest"}))
            stack.enter_context(mock_patch("evals.long_horizon_v1.run.verify_arm_runtime",
                                           return_value=runtime))
            judge = stack.enter_context(mock_patch(
                "evals.long_horizon_v1.cancellation_adjudication._judge",
                return_value=adjudication_proof))
            self.assertEqual(live_preflight(path, cli, "baseline", self.workspace)["cli_options"],
                             policy["cli_options"])
            judge.assert_called_once_with(json.loads(evidence_path.read_text(encoding="utf-8")),
                                          sessions, evidence_path)
            proof["capability"]["cancellation_adjudication_sha256"] = "0" * 64
            path.write_text(json.dumps(proof), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "proof incomplete"):
                live_preflight(path, cli, "baseline", self.workspace)
            proof["capability"]["cancellation_adjudication_sha256"] = file_sha(decision_path)
            decision["proof"]["parent_turn_id"] = "wrong-turn"
            decision_path.write_text(json.dumps(decision), encoding="utf-8")
            proof["capability"]["cancellation_adjudication_sha256"] = file_sha(decision_path)
            path.write_text(json.dumps(proof), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "proof incomplete"):
                live_preflight(path, cli, "baseline", self.workspace)
            decision["proof"] = adjudication_proof
            decision_path.write_text(json.dumps(decision), encoding="utf-8")
            proof["capability"]["cancellation_adjudication_sha256"] = file_sha(decision_path)
            decision["source_adjudication"]["sha256"] = "0" * 64
            decision_path.write_text(json.dumps(decision), encoding="utf-8")
            proof["capability"]["cancellation_adjudication_sha256"] = file_sha(decision_path)
            path.write_text(json.dumps(proof), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "proof incomplete"):
                live_preflight(path, cli, "baseline", self.workspace)
            decision["source_adjudication"]["sha256"] = file_sha(prior_path)
            decision_path.write_text(json.dumps(decision), encoding="utf-8")
            proof["capability"]["cancellation_adjudication_sha256"] = file_sha(decision_path)
            baseline_evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            baseline_evidence["cancellation"]["interrupt_requests"] = []
            evidence_path.write_text(json.dumps(baseline_evidence), encoding="utf-8")
            proof["capability"]["cancellation_receipt_sha256"] = file_sha(evidence_path)
            path.write_text(json.dumps(proof), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "proof incomplete"):
                live_preflight(path, cli, "baseline", self.workspace)
            baseline_evidence["cancellation"]["interrupt_requests"] = [
                {"thread_id": "parent", "turn_id": "parent-turn", "dispatch_time_ns": 1}]
            evidence_path.write_text(json.dumps(baseline_evidence), encoding="utf-8")
            proof["capability"]["cancellation_receipt_sha256"] = file_sha(evidence_path)
            baseline_evidence["usage"]["sessions"].append({"id": "unexpected-child",
                "calls": 1, "terminal": "turn_aborted", "reported_total_usage": {}})
            evidence_path.write_text(json.dumps(baseline_evidence), encoding="utf-8")
            proof["capability"]["cancellation_receipt_sha256"] = file_sha(evidence_path)
            path.write_text(json.dumps(proof), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "proof incomplete"):
                live_preflight(path, cli, "baseline", self.workspace)
            baseline_evidence["usage"]["sessions"].pop()
            evidence_path.write_text(json.dumps(baseline_evidence), encoding="utf-8")
            proof["capability"]["cancellation_receipt_sha256"] = file_sha(evidence_path)
            proof["capability"]["cancellation"]["usage_drained"] = False
            path.write_text(json.dumps(proof), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "proof incomplete"):
                live_preflight(path, cli, "baseline", self.workspace)
            proof["capability"]["cancellation"]["usage_drained"] = True
            proof["capability"]["arm"] = "treatment"
            path.write_text(json.dumps(proof), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "proof incomplete"):
                live_preflight(path, cli, "baseline", self.workspace)
            proof["capability"]["arm"] = "baseline"
            proof["capability"]["cancellation_receipt_sha256"] = "0" * 64
            path.write_text(json.dumps(proof), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "proof incomplete"):
                live_preflight(path, cli, "baseline", self.workspace)
            proof["capability"]["cancellation_receipt_sha256"] = file_sha(evidence_path)
            proof["routing_config_canonical_sha256"] = "0" * 64
            path.write_text(json.dumps(proof), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "proof incomplete"):
                live_preflight(path, cli, "baseline", self.workspace)

    def test_treatment_preflight_rechecks_separate_adjudication_and_raw_binding(self) -> None:
        spec = manifest()
        spec["live_enabled"] = True
        spec.pop("pilot", None)
        (self.workspace / "treatment").mkdir()
        cli = self.local_cli(spec)
        policy = arm_execution(spec, "treatment")
        runtime = {"arm": "treatment", "cli_options": policy["cli_options"],
            "policy_sha256": policy["policy_sha256"],
            "prompt_suffix_sha256": policy["prompt_suffix_sha256"],
            "router_hook_count": 5, "model_turns": 0,
            "thread_started_without_turn": True,
            "protocol_contract": {"client_request_sha256": "1" * 64,
                "dispatch_contract_sha256": "2" * 64,
                "installed_hook_closure_sha256": "3" * 64,
                "interrupt_method": "turn/interrupt"},
            "model_catalog": {"gpt-6-sol": "low", "gpt-6-astra": "xhigh"}}
        source = {"kind": "cancellation-capability", "status": "UNKNOWN",
            "live_enabled": False, "arm": "treatment", "model": "gpt-6-sol",
            "effort": "low", "runtime_binding": runtime,
            "runtime_binding_sha256": _canonical_sha(runtime),
            "parent_thread_id": "parent", "turn_id": "parent-turn",
            "real_child_observed": True, "wall_seconds": 85,
            "accounting_issues": ["parent session lacks successful terminal result"],
            "native_attempts": [{"session_id": "parent", "turn_id": "parent-turn",
                "child_id": "child", "model": "gpt-6-astra", "effort": "xhigh",
                "call_line": 1, "start_line": 2, "result_line": 3}],
            "usage": {"estimated_usd_upper_bound": 0.2, "sessions": [
                {"id": "parent", "calls": 2, "terminal": "turn_aborted",
                 "reported_total_usage": {}},
                {"id": "child", "calls": 2, "terminal": "turn_aborted",
                 "model": "gpt-6-astra", "effort": "xhigh",
                 "reported_total_usage": {}}]},
            "cancellation": {"status": "verified-drained", "interrupt_ack": True,
                "turn_completed": True, "usage_drained": True,
                "descendants_drained": True}}
        receipt = self.workspace / "cancellation.json"
        receipt.write_text(json.dumps(source), encoding="utf-8")
        sessions = self.workspace / "sessions"
        sessions.mkdir()
        adjudication_proof = {"parent_rollout": {"session_id": "parent", "sha256": "a" * 64},
            "child_rollout": {"session_id": "child", "sha256": "b" * 64}}
        prior_path = self.workspace / "adjudication-v1.json"
        write_legacy_cancel_decision(prior_path, receipt, "treatment",
                                     adjudication_proof)
        decision = {"schema_version": 2, "kind": "cancellation-adjudication",
            "status": "verified", "source_receipt_status": "UNKNOWN",
            "source_receipt_sha256": file_sha(receipt), "arm": "treatment",
            "live_enabled": False, "reason": None,
            "source_adjudication": {"path": str(prior_path.resolve()),
                "sha256": file_sha(prior_path)},
            "proof": json.loads(json.dumps(adjudication_proof))}
        decision_path = self.workspace / "adjudication.json"
        decision_path.write_text(json.dumps(decision), encoding="utf-8")
        proof = {"status": "verified", "cli_sha256": spec["runtime"]["cli_sha256"],
            "code_mode_host_sha256": spec["runtime"]["code_mode_host_sha256"],
            "routing_config_canonical_sha256": "routing-digest",
            "cli_options": policy["cli_options"],
            "arm_execution_policy_sha256": policy["policy_sha256"],
            "prompt_suffix_sha256": policy["prompt_suffix_sha256"],
            "native_spawn_available": True, "router_hooks_verified": True,
            "preparation_sha256": "prepared-digest", "multi_turn_same_thread": True,
            "child_usage_complete": True, "cancellation_verified": True,
            "selector_verified": True, "model": "gpt-6-sol", "effort": "low",
            "capability": {"status": "verified", "arm": "treatment",
                "model": "gpt-6-sol", "effort": "low", "canary_id": "treatment-cancel-1",
                "runtime_binding_sha256": _canonical_sha(runtime),
                "cancellation": {"status": "verified", "interrupt_ack": True,
                    "turn_completed": True, "usage_drained": True,
                    "descendants_drained": True, "real_child_observed": True,
                    "child_model": "gpt-6-astra", "child_effort": "xhigh"},
                "cancellation_receipt_path": str(receipt.resolve()),
                "cancellation_receipt_sha256": file_sha(receipt),
                "cancellation_adjudication_path": str(decision_path.resolve()),
                "cancellation_adjudication_sha256": file_sha(decision_path),
                "cancellation_sessions_path": str(sessions.resolve())}}
        proof_path = self.workspace / "capability.json"
        def check() -> None:
            proof_path.write_text(json.dumps(proof), encoding="utf-8")
            return live_preflight(proof_path, cli, "treatment", self.workspace)
        with ExitStack() as stack:
            stack.enter_context(mock_patch("evals.long_horizon_v1.run.manifest", return_value=spec))
            stack.enter_context(mock_patch("evals.long_horizon_v1.run.verify_cli", return_value={
                "cli_sha256": spec["runtime"]["cli_sha256"],
                "code_mode_host_sha256": spec["runtime"]["code_mode_host_sha256"]}))
            stack.enter_context(mock_patch("evals.long_horizon_v1.run.verify_prepared",
                return_value={"preparation_sha256": "prepared-digest"}))
            stack.enter_context(mock_patch("evals.long_horizon_v1.run.verify_arm_config",
                return_value={"canonical_sha256": "routing-digest"}))
            stack.enter_context(mock_patch("evals.long_horizon_v1.run.verify_arm_runtime",
                return_value=runtime))
            judge = stack.enter_context(mock_patch(
                "evals.long_horizon_v1.cancellation_adjudication._judge",
                return_value=adjudication_proof))
            self.assertEqual(check()["model"], "gpt-6-sol")
            judge.assert_called_once_with(source, sessions, receipt)
            proof["capability"]["cancellation_adjudication_sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "proof incomplete"):
                check()
            proof["capability"]["cancellation_adjudication_sha256"] = file_sha(decision_path)
            decision["proof"]["child_rollout"]["sha256"] = "0" * 64
            decision_path.write_text(json.dumps(decision), encoding="utf-8")
            proof["capability"]["cancellation_adjudication_sha256"] = file_sha(decision_path)
            with self.assertRaisesRegex(ValueError, "proof incomplete"):
                check()
            decision["proof"] = adjudication_proof
            decision_path.write_text(json.dumps(decision), encoding="utf-8")
            proof["capability"]["cancellation_adjudication_sha256"] = file_sha(decision_path)
            source["runtime_binding_sha256"] = "0" * 64
            receipt.write_text(json.dumps(source), encoding="utf-8")
            proof["capability"]["cancellation_receipt_sha256"] = file_sha(receipt)
            with self.assertRaisesRegex(ValueError, "proof incomplete"):
                check()
            source["runtime_binding_sha256"] = _canonical_sha(runtime)
            source["arm"] = "baseline"
            receipt.write_text(json.dumps(source), encoding="utf-8")
            decision["source_receipt_sha256"] = file_sha(receipt)
            decision_path.write_text(json.dumps(decision), encoding="utf-8")
            proof["capability"]["cancellation_receipt_sha256"] = file_sha(receipt)
            proof["capability"]["cancellation_adjudication_sha256"] = file_sha(decision_path)
            with self.assertRaisesRegex(ValueError, "proof incomplete"):
                check()
            proof["capability"]["cancellation_receipt_path"] = str(self.workspace / "missing.json")
            with self.assertRaisesRegex(ValueError, "proof incomplete"):
                check()
            proof["capability"]["cancellation_receipt_path"] = str(receipt)
            proof["capability"]["cancellation_adjudication_path"] = str(
                self.workspace / "missing-adjudication.json")
            with self.assertRaisesRegex(ValueError, "proof incomplete"):
                check()

    def test_fresh_preparation_rejects_reuse_later_reveal_and_product_drift(self) -> None:
        root = self.workspace / "fresh-prepared"
        spec = self.local_plugin_spec(manifest())
        with mock_patch("evals.long_horizon_v1.prepare.manifest", return_value=spec):
            prepared = prepare(root)
        treatment = root / "treatment"
        expected_arms = (["baseline", "treatment"] if
                         spec.get("pilot", {}).get("schema_version") in (5, 7) else ["treatment"])
        self.assertEqual(list(prepared["arms"]), expected_arms)
        self.assertEqual((root / "baseline").exists(), "baseline" in expected_arms)
        real_scandir = os.scandir
        git_object_scans = []

        def guarded_scandir(path):
            if Path(path).parts[-2:] == (".git", "objects"):
                git_object_scans.append(path)
                raise FileNotFoundError("Git objects changed during traversal")
            return real_scandir(path)

        with mock_patch("os.scandir", side_effect=guarded_scandir):
            t = verify_prepared(root, "treatment", spec)
        self.assertEqual(git_object_scans, [])
        self.assertEqual(t["start_tree"], spec["start_tree"])
        self.assertEqual(file_sha(treatment / ".benchmark" / "submit_checkpoint.py"),
                         spec["assets"]["submit_checkpoint.py"])
        self.assertFalse((treatment / ".benchmark" / "round1.md").exists())
        self.assertFalse((treatment / ".benchmark" / "oracle").exists())
        with mock_patch("evals.long_horizon_v1.prepare.manifest", return_value=spec), \
             self.assertRaisesRegex(ValueError, "output exists"):
            prepare(root)
        checkpoint = treatment / ".benchmark" / "checkpoint.json"
        checkpoint.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unexpected files"):
            verify_prepared(root, "treatment", spec)
        checkpoint.unlink()
        (treatment / ".benchmark" / "round1.md").write_text("future", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unexpected files"):
            verify_prepared(root, "treatment", spec)
        (treatment / ".benchmark" / "round1.md").unlink()
        oracle = treatment / ".benchmark" / "oracle" / "hidden_test.go"
        oracle.parent.mkdir()
        oracle.write_text("hidden", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unexpected files"):
            verify_prepared(root, "treatment", spec)
        oracle.unlink()
        (treatment / "go.mod").write_text("contaminated", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "clean frozen start tree"):
            verify_prepared(root, "treatment", spec)

    def test_arm_prompt_suffixes_preserve_shared_text(self) -> None:
        spec = manifest()
        shared = "Frozen R0 task and environment."
        baseline = submitted_prompt(shared, "baseline", spec)
        treatment = submitted_prompt(shared, "treatment", spec)
        self.assertEqual(baseline.split("\n\n", 1)[0], shared)
        self.assertEqual(treatment.split("\n\n", 1)[0], shared)
        self.assertIn("Do not start child agents", baseline)
        self.assertIn(("Delegate bounded hard reasoning" if
                       spec.get("pilot", {}).get("schema_version") in (6, 7) else
                       "I authorize you to delegate"), treatment)
        self.assertNotIn("fixed child count", baseline)
        self.assertFalse(baseline_has_child("parent", [{"id": "parent"}]))
        self.assertTrue(baseline_has_child("parent", [{"id": "parent"}, {"id": "child"}]))

    def test_per_turn_usage_deduplicates_cumulative_snapshot(self) -> None:
        session_root = self.workspace / "sessions"
        session_root.mkdir()
        usage = {"input_tokens": 1000, "cached_input_tokens": 500,
                 "cache_write_input_tokens": 100, "output_tokens": 100,
                 "reasoning_output_tokens": 20}
        events = [
            {"type": "session_meta", "payload": {"id": "parent", "agent_path": "/root"}},
            {"type": "turn_context", "payload": {"turn_id": "turn-1", "model": "gpt-6-astra", "effort": "xhigh"}},
            {"type": "event_msg", "payload": {"type": "token_count", "info": {
                "last_token_usage": usage, "total_token_usage": usage}}},
            {"type": "event_msg", "payload": {"type": "token_count", "info": {
                "last_token_usage": usage, "total_token_usage": usage}}},
            {"type": "event_msg", "payload": {"type": "task_complete"}},
        ]
        (session_root / "rollout-parent.jsonl").write_text(
            "".join(json.dumps(item) + "\n" for item in events), encoding="utf-8")
        observed = account(session_root, "parent", "gpt-6-astra", "xhigh", False)
        self.assertEqual(observed["status"], "complete")
        self.assertEqual(len(observed["responses"]), 1)
        self.assertEqual(observed["responses"][0]["turn_id"], "turn-1")

    def test_multi_turn_usage_reconciles_and_missing_turn_context_fails(self) -> None:
        root = self.workspace / "sessions"
        root.mkdir()
        first = {"input_tokens": 100, "cached_input_tokens": 0,
                 "cache_write_input_tokens": 0, "output_tokens": 10,
                 "reasoning_output_tokens": 0}
        second = {key: value * 2 for key, value in first.items()}
        events = [
            {"type": "session_meta", "payload": {"id": "parent", "agent_path": "/root"}},
            {"type": "turn_context", "payload": {"turn_id": "turn-1",
                "model": "gpt-6-sol", "effort": "low"}},
            {"type": "event_msg", "payload": {"type": "token_count", "info": {
                "last_token_usage": first, "total_token_usage": first}}},
            {"type": "event_msg", "payload": {"type": "token_count", "info": {
                "last_token_usage": first, "total_token_usage": first}}},
            {"type": "turn_context", "payload": {"turn_id": "turn-2",
                "model": "gpt-6-sol", "effort": "low"}},
            {"type": "event_msg", "payload": {"type": "token_count", "info": {
                "last_token_usage": first, "total_token_usage": second}}},
            {"type": "event_msg", "payload": {"type": "task_complete"}},
        ]
        path = root / "rollout-parent.jsonl"
        path.write_text("".join(json.dumps(item) + "\n" for item in events),
                        encoding="utf-8")
        observed = account(root, "parent", "gpt-6-sol", "low", False)
        self.assertEqual(observed["status"], "complete", observed["issues"])
        self.assertEqual([item["turn_id"] for item in observed["responses"]],
                         ["turn-1", "turn-2"])
        self.assertEqual(observed["summary"]["estimated_usd"], 0.0006)
        events[4]["payload"].pop("turn_id")
        path.write_text("".join(json.dumps(item) + "\n" for item in events),
                        encoding="utf-8")
        self.assertEqual(account(root, "parent", "gpt-6-sol", "low", False)["status"],
                         "UNKNOWN")

    def test_transport_rejects_completed_turn_without_parent_identity(self) -> None:
        transport = object.__new__(AppServerTransport)
        transport.thread_id = "parent"
        transport.model, transport.effort = "gpt-6-sol", "low"
        transport.workspace = self.workspace
        transport.deadline = time.monotonic() + 30
        transport._send = lambda *_args, **_kwargs: 3
        transport._response = lambda _request, _budget=None: {"turn": {"id": "turn-1"}}
        transport._frame = lambda: {"method": "turn/completed",
                                    "params": {"turn": {"id": "turn-1",
                                                        "status": "completed"}}}
        with self.assertRaisesRegex(TransportError, "parent identity"):
            transport.turn("synthetic", lambda: None)

    def test_transport_preflight_rejects_expired_deadline_without_turn_start(self) -> None:
        transport = object.__new__(AppServerTransport)
        transport.thread_id = "parent"
        transport.deadline = time.monotonic() - 1
        sends = []
        transport._send = lambda *args, **kwargs: sends.append((args, kwargs))
        with self.assertRaisesRegex(TransportError, "wall limit"):
            transport.turn("synthetic", lambda: None)
        self.assertEqual(sends, [])

    def test_transport_preflight_rejects_budget_without_turn_start(self) -> None:
        transport = object.__new__(AppServerTransport)
        transport.thread_id = "parent"
        transport.deadline = time.monotonic() + 30
        sends = []
        transport._send = lambda *args, **kwargs: sends.append((args, kwargs))
        def reject() -> None:
            raise TransportError("$1 bound")
        with self.assertRaisesRegex(TransportError, "\\$1 bound"):
            transport.turn("synthetic", reject)
        self.assertEqual(sends, [])

    def test_transport_budget_crossing_during_start_response_stops_next_turn(self) -> None:
        transport = object.__new__(AppServerTransport)
        transport.thread_id = "parent"
        transport.model, transport.effort = "gpt-6-sol", "low"
        transport.workspace = self.workspace
        transport.deadline = time.monotonic() + 30
        sends = []
        transport._send = lambda method, params: sends.append(method) or len(sends)
        transport._frame = lambda: {}
        checks = []
        def budget() -> None:
            checks.append(None)
            if len(checks) >= 3:
                raise TransportError("$1 bound")
        with self.assertRaisesRegex(TransportError, "\\$1 bound"):
            transport.turn("first", budget)
        with self.assertRaisesRegex(TransportError, "\\$1 bound"):
            transport.turn("second", budget)
        self.assertEqual(sends, ["turn/start"])
        self.assertEqual(len(checks), 4)

    def test_transport_deadline_crossing_during_start_response_stops_wait(self) -> None:
        transport = object.__new__(AppServerTransport)
        transport.thread_id = "parent"
        transport.model, transport.effort = "gpt-6-sol", "low"
        transport.workspace = self.workspace
        transport.deadline = time.monotonic() + 30
        sends = []
        transport._send = lambda method, params: sends.append(method) or len(sends)
        def pending_frame() -> dict:
            transport.deadline = time.monotonic() - 1
            return {}
        transport._frame = pending_frame
        with self.assertRaisesRegex(TransportError, "wall limit"):
            transport.turn("synthetic", lambda: None)
        self.assertEqual(sends, ["turn/start"])

    def test_transport_normal_turn_keeps_completion_identity(self) -> None:
        transport = object.__new__(AppServerTransport)
        transport.thread_id = "parent"
        transport.model, transport.effort = "gpt-6-sol", "low"
        transport.workspace = self.workspace
        transport.deadline = time.monotonic() + 30
        sends = []
        transport._send = lambda method, params: sends.append(method) or len(sends)
        frames = iter([
            {"id": 1, "result": {"turn": {"id": "turn-1"}}},
            {"method": "turn/completed", "params": {"threadId": "other",
                "turn": {"id": "other-turn", "status": "completed"}}},
            {"method": "turn/completed", "params": {"threadId": "parent",
                "turn": {"id": "turn-1", "status": "completed"}}},
        ])
        transport._frame = lambda: next(frames)
        checks = []
        result = transport.turn("synthetic", lambda: checks.append(None))
        self.assertEqual(result, {"turn_id": "turn-1", "thread_id": "parent",
                                  "status": "completed"})
        self.assertEqual(sends, ["turn/start"])
        self.assertGreaterEqual(len(checks), 3)

    def test_transport_checks_every_delta_and_priced_frame_stops_turn(self) -> None:
        transport = object.__new__(AppServerTransport)
        transport.thread_id = "parent"
        transport.model, transport.effort = "gpt-6-sol", "low"
        transport.workspace = self.workspace
        transport.deadline = time.monotonic() + 30
        sends = []
        transport._send = lambda method, _params: sends.append(method) or len(sends)
        transport._response = lambda *_args: {"turn": {"id": "turn-1"}}
        frames = iter([{"method": "item/commandExecution/outputDelta"}] * 5000 +
                      [{"method": "thread/tokenUsage/updated"}])
        transport._frame = lambda: next(frames)
        checks = []
        observed = []
        def frame_check(frame):
            observed.append(frame["method"])
            if frame["method"] == "thread/tokenUsage/updated":
                raise TransportError("cost-safety-threshold")
        with self.assertRaisesRegex(TransportError, "cost-safety-threshold"):
            transport.turn("synthetic", lambda: checks.append(None), frame_check)
        self.assertEqual(sends, ["turn/start"])
        self.assertGreaterEqual(len(checks), 5001)
        self.assertEqual(observed.count("item/commandExecution/outputDelta"), 5000)
        self.assertEqual(observed[-1], "thread/tokenUsage/updated")

    def test_pilot_observed_fifteen_dollar_stop_blocks_first_turn(self) -> None:
        (self.workspace / "baseline").mkdir()
        proof = {"cli_sha256": "1" * 64, "code_mode_host_sha256": "2" * 64,
            "routing_config_canonical_sha256": None,
            "preparation": {"preparation_sha256": "3" * 64},
            "zero_model_runtime_binding": {"arm": "baseline"}}
        turns = []
        class StoppedTransport:
            def __init__(self, *_args, **_kwargs):
                self.thread_id = "parent"
                self.active_turn_complete = True
                self.events = []
            def start(self):
                return "parent"
            def turn(self, *_args):
                turns.append(True)
                raise AssertionError("turn must not start at observed stop")
            def close(self):
                pass
        class PricedMeter:
            def __init__(self, *_args, **_kwargs):
                self.cost_upper = 15.0
                self.unknown_models = []
                self.unknown_usage = []
                self.paths = {}
                self.calls = 1
            def refresh(self):
                pass
        account = {"summary": {"sessions": [], "estimated_usd": 15.0,
                    "estimated_usd_upper_bound": 15.0},
                   "issues": [], "responses": [], "by_model": {},
                   "failed_child_attempts": [], "native_attempts": []}
        output = self.workspace / "cap-stop"
        with mock_patch("evals.long_horizon_v1.run.manifest",
                        return_value={**manifest(), "pilot": None}), \
             mock_patch("evals.long_horizon_v1.run.live_preflight", return_value=proof), \
             mock_patch("evals.long_horizon_v1.run.AppServerTransport", StoppedTransport), \
             mock_patch("evals.long_horizon_v1.run.SessionMeter", PricedMeter), \
             mock_patch("evals.long_horizon_v1.run.account", return_value=account):
            result = live_run(self.workspace, output, "baseline", self.workspace / "cli",
                              self.workspace / "capability", self.workspace / "sessions")
        self.assertEqual(result["stop_reason"], "cost-safety-threshold")
        self.assertEqual(turns, [])
        self.assertTrue((output / "run.json").is_file())

    def test_synthetic_native_child_links_one_attempt_and_prices_both_sessions(self) -> None:
        fixture = json.loads((Path(__file__).parent / "fixtures" /
                              "native-multiturn-observed-sanitized.json").read_text(encoding="utf-8"))
        parent_id = fixture["parent_id"]
        child_id = fixture["child_id"]
        parent_events = fixture["native"][:9]
        parent_events[1]["payload"].update(model="gpt-6-sol", effort="low")
        parent_usage = {"input_tokens": 100, "cached_input_tokens": 0,
                        "cache_write_input_tokens": 0, "output_tokens": 10,
                        "reasoning_output_tokens": 0}
        child_usage = {"input_tokens": 50, "cached_input_tokens": 0,
                       "cache_write_input_tokens": 0, "output_tokens": 5,
                       "reasoning_output_tokens": 0}
        parent_events += [
            {"type": "event_msg", "payload": {"type": "token_count", "info": {
                "last_token_usage": parent_usage, "total_token_usage": parent_usage}}},
            {"type": "event_msg", "payload": {"type": "task_complete",
                "turn_id": "parent-turn-1"}},
        ]
        child_events = fixture["child"][:7] + [
            {"type": "event_msg", "payload": {"type": "token_count", "info": {
                "last_token_usage": child_usage, "total_token_usage": child_usage}}},
            fixture["child"][7],
        ]
        root = self.workspace / "native-sessions"
        root.mkdir()
        parent_path, child_path = root / "rollout-parent.jsonl", root / "rollout-child.jsonl"
        parent_path.write_text("".join(json.dumps(row) + "\n" for row in parent_events),
                               encoding="utf-8")
        child_path.write_text("".join(json.dumps(row) + "\n" for row in child_events),
                              encoding="utf-8")
        observed = account(root, parent_id, "gpt-6-sol", "low", True)
        self.assertEqual(observed["status"], "complete", observed["issues"])
        self.assertEqual(observed["summary"]["model_calls"], 2)
        self.assertEqual({item["session_id"] for item in observed["responses"]},
                         {parent_id, child_id})
        self.assertEqual(len(observed["native_attempts"]), 1)
        self.assertEqual(observed["native_attempts"][0]["child_id"], child_id)
        self.assertEqual(observed["native_attempts"][0]["turn_id"],
                         parent_events[1]["payload"]["turn_id"])
        self.assertEqual(observed["summary"]["estimated_usd"], 0.00045)
        child_path.unlink()
        missing = account(root, parent_id, "gpt-6-sol", "low", True)
        self.assertEqual(missing["status"], "UNKNOWN")
        self.assertTrue(any("child" in issue for issue in missing["issues"]))

    def _observed_multiturn_rollouts(self) -> tuple[Path, str, str, list, list]:
        fixture_dir = Path(__file__).parent / "fixtures"
        native = json.loads((fixture_dir / "native-multiturn-observed-sanitized.json")
                            .read_text(encoding="utf-8"))
        measured = json.loads((fixture_dir / "native-multiturn-usage-sanitized.json")
                              .read_text(encoding="utf-8"))
        parent_id, child_id = native["parent_id"], native["child_id"]
        parent, child = native["native"], native["child"]
        parent[1]["payload"].update(model="gpt-6-sol", effort="low")

        def priced_rows(rows: list, session_id: str, turn_id: str,
                        thread_total: dict, prefix: str) -> list:
            output = []
            turn_total: dict[str, int] = {}
            for index, row in enumerate(rows):
                usage = row["usage"]
                turn_total = {key: turn_total.get(key, 0) + value
                              for key, value in usage.items()}
                thread_total.update({key: thread_total.get(key, 0) + value
                                     for key, value in usage.items()})
                output.extend([
                    {"type": "token_usage_record", "payload": {
                        "thread_id": session_id, "session_id": parent_id,
                        "turn_id": turn_id, "root_turn_id": row["root_turn_id"],
                        "response_id": f"{prefix}-{index}",
                        "usage": usage, "turn_token_usage": dict(turn_total),
                        "thread_token_usage": dict(thread_total)}},
                    {"type": "event_msg", "payload": {"type": "token_count",
                        "info": {"last_token_usage": usage,
                                 "total_token_usage": dict(thread_total)}}},
                ])
            return output

        parent_turn = parent[1]["payload"]["turn_id"]
        parent = parent + priced_rows(measured["parent"], parent_id, parent_turn,
                                       {}, "parent") + [
            {"type": "event_msg", "payload": {"type": "task_complete",
                "turn_id": parent_turn}}]
        child_events = []
        child_total: dict[str, int] = {}
        for item in child:
            if item["type"] == "event_msg" and item["payload"].get("type") == "task_complete":
                turn_id = item["payload"]["turn_id"]
                turn_index = 0 if turn_id == "child-turn-1" else 1
                usage = [row for row in measured["child"]
                         if row["turn_index"] == turn_index]
                child_events.extend(priced_rows(usage, child_id, turn_id,
                                                child_total, f"child-{turn_index}"))
            child_events.append(item)
        root = self.workspace / "observed-multiturn"
        root.mkdir()
        return root, parent_id, child_id, parent, child_events

    @staticmethod
    def _write_multiturn(root: Path, parent: list, child: list) -> None:
        (root / "rollout-parent.jsonl").write_text(
            "".join(json.dumps(item) + "\n" for item in parent), encoding="utf-8")
        (root / "rollout-child.jsonl").write_text(
            "".join(json.dumps(item) + "\n" for item in child), encoding="utf-8")

    @staticmethod
    def _fork_plan(root: Path, parent: list) -> Path:
        plans = root / ".benchmark" / "dispatch-plans"
        plans.mkdir(parents=True)
        spawn = next(item for item in parent if item["type"] == "response_item" and
                     item["payload"].get("name") == "spawn_agent")
        spawn["timestamp"] = (
            datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
        (root / "rollout-parent.jsonl").write_text(
            "".join(json.dumps(item) + "\n" for item in parent), encoding="utf-8")
        arguments = json.loads(spawn["payload"]["arguments"])
        packet = {"stage_id": "fixture-stage", "owner": arguments["task_name"],
                  "dependencies": [], "write_scope": ["fixture-only"],
                  "context_budget": "bounded", "acceptance_criteria": "complete",
                  "self_check": "inspect", "receipt": "compact"}
        (plans / f"{arguments['task_name']}.plan.json").write_text(
            json.dumps({"arguments": arguments, "packet": packet}), encoding="utf-8")
        (plans / f"{arguments['task_name']}.json").write_text(
            json.dumps(planned_commitment(arguments, packet)), encoding="utf-8")
        return plans

    def test_runner_final_fork_observation_accepts_closed_child_ledger(self) -> None:
        root, parent_id, child_id, parent, child = self._observed_multiturn_rollouts()
        self._write_multiturn(root, parent, child)
        plans = self._fork_plan(root, parent)
        observed = account(root, parent_id, "gpt-6-sol", "low", True,
                           fork_policy_plans=plans)
        self.assertEqual(observed["status"], "complete", observed["issues"])
        meter = CanarySessionMeter(root, parent_id, "gpt-6-sol", "low", True)
        meter.refresh()
        calls, issues = final_fork_observation(
            meter, parent_id, root, observed["native_attempts"])
        self.assertEqual(issues, [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["fork_turns"], "none")
        self.assertEqual(calls[0]["child_id"], child_id)
        self.assertEqual(calls[0]["agent_path"], "/root/worker")

    def test_observed_two_turn_usage_joins_closed_ledger_without_double_charge(self) -> None:
        root, parent_id, child_id, parent, child = self._observed_multiturn_rollouts()
        first_count = next(index for index, item in enumerate(child)
                           if item["type"] == "event_msg" and
                           item["payload"].get("type") == "token_count")
        child.insert(first_count + 1, json.loads(json.dumps(child[first_count])))
        self._write_multiturn(root, parent, child)
        observed = account(root, parent_id, "gpt-6-sol", "low", True)
        self.assertEqual(observed["status"], "complete", observed["issues"])
        self.assertEqual(observed["summary"]["model_calls"], 112)
        self.assertEqual(observed["summary"]["estimated_usd"], 1.7349132)
        self.assertEqual(len(observed["native_attempts"]), 1)
        self.assertEqual(len(observed["native_attempts"][0]["turns"]), 2)
        self.assertEqual([sum(item["session_id"] == child_id and
                              item["turn_id"] == f"child-turn-{number}"
                              for item in observed["responses"]) for number in (1, 2)],
                         [65, 4])
        self.assertEqual(len({row["response_id_sha256"] for row in
                              observed["responses"]}), 112)

    def test_observed_two_turn_missing_or_ambiguous_evidence_fails_closed(self) -> None:
        root, parent_id, _child_id, parent, original = self._observed_multiturn_rollouts()
        for change in ("missing-terminal", "missing-usage", "missing-raw",
                       "wrong-turn-abort", "duplicate-response", "cumulative-reset",
                       "selector-drift", "wrong-root-turn", "conflicting-snapshot",
                       "duplicate-billed-snapshot"):
            with self.subTest(change=change):
                child = json.loads(json.dumps(original))
                second_raw = next(i for i, item in enumerate(child)
                                  if item["type"] == "token_usage_record" and
                                  item["payload"]["turn_id"] == "child-turn-2")
                if change == "missing-terminal":
                    child.pop()
                elif change == "missing-usage":
                    child = [item for index, item in enumerate(child) if not (
                        index >= second_raw and
                        (item["type"] == "token_usage_record" or
                         item["type"] == "event_msg" and
                         item["payload"].get("type") == "token_count"))]
                elif change == "missing-raw":
                    child.pop(second_raw)
                elif change == "wrong-turn-abort":
                    child[-1]["payload"].update(type="turn_aborted",
                                                 turn_id="child-turn-1",
                                                 reason="interrupted")
                elif change == "duplicate-response":
                    first = next(item for item in child
                                 if item["type"] == "token_usage_record")
                    child[second_raw]["payload"]["response_id"] = first["payload"]["response_id"]
                elif change == "cumulative-reset":
                    usage = child[second_raw]["payload"]["usage"]
                    child[second_raw]["payload"]["thread_token_usage"] = usage
                    child[second_raw + 1]["payload"]["info"]["total_token_usage"] = usage
                elif change == "selector-drift":
                    next(item for item in child if item["type"] == "turn_context" and
                         item["payload"]["turn_id"] == "child-turn-2")["payload"]["model"] = "gpt-6-astra"
                elif change == "wrong-root-turn":
                    child[second_raw]["payload"]["root_turn_id"] = "other-parent-turn"
                elif change == "conflicting-snapshot":
                    duplicate = json.loads(json.dumps(child[second_raw + 1]))
                    duplicate["payload"]["info"]["last_token_usage"]["output_tokens"] += 1
                    child.insert(second_raw + 2, duplicate)
                elif change == "duplicate-billed-snapshot":
                    duplicate = json.loads(json.dumps(child[second_raw:second_raw + 2]))
                    duplicate[0]["payload"]["response_id"] = "new-billed-response"
                    child[second_raw + 2:second_raw + 2] = duplicate
                self._write_multiturn(root, parent, child)
                result = account(root, parent_id, "gpt-6-sol", "low", True)
                self.assertEqual(result["status"], "UNKNOWN", (change, result["issues"]))

    def test_second_child_turn_prefix_is_active_for_cancellation(self) -> None:
        root, parent_id, child_id, parent, child = self._observed_multiturn_rollouts()
        child.pop()  # The second turn has started and used tokens, but has no terminal.
        self._write_multiturn(root, parent, child)
        meter = CanarySessionMeter(root, parent_id, "gpt-6-sol", "low", True)
        meter.refresh()
        state = next(row for row in meter.paths.values() if row["id"] == child_id)
        self.assertEqual(state["turns"][0]["terminal"], "task_complete")
        self.assertIsNone(state["turns"][1]["terminal"])
        self.assertIsNone(state["terminal"])
        transport = object.__new__(AppServerTransport)
        transport.thread_id, transport.active_turn_id = parent_id, "parent-turn-1"
        transport.active_turn_complete = True
        requests = []
        transport._send = lambda _method, params: requests.append(params) or 1
        transport._frame = lambda **_kwargs: (_ for _ in ()).throw(
            TransportError("offline drain stopped"))
        receipt = transport.cancel_and_drain(meter, timeout=0.1)
        self.assertEqual(receipt["status"], "UNKNOWN")
        self.assertEqual(requests, [{"threadId": child_id, "turnId": "child-turn-2"}])

    def test_active_guard_requires_usage_in_followup_turn(self) -> None:
        class Meter:
            paths = {Path("parent"): {"id": "parent", "calls": 1,
                "turns": [{"turn_id": "parent-turn", "model": "gpt-6-sol",
                           "effort": "low", "calls": 1}]},
                Path("child"): {"id": "child", "calls": 1,
                    "turns": [{"turn_id": "child-one", "model": "gpt-6-sol",
                               "effort": "high", "calls": 1},
                              {"turn_id": "child-two", "model": "gpt-6-sol",
                               "effort": "high", "calls": 0}]}}
            calls = 2
            unknown_models = set()
            unknown_usage = []

            @staticmethod
            def dispatch_coverage_issues() -> list:
                return []

        guard = ActiveTelemetryGuard("treatment")
        guard.check(Meter(), "parent", "gpt-6-sol", "low", 0, 0, now=1,
                    turn_id="parent-turn")
        with self.assertRaisesRegex(TransportError, "turn usage missing beyond grace"):
            guard.check(Meter(), "parent", "gpt-6-sol", "low", 0, 0, now=1000,
                        turn_id="parent-turn")

    def test_current_turn_drain_records_child_first_interrupt_proof(self) -> None:
        transport = object.__new__(AppServerTransport)
        transport.thread_id, transport.active_turn_id = "parent", "parent-turn"
        transport.active_turn_complete = False
        sent = []
        transport._send = lambda _method, params: sent.append(params) or len(sent)
        parent = {"id": "parent", "terminal": None, "calls": 1,
                  "last_total_usage": {}, "turns": [{"turn_id": "parent-turn",
                      "terminal": None, "calls": 1}]}
        child = {"id": "child", "terminal": None, "calls": 1,
                 "last_total_usage": {}, "turns": [{"turn_id": "child-turn-2",
                     "terminal": None, "calls": 1}]}

        class Meter:
            paths = {Path("parent"): parent, Path("child"): child}
            calls = 2
            unknown_models = set()
            unknown_usage = []

            @staticmethod
            def refresh() -> None:
                pass

            @staticmethod
            def dispatch_coverage_issues() -> list:
                return []

        frames = [{"id": 1, "result": {}}, {"id": 2, "result": {}}, {}, {}, {}]

        def frame(**_kwargs):
            transport.active_turn_complete = True
            parent["terminal"] = parent["turns"][-1]["terminal"] = "turn_aborted"
            child["terminal"] = child["turns"][-1]["terminal"] = "turn_aborted"
            return frames.pop(0)

        transport._frame = frame

        def before(_thread: str, _turn: str) -> dict:
            now = time.time_ns()
            return {"target_thread_id": "child", "target_turn_id": "child-turn-2",
                    "marker_observed_ns": now,
                    "completion_absent_checked_ns": time.time_ns()}

        with mock_patch("evals.long_horizon_v1.transport.DRAIN_QUIET_SECONDS", 0), \
             mock_patch("evals.long_horizon_v1.transport.DRAIN_REFRESH_SECONDS", 0):
            receipt = transport.cancel_and_drain(Meter(), timeout=1,
                descendants_first=True, before_interrupt=before)
        self.assertEqual(receipt["status"], "verified-drained")
        self.assertEqual(sent, [{"threadId": "child", "turnId": "child-turn-2"},
                                {"threadId": "parent", "turnId": "parent-turn"}])
        self.assertEqual(receipt["interrupted_threads"], ["child", "parent"])
        self.assertTrue(all(row["pre_dispatch_evidence"]["target_turn_id"] ==
                            "child-turn-2" for row in receipt["interrupt_requests"]))

    def test_drain_rejects_parent_usage_from_prior_turn(self) -> None:
        transport = object.__new__(AppServerTransport)
        transport.thread_id, transport.active_turn_id = "parent", "parent-new"
        transport.active_turn_complete = True
        transport._send = lambda *_args, **_kwargs: 1
        child = {"id": "child", "terminal": None, "calls": 1,
                 "last_total_usage": {}, "turns": [{"turn_id": "child-new",
                     "terminal": None, "calls": 1}]}

        class Meter:
            paths = {Path("parent"): {"id": "parent", "terminal": "task_complete",
                "calls": 1, "last_total_usage": {}, "turns": [{"turn_id": "parent-old",
                    "terminal": "task_complete", "calls": 1}]},
                Path("child"): child}
            calls = 2
            unknown_models = set()
            unknown_usage = []

            @staticmethod
            def refresh() -> None:
                pass

            @staticmethod
            def dispatch_coverage_issues() -> list:
                return []

        transport._frame = lambda **_kwargs: (
            child.update(terminal="turn_aborted") or
            child["turns"][-1].update(terminal="turn_aborted") or
            {"id": 1, "result": {}})
        receipt = transport.cancel_and_drain(Meter(), timeout=0.02)
        self.assertEqual(receipt["status"], "UNKNOWN")
        self.assertFalse(receipt["usage_drained"])

    def _simulate_late_child_drain(self, *, child_parent: str = "parent",
                                   child_at: float = 101.7,
                                   child_terminal: bool = True,
                                   child_usage: bool = True,
                                   child_ack: bool = True,
                                   deadline: float = 106.0) -> tuple[dict, list, list]:
        """Pilot-11 ordering: parent ack precedes child discovery by 1.7 seconds."""
        transport = object.__new__(AppServerTransport)
        transport.thread_id, transport.active_turn_id = "parent", "parent-turn"
        transport.active_turn_complete = False
        clock = {"now": 100.0}
        sent: list[tuple[float, str, str]] = []
        replies: list[dict] = []
        observations: list[tuple[str, float]] = []
        parent = {"id": "parent", "terminal": None, "calls": 1,
                  "last_total_usage": {}, "turns": [{"turn_id": "parent-turn",
                      "terminal": None, "calls": 1}]}
        child = {"id": "child", "parent_id": child_parent, "terminal": None,
                 "calls": 1 if child_usage else 0,
                 "last_total_usage": {} if child_usage else None,
                 "turns": [{"turn_id": "child-turn", "terminal": None,
                            "calls": 1 if child_usage else 0}]}

        class Meter:
            def __init__(self):
                self.paths = {Path("parent"): parent}
                self.unknown_models = set()
                self.unknown_usage = []

            def refresh(self):
                if clock["now"] >= child_at and Path("child") not in self.paths:
                    self.paths[Path("child")] = child
                    observations.append(("child-discovered", clock["now"]))

            def dispatch_coverage_issues(self):
                return []

        def send(method, params):
            self.assertEqual(method, "turn/interrupt")
            sent.append((clock["now"], params["threadId"], params["turnId"]))
            replies.append({"id": len(sent), "result": {}})
            return len(sent)

        def frame(**kwargs):
            self.assertLessEqual(kwargs["drain_deadline"], deadline)
            clock["now"] += min(kwargs["max_wait"], 0.1)
            if replies:
                reply = replies.pop(0)
                if reply["id"] == 1:
                    transport.active_turn_complete = True
                    parent["terminal"] = parent["turns"][-1]["terminal"] = "turn_aborted"
                    observations.append(("parent-ack", clock["now"]))
                elif child_terminal:
                    child["terminal"] = child["turns"][-1]["terminal"] = "turn_aborted"
                    observations.append(("child-ack", clock["now"]))
                if reply["id"] == 2 and not child_ack:
                    return {"id": 2, "error": {"message": "not acknowledged"}}
                return reply
            return {}

        def before(thread, turn):
            if thread == "parent":
                return None
            now = time.time_ns()
            return {"target_thread_id": thread, "target_turn_id": turn,
                    "marker_observed_ns": now,
                    "completion_absent_checked_ns": time.time_ns()}

        transport._send, transport._frame = send, frame
        with mock_patch("evals.long_horizon_v1.transport.time.monotonic",
                        side_effect=lambda: clock["now"]):
            receipt = transport.cancel_and_drain(Meter(), timeout=15,
                absolute_deadline=deadline, descendants_first=True,
                before_interrupt=before)
        observations.append(("app-server-close-eligible", clock["now"]))
        return receipt, sent, observations

    def test_late_child_is_interrupted_once_before_app_server_close(self) -> None:
        receipt, sent, events = self._simulate_late_child_drain()
        self.assertEqual(receipt["status"], "verified-drained", receipt)
        self.assertEqual([(thread, turn) for _, thread, turn in sent],
                         [("parent", "parent-turn"), ("child", "child-turn")])
        self.assertEqual(receipt["interrupted_threads"], ["parent", "child"])
        self.assertEqual([row["acknowledged"] for row in receipt["interrupt_requests"]],
                         [True, True])
        self.assertTrue(all(receipt[key] for key in ("interrupt_ack", "turn_completed",
            "descendants_drained", "usage_drained")))
        stamps = dict(events)
        self.assertLess(stamps["parent-ack"], stamps["child-discovered"])
        self.assertGreaterEqual(stamps["child-discovered"], 101.7)
        self.assertLess(sent[1][0], stamps["app-server-close-eligible"])
        self.assertGreaterEqual(stamps["app-server-close-eligible"] -
                                stamps["child-ack"], 2.0)

    def test_late_child_missing_ack_terminal_or_usage_remains_unknown(self) -> None:
        for change in ("ack", "terminal", "usage"):
            with self.subTest(change=change):
                receipt, sent, _ = self._simulate_late_child_drain(
                    child_terminal=change != "terminal", child_usage=change != "usage",
                    child_ack=change != "ack")
                self.assertEqual(receipt["status"], "UNKNOWN")
                self.assertEqual([thread for _, thread, _ in sent], ["parent", "child"])
                gate = {"ack": "interrupt_ack", "terminal": "descendants_drained",
                        "usage": "usage_drained"}[change]
                self.assertFalse(receipt[gate])
                if change == "ack":
                    self.assertEqual([row["acknowledged"] for row in
                                      receipt["interrupt_requests"]], [True, False])

    def test_wrong_or_after_deadline_child_is_never_targeted(self) -> None:
        wrong, wrong_sends, _ = self._simulate_late_child_drain(child_parent="other")
        self.assertEqual(wrong["status"], "UNKNOWN")
        self.assertEqual([thread for _, thread, _ in wrong_sends], ["parent"])
        self.assertIn("lineage", wrong["reason"])
        late, late_sends, _ = self._simulate_late_child_drain(deadline=101.5)
        self.assertEqual(late["status"], "UNKNOWN")
        self.assertEqual([thread for _, thread, _ in late_sends], ["parent"])

    def test_parent_only_drain_respects_quiet_period(self) -> None:
        receipt, sent, events = self._simulate_late_child_drain(child_at=float("inf"),
                                                                 deadline=104.0)
        self.assertEqual(receipt["status"], "verified-drained")
        self.assertEqual([(thread, turn) for _, thread, turn in sent],
                         [("parent", "parent-turn")])
        stamps = dict(events)
        self.assertGreaterEqual(stamps["app-server-close-eligible"] -
                                stamps["parent-ack"], 2.0)

    def test_zero_model_app_server_frame_loopback_drains_late_child(self) -> None:
        transport = object.__new__(AppServerTransport)
        transport.thread_id, transport.active_turn_id = "parent", "parent-turn"
        transport.active_turn_complete = False
        transport.deadline = time.monotonic() + 6
        transport.messages = queue.Queue()
        transport.events = []
        transport.process = type("Process", (), {"poll": lambda self: None})()
        parent = {"id": "parent", "terminal": None, "calls": 1,
                  "last_total_usage": {}, "turns": [{"turn_id": "parent-turn",
                      "terminal": None, "calls": 1}]}
        child = {"id": "child", "parent_id": "parent", "terminal": None,
                 "calls": 1, "last_total_usage": {},
                 "turns": [{"turn_id": "child-turn", "terminal": None, "calls": 1}]}
        started = time.monotonic()
        sent = []
        child_sent_at = []

        class Meter:
            def __init__(self):
                self.paths = {Path("parent"): parent}
                self.unknown_models = set()
                self.unknown_usage = []

            def refresh(self):
                if time.monotonic() - started >= 1.7:
                    self.paths[Path("child")] = child
                if child_sent_at and time.monotonic() - child_sent_at[0] >= 0.1:
                    child["terminal"] = child["turns"][-1]["terminal"] = "turn_aborted"

            def dispatch_coverage_issues(self):
                return []

        def send(method, params):
            self.assertEqual(method, "turn/interrupt")
            sent.append((time.monotonic() - started, params["threadId"], params["turnId"]))
            request_id = len(sent)
            transport.messages.put((json.dumps({"id": request_id,
                "result": {}}) + "\n").encode())
            if request_id == 1:
                parent["terminal"] = parent["turns"][-1]["terminal"] = "turn_aborted"
                transport.messages.put((json.dumps({"method": "turn/completed",
                    "params": {"threadId": "parent", "turn": {"id": "parent-turn",
                    "status": "interrupted"}}}) + "\n").encode())
            else:
                child_sent_at.append(time.monotonic())
            return request_id

        transport._send = send
        receipt = transport.cancel_and_drain(Meter(), timeout=5,
                                              descendants_first=True)
        self.assertEqual(receipt["status"], "verified-drained", receipt)
        self.assertEqual([(thread, turn) for _, thread, turn in sent],
                         [("parent", "parent-turn"), ("child", "child-turn")])
        self.assertGreaterEqual(sent[1][0], 1.7)
        self.assertLess(sent[1][0], 5)
        self.assertIn("turn/completed", [event["method"] for event in transport.events])

    def test_collector_independently_requires_closed_child_ledger(self) -> None:
        root, parent_id, child_id, parent, child = self._observed_multiturn_rollouts()
        self._write_multiturn(root, parent, child)
        plans = self._fork_plan(root, parent)
        observed = account(root, parent_id, "gpt-6-sol", "low", True)
        self.assertEqual(observed["status"], "complete")
        run = {"arm": "treatment", "task_id": manifest()["task_id"],
               "manifest_sha256": file_sha(ASSET_ROOT.parent / "manifest.json"),
               "parent_thread_id": parent_id,
               "stage": {"thread_id": parent_id, "events": [
                   {"seq": 0, "time_ns": 1, "round": 0, "kind": "submit",
                    "thread_id": parent_id}]},
               "attempts": [{"thread_id": parent_id, "turn_id": "parent-turn-1",
                             "round": 0, "stage_event_start": 0, "stage_event_end": 1}],
               "usage": observed["summary"],
               "per_turn_responses": observed["responses"],
               "native_attempts": observed["native_attempts"],
               "parent_rollout_path": str(root / "rollout-parent.jsonl"),
               "dispatch_plans_path": str(plans),
               "packet_scope_mode": "diagnostic-feasibility",
               "claim_class": "non-matched-non-interleaved-diagnostic",
               "child_rollout_paths": {child_id: str(root / "rollout-child.jsonl")},
               "fork_policy": {"packet_scope": "UNKNOWN"},
               "cost_status": "complete", "usage_issues": []}
        path = self.workspace / "collector-multiturn.json"
        path.write_text(json.dumps(run), encoding="utf-8")
        self.assertEqual(_arm(path, "treatment")["cost_status"], "complete")
        invalid_parent = json.loads(json.dumps(parent))
        spawn = next(item for item in invalid_parent if item["type"] == "response_item"
                     and item["payload"].get("name") == "spawn_agent")
        invalid_args = json.loads(spawn["payload"]["arguments"])
        invalid_args["fork_turns"] = "all"
        spawn["payload"]["arguments"] = json.dumps(invalid_args)
        self._write_multiturn(root, invalid_parent, child)
        with self.assertRaisesRegex(ValueError, "context-policy-failure"):
            _arm(path, "treatment")
        self._write_multiturn(root, parent, child)
        for change in ("ledger-terminal", "missing-turn-response", "duplicate-response"):
            with self.subTest(change=change):
                damaged = json.loads(json.dumps(run))
                if change == "ledger-terminal":
                    damaged["native_attempts"][0]["turns"][1].pop("terminal_line")
                elif change == "missing-turn-response":
                    damaged["per_turn_responses"] = [item for item in
                        damaged["per_turn_responses"] if not (
                            item["session_id"] == child_id and
                            item["turn_id"] == "child-turn-2")]
                    damaged["usage"]["model_calls"] = len(damaged["per_turn_responses"])
                else:
                    damaged["per_turn_responses"][-1]["response_id_sha256"] = (
                        damaged["per_turn_responses"][-2]["response_id_sha256"])
                path.write_text(json.dumps(damaged), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "closed child|response"):
                    _arm(path, "treatment")

    def test_interrupted_child_cost_requires_exact_drain_proof(self) -> None:
        fixture = json.loads((Path(__file__).parent / "fixtures" /
                              "native-abort-observed-sanitized.json").read_text(
                                  encoding="utf-8"))
        parent_id, child_id = fixture["parent_id"], fixture["child_id"]
        parent, child = fixture["native"], fixture["child"]
        parent[1]["payload"].update(model="gpt-6-sol", effort="low")
        first = {"input_tokens": 100, "cached_input_tokens": 0,
                 "cache_write_input_tokens": 0, "output_tokens": 10,
                 "reasoning_output_tokens": 0}
        second = {"input_tokens": 50, "cached_input_tokens": 0,
                  "cache_write_input_tokens": 0, "output_tokens": 5,
                  "reasoning_output_tokens": 0}
        child_usage = {"input_tokens": 25, "cached_input_tokens": 0,
                       "cache_write_input_tokens": 0, "output_tokens": 5,
                       "reasoning_output_tokens": 0}

        def priced(thread: str, turn: str, response: str, usage: dict,
                   total: dict) -> list:
            return [{"type": "token_usage_record", "payload": {
                "thread_id": thread, "session_id": parent_id,
                "turn_id": turn, "root_turn_id": "abort-parent-turn",
                "response_id": response, "usage": usage,
                "turn_token_usage": total, "thread_token_usage": total}},
                {"type": "event_msg", "payload": {"type": "token_count",
                    "info": {"last_token_usage": usage,
                             "total_token_usage": total}}}]

        cumulative = {key: first[key] + second[key] for key in first}
        parent = (parent[:-1] +
                  priced(parent_id, "abort-parent-turn", "parent-response-1",
                         first, first) +
                  priced(parent_id, "abort-parent-turn", "parent-response-2",
                         second, cumulative) + [parent[-1]])
        child = (child[:-1] +
                 priced(child_id, "abort-child-turn", "child-response-1",
                        child_usage, child_usage) + [child[-1]])
        root = self.workspace / "aborted-sessions"
        root.mkdir()
        self._write_multiturn(root, parent, child)
        plans = self._fork_plan(root, parent)
        meter = CanarySessionMeter(root, parent_id, "gpt-6-sol", "low", True)
        meter.refresh()
        proof = fixture["interruption_proof"]
        proof["usage"] = meter.summary()
        complete = account(root, parent_id, "gpt-6-sol", "low", True, proof)
        self.assertEqual(complete["status"], "complete", complete["issues"])
        self.assertTrue(complete["interruption_verified"])
        self.assertEqual(complete["summary"]["model_calls"], 3)
        self.assertEqual(complete["native_attempts"][0]["turns"][0]["closure"],
                         "interrupted")
        self.assertEqual(account(root, parent_id, "gpt-6-sol", "low", True)["status"],
                         "UNKNOWN")
        still_unknown = account(root, parent_id, "gpt-6-sol", "low", True,
                                fork_policy_plans=plans)
        self.assertEqual(still_unknown["status"], "UNKNOWN")
        self.assertFalse(any("context-policy-failure" in issue
                             for issue in still_unknown["issues"]))
        damaged = json.loads(json.dumps(proof))
        damaged["cancellation"]["interrupt_requests"].reverse()
        self.assertEqual(account(root, parent_id, "gpt-6-sol", "low", True,
                                 damaged)["status"], "UNKNOWN")
        damaged = json.loads(json.dumps(proof))
        damaged["usage"]["estimated_usd_lower_bound"] += 0.01
        self.assertEqual(account(root, parent_id, "gpt-6-sol", "low", True,
                                 damaged)["status"], "UNKNOWN")
        for change in ("wrong-cancellation-turn", "late-marker",
                       "negative-completion-check"):
            with self.subTest(accounting_proof=change):
                damaged = json.loads(json.dumps(proof))
                cancellation = damaged["cancellation"]
                if change == "wrong-cancellation-turn":
                    cancellation["turn_id"] = "wrong-turn"
                elif change == "late-marker":
                    request = cancellation["interrupt_requests"][0]
                    request["pre_dispatch_evidence"]["marker_observed_ns"] = (
                        request["dispatch_time_ns"] + 1)
                else:
                    cancellation["interrupt_requests"][0]["pre_dispatch_evidence"][
                        "completion_absent_checked_ns"] = -1
                self.assertEqual(account(root, parent_id, "gpt-6-sol", "low",
                                         True, damaged)["status"], "UNKNOWN")

        run = {"arm": "treatment", "task_id": manifest()["task_id"],
               "manifest_sha256": file_sha(ASSET_ROOT.parent / "manifest.json"),
               "parent_thread_id": parent_id, "stop_reason": "interrupted",
               "stage": {"thread_id": parent_id, "events": []}, "attempts": [],
               "usage": complete["summary"],
               "per_turn_responses": complete["responses"],
               "native_attempts": complete["native_attempts"],
               "parent_rollout_path": str(root / "rollout-parent.jsonl"),
               "dispatch_plans_path": str(plans),
               "packet_scope_mode": "diagnostic-feasibility",
               "claim_class": "non-matched-non-interleaved-diagnostic",
               "child_rollout_paths": {child_id: str(root / "rollout-child.jsonl")},
               "fork_policy": {"packet_scope": "UNKNOWN"},
               "cancellation": proof["cancellation"],
               "interruption_proof": proof,
               "cost_status": "complete", "usage_issues": []}
        path = self.workspace / "interrupted-run.json"
        path.write_text(json.dumps(run), encoding="utf-8")
        self.assertEqual(_arm(path, "treatment")["cost_status"], "complete")
        for change in ("missing-proof", "wrong-order", "wrong-parent-terminal",
                       "wrong-root-turn", "early-child-terminal",
                       "early-parent-terminal", "accepted-final",
                       "wrong-cancellation-turn", "late-marker",
                       "negative-completion-check"):
            with self.subTest(change=change):
                changed = json.loads(json.dumps(run))
                if change == "missing-proof":
                    changed["interruption_proof"] = None
                elif change == "wrong-order":
                    changed["interruption_proof"]["cancellation"][
                        "interrupt_requests"].reverse()
                elif change == "wrong-parent-terminal":
                    next(row for row in changed["usage"]["sessions"]
                         if row["id"] == parent_id)["turns"][0][
                        "terminal"] = "task_complete"
                elif change == "early-child-terminal":
                    changed["native_attempts"][0]["turns"][0][
                        "terminal_timestamp"] = "2030-01-01T00:00:00.000000Z"
                elif change == "early-parent-terminal":
                    next(row for row in changed["usage"]["sessions"]
                         if row["id"] == parent_id)["turns"][0][
                        "terminal_timestamp"] = "2030-01-01T00:00:00.000000Z"
                elif change == "accepted-final":
                    changed["stop_reason"] = "accepted-final"
                elif change == "wrong-cancellation-turn":
                    changed["interruption_proof"]["cancellation"]["turn_id"] = "wrong-turn"
                elif change == "late-marker":
                    request = changed["interruption_proof"]["cancellation"][
                        "interrupt_requests"][0]
                    request["pre_dispatch_evidence"]["marker_observed_ns"] = (
                        request["dispatch_time_ns"] + 1)
                elif change == "negative-completion-check":
                    changed["interruption_proof"]["cancellation"][
                        "interrupt_requests"][0]["pre_dispatch_evidence"][
                            "completion_absent_checked_ns"] = -1
                else:
                    next(row for row in changed["per_turn_responses"]
                         if row["session_id"] == child_id)["root_turn_id"] = "wrong"
                if change in ("wrong-cancellation-turn", "late-marker",
                              "negative-completion-check"):
                    changed["cancellation"] = json.loads(json.dumps(
                        changed["interruption_proof"]["cancellation"]))
                path.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaises(ValueError):
                    _arm(path, "treatment")

    def test_retained_canary_interrupted_cost_replay_when_available(self) -> None:
        receipt = (ASSET_ROOT.parent / "_scratch" / "cancellation-canary" /
                   "retry-20260930120001-96e403dd" / "canary" / "cancellation.json")
        sessions = Path.home() / ".codex" / "sessions" / "2026" / "09" / "30"
        if not receipt.is_file():
            self.skipTest("retained canary receipt unavailable")
        proof = json.loads(receipt.read_text(encoding="utf-8"))
        parent_id = proof["parent_thread_id"]
        if not list(sessions.glob(f"rollout-*{parent_id}.jsonl")):
            self.skipTest("retained canary rollouts unavailable")
        observed = account(sessions, parent_id, "gpt-6-sol", "low", True, proof)
        self.assertEqual(observed["status"], "complete", observed["issues"])
        self.assertEqual(observed["summary"]["model_calls"], 10)
        self.assertEqual(observed["summary"]["estimated_usd"], 0.3768448)
        self.assertEqual(account(sessions, parent_id, "gpt-6-sol", "low", True)[
            "status"], "UNKNOWN")

    def test_collector_rejects_missing_live_lineage(self) -> None:
        path = self.workspace / "run.json"
        run = {"arm": "baseline", "task_id": manifest()["task_id"],
               "manifest_sha256": file_sha(ASSET_ROOT.parent / "manifest.json"),
               "parent_thread_id": "parent", "stage": {"thread_id": "parent",
                    "events": [{"seq": 0, "time_ns": 1, "round": 0,
                                "kind": "submit", "thread_id": "parent"}]},
               "usage": {"sessions": [{"id": "parent", "parent_id": None,
                                        "turns": [{"turn_id": "turn-1"}]}],
                         "model_calls": 1},
               "per_turn_responses": [{"session_id": "parent", "turn_id": "turn-1"}],
               "attempts": [{"thread_id": "parent", "turn_id": "turn-1",
                             "round": 0, "stage_event_start": 0, "stage_event_end": 1}],
               "native_attempts": [],
               "cost_status": "complete", "usage_issues": []}
        path.write_text(json.dumps(run), encoding="utf-8")
        self.assertEqual(_arm(path, "baseline")["parent_thread_id"], "parent")
        for change in ({"parent_thread_id": None},
                       {"stage": {"thread_id": "different"}},
                       {"per_turn_responses": [{"session_id": "unknown",
                                                "turn_id": "turn-1"}]},
                       {"per_turn_responses": [{"session_id": "parent",
                                                "turn_id": "wrong-turn"}]},
                       {"usage": {"sessions": [{"id": "parent", "parent_id": None,
                                                "turns": [{"turn_id": "turn-1"}]},
                                                 {"id": "child", "parent_id": None,
                                                  "turns": [{"turn_id": "child-turn"}]}],
                                  "model_calls": 1}},
                       {"attempts": []},
                       {"attempts": [{"thread_id": "parent", "turn_id": "turn-1",
                                      "round": 0, "stage_event_start": 0,
                                      "stage_event_end": 0}]},
                       {"native_attempts": [{"turn_id": "turn-1",
                                             "child_id": "missing-child"}]}):
            with self.subTest(change=change):
                path.write_text(json.dumps(run | change), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "identity|lineage|attempt|span|child"):
                    _arm(path, "baseline")

    def test_collector_independently_rejects_validly_linked_baseline_child(self) -> None:
        path = self.workspace / "baseline-child.json"
        run = {"arm": "baseline", "task_id": manifest()["task_id"],
            "manifest_sha256": file_sha(ASSET_ROOT.parent / "manifest.json"),
            "parent_thread_id": "parent", "stage": {"thread_id": "parent"},
            "usage": {"sessions": [
                {"id": "parent", "parent_id": None,
                 "turns": [{"turn_id": "parent-turn"}]},
                {"id": "child", "parent_id": "parent",
                 "turns": [{"turn_id": "child-turn"}]}]},
            "per_turn_responses": [], "native_attempts": [], "cost_status": "UNKNOWN"}
        path.write_text(json.dumps(run), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "baseline child session forbidden"):
            _arm(path, "baseline")

    def test_active_telemetry_grace_and_budget_usage_integrity(self) -> None:
        class Meter:
            def __init__(self):
                self.paths = {}
                self.calls = 0
                self.unknown_models = set()
                self.unknown_usage = []
                self.coverage = []
            def refresh(self):
                pass
            def dispatch_coverage_issues(self):
                return self.coverage
        meter = Meter()
        guard = ActiveTelemetryGuard("treatment")
        guard.check(meter, "parent", "gpt-6-sol", "low", 100, 0, now=119)
        with self.assertRaisesRegex(TransportError, "parent rollout missing"):
            guard.check(meter, "parent", "gpt-6-sol", "low", 100, 0, now=120)
        meter.paths = {self.workspace / "parent": {"id": "parent",
            "turns": [{"turn_id": "old-turn", "model": "gpt-6-sol", "effort": "low"}],
            "model": "gpt-6-sol", "effort": "low"}}
        with self.assertRaisesRegex(TransportError, "parent selector missing"):
            guard.check(meter, "parent", "gpt-6-sol", "low", 100, 0,
                        now=120, turn_id="new-turn")
        guard.check(meter, "parent", "gpt-6-sol", "low", 100, 0, now=129)
        with self.assertRaisesRegex(TransportError, "usage missing"):
            guard.check(meter, "parent", "gpt-6-sol", "low", 100, 0, now=130)
        meter.calls = 1
        meter.coverage = ["child session missing"]
        guard.check(meter, "parent", "gpt-6-sol", "low", 100, 0, now=131)
        with self.assertRaisesRegex(TransportError, "lineage unresolved"):
            guard.check(meter, "parent", "gpt-6-sol", "low", 100, 0, now=141)
        meter.coverage = []
        meter.unknown_usage = ["unpriced"]
        with self.assertRaisesRegex(TransportError, "unpriced"):
            guard.check(meter, "parent", "gpt-6-sol", "low", 100, 0, now=142)
        meter.unknown_usage = []
        meter.paths[self.workspace / "child"] = {"id": "child", "calls": 0,
            "turns": [{"turn_id": "child-turn", "model": "gpt-6-astra",
                       "effort": "xhigh"}], "model": "gpt-6-astra", "effort": "xhigh"}
        guard.check(meter, "parent", "gpt-6-sol", "low", 100, 0, now=143)
        with self.assertRaisesRegex(TransportError, "active child .* usage missing"):
            guard.check(meter, "parent", "gpt-6-sol", "low", 100, 0, now=173)

    def test_meter_refresh_gate_burst_silence_and_child_event(self) -> None:
        class Meter:
            def __init__(self):
                self.refreshes = 0
            def refresh(self):
                self.refreshes += 1
        meter = Meter()
        gate = MeterRefreshGate(meter, interval=1.0)
        self.assertTrue(gate.refresh(now=100.0))
        for _ in range(8000):
            self.assertFalse(gate.refresh(now=100.1))
        self.assertEqual(meter.refreshes, 1)
        self.assertFalse(meter_relevant_frame({"method":
            "item/commandExecution/outputDelta"}))
        self.assertTrue(meter_relevant_frame({"method":
            "thread/tokenUsage/updated"}))
        self.assertTrue(meter_relevant_frame({"method": "item/started",
            "params": {"item": {"type": "collabToolCall",
                                "agentThreadId": "child"}}}))
        self.assertTrue(gate.refresh(force=True, now=100.1))
        self.assertFalse(gate.refresh(now=100.9))
        self.assertTrue(gate.refresh(now=101.1))
        self.assertEqual(meter.refreshes, 3)

    def test_active_telemetry_uses_one_refreshed_snapshot(self) -> None:
        class Meter:
            paths = {Path("parent"): {"id": "parent", "calls": 1,
                "turns": [{"turn_id": "turn-1", "model": "gpt-6-sol",
                           "effort": "low"}]}}
            calls = 1
            unknown_models = set()
            unknown_usage = []
            def refresh(self):
                raise AssertionError("duplicate refresh")
            def dispatch_coverage_issues(self):
                return []
        ActiveTelemetryGuard("treatment").check(Meter(), "parent",
            "gpt-6-sol", "low", 100, 0, now=101, turn_id="turn-1")

    def test_public_grade_uses_remaining_global_deadline(self) -> None:
        with mock_patch("evals.long_horizon_v1.run.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 0,
                json.dumps({"round": 1, "behavior_pass": True}), "")
            with mock_patch("evals.long_horizon_v1.run.time.monotonic",
                            side_effect=[100.0, 101.0]):
                result = grade_before_deadline(self.workspace / "candidate.patch",
                    self.workspace, self.workspace / "grade", 1, 150.0)
            self.assertTrue(result["behavior_pass"])
            self.assertEqual(run.call_args.kwargs["timeout"], 50.0)
            run.reset_mock()
            with mock_patch("evals.long_horizon_v1.run.time.monotonic", return_value=150.0):
                with self.assertRaisesRegex(TransportError, "before grade"):
                    grade_before_deadline(self.workspace / "candidate.patch",
                        self.workspace, self.workspace / "grade", 1, 150.0)
            run.assert_not_called()
            run.side_effect = subprocess.TimeoutExpired("grade", 3)
            with mock_patch("evals.long_horizon_v1.run.time.monotonic", return_value=100.0), \
                 mock_patch("evals.long_horizon_v1.run.stop_wsl_grader", return_value=True):
                with self.assertRaisesRegex(TransportError, "timed out"):
                    grade_before_deadline(self.workspace / "candidate.patch",
                        self.workspace, self.workspace / "grade", 1, 150.0)
            run.side_effect = None
            with mock_patch("evals.long_horizon_v1.run.time.monotonic",
                            side_effect=[100.0, 151.0]):
                with self.assertRaisesRegex(TransportError, "before accepting"):
                    grade_before_deadline(self.workspace / "candidate.patch",
                        self.workspace, self.workspace / "grade", 1, 150.0)

    def test_wsl_grader_process_group_cleanup_and_fail_closed_marker(self) -> None:
        output = self.workspace / "grade"
        marker = output / "go-test" / "wsl-pgid.txt"
        marker.parent.mkdir(parents=True)
        marker.write_text("4321\n", encoding="ascii")
        with mock_patch("evals.long_horizon_v1.grade.wsl_path",
                        return_value="/mnt/c/grade/go-test/wsl-pgid.txt"), \
             mock_patch("evals.long_horizon_v1.grade.subprocess.run",
                        return_value=subprocess.CompletedProcess([], 0, b"", b"")) as run:
            self.assertTrue(stop_wsl_grader(output))
            self.assertIn("kill -TERM -- -4321", run.call_args.args[0][-1])
        self.assertFalse(marker.exists())
        marker.write_text("4321\n", encoding="ascii")
        with mock_patch("evals.long_horizon_v1.grade.wsl_path",
                        return_value="/mnt/c/grade/go-test/wsl-pgid.txt"), \
             mock_patch("evals.long_horizon_v1.grade.subprocess.run",
                        return_value=subprocess.CompletedProcess([], 2, b"", b"")):
            self.assertFalse(stop_wsl_grader(output))
        self.assertTrue(marker.exists())
        with mock_patch("evals.long_horizon_v1.run.subprocess.run",
                        side_effect=subprocess.TimeoutExpired("grade", 3)), \
             mock_patch("evals.long_horizon_v1.run.stop_wsl_grader", return_value=False), \
             mock_patch("evals.long_horizon_v1.run.time.monotonic", return_value=100.0):
            with self.assertRaisesRegex(TransportError, "cleanup UNKNOWN"):
                grade_before_deadline(self.workspace / "candidate.patch", self.workspace,
                                      output, 1, 150.0)
        hazard = json.loads((self.workspace / "grader-hazard.json").read_text())
        self.assertEqual(hazard["status"], "UNKNOWN")
        with mock_patch("evals.long_horizon_v1.run.manifest",
                        return_value={"live_enabled": True}):
            with self.assertRaisesRegex(ValueError, "blocks both arms"):
                live_preflight(None, None, "treatment", self.workspace)

    def test_real_wsl_cleanup_rejects_unrelated_group_marker(self) -> None:
        try:
            ready = subprocess.run(["wsl", "-d", "Ubuntu", "--", "true"],
                                   capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            self.skipTest("Ubuntu WSL unavailable")
        if ready.returncode:
            self.skipTest("Ubuntu WSL unavailable")

        output = self.workspace / "grade"
        prefix = output / "go-test"
        prefix.mkdir(parents=True)
        script = prefix / "run-go.sh"
        marker = prefix / "wsl-pgid.txt"
        script.write_bytes(b"#!/usr/bin/env bash\nprintf '%s\\n' \"$$\" > \"$1\"\nsleep 12\n")
        script_path, marker_path = wsl_path(script), wsl_path(marker)
        process = subprocess.Popen(["wsl", "-d", "Ubuntu", "--", "setsid", "--wait",
                                    "bash", script_path, marker_path],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pgid = None
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and not marker.is_file():
                time.sleep(0.1)
            self.assertTrue(marker.is_file(), "WSL script did not write its group marker")
            pgid = int(marker.read_text(encoding="ascii").strip())
            leader = subprocess.run(["wsl", "-d", "Ubuntu", "--", "ps", "-ww",
                                     "-o", "args=", "-p", str(pgid)],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(leader.returncode, 0)
            self.assertIn(marker_path, leader.stdout)

            forged = self.workspace / "forged-unrelated" / "go-test" / "wsl-pgid.txt"
            forged.parent.mkdir(parents=True)
            forged.write_text(f"{pgid}\n", encoding="ascii")
            self.assertFalse(stop_wsl_grader(forged.parent.parent))
            self.assertTrue(forged.exists())
            self.assertIsNone(process.poll())
            alive = subprocess.run(["wsl", "-d", "Ubuntu", "--", "kill", "-0",
                                    "--", f"-{pgid}"], capture_output=True, timeout=5)
            self.assertEqual(alive.returncode, 0)

            self.assertTrue(stop_wsl_grader(output))
            self.assertFalse(marker.exists())
            process.wait(timeout=8)
            gone = subprocess.run(["wsl", "-d", "Ubuntu", "--", "kill", "-0",
                                   "--", f"-{pgid}"], capture_output=True, timeout=5)
            self.assertNotEqual(gone.returncode, 0)
        finally:
            if pgid is None and marker.is_file():
                try:
                    pgid = int(marker.read_text(encoding="ascii").strip())
                except (OSError, ValueError):
                    pass
            if pgid is not None and process.poll() is None:
                leader = subprocess.run(["wsl", "-d", "Ubuntu", "--", "ps", "-ww",
                                         "-o", "args=", "-p", str(pgid)],
                                        capture_output=True, text=True, timeout=5)
                if script_path in leader.stdout and marker_path in leader.stdout:
                    subprocess.run(["wsl", "-d", "Ubuntu", "--", "kill", "-KILL",
                                    "--", f"-{pgid}"], capture_output=True, timeout=5)
            process.wait(timeout=15)

    def test_wsl_go_status_is_required_and_cross_checked(self) -> None:
        output = self.workspace / "grade"
        prefix = output / "go-test"
        prefix.mkdir(parents=True)
        script = prefix / "run-go.sh"
        script.write_bytes(b"exit 0\n")
        status = prefix / "go-exit-status.txt"

        def run_case(wsl_code: int, go_status: bytes | None,
                     stdout: bytes) -> tuple[int, bytes, bytes]:
            status.unlink(missing_ok=True)
            def fake_run(*_args, **_kwargs):
                if go_status is not None:
                    status.write_bytes(go_status)
                return subprocess.CompletedProcess([], wsl_code, stdout, b"diagnostic")
            with mock_patch("evals.long_horizon_v1.grade.wsl_path",
                            side_effect=lambda path: "/mnt/c/" + path.name), \
                 mock_patch("evals.long_horizon_v1.grade.stop_wsl_grader",
                            return_value=True), \
                 mock_patch("evals.long_horizon_v1.grade.subprocess.run",
                            side_effect=fake_run) as run:
                result = run_wsl_go(script, status, output, prefix, self.workspace)
                self.assertEqual(run.call_args.args[0][4:],
                                 ["setsid", "--wait", "bash", "/mnt/c/run-go.sh",
                                  "/mnt/c/wsl-pgid.txt"])
                return result

        self.assertEqual(run_case(0, b"0\n", b"ok\n")[0], 0)
        self.assertEqual(run_case(1, b"1\n", b"ok\n")[0], 1)
        self.assertEqual((prefix / "stdout.txt").read_bytes(), b"ok\n")
        self.assertEqual((prefix / "stderr.txt").read_bytes(), b"diagnostic")
        with self.assertRaisesRegex(RuntimeError, "status missing"):
            run_case(0, None, b"ok\n")
        with self.assertRaisesRegex(RuntimeError, "status invalid"):
            run_case(0, b"0\n1\n", b"ok\n")
        with self.assertRaisesRegex(RuntimeError, "status conflict"):
            run_case(0, b"1\n", b"ok\n")
        with self.assertRaisesRegex(RuntimeError, "failure output"):
            run_case(0, b"0\n", b"--- FAIL: TestExample\nFAIL\n")
        self.assertEqual(checked_go_status(status, 0, b"log mentions FAIL but passed\n"), 0)

    def test_wsl_go_timeout_preserves_output_and_cleanup(self) -> None:
        output = self.workspace / "grade"
        prefix = output / "go-test"
        prefix.mkdir(parents=True)
        with mock_patch("evals.long_horizon_v1.grade.wsl_path",
                        side_effect=lambda path: "/mnt/c/" + path.name), \
             mock_patch("evals.long_horizon_v1.grade.stop_wsl_grader",
                        return_value=True) as stop, \
             mock_patch("evals.long_horizon_v1.grade.subprocess.run",
                        side_effect=subprocess.TimeoutExpired("go", 870,
                            output=b"partial", stderr=b"error")):
            self.assertEqual(run_wsl_go(prefix / "run-go.sh", prefix / "status.txt",
                                       output, prefix, self.workspace),
                             (124, b"partial", b"error"))
        stop.assert_called_once_with(output)
        self.assertEqual((prefix / "stdout.txt").read_bytes(), b"partial")
        self.assertEqual((prefix / "stderr.txt").read_bytes(), b"error")
        with mock_patch("evals.long_horizon_v1.grade.wsl_path",
                        side_effect=lambda path: "/mnt/c/" + path.name), \
             mock_patch("evals.long_horizon_v1.grade.stop_wsl_grader",
                        return_value=False), \
             mock_patch("evals.long_horizon_v1.grade.subprocess.run",
                        side_effect=subprocess.TimeoutExpired("go", 870)):
            with self.assertRaisesRegex(RuntimeError, "cleanup unconfirmed"):
                run_wsl_go(prefix / "run-go.sh", prefix / "status.txt",
                           output, prefix, self.workspace)
        hazard = json.loads((self.workspace / "grader-hazard.json").read_text())
        self.assertEqual(hazard["status"], "UNKNOWN")

    def test_transport_interrupt_requires_ack_completion_and_usage_drain(self) -> None:
        transport = object.__new__(AppServerTransport)
        transport.thread_id, transport.active_turn_id = "parent", "turn-1"
        transport.active_turn_complete = False
        transport._send = lambda *_args, **_kwargs: 7
        frames = [{"id": 7, "result": {}}, {}, {}, {}]
        def frame(**_kwargs):
            transport.active_turn_complete = True
            return frames.pop(0)
        transport._frame = frame
        class Meter:
            paths = {Path("parent"): {"id": "parent", "terminal": "task_cancelled",
                "calls": 1, "last_total_usage": {}},
                Path("child"): {"id": "child", "terminal": "task_cancelled",
                "calls": 1, "last_total_usage": {}}}
            calls = 2
            unknown_models = set()
            unknown_usage = []
            def refresh(self):
                pass
            def dispatch_coverage_issues(self):
                return []
        with mock_patch("evals.long_horizon_v1.transport.DRAIN_QUIET_SECONDS", 0), \
             mock_patch("evals.long_horizon_v1.transport.DRAIN_REFRESH_SECONDS", 0):
            self.assertEqual(transport.cancel_and_drain(Meter(), timeout=1)["status"],
                             "verified-drained")
        transport.active_turn_complete = False
        transport.active_turn_id = None
        self.assertEqual(transport.cancel_and_drain(Meter(), timeout=1)["status"],
                         "UNKNOWN")
        transport.active_turn_complete = True
        child = {"id": "child", "terminal": None, "calls": 1,
                 "last_total_usage": {}, "turns": [{"turn_id": "child-turn"}]}
        class ActiveChild(Meter):
            paths = {Path("parent"): Meter.paths[Path("parent")],
                     Path("child"): child}
        targets = []
        transport._send = lambda method, params: targets.append(params) or 8
        frames = [{"id": 8, "result": {}}, {}, {}, {}]
        def child_frame(**_kwargs):
            child["terminal"] = "task_cancelled"
            return frames.pop(0)
        transport._frame = child_frame
        with mock_patch("evals.long_horizon_v1.transport.DRAIN_QUIET_SECONDS", 0), \
             mock_patch("evals.long_horizon_v1.transport.DRAIN_REFRESH_SECONDS", 0):
            self.assertEqual(transport.cancel_and_drain(ActiveChild(), timeout=1)["status"],
                             "verified-drained")
        self.assertEqual(targets, [{"threadId": "child", "turnId": "child-turn"}])

    def test_cancel_drain_output_burst_does_not_rescan_per_frame(self) -> None:
        transport = object.__new__(AppServerTransport)
        transport.thread_id, transport.active_turn_id = "parent", "turn-1"
        transport.active_turn_complete = False
        transport._send = lambda *_args, **_kwargs: 7
        frames = ([{"method": "item/commandExecution/outputDelta"}] * 3000 +
                  [{"id": 7, "result": {}},
                   {"method": "turn/completed", "params": {"threadId": "parent",
                       "turn": {"id": "turn-1"}}}, {}, {}, {}])
        def frame(**_kwargs):
            value = frames.pop(0)
            if value.get("method") == "turn/completed":
                transport.active_turn_complete = True
            return value
        transport._frame = frame
        class Meter:
            paths = {Path("parent"): {"id": "parent", "terminal": "task_cancelled",
                "calls": 1, "last_total_usage": {}}}
            calls = 1
            unknown_models = set()
            unknown_usage = []
            def __init__(self):
                self.refreshes = 0
            def refresh(self):
                self.refreshes += 1
            def dispatch_coverage_issues(self):
                return []
        meter = Meter()
        with mock_patch("evals.long_horizon_v1.transport.DRAIN_QUIET_SECONDS", 0):
            receipt = transport.cancel_and_drain(meter, timeout=2)
        self.assertEqual(receipt["status"], "verified-drained")
        self.assertLessEqual(meter.refreshes, 6)
        self.assertEqual(receipt["interrupted_threads"], ["parent"])

    def test_turn_aborted_is_terminal_but_missing_usage_is_unknown(self) -> None:
        path = self.workspace / "rollout-child.jsonl"
        rows = [
            {"type": "session_meta", "payload": {"id": "child", "parent_thread_id": "parent"}},
            {"type": "turn_context", "payload": {"turn_id": "child-turn",
                "model": "gpt-6-astra", "effort": "xhigh"}},
            {"type": "event_msg", "payload": {"type": "turn_aborted",
                "turn_id": "child-turn", "reason": "interrupted"}},
        ]
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        meter = CanarySessionMeter(self.workspace, "parent", "gpt-6-sol", "low")
        meter.known_ids.add("child")
        meter.refresh()
        self.assertEqual(meter.paths[path]["terminal"], "turn_aborted")
        self.assertEqual(meter.paths[path]["calls"], 0)
        meter.paths[self.workspace / "parent.jsonl"] = {"id": "parent",
            "terminal": "turn_aborted", "calls": 1, "last_total_usage": {},
            "turns": [{"turn_id": "parent-turn"}]}
        meter.calls = 1
        meter.dispatch_coverage_issues = lambda: []
        transport = object.__new__(AppServerTransport)
        transport.thread_id, transport.active_turn_id = "parent", "parent-turn"
        transport.active_turn_complete = False
        transport._send = lambda *_args, **_kwargs: 1
        def frame(**_kwargs):
            transport.active_turn_complete = True
            return {"id": 1, "result": {}}
        transport._frame = frame
        receipt = transport.cancel_and_drain(meter, timeout=0.01)
        self.assertEqual(receipt["status"], "UNKNOWN")
        self.assertFalse(receipt["usage_drained"])

    def test_canary_target_requires_completed_readiness_priced_response_and_live_second_command(self) -> None:
        attempt_id = "test-attempt"
        path = self.workspace / "child.jsonl"
        state = {"id": "child", "parent_id": "parent", "agent_path": "/root/canary",
            "model": "gpt-6-astra", "effort": "xhigh", "terminal": None,
            "turns": [{"turn_id": "child-turn", "model": "gpt-6-astra", "effort": "xhigh"}],
            "calls": 1, "last_total_usage": {"input_tokens": 1}}
        class Meter:
            paths = {path: state, self.workspace / "parent.jsonl": {"id": "parent"}}
        first = {"type": "response_item", "payload": {"type": "custom_tool_call",
            "name": "exec", "call_id": "first", "input": READY_MARKER + attempt_id}}
        done = {"type": "response_item", "payload": {"type": "custom_tool_call_output",
            "call_id": "first", "output": READY_MARKER + attempt_id}}
        priced = {"type": "event_msg", "payload": {"type": "token_count",
            "info": {"last_token_usage": {"input_tokens": 1}}}}
        second = {"type": "response_item", "payload": {"type": "custom_tool_call",
            "name": "exec", "call_id": "second", "input": RUNNING_MARKER + attempt_id}}
        def write(*rows):
            path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        lineage = [{"child_id": "child", "turn_id": "parent-turn",
            "model": "gpt-6-astra", "effort": "xhigh"}]
        with mock_patch("evals.long_horizon_v1.cancellation_canary.native_lineage",
                        return_value=(lineage, [])):
            write(first)
            self.assertFalse(cancellation_target_ready(Meter(), "parent", "parent-turn",
                                                       "treatment", self.workspace, attempt_id))
            write(first, done, second)
            with self.assertRaisesRegex(TransportError, "preceded priced readiness"):
                cancellation_target_ready(Meter(), "parent", "parent-turn",
                                          "treatment", self.workspace, attempt_id)
            write(first, done, priced, second)
            marker_path = self.workspace / (RUNNING_FILE + attempt_id)
            marker_path.write_text(RUNNING_MARKER + attempt_id, encoding="utf-8")
            self.assertTrue(cancellation_target_ready(Meter(), "parent", "parent-turn",
                                                      "treatment", self.workspace, attempt_id))
            state["terminal"] = "task_complete"
            with self.assertRaisesRegex(TransportError, "finished before interruption"):
                cancellation_target_ready(Meter(), "parent", "parent-turn",
                                          "treatment", self.workspace, attempt_id)
            state["terminal"] = None
            second_done = {"type": "response_item", "payload": {
                "type": "custom_tool_call_output", "call_id": "second", "output": "done"}}
            write(first, done, priced, second, second_done)
            with self.assertRaisesRegex(TransportError, "completed before interruption"):
                cancellation_target_ready(Meter(), "parent", "parent-turn",
                                          "treatment", self.workspace, attempt_id)

    def test_canary_completion_races_send_no_interrupt(self) -> None:
        attempt_id = "race-attempt"
        path = self.workspace / "parent.jsonl"
        marker_path = self.workspace / (RUNNING_FILE + attempt_id)
        marker_path.write_text(RUNNING_MARKER + attempt_id, encoding="utf-8")
        state = {"id": "parent", "model": "gpt-6-astra", "effort": "xhigh",
            "terminal": None, "turns": [{"turn_id": "turn-1",
                "model": "gpt-6-astra", "effort": "xhigh"}],
            "calls": 1, "last_total_usage": {"input_tokens": 1}}
        class Meter:
            paths = {path: state}
            parent_model, parent_effort = "gpt-6-astra", "xhigh"
            calls = 1
            unknown_models = set()
            unknown_usage = []
            def refresh(self):
                pass
            def dispatch_coverage_issues(self):
                return []
        rows = [
            {"type": "response_item", "payload": {"type": "custom_tool_call",
                "name": "exec", "call_id": "first", "input": READY_MARKER + attempt_id}},
            {"type": "response_item", "payload": {"type": "custom_tool_call_output",
                "call_id": "first", "output": READY_MARKER + attempt_id}},
            {"type": "event_msg", "payload": {"type": "token_count",
                "info": {"last_token_usage": {"input_tokens": 1}}}},
            {"type": "response_item", "payload": {"type": "custom_tool_call",
                "name": "exec", "call_id": "second", "input": RUNNING_MARKER + attempt_id}},
        ]
        completed = {"type": "response_item", "payload": {
            "type": "custom_tool_call_output", "call_id": "second", "output": "done"}}
        def write(*events):
            path.write_text("".join(json.dumps(row) + "\n" for row in events), encoding="utf-8")
        write(*rows)
        original_read = Path.read_text
        def completing_read(source, *args, **kwargs):
            value = original_read(source, *args, **kwargs)
            if source == marker_path:
                write(*rows, completed)
            return value
        with mock_patch.object(Path, "read_text", completing_read):
            with self.assertRaisesRegex(TransportError, "completed before interruption"):
                cancellation_target_ready(Meter(), "parent", "turn-1", "baseline",
                                          self.workspace, attempt_id)

        transport = object.__new__(AppServerTransport)
        transport.thread_id, transport.active_turn_id = "parent", "turn-1"
        transport.active_turn_complete = False
        sends = []
        transport._send = lambda method, params: sends.append((method, params)) or 1
        transport._frame = lambda **_kwargs: {"id": 1, "result": {}}
        write(*rows)
        def late_completion(thread, turn):
            write(*rows, completed)
            return cancellation_target_ready(Meter(), thread, turn, "baseline",
                                             self.workspace, attempt_id)
        receipt = transport.cancel_and_drain(Meter(), timeout=0.02,
                                             before_interrupt=late_completion)
        self.assertEqual(receipt["status"], "UNKNOWN")
        self.assertEqual(sends, [])

        write(*rows)
        def running_target(thread, turn):
            return cancellation_target_ready(Meter(), thread, turn, "baseline",
                                             self.workspace, attempt_id)
        def frame(**_kwargs):
            transport.active_turn_complete = True
            state["terminal"] = "turn_aborted"
            return {"id": 1, "result": {}}
        transport._frame = frame
        receipt = transport.cancel_and_drain(Meter(), timeout=0.02,
                                             before_interrupt=running_target)
        self.assertEqual(sends, [("turn/interrupt", {"threadId": "parent", "turnId": "turn-1"})])
        self.assertEqual(receipt["interrupt_requests"][0]["pre_dispatch_evidence"]
                         ["second_call_id"], "second")

        # Treatment checks the child before any parent interrupt can complete it.
        transport.active_turn_complete = False
        child = {"id": "child", "terminal": None,
                 "turns": [{"turn_id": "child-turn"}]}
        class TreatmentMeter(Meter):
            paths = {path: state, self.workspace / "child.jsonl": child}
        state["terminal"] = None
        sends.clear()
        checked = []
        def child_completed_before_dispatch(thread, turn):
            checked.append((thread, turn))
            raise CompletedTargetError("child completed before dispatch")
        receipt = transport.cancel_and_drain(TreatmentMeter(), timeout=0.02,
            before_interrupt=child_completed_before_dispatch, descendants_first=True)
        self.assertEqual(checked, [("child", "child-turn")])
        self.assertEqual(sends, [])
        self.assertEqual(receipt["status"], "UNKNOWN")

    def test_canary_completed_target_skips_cleanup_interrupt(self) -> None:
        (self.workspace / "baseline").mkdir()
        sends = []
        class Transport:
            active_turn_complete = False
            def __init__(self, *_args, **_kwargs):
                pass
            def start(self):
                return "parent"
            def _guard_turn(self, callback):
                callback()
            def _frame(self):
                return {}
            def cancel_and_drain(self, *_args, **_kwargs):
                sends.append("turn/interrupt")
                return {"status": "UNKNOWN"}
            def close(self):
                pass
        class Meter:
            def __init__(self, *_args, **_kwargs):
                self.paths = {}
                self.cost_upper = 0.0
                self.unknown_models = set()
                self.unknown_usage = []
            def refresh(self):
                pass
            def summary(self):
                return {"sessions": [], "estimated_usd_upper_bound": 0.0}
        spec = {"runtime": {"cli": str(self.workspace / "cli")}, "live_enabled": False}
        output = self.workspace / "canary-completed-target"
        with mock_patch("evals.long_horizon_v1.cancellation_canary.manifest", return_value=spec), \
             mock_patch("evals.long_horizon_v1.cancellation_canary.verify_cli"), \
             mock_patch("evals.long_horizon_v1.cancellation_canary.verify_prepared"), \
             mock_patch("evals.long_horizon_v1.cancellation_canary.arm_execution",
                        return_value={"model": "gpt-6-astra", "effort": "xhigh", "cli_options": []}), \
             mock_patch("evals.long_horizon_v1.cancellation_canary.verify_arm_runtime",
                        return_value={"arm": "baseline"}), \
             mock_patch("evals.long_horizon_v1.cancellation_canary.AppServerTransport", Transport), \
             mock_patch("evals.long_horizon_v1.cancellation_canary.CanarySessionMeter", Meter), \
             mock_patch("evals.long_horizon_v1.cancellation_canary.start_canary_turn",
                        return_value="turn-1"), \
             mock_patch("evals.long_horizon_v1.cancellation_canary.cancellation_target_ready",
                        side_effect=CompletedTargetError("completed before interruption")), \
             mock_patch("evals.long_horizon_v1.cancellation_canary.account",
                        side_effect=OSError("usage unavailable")):
            receipt = run_cancellation_canary("baseline", self.workspace, output,
                                              self.workspace / "sessions", 1.0)
        self.assertEqual(sends, [])
        self.assertEqual(receipt["status"], "UNKNOWN")
        self.assertIn("completed before interruption", receipt["failure"])

    def test_cancellation_adjudication_accepts_only_matched_runtime_abort(self) -> None:
        attempt = "synthetic-attempt"
        marker_dir = self.workspace / "canary" / "workspace"
        marker_dir.mkdir(parents=True)
        (marker_dir / (RUNNING_FILE + attempt)).write_text(
            RUNNING_MARKER + attempt, encoding="utf-8")
        receipt_path = marker_dir.parent / "cancellation.json"
        sessions = self.workspace / "sessions"
        sessions.mkdir()
        parent_path = sessions / "rollout-parent.jsonl"
        child_path = sessions / "rollout-child.jsonl"
        parent_rows = [
            {"type": "session_meta", "payload": {"id": "parent"}},
            {"timestamp": "2026-09-30T12:03:00.322Z", "type": "event_msg",
             "payload": {"type": "turn_aborted", "turn_id": "parent-turn",
                         "reason": "interrupted"}},
        ]
        child_rows = [
            {"type": "session_meta", "payload": {"id": "child"}},
            {"type": "turn_context", "payload": {"turn_id": "child-turn"}},
            {"type": "response_item", "payload": {"type": "custom_tool_call_output",
                "call_id": "first", "output": READY_MARKER + attempt}},
            {"type": "event_msg", "payload": {"type": "token_count",
                "info": {"last_token_usage": {"input_tokens": 1}}}},
            {"type": "response_item", "payload": {"type": "custom_tool_call",
                "name": "exec", "call_id": "second",
                "input": RUNNING_MARKER + attempt + " " + RUNNING_FILE + attempt +
                         " time.sleep(30)"}},
            {"timestamp": "2026-09-30T12:03:00.253Z", "type": "response_item",
             "payload": {"type": "custom_tool_call_output", "call_id": "second",
                 "output": "aborted by user after 1.9s",
                 "internal_chat_message_metadata_passthrough": {
                     "turn_id": "child-turn", "create_time": 1790769780.2534616}}},
            {"timestamp": "2026-09-30T12:03:00.305Z", "type": "event_msg",
             "payload": {"type": "turn_aborted", "turn_id": "child-turn",
                         "reason": "interrupted"}},
        ]
        def write_rows():
            parent_path.write_text("".join(json.dumps(row) + "\n" for row in parent_rows),
                                   encoding="utf-8")
            child_path.write_text("".join(json.dumps(row) + "\n" for row in child_rows),
                                  encoding="utf-8")
        write_rows()
        proof = {"attempt_id": attempt, "target_thread_id": "child",
                 "target_turn_id": "child-turn", "first_call_id": "first",
                 "first_done_line": 3, "priced_line": 4,
                 "second_call_id": "second", "second_call_line": 5,
                 "marker_observed_ns": 1790769780251000000,
                 "completion_absent_checked_ns": 1790769780252000000}
        requests = [
            {"thread_id": "child", "turn_id": "child-turn",
             "dispatch_time_ns": 1790769780252775900,
             "pre_dispatch_evidence": proof},
            {"thread_id": "parent", "turn_id": "parent-turn",
             "dispatch_time_ns": 1790769780252870300,
             "pre_dispatch_evidence": proof},
        ]
        attempts = [{"session_id": "parent", "turn_id": "parent-turn",
                     "child_id": "child", "model": "gpt-6-astra", "effort": "xhigh"}]
        usage = {"model_calls": 2, "estimated_usd_upper_bound": 0.1,
                 "sessions": [{"id": sid, "calls": 1, "reported_total_usage": {},
                    "terminal": "turn_aborted",
                    "model": "gpt-6-sol" if sid == "parent" else "gpt-6-astra",
                    "effort": "low" if sid == "parent" else "xhigh",
                    "turns": [{"turn_id": sid + "-turn",
                        "model": "gpt-6-sol" if sid == "parent" else "gpt-6-astra",
                        "effort": "low" if sid == "parent" else "xhigh"}],
                    "responses": [{"model": "gpt-6-sol" if sid == "parent" else "gpt-6-astra",
                        "effort": "low" if sid == "parent" else "xhigh"}]}
                    for sid in ("parent", "child")]}
        source = {"kind": "cancellation-capability", "arm": "treatment",
            "status": "UNKNOWN", "live_enabled": False,
            "failure": "canary command completion or identity changed across interrupt",
            "wall_seconds": 85.0, "usage": usage, "model": "gpt-6-sol", "effort": "low",
            "parent_thread_id": "parent", "turn_id": "parent-turn",
            "trigger_evidence": proof, "native_attempts": attempts,
            "post_interrupt_evidence": {"second_call_id": "second",
                "target_thread_id": "child", "second_completed": True},
            "cancellation": {"status": "verified-drained", "interrupt_ack": True,
                "turn_completed": True, "descendants_drained": True,
                "usage_drained": True, "interrupt_requests": requests}}
        current_usage = expanded_cancel_usage(usage, "parent-turn",
            {"parent": "parent-turn", "child": "child-turn"},
            {"parent": "2026-09-30T12:03:00.322Z",
             "child": "2026-09-30T12:03:00.305Z"})
        current_attempts = [{**attempts[0], "turns": [{"turn_id": "child-turn",
            "parent_turn_id": "parent-turn", "root_turn_id": "parent-turn",
            "model": "gpt-6-astra", "effort": "xhigh", "terminal": "turn_aborted",
            "closure": "interrupted", "terminal_line": 7}]}]
        accounting = {"status": "complete", "issues": [],
                      "interruption_verified": True,
                      "summary": current_usage, "native_attempts": current_attempts}
        with mock_patch("evals.long_horizon_v1.cancellation_adjudication.account",
                        return_value=accounting) as account_mock:
            receipt_path.write_text(json.dumps(source), encoding="utf-8")
            prior_path = self.workspace / "adjudication-v1.json"
            write_legacy_cancel_decision(prior_path, receipt_path, "treatment",
                                         _judge(source, sessions, receipt_path))
            decision = adjudicate(receipt_path, sessions,
                                  self.workspace / "adjudication.json", prior_path)
            self.assertEqual(decision["status"], "verified")
            self.assertEqual(decision["source_receipt_sha256"], file_sha(receipt_path))
            self.assertEqual(account_mock.call_args.kwargs["interruption_proof"], source)
            self.assertEqual(json.loads(receipt_path.read_text(encoding="utf-8")), source)
            for mutation in ("ordinary", "early", "ambiguous", "mismatched",
                             "terminal", "usage", "ack"):
                child_rows[5]["payload"]["output"] = "aborted by user after 1.9s"
                child_rows[5]["payload"]["call_id"] = "second"
                child_rows[5]["payload"]["internal_chat_message_metadata_passthrough"][
                    "create_time"] = 1790769780.2534616
                child_rows[6]["payload"]["reason"] = "interrupted"
                accounting["summary"] = current_usage
                source["cancellation"]["interrupt_ack"] = True
                if mutation == "ordinary":
                    child_rows[5]["payload"]["output"] = "Script completed"
                elif mutation == "early":
                    child_rows[5]["payload"]["internal_chat_message_metadata_passthrough"][
                        "create_time"] = 1790769780.252
                elif mutation == "ambiguous":
                    child_rows[5]["payload"]["internal_chat_message_metadata_passthrough"][
                        "create_time"] = None
                elif mutation == "mismatched":
                    child_rows[5]["payload"]["call_id"] = "other"
                elif mutation == "terminal":
                    child_rows[6]["payload"]["reason"] = "completed"
                elif mutation == "usage":
                    accounting["summary"] = {**current_usage, "sessions": [
                        {**current_usage["sessions"][0], "reported_total_usage": None},
                        current_usage["sessions"][1]]}
                else:
                    source["cancellation"]["interrupt_ack"] = False
                write_rows()
                with self.assertRaises(AdjudicationError, msg=mutation):
                    _judge(source, sessions, receipt_path)
            source["cancellation"]["interrupt_ack"] = True
            accounting["summary"] = current_usage
            for field, value in (("root_turn_id", "wrong-root"),
                                 ("closure", "completed"),
                                 ("turn_id", "wrong-inner-turn")):
                changed = copy.deepcopy(current_attempts)
                changed[0]["turns"][0][field] = value
                accounting["native_attempts"] = changed
                with self.assertRaises(AdjudicationError, msg=field):
                    _judge(source, sessions, receipt_path)

    def test_cancellation_v2_usage_identity_and_prior_binding_fail_closed(self) -> None:
        usage = {"model_calls": 2, "sessions": [{"id": "parent", "calls": 2,
            "model": "gpt-6-astra", "effort": "xhigh", "terminal": "turn_aborted",
            "turns": [{"turn_id": "turn", "model": "gpt-6-astra", "effort": "xhigh"}],
            "responses": [{"model": "gpt-6-astra", "effort": "xhigh"}
                          for _ in range(2)]}]}
        fresh = expanded_cancel_usage(usage, "turn", {"parent": "turn"},
                                      {"parent": "2026-09-30T12:46:04.127Z"})
        arguments = (usage, fresh, {"parent": "turn"}, "turn", {"parent": 1})
        self.assertIn("current_usage_sha256", _reconcile_usage(*arguments))
        for field, value in (("turn_id", "inner-turn"), ("root_turn_id", "wrong-root"),
                             ("response_id_sha256", None), ("model", "gpt-6-sol"),
                             ("effort", "low")):
            changed = copy.deepcopy(fresh)
            changed["sessions"][0]["responses"][0][field] = value
            with self.assertRaises(AdjudicationError, msg=field):
                _reconcile_usage(usage, changed, {"parent": "turn"}, "turn",
                                 {"parent": 1})
        changed = copy.deepcopy(fresh)
        changed["sessions"][0]["responses"][1]["response_id_sha256"] = (
            changed["sessions"][0]["responses"][0]["response_id_sha256"])
        with self.assertRaises(AdjudicationError):
            _reconcile_usage(usage, changed, {"parent": "turn"}, "turn",
                             {"parent": 1})
        changed = copy.deepcopy(fresh)
        changed["sessions"][0]["turns"][0]["calls"] = 1
        with self.assertRaises(AdjudicationError):
            _reconcile_usage(usage, changed, {"parent": "turn"}, "turn",
                             {"parent": 1})
        changed = copy.deepcopy(fresh)
        changed["sessions"][0]["responses"][0]["new_field"] = "unreviewed"
        with self.assertRaises(AdjudicationError):
            _reconcile_usage(usage, changed, {"parent": "turn"}, "turn",
                             {"parent": 1})

        source = self.workspace / "receipt.json"
        source.write_text("{}", encoding="utf-8")
        prior = self.workspace / "prior.json"
        proof = {"parent_rollout": {"session_id": "parent", "sha256": "a" * 64}}
        write_legacy_cancel_decision(prior, source, "baseline", proof)
        decision = {"arm": "baseline", "source_adjudication":
                    {"path": str(prior.resolve()), "sha256": file_sha(prior)}}
        self.assertTrue(prior_adjudication_bound(decision, file_sha(source), proof))
        decision["source_adjudication"]["sha256"] = "0" * 64
        self.assertFalse(prior_adjudication_bound(decision, file_sha(source), proof))
        decision["source_adjudication"]["sha256"] = file_sha(prior)
        self.assertFalse(prior_adjudication_bound(decision, "0" * 64, proof))

    def test_baseline_runtime_abort_requires_exact_parent_order_and_terminal(self) -> None:
        attempt = "baseline-attempt"
        path = self.workspace / "rollout-parent.jsonl"
        dispatch = 1790769780252775900
        rows = [
            {"type": "session_meta", "payload": {"id": "parent"}},
            {"type": "response_item", "payload": {"type": "custom_tool_call",
                "name": "exec", "call_id": "second",
                "input": RUNNING_MARKER + attempt + " " + RUNNING_FILE + attempt +
                         " time.sleep(30)"}},
            {"timestamp": "2026-09-30T12:03:00.253Z", "type": "response_item",
             "payload": {"type": "custom_tool_call_output", "call_id": "second",
                 "output": "aborted by user after 1.9s",
                 "internal_chat_message_metadata_passthrough": {
                     "turn_id": "parent-turn", "create_time": 1790769780.2534616}}},
            {"timestamp": "2026-09-30T12:03:00.305Z", "type": "event_msg",
             "payload": {"type": "turn_aborted", "turn_id": "parent-turn",
                         "reason": "interrupted"}},
        ]
        def write():
            path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        write()
        evidence = _baseline_runtime_abort(path, "parent", "parent-turn", attempt,
                                           "second", 2, dispatch)
        self.assertEqual(evidence["runtime_abort_output_line"], 3)
        self.assertGreater(evidence["runtime_abort_created_ns"], dispatch)
        for case in ("ordinary", "early", "wrong-call", "wrong-turn", "terminal",
                     "missing-time"):
            rows[2]["payload"]["output"] = "aborted by user after 1.9s"
            rows[2]["payload"]["call_id"] = "second"
            rows[2]["payload"]["internal_chat_message_metadata_passthrough"] = {
                "turn_id": "parent-turn", "create_time": 1790769780.2534616}
            rows[2]["timestamp"] = "2026-09-30T12:03:00.253Z"
            rows[3]["payload"]["reason"] = "interrupted"
            if case == "ordinary":
                rows[2]["payload"]["output"] = "Script completed"
            elif case == "early":
                rows[2]["payload"]["internal_chat_message_metadata_passthrough"][
                    "create_time"] = 1790769780.252
            elif case == "wrong-call":
                rows[2]["payload"]["call_id"] = "other"
            elif case == "wrong-turn":
                rows[2]["payload"]["internal_chat_message_metadata_passthrough"][
                    "turn_id"] = "other-turn"
            elif case == "terminal":
                rows[3]["payload"]["reason"] = "completed"
            else:
                rows[2].pop("timestamp")
            write()
            with self.assertRaises(TransportError, msg=case):
                _baseline_runtime_abort(path, "parent", "parent-turn", attempt,
                                        "second", 2, dispatch)

    def test_baseline_canary_verifies_only_complete_runtime_abort_usage(self) -> None:
        (self.workspace / "baseline").mkdir()
        attempt = "baseline-run"
        dispatch = 1790769780252775900
        path = self.workspace / "rollout-parent.jsonl"
        rows = [
            {"type": "session_meta", "payload": {"id": "parent"}},
            {"type": "response_item", "payload": {"type": "custom_tool_call",
                "name": "exec", "call_id": "first", "input": READY_MARKER + attempt}},
            {"type": "response_item", "payload": {"type": "custom_tool_call_output",
                "call_id": "first", "output": READY_MARKER + attempt}},
            {"type": "event_msg", "payload": {"type": "token_count",
                "info": {"last_token_usage": {"input_tokens": 1}}}},
            {"type": "response_item", "payload": {"type": "custom_tool_call",
                "name": "exec", "call_id": "second",
                "input": RUNNING_MARKER + attempt + " " + RUNNING_FILE + attempt +
                         " time.sleep(30)"}},
            {"timestamp": "2026-09-30T12:03:00.253Z", "type": "response_item",
             "payload": {"type": "custom_tool_call_output", "call_id": "second",
                 "output": "aborted by user after 1.9s",
                 "internal_chat_message_metadata_passthrough": {
                     "turn_id": "parent-turn", "create_time": 1790769780.2534616}}},
            {"timestamp": "2026-09-30T12:03:00.305Z", "type": "event_msg",
             "payload": {"type": "turn_aborted", "turn_id": "parent-turn",
                         "reason": "interrupted"}},
        ]
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        trigger = {"attempt_id": attempt, "target_thread_id": "parent",
            "target_turn_id": "parent-turn", "first_call_id": "first",
            "first_done_line": 3, "priced_line": 4, "second_call_id": "second",
            "second_call_line": 5, "marker_observed_ns": dispatch - 2000,
            "completion_absent_checked_ns": dispatch - 1000}
        class Transport:
            active_turn_complete = False
            def __init__(self, *_args, **_kwargs):
                pass
            def start(self):
                return "parent"
            def _guard_turn(self, callback):
                callback()
            def cancel_and_drain(self, *_args, **_kwargs):
                return {"status": "verified-drained", "interrupt_ack": True,
                    "turn_completed": True, "descendants_drained": True,
                    "usage_drained": True, "interrupt_requests": [{
                        "thread_id": "parent", "turn_id": "parent-turn",
                        "dispatch_time_ns": dispatch,
                        "pre_dispatch_evidence": trigger}]}
            def close(self):
                pass
        class Meter:
            def __init__(self, *_args, **_kwargs):
                self.paths = {path: {"id": "parent"}}
                self.cost_upper = 0.0
                self.unknown_models = set()
                self.unknown_usage = []
            def refresh(self):
                pass
        session = {"id": "parent", "model": "gpt-6-astra", "effort": "xhigh",
                   "terminal": "turn_aborted", "calls": 2,
                   "reported_total_usage": {"input_tokens": 1}}
        usage = {"sessions": [session], "model_calls": 2,
                 "estimated_usd_upper_bound": 0.1,
                 "unknown_models": [], "unknown_usage": []}
        observed = {"summary": usage, "native_attempts": [],
                    "issues": ["parent session lacks successful terminal result"]}
        spec = {"runtime": {"cli": str(self.workspace / "cli")}, "live_enabled": False}
        def execute(name):
            with mock_patch("evals.long_horizon_v1.cancellation_canary.manifest", return_value=spec), \
                 mock_patch("evals.long_horizon_v1.cancellation_canary.verify_cli"), \
                 mock_patch("evals.long_horizon_v1.cancellation_canary.verify_prepared"), \
                 mock_patch("evals.long_horizon_v1.cancellation_canary.arm_execution",
                            return_value={"model": "gpt-6-astra", "effort": "xhigh", "cli_options": []}), \
                 mock_patch("evals.long_horizon_v1.cancellation_canary.verify_arm_runtime",
                            return_value={"arm": "baseline"}), \
                 mock_patch("evals.long_horizon_v1.cancellation_canary.AppServerTransport", Transport), \
                 mock_patch("evals.long_horizon_v1.cancellation_canary.CanarySessionMeter", Meter), \
                 mock_patch("evals.long_horizon_v1.cancellation_canary.start_canary_turn",
                            return_value="parent-turn"), \
                 mock_patch("evals.long_horizon_v1.cancellation_canary.cancellation_target_ready",
                            return_value=trigger), \
                 mock_patch("evals.long_horizon_v1.cancellation_canary.account_bounded",
                            return_value=observed), \
                 mock_patch("evals.long_horizon_v1.cancellation_canary.secrets.token_hex",
                            return_value=attempt):
                return run_cancellation_canary("baseline", self.workspace,
                    self.workspace / name, self.workspace / "sessions", 1.0)
        verified = execute("baseline-runtime-abort")
        self.assertEqual(verified["status"], "verified")
        self.assertEqual(verified["post_interrupt_evidence"]["runtime_abort"]
                         ["second_call_id"], "second")
        session["reported_total_usage"] = None
        unknown = execute("baseline-missing-usage")
        self.assertEqual(unknown["status"], "UNKNOWN")

    def test_baseline_adjudication_requires_high_resolution_order_and_usage(self) -> None:
        attempt = "baseline-adjudication"
        dispatch = 1790772364077110400
        base = self.workspace / "canary"
        marker_dir = base / "workspace"
        marker_dir.mkdir(parents=True)
        (marker_dir / (RUNNING_FILE + attempt)).write_text(
            RUNNING_MARKER + attempt, encoding="utf-8")
        receipt_path = base / "cancellation.json"
        sessions = self.workspace / "sessions"
        sessions.mkdir()
        path = sessions / "rollout-parent.jsonl"
        rows = [
            {"type": "session_meta", "payload": {"id": "parent"}},
            {"type": "response_item", "payload": {"type": "custom_tool_call",
                "name": "exec", "call_id": "first", "input": READY_MARKER + attempt}},
            {"type": "response_item", "payload": {"type": "custom_tool_call_output",
                "call_id": "first", "output": READY_MARKER + attempt}},
            {"type": "event_msg", "payload": {"type": "token_count",
                "info": {"last_token_usage": {"input_tokens": 1}}}},
            {"type": "response_item", "payload": {"type": "custom_tool_call",
                "name": "exec", "call_id": "second",
                "input": RUNNING_MARKER + attempt + " " + RUNNING_FILE + attempt +
                         " time.sleep(30)"}},
            {"timestamp": "2026-09-30T12:46:04.077Z", "type": "response_item",
             "payload": {"type": "custom_tool_call_output", "call_id": "second",
                 "output": "aborted by user after 2.0s",
                 "internal_chat_message_metadata_passthrough": {
                     "turn_id": "parent-turn", "create_time": 1790772364.077675}}},
            {"timestamp": "2026-09-30T12:46:04.127Z", "type": "event_msg",
             "payload": {"type": "turn_aborted", "turn_id": "parent-turn",
                         "reason": "interrupted"}},
        ]
        def write_rows():
            path.write_text("".join(json.dumps(row) + "\n" for row in rows),
                            encoding="utf-8")
        write_rows()
        trigger = {"attempt_id": attempt, "target_thread_id": "parent",
            "target_turn_id": "parent-turn", "first_call_id": "first",
            "first_done_line": 3, "priced_line": 4,
            "second_call_id": "second", "second_call_line": 5,
            "marker_observed_ns": dispatch - 300000,
            "completion_absent_checked_ns": dispatch - 200000}
        pre = {**trigger, "marker_observed_ns": dispatch - 100000,
               "completion_absent_checked_ns": dispatch - 1000}
        usage = {"estimated_usd_upper_bound": 0.1, "model_calls": 2,
                 "unknown_models": [], "unknown_usage": [],
                 "sessions": [{"id": "parent", "model": "gpt-6-astra",
                    "effort": "xhigh", "terminal": "turn_aborted", "calls": 2,
                    "reported_total_usage": {"input_tokens": 1},
                    "turns": [{"turn_id": "parent-turn", "model": "gpt-6-astra",
                        "effort": "xhigh"}],
                    "responses": [{"model": "gpt-6-astra", "effort": "xhigh"}
                        for _ in range(2)]}]}
        source = {"kind": "cancellation-capability", "status": "UNKNOWN",
            "arm": "baseline", "model": "gpt-6-astra", "effort": "xhigh",
            "live_enabled": False, "real_child_observed": False,
            "native_attempts": [], "failure":
                "canary command order cannot be established after interrupt",
            "wall_seconds": 17.22, "usage": usage,
            "parent_thread_id": "parent", "turn_id": "parent-turn",
            "trigger_evidence": trigger,
            "post_interrupt_evidence": {"target_thread_id": "parent",
                "second_call_id": "second", "second_completed": True,
                "rollout_checked_ns": dispatch + 1_000_000_000},
            "cancellation": {"status": "verified-drained", "turn_id": "parent-turn",
                "interrupt_ack": True, "turn_completed": True,
                "descendants_drained": True, "usage_drained": True,
                "interrupted_threads": ["parent"], "interrupt_requests": [{
                    "thread_id": "parent", "turn_id": "parent-turn",
                    "dispatch_time_ns": dispatch, "pre_dispatch_evidence": pre}]}}
        current_usage = expanded_cancel_usage(usage, "parent-turn",
            {"parent": "parent-turn"},
            {"parent": "2026-09-30T12:46:04.127Z"})
        accounting = {"status": "UNKNOWN",
                      "issues": ["parent session lacks successful terminal result"],
                      "summary": current_usage, "native_attempts": []}
        with mock_patch("evals.long_horizon_v1.cancellation_adjudication.account",
                        return_value=accounting):
            receipt_path.write_text(json.dumps(source), encoding="utf-8")
            prior_path = self.workspace / "baseline-adjudication-v1.json"
            write_legacy_cancel_decision(prior_path, receipt_path, "baseline",
                                         _judge(source, sessions, receipt_path))
            decision = adjudicate(receipt_path, sessions,
                                  self.workspace / "baseline-adjudication.json", prior_path)
            self.assertEqual(decision["status"], "verified")
            self.assertEqual(decision["proof"]["runtime_abort_created_ns"],
                             1790772364077675000)
            for case in ("ordinary", "early", "missing-time", "coarse-conflict",
                         "wrong-call", "wrong-turn", "terminal", "usage", "ack"):
                rows[5]["payload"]["output"] = "aborted by user after 2.0s"
                rows[5]["payload"]["call_id"] = "second"
                rows[5]["payload"]["internal_chat_message_metadata_passthrough"] = {
                    "turn_id": "parent-turn", "create_time": 1790772364.077675}
                rows[5]["timestamp"] = "2026-09-30T12:46:04.077Z"
                rows[6]["payload"]["reason"] = "interrupted"
                accounting["summary"] = current_usage
                source["cancellation"]["interrupt_ack"] = True
                if case == "ordinary":
                    rows[5]["payload"]["output"] = "Script completed"
                elif case == "early":
                    rows[5]["payload"]["internal_chat_message_metadata_passthrough"][
                        "create_time"] = 1790772364.077
                elif case == "missing-time":
                    rows[5]["payload"]["internal_chat_message_metadata_passthrough"][
                        "create_time"] = None
                elif case == "coarse-conflict":
                    rows[5]["timestamp"] = "2026-09-30T12:46:04.076Z"
                elif case == "wrong-call":
                    rows[5]["payload"]["call_id"] = "other"
                elif case == "wrong-turn":
                    rows[5]["payload"]["internal_chat_message_metadata_passthrough"][
                        "turn_id"] = "other-turn"
                elif case == "terminal":
                    rows[6]["payload"]["reason"] = "completed"
                elif case == "usage":
                    accounting["summary"] = {**current_usage, "sessions": [
                        {**current_usage["sessions"][0], "reported_total_usage": None}]}
                else:
                    source["cancellation"]["interrupt_ack"] = False
                write_rows()
                with self.assertRaises(AdjudicationError, msg=case):
                    _judge(source, sessions, receipt_path)

    def test_canary_preturn_expiry_and_cost_rejection_send_no_turn(self) -> None:
        class Meter:
            cost_upper = 0.0
            unknown_models = set()
            unknown_usage = []
            def refresh(self):
                pass
        meter = Meter()
        transport = object.__new__(AppServerTransport)
        transport.thread_id = "parent"
        sends = []
        transport._send = lambda *args, **kwargs: sends.append((args, kwargs)) or 1
        transport.deadline = time.monotonic() - 1
        with self.assertRaisesRegex(TransportError, "wall limit"):
            start_canary_turn(transport, meter, "parent", "baseline",
                              time.monotonic() + 100, self.workspace,
                              "gpt-6-astra", "xhigh")
        self.assertEqual(sends, [])
        transport.deadline = time.monotonic() + 100
        meter.cost_upper = 1.0
        with self.assertRaisesRegex(TransportError, r"\$1 stop threshold"):
            start_canary_turn(transport, meter, "parent", "baseline",
                              time.monotonic() + 100, self.workspace,
                              "gpt-6-astra", "xhigh")
        self.assertEqual(sends, [])

    def test_canary_inflight_cost_guard_and_normal_start(self) -> None:
        class Meter:
            cost_upper = 0.0
            unknown_models = set()
            unknown_usage = []
            def refresh(self):
                pass
        meter = Meter()
        transport = object.__new__(AppServerTransport)
        transport.thread_id = "parent"
        transport.deadline = time.monotonic() + 100
        sends = []
        transport._send = lambda method, params: sends.append(method) or len(sends)
        def expensive_frame():
            meter.cost_upper = 1.0
            return {}
        transport._frame = expensive_frame
        with self.assertRaisesRegex(TransportError, r"\$1 stop threshold"):
            start_canary_turn(transport, meter, "parent", "baseline",
                              time.monotonic() + 100, self.workspace,
                              "gpt-6-astra", "xhigh")
        self.assertEqual(sends, ["turn/start"])
        meter.cost_upper = 0.0
        transport._frame = lambda: {"id": 2, "result": {"turn": {"id": "turn-2"}}}
        self.assertEqual(start_canary_turn(transport, meter, "parent", "baseline",
                              time.monotonic() + 100, self.workspace,
                              "gpt-6-astra", "xhigh"), "turn-2")
        self.assertEqual(sends, ["turn/start", "turn/start"])

    def test_drain_deadline_is_absolute_near_canary_limit(self) -> None:
        transport = object.__new__(AppServerTransport)
        transport.thread_id, transport.active_turn_id = "parent", "turn-1"
        transport.active_turn_complete = False
        sends = []
        transport._send = lambda method, params: sends.append(method) or 1
        class Meter:
            paths = {Path("parent"): {"id": "parent", "terminal": None,
                "calls": 1, "last_total_usage": {}, "turns": [{"turn_id": "turn-1"}]}}
            unknown_models = set()
            unknown_usage = []
            def refresh(self):
                pass
            def dispatch_coverage_issues(self):
                return []
        now = time.monotonic()
        receipt = transport.cancel_and_drain(Meter(), timeout=20,
                                               absolute_deadline=now - 1)
        self.assertEqual(receipt["status"], "UNKNOWN")
        self.assertEqual(sends, [])
        observed = []
        def frame(**kwargs):
            observed.append(kwargs["drain_deadline"])
            raise OSError("drain stream failed")
        transport._frame = frame
        deadline = time.monotonic() + 0.1
        receipt = transport.cancel_and_drain(Meter(), timeout=20,
                                               absolute_deadline=deadline)
        self.assertEqual(receipt["status"], "UNKNOWN")
        self.assertEqual(sends, ["turn/interrupt"])
        self.assertTrue(observed and all(value <= deadline for value in observed))

    def test_canary_accounting_respects_receipt_reserve(self) -> None:
        with mock_patch("evals.long_horizon_v1.cancellation_canary.account") as account_mock:
            with mock_patch("evals.long_horizon_v1.cancellation_canary.time.monotonic",
                            return_value=100.0):
                with self.assertRaisesRegex(TimeoutError, "accounting deadline"):
                    account_bounded(self.workspace, "parent", "gpt-6-astra", "xhigh",
                                    False, 101.0)
            account_mock.assert_not_called()
            account_mock.return_value = {"summary": {"model_calls": 1}}
            self.assertEqual(account_bounded(self.workspace, "parent", "gpt-6-astra",
                "xhigh", False, time.monotonic() + 5)["summary"]["model_calls"], 1)

    def test_transport_launch_error_writes_failure_receipt(self) -> None:
        proof = {"cli_sha256": "1" * 64, "code_mode_host_sha256": "2" * 64,
            "routing_config_canonical_sha256": None,
            "preparation": {"preparation_sha256": "3" * 64},
            "zero_model_runtime_binding": {"arm": "baseline"}}
        output = self.workspace / "launch-error"
        with mock_patch("evals.long_horizon_v1.run.manifest",
                        return_value={**manifest(), "pilot": None}), \
             mock_patch("evals.long_horizon_v1.run.live_preflight", return_value=proof), \
             mock_patch("evals.long_horizon_v1.run.AppServerTransport",
                        side_effect=OSError("launch refused")):
            result = live_run(self.workspace, output, "baseline", self.workspace / "cli",
                              self.workspace / "capability", self.workspace / "sessions")
        self.assertIn("launch refused", result["stop_reason"])
        self.assertEqual(result["cost_status"], "UNKNOWN")
        self.assertTrue((output / "run.json").is_file())

    def test_runtime_and_accounting_errors_keep_final_usage_receipt(self) -> None:
        (self.workspace / "baseline").mkdir()
        proof = {"cli_sha256": "1" * 64, "code_mode_host_sha256": "2" * 64,
            "routing_config_canonical_sha256": None,
            "preparation": {"preparation_sha256": "3" * 64},
            "zero_model_runtime_binding": {"arm": "baseline"}}
        class FailedTransport:
            def __init__(self, *_args, **_kwargs):
                self.thread_id = "parent"
                self.active_turn_complete = True
                self.turn_started_at = None
                self.events = []
            def start(self):
                return "parent"
            def turn(self, *_args):
                raise RuntimeError("turn runtime failed")
            def close(self):
                pass
        output = self.workspace / "runtime-error"
        with mock_patch("evals.long_horizon_v1.run.manifest",
                        return_value={**manifest(), "pilot": None}), \
             mock_patch("evals.long_horizon_v1.run.live_preflight", return_value=proof), \
             mock_patch("evals.long_horizon_v1.run.AppServerTransport", FailedTransport), \
             mock_patch("evals.long_horizon_v1.run.verify_cli"), \
             mock_patch("evals.long_horizon_v1.run.account",
                        side_effect=OSError("rollout read failed")):
            result = live_run(self.workspace, output, "baseline", self.workspace / "cli",
                              self.workspace / "capability", self.workspace / "sessions")
        self.assertIn("turn runtime failed", result["stop_reason"])
        self.assertIn("final accounting failed", result["usage_issues"][0])
        self.assertIsInstance(result["usage"], dict)
        self.assertEqual(result["cost_status"], "UNKNOWN")
        self.assertTrue((output / "run.json").is_file())

    def test_grader_hazard_preserves_usage_but_forces_unknown_cost_receipt(self) -> None:
        (self.workspace / "baseline").mkdir()
        (self.workspace / "grader-hazard.json").write_text(
            json.dumps({"status": "UNKNOWN", "reason": "fake descendant"}), encoding="utf-8")
        proof = {"cli_sha256": "1" * 64, "code_mode_host_sha256": "2" * 64,
            "routing_config_canonical_sha256": None,
            "preparation": {"preparation_sha256": "3" * 64},
            "zero_model_runtime_binding": {"arm": "baseline"}}
        class StoppedTransport:
            def __init__(self, *_args, **_kwargs):
                self.thread_id = "parent"
                self.active_turn_complete = True
                self.turn_started_at = None
                self.events = []
            def start(self):
                return "parent"
            def turn(self, *_args):
                raise TransportError("grader stop")
            def close(self):
                pass
        observed = {"summary": {"sessions": [{"id": "parent", "calls": 1}],
                                 "estimated_usd_upper_bound": 0.25},
                    "issues": [], "responses": [], "by_model": {},
                    "failed_child_attempts": [], "native_attempts": []}
        output = self.workspace / "hazard-run"
        with mock_patch("evals.long_horizon_v1.run.manifest",
                        return_value={**manifest(), "pilot": None}), \
             mock_patch("evals.long_horizon_v1.run.live_preflight", return_value=proof), \
             mock_patch("evals.long_horizon_v1.run.AppServerTransport", StoppedTransport), \
             mock_patch("evals.long_horizon_v1.run.verify_cli"), \
             mock_patch("evals.long_horizon_v1.run.account", return_value=observed):
            result = live_run(self.workspace, output, "baseline", self.workspace / "cli",
                              self.workspace / "capability", self.workspace / "sessions")
        self.assertEqual(result["usage"]["estimated_usd_upper_bound"], 0.25)
        self.assertIn("WSL grader process tree cleanup UNKNOWN", result["usage_issues"])
        self.assertEqual(result["cost_status"], "UNKNOWN")
        self.assertEqual(json.loads((output / "run.json").read_text())["cost_status"],
                         "UNKNOWN")

    def test_runner_drain_error_still_closes_and_writes_receipt(self) -> None:
        (self.workspace / "baseline").mkdir()
        proof = {"cli_sha256": "1" * 64, "code_mode_host_sha256": "2" * 64,
            "routing_config_canonical_sha256": None,
            "preparation": {"preparation_sha256": "3" * 64},
            "zero_model_runtime_binding": {"arm": "baseline"}}
        closed = []
        class FailedDrain:
            def __init__(self, *_args, **_kwargs):
                self.thread_id = "parent"
                self.active_turn_complete = False
                self.active_turn_id = None
                self.turn_started_at = None
                self.events = []
            def start(self):
                return "parent"
            def turn(self, *_args):
                raise RuntimeError("turn failed")
            def cancel_and_drain(self, *_args, **_kwargs):
                raise OSError("drain failed")
            def close(self):
                closed.append(True)
        output = self.workspace / "runner-drain-error"
        with mock_patch("evals.long_horizon_v1.run.manifest",
                        return_value={**manifest(), "pilot": None}), \
             mock_patch("evals.long_horizon_v1.run.live_preflight", return_value=proof), \
             mock_patch("evals.long_horizon_v1.run.AppServerTransport", FailedDrain), \
             mock_patch("evals.long_horizon_v1.run.verify_cli"), \
             mock_patch("evals.long_horizon_v1.run.account",
                        side_effect=OSError("rollout read failed")):
            result = live_run(self.workspace, output, "baseline", self.workspace / "cli",
                              self.workspace / "capability", self.workspace / "sessions")
        self.assertEqual(closed, [True])
        self.assertEqual(result["cancellation"]["status"], "UNKNOWN")
        self.assertIn("drain failed", result["cancellation"]["reason"])
        self.assertIsInstance(result["usage"], dict)
        self.assertTrue((output / "run.json").is_file())

    def test_runner_waits_for_late_child_after_completed_parent_failure(self) -> None:
        (self.workspace / "baseline").mkdir()
        proof = {"cli_sha256": "1" * 64, "code_mode_host_sha256": "2" * 64,
            "routing_config_canonical_sha256": None,
            "preparation": {"preparation_sha256": "3" * 64},
            "zero_model_runtime_binding": {"arm": "baseline"}}
        calls = []

        class CompletedParent:
            def __init__(self, *_args, **_kwargs):
                self.thread_id = "parent"
                self.active_turn_id = None
                self.active_turn_complete = True
                self.turn_started_at = None
                self.events = []

            def start(self):
                return "parent"

            def turn(self, *_args):
                self.active_turn_id = "parent-turn"
                raise TransportError("failure after parent completion")

            def cancel_and_drain(self, *_args, **kwargs):
                calls.append(("drain", kwargs["absolute_deadline"]))
                return {"status": "UNKNOWN", "reason": "late descendant usage missing"}

            def close(self):
                calls.append(("close", None))

        output = self.workspace / "runner-late-child"
        with mock_patch("evals.long_horizon_v1.run.manifest",
                        return_value={**manifest(), "pilot": None}), \
             mock_patch("evals.long_horizon_v1.run.live_preflight", return_value=proof), \
             mock_patch("evals.long_horizon_v1.run.AppServerTransport", CompletedParent), \
             mock_patch("evals.long_horizon_v1.run.verify_cli"), \
             mock_patch("evals.long_horizon_v1.run.account",
                        side_effect=OSError("rollout read failed")):
            result = live_run(self.workspace, output, "baseline", self.workspace / "cli",
                              self.workspace / "capability", self.workspace / "sessions")
        self.assertEqual([name for name, _ in calls], ["drain", "close"])
        self.assertEqual(result["cancellation"]["status"], "UNKNOWN")
        self.assertEqual(json.loads((output / "run.json").read_text())["cancellation"],
                         result["cancellation"])

    def _runner_multiple_child_cancellation(self, child_two: str) -> tuple[dict, list, list]:
        """Exercise live_run's callback with the real bounded drain, offline."""
        (self.workspace / "treatment").mkdir(exist_ok=True)
        spec = {**manifest(), "pilot": {"path": "offline-synthetic-plan.json"}}
        output = self.workspace / ("runner-children-" + child_two)
        plan = {"mode": "standalone-feasibility",
                "packet_scope_mode": "diagnostic-feasibility",
                "run_outputs": {"treatment": str(output)}}
        proof = {"cli_sha256": "1" * 64, "code_mode_host_sha256": "2" * 64,
            "routing_config_canonical_sha256": None,
            "preparation": {"preparation_sha256": "3" * 64},
            "zero_model_runtime_binding": {"arm": "treatment"}}
        def state(session_id, turn_id, parent_id):
            return {"id": session_id, "parent_id": parent_id, "terminal": None,
                "calls": 1, "last_total_usage": {}, "pending_usage_record": None,
                "turns": [{"turn_id": turn_id, "terminal": None, "calls": 1}]}
        parent = state("parent", "parent-turn", None)
        first = state("child-1", "turn-1", "parent")
        second = state("child-2", None if child_two == "unidentified" else "turn-2",
                       "other" if child_two == "wrong" else "parent")
        holder = {}
        events = []
        account_proofs = []

        class Meter:
            def __init__(self):
                self.paths = {Path("parent"): parent, Path("child-1"): first}
                if child_two in ("initial", "wrong", "unidentified"):
                    self.paths[Path("child-2")] = second
                self.unknown_models = set()
                self.unknown_usage = []
                self.cost_upper = 0.0
                self.calls = 3

            def refresh(self):
                if child_two == "late" and holder["transport"].parent_ack:
                    self.paths[Path("child-2")] = second

            def dispatch_coverage_issues(self):
                return []

            def summary(self):
                return {"sessions": [{"id": row["id"], "calls": row["calls"]}
                    for row in self.paths.values()], "unknown_models": [],
                    "unknown_usage": [], "model_calls": sum(
                        row["calls"] for row in self.paths.values()),
                    "estimated_usd_upper_bound": 0.1}

        meter = Meter()

        class Transport(AppServerTransport):
            def __init__(self, *_args, **_kwargs):
                self.thread_id = "parent"
                self.active_turn_id = None
                self.active_turn_complete = True
                self.turn_started_at = None
                self.events = []
                self.replies = []
                self.parent_ack = False
                self.idle_frames = 0
                self.created_at = time.monotonic()
                holder["transport"] = self

            def start(self):
                return "parent"

            def turn(self, *_args):
                self.active_turn_id = "parent-turn"
                self.active_turn_complete = False
                self.turn_started_at = time.monotonic()
                if child_two == "usage-unknown":
                    meter.unknown_usage.append("child-1 usage identity ambiguous")
                raise TransportError("offline stopped parent turn")

            def cancel_and_drain(self, *args, **kwargs):
                holder["drain_deadline"] = kwargs["absolute_deadline"]
                return super().cancel_and_drain(*args, **kwargs)

            def _send(self, method, params):
                self.assert_method(method)
                request_id = len(events) + 1
                events.append(("interrupt", params["threadId"], params["turnId"],
                               time.monotonic()))
                self.replies.append({"id": request_id, "result": {}})
                return request_id

            @staticmethod
            def assert_method(method):
                if method != "turn/interrupt":
                    raise AssertionError("unexpected App Server method")

            def _frame(self, **kwargs):
                self.assertLessThanDeadline(kwargs["drain_deadline"])
                if self.replies:
                    reply = self.replies.pop(0)
                    _, thread, _, _ = events[reply["id"] - 1]
                    target = {"parent": parent, "child-1": first,
                              "child-2": second}[thread]
                    target["terminal"] = target["turns"][-1]["terminal"] = "turn_aborted"
                    if thread == "parent":
                        self.active_turn_complete = True
                        self.parent_ack = True
                    events.append(("ack", thread, target["turns"][-1]["turn_id"],
                                   time.monotonic()))
                    return reply
                self.idle_frames += 1
                time.sleep(0.001)
                if self.idle_frames > 20 and (meter.unknown_usage or any(
                        row["terminal"] is None for row in meter.paths.values())):
                    raise TransportError("offline unresolved child")
                return {}

            @staticmethod
            def assertLessThanDeadline(deadline):
                if time.monotonic() >= deadline:
                    raise AssertionError("drain exceeded its absolute deadline")

            def close(self):
                events.append(("close", None, None, time.monotonic()))

        def account_stub(*_args, **kwargs):
            account_proofs.append(kwargs.get("interruption_proof"))
            return {"summary": meter.summary(), "issues": [], "responses": [],
                "by_model": {}, "failed_child_attempts": 0, "native_attempts": [],
                "interruption_verified": bool(kwargs.get("interruption_proof"))}

        with ExitStack() as stack:
            stack.enter_context(mock_patch("evals.long_horizon_v1.run.manifest",
                                           return_value=spec))
            stack.enter_context(mock_patch("evals.long_horizon_v1.run.pilot_plan",
                                           return_value=plan))
            stack.enter_context(mock_patch("evals.long_horizon_v1.run.live_preflight",
                                           return_value=proof))
            stack.enter_context(mock_patch("evals.long_horizon_v1.run.AppServerTransport",
                                           Transport))
            stack.enter_context(mock_patch("evals.long_horizon_v1.run.SessionMeter",
                                           return_value=meter))
            stack.enter_context(mock_patch("evals.long_horizon_v1.run.verify_cli"))
            stack.enter_context(mock_patch("evals.long_horizon_v1.run.verify_arm_config"))
            stack.enter_context(mock_patch("evals.long_horizon_v1.run.final_fork_observation",
                                           return_value=([], [])))
            stack.enter_context(mock_patch("evals.long_horizon_v1.run.account",
                                           side_effect=account_stub))
            stack.enter_context(mock_patch(
                "evals.long_horizon_v1.transport.DRAIN_REFRESH_SECONDS", 0.001))
            stack.enter_context(mock_patch(
                "evals.long_horizon_v1.transport.DRAIN_QUIET_SECONDS", 0.01))
            result = live_run(self.workspace, output, "treatment", self.workspace / "cli",
                              self.workspace / "capability", self.workspace / "sessions",
                              benchmark_mode="standalone-feasibility")
        self.assertEqual(json.loads((output / "run.json").read_text())["cancellation"],
                         result["cancellation"])
        if child_two == "single":
            self.assertEqual(len(account_proofs), 1)
            self.assertIsNotNone(account_proofs[0])
        else:
            self.assertEqual(account_proofs, [None])
        reserve = holder["drain_deadline"] - holder["transport"].created_at
        self.assertGreater(reserve, MAX_SECONDS - RECEIPT_RESERVE_SECONDS - 1)
        self.assertLessEqual(reserve, MAX_SECONDS - RECEIPT_RESERVE_SECONDS)
        return result, events, account_proofs

    def test_runner_retains_single_child_interrupt_proof(self) -> None:
        result, events, proofs = self._runner_multiple_child_cancellation("single")
        interrupts = [(thread, turn) for kind, thread, turn, _ in events
                      if kind == "interrupt"]
        self.assertEqual(interrupts, [("child-1", "turn-1"),
                                      ("parent", "parent-turn")])
        self.assertEqual(result["cancellation"]["status"], "verified-drained")
        self.assertEqual(result["interruption_proof"], proofs[0])

    def test_runner_interrupts_two_initial_children_without_cost_proof(self) -> None:
        result, events, _ = self._runner_multiple_child_cancellation("initial")
        interrupts = [(thread, turn) for kind, thread, turn, _ in events
                      if kind == "interrupt"]
        self.assertEqual(interrupts, [("child-1", "turn-1"),
                                      ("child-2", "turn-2"),
                                      ("parent", "parent-turn")])
        self.assertEqual(result["cancellation"]["status"], "verified-drained")
        self.assertEqual(result["cost_status"], "UNKNOWN")
        self.assertIsNone(result["interruption_proof"])
        self.assertEqual(events[-1][0], "close")
        self.assertGreaterEqual(events[-1][3] - max(
            stamp for kind, _, _, stamp in events if kind == "ack"), 0.01)

    def test_runner_interrupts_late_second_child_after_parent_ack(self) -> None:
        result, events, _ = self._runner_multiple_child_cancellation("late")
        interrupts = [(thread, turn) for kind, thread, turn, _ in events
                      if kind == "interrupt"]
        self.assertEqual(interrupts, [("child-1", "turn-1"),
                                      ("parent", "parent-turn"),
                                      ("child-2", "turn-2")])
        parent_ack = next(stamp for kind, thread, _, stamp in events
                          if kind == "ack" and thread == "parent")
        late_send = next(stamp for kind, thread, _, stamp in events
                         if kind == "interrupt" and thread == "child-2")
        self.assertLess(parent_ack, late_send)
        self.assertEqual(sum(thread == "child-2" for thread, _ in interrupts), 1)
        self.assertEqual(result["cancellation"]["status"], "verified-drained")
        self.assertEqual(result["cost_status"], "UNKNOWN")
        self.assertIsNone(result["interruption_proof"])

    def test_runner_skips_unsafe_child_and_still_interrupts_valid_targets(self) -> None:
        for child_two in ("wrong", "unidentified"):
            with self.subTest(child_two=child_two):
                result, events, _ = self._runner_multiple_child_cancellation(child_two)
                interrupts = [(thread, turn) for kind, thread, turn, _ in events
                              if kind == "interrupt"]
                self.assertEqual(interrupts, [("child-1", "turn-1"),
                                              ("parent", "parent-turn")])
                self.assertEqual(result["cancellation"]["status"], "UNKNOWN")
                self.assertEqual(result["cost_status"], "UNKNOWN")
                self.assertIsNone(result["interruption_proof"])

    def test_runner_ambiguous_usage_keeps_cost_and_proof_unknown(self) -> None:
        result, events, _ = self._runner_multiple_child_cancellation("usage-unknown")
        interrupts = [(thread, turn) for kind, thread, turn, _ in events
                      if kind == "interrupt"]
        self.assertEqual(interrupts, [("child-1", "turn-1"),
                                      ("parent", "parent-turn")])
        self.assertEqual(result["cancellation"]["status"], "UNKNOWN")
        self.assertEqual(result["cost_status"], "UNKNOWN")
        self.assertIsNone(result["interruption_proof"])

    def test_canary_drain_error_still_closes_and_writes_unknown_receipt(self) -> None:
        (self.workspace / "baseline").mkdir()
        closed = []
        class FailedDrain:
            def __init__(self, *_args, **_kwargs):
                self.thread_id = "parent"
                self.active_turn_complete = False
                self.events = []
            def start(self):
                return "parent"
            def _guard_turn(self, callback):
                callback()
            def cancel_and_drain(self, *_args, **_kwargs):
                raise OSError("canary drain failed")
            def close(self):
                closed.append(True)
        class Meter:
            def __init__(self, *_args, **_kwargs):
                self.paths = {Path("parent"): {"id": "parent", "turns": [
                    {"turn_id": "turn-1", "model": "gpt-6-astra", "effort": "xhigh"}],
                    "terminal": None}}
                self.cost_upper = 0.0
                self.unknown_models = set()
                self.unknown_usage = []
            def refresh(self):
                pass
            def summary(self):
                return {"sessions": [], "estimated_usd_upper_bound": 0.0}
        spec = {"runtime": {"cli": str(self.workspace / "cli")}, "live_enabled": False}
        policy = {"model": "gpt-6-astra", "effort": "xhigh", "cli_options": []}
        output = self.workspace / "canary-drain-error"
        with mock_patch("evals.long_horizon_v1.cancellation_canary.manifest", return_value=spec), \
             mock_patch("evals.long_horizon_v1.cancellation_canary.verify_cli"), \
             mock_patch("evals.long_horizon_v1.cancellation_canary.verify_prepared"), \
             mock_patch("evals.long_horizon_v1.cancellation_canary.arm_execution",
                        return_value=policy), \
             mock_patch("evals.long_horizon_v1.cancellation_canary.verify_arm_runtime",
                        return_value={"arm": "baseline"}), \
             mock_patch("evals.long_horizon_v1.cancellation_canary.AppServerTransport",
                        FailedDrain), \
             mock_patch("evals.long_horizon_v1.cancellation_canary.CanarySessionMeter", Meter), \
             mock_patch("evals.long_horizon_v1.cancellation_canary.cancellation_target_ready",
                        return_value=True), \
             mock_patch("evals.long_horizon_v1.cancellation_canary.start_canary_turn",
                        return_value="turn-1"), \
             mock_patch("evals.long_horizon_v1.cancellation_canary.account",
                        side_effect=OSError("usage unavailable")):
            result = run_cancellation_canary("baseline", self.workspace, output,
                                              self.workspace / "sessions", 1.0)
        self.assertEqual(closed, [True])
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertIn("canary drain failed", result["cancellation"]["reason"])
        self.assertIsInstance(result["usage"], dict)
        self.assertTrue((output / "cancellation.json").is_file())


if __name__ == "__main__":
    unittest.main()
