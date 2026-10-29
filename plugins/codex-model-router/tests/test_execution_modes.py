"""Exercise matched-mode composition and the observed extraction regression."""

import copy
import itertools
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "hooks"))
from describe_router import describe_context
from execution_decision import resolve_routing_decision
from routing_config import configured_execution_mode, load_config


def bounded_extraction_request(config):
    request = describe_context(Path("routing.json"), config)["request_template"]
    request["decision_id"] = "bounded-extraction-and-update"
    request["matched_example_ids"] = ["luna-extraction", "luna-local-bounded-change"]
    request["signals"] = {key: False for key in request["signals"]}
    for key in ("one_local_scope", "bounded_known_outcome", "permissions_confirmed",
                "safety_constraints_known", "verification_plan_present", "write_scope_known"):
        request["signals"][key] = True
    return request


class ExecutionModeTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(PLUGIN / "defaults/default-routing.json")

    def test_matched_modes_compose_without_treating_evaluation_as_a_conflict(self):
        combinations = [(("evaluate",), "evaluate"), (("direct",), "direct"),
                        (("delegate",), "delegate"), (("direct", "evaluate"), "direct"),
                        (("delegate", "evaluate"), "delegate"),
                        (("direct", "delegate"), "delegate"),
                        (("direct", "evaluate", "delegate"), "delegate"),
                        (("evaluate", "evaluate"), "evaluate")]
        for schema in (3, 4):
            for modes, expected in combinations:
                for order in set(itertools.permutations(modes)):
                    with self.subTest(schema=schema, modes=order):
                        config = copy.deepcopy(self.config)
                        config["schema_version"] = schema
                        config["execution_policy"]["default_mode"] = "delegate"
                        ids = []
                        for example, mode in zip(config["examples"], order):
                            example["execution_mode"] = mode
                            ids.append(example["id"])
                        self.assertEqual(expected, configured_execution_mode(config, ids))
        self.config["execution_policy"]["default_mode"] = "delegate"
        self.assertEqual("delegate", configured_execution_mode(self.config, []))

    def test_observed_extraction_and_local_edit_match_passes_the_real_resolver_cli(self):
        request = bounded_extraction_request(self.config)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "routing.json"
            path.write_text(json.dumps(self.config), encoding="utf-8")
            result = subprocess.run([sys.executable, str(PLUGIN / "hooks/execution_decision.py"),
                                     "--config", str(path)], input=json.dumps(request),
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(0, result.returncode, result.stderr)
            decision = json.loads(result.stdout)
            self.assertEqual("DIRECT", decision["ownership"])
            self.assertEqual("direct", decision["configured_mode"])
            self.assertEqual("bounded-direct-fast-path", decision["matched_rule"])

    def test_mixed_evaluate_match_preserves_all_direct_gate_and_review_requirements(self):
        request = bounded_extraction_request(self.config)
        required = {key for key, value in request["signals"].items() if value is True}
        unsafe = {"network_or_sync", "long_running_or_monitoring", "failure_or_recovery",
                  "named_multistep_runbook", "substantive_research_or_investigation",
                  "independent_review_required", "high_risk"}
        for field in required | unsafe:
            for value in (None, False) if field in required else (None, True):
                with self.subTest(signal=field, value=value):
                    changed = copy.deepcopy(request)
                    changed["signals"][field] = value
                    decision = resolve_routing_decision(self.config, changed)
                    self.assertEqual("DELEGATE", decision["ownership"])
                    if field in {"independent_review_required", "high_risk"}:
                        self.assertEqual("INDEPENDENT_REVIEW", decision["verification_requirement"])

    def test_explicit_workspace_delegation_and_escalation_still_take_precedence(self):
        request = bounded_extraction_request(self.config)
        extraction = next(e for e in self.config["examples"] if e["id"] == "luna-extraction")
        extraction["execution_mode"] = "delegate"
        decision = resolve_routing_decision(self.config, request)
        self.assertEqual("DELEGATE", decision["ownership"])
        self.assertEqual("configured-delegate", decision["matched_rule"])
        extraction["execution_mode"] = "evaluate"
        request.update(phase="reclassification", prior_ownership="DIRECT", escalation_trigger="scope-expanded")
        decision = resolve_routing_decision(self.config, request)
        self.assertEqual("DELEGATE", decision["ownership"])
        self.assertEqual("scope-expanded", decision["escalation_trigger"])


if __name__ == "__main__":
    unittest.main()
