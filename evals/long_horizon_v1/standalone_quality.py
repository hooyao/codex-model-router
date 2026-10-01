"""Verify treatment-only quality and wall receipts for feasibility reporting."""
from __future__ import annotations

import json
from pathlib import Path
import re

from evals.long_horizon_v1.common import ASSET_ROOT, HERE, START_TREE, TASK_ID
from evals.long_horizon_v1.common import file_sha, manifest, sha
from evals.long_horizon_v1.grade import BACKEND_PACKAGES, PACKAGES, REVIEW_REQUIREMENTS, linux_go_path

RACE_PACKAGES = ["./internal/oci", "./internal/storage/fs", "./internal/storage/fs/oci"]


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"invalid standalone evidence object: {path}")
    return value


def _tests(hidden: bool) -> list[str]:
    roots = [ASSET_ROOT / "public" / f"g{round_id}" for round_id in range(3)]
    if hidden:
        roots.append(ASSET_ROOT / "oracle")
    return sorted(f"{path.relative_to(root).as_posix()}:{file_sha(path)}"
                  for root in roots for path in root.rglob("*") if path.is_file())


def _grade(path: Path, patch_sha: str, manifest_sha: str, *, hidden: bool,
           review: dict | None = None) -> str:
    """Independently bind treatment grades to tests, raw output, and review."""
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
        raise ValueError("standalone hidden or backend grade failed or is unbound")
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
            raise ValueError("standalone grade command, result, or raw output drift")
    return file_sha(path / "grade.json")


def verify_standalone_quality(prepared: Path, plan: dict, run: dict) -> dict:
    """Reconstruct quality from the accepted patch and independent evidence."""
    if plan.get("mode") != "standalone-feasibility":
        raise ValueError("standalone quality requires standalone plan")
    evidence = HERE / plan["evidence_root"]
    run_path = HERE / plan["run_outputs"]["treatment"] / "run.json"
    manifest_sha = file_sha(HERE / "manifest.json")
    descriptor = manifest().get("pilot", {})
    plan_name = descriptor.get("path") if isinstance(descriptor, dict) else None
    match = re.fullmatch(r"pilot-plan-v(1[2-9]|[2-9][0-9]+)\.json", plan_name or "")
    if (match is None or descriptor.get("schema_version") != 4 or
            plan.get("pilot_id") != f"flipt-oci-long-horizon-pilot-{int(match.group(1)):02d}" or
            descriptor.get("sha256") != file_sha(HERE / plan_name)):
        raise ValueError("standalone plan descriptor missing or stale")
    plan_sha = file_sha(HERE / plan_name)
    preparation_sha = file_sha(prepared / "preparation.json")
    stage = run.get("stage") if isinstance(run.get("stage"), dict) else {}
    events = stage.get("events")
    attempts = run.get("attempts")
    parent = run.get("parent_thread_id")
    if (run.get("stop_reason") != "accepted-final" or run.get("cost_status") != "complete" or
            run.get("usage_issues") != [] or stage.get("complete") is not True or
            stage.get("round") != 2 or stage.get("manifest_sha256") != manifest_sha or
            run.get("runtime_binding", {}).get("preparation_sha256") != preparation_sha or
            not isinstance(parent, str) or not parent or
            not isinstance(events, list) or not events or
            not isinstance(attempts, list) or len(attempts) < 3):
        raise ValueError("standalone run or three-round stage incomplete")
    previous = "0" * 64
    for index, event in enumerate(events):
        payload = {key: value for key, value in event.items() if key != "sha256"}
        digest = sha(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
        if (event.get("seq") != index or event.get("previous_sha256") != previous or
                event.get("sha256") != digest or event.get("thread_id") != parent or
                event.get("arm") != "treatment"):
            raise ValueError("standalone stage event chain changed")
        previous = digest
    if stage.get("chain_sha256") != previous:
        raise ValueError("standalone final event hash changed")
    finals = [event for event in events if event.get("kind") == "accepted-final"]
    reveals = [event.get("round") for event in events if event.get("kind") == "reveal"]
    if (len(finals) != 1 or events[-1] != finals[0] or
            reveals != [1, 2] or
            not {0, 1, 2}.issubset({attempt.get("round") for attempt in attempts})):
        raise ValueError("standalone R0/R1/R2 acceptance incomplete")
    for round_id in range(3):
        accepted = [attempt for attempt in attempts if attempt.get("round") == round_id and
            any(event.get("kind") == "gate" and event.get("round") == round_id and
                event.get("behavior_pass") is True
                for event in events[attempt["stage_event_start"]:attempt["stage_event_end"]])]
        if len(accepted) != 1:
            raise ValueError(f"standalone R{round_id} accepted public gate missing")
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
        raise ValueError("standalone accepted patch or attempt mismatch")
    patch_path = run_path.parent / f"candidate-g2-attempt{attempt['repair']}.patch"
    if not isinstance(patch_sha, str) or file_sha(patch_path) != patch_sha:
        raise ValueError("standalone accepted patch bytes changed")
    review_path = evidence / "treatment-review.json"
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
        raise ValueError("standalone independent blind semantic review failed")
    quality_files = {
        "hidden_race_grade": _grade(evidence / "treatment-hidden-grade", patch_sha,
                                    manifest_sha, hidden=True, review=review),
        "backend_grade": _grade(evidence / "treatment-backend-grade", patch_sha,
                                manifest_sha, hidden=False),
        "semantic_review": file_sha(review_path)}
    wall_path = HERE / plan["end_to_end"]["receipts"]["treatment"]
    wall = _json(wall_path)
    started = run.get("started_utc_ns")
    run_wall = run.get("wall_seconds")
    e2e_wall = wall.get("end_to_end_wall_seconds")
    if (wall.get("schema_version") != 1 or wall.get("kind") != "arm-end-to-end-boundary" or
            wall.get("pilot_id") != plan["pilot_id"] or wall.get("task_id") != TASK_ID or
            wall.get("arm") != "treatment" or wall.get("manifest_sha256") != manifest_sha or
            wall.get("pilot_plan_sha256") != plan_sha or
            wall.get("preparation_sha256") != preparation_sha or
            wall.get("run_sha256") != file_sha(run_path) or
            wall.get("quality_files_sha256") != quality_files or
            type(started) is not int or started <= 0 or
            wall.get("started_utc_ns") != started or
            type(run_wall) not in (int, float) or run_wall < 0 or
            wall.get("run_wall_seconds") != run_wall or
            type(e2e_wall) not in (int, float) or e2e_wall < run_wall - 1 or
            type(wall.get("quality_finished_utc_ns")) is not int or
            wall["quality_finished_utc_ns"] < started or
            abs((wall["quality_finished_utc_ns"] - started) / 1e9 - e2e_wall) > .002):
        raise ValueError("standalone end-to-end quality receipt missing or stale")
    return {"sha256": file_sha(wall_path), "end_to_end_wall_seconds": e2e_wall,
            "quality_files_sha256": quality_files}
