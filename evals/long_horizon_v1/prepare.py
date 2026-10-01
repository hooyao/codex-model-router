"""Create identical clean Flipt arms and reveal only round 0."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from evals.long_horizon_v1.common import (HERE, SEED_PATCH, SOURCE, SOURCE_TREE,
    START_TREE, TASK_ID, checked_run, copy_assets, file_sha, fresh_directory,
    git_tree, manifest, write_json_new)
from evals.scripts.prepare_selective_fixture import extract_app_layer
from evals.long_horizon_v1.runtime_binding import bind_arm_config, fixture_config
from evals.long_horizon_v1.runtime_binding import arm_execution, verify_arm_config


def _head(workspace: Path) -> str:
    return checked_run(["git", "rev-parse", "HEAD"], cwd=workspace).decode().strip()


def _expected_reveal(spec: dict) -> dict[str, str]:
    names = ("round0.md", "environment.md", "checkpoint_hash.py",
             "submit_checkpoint.py")
    result = {".benchmark/" + name: spec["assets"][name] for name in names}
    for name, digest in spec["assets"].items():
        if name.startswith("public/g0/"):
            result[".benchmark/public/" + name.removeprefix("public/g0/")] = digest
    return result


def _raise_walk_error(error: OSError) -> None:
    raise error


def _walk_paths(workspace: Path, excluded: frozenset[str]):
    """Yield paths without entering excluded trees; surface scan failures."""
    for root, directories, files in os.walk(workspace, topdown=True,
                                            onerror=_raise_walk_error):
        directories[:] = sorted(name for name in directories if name not in excluded)
        for name in sorted(name for name in (*directories, *files) if name not in excluded):
            yield Path(root) / name


def _product_files(workspace: Path) -> dict[str, str]:
    excluded = frozenset({".git", ".benchmark", ".codex-model-router"})
    paths = sorted((path for path in _walk_paths(workspace, excluded)
                    if path.is_file()),
                   key=lambda path: path.relative_to(workspace).as_posix())
    return {path.relative_to(workspace).as_posix(): file_sha(path) for path in paths}


def verify_prepared(prepared: Path, arm: str, spec: dict) -> dict:
    """Reject reused or contaminated arms before any live model call."""
    if arm not in ("baseline", "treatment"):
        raise ValueError("unknown benchmark arm")
    receipt_path = prepared / "preparation.json"
    if not receipt_path.is_file():
        raise ValueError("fresh preparation receipt missing")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if (receipt.get("schema_version") != 2 or
            receipt.get("task_id") != TASK_ID or
            receipt.get("fixture_manifest_sha256") != file_sha(HERE / "manifest.json") or
            receipt.get("source_sha256") != file_sha(SOURCE) or
            receipt.get("seed_patch_sha256") != file_sha(SEED_PATCH) or
            receipt.get("start_tree") != START_TREE or
            not isinstance(receipt.get("preparation_id"), str) or
            len(receipt["preparation_id"]) != 36):
        raise ValueError("fresh preparation receipt drift")
    seed = prepared / "seed"
    workspace = prepared / arm
    if any(path.is_symlink() for root in (seed, workspace)
           for path in _walk_paths(root, frozenset({".git"}))):
        raise ValueError("prepared arm contains a symlink")
    if (not seed.is_dir() or not workspace.is_dir() or
            receipt.get("seed_head") != _head(seed) or
            git_tree(seed) != START_TREE or git_tree(workspace) != START_TREE or
            _head(workspace) != receipt["seed_head"] or
            checked_run(["git", "status", "--porcelain", "--untracked-files=all"],
                        cwd=workspace).strip()):
        raise ValueError("arm is not at the clean frozen start tree")
    entry = receipt.get("arms", {}).get(arm, {})
    policy = arm_execution(spec, arm)
    if (entry.get("workspace") != str(workspace.resolve()) or
            entry.get("start_tree") != START_TREE or
            entry.get("policy_sha256") != policy["policy_sha256"] or
            entry.get("prompt_suffix_sha256") != policy["prompt_suffix_sha256"] or
            entry.get("model") != policy["model"] or entry.get("effort") != policy["effort"] or
            entry.get("cli_options") != policy["cli_options"]):
        raise ValueError("prepared arm execution binding drift")
    expected = _expected_reveal(spec)
    expected_paths = set(expected)
    config = workspace / ".codex-model-router" / "routing.json"
    if arm == "treatment":
        routing = verify_arm_config(workspace, spec)
        if entry.get("routing_config") != routing:
            raise ValueError("prepared router binding drift")
        expected_paths.add(".codex-model-router/routing.json")
    elif config.exists() or entry.get("routing_config") is not None:
        raise ValueError("baseline contains a router configuration")
    seed_files = _product_files(seed)
    if receipt.get("seed_files") != seed_files or _product_files(workspace) != seed_files:
        raise ValueError("seed or arm product file bytes drift")
    observed = {path.relative_to(workspace).as_posix()
                for path in _walk_paths(workspace, frozenset({".git"}))
                if path.is_file()}
    if observed != set(seed_files) | expected_paths:
        raise ValueError("arm contains missing or unexpected files, later reveals, or checkpoints")
    if any(file_sha(workspace / name) != digest for name, digest in expected.items()):
        raise ValueError("R0 overlay hash mismatch")
    if entry.get("revealed") != [{"path": name, "sha256": digest}
                                  for name, digest in sorted(expected.items())]:
        raise ValueError("R0 reveal receipt mismatch")
    return {"preparation_id": receipt["preparation_id"],
            "preparation_sha256": file_sha(receipt_path),
            "manifest_sha256": receipt["fixture_manifest_sha256"],
            "seed_head": receipt["seed_head"], "start_tree": START_TREE,
            "task_and_r0_sha256": expected,
            "policy_sha256": policy["policy_sha256"],
            "prompt_suffix_sha256": policy["prompt_suffix_sha256"]}


def _arms_for_plan(spec: dict) -> tuple[str, ...]:
    """Resolve treatment-only preparation from a hash-bound plan mode."""
    descriptor = spec.get("pilot")
    if descriptor is None:
        return ("baseline", "treatment")
    if not isinstance(descriptor, dict):
        raise ValueError("pilot descriptor invalid for preparation")
    name = descriptor.get("path")
    if name in ("pilot-plan-v10.json", "pilot-plan-v11.json"):
        return ("treatment",)
    if name == "pilot-plan-v9.json":
        if descriptor.get("schema_version") != 1:
            raise ValueError("paired preparation descriptor drift")
        return ("baseline", "treatment")
    version = re.fullmatch(r"pilot-plan-v(1[2-9]|[2-9][0-9]+)\.json", name or "") if isinstance(name, str) else None
    if version is None or descriptor.get("schema_version") != 4:
        raise ValueError("unrecognized preparation plan")
    path = HERE / name
    if descriptor.get("sha256") != file_sha(path):
        raise ValueError("standalone preparation plan hash drift")
    plan = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(plan, dict) or plan.get("schema_version") != 4 or
            plan.get("mode") != "standalone-feasibility" or
            plan.get("pilot_id") !=
                f"flipt-oci-long-horizon-pilot-{int(version.group(1)):02d}" or
            plan.get("arm_order") != ["treatment"] or
            plan.get("matched_pairs") != 0 or
            any(any(word in key.lower() for word in
                    ("baseline", "comparator", "reference", "admission"))
                for key in plan)):
        raise ValueError("standalone preparation mode or arm scope drift")
    return ("treatment",)


def prepare(destination: Path) -> dict:
    specification = manifest()
    arms_to_prepare = _arms_for_plan(specification)
    config = fixture_config(specification)
    fresh_directory(destination)
    seed = destination / "seed"
    extract_app_layer(SOURCE, seed)
    if git_tree(seed) != SOURCE_TREE:
        raise ValueError("extracted source tree mismatch")
    checked_run(["git", "apply", "--binary", "-"], cwd=seed, input=SEED_PATCH.read_bytes())
    checked_run(["git", "add", "--all"], cwd=seed)
    checked_run(["git", "commit", "-q", "-m", "Pinned reference benchmark seed"], cwd=seed)
    if git_tree(seed) != START_TREE or file_sha(seed / "go.sum") != specification["go_sum_sha256"]:
        raise ValueError("seed tree or dependency lock mismatch")
    seed_head = _head(seed)
    arms = {}
    for arm in arms_to_prepare:
        workspace = destination / arm
        shutil.copytree(seed, workspace)
        if checked_run(["git", "status", "--porcelain"], cwd=workspace).strip():
            raise ValueError("dirty arm start")
        config_binding = bind_arm_config(workspace, specification) if arm == "treatment" else None
        revealed = copy_assets(0, workspace)
        policy = arm_execution(specification, arm)
        arms[arm] = {"workspace": str(workspace.resolve()), "start_tree": START_TREE,
                     "routing_config": config_binding, "revealed": revealed,
                     "policy_sha256": policy["policy_sha256"],
                     "prompt_suffix_sha256": policy["prompt_suffix_sha256"],
                     "model": policy["model"], "effort": policy["effort"],
                     "cli_options": policy["cli_options"]}
    result = {"schema_version": 2, "task_id": TASK_ID,
              "preparation_id": str(uuid.uuid4()), "seed_head": seed_head,
              "seed_files": _product_files(seed),
              "fixture_manifest_sha256": file_sha(HERE / "manifest.json"),
              "source_sha256": file_sha(SOURCE), "seed_patch_sha256": file_sha(SEED_PATCH),
              "start_tree": START_TREE, "arms": arms,
              "routing_config_canonical_sha256": config["canonical_sha256"],
              "note": "Path placement is not technical isolation; audit broader file reads."}
    write_json_new(destination / "preparation.json", result)
    for arm in arms:
        verify_prepared(destination, arm, specification)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.destination.resolve()), indent=2))
