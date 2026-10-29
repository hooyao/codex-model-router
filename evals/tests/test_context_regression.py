"""Report incomplete session accounting as inconclusive, never a cost win."""

import json
import tempfile
import shutil
import unittest
from pathlib import Path

from evals.scripts.context_regression import REPO, business_tree, grade_artifacts, summarize
from evals.scripts.artifact_reader import ArtifactReader
from evals.scripts.fixture import load_fixture


class ContextRegressionReportTests(unittest.TestCase):
    def records(self, root, *, issue=False, unknown_cost=False):
        for repetition in range(1, 4):
            for arm in ("before", "after"):
                row = {"case_id": "direct-small-control", "repetition": repetition, "arm": arm,
                       "complete": True, "usage_complete": True, "artifact_pass": True,
                       "usage_issues": ["child usage missing"] if issue and arm == "after" else [],
                       "wall_seconds": 1,
                       "usage": {"token_classes": {"input_tokens": 100, "output_tokens": 100,
                                                    "reasoning_output_tokens": 90 if arm == "after" else 10},
                                 "estimated_usd": None if unknown_cost and arm == "after" else .001}}
                (root / f"scored-{repetition}-{arm}.json").write_text(json.dumps(row))

    def test_child_accounting_issue_is_not_promoted_by_aggregate_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.records(root, issue=True)
            case = summarize(root)["cases"]["direct-small-control"]
            self.assertFalse(case["exploratory_artifact_and_economics_pass"])
            self.assertTrue(all(pair["status"] == "incomplete" for pair in case["pairs"]))

    def test_unknown_exact_cost_is_not_a_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.records(root, unknown_cost=True)
            case = summarize(root)["cases"]["direct-small-control"]
            self.assertIsNone(case["median_paired_ratios"]["estimated_cost_ratio"])
            self.assertFalse(case["exploratory_artifact_and_economics_pass"])

    def test_reasoning_is_not_added_twice_to_total_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.records(root)
            case = summarize(root)["cases"]["direct-small-control"]
            self.assertEqual(1, case["median_paired_ratios"]["token_ratio"])
            self.assertTrue(case["exploratory_artifact_and_economics_pass"])

    def test_explicit_new_json_oracle_accepts_terminal_whitespace_only(self):
        source = REPO / "evals/fixtures/architecture-review"
        definition = load_fixture(source / "fixture.json")
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            shutil.copytree(source / "reference", workspace)
            decision = workspace / "decision.json"
            decision.write_bytes(decision.read_bytes().rstrip(b"\r\n"))
            reader = ArtifactReader(Path("unused"), Path(directory) / "home", workspace)
            actual = business_tree(workspace)
            self.assertFalse(grade_artifacts(definition, actual, workspace, reader))
            self.assertTrue(grade_artifacts(definition, actual, workspace, reader, True))
            value = json.loads(decision.read_text())
            value["schema_version"] = True
            decision.write_text(json.dumps(value, separators=(",", ":")))
            self.assertFalse(grade_artifacts(definition, business_tree(workspace), workspace, reader, True))

    def test_new_json_oracle_does_not_allow_unrelated_file_changes(self):
        source = REPO / "evals/fixtures/architecture-review"
        definition = load_fixture(source / "fixture.json")
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            shutil.copytree(source / "reference", workspace)
            (workspace / "current.md").write_text("changed unrelated input")
            reader = ArtifactReader(Path("unused"), Path(directory) / "home", workspace)
            self.assertFalse(grade_artifacts(definition, business_tree(workspace), workspace, reader, True))


if __name__ == "__main__":
    unittest.main()
