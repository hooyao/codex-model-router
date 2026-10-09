"""Bind the independently admitted v17 baseline as an explicit fixed comparator."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .common import ASSET_ROOT, HERE, file_sha, sha


CORE_SOURCES = (
    "common.py", "protocol.py", "controls.py", "grade.py", "accounting.py",
    "transport.py", "runtime_binding.py", "cancellation_adjudication.py",
    "fork_policy.py", "standalone_quality.py", "recovery_quality.py",
    "end_to_end.py", "evals/scripts/run_paired_arm.py")
METADATA_SOURCES = ("prepare.py", "live_preflight.py", "collect.py")


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"fixed baseline evidence is not an object: {path}")
    return value


def verify_fixed_baseline(spec: dict, plan: dict) -> dict:
    """Fail closed on source, task, policy, or admitted artifact drift."""
    if plan.get("mode") != "fixed-baseline-recovery":
        raise ValueError("fixed baseline requires explicit recovery plan")
    binding = plan.get("fixed_baseline")
    if not isinstance(binding, dict):
        raise ValueError("fixed baseline binding missing")
    for name in ("manifest", "plan", "admission", "prechange_verification", "run",
                 "patch", "hidden", "backend", "semantic", "end_to_end", "design_review"):
        item = binding.get(name)
        if (not isinstance(item, dict) or set(item) != {"path", "sha256"} or
                not isinstance(item["path"], str) or not item["path"].startswith(
                    ("_scratch/pilot-17-evidence/", "pilot-plan-v17.json")) or
                file_sha(HERE / item["path"]) != item["sha256"]):
            raise ValueError(f"fixed baseline {name} file or hash drift")
    old_spec = _json(HERE / binding["manifest"]["path"])
    old_plan = _json(HERE / binding["plan"]["path"])
    if (old_plan.get("pilot_id") != "flipt-oci-long-horizon-pilot-17" or
            old_plan.get("mode") != "paired-recovery" or
            old_spec.get("pilot", {}).get("sha256") != binding["plan"]["sha256"]):
        raise ValueError("original baseline fixture identity changed")
    shared = set(old_spec) - {"arm_execution", "pilot", "status", "live_enabled"}
    if (set(spec) != set(old_spec) or
            any(old_spec[key] != spec[key] for key in shared) or
            old_plan.get("current_cancellation") != plan.get("current_cancellation") or
            old_plan.get("limits_per_arm") != plan.get("limits_per_arm") or
            old_plan.get("feedback") != plan.get("feedback") or
            old_plan.get("fixture", {}).get("assets_sha256") != spec.get("assets")):
        raise ValueError("shared task, runtime, source, or limits differ from v17")
    old_policy = _json(HERE / old_spec["arm_execution"]["path"])
    new_policy = _json(HERE / spec["arm_execution"]["path"])
    if (old_policy.get("baseline") != new_policy.get("baseline") or
            old_spec["arm_execution"]["prompt_suffix_sha256"]["baseline"] !=
                spec["arm_execution"]["prompt_suffix_sha256"]["baseline"] or
            {key: value for key, value in old_policy.get("treatment", {}).items()
             if key != "prompt_suffix"} !=
            {key: value for key, value in new_policy.get("treatment", {}).items()
             if key != "prompt_suffix"}):
        raise ValueError("baseline execution policy or non-instruction treatment policy changed")
    old_sources = old_plan.get("execution_sources_sha256") or {}
    new_sources = plan.get("execution_sources_sha256") or {}
    if (set(old_sources) != set(new_sources) or
            any(old_sources[name] != new_sources[name] for name in CORE_SOURCES) or
            any(name not in CORE_SOURCES + METADATA_SOURCES for name in new_sources)):
        raise ValueError("shared execution, transport, accounting, or quality source changed")
    # run.py has a separate normalized hash; its exact v18 metadata diff is
    # exposed for independent review rather than normalized by a new framework.
    if (plan.get("fixed_baseline", {}).get("allowed_changed_sources") !=
            ["run.py", *METADATA_SOURCES] or
            old_plan.get("runner_normalized_sha256") != binding.get("old_runner_sha256")):
        raise ValueError("runner metadata delta is unreviewed")
    admission = _json(HERE / binding["admission"]["path"])
    verified = _json(HERE / binding["prechange_verification"]["path"])
    run = _json(HERE / binding["run"]["path"])
    wall = _json(HERE / binding["end_to_end"]["path"])
    expected = {"accepted_patch_file_sha256": binding["patch"]["sha256"],
        "accepted_patch_sha256": binding["patch"]["sha256"],
        "baseline_run_sha256": binding["run"]["sha256"],
        "hidden_race_grade_sha256": binding["hidden"]["sha256"],
        "backend_grade_sha256": binding["backend"]["sha256"],
        "semantic_review_sha256": binding["semantic"]["sha256"],
        "manifest_sha256": binding["manifest"]["sha256"],
        "pilot_plan_sha256": binding["plan"]["sha256"]}
    if (admission.get("verdict") != "pass" or
            any(admission.get(key) != value for key, value in expected.items()) or
            verified.get("admission_sha256") != binding["admission"]["sha256"] or
            verified.get("baseline_run_sha256") != binding["run"]["sha256"] or
            verified.get("quality", {}).get("end_to_end_sha256") != binding["end_to_end"]["sha256"] or
            run.get("stop_reason") != "accepted-final" or run.get("cost_status") != "complete" or
            run.get("usage_issues") != [] or run.get("native_attempts") != [] or
            run.get("shared_initial_prompt_sha256") != sha((
                (ASSET_ROOT / "round0.md").read_text(encoding="utf-8") + "\n\n" +
                (ASSET_ROOT / "environment.md").read_text(encoding="utf-8")).encode()) or
            run.get("usage", {}).get("estimated_usd") != 8.9516085 or
            run.get("usage", {}).get("model_calls") != 55 or
            wall.get("end_to_end_wall_seconds") != 2455.738 or
            wall.get("quality_files_sha256") != {
                "hidden_race_grade": binding["hidden"]["sha256"],
                "backend_grade": binding["backend"]["sha256"],
                "semantic_review": binding["semantic"]["sha256"]}):
        raise ValueError("original baseline admission, usage, or quality changed")
    return {"status": "verified-fixed-historical-baseline",
            "admission_sha256": binding["admission"]["sha256"],
            "run_sha256": binding["run"]["sha256"],
            "end_to_end_sha256": binding["end_to_end"]["sha256"],
            "estimated_usd": run["usage"]["estimated_usd"],
            "end_to_end_wall_seconds": wall["end_to_end_wall_seconds"],
            "shared_initial_prompt_sha256": run["shared_initial_prompt_sha256"],
            "reveal_assets": [event["assets"] for event in run["stage"]["events"]
                              if event.get("kind") == "reveal"]}


def verify_hard_stages(spec: dict, plan: dict, run: dict, workspace: Path) -> dict:
    """Join each revealed hard-episode receipt to a completed Astra child turn."""
    attempts = run.get("attempts") or []
    native = run.get("native_attempts") or []
    events = (run.get("stage") or {}).get("events") or []
    bound = {}
    for round_id in (1, 2):
        accepted = [attempt for attempt in attempts if attempt.get("round") == round_id and
            any(event.get("kind") == "gate" and event.get("behavior_pass") is True
                for event in events[attempt["stage_event_start"]:attempt["stage_event_end"]])]
        if len(accepted) != 1:
            raise ValueError(f"R{round_id} accepted checkpoint missing")
        attempt = accepted[0]
        submit_ns = events[attempt["stage_event_start"]].get("time_ns")
        path = workspace / ".benchmark" / "hard-kernel" / f"round{round_id}.json"
        receipt = _json(path)
        worker_id, worker_turn = receipt.get("worker_id"), receipt.get("worker_turn_id")
        matches = [(entry, turn) for entry in native if isinstance(entry, dict)
                   for turn in entry.get("turns", []) if isinstance(turn, dict) and
                   entry.get("child_id") == worker_id and turn.get("turn_id") == worker_turn and
                   turn.get("parent_turn_id") == attempt.get("turn_id") and
                   turn.get("model") == "gpt-6-astra" and turn.get("effort") == "xhigh" and
                   turn.get("terminal") in ("task_complete", "task_completed")]
        if len(matches) != 1:
            raise ValueError(f"R{round_id} independent Astra turn missing")
        stamp = matches[0][1].get("terminal_timestamp")
        try:
            terminal_ns = int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1e9)
        except (AttributeError, ValueError, TypeError):
            raise ValueError(f"R{round_id} Astra terminal time missing") from None
        if (receipt.get("schema_version") != 1 or receipt.get("round") != round_id or
                receipt.get("model") != "gpt-6-astra" or receipt.get("effort") != "xhigh" or
                receipt.get("parent_turn_id") != attempt.get("turn_id") or
                receipt.get("implementation_owner") != "controller" or
                receipt.get("revealed_report_sha256") != spec["assets"][f"round{round_id}.md"] or
                not isinstance(receipt.get("invariants"), list) or
                not receipt["invariants"] or any(not isinstance(item, str) or not item.strip()
                    for item in receipt["invariants"]) or
                any(not isinstance(receipt.get(key), str) or not receipt[key].strip()
                    for key in ("repair_plan", "review", "self_check")) or
                not isinstance(receipt.get("public_checks"), list) or
                not receipt["public_checks"] or
                type(submit_ns) is not int or terminal_ns >= submit_ns):
            raise ValueError(f"R{round_id} hard-kernel receipt incomplete or late")
        bound[str(round_id)] = {"receipt_sha256": file_sha(path),
                                "worker_id": worker_id, "worker_turn_id": worker_turn}
    return bound
