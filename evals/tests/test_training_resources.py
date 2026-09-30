"""Offline unit checks for fixed-path training resource safety helpers."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from evals.scripts.provision_training_resources import image_identity, require_exact
from evals.scripts.run_training_scope import EXPECTED, systemd_command


class TrainingResourceTests(unittest.TestCase):
    def test_exact_path_rejects_symlink(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.write_text("x", encoding="utf-8")
            link = root / "link"
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("symlink creation unavailable")
            with self.assertRaisesRegex(ValueError, "resolution mismatch"):
                require_exact(link, link, strict=True)

    @unittest.skipUnless(os.name == "posix", "POSIX sparse-file metadata required")
    def test_image_identity_requires_regular_single_link_exact_size(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "image.ext4"
            descriptor = os.open(image, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.ftruncate(descriptor, 4096)
            finally:
                os.close(descriptor)
            with mock.patch("evals.scripts.provision_training_resources.IMAGE_BYTES", 4096):
                identity = image_identity(image)
                self.assertEqual(4096, identity["bytes"])
                linked = Path(directory) / "linked.ext4"
                os.link(image, linked)
                with self.assertRaisesRegex(ValueError, "link count"):
                    image_identity(image)

    def test_scope_contract_is_aggregate_and_fixed(self) -> None:
        self.assertEqual("100000 100000", EXPECTED["cpu.max"])
        self.assertEqual("4294967296", EXPECTED["memory.max"])
        self.assertEqual("0", EXPECTED["memory.swap.max"])
        self.assertEqual("512", EXPECTED["pids.max"])
        command = systemd_command("baseline", ["/bin/true"])
        self.assertIn("--property=RuntimeMaxSec=3600s", command)
        self.assertIn("--property=KillMode=control-group", command)
        self.assertNotIn("--wait", command)
        self.assertNotIn("--pipe", command)


if __name__ == "__main__":
    unittest.main()
