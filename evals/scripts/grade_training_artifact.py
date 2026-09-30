"""Trusted, aggregate-only grader for the paired fastText benchmark."""
from __future__ import annotations

import argparse
import errno
import hashlib
import hmac
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Callable

try:
    from evals.scripts.prepare_training_fixture import namespace_command, sha256_file, tree_id
except ModuleNotFoundError:  # Direct or copied trusted-script execution.
    from prepare_training_fixture import namespace_command, sha256_file, tree_id


def _open_anchored_model(candidate_root: Path) -> tuple[int, int, int, os.stat_result, os.stat_result]:
    if os.name != "posix" or os.open not in os.supports_dir_fd:
        raise RuntimeError("secure descriptor-anchored artifact capture requires POSIX dir_fd support")
    directory_flags = (os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW |
                       getattr(os, "O_CLOEXEC", 0))
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    root_fd = app_fd = -1
    try:
        root_fd = os.open(candidate_root, directory_flags)
        root_stat = os.fstat(root_fd)
        app_fd = os.open("app", directory_flags, dir_fd=root_fd)
        app_stat = os.fstat(app_fd)
        model_fd = os.open("model.bin", file_flags, dir_fd=app_fd)
        return root_fd, app_fd, model_fd, root_stat, app_stat
    except FileNotFoundError as error:
        if app_fd >= 0:
            os.close(app_fd)
        if root_fd >= 0:
            os.close(root_fd)
        raise ValueError("candidate is missing the fixed regular path app/model.bin") from error
    except OSError as error:
        if app_fd >= 0:
            os.close(app_fd)
        if root_fd >= 0:
            os.close(root_fd)
        if error.errno in (errno.ELOOP, errno.ENOTDIR):
            raise ValueError("candidate root, app, and model.bin must not be symlinks") from error
        raise


def snapshot_artifact(candidate_root: Path, snapshot: Path, maximum_bytes: int,
                      after_copy_hook: Callable[[], None] | None = None) -> dict:
    """Capture fixed app/model.bin relative to anchored root and app descriptors."""
    root_fd, app_fd, descriptor, root_before, app_before = _open_anchored_model(candidate_root)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("/app/model.bin must be a regular file, not a link")
        if before.st_size <= 0 or before.st_size >= maximum_bytes:
            raise ValueError(f"model size is outside (0, {maximum_bytes}): {before.st_size}")
        snapshot.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        output_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        output = os.open(snapshot, output_flags, 0o400)
        digest = hashlib.sha256()
        remaining = before.st_size
        try:
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    raise ValueError("artifact shrank while being captured")
                digest.update(chunk)
                view = memoryview(chunk)
                while view:
                    written = os.write(output, view)
                    view = view[written:]
                remaining -= len(chunk)
            if os.read(descriptor, 1):
                raise ValueError("artifact grew while being captured")
            os.fsync(output)
        finally:
            os.close(output)
        if after_copy_hook:
            after_copy_hook()
        after = os.fstat(descriptor)
        root_current = candidate_root.lstat()
        app_current = os.stat("app", dir_fd=root_fd, follow_symlinks=False)
        current = os.stat("model.bin", dir_fd=app_fd, follow_symlinks=False)
        descriptor_changed = (before.st_size, before.st_mtime_ns) != (
            after.st_size, after.st_mtime_ns)
        if os.name == "posix":
            descriptor_changed = descriptor_changed or before.st_ctime_ns != after.st_ctime_ns
        path_changed = (not os.path.samestat(root_before, root_current) or
                        not stat.S_ISDIR(app_current.st_mode) or
                        not os.path.samestat(app_before, app_current) or
                        not stat.S_ISREG(current.st_mode) or
                        not os.path.samestat(before, current) or
                        before.st_size != current.st_size or
                        before.st_mtime_ns != current.st_mtime_ns)
        if os.name == "posix":
            path_changed = path_changed or before.st_ctime_ns != current.st_ctime_ns
        if descriptor_changed or path_changed:
            raise ValueError("artifact changed or was replaced while being captured")
    finally:
        os.close(descriptor)
        os.close(app_fd)
        os.close(root_fd)
    snapshot.chmod(0o400)
    return {"sha256": digest.hexdigest(), "bytes": before.st_size}


def validate_artifact(candidate_root: Path, maximum_bytes: int) -> dict:
    """Compatibility helper for tests that do not need a persistent snapshot."""
    with tempfile.TemporaryDirectory(prefix="cmr-artifact-check-") as temporary:
        return snapshot_artifact(candidate_root, Path(temporary) / "model.bin", maximum_bytes)


