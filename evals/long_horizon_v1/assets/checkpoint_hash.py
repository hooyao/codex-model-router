"""Compute the exact seed-relative patch hash used by benchmark checkpoints."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

START_TREE = "3354dd97a389bac41536cc1cfa4ffe4861912bc8"


def capture_patch(workspace: Path, base_tree: str = START_TREE) -> bytes:
    """Include committed, staged, unstaged, and untracked product changes."""
    workspace = workspace.resolve()
    with tempfile.TemporaryDirectory() as directory:
        index = Path(directory) / "index"
        shutil.copyfile(workspace / ".git" / "index", index)
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(index)
        for command in (["git", "add", "--all"],
                        ["git", "diff", "--cached", "--binary", base_tree]):
            result = subprocess.run(command, cwd=workspace, env=env,
                                    capture_output=True, check=False)
            if result.returncode:
                raise RuntimeError(f"{command[0]} {command[1]} failed: " +
                                   result.stderr.decode(errors="replace")[-1000:])
    return result.stdout


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--base-tree", default=START_TREE,
                        help="Only for local tests; benchmark arms use the frozen seed tree")
    args = parser.parse_args()
    print(hashlib.sha256(capture_patch(args.workspace, args.base_tree)).hexdigest())
