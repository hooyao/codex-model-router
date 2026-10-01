"""Replay a captured candidate patch against cumulative public or hidden tests."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from evals.long_horizon_v1.common import (ASSET_ROOT, HERE, START_TREE, TASK_ID,
    checked_run, file_sha, fresh_directory, git_tree, manifest, sha, write_json_new)

PACKAGES = ["./internal/config", "./internal/oci", "./internal/storage/fs",
            "./internal/storage/fs/oci", "./internal/storage/fs/store"]
BACKEND_PACKAGES = ["./internal/config", "./internal/oci/...", "./internal/storage/fs",
                    "./internal/storage/fs/oci", "./internal/storage/fs/store",
                    "./internal/storage/fs/local", "./internal/storage/fs/git",
                    "./internal/storage/fs/object"]
REVIEW_REQUIREMENTS = ("reference-validation", "manifest-identity", "atomic-publication",
                       "alias-order-isolation", "last-good-and-explicit-error",
                       "retention-and-lifetime", "cancellation-and-close",
                       "resource-release", "non-oci-compatibility", "maintainer-docs")
WSL_STEPS = ("go-test", "race")
GO_FAILURE_LINE = re.compile(rb"(?m)^(?:--- FAIL:|FAIL(?:\t|\r?$))")


def record_grader_hazard(prepared: Path, output: Path, reason: str) -> None:
    """Persist an unresolved process-tree stop across the paired arms."""
    marker = prepared / "grader-hazard.json"
    if not marker.exists():
        write_json_new(marker, {"schema_version": 1, "status": "UNKNOWN",
            "grade_output": str(output.resolve()), "reason": reason})


def stop_wsl_grader(output: Path) -> bool:
    """Terminate each marked Linux process group and confirm no members remain."""
    markers = [output / step / "wsl-pgid.txt" for step in WSL_STEPS]
    found = False
    for marker in markers:
        try:
            if not marker.is_file():
                continue
            found = True
            raw = marker.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            return False
        if not raw.isdecimal() or int(raw) <= 1:
            return False
        pgid = int(raw)
        try:
            token = shlex.quote(wsl_path(marker))
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            return False
        command = (f"if kill -0 -- -{pgid} 2>/dev/null; then "
                   f"case \"$(ps -ww -o args= -p {pgid})\" in *{token}*) ;; *) exit 2;; esac; "
                   f"kill -TERM -- -{pgid} 2>/dev/null || true; "
                   "sleep 0.2; "
                   f"kill -KILL -- -{pgid} 2>/dev/null || true; fi; "
                   f"if kill -0 -- -{pgid} 2>/dev/null; then exit 1; fi")
        try:
            checked = subprocess.run(["wsl", "-d", "Ubuntu", "--", "bash", "-lc", command],
                                     capture_output=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return False
        if checked.returncode:
            return False
        try:
            marker.unlink()
        except OSError:
            return False
    return found


def overlay_tests(replay: Path, round_id: int, hidden: bool) -> list[str]:
    roots = [ASSET_ROOT / "public" / f"g{index}" for index in range(round_id + 1)]
    if hidden:
        roots.append(ASSET_ROOT / "oracle")
    result = []
    for root in roots:
        if not root.is_dir():
            raise ValueError(f"missing test corpus: {root}")
        for source in root.rglob("*"):
            if not source.is_file():
                continue
            rel = source.relative_to(root)
            target = replay / rel
            if target.exists():
                raise ValueError(f"test overlay collision: {rel}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            result.append(f"{rel.as_posix()}:{file_sha(source)}")
    return sorted(result)


def wsl_path(path: Path) -> str:
    result = subprocess.run(["wsl", "-d", "Ubuntu", "--", "wslpath", "-a", path.as_posix()],
                            capture_output=True, text=True, timeout=15)
    if result.returncode or not result.stdout.strip():
        raise RuntimeError("WSL path conversion failed")
    return result.stdout.strip()


def checked_go_status(status: Path, wsl_code: int, stdout: bytes) -> int:
    """Require the Go status written inside Linux to agree with WSL's exit."""
    try:
        raw = status.read_bytes()
    except OSError as error:
        raise RuntimeError("WSL Go status missing") from error
    if not re.fullmatch(rb"(?:0|[1-9][0-9]{0,2})\n", raw) or int(raw) > 255:
        raise RuntimeError("WSL Go status invalid")
    code = int(raw)
    if code != wsl_code:
        raise RuntimeError(f"WSL Go status conflict: Linux {code}, WSL {wsl_code}")
    if code == 0 and GO_FAILURE_LINE.search(stdout):
        raise RuntimeError("WSL Go status conflicts with failure output")
    return code