def load_bound_config(path: Path, expected_sha256: str) -> tuple[dict, str]:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("expected config SHA-256 must be 64 lowercase hexadecimal characters")
    config_bytes = path.read_bytes()
    actual = hashlib.sha256(config_bytes).hexdigest()
    if not hmac.compare_digest(actual, expected_sha256):
        raise ValueError(f"config SHA-256 mismatch: {actual}")
    config = json.loads(config_bytes)
    if config.get("schema_version") != 1:
        raise ValueError("unsupported training fixture schema")
    return config, actual


def verify_bound_file(path: Path, expected_sha256: str, label: str) -> str:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"{label} is not a regular file")
    actual = sha256_file(path)
    if actual != expected_sha256:
        raise ValueError(f"{label} hash mismatch: {actual}")
    return actual


def score_accuracy(correct: int, total: int, threshold: float,
                   significant_digits: int) -> dict:
    if total <= 0 or correct < 0 or correct > total:
        raise ValueError("invalid accuracy counts")
    exact = correct / total
    rendered = format(exact, f".{significant_digits}g")
    official = float(rendered)
    return {"correct": correct, "examples": total, "exact_accuracy": exact,
            "exact_threshold_pass": exact >= threshold,
            "official_cli_accuracy_text": rendered,
            "official_cli_accuracy": official,
            "official_cli_threshold_pass": official >= threshold,
            "minimum_accuracy": threshold}


def extract_private_dataset(archive_path: Path, destination: Path) -> Path:
    candidates = []
    with tarfile.open(archive_path, "r:*") as archive:
        for member in archive:
            pure = Path(member.name)
            if pure.is_absolute() or ".." in pure.parts or member.issym() or member.islnk():
                raise ValueError("private archive contains an unsafe entry")
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError("private archive contains a non-regular entry")
            if pure.name.startswith("._"):  # Ignore macOS AppleDouble metadata.
                continue
            if pure.suffix not in (".parquet", ".txt"):
                raise ValueError("private archive contains an unsupported entry")
            target = destination / pure.name
            if target.exists():
                raise ValueError("duplicate private archive basename")
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("private archive entry is unreadable")
            with source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
            candidates.append(target)
    if len(candidates) != 1:
        raise ValueError("private archive must contain exactly one supported dataset")
    return candidates[0]


