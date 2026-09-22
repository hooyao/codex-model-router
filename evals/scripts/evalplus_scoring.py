"""Score only attempted, infrastructure-valid samples; never adjust cost ledgers."""
import json
from pathlib import Path

EXCLUSIONS = Path(__file__).resolve().parents[1] / "evalplus/infrastructure-invalid.json"


def excluded_runs(campaign_state_sha256):
    registry = json.loads(EXCLUSIONS.read_text(encoding="utf-8"))
    return {run_id for campaign in registry["campaigns"]
            if campaign["campaign_state_sha256"] == campaign_state_sha256
            for run_id in campaign["run_ids"]}


def score_rows(rows, campaign_state_sha256, attempted_run_ids):
    attempted = set(attempted_run_ids)
    invalid = excluded_runs(campaign_state_sha256)
    eligible = [row for row in rows if row["run_id"] in attempted and row["run_id"] not in invalid]
    passed = sum(row["base_pass"] and row["plus_pass"] for row in eligible)
    return {"scheduled_runs": len(rows), "attempted_runs": sum(row["run_id"] in attempted for row in rows),
            "excluded_infrastructure_runs": sorted(row["run_id"] for row in rows if row["run_id"] in invalid),
            "scored_runs": len(eligible), "passed_runs": passed,
            "pass_rate": passed / len(eligible) if eligible else None,
            "complete": len(eligible) == len(rows), "grading_complete": True}
