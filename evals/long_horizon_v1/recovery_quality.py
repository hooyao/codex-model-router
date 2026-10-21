"""Independent, symmetric quality checks for the fresh paired recovery run."""
from __future__ import annotations

import json
from pathlib import Path

from .common import HERE, TASK_ID, file_sha, manifest, sha
from .grade import REVIEW_REQUIREMENTS
from .standalone_quality import _grade, _json


def verify_recovery_arm_quality(prepared: Path, plan: dict, run: dict,
                                arm: str, *, require_wall: bool) -> dict:
    if plan.get("mode") != "paired-recovery" or arm not in ("baseline", "treatment"):
        raise ValueError("recovery arm quality requires a paired recovery plan")
    run_path = HERE / plan["run_outputs"][arm] / "run.json"
    manifest_sha = file_sha(HERE / "manifest.json")
    plan_sha = file_sha(HERE / manifest()["pilot"]["path"])
    preparation_sha = file_sha(prepared / "preparation.json")
    evidence = HERE / plan["evidence_root"]
    stage = run.get("stage") if isinstance(run.get("stage"), dict) else {}
    events, attempts = stage.get("events"), run.get("attempts")
    parent = run.get("parent_thread_id")
    usage = run.get("usage") if isinstance(run.get("usage"), dict) else {}
    if (run.get("arm") != arm or run.get("stop_reason") != "accepted-final" or
            run.get("cost_status") != "complete" or run.get("usage_issues") != [] or
            usage.get("unknown_models") or usage.get("unknown_usage") or
            stage.get("complete") is not True or stage.get("round") != 2 or
            stage.get("manifest_sha256") != manifest_sha or
            run.get("runtime_binding", {}).get("preparation_sha256") != preparation_sha or
            not isinstance(parent, str) or not parent or
            not isinstance(events, list) or not events or
            not isinstance(attempts, list) or len(attempts) < 3):
        raise ValueError(f"{arm} accepted run, accounting, or stage incomplete")
    previous = "0" * 64
    for index, event in enumerate(events):
        payload = {key: value for key, value in event.items() if key != "sha256"}
        digest = sha(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
        if (event.get("seq") != index or event.get("previous_sha256") != previous or
                event.get("sha256") != digest or event.get("thread_id") != parent or
                event.get("arm") != arm):
            raise ValueError(f"{arm} stage event chain changed")
        previous = digest
    finals = [event for event in events if event.get("kind") == "accepted-final"]
    if (stage.get("chain_sha256") != previous or len(finals) != 1 or
            events[-1] != finals[0] or
            [event.get("round") for event in events if event.get("kind") == "reveal"] != [1, 2] or
            not {0, 1, 2}.issubset({attempt.get("round") for attempt in attempts})):
        raise ValueError(f"{arm} three-round event chain incomplete")
    for round_id in range(3):
        accepted = [attempt for attempt in attempts if attempt.get("round") == round_id and
            any(event.get("kind") == "gate" and event.get("round") == round_id and
                event.get("behavior_pass") is True
                for event in events[attempt["stage_event_start"]:attempt["stage_event_end"]])]
        if len(accepted) != 1:
            raise ValueError(f"{arm} R{round_id} accepted public gate missing")
    attempt = attempts[-1]
    span = events[attempt["stage_event_start"]:attempt["stage_event_end"]]
    patch_sha = finals[0].get("patch_sha256")
    submits = [event for event in span if event.get("kind") == "submit"]
    if (attempt.get("round") != 2 or attempt.get("thread_id") != parent or
            not span or span[-1] != finals[0] or len(submits) != 1 or
            submits[0].get("patch_sha256") != patch_sha or
            submits[0].get("repair") != attempt.get("repair") or
            not any(event.get("kind") == "gate" and event.get("behavior_pass") is True and
                    event.get("patch_sha256") == patch_sha for event in span)):
        raise ValueError(f"{arm} accepted patch or attempt mismatch")
    patch_path = run_path.parent / f"candidate-g2-attempt{attempt['repair']}.patch"
    if not isinstance(patch_sha, str) or file_sha(patch_path) != patch_sha:
        raise ValueError(f"{arm} accepted patch bytes changed")
    review_path = evidence / f"{arm}-review.json"
    review = _json(review_path)
    requirements = review.get("requirements")
    if (review.get("schema_version") != 1 or review.get("reviewer_role") != "independent" or
            review.get("arm_blind") is not True or "arm" in review or
            not isinstance(review.get("reviewer_id"), str) or not review["reviewer_id"] or
            review["reviewer_id"] == parent or
            review.get("candidate_patch_sha256") != patch_sha or
            review.get("manifest_sha256") != manifest_sha or
            review.get("preparation_sha256") != preparation_sha or
            not isinstance(requirements, dict) or set(requirements) != set(REVIEW_REQUIREMENTS) or
            any(value is not True for value in requirements.values()) or
            review.get("verdict") != "pass"):
        raise ValueError(f"{arm} independent arm-blind semantic review failed")
    quality_files = {
        "hidden_race_grade": _grade(evidence / f"{arm}-hidden-grade", patch_sha,
                                    manifest_sha, hidden=True, review=review),
        "backend_grade": _grade(evidence / f"{arm}-backend-grade", patch_sha,
                                manifest_sha, hidden=False),
        "semantic_review": file_sha(review_path)}
    result = {"patch_sha256": patch_sha, "quality_files_sha256": quality_files}
    if require_wall:
        wall_path = HERE / plan["end_to_end"]["receipts"][arm]
        wall = _json(wall_path)
        started, run_wall = run.get("started_utc_ns"), run.get("wall_seconds")
        elapsed = wall.get("end_to_end_wall_seconds")
        if (wall.get("schema_version") != 1 or wall.get("kind") != "arm-end-to-end-boundary" or
                wall.get("pilot_id") != plan["pilot_id"] or wall.get("task_id") != TASK_ID or
                wall.get("arm") != arm or wall.get("manifest_sha256") != manifest_sha or
                wall.get("pilot_plan_sha256") != plan_sha or
                wall.get("preparation_sha256") != preparation_sha or
                wall.get("run_sha256") != file_sha(run_path) or
                wall.get("quality_files_sha256") != quality_files or
                type(started) is not int or started <= 0 or
                wall.get("started_utc_ns") != started or
                type(run_wall) not in (int, float) or run_wall < 0 or
                wall.get("run_wall_seconds") != run_wall or
                type(elapsed) not in (int, float) or elapsed < run_wall - 1 or
                type(wall.get("quality_finished_utc_ns")) is not int or
                wall["quality_finished_utc_ns"] < started or
                abs((wall["quality_finished_utc_ns"] - started) / 1e9 - elapsed) > .002):
            raise ValueError(f"{arm} end-to-end quality receipt missing or stale")
        result.update(end_to_end_wall_seconds=elapsed, end_to_end_sha256=file_sha(wall_path))
    return result