def internal_grade(fasttext: Path, model: Path, parquet: Path,
                   expected_rows: int, minimum_accuracy: float,
                   significant_digits: int) -> dict:
    inputs = parquet.with_suffix(".txt")
    if inputs == parquet:
        inputs = parquet.with_name("private-inputs.txt")
    expected_labels = []
    if parquet.suffix == ".parquet":
        import pandas as pd  # Available in the pinned task image.
        frame = pd.read_parquet(parquet, columns=["label", "text"])
        expected_labels = [int(label) for label in frame["label"]]
        texts = (str(text).replace("\r", " ").replace("\n", " ")
                 for text in frame["text"])
        with inputs.open("w", encoding="utf-8", newline="\n") as stream:
            for text in texts:
                stream.write(text + "\n")
    else:
        with parquet.open("r", encoding="utf-8") as source, \
                inputs.open("w", encoding="utf-8", newline="\n") as stream:
            for line in source:
                label, separator, text = line.rstrip("\n").partition(" ")
                if not separator or not label.startswith("__label__"):
                    raise ValueError("private text dataset has an invalid labeled row")
                expected_labels.append(int(label.removeprefix("__label__")))
                stream.write(text.replace("\r", " ") + "\n")
    if len(expected_labels) != expected_rows:
        raise ValueError(f"private row count mismatch: {len(expected_labels)}")
    result = subprocess.run([str(fasttext), "predict", str(model), str(inputs)],
                            capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("fastText prediction failed")
    predictions = result.stdout.splitlines()
    if len(predictions) != expected_rows:
        raise ValueError(f"prediction count mismatch: {len(predictions)}")
    correct = 0
    for predicted, expected in zip(predictions, expected_labels, strict=True):
        label = predicted.removeprefix("__label__")
        try:
            correct += int(label) == int(expected)
        except ValueError as error:
            raise ValueError("model emitted a non-numeric label") from error
    return score_accuracy(correct, expected_rows, minimum_accuracy, significant_digits)


def grade(args: argparse.Namespace) -> dict:
    config, config_hash = load_bound_config(args.config, args.expected_config_sha256)
    oracle = config["oracle"]
    archive_hash = sha256_file(args.private_archive)
    if archive_hash != oracle["private_archive_sha256"]:
        raise ValueError(f"private archive hash mismatch: {archive_hash}")
    if oracle["artifact_path"] != "/app/model.bin":
        raise ValueError("oracle artifact path must remain fixed at /app/model.bin")
    verifier_hash = verify_bound_file(
        args.fasttext_binary, config["agent_environment"]["fasttext_binary_sha256"],
        "trusted fastText verifier")
    grader_hash = sha256_file(Path(__file__))
    source_rootfs_hash = tree_id(args.source_rootfs)
    expected_rootfs = config["agent_environment"].get("rootfs_tree_id")
    if not expected_rootfs or source_rootfs_hash != expected_rootfs:
        raise ValueError(f"trusted source rootfs hash mismatch: {source_rootfs_hash}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cmr-trusted-oracle-", dir=args.output.parent) as temporary:
        temporary_root = Path(temporary)
        snapshot = temporary_root / "model.snapshot"
        artifact = snapshot_artifact(args.candidate_rootfs, snapshot,
                                     oracle["maximum_model_bytes_exclusive"])
        trusted = Path(temporary) / "rootfs"
        shutil.copytree(args.source_rootfs, trusted, symlinks=True)
        if tree_id(trusted) != source_rootfs_hash:
            raise ValueError("trusted source rootfs changed while being copied")
        oracle_dir = trusted / "oracle"
        oracle_dir.mkdir(mode=0o700)
        model = oracle_dir / "model.bin"
        snapshot.replace(model)
        model.chmod(0o400)
        fasttext = oracle_dir / "fasttext"
        shutil.copy2(args.fasttext_binary, fasttext)
        fasttext.chmod(0o755)
        if sha256_file(fasttext) != verifier_hash:
            raise ValueError("trusted fastText verifier changed while being copied")
        private = extract_private_dataset(args.private_archive, oracle_dir)
        private.chmod(0o600)
        grader = oracle_dir / "grader.py"
        shutil.copy2(Path(__file__), grader)
        if sha256_file(grader) != grader_hash:
            raise ValueError("grader changed while being copied")
        shutil.copy2(Path(__file__).with_name("prepare_training_fixture.py"),
                     oracle_dir / "prepare_training_fixture.py")
        command = namespace_command(trusted, [
            "/usr/local/bin/python", "/oracle/grader.py", "--internal",
            "--fasttext-binary", "/oracle/fasttext", "--model", "/oracle/model.bin",
            "--parquet", f"/oracle/{private.name}", "--expected-rows", str(oracle["private_examples"]),
            "--minimum-accuracy", str(oracle["minimum_accuracy"]),
            "--significant-digits", str(oracle["official_cli_significant_digits"]),
        ])
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=config["limits"]["verifier_timeout_seconds"])
        if result.returncode:
            raise RuntimeError("trusted private verifier failed without publishing private output")
        aggregate = json.loads(result.stdout)
    report = {"schema_version": 1, "grader": "independent-fasttext-private-40k",
              "artifact": artifact, "private_archive_sha256": archive_hash,
              "bindings": {"expected_config_sha256": args.expected_config_sha256,
                           "config_sha256": config_hash, "grader_sha256": grader_hash,
                           "source_rootfs_tree_id": source_rootfs_hash,
                           "fasttext_binary_sha256": verifier_hash}, **aggregate}
    report["quality_pass"] = bool(report["official_cli_threshold_pass"] and
                                  artifact["bytes"] < oracle["maximum_model_bytes_exclusive"])
    if args.output.exists():
        raise ValueError(f"output already exists: {args.output}")
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--internal", action="store_true")
    parser.add_argument("--fasttext-binary", type=Path, required=True)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--parquet", type=Path)
    parser.add_argument("--expected-rows", type=int)
    parser.add_argument("--minimum-accuracy", type=float)
    parser.add_argument("--significant-digits", type=int)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--expected-config-sha256")
    parser.add_argument("--candidate-rootfs", type=Path)
    parser.add_argument("--source-rootfs", type=Path)
    parser.add_argument("--private-archive", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.internal:
        print(json.dumps(internal_grade(args.fasttext_binary, args.model, args.parquet,
                                        args.expected_rows, args.minimum_accuracy,
                                        args.significant_digits), sort_keys=True))
    else:
        required = (args.config, args.candidate_rootfs, args.source_rootfs,
                    args.private_archive, args.output, args.expected_config_sha256)
        if any(item is None for item in required):
            parser.error("external grading arguments are required")
        print(json.dumps(grade(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
