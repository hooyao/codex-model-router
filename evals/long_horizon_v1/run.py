"""Three-round persistent-session evaluator; dry-run makes no model calls."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from evals.long_horizon_v1.common import (ASSET_ROOT, HERE, TASK_ID, capture_patch,
    file_sha, fresh_directory, manifest, sha, write_json_new)
from evals.long_horizon_v1.assets.submit_checkpoint import submit as submit_checkpoint
from evals.long_horizon_v1.accounting import account
from evals.long_horizon_v1.grade import record_grader_hazard, stop_wsl_grader
from evals.long_horizon_v1.protocol import StageMachine
from evals.long_horizon_v1.transport import (AppServerTransport, TransportError,
                                             meter_relevant_frame)
from evals.long_horizon_v1.runtime_binding import verify_arm_config, verify_cli
from evals.long_horizon_v1.runtime_binding import arm_execution, verify_arm_runtime
from evals.long_horizon_v1.prepare import verify_prepared
from evals.long_horizon_v1.quality_admission import verify_quality_admission
from evals.long_horizon_v1.diagnostic_reference import verify_diagnostic_reference
from evals.long_horizon_v1.fork_policy import (ERROR as FORK_POLICY_ERROR,
    DIAGNOSTIC, preflight_packet_scope, validate_observed_spawns)
from evals.scripts.run_paired_arm import SessionMeter, native_lineage

MAX_SECONDS = 4500
PLANNING_USD = 18.0
STOP_USD = 15.0
CLEANUP_RESERVE_SECONDS = 25.0
RECEIPT_RESERVE_SECONDS = 5.0
TELEMETRY_STARTUP_GRACE_SECONDS = 20.0
TELEMETRY_USAGE_GRACE_SECONDS = 30.0
TELEMETRY_LINEAGE_GRACE_SECONDS = 10.0
METER_REFRESH_INTERVAL_SECONDS = 1.0
PILOT_CAPABILITY_SHA256 = {
    "treatment": "e2454ff03ea31ba57848288df72d6f119c3a9913cf3defd6ddfe2e52a1952d17",
}
PILOT_ADJUDICATION = {
    "baseline": {
        "path": "_scratch/cancellation-canary/baseline-parent-stop-20260930-1/adjudication-v2.json",
        "sha256": "41764a66cde0bd8a8983b512a686ac6829fb8d3b4985fba7a1603fa8da7863d1"},
    "treatment": {
        "path": "_scratch/cancellation-canary/retry-20260930120001-96e403dd/adjudication-v2.json",
        "sha256": "7eba0edc2e714d98dd6506fbb0fecd92a2629158d87c9b4f57e0f5034d31519c"},
}


def parent_selector(arm: str) -> tuple[str, str]:
    policy = arm_execution(manifest(), arm)
    return policy["model"], policy["effort"]


def submitted_prompt(shared_text: str, arm: str, spec: dict) -> str:
    suffix = arm_execution(spec, arm)["prompt_suffix_text"]
    return shared_text + "\n\n" + suffix


def baseline_has_child(parent_id: str, sessions: list[dict]) -> bool:
    return any(session.get("id") != parent_id for session in sessions)


def final_fork_observation(meter: SessionMeter | None, parent_id: str | None,
                           workspace: Path,
                           native_attempts: list[dict] | None,
                           mode: str = DIAGNOSTIC) -> tuple[list[dict], list[str]]:
    """Audit a closed native snapshot against every completed child attempt."""
    parent_paths = ([path for path, state in meter.paths.items()
                     if state["id"] == parent_id] if meter is not None and parent_id else [])
    if len(parent_paths) != 1:
        return [], [FORK_POLICY_ERROR + ": parent rollout missing"]
    return validate_observed_spawns(
        parent_paths[0], parent_id, native_attempts,
        closed_snapshot=True,
        plans=workspace / ".benchmark" / "dispatch-plans",
        child_rollouts={state["id"]: path for path, state in meter.paths.items()
                        if state["id"] != parent_id}, mode=mode)


def _canonical_sha(value: dict) -> str:
    return sha(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _canary_runtime_equivalent(previous: dict, current: dict, plan: dict | None) -> bool:
    """Allow only the reviewed treatment v1-to-v2 policy/suffix transition."""
    if not isinstance(previous, dict):
        return False
    if plan is None or (plan.get("schema_version") not in (2, 3) and
                        plan.get("mode") != "standalone-feasibility"):
        return previous == current
    if plan.get("mode") == "standalone-feasibility":
        # The cancellation canary predates the reviewed treatment v2 policy.
        # Bind that narrow transition without importing a performance comparator.
        historical = {"sha256": "4e0e535fb8f67ea68aa09781c799dd7acab30946e3eae5043174834c332af008",
            "prompt_suffix_sha256": {"treatment":
                "0a3cbaf15e717e7e689a496e54f81181fa1c66072259c41e07a8993688524763"}}
        if (file_sha(HERE / "arm-execution-v1.json") != historical["sha256"] or
                file_sha(HERE / "treatment-execution-v1.md") !=
                historical["prompt_suffix_sha256"]["treatment"]):
            return False
    else:
        historical = plan["diagnostic_reference"]["historical_arm_execution"]
    if (previous.get("policy_sha256") != historical["sha256"] or
            previous.get("prompt_suffix_sha256") !=
                historical["prompt_suffix_sha256"]["treatment"]):
        return False
    ignored = {"policy_sha256", "prompt_suffix_sha256"}
    return ({key: value for key, value in previous.items() if key not in ignored} ==
            {key: value for key, value in current.items() if key not in ignored})


def _runner_source_sha256() -> str:
    """Bind runner bytes while leaving the fresh capability digest slots fillable."""
    source = (HERE / "run.py").read_text(encoding="utf-8")
    normalized, count = re.subn(
        r"PILOT_CAPABILITY_SHA256 = \{\n.*?\n\}\n",
        "PILOT_CAPABILITY_SHA256 = <frozen-capability-slots>\n",
        source, count=1, flags=re.S)
    if count != 1:
        raise ValueError("runner capability digest slots missing")
    return sha(normalized.encode("utf-8"))


def _verify_corrected_controls(controls: dict, spec: dict) -> None:
    """Recheck the corrected oracle matrix, replay files, and Linux exit bytes."""
    matrix_name = "controls/control-matrix-r9e-20261001.json"
    receipt_name = "controls/oracle-erratum-r9e-receipt.json"
    matrix_path, receipt_path = HERE / matrix_name, HERE / receipt_name
    if (controls.get("matrix_path") != matrix_name or
            controls.get("receipt_path") != receipt_name or
            controls.get("matrix_sha256") != file_sha(matrix_path) or
            controls.get("receipt_sha256") != file_sha(receipt_path) or
            controls.get("expected_controls") != 12 or
            controls.get("expected_grades") != 20 or
            controls.get("expected_linux_status_checks") != 22 or
            controls.get("expected_additional_status_checks") != 5):
        raise ValueError("corrected control evidence hash or count drift")
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    erratum = spec.get("oracle_erratum", {})
    changed_assets = {name: spec["assets"][name] for name in erratum.get("changed_assets", [])}
    if (receipt.get("controls_pass") is not True or
            receipt.get("control_count") != 12 or receipt.get("grade_count") != 20 or
            receipt.get("linux_status_checks") != 22 or
            receipt.get("additional_linux_status_checks") != 5 or
            receipt.get("paid_model_calls") != 0 or
            receipt.get("matrix_path") != matrix_name or
            receipt.get("matrix_sha256") != controls["matrix_sha256"] or
            receipt.get("adjudication_sha256") != erratum.get("adjudication_sha256") or
            receipt.get("asset_sha256") != changed_assets or
            receipt.get("frozen_test_asset_audit", {}).get("public_changed") is not False or
            set(receipt.get("frozen_test_asset_audit", {}).get("changed", [])) !=
                set(changed_assets) or
            receipt.get("historical_sha256_unchanged", {}).get("baseline_hidden_grade") !=
                erratum.get("historical_hidden_grade_sha256") or
            matrix.get("controls_pass") is not True):
        raise ValueError("corrected oracle receipt or asset audit incomplete")
    raw_matrix = HERE / receipt["raw_matrix_path"]
    if (file_sha(raw_matrix) != receipt.get("raw_matrix_sha256") or
            receipt["raw_matrix_sha256"] != controls["matrix_sha256"]):
        raise ValueError("corrected raw matrix drift")
    evidence_root = (HERE / "controls" / "oracle-erratum-r9e-evidence").resolve()
    evidence = receipt.get("evidence_files_sha256")
    if not isinstance(evidence, dict) or len(evidence) != 143:
        raise ValueError("corrected oracle raw evidence incomplete")
    for name, digest in evidence.items():
        path = (evidence_root / name).resolve()
        if not path.is_relative_to(evidence_root) or file_sha(path) != digest:
            raise ValueError("corrected oracle raw evidence drift")
    def verify_status(relative: str, grade_check: dict, kind: str) -> None:
        directory = evidence_root / relative / kind
        status_name = f"{relative}/{kind}/go-exit-status.txt"
        script_name = f"{relative}/{kind}/run-go.sh"
        raw = (directory / "go-exit-status.txt").read_bytes()
        if (not re.fullmatch(rb"(?:0|[1-9][0-9]*)\n", raw) or
                evidence.get(status_name) != file_sha(directory / "go-exit-status.txt") or
                evidence.get(script_name) != file_sha(directory / "run-go.sh") or
                (directory / "wsl-pgid.txt").exists() or
                grade_check.get("exit_code") != int(raw) or
                any(evidence.get(f"{relative}/{kind}/{stream}.txt") !=
                    file_sha(directory / f"{stream}.txt") or
                    grade_check.get(f"{stream}_sha256") !=
                        evidence.get(f"{relative}/{kind}/{stream}.txt")
                    for stream in ("stdout", "stderr"))):
            raise ValueError("corrected control Linux exit or output drift")
    rows = matrix.get("controls")
    if (not isinstance(rows, dict) or len(rows) != 12 or
            any(row.get("status") != "matched" for row in rows.values()) or
            sum(len(row.get("checks", [])) for row in rows.values()) != 20):
        raise ValueError("corrected control matrix incomplete")
    matrix_statuses = 0
    for control, row in rows.items():
        for check in row["checks"]:
            relative = f"matrix/{control}-g{check['round']}"
            grade_path = evidence_root / relative / "grade.json"
            if (file_sha(grade_path) != check.get("grade_sha256") or
                    evidence.get(f"{relative}/grade.json") != check.get("grade_sha256") or
                    check.get("exact_failures") is not True):
                raise ValueError("corrected control grade binding drift")
            grade = json.loads(grade_path.read_text(encoding="utf-8"))
            if grade.get("behavior_pass") != check.get("observed_pass"):
                raise ValueError("corrected control grade result drift")
            checks = grade.get("checks")
            if not isinstance(checks, list) or len(checks) not in (1, 2):
                raise ValueError("corrected control Go checks incomplete")
            for index, grade_check in enumerate(checks):
                verify_status(relative, grade_check, "go-test" if index == 0 else "race")
                matrix_statuses += 1
    extra = receipt.get("grades")
    if not isinstance(extra, dict) or set(extra) != {
            "candidate-hidden", "candidate-backend", "positive-backend",
            "alternative-backend"}:
        raise ValueError("corrected extra grades incomplete")
    extra_statuses = 0
    for relative, bound in extra.items():
        grade_path = evidence_root / relative / "grade.json"
        if (file_sha(grade_path) != bound.get("grade_sha256") or
                evidence.get(f"{relative}/grade.json") != bound.get("grade_sha256")):
            raise ValueError("corrected extra grade drift")
        grade = json.loads(grade_path.read_text(encoding="utf-8"))
        checks = grade.get("checks")
        if (grade.get("behavior_pass") != bound.get("behavior_pass") or
                grade.get("quality_pass") != bound.get("quality_pass") or
                not isinstance(checks, list) or
                [check.get("exit_code") for check in checks] != bound.get("exit_codes")):
            raise ValueError("corrected extra grade result drift")
        for index, grade_check in enumerate(checks):
            verify_status(relative, grade_check, "go-test" if index == 0 else "race")
            extra_statuses += 1
    if matrix_statuses != 22 or extra_statuses != 5:
        raise ValueError("corrected control status checks incomplete")

def pilot_plan(spec: dict) -> dict:
    """Verify the frozen one-pair plan and its offline evidence without a model turn."""
    descriptor = spec.get("pilot")
    if isinstance(descriptor, dict) and _standalone_plan_version(descriptor.get("path")) is not None:
        return _pilot_plan_standalone(spec)
    if isinstance(descriptor, dict) and descriptor.get("path") in (
            "pilot-plan-v10.json", "pilot-plan-v11.json"):
        return _pilot_plan_diagnostic(spec)
    if not isinstance(descriptor, dict) or descriptor.get("path") != "pilot-plan-v9.json":
        raise ValueError("frozen pilot descriptor missing")
    path = HERE / descriptor["path"]
    if not path.is_file() or file_sha(path) != descriptor.get("sha256"):
        raise ValueError("frozen pilot plan hash mismatch")
    plan = json.loads(path.read_text(encoding="utf-8"))
    limits = plan.get("limits_per_arm", {})
    if (descriptor.get("schema_version") != 1 or plan.get("schema_version") != 1 or
            plan.get("pilot_id") != "flipt-oci-long-horizon-pilot-09" or
            plan.get("arm_order") != ["baseline", "treatment"] or
            plan.get("matched_pairs") != 1 or
            plan.get("preparation_root") != "_scratch/pilot-09-prep" or
            plan.get("evidence_root") != "_scratch/pilot-09-evidence" or
            plan.get("quality_admission") != "_scratch/pilot-09-evidence/baseline-quality-admission.json" or
            plan.get("capabilities") != {
                "baseline": "_scratch/pilot-09-evidence/baseline-capability.json",
                "treatment": "_scratch/pilot-09-evidence/treatment-capability.json"} or
            plan.get("run_outputs") != {
                "baseline": "_scratch/pilot-09-evidence/baseline-live",
                "treatment": "_scratch/pilot-09-evidence/treatment-live"} or
            limits != {"planning_envelope_usd": PLANNING_USD,
                       "dispatch_stop_usd": STOP_USD,
                       "wall_seconds": MAX_SECONDS,
                       "cleanup_reserve_seconds": CLEANUP_RESERVE_SECONDS,
                       "automatic_retries": 0,
                       "public_repairs_per_round": 1} or
            spec.get("limits", {}).get("worker_retries_per_stage") != 0 or
            any(spec.get("limits", {}).get(key) != value for key, value in
                (("planning_envelope_usd", PLANNING_USD),
                 ("dispatch_stop_usd", STOP_USD),
                 ("wall_seconds", MAX_SECONDS),
                 ("cleanup_reserve_seconds", CLEANUP_RESERVE_SECONDS),
                 ("public_repairs_per_round", 1)))):
        raise ValueError("frozen pilot order or limits drift")
    fixture = plan.get("fixture", {})
    expected_fixture = {"source_sha256": spec["source_sha256"],
        "seed_patch_sha256": spec["seed_patch_sha256"],
        "start_tree": spec["start_tree"],
        "arm_execution_sha256": spec["arm_execution"]["sha256"],
        "baseline_suffix_sha256": spec["arm_execution"]["prompt_suffix_sha256"]["baseline"],
        "treatment_suffix_sha256": spec["arm_execution"]["prompt_suffix_sha256"]["treatment"],
        "routing_config_canonical_sha256": spec["routing_config"]["canonical_sha256"],
        "submit_checkpoint_sha256": spec["assets"]["submit_checkpoint.py"],
        "checkpoint_hash_sha256": spec["assets"]["checkpoint_hash.py"],
        "environment_sha256": spec["assets"]["environment.md"],
        "oracle_assets_sha256": {name: spec["assets"][name]
            for name in spec["oracle_erratum"]["changed_assets"]},
        "grader_sha256": file_sha(HERE / "grade.py"),
        "quality_admission_sha256": file_sha(HERE / "quality_admission.py"),
        "end_to_end_sha256": file_sha(HERE / "end_to_end.py")}
    if fixture != expected_fixture:
        raise ValueError("pilot fixture or arm policy drift")
    erratum = plan.get("oracle_erratum", {})
    adjudication_path = HERE / "controls/oracle-erratum-r9e-evidence/adjudication/checks.json"
    if (erratum.get("manifest_descriptor") != spec.get("oracle_erratum") or
            erratum.get("adjudication_path") !=
                "controls/oracle-erratum-r9e-evidence/adjudication/checks.json" or
            erratum.get("adjudication_sha256") != file_sha(adjudication_path) or
            erratum.get("adjudication_sha256") !=
                spec["oracle_erratum"]["adjudication_sha256"]):
        raise ValueError("pilot oracle erratum adjudication drift")
    expected_runtime = {key: spec["runtime"][key] for key in (
        "cli_sha256", "code_mode_host_sha256", "plugin_manifest_sha256",
        "plugin_hooks_sha256", "router_hook_sha256", "routing_validator_sha256")}
    if plan.get("runtime") != expected_runtime:
        raise ValueError("pilot runtime binding drift")
    if (plan.get("cancellation_adjudications") != PILOT_ADJUDICATION or
            any(file_sha(HERE / value["path"]) != value["sha256"]
                for value in PILOT_ADJUDICATION.values())):
        raise ValueError("pilot cancellation adjudication drift")
    metering = plan.get("metering", {})
    replay_path = HERE / "_scratch" / "meter-replay" / "replay-result.json"
    history_path = HERE / "_scratch" / "pilot-03-evidence" / "baseline-live" / "run.json"
    if (metering.get("strategy") != "event-or-one-second-cadence-v1" or
            metering.get("refresh_interval_seconds") != METER_REFRESH_INTERVAL_SECONDS or
            metering.get("runner_normalized_sha256") != _runner_source_sha256() or
            metering.get("transport_sha256") != file_sha(HERE / "transport.py") or
            metering.get("replay_path") != "_scratch/meter-replay/replay-result.json" or
            metering.get("replay_sha256") != file_sha(replay_path) or
            metering.get("historical_baseline_sha256") != file_sha(history_path)):
        raise ValueError("pilot metering source or replay drift")
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    legacy = replay.get("comparison", {}).get("legacy", {})
    optimized = replay.get("comparison", {}).get("optimized", {})
    if (replay.get("scope") != "offline harness processing only; no model latency claim" or
            replay.get("pilot_receipt_sha256") != metering["historical_baseline_sha256"] or
            any(legacy.get(key) != optimized.get(key) for key in (
                "frames", "cost_upper", "event_sha256", "model_calls",
                "session_ids", "terminals", "unknown_models", "unknown_usage")) or
            optimized.get("frames") != 8811 or optimized.get("model_calls") != 45 or
            legacy.get("refresh_count") != 17623 or
            optimized.get("refresh_count") != 55 or
            optimized.get("unknown_models") != [] or
            optimized.get("unknown_usage") != []):
        raise ValueError("pilot metering replay equivalence incomplete")
    calibration = plan.get("budget_calibration", {})
    previous_run = HERE / "_scratch/pilot-04-evidence/baseline-live/run.json"
    if (calibration.get("reviewed_raw_responses") != 111 or
            calibration.get("observed_dispatch_stop_usd") != STOP_USD or
            calibration.get("empirical_planning_envelope_usd") != PLANNING_USD or
            calibration.get("planning_envelope_is_billed_ceiling") is not False or
            calibration.get("pilot_04_baseline_sha256") != file_sha(previous_run) or
            calibration.get("pilot_03_baseline_sha256") !=
                metering["historical_baseline_sha256"] or
            calibration.get("if_baseline_fails") != "simplify-fixture-before-another-cap-change"):
        raise ValueError("pilot cost calibration or historical receipt drift")
    boundary = plan.get("end_to_end", {})
    if (boundary.get("start") != "runner-start-before-app-server-launch" or
            boundary.get("finish") != "after-hidden-race-backend-and-arm-blind-review" or
            boundary.get("receipts") != {
                "baseline": "_scratch/pilot-09-evidence/baseline-end-to-end.json",
                "treatment": "_scratch/pilot-09-evidence/treatment-end-to-end.json"} or
            boundary.get("same_boundary_both_arms") is not True or
            boundary.get("includes_post_run_quality_wall") is not True):
        raise ValueError("pilot end-to-end boundary incomplete")
    lineage = plan.get("lineage_accounting", {})
    source_paths = {
        "session_meter": HERE.parents[1] / "evals/scripts/run_paired_arm.py",
        "accounting": HERE / "accounting.py",
        "collector": HERE / "collect.py",
        "transport": HERE / "transport.py",
        "cancellation_adjudication": HERE / "cancellation_adjudication.py",
        "capability_builder": HERE / "live_preflight.py"}
    expected_sources = {name: file_sha(path) for name, path in source_paths.items()}
    pilot_replay_path = HERE / "_scratch/multiturn-accounting/pilot-07-offline-replay.json"
    canary_replay_path = HERE / "_scratch/multiturn-accounting/retained-canary-offline-replay.json"
    previous_baseline = HERE / "_scratch/pilot-07-evidence/baseline-live/run.json"
    previous_admission = HERE / "_scratch/pilot-07-evidence/baseline-quality-admission.json"
    previous_treatment = HERE / "_scratch/pilot-07-evidence/treatment-live/run.json"
    if (lineage.get("source_sha256") != expected_sources or
            lineage.get("pilot_replay_path") !=
                "_scratch/multiturn-accounting/pilot-07-offline-replay.json" or
            lineage.get("pilot_replay_sha256") != file_sha(pilot_replay_path) or
            lineage.get("canary_replay_path") !=
                "_scratch/multiturn-accounting/retained-canary-offline-replay.json" or
            lineage.get("canary_replay_sha256") != file_sha(canary_replay_path) or
            lineage.get("pilot_07_baseline_sha256") != file_sha(previous_baseline) or
            lineage.get("pilot_07_admission_sha256") != file_sha(previous_admission) or
            lineage.get("pilot_07_treatment_sha256") != file_sha(previous_treatment) or
            lineage.get("pilot_07_baseline_reused_for_comparison") is not False):
        raise ValueError("pilot native lineage or accounting source drift")
    pilot_replay = json.loads(pilot_replay_path.read_text(encoding="utf-8"))
    canary_replay = json.loads(canary_replay_path.read_text(encoding="utf-8"))
    if (pilot_replay.get("source_run_sha256") != lineage["pilot_07_treatment_sha256"] or
            pilot_replay.get("source_cost_status") != "UNKNOWN" or
            pilot_replay.get("replay_cost_status") != "complete" or
            pilot_replay.get("native_child_attempts") != 1 or
            pilot_replay.get("child_turn_counts") != [2] or
            pilot_replay.get("observed_response_count") != 112 or
            pilot_replay.get("observed_estimated_usd") != 1.7349132 or
            pilot_replay.get("issues") != [] or
            pilot_replay.get("benchmark_valid") is not False or
            pilot_replay.get("paid_calls_made") is not False or
            canary_replay.get("source_receipt_sha256") !=
                "40b99a0ed0dda9bb19807f9e466661fc7fe7120d203c887a59ca0c589608e8d5" or
            canary_replay.get("with_exact_proof_status") != "complete" or
            canary_replay.get("without_proof_status") != "UNKNOWN" or
            canary_replay.get("observed_response_count") != 10 or
            canary_replay.get("observed_estimated_usd") != 0.3768448 or
            canary_replay.get("issues_with_exact_proof") != [] or
            canary_replay.get("benchmark_valid") is not False or
            canary_replay.get("paid_calls_made") is not False):
        raise ValueError("pilot native lineage replay proof incomplete")
    _verify_corrected_controls(plan.get("controls", {}), spec)
    historical = plan.get("historical_diagnostic", {})
    prior_run = HERE / "_scratch/pilot-06-evidence/baseline-live/run.json"
    prior_grade = HERE / "_scratch/pilot-06-evidence/baseline-hidden-grade/grade.json"
    if (historical.get("run_sha256") != file_sha(prior_run) or
            historical.get("hidden_grade_sha256") != file_sha(prior_grade) or
            historical.get("hidden_grade_sha256") !=
                spec["oracle_erratum"]["historical_hidden_grade_sha256"] or
            historical.get("diagnostic_only") is not True or
            historical.get("matched_baseline_for_pilot_07") is not False):
        raise ValueError("historical pilot-06 diagnostic binding drift")
    acceptance = plan.get("acceptance", {})
    if (not isinstance(acceptance, dict) or
            acceptance.get("single_pair_performance_claim_allowed") is not False or
            any(acceptance.get(key) is not True for key in (
                "same_start_and_reveals", "complete_three_round_event_chain",
                "hidden_behavior_and_race", "pinned_backend_regressions",
                "arm_blind_patch_bound_semantic_retention_review",
                "complete_parent_and_child_usage", "quality_parity_and_total_usd_primary",
                "end_to_end_wall_includes_post_run_quality",
                "historical_baseline_excluded_from_comparison"))):
        raise ValueError("pilot acceptance contract incomplete")
    return plan


def _reject_comparator_fields(value: object) -> None:
    """A standalone plan cannot carry even an unused comparator admission."""
    if isinstance(value, dict):
        for key, child in value.items():
            if any(word in key.lower() for word in ("baseline", "comparator", "reference", "admission")):
                raise ValueError(f"standalone comparator field forbidden: {key}")
            _reject_comparator_fields(child)
    elif isinstance(value, list):
        for child in value:
            _reject_comparator_fields(child)


def _standalone_plan_version(name: object) -> int | None:
    match = re.fullmatch(r"pilot-plan-v(1[2-9]|[2-9][0-9]+)\.json", name or "") if isinstance(name, str) else None
    return int(match.group(1)) if match else None


def _standalone_source_hashes() -> dict[str, str]:
    names = ("common.py", "protocol.py", "controls.py", "grade.py", "prepare.py",
        "live_preflight.py", "accounting.py", "collect.py", "transport.py",
        "runtime_binding.py", "cancellation_adjudication.py", "fork_policy.py",
        "standalone_quality.py", "end_to_end.py")
    return {**{name: file_sha(HERE / name) for name in names},
            "evals/scripts/run_paired_arm.py": file_sha(HERE.parent / "scripts/run_paired_arm.py")}


def _verify_standalone_sources(plan: dict) -> None:
    if plan.get("runner_normalized_sha256") != _runner_source_sha256():
        raise ValueError("standalone runner source drift")
    if plan.get("execution_sources_sha256") != _standalone_source_hashes():
        raise ValueError("standalone execution source drift")


def _pilot_plan_standalone(spec: dict) -> dict:
    """Validate an explicit, treatment-only freeze with no comparator path."""
    descriptor = spec["pilot"]
    version = _standalone_plan_version(descriptor.get("path"))
    if version is None:
        raise ValueError("standalone plan filename invalid")
    path = HERE / descriptor["path"]
    if descriptor != {"path": path.name, "schema_version": 4, "sha256": file_sha(path)}:
        raise ValueError("standalone plan descriptor drift")
    plan = json.loads(path.read_text(encoding="utf-8"))
    _reject_comparator_fields(plan)
    if set(plan) != {"schema_version", "mode", "pilot_id", "claim", "arm_order",
                     "matched_pairs", "packet_scope_mode", "preparation_root",
                     "evidence_root", "capabilities", "run_outputs", "live_rebind",
                     "limits_per_arm", "cancellation_adjudications", "acceptance",
                     "end_to_end", "fixture", "runtime", "runner_normalized_sha256",
                     "execution_sources_sha256", "controls", "oracle_erratum", "stop_rule"}:
        raise ValueError("standalone plan has missing or unrecognized fields")
    if (plan.get("schema_version") != 4 or plan.get("mode") != "standalone-feasibility" or
            plan.get("pilot_id") != f"flipt-oci-long-horizon-pilot-{version:02d}" or
            plan.get("claim") != "treatment-only-feasibility" or
            plan.get("arm_order") != ["treatment"] or plan.get("matched_pairs") != 0 or
            plan.get("packet_scope_mode") != DIAGNOSTIC or
            re.fullmatch(rf"_scratch/pilot-{version:02d}-prep(?:-v[1-9][0-9]*)?",
                         plan.get("preparation_root", "")) is None or
            plan.get("evidence_root") != f"_scratch/pilot-{version:02d}-evidence" or
            plan.get("capabilities") != {"treatment": f"_scratch/pilot-{version:02d}-evidence/treatment-capability.json"} or
            plan.get("run_outputs") != {"treatment": f"_scratch/pilot-{version:02d}-evidence/treatment-live"} or
            plan.get("live_rebind") != {"mode": "no-model-rebind-v1",
                "expected_final_manifest_sha256_required": True,
                "turn_start_forbidden": True} or
            plan.get("limits_per_arm") != {"planning_envelope_usd": PLANNING_USD,
                "dispatch_stop_usd": STOP_USD, "wall_seconds": MAX_SECONDS,
                "cleanup_reserve_seconds": CLEANUP_RESERVE_SECONDS,
                "automatic_retries": 0, "public_repairs_per_round": 1} or
            any(spec.get("limits", {}).get(key) != value for key, value in (
                ("planning_envelope_usd", PLANNING_USD), ("dispatch_stop_usd", STOP_USD),
                ("wall_seconds", MAX_SECONDS), ("cleanup_reserve_seconds", CLEANUP_RESERVE_SECONDS),
                ("worker_retries_per_stage", 0), ("public_repairs_per_round", 1))) or
            plan.get("cancellation_adjudications") != {"treatment": PILOT_ADJUDICATION["treatment"]} or
            file_sha(HERE / PILOT_ADJUDICATION["treatment"]["path"]) != PILOT_ADJUDICATION["treatment"]["sha256"] or
            plan.get("acceptance") != {"complete_three_round_event_chain": True,
                "hidden_behavior_and_race": True, "pinned_backend_regressions": True,
                "arm_blind_patch_bound_semantic_retention_review": True,
                "complete_parent_and_child_usage": True,
                "end_to_end_wall_includes_post_run_quality": True,
                "performance_advantage_claim_allowed": False} or
            plan.get("end_to_end") != {"start": "runner-start-before-app-server-launch",
                "finish": "after-hidden-race-backend-and-arm-blind-review",
                "receipts": {"treatment": f"_scratch/pilot-{version:02d}-evidence/treatment-end-to-end.json"}}):
        raise ValueError("standalone plan schema, scope, or limits drift")
    expected_fixture = {"source_sha256": spec["source_sha256"],
        "seed_patch_sha256": spec["seed_patch_sha256"], "start_tree": spec["start_tree"],
        "arm_execution_sha256": spec["arm_execution"]["sha256"],
        "treatment_suffix_sha256": spec["arm_execution"]["prompt_suffix_sha256"]["treatment"],
        "routing_config_canonical_sha256": spec["routing_config"]["canonical_sha256"],
        "assets_sha256": spec["assets"], "grader_sha256": file_sha(HERE / "grade.py"),
        "end_to_end_sha256": file_sha(HERE / "end_to_end.py"),
        "fork_policy_sha256": file_sha(HERE / "fork_policy.py")}
    expected_runtime = {key: spec["runtime"][key] for key in (
        "cli_sha256", "code_mode_host_sha256", "plugin_manifest_sha256",
        "plugin_hooks_sha256", "router_hook_sha256", "routing_validator_sha256")}
    if plan.get("fixture") != expected_fixture or plan.get("runtime") != expected_runtime:
        raise ValueError("standalone fixture or runtime drift")
    _verify_standalone_sources(plan)
    controls = {"matrix_path": "controls/control-matrix-r9e-20261001.json",
        "matrix_sha256": file_sha(HERE / "controls/control-matrix-r9e-20261001.json"),
        "receipt_path": "controls/oracle-erratum-r9e-receipt.json",
        "receipt_sha256": file_sha(HERE / "controls/oracle-erratum-r9e-receipt.json"),
        "expected_controls": 12, "expected_grades": 20,
        "expected_linux_status_checks": 22, "expected_additional_status_checks": 5}
    if (plan.get("controls") != controls or plan.get("oracle_erratum") != {
            "manifest_descriptor": spec["oracle_erratum"],
            "adjudication_path": "controls/oracle-erratum-r9e-evidence/adjudication/checks.json",
            "adjudication_sha256": file_sha(HERE / "controls/oracle-erratum-r9e-evidence/adjudication/checks.json")}):
        raise ValueError("standalone oracle or controls drift")
    _verify_corrected_controls(controls, spec)
    return plan


def _pilot_plan_diagnostic(spec: dict) -> dict:
    """Validate the treatment-only diagnostic freeze before transport starts."""
    descriptor = spec["pilot"]
    version = 11 if descriptor.get("path") == "pilot-plan-v11.json" else 10
    schema = 3 if version == 11 else 2
    name = f"pilot-plan-v{version}.json"
    path = HERE / name
    if descriptor != {"path": name, "schema_version": schema,
                      "sha256": file_sha(path)}:
        raise ValueError("diagnostic treatment plan descriptor drift")
    plan = json.loads(path.read_text(encoding="utf-8"))
    if (plan.get("schema_version") != schema or
            plan.get("pilot_id") != f"flipt-oci-long-horizon-pilot-{version:02d}" or
            plan.get("claim") != "treatment-only-exploratory-feasibility" or
            plan.get("arm_order") != ["treatment"] or
            plan.get("matched_pairs") != 0 or
            plan.get("preparation_root") != f"_scratch/pilot-{version:02d}-prep" or
            plan.get("evidence_root") != f"_scratch/pilot-{version:02d}-evidence" or
            plan.get("capabilities") != {
                "treatment": f"_scratch/pilot-{version:02d}-evidence/treatment-capability.json"} or
            plan.get("run_outputs") != {
                "treatment": f"_scratch/pilot-{version:02d}-evidence/treatment-live"} or
            (plan.get("live_rebind") != {"mode": "no-model-rebind-v1",
                "expected_final_manifest_sha256_required": True,
                "turn_start_forbidden": True} if version == 11 else
             "live_rebind" in plan) or
            plan.get("limits_per_arm") != {
                "planning_envelope_usd": PLANNING_USD,
                "dispatch_stop_usd": STOP_USD,
                "wall_seconds": MAX_SECONDS,
                "cleanup_reserve_seconds": CLEANUP_RESERVE_SECONDS,
                "automatic_retries": 0,
                "public_repairs_per_round": 1} or
            any(spec.get("limits", {}).get(key) != value for key, value in (
                ("planning_envelope_usd", PLANNING_USD),
                ("dispatch_stop_usd", STOP_USD),
                ("wall_seconds", MAX_SECONDS),
                ("cleanup_reserve_seconds", CLEANUP_RESERVE_SECONDS),
                ("worker_retries_per_stage", 0),
                ("public_repairs_per_round", 1))) or
            plan.get("cancellation_adjudications") != {
                "treatment": PILOT_ADJUDICATION["treatment"]} or
            file_sha(HERE / PILOT_ADJUDICATION["treatment"]["path"]) !=
                PILOT_ADJUDICATION["treatment"]["sha256"] or
            plan.get("acceptance") != {
                "complete_three_round_event_chain": True,
                "hidden_behavior_and_race": True,
                "pinned_backend_regressions": True,
                "arm_blind_patch_bound_semantic_retention_review": True,
                "complete_parent_and_child_usage": True,
                "end_to_end_wall_includes_post_run_quality": True,
                "fresh_matched_pair": False,
                "performance_advantage_claim_allowed": False} or
            plan.get("end_to_end") != {
                "start": "runner-start-before-app-server-launch",
                "finish": "after-hidden-race-backend-and-arm-blind-review",
                "receipts": {"treatment":
                    f"_scratch/pilot-{version:02d}-evidence/treatment-end-to-end.json"}}):
        raise ValueError("diagnostic treatment plan schema, limits, or claim drift")
    fixture = plan.get("fixture", {})
    expected_fixture = {"source_sha256": spec["source_sha256"],
        "seed_patch_sha256": spec["seed_patch_sha256"],
        "start_tree": spec["start_tree"],
        "arm_execution_sha256": spec["arm_execution"]["sha256"],
        "treatment_suffix_sha256": spec["arm_execution"]["prompt_suffix_sha256"]["treatment"],
        "routing_config_canonical_sha256": spec["routing_config"]["canonical_sha256"],
        "assets_sha256": spec["assets"],
        "grader_sha256": file_sha(HERE / "grade.py"),
        "end_to_end_sha256": file_sha(HERE / "end_to_end.py"),
        "fork_policy_sha256": file_sha(HERE / "fork_policy.py")}
    if fixture != expected_fixture or plan.get("runtime") != {
            key: spec["runtime"][key] for key in (
                "cli_sha256", "code_mode_host_sha256", "plugin_manifest_sha256",
                "plugin_hooks_sha256", "router_hook_sha256", "routing_validator_sha256")}:
        raise ValueError("diagnostic treatment fixture or runtime drift")
    if plan.get("runner_normalized_sha256") != _runner_source_sha256():
        raise ValueError("diagnostic treatment runner source drift")
    execution_sources = {name: file_sha(HERE / name) for name in (
        "prepare.py", "live_preflight.py", "accounting.py", "collect.py",
        "transport.py", "runtime_binding.py", "cancellation_adjudication.py",
        "diagnostic_reference.py", "fork_policy.py", "quality_admission.py",
        "end_to_end.py")}
    if plan.get("execution_sources_sha256") != execution_sources:
        raise ValueError("diagnostic treatment execution source drift")
    expected_controls = {"matrix_path": "controls/control-matrix-r9e-20261001.json",
        "matrix_sha256": file_sha(HERE / "controls/control-matrix-r9e-20261001.json"),
        "receipt_path": "controls/oracle-erratum-r9e-receipt.json",
        "receipt_sha256": file_sha(HERE / "controls/oracle-erratum-r9e-receipt.json"),
        "expected_controls": 12, "expected_grades": 20,
        "expected_linux_status_checks": 22, "expected_additional_status_checks": 5}
    if (plan.get("controls") != expected_controls or
            plan.get("oracle_erratum") != {
                "manifest_descriptor": spec["oracle_erratum"],
                "adjudication_path": "controls/oracle-erratum-r9e-evidence/adjudication/checks.json",
                "adjudication_sha256": file_sha(HERE / "controls/oracle-erratum-r9e-evidence/adjudication/checks.json")}):
        raise ValueError("diagnostic corrected oracle or controls drift")
    _verify_corrected_controls(plan["controls"], spec)
    verify_diagnostic_reference(spec, plan)
    return plan


class ActiveTelemetryGuard:
    """Bound missing rollout, selector, child, and usage grace during a model turn."""

    def __init__(self, arm: str, workspace: Path | None = None,
                 packet_scope_mode: str = DIAGNOSTIC):
        self.arm = arm
        self.workspace = workspace
        self.packet_scope_mode = packet_scope_mode
        self.first_seen: dict[str, float] = {}
        self.child_usage_first_seen: dict[str, float] = {}

    def check(self, meter: SessionMeter, parent_id: str, model: str, effort: str,
              turn_started_at: float | None, calls_at_start: int,
              now: float | None = None, turn_id: str | None = None) -> None:
        if turn_started_at is None:
            return
        now = time.monotonic() if now is None else now
        if meter.unknown_models or meter.unknown_usage:
            raise TransportError("active turn has unpriced model or usage")
        parents = [state for state in meter.paths.values() if state["id"] == parent_id]
        if len(parents) != 1:
            if now - turn_started_at >= TELEMETRY_STARTUP_GRACE_SECONDS:
                raise TransportError("active turn parent rollout missing")
            return
        parent = parents[0]
        if self.arm == "treatment" and self.workspace is not None:
            parent_paths = [path for path, state in meter.paths.items()
                            if state["id"] == parent_id]
            if len(parent_paths) == 1 and parent_paths[0].is_file():
                _, fork_issues = validate_observed_spawns(
                    parent_paths[0], parent_id,
                    plans=self.workspace / ".benchmark" / "dispatch-plans",
                    child_rollouts={state["id"]: path for path, state in meter.paths.items()
                                    if state["id"] != parent_id}, mode=self.packet_scope_mode)
                if fork_issues:
                    raise TransportError("; ".join(fork_issues))
        if (not any(turn.get("model") == model and turn.get("effort") == effort and
                    (turn_id is None or turn.get("turn_id") == turn_id)
                    for turn in parent["turns"])):
            if now - turn_started_at >= TELEMETRY_STARTUP_GRACE_SECONDS:
                raise TransportError("active turn parent selector missing")
        if meter.calls <= calls_at_start and now - turn_started_at >= TELEMETRY_USAGE_GRACE_SECONDS:
            raise TransportError("active turn usage missing beyond grace")
        if self.arm == "baseline" and baseline_has_child(parent_id, list(meter.paths.values())):
            raise TransportError("baseline child session forbidden")
        issues = meter.dispatch_coverage_issues()
        for state in meter.paths.values():
            latest = state["turns"][-1] if state["turns"] else None
            if state["id"] != parent_id and (latest is None or
                    latest.get("model") is None or latest.get("effort") is None):
                issues.append(f"child {state['id']} selector missing")
            if state["id"] != parent_id and latest is not None and latest.get(
                    "calls", state["calls"]) == 0:
                key = f"{state['id']}:{latest.get('turn_id')}"
                first = self.child_usage_first_seen.setdefault(key, now)
                if now - first >= TELEMETRY_USAGE_GRACE_SECONDS:
                    raise TransportError(f"active child {state['id']} turn usage missing beyond grace")
        active_keys = {f"{state['id']}:{state['turns'][-1].get('turn_id')}"
            for state in meter.paths.values() if state["id"] != parent_id and
            state["turns"] and state["turns"][-1].get("calls", state["calls"]) == 0}
        self.child_usage_first_seen = {key: self.child_usage_first_seen[key]
            for key in active_keys if key in self.child_usage_first_seen}
        for issue in issues:
            first = self.first_seen.setdefault(issue, now)
            if now - first >= TELEMETRY_LINEAGE_GRACE_SECONDS:
                raise TransportError("active turn lineage unresolved: " + issue)
        self.first_seen = {issue: self.first_seen[issue] for issue in issues}


class MeterRefreshGate:
    """Refresh file-backed usage on priced events and at a bounded cadence."""

    def __init__(self, meter: SessionMeter,
                 interval: float = METER_REFRESH_INTERVAL_SECONDS):
        self.meter = meter
        self.interval = interval
        self.last_refresh: float | None = None

    @staticmethod
    def relevant(event: dict) -> bool:
        return meter_relevant_frame(event)

    def refresh(self, *, force: bool = False, now: float | None = None) -> bool:
        measured_now = now is None
        now = time.monotonic() if now is None else now
        if (force or self.last_refresh is None or
                now - self.last_refresh >= self.interval):
            self.meter.refresh()
            self.last_refresh = time.monotonic() if measured_now else now
            return True
        return False


def grade_before_deadline(candidate: Path, prepared: Path, output: Path,
                          round_id: int, deadline: float) -> dict:
    """Run the public grader with the remaining global wall budget."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TransportError("global wall limit before grade")
    command = [sys.executable, str(HERE / "grade.py"), "--candidate", str(candidate),
               "--prepared", str(prepared), "--output", str(output),
               "--round", str(round_id)]
    try:
        completed = subprocess.run(command, capture_output=True, text=True,
                                   timeout=min(900.0, remaining))
    except subprocess.TimeoutExpired as error:
        if not stop_wsl_grader(output):
            record_grader_hazard(prepared, output,
                                 "WSL grader cleanup unconfirmed after outer timeout")
            raise TransportError("public grade timed out; WSL cleanup UNKNOWN") from error
        raise TransportError("public grade timed out; WSL process tree confirmed stopped") from error
    if time.monotonic() >= deadline:
        raise TransportError("global wall limit before accepting grade")
    if completed.returncode:
        if any((output / step / "wsl-pgid.txt").exists() for step in ("go-test", "race")):
            if not stop_wsl_grader(output):
                record_grader_hazard(prepared, output,
                                     "WSL grader cleanup unconfirmed after grader failure")
                raise TransportError("public grade failed; WSL cleanup UNKNOWN")
        raise TransportError("public grade failed: " + completed.stderr[-500:])
    receipt = json.loads(completed.stdout)
    if not isinstance(receipt, dict) or receipt.get("round") != round_id:
        raise TransportError("public grade receipt invalid")
    return receipt


