"""Hash-pinned exact-tree oracle; never executes candidate code."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

try:
    from .contract import (check_node, digest, hash_ref, integer, nonempty, object_fields,
                           read_json, relative_name, require, safe_path, sha256, string_list)
except ImportError:
    from contract import (check_node, digest, hash_ref, integer, nonempty, object_fields,
                          read_json, relative_name, require, safe_path, sha256, string_list)

GRADER_VERSION = "exact-tree-v1"


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
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


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
    fixture = read_json(path)
    object_fields(fixture, "schema_version id grader_version prompt initial reference", "fixture")
    integer(fixture["schema_version"], "fixture.schema_version", 2, 2)
    require(fixture["id"] == "small-edit", "only small-edit is fixture-backed in this slice")
    require(fixture["grader_version"] == GRADER_VERSION, "unsupported grader version")
    nonempty(fixture["prompt"], "fixture.prompt")
    for field in ("initial", "reference"):
        value = object_fields(fixture[field], "path tree", field)
        validate_snapshot(value["tree"])
        actual = tree_snapshot(safe_path(path.parent, value["path"]))
        require(actual == value["tree"], f"fixture {field} hash/tree mismatch")
    require(fixture["initial"]["tree"] != fixture["reference"]["tree"], "reference must change the initial tree")
    return fixture


def grade(path: Path, candidate: Path, expected_sha256: str | None = None) -> dict:
    fixture = load_fixture(path)
    actual = tree_snapshot(candidate)
    actual_hash = tree_digest(actual)
    if expected_sha256 is not None:
        digest(expected_sha256, "candidate SHA-256")
        require(actual_hash == expected_sha256, "candidate tree hash mismatch")
    expected = fixture["reference"]["tree"]
    missing = sorted(set(expected["files"]) - set(actual["files"]))
    extra = sorted(set(actual["files"]) - set(expected["files"]))
    changed = sorted(name for name in set(actual["files"]) & set(expected["files"])
                     if actual["files"][name] != expected["files"][name])
    return {"grader_version": GRADER_VERSION, "passed": actual == expected,
            "tree_sha256": actual_hash, "missing_files": missing, "extra_files": extra,
            "hash_mismatches": changed, "directory_mismatch": actual["directories"] != expected["directories"]}


def fixture_for_case(cases_root: Path, case: dict) -> Path | None:
    if case["fixture"] is None:
        return None
    path = hash_ref(cases_root, case["fixture"], "case fixture")
    fixture = load_fixture(path)
    require(case["id"] == fixture["id"] and case["prompt"] == fixture["prompt"], "case/fixture provenance mismatch")
    return path
