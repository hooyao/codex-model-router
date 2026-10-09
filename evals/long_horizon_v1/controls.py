"""Run frozen offline controls and expose every missing readiness condition."""
from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
import re
import sys
from unittest.mock import patch as mock_patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from evals.long_horizon_v1.common import HERE, TASK_ID, file_sha, fresh_directory, manifest, write_json_new
from evals.long_horizon_v1.grade import grade

CONTROL_EXPECTATIONS = {
    "seed": [(0, False)],
    "positive": [(0, True), (1, True), (2, True)],
    "round0-only": [(0, True), (1, False)],
    "round1-only": [(0, True), (1, True), (2, False)],
    "ignore-reference": [(0, False)],
    "wrong-manifest-digest": [(2, False)],
    "stale-publication": [(1, False)],
    "swallowed-explicit-error": [(1, False)],
    "leaked-stream": [(2, False)],
    "broken-close": [(2, False)],
    "global-blocking": [(0, True), (1, False)],
    "per-reference-serialization": [(0, True), (1, True), (2, True)],
}
FAILURE_MARKERS = {("seed", 0): "TestBenchmarkG0ReferenceSelection",
                   ("round0-only", 1): "TestBenchmarkG1AliasAndMove",
                   ("round1-only", 2): ("TestBenchmarkG2CancelledAndClosed",
                                         "TestBenchmarkOracleHeldCallbackPublicationAndShutdown"),
                   ("ignore-reference", 0): "TestBenchmarkG0ReferenceSelection",
                   ("wrong-manifest-digest", 2): "TestBenchmarkOracleManifestIdentityViews",
                   ("stale-publication", 1): "TestBenchmarkG1AliasAndMove",
                   ("swallowed-explicit-error", 1): "TestBenchmarkG1AliasAndMove",
                   ("leaked-stream", 2): "TestBenchmarkOracleAllSuppliedStreamsCloseOnFailure",
                   ("broken-close", 2): "TestBenchmarkG2CancelledAndClosed",
                   ("global-blocking", 1): "TestBenchmarkG1UnrelatedReferenceProgress"}
FAILURE_LINE = re.compile(r"^--- FAIL: ([^ (]+)", re.MULTILINE)


def expected_failure_names(name: str, round_id: int, should_pass: bool) -> list[str]:
    if should_pass:
        return []
    marker = FAILURE_MARKERS[(name, round_id)]
    return list(marker) if isinstance(marker, tuple) else [marker]


def controls(prepared: Path, output: Path, build_cache: Path | None = None) -> dict:
    spec = manifest()
    fresh_directory(output)
    # Go keys compiled objects by source and flags. Reusing one cache for the
    # serial evaluator-only controls avoids rebuilding dependencies per replay;
    # ordinary grades and arm replays retain their private caches.
    shared_cache = build_cache if build_cache is not None else output / "_go-build-cache"
    if build_cache is None:
        shared_cache.mkdir()
    elif not shared_cache.is_dir():
        raise ValueError("specified Go build cache does not exist")
    grade_module = importlib.import_module("evals.long_horizon_v1.grade")
    original_wsl_path = grade_module.wsl_path
    shared_linux_cache = original_wsl_path(shared_cache)

    def control_wsl_path(path: Path) -> str:
        if path.name == "go-build-cache":
            return shared_linux_cache
        return original_wsl_path(path)

    entries = {}
    control_specs = spec.get("controls", {})
    for name, expectations in CONTROL_EXPECTATIONS.items():
        if name == "seed":
            patch = output / "seed.patch"
            patch.write_bytes(b"")
        else:
            descriptor = control_specs.get(name)
            if not isinstance(descriptor, dict):
                entries[name] = {"status": "missing-frozen-control"}
                continue
            patch = HERE / descriptor["path"]
            if not patch.is_file() or file_sha(patch) != descriptor["sha256"]:
                entries[name] = {"status": "control-hash-drift"}
                continue
        checks = []
        for round_id, should_pass in expectations:
            with mock_patch.object(grade_module, "wsl_path", control_wsl_path):
                receipt = grade(patch, prepared, output / f"{name}-g{round_id}", round_id,
                                hidden=(round_id == 2),
                                run_race=(name in ("positive", "per-reference-serialization") and round_id == 2))
            observed = receipt["behavior_pass"]
            marker = FAILURE_MARKERS.get((name, round_id))
            failures = sorted(set(FAILURE_LINE.findall(
                (output / f"{name}-g{round_id}" / "go-test" / "stdout.txt").read_text(
                    encoding="utf-8", errors="replace"))))
            expected_failures = expected_failure_names(name, round_id, should_pass)
            exact_failures = failures == expected_failures
            checks.append({"round": round_id, "expected_pass": should_pass,
                           "observed_pass": observed,
                           "expected_failure_marker": list(marker) if isinstance(marker, tuple) else marker,
                           "observed_failures": failures,
                           "exact_failures": exact_failures,
                           "race_pass": (round_id != 2 or name not in
                                         ("positive", "per-reference-serialization") or
                                         len(receipt["checks"]) == 2 and
                                         receipt["checks"][1]["exit_code"] == 0),
                           "grade_sha256": file_sha(output / f"{name}-g{round_id}" / "grade.json")})
        entries[name] = {"status": "matched" if all(item["expected_pass"] == item["observed_pass"]
                                                    and item["exact_failures"] and item["race_pass"]
                                                    for item in checks) else "failed", "checks": checks}
    ready = all(item["status"] == "matched" for item in entries.values())
    result = {"schema_version": 1, "task_id": TASK_ID,
              "manifest_sha256": file_sha(HERE / "manifest.json"),
              "build_cache": "serial evaluator-only shared Go build cache",
              "controls_pass": ready,
              "live_enabled": spec.get("live_enabled", False) is True,
              "ready_for_live": ready and spec.get("live_enabled", False) is True,
              "controls": entries}
    write_json_new(output / "controls.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--build-cache", type=Path,
                        help="reuse an existing evaluator-only Go build cache")
    args = parser.parse_args()
    result = controls(args.prepared.resolve(), args.output.resolve(),
                      args.build_cache.resolve() if args.build_cache else None)
    print(json.dumps({"ready_for_live": result["ready_for_live"],
                      "control_statuses": {name: value["status"] for name, value in result["controls"].items()}}, indent=2))
