"""Derive evaluator-only control patches from the frozen seed and positive patch.

Each candidate starts in its own fresh copy of the seed. This script never
touches an arm, the positive workspace, or the positive patch.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from evals.long_horizon_v1.common import START_TREE, capture_patch, file_sha, git_tree

HERE = Path(__file__).resolve().parent
FIXTURE = HERE.parent
ADDED_TESTS = (
    "internal/oci/ownership_test.go",
    "internal/storage/fs/oci/reference_test.go",
    "internal/storage/fs/snapshot_ownership_test.go",
    "internal/storage/fs/store/oci_reference_test.go",
)


def replace(path: Path, old: str, new: str) -> None:
    source = path.read_text(encoding="utf-8")
    if source.count(old) != 1:
        raise ValueError(f"expected exactly one edit site in {path}: {old[:70]!r}")
    path.write_text(source.replace(old, new), encoding="utf-8", newline="\n")


def mutate(name: str, root: Path) -> None:
    oci = root / "internal/storage/fs/oci/store.go"
    fs = root / "internal/storage/fs/snapshot.go"
    if name == "round0-only":
        replace(oci, "\t\tvar err error\n\t\tresult, _, err = s.acquire(ctx, string(ref))\n\t\tif err != nil {",
            "\t\tvar err error\n\t\ts.mu.Lock()\n\t\tcached, resident := s.entries[string(ref)]\n\t\ts.mu.Unlock()\n"
            "\t\tif resident && !digestPattern.MatchString(string(ref)) {\n\t\t\tresult = cached\n"
            "\t\t} else {\n\t\t\tresult, _, err = s.acquire(ctx, string(ref))\n\t\t}\n"
            "\t\tif err != nil {")
    elif name == "round1-only":
        replace(oci, "\tworkers  sync.WaitGroup\n", "\tworkers  sync.WaitGroup\n\tcallbacks sync.WaitGroup\n")
        replace(oci, "\ts.mu.Unlock()\n\tif err != nil {\n\t\treturn err\n\t}\n\treturn fn(result.snap)",
            "\tif err == nil {\n\t\ts.callbacks.Add(1)\n\t}\n\ts.mu.Unlock()\n"
            "\tif err != nil {\n\t\treturn err\n\t}\n\tdefer s.callbacks.Done()\n\treturn fn(result.snap)")
        replace(oci, "\ts.workers.Wait()\n\treturn nil\n", "\ts.workers.Wait()\n\ts.callbacks.Wait()\n\treturn nil\n")
    elif name == "ignore-reference":
        replace(oci, "result, _, err = s.acquire(ctx, string(ref))",
                "result, _, err = s.acquire(ctx, s.ref.Reference.Reference)")
    elif name == "wrong-manifest-digest":
        replace(oci, "if cached.manifest.String() == key {",
                "if cached.manifest.String() == key || cached.content.String() == key {")
    elif name == "stale-publication":
        replace(oci, "\t\tif err == nil {\n\t\t\ts.retain(key, result)\n\t\t}",
                "\t\tif err == nil {\n\t\t\tif previous.snap != nil && key != s.ref.Reference.Reference {\n"
                "\t\t\t\tresult = previous\n\t\t\t}\n\t\t\ts.retain(key, result)\n\t\t}")
    elif name == "swallowed-explicit-error":
        replace(oci, "\ts.mu.Lock()\n\tdefer s.mu.Unlock()\n\tif closeErr := s.closedError(); closeErr != nil {",
                "\tif err != nil && previous.snap != nil {\n\t\terr = nil\n\t}\n"
                "\ts.mu.Lock()\n\tdefer s.mu.Unlock()\n\tif closeErr := s.closedError(); closeErr != nil {")
    elif name == "leaked-stream":
        replace(fs, "\tdefer func() {\n\t\tfor _, file := range files {\n\t\t\t_ = file.Close()\n\t\t}\n\t}()\n",
                "")
        replace(fs, "\tfor _, fi := range files {\n\t\tinfo, err := fi.Stat()",
                "\tfor _, fi := range files {\n\t\tdefer fi.Close()\n\t\tinfo, err := fi.Stat()")
    elif name == "broken-close":
        replace(oci, "if s.closed || s.ctx.Err() != nil {",
                "if !s.closed && s.ctx.Err() != nil {")
    elif name == "global-blocking":
        replace(oci, "\tmu       sync.Mutex\n", "\tmu       sync.Mutex\n\tviewMu   sync.Mutex\n")
        replace(oci, "func (s *SnapshotStore) View(ctx context.Context, ref storage.Reference, fn func(storage.ReadOnlyStore) error) error {\n",
                "func (s *SnapshotStore) View(ctx context.Context, ref storage.Reference, fn func(storage.ReadOnlyStore) error) error {\n"
                "\ts.viewMu.Lock()\n\tdefer s.viewMu.Unlock()\n")
    elif name == "per-reference-serialization":
        replace(oci, "type referenceSnapshot struct {", "type viewSerial struct {\n\tmu sync.Mutex\n\tusers int\n}\n\n"
                "type referenceSnapshot struct {")
        replace(oci, "\tworkers  sync.WaitGroup\n", "\tworkers  sync.WaitGroup\n\tviewLocks map[string]*viewSerial\n")
        replace(oci, "\t\tentries: make(map[string]referenceSnapshot), inflight: make(map[string]*acquisition),",
                "\t\tentries: make(map[string]referenceSnapshot), inflight: make(map[string]*acquisition),\n"
                "\t\tviewLocks: make(map[string]*viewSerial),")
        replace(oci, "\treturn fn(result.snap)\n}",
                "\tkey := string(ref)\n\tif key == \"\" {\n\t\tkey = s.ref.Reference.Reference\n\t}\n"
                "\ts.mu.Lock()\n\tserial := s.viewLocks[key]\n\tif serial == nil {\n"
                "\t\tserial = &viewSerial{}\n\t\ts.viewLocks[key] = serial\n\t}\n"
                "\tserial.users++\n\ts.mu.Unlock()\n\tdefer func() {\n\t\ts.mu.Lock()\n"
                "\t\tserial.users--\n\t\tif serial.users == 0 {\n\t\t\tdelete(s.viewLocks, key)\n\t\t}\n"
                "\t\ts.mu.Unlock()\n\t}()\n\tserial.mu.Lock()\n\tdefer serial.mu.Unlock()\n"
                "\ts.mu.Lock()\n\terr = ctx.Err()\n\tif err == nil {\n\t\terr = s.closedError()\n\t}\n"
                "\ts.mu.Unlock()\n\tif err != nil {\n\t\treturn err\n\t}\n\treturn fn(result.snap)\n}")
    else:
        raise ValueError(name)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--only", choices=("round0-only", "round1-only", "ignore-reference",
                        "wrong-manifest-digest", "stale-publication", "swallowed-explicit-error",
                        "leaked-stream", "broken-close", "global-blocking",
                        "per-reference-serialization"))
    parser.add_argument("--output-name", help="For a single candidate, a new patch filename")
    args = parser.parse_args()
    seed = args.seed.resolve()
    workspace = args.workspace.resolve()
    if workspace.exists() or not seed.is_dir() or git_tree(seed) != START_TREE:
        raise ValueError("workspace must be new and seed must be frozen")
    if subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"],
                      cwd=seed, check=True, capture_output=True).stdout.strip():
        raise ValueError("seed must have no tracked or untracked changes")
    workspace.mkdir(parents=True)
    positive = HERE / "positive.patch"
    names = ("round0-only", "round1-only", "ignore-reference", "wrong-manifest-digest",
                 "stale-publication", "swallowed-explicit-error", "leaked-stream",
                 "broken-close", "global-blocking", "per-reference-serialization")
    for name in ((args.only,) if args.only else names):
        candidate = workspace / name
        shutil.copytree(seed, candidate)
        subprocess.run(["git", "apply", "--binary", "-"], cwd=candidate,
                       input=positive.read_bytes(), check=True, capture_output=True)
        for relative in ADDED_TESTS:
            (candidate / relative).unlink()
        mutate(name, candidate)
        patch = capture_patch(candidate, START_TREE)
        output = HERE / (args.output_name or f"{name}.patch")
        if output.exists():
            if output.read_bytes() != patch:
                raise ValueError(f"frozen patch differs from clean derivation: {output}")
        else:
            output.write_bytes(patch)
        print(f"{name} {file_sha(output)} {len(patch)}")


if __name__ == "__main__":
    main()
