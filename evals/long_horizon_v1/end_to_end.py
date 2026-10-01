"""Record each arm's wall boundary after its independent post-run quality work."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from evals.long_horizon_v1.collect import _arm
from evals.long_horizon_v1.common import HERE, TASK_ID, file_sha, manifest, write_json_new
from evals.long_horizon_v1.grade import REVIEW_REQUIREMENTS
from evals.long_horizon_v1.quality_admission import (
    _grade, _json, verify_quality_admission)


def finalize_arm(prepared: Path, arm: str, plan: dict) -> dict:
    """Require completed run and quality evidence, then write a separate wall receipt."""
    if arm not in ("baseline", "treatment"):
        raise ValueError("unknown pilot arm")
    evidence = HERE / plan["evidence_root"]
    run_path = HERE / plan["run_outputs"][arm] / "run.json"
    run = _arm(run_path, arm)
    started = run.get("started_utc_ns")
    run_wall = run.get("wall_seconds")
    preparation_sha = file_sha(prepared / "preparation.json")
    manifest_sha = file_sha(HERE / "manifest.json")
    if (run.get("stop_reason") != "accepted-final" or
            run.get("cost_status") != "complete" or
            run.get("runtime_binding", {}).get("preparation_sha256") != preparation_sha or
            type(started) is not int or started <= 0 or
            type(run_wall) not in (int, float) or run_wall < 0):
        raise ValueError("arm run or accounting incomplete for end-to-end boundary")
    quality_files: dict[str, str] = {}
    if arm == "baseline":
        admission_path = HERE / plan["quality_admission"]
        admission = verify_quality_admission(prepared, plan)
        if admission.get("verdict") != "pass":
            raise ValueError("baseline quality admission failed")
        quality_files = {"admission": file_sha(admission_path),
            "hidden_race_grade": admission["hidden_race_grade_sha256"],
            "backend_grade": admission["backend_grade_sha256"],
            "semantic_review": admission["semantic_review_sha256"]}
    else:
        stage = run.get("stage") or {}
        events = stage.get("events") or []
        attempts = run.get("attempts") or []
        if (stage.get("complete") is not True or stage.get("round") != 2 or
                stage.get("manifest_sha256") != manifest_sha or
                not events or events[-1].get("kind") != "accepted-final" or
                not attempts or attempts[-1].get("round") != 2):
            raise ValueError("treatment accepted final stage missing")
        patch_sha = events[-1].get("patch_sha256")
        patch_path = run_path.parent / f"candidate-g2-attempt{attempts[-1]['repair']}.patch"
        if not isinstance(patch_sha, str) or file_sha(patch_path) != patch_sha:
            raise ValueError("treatment accepted patch drift")
        review_path = evidence / "treatment-review.json"
        review = _json(review_path)
        requirements = review.get("requirements")
        if (review.get("schema_version") != 1 or
                review.get("reviewer_role") != "independent" or
                review.get("arm_blind") is not True or "arm" in review or
                review.get("candidate_patch_sha256") != patch_sha or
                review.get("manifest_sha256") != manifest_sha or
                review.get("preparation_sha256") != preparation_sha or
                not isinstance(review.get("reviewer_id"), str) or
                not review["reviewer_id"] or
                not isinstance(requirements, dict) or
                set(requirements) != set(REVIEW_REQUIREMENTS) or
                any(value is not True for value in requirements.values()) or
                review.get("verdict") != "pass"):
            raise ValueError("treatment arm-blind semantic review missing or failed")
        quality_files = {
            "hidden_race_grade": _grade(evidence / "treatment-hidden-grade",
                                        patch_sha, manifest_sha, hidden=True, review=review),
            "backend_grade": _grade(evidence / "treatment-backend-grade",
                                    patch_sha, manifest_sha, hidden=False),
            "semantic_review": file_sha(review_path)}
    finished = time.time_ns()
    elapsed = (finished - started) / 1e9
    if finished < started or elapsed < run_wall - 1:
        raise ValueError("system wall clock cannot support end-to-end boundary")
    result = {"schema_version": 1, "kind": "arm-end-to-end-boundary",
        "pilot_id": plan["pilot_id"], "task_id": TASK_ID, "arm": arm,
        "manifest_sha256": manifest_sha,
        "pilot_plan_sha256": file_sha(HERE / manifest()["pilot"]["path"]),
        "preparation_sha256": preparation_sha,
        "run_sha256": file_sha(run_path),
        "started_utc_ns": started, "quality_finished_utc_ns": finished,
        "end_to_end_wall_seconds": round(elapsed, 3),
        "run_wall_seconds": run_wall,
        "post_run_quality_wall_seconds": round(max(0.0, elapsed - run_wall), 3),
        "quality_files_sha256": quality_files,
        "boundary": "runner start before App Server launch through completed hidden/race, backend, and independent semantic review",
        "clock": "same-host system UTC wall clock; scheduling delay is included"}
    write_json_new(HERE / plan["end_to_end"]["receipts"][arm], result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", required=True, type=Path)
    parser.add_argument("--arm", required=True, choices=("baseline", "treatment"))
    args = parser.parse_args()
    from evals.long_horizon_v1.run import pilot_plan
    print(json.dumps(finalize_arm(args.prepared.resolve(), args.arm,
                                  pilot_plan(manifest())), indent=2))
