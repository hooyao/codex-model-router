"""Fail-closed, hash-bound baseline quality admission for the paid pilot."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from evals.long_horizon_v1.collect import _arm
from evals.long_horizon_v1.common import ASSET_ROOT, HERE, START_TREE, TASK_ID
from evals.long_horizon_v1.common import file_sha, manifest, sha, write_json_new
from evals.long_horizon_v1.grade import BACKEND_PACKAGES, PACKAGES, REVIEW_REQUIREMENTS, linux_go_path

RACE_PACKAGES = ["./internal/oci", "./internal/storage/fs", "./internal/storage/fs/oci"]


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"invalid evidence object: {path}")
    return value


def _tests(hidden: bool) -> list[str]:
    roots = [ASSET_ROOT / "public" / f"g{round_id}" for round_id in range(3)]
    if hidden:
        roots.append(ASSET_ROOT / "oracle")
    return sorted(f"{path.relative_to(root).as_posix()}:{file_sha(path)}"
                  for root in roots for path in root.rglob("*") if path.is_file())


def _grade(path: Path, patch_sha: str, manifest_sha: str, *, hidden: bool,
           review: dict | None = None) -> str:
    value = _json(path / "grade.json")
    checks = value.get("checks")
    if (value.get("schema_version") != 1 or value.get("task_id") != TASK_ID or
            value.get("kind") != ("hidden-final" if hidden else "backend-regression") or
            value.get("round") != 2 or value.get("start_tree") != START_TREE or
            value.get("manifest_sha256") != manifest_sha or
            value.get("candidate_patch_sha256") != patch_sha or
            value.get("tests") != _tests(hidden) or
            value.get("behavior_pass") is not True or
            value.get("quality_pass") is not True or
            value.get("semantic_review") != (review if hidden else "not-applicable") or
            not isinstance(checks, list) or len(checks) != (2 if hidden else 1)):
        raise ValueError("baseline hidden or backend grade failed or is unbound")
    for index, check in enumerate(checks):
        kind = "race" if hidden and index == 1 else "go-test"
        expected = RACE_PACKAGES if kind == "race" else (PACKAGES if hidden else BACKEND_PACKAGES)
        command = check.get("command") if isinstance(check, dict) else None
        if (not isinstance(command, list) or
                command[0:4] != [linux_go_path(manifest()["toolchain"]),
                                  "test", "-count=1", "-timeout=90s"] or
                command[4:] != (["-race"] if kind == "race" else []) + expected or
                check.get("exit_code") != 0 or
                any(check.get(f"{stream}_sha256") != file_sha(path / kind / f"{stream}.txt")
                    for stream in ("stdout", "stderr"))):
            raise ValueError("baseline grade command, result, or raw output drift")
    return file_sha(path / "grade.json")


def expected_admission(prepared: Path, plan: dict) -> dict:
    """Reconstruct admission from current immutable evidence, never from assertions alone."""
    evidence = HERE / plan["evidence_root"]
    run_path = HERE / plan["run_outputs"]["baseline"] / "run.json"
    run = _arm(run_path, "baseline")
    stage = run["stage"]
    events = stage["events"]
    parent = run.get("parent_thread_id")
    usage = run.get("usage") or {}
    preparation_sha = file_sha(prepared / "preparation.json")
    manifest_sha = file_sha(HERE / "manifest.json")
    plan_sha = file_sha(HERE / manifest()["pilot"]["path"])
    if (run.get("stop_reason") != "accepted-final" or run.get("cost_status") != "complete" or
            stage.get("complete") is not True or stage.get("round") != 2 or
            stage.get("manifest_sha256") != manifest_sha or
            run.get("runtime_binding", {}).get("preparation_sha256") != preparation_sha or
            not isinstance(parent, str) or not parent or
            type(usage.get("estimated_usd")) not in (int, float) or
            type(usage.get("estimated_usd_upper_bound")) not in (int, float) or
            not 0 <= usage["estimated_usd"] <= usage["estimated_usd_upper_bound"] or
            type(usage.get("model_calls")) is not int or usage["model_calls"] < 1 or
            run.get("usage_issues") != [] or run.get("native_attempts") != [] or
            not events or events[-1].get("kind") != "accepted-final"):
        raise ValueError("baseline run, accounting, or final stage incomplete")
    previous = "0" * 64
    for index, event in enumerate(events):
        payload = {key: value for key, value in event.items() if key != "sha256"}
        digest = sha(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
        if (event.get("seq") != index or event.get("previous_sha256") != previous or
                event.get("sha256") != digest or event.get("thread_id") != parent or
                event.get("arm") != "baseline"):
            raise ValueError("baseline stage event chain changed")
        previous = digest
    if stage.get("chain_sha256") != previous:
        raise ValueError("baseline final event chain hash changed")
    finals = [event for event in events if event.get("kind") == "accepted-final"]
    attempts = run.get("attempts")
    if len(finals) != 1 or not isinstance(attempts, list) or not attempts:
        raise ValueError("baseline final attempt missing or duplicated")
    attempt = attempts[-1]
    span = events[attempt["stage_event_start"]:attempt["stage_event_end"]]
    submits = [event for event in span if event.get("kind") == "submit"]
    patch_sha = finals[0].get("patch_sha256")
    if (attempt.get("round") != 2 or attempt.get("thread_id") != parent or
            span[-1] != finals[0] or len(submits) != 1 or
            submits[0].get("patch_sha256") != patch_sha or
            submits[0].get("repair") != attempt.get("repair") or
            not any(event.get("kind") == "gate" and event.get("behavior_pass") is True and
                    event.get("patch_sha256") == patch_sha for event in span)):
        raise ValueError("baseline accepted patch or attempt mismatch")
    patch_path = run_path.parent / f"candidate-g2-attempt{attempt['repair']}.patch"
    if file_sha(patch_path) != patch_sha:
        raise ValueError("baseline accepted patch bytes changed")
    review_path = evidence / "baseline-review.json"
    review = _json(review_path)
    requirements = review.get("requirements")
    if (review.get("schema_version") != 1 or review.get("reviewer_role") != "independent" or
            review.get("arm_blind") is not True or
            not isinstance(review.get("reviewer_id"), str) or not review["reviewer_id"] or
            review["reviewer_id"] == parent or "arm" in review or
            review.get("candidate_patch_sha256") != patch_sha or
            review.get("manifest_sha256") != manifest_sha or
            review.get("preparation_sha256") != preparation_sha or
            not isinstance(requirements, dict) or set(requirements) != set(REVIEW_REQUIREMENTS) or
            any(value is not True for value in requirements.values()) or
            review.get("verdict") != "pass"):
        raise ValueError("independent arm-blind semantic review missing or failed")
    hidden_sha = _grade(evidence / "baseline-hidden-grade", patch_sha, manifest_sha,
                        hidden=True, review=review)
    backend_sha = _grade(evidence / "baseline-backend-grade", patch_sha, manifest_sha,
                         hidden=False)
    return {"schema_version": 1, "kind": "baseline-quality-admission", "verdict": "pass",
            "pilot_id": plan["pilot_id"], "arm": "baseline", "task_id": TASK_ID,
            "manifest_sha256": manifest_sha, "pilot_plan_sha256": plan_sha,
            "preparation_sha256": preparation_sha, "baseline_run_sha256": file_sha(run_path),
            "parent_thread_id": parent, "accepted_turn_id": attempt["turn_id"],
            "accepted_attempt": {"round": 2, "repair": attempt["repair"]},
            "accepted_patch_sha256": patch_sha, "accepted_patch_file_sha256": file_sha(patch_path),
            "accepted_patch_source": f"{plan['run_outputs']['baseline']}/candidate-g2-attempt{attempt['repair']}.patch",
            "hidden_race_grade_source": f"{plan['evidence_root']}/baseline-hidden-grade/grade.json",
            "hidden_race_grade_sha256": hidden_sha, "backend_grade_sha256": backend_sha,
            "backend_grade_source": f"{plan['evidence_root']}/baseline-backend-grade/grade.json",
            "semantic_review_source": f"{plan['evidence_root']}/baseline-review.json",
            "semantic_review_sha256": file_sha(review_path),
            "semantic_review_reviewer_id": review["reviewer_id"],
            "semantic_review_verdict": review["verdict"]}


def verify_quality_admission(prepared: Path, plan: dict) -> dict:
    path = HERE / plan["quality_admission"]
    if not path.is_file():
        raise ValueError("baseline full-quality admission receipt missing")
    receipt = _json(path)
    expected = expected_admission(prepared, plan)
    if receipt != expected:
        raise ValueError("baseline quality admission receipt missing, failed, or stale")
    return receipt


def create_quality_admission(prepared: Path, plan: dict) -> dict:
    result = expected_admission(prepared, plan)
    write_json_new(HERE / plan["quality_admission"], result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", required=True, type=Path)
    args = parser.parse_args()
    from evals.long_horizon_v1.run import pilot_plan
    print(json.dumps(create_quality_admission(args.prepared.resolve(), pilot_plan(manifest())), indent=2))
