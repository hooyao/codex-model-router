"""Create identical offline Linux root filesystems for the paired ML task."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def tree_id(root: Path) -> str:
    """Hash a full tree's paths, modes, file bytes, and symlink targets."""
    digest = hashlib.sha256()
    paths = [root, *root.rglob("*")]
    for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode()
        metadata = path.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            digest.update(b"D\0" + relative + b"\0")
            digest.update(oct(metadata.st_mode & 0o7777).encode() + b"\0")
        if stat.S_ISREG(metadata.st_mode):
            digest.update(b"F\0" + relative + b"\0")
            digest.update(oct(metadata.st_mode & 0o7777).encode() + b"\0")
            digest.update(str(metadata.st_size).encode() + b"\0")
            digest.update(bytes.fromhex(sha256_file(path)))
        elif stat.S_ISLNK(metadata.st_mode):
            digest.update(b"L\0" + relative + b"\0" + os.readlink(path).encode() + b"\0")
        elif not stat.S_ISDIR(metadata.st_mode):
            raise ValueError(f"unsupported agent-workspace entry: {path}")
    return digest.hexdigest()


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("schema_version") != 1:
        raise ValueError("unsupported training fixture schema")
    return config


def public_input_path(rootfs: Path, configured: str) -> Path:
    if not configured.startswith("/app/data/") or ".." in Path(configured).parts:
        raise ValueError(f"unsafe public input path: {configured}")
    return rootfs / configured.lstrip("/")


def contamination_paths(rootfs: Path) -> list[str]:
    forbidden_names = {
        "private_test.tar.gz", "private_test.txt", "test_outputs.py", "solve.sh",
        "solution", "reference.bin", "reference.ftz", "grader.py", "oracle",
        "build_training_reference.py",
    }
    leaked = []
    for path in rootfs.rglob("*"):
        relative = path.relative_to(rootfs)
        name = path.name.lower()
        if name in forbidden_names or name.startswith("private_test") or ".git" in relative.parts or (
                relative.parts and relative.parts[0].lower() in {"tests", "oracle"}):
            leaked.append(relative.as_posix())
    model = rootfs / "app" / "model.bin"
    if model.exists() or model.is_symlink():
        leaked.append("app/model.bin")
    return sorted(set(leaked))


def verify_source(rootfs: Path, config: dict, *, require_agent_tools: bool = False) -> dict:
    if not (rootfs / "app").is_dir():
        raise ValueError("root filesystem has no /app")
    observed = {}
    for name in ("train", "public_test"):
        path = public_input_path(rootfs, config["public_inputs"][f"{name}_path"])
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"missing regular {name} input")
        actual_hash = sha256_file(path)
        actual_size = path.stat().st_size
        if actual_hash != config["public_inputs"][f"{name}_sha256"]:
            raise ValueError(f"{name} input hash mismatch: {actual_hash}")
        if actual_size != config["public_inputs"][f"{name}_bytes"]:
            raise ValueError(f"{name} input size mismatch: {actual_size}")
        observed[name] = {"sha256": actual_hash, "bytes": actual_size}
    leaked = contamination_paths(rootfs)
    if leaked:
        raise ValueError(f"forbidden material in agent workspace: {leaked}")
    result = {"public_inputs": observed, "app_tree_id": tree_id(rootfs / "app"),
              "rootfs_tree_id": tree_id(rootfs)}
    if not require_agent_tools:
        expected_base = config.get("agent_environment", {}).get("base_rootfs_tree_id")
        if expected_base and result["rootfs_tree_id"] != expected_base:
            raise ValueError(f"base rootfs tree mismatch: {result['rootfs_tree_id']}")
    if require_agent_tools:
        tool = rootfs / config["agent_environment"]["fasttext_path"].lstrip("/")
        if not tool.is_file() or tool.is_symlink() or not os.access(tool, os.X_OK):
            raise ValueError("derived agent rootfs is missing the executable fastText CLI")
        actual = sha256_file(tool)
        expected = config["agent_environment"]["fasttext_binary_sha256"]
        if actual != expected:
            raise ValueError(f"derived fastText CLI hash mismatch: {actual}")
        expected_tree = config["agent_environment"].get("rootfs_tree_id")
        if expected_tree and result["rootfs_tree_id"] != expected_tree:
            raise ValueError(f"derived rootfs tree mismatch: {result['rootfs_tree_id']}")
        result["fasttext"] = {"path": config["agent_environment"]["fasttext_path"],
                              "sha256": actual}
    return result


