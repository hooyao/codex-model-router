"""Frozen inputs, patch identity, and receipts for the OCI long-horizon fixture."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from .assets.checkpoint_hash import capture_patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TASK_ID = "flipt-oci-reference-rollout-v1"
START_TREE = "3354dd97a389bac41536cc1cfa4ffe4861912bc8"
SOURCE_TREE = "20121a7049c58571664b87989d1fc7bab8562884"
SOURCE_SHA = "d936e33490bfbfc686871c236abfde86b629c4aa153cc6220a108d05c5827dfe"
SEED_SHA = "3d824219a77b11d8b6f6ec506403daad092594d07c9a697a3c2280a2c935c74b"
ASSET_ROOT = HERE / "assets"
SOURCE = ROOT / "evals" / "paired_selective" / "assets" / "source-layer.tar.gz"
SEED_PATCH = ROOT / "evals" / "paired_selective_lifecycle_v1" / "assets" / "seed.patch"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha(path: Path) -> str:
    return sha(path.read_bytes())


def checked_run(args: list[str], *, cwd: Path, env: dict | None = None,
                input: bytes | None = None, timeout: int = 120) -> bytes:
    result = subprocess.run(args, cwd=cwd, env=env, input=input, capture_output=True,
                            timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f"command {args[0]} failed ({result.returncode}): " +
                           result.stderr.decode(errors="replace")[-1000:])
    return result.stdout


def manifest() -> dict:
    data = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or data.get("task_id") != TASK_ID:
        raise ValueError("invalid fixture manifest")
    if data.get("start_tree") != START_TREE or data.get("source_tree") != SOURCE_TREE:
        raise ValueError("fixture tree drift")
    if file_sha(SOURCE) != SOURCE_SHA or file_sha(SEED_PATCH) != SEED_SHA:
        raise ValueError("source or seed patch drift")
    for name, digest in data["assets"].items():
        path = ASSET_ROOT / name
        if not path.is_file() or file_sha(path) != digest:
            raise ValueError(f"fixture asset drift: {name}")
    return data


def fresh_directory(destination: Path) -> None:
    if destination.exists():
        raise ValueError(f"output exists: {destination}")
    destination.mkdir(parents=True)


def write_json_new(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
        stream.write("\n")


def git_tree(workspace: Path) -> str:
    return checked_run(["git", "rev-parse", "HEAD^{tree}"], cwd=workspace).decode().strip()


def copy_assets(round_id: int, workspace: Path) -> list[dict]:
    """Reveal only this round's public files; caller validates round order."""
    spec = manifest()
    copied = []
    paths = (["round0.md", "environment.md", "checkpoint_hash.py",
              "submit_checkpoint.py"] if round_id == 0
             else [f"round{round_id}.md"])
    paths += [path for path in spec["assets"] if path.startswith(f"public/g{round_id}/")]
    for relative in sorted(paths):
        source = ASSET_ROOT / relative
        target_name = (Path(".benchmark") / "public" / relative.removeprefix(f"public/g{round_id}/")
                       if relative.startswith("public/") else Path(".benchmark") / relative)
        target = workspace / target_name
        if target.exists():
            raise ValueError(f"duplicate reveal: {target_name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        copied.append({"path": str(target_name).replace("\\", "/"),
                       "sha256": spec["assets"][relative]})
    # A Git exclude prevents checkpoint patches from including evaluator assets.
    exclude = workspace / ".git" / "info" / "exclude"
    with exclude.open("a", encoding="utf-8") as stream:
        stream.write("\n.benchmark/\n")
    return copied


def asset_hashes() -> dict[str, str]:
    return {str(path.relative_to(ASSET_ROOT)).replace("\\", "/"): file_sha(path)
            for path in ASSET_ROOT.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts}