def run_wsl_go(script: Path, status: Path, output: Path, prefix: Path,
               prepared: Path | None) -> tuple[int, bytes, bytes]:
    """Run a file-backed Linux script and confirm its process group is gone."""
    try:
        result = subprocess.run(["wsl", "-d", "Ubuntu", "--", "setsid", "--wait",
                                 "bash", wsl_path(script),
                                 wsl_path(prefix / "wsl-pgid.txt")],
                                capture_output=True, timeout=870)
        wsl_code, stdout, stderr = result.returncode, result.stdout, result.stderr
        if not stop_wsl_grader(output):
            if prepared is not None:
                record_grader_hazard(prepared, output, "WSL grader exit not confirmed")
            raise RuntimeError("WSL grader exit not confirmed")
    except subprocess.TimeoutExpired as error:
        wsl_code, stdout, stderr = 124, error.stdout or b"", error.stderr or b""
        if not stop_wsl_grader(output):
            if prepared is not None:
                record_grader_hazard(prepared, output, "WSL grader cleanup unconfirmed after timeout")
            raise RuntimeError("WSL grader cleanup unconfirmed after timeout")
        (prefix / "stdout.txt").write_bytes(stdout)
        (prefix / "stderr.txt").write_bytes(stderr)
        return wsl_code, stdout, stderr
    (prefix / "stdout.txt").write_bytes(stdout)
    (prefix / "stderr.txt").write_bytes(stderr)
    return checked_go_status(status, wsl_code, stdout), stdout, stderr


def linux_go_path(specification: dict) -> str:
    """Resolve the Linux command used in a grade receipt."""
    return os.environ.get("LONG_HORIZON_GO_LINUX", specification["linux_path"])


def pinned_go_paths(specification: dict) -> tuple[Path, str]:
    """Allow local tool locations while retaining the frozen binary digests."""
    windows = Path(os.environ.get("LONG_HORIZON_GO_WINDOWS", specification["windows_path"]))
    linux = linux_go_path(specification)
    if not windows.is_absolute() or not PurePosixPath(linux).is_absolute():
        raise ValueError("pinned Go overrides must be absolute paths")
    return windows, linux


def go_test(replay: Path, output: Path, *, race: bool = False,
            prepared: Path | None = None, backend: bool = False) -> dict:
    specification = manifest()["toolchain"]
    binary, linux_binary = pinned_go_paths(specification)
    if not binary.is_file() or file_sha(binary) != specification["sha256"]:
        raise RuntimeError("pinned Go binary unavailable or changed")
    # Use WSL to run the Linux binary with a private build cache. GOPROXY=off
    # makes missing cached dependencies an explicit preparation failure.
    if subprocess.run(["wsl", "-d", "Ubuntu", "--", "test", "-x", linux_binary],
                      capture_output=True, timeout=15).returncode:
        raise RuntimeError("pinned Linux Go binary unavailable")
    linux_hash = subprocess.run(["wsl", "-d", "Ubuntu", "--", "sha256sum", linux_binary],
                                capture_output=True, text=True, timeout=30)
    words = linux_hash.stdout.split()
    if linux_hash.returncode or not words or words[0] != specification["linux_sha256"]:
        raise RuntimeError("pinned Linux Go binary hash changed")
    packages = (BACKEND_PACKAGES if backend else PACKAGES) if not race else [
        "./internal/oci", "./internal/storage/fs", "./internal/storage/fs/oci"]
    args = [linux_binary, "test", "-count=1", "-timeout=90s"]
    if race:
        args.append("-race")
    args += packages
    cache = output / "go-build-cache"
    cache.mkdir(exist_ok=True)
    prefix = output / ("race" if race else "go-test")
    prefix.mkdir(exist_ok=True)
    status = prefix / "go-exit-status.txt"
    script = prefix / "run-go.sh"
    status_path = shlex.quote(wsl_path(status))
    temporary_status = shlex.quote(wsl_path(prefix / "go-exit-status.tmp"))
    inner = ("#!/usr/bin/env bash\n"
             "printf '%s\\n' \"$$\" > \"$1\" || exit 125\n"
             "cd " + shlex.quote(wsl_path(replay)) + " || exit 125\n"
             "env GOPROXY=off GOSUMDB=off GOCACHE=" +
             shlex.quote(wsl_path(cache)) + " " +
             " ".join(shlex.quote(arg) for arg in args) + "\n"
             "status=$?\n"
             "printf '%s\\n' \"$status\" > " + temporary_status + " || exit 125\n"
             "mv -f -- " + temporary_status + " " + status_path + " || exit 125\n"
             "exit \"$status\"\n")
    # Write LF bytes: Windows text-mode CRLF breaks scripts executed by WSL.
    script.write_bytes(inner.encode("utf-8"))
    start = time.monotonic()
    code, stdout, stderr = run_wsl_go(script, status, output, prefix, prepared)
    return {"command": args, "exit_code": code, "seconds": round(time.monotonic() - start, 3),
            "stdout_sha256": sha(stdout), "stderr_sha256": sha(stderr),
            "stdout_tail": stdout.decode(errors="replace")[-3000:],
            "stderr_tail": stderr.decode(errors="replace")[-3000:]}


