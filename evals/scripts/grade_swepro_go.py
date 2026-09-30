"""Replay one SWE-bench Pro Go patch against a fresh base and hidden test patch.

This local fallback is not the official Harbor verifier. It uses the benchmark's
published test patch and selected Go packages when Docker is unavailable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import time
from pathlib import Path


def run(argv: list[str], cwd: Path, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=cwd, capture_output=True, **kwargs)


DATA_DIRS = {"testdata", "test_data", "fixtures", "fixture", "mocks", "mock", "__snapshots__"}
CODE_DIRS = {"tests", "test", "__tests__", "spec", "specs"}


def is_test_code(relative: Path) -> bool:
    """Match V2's test-code reset without deleting fixture data."""
    parents = set(relative.parts[:-1])
    if parents & DATA_DIRS:
        return False
    name = relative.name
    return (bool(parents & CODE_DIRS) or name == "conftest.py" or
            bool(re.search(r"_test\.(go|py|js|jsx|ts|tsx)$", name)) or
            bool(re.search(r"^test_.*\.py$", name)) or
            bool(re.search(r"\.(test|spec)\.(js|jsx|ts|tsx)$", name)))


def hidden_patch_paths(test_patch: bytes) -> tuple[set[Path], set[Path]]:
    paths: set[Path] = set()
    created: set[Path] = set()
    for old, new in re.findall(rb"^diff --git a/(.+?) b/(.+?)$", test_patch, re.MULTILINE):
        if old != new:
            raise RuntimeError("renamed hidden-test paths require the official verifier")
        relative = Path(new.decode("utf-8"))
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise RuntimeError(f"unsafe hidden-test path: {relative}")
        paths.add(relative)
    for name in re.findall(rb"^--- /dev/null\n\+\+\+ b/(.+)$", test_patch, re.MULTILINE):
        relative = Path(name.decode("utf-8"))
        if relative not in paths:
            raise RuntimeError(f"unlisted hidden-test creation: {relative}")
        created.add(relative)
    return paths, created


def restore_verifier_surface(grade: Path, test_patch: bytes) -> list[str]:
    """Replay the pinned V2 verifier's reset rules on the disposable grader."""
    changed = run(["git", "status", "--porcelain", "--untracked-files=all"],
                  grade, text=True).stdout.splitlines()
    paths, created = hidden_patch_paths(test_patch)
    for entry in changed:
        relative = Path(entry[3:])
        if not is_test_code(relative):
            continue
        tracked = run(["git", "ls-files", "--error-unmatch", "--", str(relative)], grade).returncode == 0
        if tracked:
            restored = run(["git", "restore", "--source", "HEAD", "--", str(relative)], grade)
            if restored.returncode:
                raise RuntimeError(restored.stderr.decode(errors="replace"))
        elif (grade / relative).is_file():
            (grade / relative).unlink()
    for relative in sorted(paths):
        target = grade / relative
        tracked = run(["git", "ls-files", "--error-unmatch", "--", str(relative)], grade).returncode == 0
        if tracked:
            result = run(["git", "restore", "--source", "HEAD", "--", str(relative)], grade)
            if result.returncode:
                raise RuntimeError(result.stderr.decode(errors="replace"))
        elif relative in created and target.is_file():
            target.unlink()
    # V2 restores modified tracked data paths, but preserves new fixture files
    # supplied by the candidate unless the hidden patch itself creates them.
    for entry in changed:
        relative = Path(entry[3:])
        if not set(relative.parts[:-1]) & DATA_DIRS:
            continue
        tracked = run(["git", "ls-files", "--error-unmatch", "--", str(relative)], grade).returncode == 0
        if tracked:
            result = run(["git", "restore", "--source", "HEAD", "--", str(relative)], grade)
            if result.returncode:
                raise RuntimeError(result.stderr.decode(errors="replace"))
    result = run(["git", "apply", "--binary", "-"], grade, input=test_patch)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace"))
    return [str(path) for path in sorted(paths)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--test-patch", type=Path, required=True)
    parser.add_argument("--grade-workspace", type=Path, required=True)
    parser.add_argument("--go-executable", required=True)
    parser.add_argument("--candidate-patch", type=Path)
    parser.add_argument("--oracle-config", type=Path)
    parser.add_argument("--packages", nargs="+", default=["./internal/config", "./internal/metrics",
                                                       "./internal/tracing"])
    parser.add_argument("--wsl-distro", default="Ubuntu")
    args = parser.parse_args()
    if args.grade_workspace.exists():
        parser.error(f"grade workspace already exists: {args.grade_workspace}")
    shutil.copytree(args.seed, args.grade_workspace)
    grade = args.grade_workspace
    if (grade / ".git").exists():
        if run(["git", "status", "--porcelain", "--untracked-files=all"], grade).stdout:
            raise RuntimeError("seed repository is not clean")
    else:
        for command in (["git", "init"], ["git", "config", "user.name", "Benchmark grader"],
                        ["git", "config", "user.email", "grader@example.invalid"],
                        ["git", "config", "core.autocrlf", "false"], ["git", "add", "--all"],
                        ["git", "commit", "-m", "Pinned benchmark base"]):
            result = run(command, grade)
            if result.returncode:
                raise RuntimeError(result.stderr.decode(errors="replace"))
    expected_tree = run(["git", "rev-parse", "HEAD^{tree}"], grade, text=True).stdout.strip()
    actual_tree = run(["git", "rev-parse", "HEAD^{tree}"], args.arm, text=True).stdout.strip()
    if expected_tree != actual_tree:
        raise RuntimeError(f"base tree mismatch: {expected_tree} != {actual_tree}")
    # Include new files in the diff without staging their content as a commit.
    result = run(["git", "add", "-N", "--", ".", ":(exclude).codex-model-router"], args.arm)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace"))
    patch = run(["git", "diff", "--binary", "HEAD", "--", ".",
                 ":(exclude).codex-model-router"], args.arm).stdout
    if args.candidate_patch:
        captured_patch = args.candidate_patch.read_bytes()
        if patch != captured_patch:
            raise RuntimeError("captured candidate patch differs from arm workspace")
        patch = captured_patch
    if patch:
        result = run(["git", "apply", "--binary", "-"], grade, input=patch)
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors="replace"))
    test_patch = args.test_patch.read_bytes()
    test_paths = restore_verifier_surface(grade, test_patch)
    started = time.monotonic()
    command = ["wsl", "-d", args.wsl_distro, "--", args.go_executable,
               "test", "-count=1", *args.packages]
    tests = run(command, grade)
    (grade / "grader-go.stdout").write_bytes(tests.stdout)
    (grade / "grader-go.stderr").write_bytes(tests.stderr)
    result = {
        "grader": "local-hidden-test-replay-not-official-harbor",
        "base_tree": expected_tree,
        "candidate_patch_sha256": hashlib.sha256(patch).hexdigest(),
        "candidate_patch_bytes": len(patch),
        "test_patch_sha256": hashlib.sha256(test_patch).hexdigest(),
        "oracle_config_sha256": (hashlib.sha256(args.oracle_config.read_bytes()).hexdigest()
                                  if args.oracle_config else None),
        "test_paths_restored": test_paths,
        "go_test_exit_code": tests.returncode,
        "packages": args.packages,
        "test_command": command,
        "stdout_sha256": hashlib.sha256(tests.stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(tests.stderr).hexdigest(),
        "go_test_wall_seconds": round(time.monotonic() - started, 3),
        "quality_pass": tests.returncode == 0,
    }
    (grade / "grade.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
