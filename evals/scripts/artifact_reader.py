"""Read permitted benchmark outputs without widening their filesystem ACLs."""

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

from .codex_runtime import isolated_environment


class ArtifactReader:
    def __init__(self, cli: Path, home: Path, workspace: Path):
        self.cli, self.home, self.workspace = cli, home, workspace.resolve()
        self.sandbox_reads = []

    def read_bytes(self, path: Path) -> bytes:
        if not path.resolve().is_relative_to(self.workspace) or path.is_symlink():
            raise ValueError("artifact read escaped the benchmark workspace")
        try:
            return path.read_bytes()
        except PermissionError:
            if os.name != "nt":
                raise
        code = ("import base64,json,pathlib; p=pathlib.Path(" + repr(str(path)) + "); "
                "print(json.dumps({'data':base64.b64encode(p.read_bytes()).decode()}))")
        result = subprocess.run([str(self.cli), "sandbox", "--include-managed-config",
            "--permission-profile", ":workspace", "-C", str(self.workspace), "--",
            sys.executable, "-c", code], env=isolated_environment(self.home),
            capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise ValueError("runtime sandbox could not read the permitted output artifact")
        try:
            value = json.loads(result.stdout.strip().splitlines()[-1])
            data = base64.b64decode(value["data"], validate=True)
        except (ValueError, KeyError, IndexError) as error:
            raise ValueError("runtime artifact read returned an invalid byte envelope") from error
        self.sandbox_reads.append(path.relative_to(self.workspace).as_posix())
        return data
