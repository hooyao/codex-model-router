"""Run visible checks and atomically submit a seed-bound benchmark checkpoint.

Run from the arm checkout: python .benchmark/submit_checkpoint.py --round 0
--summary "Initial reference contract implemented". Repeat for rounds 1 and 2.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

try:  # Copied script in an arm; package import in evaluator dry runs and tests.
    from .checkpoint_hash import START_TREE, capture_patch
except ImportError:
    from checkpoint_hash import START_TREE, capture_patch

CHECKS = ("diff", "public", "packages")
PACKAGE_PATHS = ("./internal/oci/...", "./internal/storage/fs/oci",
                 "./internal/storage/fs/store")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".checkpoint-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def public_overlay(workspace: Path, round_id: int) -> Path:
    """Map only already visible public tests into the Go package tree."""
    root = workspace / ".benchmark" / "public"
    replacements: dict[str, str] = {}
    seen_rounds = set()
    for source in sorted(root.rglob("benchmark_g*_test.go")):
        if source.is_symlink() or not source.is_file():
            raise ValueError("public test is not a regular file")
        name = source.name
        if len(name) < 12 or name[11] not in "012":
            raise ValueError(f"unknown public test name: {name}")
        visible_round = int(name[11])
        if visible_round > round_id:
            raise ValueError("future public test appeared before reveal")
        seen_rounds.add(visible_round)
        relative = source.relative_to(root)
        target = workspace / relative
        if target.exists():
            raise ValueError(f"public test collision: {relative}")
        replacements[str(target.resolve())] = str(source.resolve())
    if seen_rounds != set(range(round_id + 1)):
        raise ValueError(f"public tests for round {round_id} are incomplete")
    overlay = workspace / ".benchmark" / "public-overlay.json"
    atomic_write(overlay, (json.dumps({"Replace": replacements}, sort_keys=True) + "\n").encode())
    return overlay


def command_for(check: str, workspace: Path, round_id: int) -> list[str]:
    if check == "diff":
        return ["git", "diff", "--check"]
    if check == "public":
        overlay = public_overlay(workspace, round_id)
        return ["go", "test", "-vet=off", "-overlay",
                overlay.relative_to(workspace).as_posix(),
                "-count=1", "-timeout=90s", "./internal/storage/fs/oci"]
    if check == "packages":
        return ["go", "test", "-count=1", "-timeout=90s", *PACKAGE_PATHS]
    raise ValueError(f"unsupported check: {check}")


def run_check(command: list[str], workspace: Path) -> tuple[int, bytes, bytes]:
    env = os.environ.copy()
    env["GOPROXY"] = "off"
    env["GOSUMDB"] = "off"
    try:
        result = subprocess.run(command, cwd=workspace, env=env,
                                capture_output=True, timeout=180, check=False)
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired as error:
        return 124, error.stdout or b"", (error.stderr or b"") + b"\ncheck timed out\n"
    except OSError as error:
        return 127, b"", str(error).encode(errors="replace")


def submit(workspace: Path, round_id: int, summary: str,
           selected: tuple[str, ...] = ("diff", "public"),
           base_tree: str = START_TREE) -> dict:
    workspace = workspace.resolve()
    if round_id not in (0, 1, 2) or not (workspace / ".git" / "index").is_file():
        raise ValueError("expected a benchmark Git checkout and round 0, 1, or 2")
    if not (workspace / ".benchmark" / f"round{round_id}.md").is_file():
        raise ValueError(f"round {round_id} has not been revealed")
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 4000:
        raise ValueError("summary must contain 1 to 4000 characters")
    if not selected or len(set(selected)) != len(selected) or any(x not in CHECKS for x in selected):
        raise ValueError("checks must be distinct entries from diff, public, packages")
    checks = []
    for check in selected:
        command = command_for(check, workspace, round_id)
        code, stdout, stderr = run_check(command, workspace)
        record_dir = workspace / ".benchmark" / "checks" / f"round{round_id}"
        stdout_path = record_dir / f"{check}.stdout"
        stderr_path = record_dir / f"{check}.stderr"
        atomic_write(stdout_path, stdout)
        atomic_write(stderr_path, stderr)
        command_text = subprocess.list2cmdline(command)
        status = {"command": command_text, "exit_code": code,
                  "stdout_sha256": digest(stdout), "stderr_sha256": digest(stderr)}
        status_bytes = (json.dumps(status, sort_keys=True) + "\n").encode()
        status_path = record_dir / f"{check}.status.json"
        atomic_write(status_path, status_bytes)
        checks.append({"command": command_text, "exit_code": code,
                       "stdout_path": stdout_path.relative_to(workspace).as_posix(),
                       "stdout_sha256": digest(stdout),
                       "stderr_path": stderr_path.relative_to(workspace).as_posix(),
                       "stderr_sha256": digest(stderr),
                       "status_path": status_path.relative_to(workspace).as_posix(),
                       "status_sha256": digest(status_bytes)})
    checkpoint = {"schema_version": 1, "round": round_id,
                  "requested_action": "submit",
                  "candidate_patch_sha256": digest(capture_patch(workspace, base_tree)),
                  "checks": checks, "summary": summary.strip()}
    path = workspace / ".benchmark" / "checkpoint.json"
    atomic_write(path, (json.dumps(checkpoint, indent=2, sort_keys=True) + "\n").encode())
    return checkpoint


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round", type=int, required=True, choices=(0, 1, 2))
    parser.add_argument("--summary", required=True)
    parser.add_argument("--check", action="append", choices=CHECKS,
                        help="Repeat to select checks; default is diff and public")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(),
                        help="Only for local tests; benchmark arms run from their checkout")
    parser.add_argument("--base-tree", default=START_TREE,
                        help="Only for local tests; arms use the frozen seed tree")
    args = parser.parse_args()
    result = submit(args.workspace, args.round, args.summary,
                    tuple(args.check) if args.check else ("diff", "public"), args.base_tree)
    print(json.dumps({"checkpoint": ".benchmark/checkpoint.json",
                      "candidate_patch_sha256": result["candidate_patch_sha256"],
                      "exit_codes": [check["exit_code"] for check in result["checks"]]},
                     sort_keys=True))