def grade(candidate: Path, prepared: Path, output: Path, round_id: int,
          hidden: bool = False, run_race: bool = False,
          semantic_review: Path | None = None, backend: bool = False) -> dict:
    specification = manifest()
    if round_id not in (0, 1, 2):
        raise ValueError("invalid round")
    if hidden and round_id != 2:
        raise ValueError("hidden oracle runs only after G2")
    if backend and (hidden or run_race or semantic_review is not None or round_id != 2):
        raise ValueError("backend regression is a separate G2 check")
    if not candidate.is_file():
        raise ValueError("candidate patch missing")
    preparation = json.loads((prepared / "preparation.json").read_text(encoding="utf-8"))
    if preparation.get("start_tree") != START_TREE or preparation.get("fixture_manifest_sha256") != file_sha(HERE / "manifest.json"):
        raise ValueError("preparation fixture drift")
    fresh_directory(output)
    replay = output / "replay"
    shutil.copytree(prepared / "seed", replay)
    if git_tree(replay) != START_TREE:
        raise ValueError("replay start tree drift")
    patch = candidate.read_bytes()
    if patch:
        checked_run(["git", "apply", "--binary", "-"], cwd=replay, input=patch)
    test_refs = overlay_tests(replay, round_id, hidden)
    checks = [go_test(replay, output, prepared=prepared, backend=backend)]
    if run_race and checks[0]["exit_code"] == 0:
        checks.append(go_test(replay, output, race=True, prepared=prepared))
    passed = all(check["exit_code"] == 0 for check in checks)
    reviewed = None
    quality = passed if not hidden else None
    if hidden and semantic_review is not None:
        reviewed = json.loads(semantic_review.read_text(encoding="utf-8"))
        requirements = reviewed.get("requirements") if isinstance(reviewed, dict) else None
        if (not isinstance(reviewed, dict) or
                reviewed.get("candidate_patch_sha256") != sha(patch) or
                reviewed.get("manifest_sha256") != file_sha(HERE / "manifest.json") or
                not isinstance(requirements, dict) or
                set(requirements) != set(REVIEW_REQUIREMENTS) or
                not all(type(value) is bool for value in requirements.values())):
            raise ValueError("semantic review is incomplete or unbound")
        quality = passed and all(reviewed["requirements"].values())
    result = {"schema_version": 1, "task_id": TASK_ID, "round": round_id,
              "kind": "backend-regression" if backend else
                      "hidden-final" if hidden else "public-gate",
              "start_tree": START_TREE, "manifest_sha256": file_sha(HERE / "manifest.json"),
              "candidate_patch_sha256": sha(patch), "tests": test_refs,
              "checks": checks, "behavior_pass": passed,
              "semantic_review": reviewed if hidden else "not-applicable",
              "quality_pass": quality}
    write_json_new(output / "grade.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--prepared", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--round", required=True, type=int)
    parser.add_argument("--hidden", action="store_true")
    parser.add_argument("--race", action="store_true")
    parser.add_argument("--semantic-review", type=Path)
    parser.add_argument("--backend", action="store_true")
    args = parser.parse_args()
    print(json.dumps(grade(args.candidate.resolve(), args.prepared.resolve(),
                           args.output.resolve(), args.round, args.hidden, args.race,
                           args.semantic_review.resolve() if args.semantic_review else None,
                           args.backend), indent=2))
