"""Replay the frozen lifecycle oracle and require its supplemental race gate."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from evals.scripts.run_paired_arm import pinned_prompt, sha256


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.json"))
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--candidate-patch", type=Path)
    parser.add_argument("--grade-workspace", type=Path, required=True)
    args = parser.parse_args()
    config_path = args.config.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    pinned_prompt(config_path, config)
    env = config["environment"]
    linux_binary = ROOT / "evals/live/paired-swepro-flipt-20260923/tools/usr/lib/go-1.22/bin/go"
    if sha256(linux_binary) != env["linux_go_sha256"]:
        raise ValueError("Linux Go binary hash mismatch")
    version = subprocess.check_output(["wsl", "-d", "Ubuntu", "--", env["linux_go"], "version"], text=True).strip()
    if version != env["linux_go_version"]:
        raise ValueError("Linux Go version mismatch")
    oracle = config["benchmark"]
    command = [sys.executable, str(ROOT / "evals/scripts/grade_swepro_go.py"),
               "--seed", str(args.seed.resolve()), "--arm", str(args.arm.resolve()),
               "--test-patch", str(config_path.parent / oracle["oracle_patch"]),
               "--oracle-config", str(config_path.parent / oracle["oracle_config"]),
               "--grade-workspace", str(args.grade_workspace.resolve()),
               "--go-executable", env["linux_go"], "--packages", *oracle["oracle_packages"]]
    if args.candidate_patch:
        command += ["--candidate-patch", str(args.candidate_patch.resolve())]
    subprocess.run(command, check=True, timeout=900, capture_output=True)
    grade = args.grade_workspace.resolve()
    primary = json.loads((grade / "grade.json").read_text(encoding="utf-8"))
    race_command = ["wsl", "-d", "Ubuntu", "--", env["linux_go"], "test", "-race", "-count=3",
                    "-run", "TestLifecycle", "./internal/oci", "./internal/oci/ecr"]
    started = time.monotonic()
    race = subprocess.run(race_command, cwd=grade, capture_output=True, timeout=900)
    (grade / "race.stdout").write_bytes(race.stdout)
    (grade / "race.stderr").write_bytes(race.stderr)
    result = {"task_id": oracle["task_id"], "config_sha256": sha256(config_path),
              "grade_json_sha256": sha256(grade / "grade.json"),
              "candidate_patch_sha256": primary["candidate_patch_sha256"],
              "test_patch_sha256": primary["test_patch_sha256"],
              "package_tests_pass": primary["quality_pass"],
              "race_command": race_command, "race_exit_code": race.returncode,
              "race_wall_seconds": round(time.monotonic() - started, 3),
              "race_stdout_sha256": hashlib.sha256(race.stdout).hexdigest(),
              "race_stderr_sha256": hashlib.sha256(race.stderr).hexdigest(),
              "quality_pass": primary["quality_pass"] and race.returncode == 0,
              "semantic_review_required": ["candidate regression tests", "maintainer documentation"]}
    (grade / "lifecycle-quality.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["quality_pass"] else 1)


if __name__ == "__main__":
    main()
