"""Hash-pinned exact-tree oracle; never executes candidate code."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

try:
    from .contract import (check_node, digest, hash_ref, integer, nonempty, object_fields,
                           read_json, relative_name, require, safe_path, sha256, string_list)
except ImportError:
    from contract import (check_node, digest, hash_ref, integer, nonempty, object_fields,
                          read_json, relative_name, require, safe_path, sha256, string_list)

GRADER_VERSION = "exact-tree-v2"


def tree_snapshot(root: Path) -> dict:
    check_node(root)
    require(root.is_dir(), "candidate must be a directory")
    files, directories = {}, []

    def visit(directory: Path) -> None:
        for child in sorted(directory.iterdir()):
            check_node(child)
            name = child.relative_to(root).as_posix()
            relative_name(name)
            if child.is_dir():
                directories.append(name)
                visit(child)
            else:
                files[name] = sha256(child)

    visit(root)
    return {"files": files, "directories": sorted(directories)}


def tree_digest(snapshot: dict) -> str:
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_snapshot(snapshot: dict) -> None:
    object_fields(snapshot, "files directories", "tree snapshot")
    require(type(snapshot["files"]) is dict and bool(snapshot["files"]), "snapshot files must be non-empty")
    for name, value in snapshot["files"].items():
        relative_name(name)
        digest(value, "file SHA-256")
    string_list(snapshot["directories"], "directories", allow_empty=True)
    for name in snapshot["directories"]:
        relative_name(name)


def load_fixture(path: Path) -> dict:
    value = read_json(path)
    object_fields(value, "schema_version id grader_version prompt initial reference", "fixture")
    integer(value["schema_version"], "fixture.schema_version", 1, 1)
    nonempty(value["id"], "fixture.id")
    require(value["grader_version"] == GRADER_VERSION, "unsupported grader version")
    nonempty(value["prompt"], "fixture.prompt")
    for field in ("initial", "reference"):
        descriptor = object_fields(value[field], "path tree", field)
        validate_snapshot(descriptor["tree"])
        require(tree_snapshot(safe_path(path.parent, descriptor["path"])) == descriptor["tree"],
                f"fixture {field} hash/tree mismatch")
    require(value["initial"]["tree"] != value["reference"]["tree"], "reference must change initial tree")
    return value


def fixture_for_case(cases_root: Path, case: dict) -> Path:
    path = hash_ref(cases_root, case["fixture"], "case fixture")
    value = load_fixture(path)
    require(case["id"] == value["id"] and case["prompt"] == value["prompt"],
            "case/fixture provenance mismatch")
    return path


def grade(path: Path, candidate: Path, expected_sha256: Optional[str] = None) -> dict:
    value = load_fixture(path)
    actual = tree_snapshot(candidate)
    actual_hash = tree_digest(actual)
    if expected_sha256 is not None:
        digest(expected_sha256, "candidate SHA-256")
        require(actual_hash == expected_sha256, "candidate tree hash mismatch")
    expected = value["reference"]["tree"]
    missing = sorted(set(expected["files"]) - set(actual["files"]))
    extra = sorted(set(actual["files"]) - set(expected["files"]))
    changed = sorted(name for name in set(actual["files"]) & set(expected["files"])
                     if actual["files"][name] != expected["files"][name])
    return {"grader_version": GRADER_VERSION, "passed": actual == expected,
            "tree_sha256": actual_hash, "missing_files": missing, "extra_files": extra,
            "hash_mismatches": changed, "directory_mismatch": actual["directories"] != expected["directories"]}
