"""Materialize the pinned SWE-bench Pro source layer as a clean Git fixture.

The image's `/app` layer is complete for this task. Image-internal Git history
and Docker whiteout markers are deliberately omitted from agent workspaces.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tarfile
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(*args: str, cwd: Path) -> str:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"{args!r}: {result.stderr.strip()}")
    return result.stdout.strip()


def extract_app_layer(layer: Path, destination: Path) -> None:
    if destination.exists():
        raise ValueError(f"destination already exists: {destination}")
    destination.mkdir(parents=True)
    try:
        with tarfile.open(layer, "r:gz") as archive:
            for member in archive:
                parts = Path(member.name).parts
                if not parts or parts[0] != "app" or len(parts) == 1:
                    continue
                relative = Path(*parts[1:])
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError(f"unsafe archive path: {member.name}")
                if ".git" in relative.parts or any(part.startswith(".wh.") for part in relative.parts):
                    continue
                target = destination / relative
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source = archive.extractfile(member)
                    if source is None:
                        raise ValueError(f"unreadable archive file: {member.name}")
                    with source, target.open("wb") as output:
                        while chunk := source.read(1024 * 1024):
                            output.write(chunk)
                else:
                    raise ValueError(f"unsupported archive entry: {member.name}")
        run("git", "init", "-q", cwd=destination)
        run("git", "config", "user.name", "Benchmark fixture", cwd=destination)
        run("git", "config", "user.email", "fixture@example.invalid", cwd=destination)
        run("git", "config", "core.autocrlf", "false", cwd=destination)
        run("git", "config", "gc.auto", "0", cwd=destination)
        run("git", "config", "maintenance.auto", "false", cwd=destination)
        run("git", "add", "--all", cwd=destination)
        run("git", "commit", "-q", "-m", "Pinned SWE-bench Pro image source", cwd=destination)
    except BaseException:
        # Preserve the partial extraction for diagnosis; never overwrite it.
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--layer", type=Path, required=True)
    parser.add_argument("--expected-layer-sha256", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    actual = sha256(args.layer)
    if actual != args.expected_layer_sha256:
        parser.error(f"source layer mismatch: {actual}")
    extract_app_layer(args.layer, args.destination)
    result = {
        "source_layer_sha256": actual,
        "initial_tree": run("git", "rev-parse", "HEAD^{tree}", cwd=args.destination),
        "initial_diff": run("git", "status", "--porcelain", "--untracked-files=all", cwd=args.destination),
        "workspace": str(args.destination.resolve()),
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
