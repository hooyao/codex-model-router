"""Frozen evidence checks for the no-model Linux Codex sandbox probe."""
from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path

from evals.scripts.probe_linux_codex_runtime import (EXPECTED_OUTCOMES,
                                                     validate_namespace_evidence,
                                                     validate_outcomes,
                                                     validate_socket_results)


class LinuxCodexRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1] / "paired_training"

    def test_probe_records_expected_package_and_boundaries(self) -> None:
        probe = json.loads((self.root / "linux-runtime-probe.json").read_text(encoding="utf-8"))
        self.assertEqual("PARTIAL", probe["status"])
        self.assertEqual(2, probe["schema_version"])
        self.assertEqual("codex-cli 0.144.1", probe["version"])
        self.assertEqual(
            "3fd50cf96809b1eea294bbfba0a5c3a576871b4876a1f0e91226e520c1923be1",
            probe["install"]["package_sha256"])
        self.assertEqual("network-denied", probe["probes"]["network"])
        self.assertEqual("reads-denied", probe["probes"]["protected_reads"])
        self.assertEqual("writes-bounded", probe["probes"]["writes"])
        self.assertEqual("nested-bounded", probe["probes"]["nested"])
        self.assertEqual("interop-denied", probe["probes"]["wsl_interop"])
        self.assertEqual("UNKNOWN", probe["probes"]["copied_pe"]["verdict"])
        self.assertEqual("not-executable", probe["probes"]["copied_pe"]["outside"])
        self.assertEqual("pe-denied", probe["probes"]["copied_pe"]["inside"])
        for result in probe["probes"]["unix_sockets"].values():
            self.assertTrue(result["attempted"])
            self.assertTrue(result["outside_connectable"])
            self.assertTrue(result["inside_denied"])
        inside = probe["probes"]["isolation"]["inside"]
        self.assertIn("NoNewPrivs:\t1", inside)
        self.assertIn("Seccomp:\t2", inside)
        self.assertTrue(probe["negative_controls"]["unconfined_projection_rejected"])
        self.assertTrue(probe["negative_controls"]["permission_widening_rejected"])
        self.assertTrue(probe["negative_controls"]["socket_second_route_allowed_rejected"])
        self.assertEqual("reachable", probe["outside_positive_controls"]["network"])
        self.assertIn("script_sha256", probe["bindings"])
        self.assertIn("profile_sha256", probe["bindings"])
        self.assertIn("runtime_tree_sha256", probe["bindings"])

    def test_profile_is_named_offline_and_denies_protected_paths(self) -> None:
        config = tomllib.loads((self.root / "linux-probe.config.toml").read_text(encoding="utf-8"))
        self.assertEqual("cmr-offline", config["default_permissions"])
        profile = config["permissions"]["cmr-offline"]
        self.assertFalse(profile["network"]["enabled"])
        self.assertEqual("deny", profile["filesystem"]["/var/lib/cmr-codex-probe-protected"])
        self.assertEqual("write", profile["filesystem"][":workspace_roots"]["."])
        self.assertEqual(
            "deny", profile["network"]["unix_sockets"][
                "/var/lib/cmr-codex-probe/workspace/host-service-canary.sock"])

    def test_deliberate_unconfined_projection_is_rejected(self) -> None:
        outside = ["mnt:[1]", "net:[2]", "pid:[3]"]
        with self.assertRaisesRegex(ValueError, "did not isolate"):
            validate_namespace_evidence(outside, [*outside, "NoNewPrivs:\t1", "Seccomp:\t2"])

    def test_deliberate_permission_widening_is_rejected(self) -> None:
        widened = dict(EXPECTED_OUTCOMES)
        widened["network"] = "network-reachable"
        with self.assertRaisesRegex(ValueError, "permission route network"):
            validate_outcomes(widened)

    def test_first_denied_second_allowed_socket_projection_is_rejected(self) -> None:
        results = {
            "protected": {"attempted": True, "outside_connectable": True,
                          "inside_denied": True},
            "workspace": {"attempted": True, "outside_connectable": True,
                          "inside_denied": False},
        }
        with self.assertRaisesRegex(ValueError, "workspace was reachable"):
            validate_socket_results(results)


if __name__ == "__main__":
    unittest.main()