def _checkpoint_file(workspace: Path, round_id: int) -> Path:
    return workspace / ".benchmark" / "checkpoint.json"


def dry_run(prepared: Path, output: Path, arm: str, outcomes: list[bool]) -> dict:
    manifest()
    fresh_directory(output)
    workspace = prepared / arm
    if not workspace.is_dir():
        raise ValueError("arm workspace missing")
    preparation = verify_prepared(prepared, arm, manifest())
    parent_id = f"mock-{arm}-one-persistent-thread"
    def gate(patch: bytes, round_id: int, repair: int) -> dict:
        index = sum(event["kind"] == "gate" for event in machine.events)
        return {"round": round_id, "candidate_patch_sha256": sha(patch),
                "behavior_pass": outcomes[index], "mock": True}
    machine = StageMachine(workspace, arm, parent_id, gate)
    submitted = []
    for round_id in range(3):
        if machine.round != round_id:
            raise ValueError("dry-run stopped before expected reveal")
        checkpoint = submit_checkpoint(workspace, round_id,
            "Scripted transport checkpoint; actual git diff check, no model or Go result.",
            selected=("diff",))
        path = _checkpoint_file(workspace, round_id)
        response = machine.submit(parent_id, path)
        submitted.append({"round": round_id, "action": response["action"],
                          "patch_sha256": checkpoint["candidate_patch_sha256"]})
        if response["action"] != ("final" if round_id == 2 else "continue"):
            break
    result = machine.receipt()
    result["submissions"] = submitted
    result["mode"] = "mock-dry-run"
    result["model_calls"] = 0
    result["preparation"] = preparation
    write_json_new(output / "run.json", result)
    return result