def copy_rootfs(source: Path, destination: Path) -> None:
    if destination.exists():
        raise ValueError(f"destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        destination.mkdir()
        result = subprocess.run(
            ["cp", "-a", "--reflink=auto", f"{source}/.", str(destination)],
            capture_output=True, text=True,
        )
        if result.returncode:
            raise RuntimeError(f"rootfs copy failed: {result.stderr.strip()}")
    else:
        shutil.copytree(source, destination, symlinks=True)


def derive_agent_rootfs(source: Path, fasttext: Path, destination: Path,
                        config: dict) -> dict:
    verify_source(source, config)
    expected = config["agent_environment"]["fasttext_binary_sha256"]
    if not fasttext.is_file() or fasttext.is_symlink() or sha256_file(fasttext) != expected:
        raise ValueError("pinned fastText build is missing or has the wrong hash")
    copy_rootfs(source, destination)
    target = destination / config["agent_environment"]["fasttext_path"].lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    with fasttext.open("rb") as source_stream, target.open("xb") as output:
        shutil.copyfileobj(source_stream, output, 1024 * 1024)
    target.chmod(0o755)
    return verify_source(destination, config, require_agent_tools=True)


def resource_preflight(rootfs: Path, config: dict) -> dict:
    limits = config["limits"]
    if not hasattr(os, "statvfs"):
        raise ValueError("capped filesystem capacity enforcement is unavailable")
    capacity = os.statvfs(rootfs).f_blocks * os.statvfs(rootfs).f_frsize
    maximum = limits["storage_mb"] * 1024 * 1024
    if capacity > maximum:
        raise ValueError(
            f"rootfs filesystem capacity {capacity} exceeds enforced storage limit {maximum}; "
            "place each arm on a capped filesystem")
    affinity = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else []
    if not affinity:
        raise ValueError("CPU affinity enforcement is unavailable")
    if shutil.which("prlimit") is None or shutil.which("taskset") is None:
        raise ValueError("taskset/prlimit resource enforcement is unavailable")
    return {"cpu": affinity[0], "memory_bytes": limits["memory_mb"] * 1024 * 1024,
            "filesystem_capacity_bytes": capacity}


def namespace_command(rootfs: Path, command: list[str], resources: dict | None = None) -> list[str]:
    if not command:
        raise ValueError("namespace command is empty")
    isolated = [
        "unshare", "--user", "--map-root-user", "--mount", "--net", "--pid",
        "--fork", "--kill-child", f"--root={rootfs}", "--wd=/app", "--mount-proc",
        "/usr/bin/env", "-i", "HOME=/root", "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        *command,
    ]
    if resources:
        return ["taskset", "--cpu-list", str(resources["cpu"]), "prlimit",
                f"--as={resources['memory_bytes']}", "--", *isolated]
    return isolated


def run_namespace_probe(rootfs: Path) -> dict:
    script = (
        "set -eu; test ! -e /app/model.bin; "
        "test \"$(grep -c : /proc/net/dev)\" -eq 1; "
        "test ! -e /host; test -r /app/data/train-00000-of-00001.parquet; "
        "/usr/local/bin/fasttext 2>&1 | grep -q 'usage: fasttext'"
    )
    result = subprocess.run(namespace_command(rootfs, ["/bin/sh", "-c", script]),
                            capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"namespace probe failed: {result.stderr.strip()}")
    return {"user_mount_pid_network_namespaces": True, "network_interfaces": ["lo"],
            "host_path_visible": False}


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="action", required=True)
    for action in ("verify-source", "verify-agent-rootfs", "namespace-probe", "resource-probe"):
        command = subparsers.add_parser(action)
        command.add_argument("--config", type=Path, required=True)
        command.add_argument("--rootfs", type=Path, required=True)
    materialize = subparsers.add_parser("materialize-pair")
    materialize.add_argument("--config", type=Path, required=True)
    materialize.add_argument("--source-rootfs", type=Path, required=True)
    materialize.add_argument("--baseline-rootfs", type=Path, required=True)
    materialize.add_argument("--treatment-rootfs", type=Path, required=True)
    materialize.add_argument("--receipt", type=Path, required=True)
    derive = subparsers.add_parser("derive-agent-rootfs")
    derive.add_argument("--config", type=Path, required=True)
    derive.add_argument("--source-rootfs", type=Path, required=True)
    derive.add_argument("--fasttext-binary", type=Path, required=True)
    derive.add_argument("--destination", type=Path, required=True)
    derive.add_argument("--receipt", type=Path, required=True)
    launch = subparsers.add_parser("launch")
    launch.add_argument("--config", type=Path, required=True)
    launch.add_argument("--rootfs", type=Path, required=True)
    launch.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    if args.action == "launch":
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        config = load_config(args.config)
        verify_source(args.rootfs, config, require_agent_tools=True)
        resources = resource_preflight(args.rootfs, config)
        raise SystemExit(subprocess.run(namespace_command(args.rootfs, command, resources)).returncode)
    config = load_config(args.config)
    if args.action == "verify-source":
        print(json.dumps(verify_source(args.rootfs, config), indent=2, sort_keys=True))
    elif args.action == "verify-agent-rootfs":
        print(json.dumps(verify_source(args.rootfs, config, require_agent_tools=True),
                         indent=2, sort_keys=True))
    elif args.action == "namespace-probe":
        verify_source(args.rootfs, config, require_agent_tools=True)
        print(json.dumps(run_namespace_probe(args.rootfs), indent=2, sort_keys=True))
    elif args.action == "resource-probe":
        verify_source(args.rootfs, config, require_agent_tools=True)
        print(json.dumps(resource_preflight(args.rootfs, config), indent=2, sort_keys=True))
    elif args.action == "derive-agent-rootfs":
        if args.receipt.exists():
            parser.error(f"receipt already exists: {args.receipt}")
        receipt = derive_agent_rootfs(args.source_rootfs, args.fasttext_binary,
                                      args.destination, config)
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                                encoding="utf-8")
        print(json.dumps(receipt, indent=2, sort_keys=True))
    else:
        source_receipt = verify_source(args.source_rootfs, config, require_agent_tools=True)
        if args.receipt.exists():
            parser.error(f"receipt already exists: {args.receipt}")
        copy_rootfs(args.source_rootfs, args.baseline_rootfs)
        copy_rootfs(args.source_rootfs, args.treatment_rootfs)
        baseline = verify_source(args.baseline_rootfs, config, require_agent_tools=True)
        treatment = verify_source(args.treatment_rootfs, config, require_agent_tools=True)
        ids = {source_receipt["rootfs_tree_id"], baseline["rootfs_tree_id"],
               treatment["rootfs_tree_id"]}
        if len(ids) != 1:
            raise RuntimeError("arm start trees differ")
        receipt = {"schema_version": 1, "image_digest": config["benchmark"]["image_digest"],
                   "source_commit": config["benchmark"]["source_commit"],
                   "rootfs_tree_id": ids.pop(), "app_tree_id": source_receipt["app_tree_id"],
                   "fasttext_binary_sha256": config["agent_environment"]["fasttext_binary_sha256"],
                   "baseline": str(args.baseline_rootfs.resolve()),
                   "treatment": str(args.treatment_rootfs.resolve()),
                   "private_material_in_agent_workspaces": False}
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
