"""Run one training arm under a verified aggregate systemd/cgroup-v2 scope."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve()
REPO_ROOT = SCRIPT.parents[2]
CONFIG = REPO_ROOT / "evals" / "paired_training" / "config.json"
FIXTURE = REPO_ROOT / "evals" / "scripts" / "prepare_training_fixture.py"
RESOURCE_ROOT = Path("/var/lib/cmr-train-fasttext/resource-sandbox-v1")
ROOTFS = {"baseline": RESOURCE_ROOT / "mnt-baseline" / "rootfs",
          "treatment": RESOURCE_ROOT / "mnt-treatment" / "rootfs"}
EXPECTED = {"cpu.max": "100000 100000", "memory.max": "4294967296",
            "memory.swap.max": "0", "pids.max": "512"}
WALL_SECONDS = 3600


def cgroup_path() -> Path:
    lines = Path("/proc/self/cgroup").read_text(encoding="ascii").splitlines()
    matches = [line.split("::", 1)[1] for line in lines if line.startswith("0::")]
    if len(matches) != 1 or not matches[0].startswith("/"):
        raise RuntimeError("process has no unambiguous cgroup-v2 path")
    path = Path("/sys/fs/cgroup") / matches[0].lstrip("/")
    if not path.is_dir():
        raise RuntimeError(f"cgroup path is unavailable: {path}")
    return path


def verify_limits() -> dict:
    path = cgroup_path()
    observed = {}
    for name, expected in EXPECTED.items():
        value = (path / name).read_text(encoding="ascii").strip()
        if value != expected:
            raise RuntimeError(f"effective {name}={value!r}, expected {expected!r}")
        observed[name] = value
    child = subprocess.run(["/bin/sh", "-c", "cat /proc/self/cgroup"],
                           capture_output=True, text=True, check=True).stdout.strip()
    if f"0::{str(path).removeprefix('/sys/fs/cgroup')}" not in child:
        raise RuntimeError("child process did not inherit the arm cgroup")
    observed["path"] = str(path)
    observed["pids.current"] = (path / "pids.current").read_text().strip()
    observed["memory.current"] = (path / "memory.current").read_text().strip()
    observed["cpu.stat"] = (path / "cpu.stat").read_text().splitlines()
    return observed


def systemd_command(arm: str, inner: list[str]) -> list[str]:
    unit = f"cmr-train-fasttext-{arm}-{os.getpid()}"
    return ["systemd-run", "--scope", "--collect", "--quiet",
            f"--unit={unit}", "--property=CPUAccounting=yes",
            "--property=CPUQuota=100%", "--property=CPUQuotaPeriodSec=100ms",
            "--property=MemoryAccounting=yes", "--property=MemoryMax=4294967296",
            "--property=MemorySwapMax=0",
            "--property=TasksAccounting=yes", "--property=TasksMax=512",
            f"--property=RuntimeMaxSec={WALL_SECONDS}s",
            "--property=KillMode=control-group", "--", *inner]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("probe", "run", "_inside-probe", "_inside-run"))
    parser.add_argument("--arm", choices=tuple(ROOTFS), required=True)
    args, command = parser.parse_known_args()
    command = command[1:] if command[:1] == ["--"] else command
    if args.mode == "probe":
        inner = [sys.executable, str(SCRIPT), "_inside-probe", "--arm", args.arm]
        raise SystemExit(subprocess.run(systemd_command(args.arm, inner)).returncode)
    if args.mode == "run":
        if not command:
            parser.error("run requires a command after --")
        inner = [sys.executable, str(SCRIPT), "_inside-run", "--arm", args.arm,
                 "--", *command]
        raise SystemExit(subprocess.run(systemd_command(args.arm, inner)).returncode)
    limits = verify_limits()
    if args.mode == "_inside-probe":
        print(json.dumps({"status": "verified", "arm": args.arm, "limits": limits},
                         indent=2, sort_keys=True))
        return
    if not command:
        parser.error("internal run requires a command")
    rootfs = ROOTFS[args.arm]
    if not rootfs.is_dir():
        raise RuntimeError(f"arm rootfs is unavailable: {rootfs}")
    launch = ["/usr/bin/timeout", "--foreground", "--signal=TERM", "--kill-after=30s",
              f"{WALL_SECONDS}s", sys.executable, str(FIXTURE), "launch",
              "--config", str(CONFIG), "--rootfs", str(rootfs), "--", *command]
    os.execv(launch[0], launch)


if __name__ == "__main__":
    main()
