"""Zero-model smoke for the pinned desktop CLI, host, arms, and App Server."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from evals.long_horizon_v1.common import manifest, write_json_new
from evals.long_horizon_v1.runtime_binding import verify_arm_runtime, verify_cli
from evals.long_horizon_v1.prepare import verify_prepared


def smoke(prepared: Path, output: Path) -> dict:
    spec = manifest()
    cli = Path(spec["runtime"]["cli"])
    binary = verify_cli(cli, spec)
    host_help = subprocess.run([binary["code_mode_host"], "--help"],
                               capture_output=True, text=True, encoding="utf-8", timeout=10)
    if host_help.returncode or "--listen" not in host_help.stdout:
        raise ValueError("pinned code-mode host did not start")
    arms = {}
    for arm in ("baseline", "treatment"):
        workspace = prepared / arm
        preparation = verify_prepared(prepared, arm, spec)
        arms[arm] = {"preparation": preparation,
                     "runtime": verify_arm_runtime(cli, workspace, spec, arm)}
    receipt = {"schema_version": 1, "status": "passed", "model_turns": 0,
               "cli": binary,
               "host_help_exit_code": host_help.returncode, "arms": arms,
               "live_enabled": spec["live_enabled"]}
    write_json_new(output, receipt)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = smoke(args.prepared.resolve(), args.output.resolve())
    print(json.dumps({"status": result["status"], "model_turns": result["model_turns"],
                      "output": str(args.output.resolve())}))