def live_preflight(capability: Path | None, cli: Path | None, arm: str,
                   prepared: Path | None = None, *, allow_disabled: bool = False,
                   benchmark_mode: str | None = None) -> dict:
    spec = manifest()
    if not spec.get("live_enabled", False) and not allow_disabled:
        raise ValueError("fixture is not live-enabled")
    plan = pilot_plan(spec) if spec.get("pilot") else None
    if plan and plan.get("mode") == "standalone-feasibility":
        if benchmark_mode != "standalone-feasibility" or arm != "treatment":
            raise ValueError("standalone treatment requires explicit --mode standalone-feasibility")
    elif benchmark_mode is not None:
        raise ValueError("standalone mode requires a standalone plan")
    if arm == "treatment" and plan is not None:
        preflight_packet_scope(plan.get("packet_scope_mode"))
    if prepared is not None and (prepared / "grader-hazard.json").exists():
        raise ValueError("unconfirmed WSL grader process tree blocks both arms")
    if capability is None or cli is None or not capability.is_file() or not cli.is_file():
        raise ValueError("runtime capability and pinned CLI required")
    if prepared is None or not (prepared / arm).is_dir():
        raise ValueError("prepared arm workspace required")
    if plan and (prepared.resolve() != (HERE / plan["preparation_root"]).resolve() or
                 capability.resolve() != (HERE / plan["capabilities"][arm]).resolve()):
        raise ValueError("pilot preparation or capability path drift")
    if plan and file_sha(capability) != PILOT_CAPABILITY_SHA256[arm]:
        raise ValueError("frozen pilot capability hash mismatch")
    policy = arm_execution(spec, arm)
    cli_binding = verify_cli(cli, spec)
    preparation = verify_prepared(prepared, arm, spec)
    routing_binding = (verify_arm_config(prepared / arm, spec) if arm == "treatment" else None)
    runtime_probe = verify_arm_runtime(cli, prepared / arm, spec, arm)
    proof = json.loads(capability.read_text(encoding="utf-8"))
    if not isinstance(proof, dict):
        raise ValueError("persistent runtime capability proof invalid")
    canary = proof.get("capability") if isinstance(proof.get("capability"), dict) else {}
    cancellation = canary.get("cancellation") if isinstance(canary.get("cancellation"), dict) else {}
    evidence_path_value = canary.get("cancellation_receipt_path")
    evidence_path = Path(evidence_path_value) if isinstance(evidence_path_value, str) else None
    evidence = {}
    if (evidence_path is not None and evidence_path.is_absolute() and
            evidence_path.is_file() and
            canary.get("cancellation_receipt_sha256") == file_sha(evidence_path)):
        try:
            parsed = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence = parsed if isinstance(parsed, dict) else {}
        except (ValueError, OSError):
            evidence = {}
    evidence_usage = evidence.get("usage") if isinstance(evidence.get("usage"), dict) else {}
    evidence_cancellation = (evidence.get("cancellation")
        if isinstance(evidence.get("cancellation"), dict) else {})
    evidence_sessions = evidence_usage.get("sessions")
    if not isinstance(evidence_sessions, list):
        evidence_sessions = []
    evidence_parent = evidence.get("parent_thread_id")
    accounting_issues = evidence.get("accounting_issues")
    evidence_children = [item for item in evidence_sessions if isinstance(item, dict)
                         and item.get("id") != evidence_parent]
    evidence_turn = evidence.get("turn_id")
    interrupt_requests = evidence_cancellation.get("interrupt_requests")
    baseline_parent_stop = (arm != "baseline" or
        isinstance(evidence_turn, str) and bool(evidence_turn) and
        isinstance(interrupt_requests, list) and len(interrupt_requests) == 1 and
        isinstance(interrupt_requests[0], dict) and
        interrupt_requests[0].get("thread_id") == evidence_parent and
        interrupt_requests[0].get("turn_id") == evidence_turn and
        type(interrupt_requests[0].get("dispatch_time_ns")) is int and
        evidence_cancellation.get("turn_id") == evidence_turn and
        len(evidence_sessions) == 1 and
        evidence_sessions[0].get("terminal") == "turn_aborted")
    native_attempts = evidence.get("native_attempts")
    native_bound = (native_attempts == [] if arm == "baseline" else
        isinstance(native_attempts, list) and len(native_attempts) == 1 and
        isinstance(native_attempts[0], dict) and len(evidence_children) == 1 and
        native_attempts[0].get("child_id") == evidence_children[0].get("id") and
        native_attempts[0].get("turn_id") == evidence.get("turn_id") and
        native_attempts[0].get("model") == "gpt-6-astra" and
        native_attempts[0].get("effort") == "xhigh" and
        all(type(native_attempts[0].get(key)) is int for key in
            ("call_line", "start_line", "result_line")) and
        native_attempts[0]["call_line"] < native_attempts[0]["start_line"] <
        native_attempts[0]["result_line"])
    usage_bound = (isinstance(evidence_parent, str) and evidence_parent and
        all(isinstance(item, dict) for item in evidence_sessions) and
        len([item for item in evidence_sessions if isinstance(item, dict) and
             item.get("id") == evidence_parent]) == 1 and
        len(evidence_children) == (1 if arm == "treatment" else 0) and
        all(type(item.get("calls")) is int and item["calls"] > 0 and
            isinstance(item.get("reported_total_usage"), dict) and
            item.get("terminal") == "turn_aborted"
            for item in evidence_sessions if isinstance(item, dict)) and
        (arm == "baseline" or (evidence_children[0].get("model") == "gpt-6-astra" and
                               evidence_children[0].get("effort") == "xhigh")) and
        type(evidence.get("wall_seconds")) in (int, float) and
        0 <= evidence["wall_seconds"] <= 180 and
        type(evidence_usage.get("estimated_usd_upper_bound")) in (int, float) and
        0 <= evidence_usage["estimated_usd_upper_bound"] <= 1.0)
    required_runtime = (runtime_probe.get("arm") == arm and
        runtime_probe.get("cli_options") == policy["cli_options"] and
        runtime_probe.get("policy_sha256") == policy["policy_sha256"] and
        runtime_probe.get("prompt_suffix_sha256") == policy["prompt_suffix_sha256"] and
        runtime_probe.get("router_hook_count") == (5 if arm == "treatment" else 0) and
        runtime_probe.get("model_turns") == 0 and
        runtime_probe.get("thread_started_without_turn") is True and
        isinstance(runtime_probe.get("protocol_contract"), dict) and
        runtime_probe["protocol_contract"].get("interrupt_method") == "turn/interrupt" and
        all(isinstance(runtime_probe["protocol_contract"].get(field), str)
            for field in ("client_request_sha256", "dispatch_contract_sha256",
                          "installed_hook_closure_sha256")) and
        isinstance(runtime_probe.get("model_catalog"), dict) and
        runtime_probe["model_catalog"].get(policy["model"]) == policy["effort"] and
        (arm == "baseline" or runtime_probe["model_catalog"].get("gpt-6-astra") == "xhigh"))
    historical_runtime = evidence.get("runtime_binding")
    historical_runtime_bound = (isinstance(historical_runtime, dict) and
        evidence.get("runtime_binding_sha256") == _canonical_sha(historical_runtime) and
        _canary_runtime_equivalent(historical_runtime, runtime_probe, plan))
    capability_bound = (canary.get("status") == "verified" and
        canary.get("arm") == arm and canary.get("model") == policy["model"] and
        canary.get("effort") == policy["effort"] and
        canary.get("runtime_binding_sha256") == _canonical_sha(runtime_probe) and
        isinstance(canary.get("canary_id"), str) and canary["canary_id"] and
        cancellation.get("status") == "verified" and
        cancellation.get("interrupt_ack") is True and
        cancellation.get("turn_completed") is True and
        cancellation.get("usage_drained") is True and
        cancellation.get("descendants_drained") is True and
        cancellation.get("real_child_observed") is (arm == "treatment") and
        (arm == "baseline" or (cancellation.get("child_model") == "gpt-6-astra" and
                                cancellation.get("child_effort") == "xhigh")) and
        evidence.get("kind") == "cancellation-capability" and
        evidence.get("status") == "UNKNOWN" and
        evidence.get("arm") == arm and
        evidence.get("model") == policy["model"] and
        evidence.get("effort") == policy["effort"] and
        historical_runtime_bound and
        isinstance(accounting_issues, list) and
        accounting_issues == ["parent session lacks successful terminal result"] and
        usage_bound and native_bound and baseline_parent_stop and
        evidence.get("real_child_observed") is (arm == "treatment") and
        evidence_cancellation.get("status") == "verified-drained" and
        evidence_cancellation.get("interrupt_ack") is True and
        evidence_cancellation.get("turn_completed") is True and
        evidence_cancellation.get("usage_drained") is True and
        evidence_cancellation.get("descendants_drained") is True)
    if arm in ("baseline", "treatment"):
        from evals.long_horizon_v1.cancellation_adjudication import (
            _judge, prior_adjudication_bound)
        adjudication_path_value = canary.get("cancellation_adjudication_path")
        sessions_path_value = canary.get("cancellation_sessions_path")
        adjudication_path = (Path(adjudication_path_value)
            if isinstance(adjudication_path_value, str) else None)
        sessions_path = (Path(sessions_path_value)
            if isinstance(sessions_path_value, str) else None)
        adjudication = None
        if (adjudication_path is not None and adjudication_path.is_absolute() and
                adjudication_path.is_file() and sessions_path is not None and
                sessions_path.is_absolute() and sessions_path.is_dir() and
                canary.get("cancellation_adjudication_sha256") == file_sha(adjudication_path)):
            try:
                adjudication = json.loads(adjudication_path.read_text(encoding="utf-8"))
                verified_proof = _judge(evidence, sessions_path, evidence_path)
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                adjudication = None
        capability_bound = (capability_bound and isinstance(adjudication, dict) and
            adjudication.get("schema_version") == 2 and
            adjudication.get("kind") == "cancellation-adjudication" and
            adjudication.get("status") == "verified" and
            adjudication.get("source_receipt_status") == "UNKNOWN" and
            adjudication.get("source_receipt_sha256") == file_sha(evidence_path) and
            adjudication.get("arm") == arm and
            adjudication.get("live_enabled") is False and
            adjudication.get("reason") is None and
            adjudication.get("proof") == verified_proof and
            prior_adjudication_bound(adjudication, file_sha(evidence_path),
                                     verified_proof) and
            verified_proof.get("parent_rollout", {}).get("session_id") == evidence_parent and
            (verified_proof.get("parent_turn_id") == evidence_turn and
             "child_rollout" not in verified_proof if arm == "baseline" else
             bool(evidence_children) and
             verified_proof.get("child_rollout", {}).get("session_id") ==
                 evidence_children[0].get("id")))
    if (proof.get("status") != "verified" or
            proof.get("cli_sha256") != cli_binding["cli_sha256"] or
            proof.get("code_mode_host_sha256") != cli_binding["code_mode_host_sha256"] or
            proof.get("routing_config_canonical_sha256") != (
                routing_binding["canonical_sha256"] if routing_binding else None) or
            proof.get("cli_options") != policy["cli_options"] or
            proof.get("arm_execution_policy_sha256") != policy["policy_sha256"] or
            proof.get("prompt_suffix_sha256") != policy["prompt_suffix_sha256"] or
            proof.get("native_spawn_available") is not (arm == "treatment") or
            proof.get("router_hooks_verified") is not (arm == "treatment") or
            proof.get("preparation_sha256") != preparation["preparation_sha256"] or
            proof.get("multi_turn_same_thread") is not True or
            proof.get("child_usage_complete") is not True or
            proof.get("cancellation_verified") is not True or
            proof.get("selector_verified") is not True or
            proof.get("model") != parent_selector(arm)[0] or
            proof.get("effort") != parent_selector(arm)[1] or
            not required_runtime or not capability_bound):
        raise ValueError("persistent runtime capability proof incomplete or selector drift")
    return {**proof, "zero_model_runtime_binding": runtime_probe,
            "preparation": preparation}


