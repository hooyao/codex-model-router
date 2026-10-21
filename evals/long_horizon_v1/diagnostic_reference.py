"""Verify the pilot-09 baseline as an exploratory diagnostic reference only."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from evals.long_horizon_v1.common import HERE, TASK_ID, file_sha, manifest, sha

REVIEW_PATH = HERE / "controls/pilot09-diagnostic-reference-review.json"
REVIEW_SHA256 = "1b347c483db481c7125c570fcfb1f3a8457f56d4eb67d721bb5f264e2f43721a"
HISTORICAL_MANIFEST_SHA256 = "7f8c7c39ea66a39a6ac211f626c62fe9c5c15e8043704449b1687cda4f8fdb54"
CLAIM = "treatment-only-exploratory-feasibility"


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"diagnostic reference object invalid: {path}")
    return value


def verify_diagnostic_reference(spec: dict, plan: dict) -> dict:
    """Check all attested bytes and cross-bind quality, run, and wall receipts."""
    reference = plan.get("diagnostic_reference")
    if (plan.get("claim") != CLAIM or not isinstance(reference, dict) or
            reference.get("claim") != CLAIM or
            reference.get("fresh_matched_pair") is not False or
            reference.get("historical_manifest_sha256") != HISTORICAL_MANIFEST_SHA256 or
            reference.get("review_path") != "controls/pilot09-diagnostic-reference-review.json" or
            reference.get("review_sha256") != REVIEW_SHA256 or
            reference.get("historical_pilot_id") != "flipt-oci-long-horizon-pilot-09"):
        raise ValueError("diagnostic reference plan missing or wrongly labeled")
    if file_sha(REVIEW_PATH) != REVIEW_SHA256:
        raise ValueError("diagnostic reference independent review drift")
    review = _json(REVIEW_PATH)
    if (review.get("schema_version") != 1 or review.get("kind") !=
            "independent-diagnostic-reference-review" or
            review.get("reviewer_role") != "independent" or
            review.get("verdict") != "PASS" or
            review.get("decision") != "PASS-diagnostic-reuse" or
            review.get("scope") != CLAIM or
            review.get("authorization", {}).get("authorizes_paid_execution") is not False or
            review.get("historical_reference", {}).get("arm") != "baseline" or
            review["historical_reference"].get("pilot_id") != reference["historical_pilot_id"] or
            review["historical_reference"].get("manifest_sha256") != HISTORICAL_MANIFEST_SHA256):
        raise ValueError("diagnostic reference review failed or wrong arm")
    # Reconstruct the pre-pause manifest in memory; never rewrite historical evidence.
    historical = dict(spec)
    historical["live_enabled"] = True
    historical["arm_execution"] = reference["historical_arm_execution"]
    historical["pilot"] = reference["historical_pilot"]
    serialized = (json.dumps(historical, indent=2, sort_keys=True) + "\n").encode()
    if sha(serialized) != HISTORICAL_MANIFEST_SHA256:
        raise ValueError("historical baseline-effective manifest no longer matches")
    artifacts = review["historical_reference"].get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != {
            "accepted_patch", "backend_grade", "canonical_semantic_review",
            "end_to_end", "hidden_race_grade", "neutral_semantic_review", "plan",
            "preparation", "public_g0_grade", "public_g1_grade", "public_g2_grade",
            "quality_admission", "run", "semantic_review_attestation"}:
        raise ValueError("historical diagnostic artifact inventory incomplete")
    for name, artifact in artifacts.items():
        relative = artifact.get("path") if isinstance(artifact, dict) else None
        expected = artifact.get("sha256") if isinstance(artifact, dict) else None
        if (not isinstance(relative, str) or not isinstance(expected, str) or
                not (HERE / relative).resolve().is_relative_to(HERE.resolve()) or
                file_sha(HERE / relative) != expected):
            raise ValueError(f"historical diagnostic artifact drift: {name}")
    if (artifacts["plan"]["sha256"] != reference.get("historical_pilot", {}).get("sha256") or
            file_sha(HERE / reference["historical_arm_execution"]["path"]) !=
                reference["historical_arm_execution"]["sha256"] or
            any(file_sha(HERE / name) != digest for name, digest in
                (("baseline-execution-v1.md", reference["historical_arm_execution"]
                  ["prompt_suffix_sha256"]["baseline"]),
                 ("treatment-execution-v1.md", reference["historical_arm_execution"]
                  ["prompt_suffix_sha256"]["treatment"])))):
        raise ValueError("historical policy or plan drift")
    roots = {name: _json(HERE / artifact["path"]) for name, artifact in artifacts.items()
             if name not in {"accepted_patch"}}
    run, admission, e2e = (roots[key] for key in ("run", "quality_admission", "end_to_end"))
    patch = artifacts["accepted_patch"]["sha256"]
    manifest_sha = HISTORICAL_MANIFEST_SHA256
    if (run.get("arm") != "baseline" or run.get("task_id") != TASK_ID or
            run.get("stop_reason") != "accepted-final" or
            run.get("cost_status") != "complete" or run.get("usage_issues") != [] or
            run.get("native_attempts") != [] or
            run.get("usage", {}).get("estimated_usd") !=
                review["historical_reference"]["estimated_usd"] or
            run.get("usage", {}).get("model_calls") !=
                review["historical_reference"]["model_responses"] or
            run.get("wall_seconds") != review["historical_reference"]["run_wall_seconds"] or
            run.get("stage", {}).get("manifest_sha256") != manifest_sha or
            run.get("stage", {}).get("complete") is not True or
            run.get("stage", {}).get("events", [{}])[-1].get("patch_sha256") != patch):
        raise ValueError("historical baseline run not accepted and fully accounted")
    if (roots["plan"].get("pilot_id") != reference["historical_pilot_id"] or
            roots["plan"].get("arm_order") != ["baseline", "treatment"] or
            roots["plan"].get("runtime") != plan.get("runtime") or
            roots["preparation"].get("fixture_manifest_sha256") != manifest_sha or
            roots["preparation"].get("start_tree") != spec["start_tree"] or
            "baseline" not in roots["preparation"].get("arms", {})):
        raise ValueError("historical plan or preparation stale")
    for round_id in range(3):
        grade = roots[f"public_g{round_id}_grade"]
        if (grade.get("kind") != "public-gate" or grade.get("round") != round_id or
                grade.get("behavior_pass") is not True or
                grade.get("quality_pass") is not True or
                grade.get("manifest_sha256") != manifest_sha):
            raise ValueError(f"historical public G{round_id} grade failed or stale")
    if (admission.get("kind") != "baseline-quality-admission" or
            admission.get("pilot_id") != reference["historical_pilot_id"] or
            admission.get("arm") != "baseline" or admission.get("verdict") != "pass" or
            admission.get("manifest_sha256") != manifest_sha or
            admission.get("baseline_run_sha256") != artifacts["run"]["sha256"] or
            admission.get("preparation_sha256") != artifacts["preparation"]["sha256"] or
            admission.get("pilot_plan_sha256") != artifacts["plan"]["sha256"] or
            admission.get("accepted_patch_sha256") != patch or
            admission.get("hidden_race_grade_sha256") != artifacts["hidden_race_grade"]["sha256"] or
            admission.get("backend_grade_sha256") != artifacts["backend_grade"]["sha256"] or
            admission.get("semantic_review_sha256") !=
                artifacts["canonical_semantic_review"]["sha256"]):
        raise ValueError("historical full-quality admission stale or failed")
    for name, kind in (("hidden_race_grade", "hidden-final"),
                       ("backend_grade", "backend-regression")):
        grade = roots[name]
        if (grade.get("kind") != kind or grade.get("candidate_patch_sha256") != patch or
                grade.get("manifest_sha256") != manifest_sha or
                grade.get("behavior_pass") is not True or
                grade.get("quality_pass") is not True or
                any(check.get("exit_code") != 0 for check in grade.get("checks", []))):
            raise ValueError(f"historical {name} failed or stale")
    for name in ("canonical_semantic_review", "neutral_semantic_review",
                 "semantic_review_attestation"):
        semantic = roots[name]
        if (semantic.get("verdict") != "pass" or
                semantic.get("candidate_patch_sha256") != patch or
                semantic.get("reviewer_role") != "independent" or
                semantic.get("arm_blind") is not True):
            raise ValueError(f"historical {name} failed or stale")
    if (e2e.get("kind") != "arm-end-to-end-boundary" or e2e.get("arm") != "baseline" or
            e2e.get("manifest_sha256") != manifest_sha or
            e2e.get("pilot_plan_sha256") != artifacts["plan"]["sha256"] or
            e2e.get("preparation_sha256") != artifacts["preparation"]["sha256"] or
            e2e.get("run_sha256") != artifacts["run"]["sha256"] or
            e2e.get("quality_files_sha256") != {
                "admission": artifacts["quality_admission"]["sha256"],
                "hidden_race_grade": artifacts["hidden_race_grade"]["sha256"],
                "backend_grade": artifacts["backend_grade"]["sha256"],
                "semantic_review": artifacts["canonical_semantic_review"]["sha256"]} or
            e2e.get("end_to_end_wall_seconds") !=
                review["historical_reference"]["end_to_end_wall_seconds"]):
        raise ValueError("historical end-to-end boundary stale")
    sources = reference.get("current_source_sha256")
    if (not isinstance(sources, dict) or set(sources) != {
            "accounting.py", "collect.py", "grade.py", "quality_admission.py",
            "end_to_end.py", "transport.py", "fork_policy.py", "arm-execution-v2.json",
            "treatment-execution-v2.md", "evals/scripts/run_paired_arm.py"}):
        raise ValueError("current diagnostic source pins incomplete")
    for name, digest in sources.items():
        path = HERE.parents[1] / name if name.startswith("evals/") else HERE / name
        if file_sha(path) != digest:
            raise ValueError(f"current diagnostic source drift: {name}")
    reviewed = review.get("reviewed_current_sources", {})
    for name, entry in reviewed.items():
        relative = entry.get("path") if isinstance(entry, dict) else None
        if (not isinstance(relative, str) or name == "runner" or
                not relative.startswith("evals/long_horizon_v1/") and
                relative != "evals/scripts/run_paired_arm.py"):
            continue
        current_name = relative.removeprefix("evals/long_horizon_v1/")
        if current_name in sources and sources[current_name] != entry["sha256"]:
            raise ValueError(f"baseline-effective reviewed source changed: {name}")
    return {"schema_version": 1, "status": "passed", "claim": CLAIM,
            "historical_pilot_id": reference["historical_pilot_id"],
            "historical_manifest_sha256": manifest_sha,
            "review_sha256": REVIEW_SHA256,
            "baseline_run_sha256": artifacts["run"]["sha256"],
            "baseline_quality_admission_sha256": artifacts["quality_admission"]["sha256"],
            "baseline_end_to_end_sha256": artifacts["end_to_end"]["sha256"],
            "fresh_matched_pair": False, "paid_model_turns": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, default=HERE / "pilot-plan-v10.json")
    args = parser.parse_args()
    print(json.dumps(verify_diagnostic_reference(manifest(), _json(args.plan)), indent=2))
