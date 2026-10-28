"""Keep grading within the caller's sandbox profile and preserve file ACLs."""

import base64
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from evals.scripts import artifact_reader


class ArtifactReaderTests(unittest.TestCase):
    def test_normal_reads_need_no_runtime_command(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "result.json"
            path.write_bytes(b"public fixture")
            reader = artifact_reader.ArtifactReader(root / "codex", root / "home", root)
            with mock.patch.object(artifact_reader.subprocess, "run") as run:
                self.assertEqual(b"public fixture", reader.read_bytes(path))
            run.assert_not_called()

    def test_owner_only_artifact_uses_the_same_workspace_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "result.json"
            path.write_bytes(b"public fixture")
            reader = artifact_reader.ArtifactReader(root / "codex", root / "home", root)
            reply = subprocess.CompletedProcess([], 0, stdout=json.dumps({"data": base64.b64encode(b"bytes").decode()}))
            with mock.patch.object(type(path), "read_bytes", side_effect=PermissionError), \
                 mock.patch.object(artifact_reader, "os", SimpleNamespace(name="nt")), \
                 mock.patch.object(artifact_reader.subprocess, "run", return_value=reply) as run:
                self.assertEqual(b"bytes", reader.read_bytes(path))
            command = run.call_args.args[0]
            self.assertIn("--include-managed-config", command)
            self.assertEqual(":workspace", command[command.index("--permission-profile") + 1])
            self.assertEqual(["result.json"], reader.sandbox_reads)
            self.assertEqual(b"public fixture", path.read_bytes())

    def test_foreign_path_is_rejected_before_any_runtime_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            reader = artifact_reader.ArtifactReader(root / "codex", root / "home", workspace)
            with mock.patch.object(artifact_reader.subprocess, "run") as run:
                with self.assertRaises(ValueError):
                    reader.read_bytes(root / "private-provider-config")
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