def live_run(prepared: Path, output: Path, arm: str, cli: Path,
             capability: Path, session_root: Path,
             benchmark_mode: str | None = None) -> dict:
    spec = manifest()
    plan = None
    if spec.get("pilot"):
        plan = pilot_plan(spec)
        if plan.get("mode") == "standalone-feasibility":
            if benchmark_mode != "standalone-feasibility" or arm != "treatment":
                raise ValueError("standalone treatment requires explicit --mode standalone-feasibility")
        elif benchmark_mode is not None:
            raise ValueError("standalone mode requires a standalone plan")
        if output.resolve() != (HERE / plan["run_outputs"][arm]).resolve():
            raise ValueError("pilot output path drift")
        if arm == "treatment":
            if plan.get("mode") == "standalone-feasibility":
                pass
            elif plan.get("schema_version") in (2, 3):
                verify_diagnostic_reference(spec, plan)
            else:
                verify_quality_admission(prepared, plan)
    proof = live_preflight(capability, cli, arm, prepared,
                           benchmark_mode=benchmark_mode)
    if plan and arm == "treatment":
        if plan.get("mode") == "standalone-feasibility":
            pass
        elif plan.get("schema_version") in (2, 3):
            verify_diagnostic_reference(spec, plan)
        else:
            verify_quality_admission(prepared, plan)
        preflight_packet_scope(plan.get("packet_scope_mode"))
    fresh_directory(output)
    workspace = prepared / arm
    spec = manifest()
    policy = arm_execution(spec, arm)
    model, effort = policy["model"], policy["effort"]
    start = time.monotonic()
    started_utc_ns = time.time_ns()
    deadline = start + MAX_SECONDS
    operational_deadline = deadline - CLEANUP_RESERVE_SECONDS
    drain_deadline = deadline - RECEIPT_RESERVE_SECONDS
    transport = None
    meter = None
    refresh_gate = None
    stop_reason = None
    cancellation = {"status": "not-needed"}
    interruption_target = None
    interruption_pre_evidence = None
    interruption_proof = None
    telemetry = ActiveTelemetryGuard(arm, workspace,
        plan.get("packet_scope_mode") if plan and arm == "treatment" else DIAGNOSTIC)
    turn_calls_at_start = 0
    attempts: list[dict] = []
    shared_initial_prompt_sha256 = None
    def check_budget(*, force: bool = False):
        nonlocal stop_reason
        if time.monotonic() >= operational_deadline:
            stop_reason = "wall-limit"
            raise TransportError(stop_reason)
        if meter is not None:
            refreshed = refresh_gate.refresh(force=force)
            if time.monotonic() >= operational_deadline:
                stop_reason = "wall-limit"
                raise TransportError(stop_reason)
            if refreshed and arm == "treatment" and transport is not None:
                parent_paths = [path for path, state in meter.paths.items()
                                if state["id"] == transport.thread_id]
                if len(parent_paths) == 1 and parent_paths[0].is_file():
                    _, fork_issues = validate_observed_spawns(
                        parent_paths[0], transport.thread_id,
                        plans=workspace / ".benchmark" / "dispatch-plans",
                        child_rollouts={state["id"]: path for path, state in meter.paths.items()
                                        if state["id"] != transport.thread_id},
                        mode=plan["packet_scope_mode"])
                    if fork_issues:
                        stop_reason = FORK_POLICY_ERROR
                        raise TransportError(FORK_POLICY_ERROR + ": " + "; ".join(fork_issues))
            if meter.unknown_models or meter.unknown_usage:
                stop_reason = "usage-integrity-failure"
                raise TransportError(stop_reason)
            if meter.cost_upper >= STOP_USD:
                stop_reason = "cost-safety-threshold"
                raise TransportError(stop_reason)
            if (refreshed and transport is not None and
                    not transport.active_turn_complete):
                telemetry.check(meter, transport.thread_id, model, effort,
                                transport.turn_started_at, turn_calls_at_start,
                                turn_id=transport.active_turn_id)
    def check_frame(event: dict) -> None:
        if refresh_gate is not None and refresh_gate.relevant(event):
            check_budget(force=True)
    try:
        transport = AppServerTransport(cli, workspace, policy["cli_options"],
                                       model, effort, operational_deadline)
        thread_id = transport.start()
        meter = SessionMeter(session_root, thread_id, model, effort,
                             require_native_activity=(arm == "treatment"))
        refresh_gate = MeterRefreshGate(meter)
        gate_number = 0
        def gate(patch: bytes, round_id: int, repair: int) -> dict:
            nonlocal gate_number
            gate_number += 1
            patch_path = output / f"candidate-g{round_id}-attempt{repair}.patch"
            patch_path.write_bytes(patch)
            check_budget()
            receipt = grade_before_deadline(patch_path, prepared,
                                            output / f"gate-{gate_number}", round_id,
                                            operational_deadline)
            check_budget()
            return receipt
        machine = StageMachine(workspace, arm, thread_id, gate)
        shared_prompt = ((ASSET_ROOT / "round0.md").read_text(encoding="utf-8") + "\n\n" +
                         (ASSET_ROOT / "environment.md").read_text(encoding="utf-8"))
        shared_initial_prompt_sha256 = sha(shared_prompt.encode("utf-8"))
        prompt = submitted_prompt(shared_prompt, arm, spec)
        while True:
            check_budget(force=True)
            verify_cli(cli, manifest())
            if arm == "treatment":
                verify_arm_config(workspace, manifest())
            turn_calls_at_start = meter.calls
            turn = transport.turn(prompt, check_budget, check_frame)
            if arm == "treatment":
                verify_arm_config(workspace, manifest())
            if turn["thread_id"] != thread_id or turn["status"] != "completed":
                stop_reason = "turn-failed-or-lineage-changed"
                break
            meter.refresh()
            if arm == "treatment":
                child_grace_end = min(operational_deadline, time.monotonic() + 10.0)
                while (any(state["id"] != thread_id and state["terminal"] not in
                           ("task_complete", "task_completed", "task_failed",
                            "task_cancelled", "task_cancel")
                           for state in meter.paths.values()) and
                       time.monotonic() < child_grace_end):
                    check_budget()
                    time.sleep(0.25)
                    meter.refresh()
                if any(state["id"] != thread_id and state["terminal"] not in
                       ("task_complete", "task_completed", "task_failed",
                        "task_cancelled", "task_cancel")
                       for state in meter.paths.values()):
                    raise TransportError("descendant remains active after completed parent turn")
            parent_sessions = [state for state in meter.paths.values()
                               if state["id"] == thread_id]
            parent_paths = [path for path, state in meter.paths.items()
                            if state["id"] == thread_id]
            lineage_issues = (native_lineage(parent_paths[0], meter.paths, thread_id,
                                             closed_snapshot=True)[1]
                              if arm == "treatment" and len(parent_paths) == 1 else [])
            unexpected_children = (arm == "baseline" and
                                   baseline_has_child(thread_id, list(meter.paths.values())))
            if (len(parent_sessions) != 1 or unexpected_children or
                    not any((entry["turn_id"], entry["model"], entry["effort"]) ==
                            (turn["turn_id"], model, effort)
                            for entry in parent_sessions[0]["turns"][-1:]) or
                    meter.dispatch_coverage_issues() or lineage_issues or meter.unknown_models or
                    meter.unknown_usage):
                raise TransportError("session, selector, or child identity missing after completed turn")
            stage_event_start = len(machine.events)
            round_id, repair = machine.round, machine.repair
            action = machine.submit(thread_id, _checkpoint_file(workspace, machine.round))
            check_budget()
            completed = [event for event in transport.events
                         if event["method"] == "turn/completed" and
                         event["thread_id"] == thread_id and
                         event["turn_id"] == turn["turn_id"]]
            if len(completed) != 1 or any(item["turn_id"] == turn["turn_id"]
                                          for item in attempts):
                raise TransportError("turn completion identity missing or duplicated")
            attempts.append({"round": round_id, "repair": repair,
                             "turn_id": turn["turn_id"], "thread_id": thread_id,
                             "shared_prompt_sha256": sha(shared_prompt.encode("utf-8")),
                             "submitted_prompt_sha256": sha(prompt.encode("utf-8")),
                             "completion_time_ns": completed[0]["time_ns"],
                             "stage_event_start": stage_event_start,
                             "stage_event_end": len(machine.events)})
            if action["action"] in ("stop", "final"):
                stop_reason = "accepted-final" if action["action"] == "final" else action["reason"]
                break
            shared_prompt = (action["message"] if action["action"] == "repair" else
                             action["report"] + "\n\nPublic gate receipt:\n" +
                             json.dumps(action["receipt"], sort_keys=True))
            prompt = submitted_prompt(shared_prompt, arm, spec)
    except (TransportError, ValueError, RuntimeError, OSError) as error:
        stop_reason = str(error)
    finally:
        if transport is not None:
            try:
                if meter is not None:
                    meter.refresh()
                if (not transport.active_turn_complete or
                        (meter is not None and any(state["id"] != transport.thread_id and
                            state["terminal"] not in ("task_complete", "task_completed",
                                                      "task_failed", "task_cancelled", "task_cancel")
                            for state in meter.paths.values())) or
                        (meter is not None and
                         getattr(transport, "active_turn_id", None) is not None and
                         stop_reason != "accepted-final")):
                    def before_interrupt(target_thread: str, target_turn: str) -> dict | None:
                        nonlocal interruption_target, interruption_pre_evidence
                        if arm != "treatment" or meter is None:
                            return None
                        meter.refresh()
                        states = list(meter.paths.values())
                        if target_thread == transport.thread_id:
                            # The parent can carry the optional single-child
                            # proof marker. It is never a gate for child cleanup.
                            child_ids = {state.get("id") for state in states
                                         if state.get("id") != transport.thread_id}
                            return (interruption_pre_evidence if
                                not transport.active_turn_complete and
                                target_turn == transport.active_turn_id and
                                interruption_target is not None and
                                child_ids == {interruption_target["target_thread_id"]}
                                else None)
                        def exact_active_descendant(rows: list[dict]) -> bool:
                            ids = [state.get("id") for state in rows]
                            if ids.count(target_thread) != 1:
                                return False
                            by_id = {state.get("id"): state for state in rows}
                            child = by_id[target_thread]
                            turns = child.get("turns") or []
                            if (child.get("terminal") is not None or not turns or
                                    not isinstance(target_turn, str) or not target_turn or
                                    turns[-1].get("turn_id") != target_turn or
                                    turns[-1].get("terminal") is not None):
                                return False
                            ancestor = child.get("parent_id")
                            seen = {target_thread}
                            while ancestor != transport.thread_id:
                                if ancestor in seen or ids.count(ancestor) != 1:
                                    return False
                                seen.add(ancestor)
                                ancestor = by_id[ancestor].get("parent_id")
                            return True
                        if not exact_active_descendant(states):
                            return None
                        observed_ns = time.time_ns()
                        meter.refresh()
                        if not exact_active_descendant(list(meter.paths.values())):
                            return None
                        identity = {"target_thread_id": target_thread,
                                    "target_turn_id": target_turn}
                        evidence = {**identity, "parent_thread_id": transport.thread_id,
                            "marker_observed_ns": observed_ns,
                            "completion_absent_checked_ns": time.time_ns()}
                        if interruption_target is None:
                            interruption_target = identity
                            interruption_pre_evidence = evidence
                        return evidence
                    cancellation = transport.cancel_and_drain(
                        meter, absolute_deadline=drain_deadline,
                        before_interrupt=before_interrupt if arm == "treatment" else None,
                        descendants_first=(arm == "treatment"))
            except (RuntimeError, OSError, ValueError) as error:
                cancellation = {"status": "UNKNOWN", "reason": f"cancellation failed: {error}"}
                stop_reason = stop_reason or f"cancellation failed: {error}"
            try:
                transport.close()
            except (RuntimeError, OSError) as error:
                stop_reason = stop_reason or f"transport close failed: {error}"
                cancellation = {**cancellation, "status": "UNKNOWN",
                                "close_error": str(error)}
    if meter is not None:
        try:
            if (arm == "treatment" and transport is not None and
                    interruption_target is not None and thread_id is not None):
                meter.refresh()
                child_states = [state for state in meter.paths.values()
                                if state.get("id") != thread_id]
                requests = cancellation.get("interrupt_requests") or []
                child_target = (interruption_target["target_thread_id"],
                                interruption_target["target_turn_id"])
                expected = [child_target, (thread_id, transport.active_turn_id)]
                if (cancellation.get("status") == "verified-drained" and
                        len(child_states) == 1 and
                        child_states[0].get("id") == child_target[0] and
                        not meter.dispatch_coverage_issues() and
                        not meter.unknown_models and not meter.unknown_usage and
                        len(requests) == 2 and all(
                            isinstance(row, dict) for row in requests) and
                        [(row.get("thread_id"), row.get("turn_id"))
                         for row in requests] == expected and
                        all(isinstance(row.get("pre_dispatch_evidence"), dict) and
                            (row["pre_dispatch_evidence"].get("target_thread_id"),
                             row["pre_dispatch_evidence"].get("target_turn_id")) ==
                            child_target for row in requests)):
                    interruption_proof = {"parent_thread_id": thread_id,
                        "turn_id": transport.active_turn_id,
                        "trigger_evidence": interruption_target,
                        "cancellation": cancellation, "usage": meter.summary()}
            observed = account(session_root, thread_id, model, effort,
                               arm == "treatment", interruption_proof=interruption_proof,
                               fork_policy_plans=(workspace / ".benchmark" / "dispatch-plans"
                                                  if arm == "treatment" else None),
                               packet_scope_mode=(plan["packet_scope_mode"] if arm == "treatment"
                                                  else DIAGNOSTIC))
            usage = observed["summary"]
            issues = observed["issues"]
        except (RuntimeError, OSError, ValueError) as error:
            observed = None
            meter.refresh()
            usage = meter.summary()
            issues = [f"final accounting failed: {error}"]
        if arm == "baseline" and baseline_has_child(thread_id, usage["sessions"]):
            issues.append("baseline child session forbidden")
        if (arm == "treatment" and cancellation.get("status") == "verified-drained" and
                interruption_proof is None):
            issues.append("cancellation lacks supported single-child interruption proof")
        if any(issue.startswith(FORK_POLICY_ERROR) for issue in issues):
            stop_reason = FORK_POLICY_ERROR
    else:
        observed = None
        usage, issues = None, ["parent session never started"]
    if time.monotonic() >= deadline:
        issues.append("global wall limit exceeded during cleanup")
        if stop_reason == "accepted-final":
            stop_reason = "wall-limit-after-final"
    if (prepared / "grader-hazard.json").exists():
        issues.append("WSL grader process tree cleanup UNKNOWN")
    fork_evidence = None
    if arm == "treatment":
        calls, fork_issues = final_fork_observation(
            meter, thread_id, workspace,
            observed["native_attempts"] if observed else None,
            mode=plan["packet_scope_mode"])
        for issue in fork_issues:
            if issue not in issues:
                issues.append(issue)
        if fork_issues:
            stop_reason = FORK_POLICY_ERROR
        fork_evidence = {"policy": "bounded-fork-v2",
                         "status": "failed" if fork_issues else "observed",
                         "issues": fork_issues, "spawns": calls,
                         "packet_scope": ("VERIFIED" if calls and all(
                             call.get("packet_scope") == "VERIFIED" for call in calls)
                             else "UNKNOWN"),
                         "known_estimated_usd_lower_bound":
                             usage.get("estimated_usd_lower_bound") if usage else None,
                         "cost_status": "UNKNOWN" if issues else "complete"}
    result = {"schema_version": 1, "task_id": TASK_ID, "arm": arm,
              "started_utc_ns": started_utc_ns,
              "parent_thread_id": transport.thread_id if transport else None,
              "stop_reason": stop_reason or "run-ended-without-terminal-result",
              "wall_seconds": round(time.monotonic() - start, 3),
              "usage": usage, "usage_issues": issues,
              "cancellation": cancellation,
              "interruption_proof": (interruption_proof if observed and
                  observed.get("interruption_verified") else None),
              "per_turn_responses": observed["responses"] if observed else None,
              "by_model": observed["by_model"] if observed else None,
              "failed_child_attempts": observed["failed_child_attempts"] if observed else None,
              "native_attempts": observed["native_attempts"] if observed else None,
              "parent_rollout_path": (str(next((path for path, state in meter.paths.items()
                  if state["id"] == thread_id), "")) if meter is not None and thread_id else None),
              "dispatch_plans_path": (str(workspace / ".benchmark" / "dispatch-plans")
                                      if arm == "treatment" else None),
              "fork_policy": fork_evidence,
              "packet_scope_mode": plan.get("packet_scope_mode") if plan else None,
              "benchmark_mode": benchmark_mode,
              "claim_class": "standalone-treatment-feasibility" if
                  benchmark_mode == "standalone-feasibility" else "non-matched-non-interleaved-diagnostic" if
                  plan and plan.get("packet_scope_mode") == DIAGNOSTIC else "strict-selective",
              "child_rollout_paths": ({state["id"]: str(path) for path, state in
                  meter.paths.items() if state["id"] != thread_id} if meter is not None else {}),
              "attempts": attempts,
              "shared_initial_prompt_sha256": shared_initial_prompt_sha256,
              "transport_events": transport.events if transport else [],
              "stage": machine.receipt() if "machine" in locals() else None,
              "cost_status": "complete" if usage and not issues and
                  cancellation.get("status") != "UNKNOWN" else "UNKNOWN"}
    result["runtime_binding"] = {"cli_sha256": proof["cli_sha256"],
        "code_mode_host_sha256": proof["code_mode_host_sha256"],
        "routing_config_canonical_sha256": proof["routing_config_canonical_sha256"],
        "arm_execution_policy_sha256": policy["policy_sha256"],
        "prompt_suffix_sha256": policy["prompt_suffix_sha256"],
        "preparation_sha256": proof["preparation"]["preparation_sha256"],
        "zero_model_runtime_binding": proof["zero_model_runtime_binding"]}
    write_json_new(output / "run.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--arm", required=True, choices=("baseline", "treatment"))
    parser.add_argument("--mode", choices=("standalone-feasibility",),
                        help="Explicit treatment-only feasibility mode")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--preflight-only", action="store_true",
                      help="Check a live arm without starting a model turn")
    parser.add_argument("--cli", type=Path)
    parser.add_argument("--capability", type=Path)
    parser.add_argument("--session-root", type=Path,
                        default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "sessions")
    args = parser.parse_args()
    if args.dry_run:
        result = dry_run(args.prepared.resolve(), args.output.resolve(), args.arm,
                         [True, True, True])
    else:
        if args.cli is None or args.capability is None:
            parser.error("--live and --preflight-only require --cli and --capability")
        if args.preflight_only:
            proof = live_preflight(args.capability.resolve(), args.cli.resolve(),
                                   args.arm, args.prepared.resolve(), allow_disabled=True,
                                   benchmark_mode=args.mode)
            fresh_directory(args.output.resolve())
            result = {"schema_version": 1, "mode": "no-spend-live-preflight",
                      "arm": args.arm, "model_turns": 0, "status": "passed",
                      "benchmark_mode": args.mode,
                      "manifest_sha256": file_sha(HERE / "manifest.json"),
                      "pilot_plan_sha256": manifest()["pilot"]["sha256"],
                      "capability_sha256": file_sha(args.capability.resolve()),
                      "preparation": proof["preparation"],
                      "zero_model_runtime_binding": proof["zero_model_runtime_binding"]}
            write_json_new(args.output.resolve() / "preflight.json", result)
        else:
            result = live_run(args.prepared.resolve(), args.output.resolve(), args.arm,
                              args.cli.resolve(), args.capability.resolve(),
                              args.session_root.resolve(), benchmark_mode=args.mode)
    print(json.dumps({"output": str(args.output), "mode": result.get("mode", "live"),
                      "complete": result.get("status") == "passed" if args.preflight_only
                      else result.get("complete", result.get("stop_reason") == "accepted-final")},
                     indent=2))
