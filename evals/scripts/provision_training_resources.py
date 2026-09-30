"""Provision or verify the two pinned 10-GiB training arm filesystems.

The target paths are intentionally fixed. Provisioning is fail-closed and
never performs cleanup: on an error it reports the exact observed state so an
operator can make a separate recovery decision.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

try:
    from evals.scripts.prepare_training_fixture import load_config, verify_source
except ModuleNotFoundError:
    from prepare_training_fixture import load_config, verify_source


RESOURCE_ROOT = Path("/var/lib/cmr-train-fasttext/resource-sandbox-v1")
PARENT_ROOT = Path("/var/lib/cmr-train-fasttext")
SOURCE_ROOTFS = PARENT_ROOT / "agent-rootfs"
IMAGE_BYTES = 10 * 1024 * 1024 * 1024
ARMS = {
    "baseline": {"image": RESOURCE_ROOT / "baseline.ext4",
                 "mount": RESOURCE_ROOT / "mnt-baseline",
                 "label": "cmr-train-base"},
    "treatment": {"image": RESOURCE_ROOT / "treatment.ext4",
                  "mount": RESOURCE_ROOT / "mnt-treatment",
                  "label": "cmr-train-treat"},
}
RECEIPT = RESOURCE_ROOT / "resources.json"


def run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError(f"{args!r}: {result.stderr.strip() or result.stdout.strip()}")
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def require_exact(path: Path, expected: Path, *, strict: bool) -> None:
    actual = path.resolve(strict=strict)
    expected_text = str(expected)
    if str(actual) != expected_text:
        raise ValueError(f"path resolution mismatch: {path} -> {actual}, expected {expected_text}")


def validate_parent() -> None:
    require_exact(PARENT_ROOT, PARENT_ROOT, strict=True)
    metadata = PARENT_ROOT.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or PARENT_ROOT.is_symlink():
        raise ValueError(f"benchmark parent is not a real directory: {PARENT_ROOT}")
    if metadata.st_uid != 0 or metadata.st_gid != 0 or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise ValueError("benchmark parent must be root:root mode 0700")


def validate_resource_root() -> None:
    require_exact(RESOURCE_ROOT, RESOURCE_ROOT, strict=True)
    metadata = RESOURCE_ROOT.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or RESOURCE_ROOT.is_symlink():
        raise ValueError("resource root is not a real directory")
    if metadata.st_uid != 0 or metadata.st_gid != 0 or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise ValueError("resource root must be root:root mode 0700")


def image_identity(path: Path) -> dict:
    require_exact(path, path, strict=True)
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise ValueError(f"image is not a regular no-follow file: {path}")
    if metadata.st_uid != 0 or metadata.st_gid != 0 or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise ValueError(f"image must be root:root mode 0600: {path}")
    if metadata.st_nlink != 1 or metadata.st_size != IMAGE_BYTES:
        raise ValueError(f"image link count or size mismatch: {path}")
    return {"path": str(path), "device": metadata.st_dev, "inode": metadata.st_ino,
            "nlink": metadata.st_nlink, "bytes": metadata.st_size,
            "allocated_bytes": metadata.st_blocks * 512}


def require_no_mounts(image: Path, loop: str | None = None, mountpoint: Path | None = None) -> None:
    for source in (str(image), loop):
        if source and run(["findmnt", "-rn", "-S", source], check=False).stdout.strip():
            raise ValueError(f"unexpected existing mount source: {source}")
    if mountpoint and run(["findmnt", "-rn", "-M", str(mountpoint)], check=False).stdout.strip():
        raise ValueError(f"unexpected existing mountpoint: {mountpoint}")


def require_no_signature(path: str) -> None:
    blkid = run(["blkid", "-p", "-o", "export", path], check=False)
    wipefs = run(["wipefs", "--noheadings", "--output", "TYPE", path], check=False)
    if blkid.stdout.strip() or wipefs.stdout.strip():
        raise ValueError(f"unexpected existing filesystem signature on {path}")
    if blkid.returncode not in (0, 2):
        raise RuntimeError(f"blkid probe failed for {path}: {blkid.stderr.strip()}")


def loop_record(loop: str, image: Path) -> dict:
    if not loop.startswith("/dev/loop") or not loop.removeprefix("/dev/loop").isdigit():
        raise ValueError(f"unexpected loop device name: {loop}")
    metadata = os.stat(loop, follow_symlinks=False)
    if not stat.S_ISBLK(metadata.st_mode):
        raise ValueError(f"loop result is not a block device: {loop}")
    result = run(["losetup", "--json", "--output",
                  "NAME,BACK-FILE,OFFSET,SIZELIMIT,RO,AUTOCLEAR", loop])
    devices = json.loads(result.stdout).get("loopdevices", [])
    if len(devices) != 1:
        raise ValueError(f"loop identity is ambiguous: {loop}")
    item = devices[0]
    backing = Path(item["back-file"]).resolve(strict=True)
    if backing != image or int(item["offset"]) != 0 or int(item["sizelimit"]) != IMAGE_BYTES:
        raise ValueError(f"loop backing identity/offset/size mismatch: {item}")
    if bool(item["ro"]) or bool(item["autoclear"]):
        raise ValueError(f"loop unexpectedly read-only or autoclear: {item}")
    size = int(run(["blockdev", "--getsize64", loop]).stdout.strip())
    if size != IMAGE_BYTES:
        raise ValueError(f"loop size mismatch: {size}")
    return {"device": loop, "backing_file": str(backing), "offset": 0,
            "size_limit": IMAGE_BYTES, "block_bytes": size}


def filesystem_record(loop: str, expected_label: str) -> dict:
    fs_type = run(["blkid", "-s", "TYPE", "-o", "value", loop]).stdout.strip()
    label = run(["blkid", "-s", "LABEL", "-o", "value", loop]).stdout.strip()
    uuid = run(["blkid", "-s", "UUID", "-o", "value", loop]).stdout.strip()
    if fs_type != "ext4" or label != expected_label or not uuid:
        raise ValueError(f"unexpected formatted filesystem identity: {loop}")
    return {"type": fs_type, "label": label, "uuid": uuid}


def mounted_record(loop: str, mountpoint: Path) -> dict:
    result = run(["findmnt", "--json", "--mountpoint", str(mountpoint),
                  "--output", "SOURCE,TARGET,FSTYPE,OPTIONS"])
    filesystems = json.loads(result.stdout).get("filesystems", [])
    if len(filesystems) != 1:
        raise ValueError(f"mount identity is ambiguous: {mountpoint}")
    item = filesystems[0]
    if item["source"] != loop or Path(item["target"]) != mountpoint or item["fstype"] != "ext4":
        raise ValueError(f"mount source/target/type mismatch: {item}")
    options = set(item["options"].split(","))
    if not {"rw", "nosuid", "nodev", "noatime"}.issubset(options):
        raise ValueError(f"required mount options missing: {item['options']}")
    stats = os.statvfs(mountpoint)
    capacity = stats.f_blocks * stats.f_frsize
    if capacity > IMAGE_BYTES:
        raise ValueError(f"mounted capacity exceeds 10 GiB: {capacity}")
    return {"source": item["source"], "mountpoint": item["target"],
            "options": item["options"], "capacity_bytes": capacity}


def create_image(path: Path) -> dict:
    require_exact(path, path, strict=False)
    if os.path.lexists(path):
        raise FileExistsError(f"refusing to replace existing path: {path}")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.ftruncate(descriptor, IMAGE_BYTES)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chown(path, 0, 0, follow_symlinks=False)
    os.chmod(path, 0o600, follow_symlinks=False)
    identity = image_identity(path)
    if identity["allocated_bytes"] >= IMAGE_BYTES:
        raise ValueError(f"image is not sparse: {path}")
    require_no_mounts(path)
    if run(["losetup", "-j", str(path)], check=False).stdout.strip():
        raise ValueError(f"new image already has a loop association: {path}")
    require_no_signature(str(path))
    return identity


def provision_arm(name: str, spec: dict, config: dict, source_receipt: dict) -> dict:
    image: Path = spec["image"]
    mountpoint: Path = spec["mount"]
    image_info = create_image(image)
    loop = run(["losetup", "--find", "--show", "--nooverlap", "--offset", "0",
                "--sizelimit", str(IMAGE_BYTES), str(image)]).stdout.strip()
    loop_info = loop_record(loop, image)
    image_after_loop = image_identity(image)
    if (image_after_loop["device"], image_after_loop["inode"]) != (
            image_info["device"], image_info["inode"]):
        raise ValueError("image identity changed after loop attachment")
    require_no_mounts(image, loop, mountpoint)
    require_no_signature(loop)
    run(["mkfs.ext4", "-q", "-m", "0", "-L", spec["label"], loop])
    fs_info = filesystem_record(loop, spec["label"])
    image_identity(image)
    loop_record(loop, image)
    require_no_mounts(image, loop, mountpoint)
    if os.path.lexists(mountpoint):
        raise FileExistsError(f"refusing existing mountpoint path: {mountpoint}")
    mountpoint.mkdir(mode=0o700)
    os.chown(mountpoint, 0, 0)
    require_exact(mountpoint, mountpoint, strict=True)
    run(["mount", "-t", "ext4", "-o", "rw,nosuid,nodev,noatime", loop,
         str(mountpoint)])
    mount_info = mounted_record(loop, mountpoint)
    rootfs = mountpoint / "rootfs"
    rootfs.mkdir(mode=0o755)
    run(["cp", "-a", "--reflink=auto", f"{SOURCE_ROOTFS}/.", str(rootfs)])
    run(["sync", "-f", str(mountpoint)])
    copied = verify_source(rootfs, config, require_agent_tools=True)
    if copied["rootfs_tree_id"] != source_receipt["rootfs_tree_id"]:
        raise ValueError(f"{name} copied rootfs differs from pinned source")
    image_final = image_identity(image)
    return {"name": name, "image": image_final, "loop": loop_info,
            "filesystem": fs_info, "mount": mount_info,
            "rootfs": str(rootfs), "rootfs_tree_id": copied["rootfs_tree_id"],
            "app_tree_id": copied["app_tree_id"]}


def state_snapshot() -> dict:
    state = {"resource_root_exists": os.path.lexists(RESOURCE_ROOT), "arms": {}}
    for name, spec in ARMS.items():
        image = spec["image"]
        mountpoint = spec["mount"]
        state["arms"][name] = {
            "image_exists": os.path.lexists(image),
            "loop_associations": run(["losetup", "-j", str(image)], check=False).stdout.splitlines(),
            "mount": run(["findmnt", "-rn", "-M", str(mountpoint)], check=False).stdout.strip(),
        }
    return state


def provision(config_path: Path) -> dict:
    if os.geteuid() != 0:
        raise PermissionError("provisioning requires WSL root")
    validate_parent()
    require_exact(RESOURCE_ROOT, RESOURCE_ROOT, strict=False)
    if os.path.lexists(RESOURCE_ROOT):
        raise FileExistsError(f"resource root must be new: {RESOURCE_ROOT}")
    source_config = load_config(config_path)
    source_receipt = verify_source(SOURCE_ROOTFS, source_config, require_agent_tools=True)
    RESOURCE_ROOT.mkdir(mode=0o700)
    os.chown(RESOURCE_ROOT, 0, 0)
    validate_resource_root()
    arms = [provision_arm(name, spec, source_config, source_receipt)
            for name, spec in ARMS.items()]
    receipt = {"schema_version": 1, "config_sha256": sha256(config_path),
               "source_rootfs": str(SOURCE_ROOTFS),
               "source_rootfs_tree_id": source_receipt["rootfs_tree_id"],
               "image_bytes": IMAGE_BYTES, "arms": arms}
    descriptor = os.open(RECEIPT, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(descriptor, (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return receipt


def verify(config_path: Path) -> dict:
    validate_parent()
    validate_resource_root()
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    if receipt["config_sha256"] != sha256(config_path):
        raise ValueError("resource receipt config hash mismatch")
    config = load_config(config_path)
    expected_entries = {"baseline.ext4", "treatment.ext4", "mnt-baseline",
                        "mnt-treatment", "resources.json"}
    if {path.name for path in RESOURCE_ROOT.iterdir()} != expected_entries:
        raise ValueError("resource root contains unexpected entries")
    observed = []
    receipt_by_name = {arm["name"]: arm for arm in receipt["arms"]}
    for name, spec in ARMS.items():
        expected = receipt_by_name[name]
        image = image_identity(spec["image"])
        if (image["device"], image["inode"], image["bytes"]) != (
                expected["image"]["device"], expected["image"]["inode"],
                expected["image"]["bytes"]):
            raise ValueError(f"{name} image identity changed")
        loop = expected["loop"]["device"]
        loop_info = loop_record(loop, spec["image"])
        fs_info = filesystem_record(loop, spec["label"])
        if fs_info["uuid"] != expected["filesystem"]["uuid"]:
            raise ValueError(f"{name} filesystem UUID changed")
        mount_info = mounted_record(loop, spec["mount"])
        rootfs = spec["mount"] / "rootfs"
        root_receipt = verify_source(rootfs, config, require_agent_tools=True)
        if root_receipt["rootfs_tree_id"] != receipt["source_rootfs_tree_id"]:
            raise ValueError(f"{name} rootfs tree changed")
        observed.append({"name": name, "image": image, "loop": loop_info,
                         "filesystem": fs_info, "mount": mount_info,
                         "rootfs_tree_id": root_receipt["rootfs_tree_id"]})
    return {"schema_version": 1, "status": "verified", "arms": observed}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("provision", "verify"))
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = provision(args.config) if args.mode == "provision" else verify(args.config)
    except BaseException as error:
        print(json.dumps({"status": "stopped", "error": str(error),
                          "safe_state": state_snapshot()}, indent=2, sort_keys=True),
              file=sys.stderr)
        raise
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
