"""Recompute published live token/cost arithmetic without making model calls."""

import hashlib
import json
import statistics
import subprocess
import sys
import unittest
from pathlib import Path

from evals.scripts.run_paired_arm import USAGE_KEYS, response_cost_interval


class ContextLiveEvidenceTests(unittest.TestCase):
    def test_repair_replays_original_classified_requests_without_rescoring_live_runs(self):
        root = Path(__file__).resolve().parents[1] / "live/context-repair-20261028"
        repo = Path(__file__).resolve().parents[2]
        replay = json.loads((root / "decision-replay.json").read_text(encoding="utf-8"))
        self.assertEqual(3, len(replay["records"]))
        self.assertEqual(["DELEGATE", "DIRECT", "DIRECT"],
                         [r["old_result"]["ownership"] for r in replay["records"]])
        for row in replay["records"]:
            receipt = json.loads((root / row["source_receipt"]).read_text(encoding="utf-8"))
            self.assertEqual(receipt["raw_events_sha256"], row["raw_events_sha256"])
            original = receipt["observed_resolver_decisions"][0]
            self.assertEqual(original["decision_id"], row["request"]["decision_id"])
            self.assertEqual(original["ownership"], row["old_result"]["ownership"])
            result = subprocess.run([sys.executable,
                str(repo / "plugins/codex-model-router/hooks/execution_decision.py"),
                "--config", str(repo / "plugins/codex-model-router/defaults/default-routing.json")],
                input=json.dumps(row["request"]), capture_output=True, text=True, timeout=10)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(row["candidate_result"], json.loads(result.stdout))
            self.assertEqual("DIRECT", row["candidate_result"]["ownership"])

    def test_refined_cohort_response_accounting_and_paired_verdict(self):
        root = Path(__file__).resolve().parents[1] / "live/context-refinement-20261028"
        campaign = json.loads((root / "scored/campaign.json").read_text(encoding="utf-8"))
        analysis = json.loads((root / "analysis.json").read_text(encoding="utf-8"))
        summary = json.loads((root / "scored/summary.json").read_text(encoding="utf-8"))
        records = [json.loads(p.read_text(encoding="utf-8"))
                   for p in (root / "scored").glob("scored-*.json")]
        self.assertEqual(30, len(records))
        totals = dict.fromkeys(USAGE_KEYS, 0)
        cost, calls, response_ids = 0.0, 0, set()
        for record in records:
            with self.subTest(run=record["run_id"]):
                self.assertTrue(all(record[key] for key in
                                    ("complete", "artifact_pass", "grade_complete", "usage_complete")))
                self.assertEqual([], record["usage_issues"])
                self.assertEqual([], record["usage"]["unknown_models"])
                self.assertEqual([], record["usage"]["unknown_usage"])
                run_totals = dict.fromkeys(USAGE_KEYS, 0)
                run_cost, run_calls = 0.0, 0
                for session in record["usage"]["sessions"]:
                    session_totals = dict.fromkeys(USAGE_KEYS, 0)
                    session_cost = 0.0
                    for response in session["responses"]:
                        usage = response["token_classes"]
                        self.assertNotIn(response["response_id_sha256"], response_ids)
                        response_ids.add(response["response_id_sha256"])
                        rates = campaign["rate_card"]["per_million"][response["model"]]
                        # Independent arithmetic, without the runner's pricing helper.
                        if usage["input_tokens"] > 272_000:
                            rates = [rate * (1.5 if i == 3 else 2) for i, rate in enumerate(rates)]
                        uncached_read = (usage["input_tokens"] - usage["cached_input_tokens"]
                                         - usage["cache_write_input_tokens"])
                        self.assertGreaterEqual(uncached_read, 0)
                        self.assertLessEqual(usage["reasoning_output_tokens"], usage["output_tokens"])
                        price = (uncached_read * rates[0] + usage["cached_input_tokens"] * rates[1]
                                 + usage["cache_write_input_tokens"] * rates[2]
                                 + usage["output_tokens"] * rates[3]) / 1_000_000
                        for field in ("estimated_usd", "estimated_usd_lower_bound", "estimated_usd_upper_bound"):
                            self.assertAlmostEqual(price, response[field], places=10)
                        session_cost += price
                        run_calls += 1
                        for key in USAGE_KEYS:
                            session_totals[key] += usage[key]
                    self.assertEqual(session_totals, session["token_classes"])
                    self.assertEqual(session_totals, {key: session["reported_total_usage"][key]
                                                     for key in USAGE_KEYS})
                    self.assertEqual(len(session["responses"]), session["calls"])
                    self.assertAlmostEqual(session_cost, session["estimated_usd"], places=10)
                    run_cost += session_cost
                    for key in USAGE_KEYS:
                        run_totals[key] += session_totals[key]
                self.assertEqual(run_totals, record["usage"]["token_classes"])
                self.assertEqual(run_calls, record["usage"]["model_calls"])
                self.assertAlmostEqual(run_cost, record["usage"]["estimated_usd"], places=10)
                cost += run_cost
                calls += run_calls
                for key in USAGE_KEYS:
                    totals[key] += run_totals[key]
        self.assertEqual(totals, analysis["token_classes"])
        self.assertEqual(521, calls)
        self.assertEqual(calls, analysis["scored_responses"])
        self.assertAlmostEqual(cost, analysis["scored_api_estimated_usd"], places=10)
        self.assertAlmostEqual(cost, summary["observed_campaign_usd_upper_bound"], places=10)
        self.assertLess(cost, campaign["campaign_stop_usd"])
        self.assertEqual(30, analysis["artifact_passes"])
        for case in campaign["cases"]:
            pairs = []
            for repetition in range(1, campaign["repetitions"] + 1):
                rows = {r["arm"]: r for r in records
                        if r["case_id"] == case and r["repetition"] == repetition}
                self.assertEqual({"before", "after"}, set(rows))
                before, after = rows["before"], rows["after"]
                def tokens(row):
                    counts = row["usage"]["token_classes"]
                    return counts["input_tokens"] + counts["output_tokens"]
                pairs.append({"wall_ratio": after["wall_seconds"] / before["wall_seconds"],
                              "token_ratio": tokens(after) / tokens(before),
                              "estimated_cost_ratio": after["usage"]["estimated_usd"] /
                              before["usage"]["estimated_usd"]})
                saved = analysis["cases"][case]["pairs"][repetition - 1]
                for key, value in pairs[-1].items():
                    self.assertAlmostEqual(value, saved[key], places=10)
            medians = {key: statistics.median(pair[key] for pair in pairs) for key in pairs[0]}
            self.assertEqual(medians, analysis["cases"][case]["median_paired_ratios"])
            self.assertEqual(medians, summary["cases"][case]["median_paired_ratios"])
            passed = all(value <= analysis["thresholds"][key] for key, value in medians.items())
            self.assertEqual(passed, analysis["cases"][case]["exploratory_artifact_and_economics_pass"])
            self.assertEqual(case != "investigation-reuse", passed)
        self.assertEqual("NOT_PASSED_INVESTIGATION_WALL_AND_TOKENS", analysis["verdict"])

    def test_refined_context_observations_and_supplementary_audit_bindings(self):
        root = Path(__file__).resolve().parents[1] / "live/context-refinement-20261028"
        audit = json.loads((root / "process-audit.json").read_text(encoding="utf-8"))
        self.assertEqual(30, len(audit["records"]))
        self.assertEqual(30, len({r["run_id"] for r in audit["records"]}))
        for row in audit["records"]:
            record = json.loads((root / "scored" / f"{row['run_id']}.json").read_text(encoding="utf-8"))
            self.assertEqual(record["raw_events_sha256"], row["raw_events_sha256"])
            if record["arm"] == "after":
                prompts = [e for e in record["hook_events"] if e["event"] == "userPromptSubmit"]
                self.assertTrue(prompts)
                self.assertTrue(all(e["context_bytes"] == 0 for e in prompts))
            decisions = row["resolver_decisions_in_mixed_command_outputs"]
            if record["case_id"] == "direct-small-control":
                self.assertEqual(["DIRECT", "DIRECT"], [d["ownership"] for d in decisions])
                self.assertEqual(0, record["native_workers"])
                self.assertEqual(3, len(record["turns"]))
                final = row["parent_turn_observations"][-1]
                self.assertEqual(record["turns"][-1]["turn_id"], final["turn_id"])
                self.assertEqual(0, final["completed_commands"])
                self.assertEqual(0, final["route_lines"])
            if record["case_id"] == "serial-escalation":
                self.assertEqual(["DIRECT", "DELEGATE"], [d["ownership"] for d in decisions])
                self.assertEqual("scope-expanded", decisions[-1]["escalation_trigger"])
            if record["case_id"] == "parallel-disjoint":
                self.assertEqual("PARALLEL", decisions[-1]["delegate_topology"])
                self.assertEqual(3, record["native_workers"])

    def test_refined_platform_receipts_reuse_source_json_without_billing_again(self):
        root = Path(__file__).resolve().parents[1] / "live/context-refinement-20261028"
        manifest = json.loads((root / "capability-provenance.json").read_text(encoding="utf-8"))
        preflight = json.loads((root / "preflight.json").read_text(encoding="utf-8"))
        repo = Path(__file__).resolve().parents[2]
        self.assertEqual(0, preflight["model_calls"])
        self.assertEqual(set(preflight["reused_capabilities"]), set(manifest["files"]))
        for name, metadata in manifest["files"].items():
            value = json.loads((root / "capability" / name).read_text(encoding="utf-8"))
            source = json.loads((repo / metadata["source_path"]).read_text(encoding="utf-8"))
            self.assertEqual(source, value)
            canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
            self.assertEqual(hashlib.sha256(canonical).hexdigest(), metadata["canonical_json_sha256"])
            self.assertEqual(preflight["reused_capabilities"][name], metadata["original_local_sha256"])

    def test_published_response_classes_reconcile_for_all_scored_sessions(self):
        root = Path(__file__).resolve().parents[1] / "live/context-regression-20261028"
        campaign = json.loads((root / "scored/campaign.json").read_text(encoding="utf-8"))
        rates = campaign["rate_card"]["per_million"]
        records = [json.loads(p.read_text(encoding="utf-8")) for p in (root / "scored").glob("scored-*.json")]
        self.assertEqual(30, len(records))
        for record in records:
            with self.subTest(run=record["run_id"]):
                total = {key: 0 for key in USAGE_KEYS}
                cost, calls = 0.0, 0
                for session in record["usage"]["sessions"]:
                    for response in session["responses"]:
                        usage = response["token_classes"]
                        low, high = response_cost_interval(usage, response["model"], rates)
                        self.assertEqual(low, high)
                        cost += low
                        calls += 1
                        for key in USAGE_KEYS:
                            total[key] += usage[key]
                self.assertEqual(total, record["usage"]["token_classes"])
                self.assertEqual(calls, record["usage"]["model_calls"])
                self.assertAlmostEqual(cost, record["usage"]["estimated_usd"], places=8)
                self.assertEqual([], record["usage_issues"])

    def test_lost_timer_and_failed_strict_scores_are_not_rewritten(self):
        root = Path(__file__).resolve().parents[1] / "live/context-regression-20261028"
        missing = json.loads((root / "scored/scored-architecture-review-r1-before.json").read_text(encoding="utf-8"))
        self.assertIsNone(missing["wall_seconds"])
        analysis = json.loads((root / "analysis.json").read_text(encoding="utf-8"))
        self.assertEqual(24, analysis["strict_artifact_passes"])
        self.assertEqual(6, analysis["strict_artifact_failures"])
        self.assertTrue(analysis["verdict"].startswith("NOT_PASSED"))


if __name__ == "__main__":
    unittest.main()
