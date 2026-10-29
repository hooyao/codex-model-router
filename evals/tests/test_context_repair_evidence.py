"""Replay the published 1.3.3 live cohort without external model calls."""

import hashlib
import json
import statistics
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "live/context-repair-20261029"
KEYS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
        "output_tokens", "reasoning_output_tokens")


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


class ContextRepairEvidenceTests(unittest.TestCase):
    def test_all_response_classes_and_prices_reconcile(self):
        rates = read(ROOT / "scored/campaign.json")["rate_card"]["per_million"]
        analysis = read(ROOT / "analysis.json")
        details = {arm["run_id"]: arm for case in analysis["cases"].values()
                   for pair in case["pairs"] for arm in pair["arms"].values()}
        records = [read(p) for p in (ROOT / "scored").glob("scored-*.json")]
        self.assertEqual(30, len(records))
        total, cost, calls, ids = dict.fromkeys(KEYS, 0), 0.0, 0, set()
        for row in records:
            self.assertTrue(all(row[k] for k in ("complete", "artifact_pass", "grade_complete", "usage_complete")))
            self.assertEqual([], row["usage_issues"])
            self.assertEqual([], row["usage"]["unknown_usage"])
            self.assertEqual([], row["usage"]["unknown_models"])
            run_total, run_cost, run_calls = dict.fromkeys(KEYS, 0), 0.0, 0
            components = dict.fromkeys(("uncached_read", "cached_read", "cache_write", "output"), 0.0)
            for session in row["usage"]["sessions"]:
                subtotal, session_cost = dict.fromkeys(KEYS, 0), 0.0
                for response in session["responses"]:
                    t, r = response["token_classes"], rates[response["model"]]
                    self.assertNotIn(response["response_id_sha256"], ids)
                    ids.add(response["response_id_sha256"])
                    self.assertTrue(all(type(t[k]) is int and t[k] >= 0 for k in KEYS))
                    self.assertLessEqual(t["reasoning_output_tokens"], t["output_tokens"])
                    ordinary = t["input_tokens"] - t["cached_input_tokens"] - t["cache_write_input_tokens"]
                    self.assertGreaterEqual(ordinary, 0)
                    if t["input_tokens"] > 272_000:
                        r = [rate * (1.5 if i == 3 else 2) for i, rate in enumerate(r)]
                    price = (ordinary * r[0] + t["cached_input_tokens"] * r[1]
                             + t["cache_write_input_tokens"] * r[2] + t["output_tokens"] * r[3]) / 1e6
                    for key, value in zip(components, (ordinary * r[0], t["cached_input_tokens"] * r[1],
                            t["cache_write_input_tokens"] * r[2], t["output_tokens"] * r[3])):
                        components[key] += value / 1e6
                    for field in ("estimated_usd", "estimated_usd_lower_bound", "estimated_usd_upper_bound"):
                        self.assertAlmostEqual(price, response[field], places=10)
                    for key in KEYS:
                        subtotal[key] += t[key]
                    session_cost += price
                    run_calls += 1
                self.assertEqual(subtotal, session["token_classes"])
                self.assertEqual(subtotal, {k: session["reported_total_usage"][k] for k in KEYS})
                self.assertEqual(len(session["responses"]), session["calls"])
                self.assertAlmostEqual(session_cost, session["estimated_usd"], places=10)
                for key in KEYS:
                    run_total[key] += subtotal[key]
                run_cost += session_cost
            self.assertEqual(run_total, row["usage"]["token_classes"])
            self.assertEqual(run_calls, row["usage"]["model_calls"])
            self.assertAlmostEqual(run_cost, row["usage"]["estimated_usd"], places=10)
            for key, value in components.items():
                self.assertAlmostEqual(value, details[row["run_id"]]["cost_components_usd"][key], places=10)
            for key in KEYS:
                total[key] += run_total[key]
            cost += run_cost
            calls += run_calls
        self.assertEqual(525, calls)
        self.assertEqual(calls, analysis["scored_responses"])
        self.assertEqual(total, analysis["token_classes"])
        self.assertAlmostEqual(cost, analysis["scored_api_estimated_usd"], places=10)
        self.assertAlmostEqual(cost, read(ROOT / "scored/summary.json")["observed_campaign_usd_upper_bound"], places=10)
        self.assertLess(cost, 7)

    def test_paired_medians_components_and_serial_failure_are_retained(self):
        analysis = read(ROOT / "analysis.json")
        summary = read(ROOT / "scored/summary.json")
        self.assertEqual({"wall_ratio": 1.15, "token_ratio": 1.05, "estimated_cost_ratio": 1.05},
                         analysis["thresholds"])
        for case, results in analysis["cases"].items():
            ratios = []
            self.assertEqual(3, len(results["pairs"]))
            for pair in results["pairs"]:
                rep = pair["repetition"]
                b, a = [read(ROOT / "scored" / f"scored-{case}-r{rep}-{arm}.json")
                        for arm in ("before", "after")]
                tokens = lambda r: r["usage"]["token_classes"]["input_tokens"] + r["usage"]["token_classes"]["output_tokens"]
                values = {"wall_ratio": a["wall_seconds"] / b["wall_seconds"],
                          "token_ratio": tokens(a) / tokens(b),
                          "estimated_cost_ratio": a["usage"]["estimated_usd"] / b["usage"]["estimated_usd"]}
                for key, value in values.items():
                    self.assertAlmostEqual(value, pair[key], places=10)
                for arm in ("before", "after"):
                    detail = pair["arms"][arm]
                    self.assertAlmostEqual(sum(detail["cost_components_usd"].values()), detail["api_estimated_usd"], places=10)
                ratios.append(values)
            medians = {k: statistics.median(v[k] for v in ratios) for k in ratios[0]}
            self.assertEqual(medians, results["median_paired_ratios"])
            self.assertEqual(medians, summary["cases"][case]["median_paired_ratios"])
            passed = all(value <= analysis["thresholds"][key] for key, value in medians.items())
            self.assertEqual(case != "serial-escalation", passed)
            self.assertEqual(passed, results["exploratory_artifact_and_economics_pass"])
        self.assertEqual("NOT_PASSED_SERIAL_TOKENS_AND_COST", analysis["verdict"])

    def test_audit_binds_reused_decisions_escalation_and_zero_prompt_context(self):
        audit = read(ROOT / "process-audit.json")
        self.assertEqual(30, len(audit["records"]))
        self.assertEqual(30, len({r["run_id"] for r in audit["records"]}))
        for item in audit["records"]:
            row = read(ROOT / "scored" / f"{item['run_id']}.json")
            self.assertEqual(row["raw_events_sha256"], item["raw_events_sha256"])
            decisions = item["resolver_decisions_in_mixed_command_outputs"]
            if row["arm"] == "after":
                prompts = [e for e in row["hook_events"] if e["event"] == "userPromptSubmit"]
                self.assertTrue(prompts)
                self.assertTrue(all(e["context_bytes"] == 0 for e in prompts))
            if row["case_id"] == "investigation-reuse":
                self.assertEqual(["DIRECT"], [d["ownership"] for d in decisions])
                self.assertEqual(0, row["native_workers"])
            if row["case_id"] == "serial-escalation":
                self.assertEqual(["DIRECT", "DELEGATE"], [d["ownership"] for d in decisions])
                self.assertEqual("scope-expanded", decisions[-1]["escalation_trigger"])
                self.assertEqual(1, row["native_workers"])
            if row["case_id"] == "parallel-disjoint":
                self.assertEqual("PARALLEL", decisions[-1]["delegate_topology"])
                self.assertEqual(3, row["native_workers"])
            if row["case_id"] == "architecture-review":
                worker_ids = {s["id"] for s in row["usage"]["sessions"] if s["parent_id"]}
                self.assertEqual(2, len(worker_ids))

    def test_platform_provenance_and_postflight_preserve_observed_subjects(self):
        manifest = read(ROOT / "capability-provenance.json")
        pre, post = read(ROOT / "preflight.json"), read(ROOT / "postflight.json")
        self.assertEqual(0, pre["model_calls"])
        self.assertEqual(0, post["model_calls"])
        self.assertFalse(pre["runtime"]["version_allowlist"])
        self.assertEqual(4, len(manifest["files"]))
        repo = Path(__file__).resolve().parents[2]
        for name, metadata in manifest["files"].items():
            value = read(ROOT / "capability" / name)
            self.assertEqual(read(repo / metadata["source_path"]), value)
            canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
            self.assertEqual(hashlib.sha256(canonical).hexdigest(), metadata["canonical_json_sha256"])
            self.assertEqual(pre["reused_capabilities"][name], metadata["original_local_sha256"])
        for arm in ("before", "after"):
            self.assertEqual(pre["observations"][arm]["subject_ref"], post["observations"][arm]["subject_ref"])
            self.assertTrue(post["observations"][arm]["tracked_subject_bytes_unchanged"])
        for flag in ("cli_unchanged", "runner_unchanged", "capability_adapter_unchanged", "fixture_definitions_unchanged"):
            self.assertTrue(post[flag])


if __name__ == "__main__":
    unittest.main()
