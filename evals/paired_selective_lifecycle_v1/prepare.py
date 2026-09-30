"""Prepare identical clean workspaces from the frozen source plus seed patch.

Only source assets and the seed patch enter workspaces. Oracle and positive
control files remain in the evaluator fixture directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from evals.scripts.prepare_selective_fixture import extract_app_layer, run, sha256
from evals.scripts.run_paired_arm import pinned_prompt


def prepare(config_path: Path, destination: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    pinned_prompt(config_path, config)
    spec = config["fixture"]
    patch = config_path.parent / spec["seed_patch"]
    if sha256(patch) != spec["seed_patch_sha256"]:
        raise ValueError("seed patch hash mismatch")
    if destination.exists():
        raise ValueError(f"destination already exists: {destination}")
    seed = destination / "seed"
    extract_app_layer(config_path.parent / config["benchmark"]["source_layer"], seed)
    source_tree = run("git", "rev-parse", "HEAD^{tree}", cwd=seed)
    if source_tree != spec["source_tree"]:
        raise ValueError("source tree differs from frozen upstream")
    subprocess.run(["git", "apply", "--binary", "-"], input=patch.read_bytes(), cwd=seed, check=True)
    run("git", "add", "--all", cwd=seed)
    run("git", "commit", "-q", "-m", "Pinned lifecycle v1 seed", cwd=seed)
    tree = run("git", "rev-parse", "HEAD^{tree}", cwd=seed)
    if tree != spec["initial_tree"]:
        raise ValueError("seed tree differs from frozen lifecycle fixture")
    arms = {}
    for name in ("baseline", "treatment"):
        workspace = destination / f"{name}-workspace"
        shutil.copytree(seed, workspace)
        if run("git", "status", "--porcelain", "--untracked-files=all", cwd=workspace):
            raise ValueError(f"{name} is dirty")
        arms[name] = {"workspace": str(workspace.resolve()), "initial_tree": tree,
                      "initial_diff_sha256": hashlib.sha256(b"").hexdigest()}
    result = {"task_id": config["benchmark"]["task_id"], "config_sha256": sha256(config_path),
              "source_tree": source_tree, "seed_patch_sha256": sha256(patch),
              "initial_tree": tree, "prompt_sha256": config["prompt"]["combined_sha256"],
              "arms": arms, "oracle_in_arm_workspaces": False}
    (destination / "preparation.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.json"))
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.config.resolve(), args.destination.resolve()), indent=2))
