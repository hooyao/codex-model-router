"""Narrow offline tests for the paired training fixture and oracle gates."""
from __future__ import annotations

import hashlib
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path

from evals.scripts.grade_training_artifact import (extract_private_dataset,
                                                   load_bound_config, score_accuracy, snapshot_artifact,
                                                   validate_artifact, verify_bound_file)
from evals.scripts.prepare_training_fixture import (derive_agent_rootfs, resource_preflight,
                                                    tree_id, verify_source)


class TrainingFixtureTests(unittest.TestCase):
    def config(self, train: bytes, public: bytes) -> dict:
        return {"public_inputs": {
            "train_path": "/app/data/train-00000-of-00001.parquet",
            "train_sha256": hashlib.sha256(train).hexdigest(), "train_bytes": len(train),
            "public_test_path": "/app/data/test-00000-of-00001.parquet",
            "public_test_sha256": hashlib.sha256(public).hexdigest(),
            "public_test_bytes": len(public)},
            "agent_environment": {"fasttext_path": "/usr/local/bin/fasttext",
                                  "fasttext_binary_sha256": hashlib.sha256(b"tool").hexdigest(),
                                  "rootfs_tree_id": None}}

    def rootfs(self, root: Path, train: bytes = b"train", public: bytes = b"public") -> dict:
        data = root / "app" / "data"
        data.mkdir(parents=True)
        (data / "train-00000-of-00001.parquet").write_bytes(train)
        (data / "test-00000-of-00001.parquet").write_bytes(public)
        return self.config(train, public)

    def test_source_verification_and_tree_id_are_content_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            train, public = b"train", b"public"
            config = self.rootfs(root, train, public)
            data = root / "app" / "data"
            first = verify_source(root, config)["app_tree_id"]
            (data / "notes.txt").write_text("one", encoding="utf-8")
            second = tree_id(root / "app")
            self.assertNotEqual(first, second)

    def test_source_rejects_private_material_and_prebuilt_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            train, public = b"a", b"b"
            config = self.rootfs(root, train, public)
            (root / "app" / "private_test.parquet").write_bytes(b"secret")
            with self.assertRaisesRegex(ValueError, "forbidden material"):
                verify_source(root, config)
            (root / "app" / "private_test.parquet").unlink()
            (root / "app" / "model.bin").write_bytes(b"model")
            with self.assertRaisesRegex(ValueError, "model.bin"):
                verify_source(root, config)

    def test_whole_rootfs_rejects_private_path_and_missing_tool(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.rootfs(root)
            with self.assertRaisesRegex(ValueError, "missing the executable"):
                verify_source(root, config, require_agent_tools=True)
            leak = root / "opt" / "oracle"
            leak.mkdir(parents=True)
            (leak / "private_test.txt").write_text("hidden", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "forbidden material"):
                verify_source(root, config)

    def test_derived_rootfs_installs_only_bound_public_tool(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            config = self.rootfs(source)
            tool = root / "fasttext"
            tool.write_bytes(b"tool")
            tool.chmod(0o755)
            destination = root / "derived"
            receipt = derive_agent_rootfs(source, tool, destination, config)
            installed = destination / "usr" / "local" / "bin" / "fasttext"
            self.assertEqual(b"tool", installed.read_bytes())
            self.assertEqual(config["agent_environment"]["fasttext_binary_sha256"],
                             receipt["fasttext"]["sha256"])
            self.assertFalse((destination / "app" / "model.bin").exists())

    def test_resource_gate_fails_on_uncapped_filesystem(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = {"limits": {"storage_mb": 1, "memory_mb": 4096}}
            with self.assertRaisesRegex(ValueError, "filesystem"):
                resource_preflight(Path(directory), config)

    @unittest.skipUnless(os.name == "posix", "descriptor-anchored capture requires POSIX")
    def test_artifact_gate_rejects_empty_large_and_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / "app"
            app.mkdir()
            model = app / "model.bin"
            with self.assertRaisesRegex(ValueError, "missing"):
                validate_artifact(root, 10)
            model.write_bytes(b"")
            with self.assertRaisesRegex(ValueError, "outside"):
                validate_artifact(root, 10)
            model.write_bytes(b"0123456789")
            with self.assertRaisesRegex(ValueError, "outside"):
                validate_artifact(root, 10)
            model.write_bytes(b"ok")
            self.assertEqual(2, validate_artifact(root, 10)["bytes"])
            if hasattr(os, "symlink"):
                target = root / "target.bin"
                target.write_bytes(b"ok")
                model.unlink()
                try:
                    model.symlink_to(target)
                except OSError:
                    return
                with self.assertRaisesRegex(ValueError, "must not be symlinks"):
                    validate_artifact(root, 10)

    @unittest.skipUnless(os.name == "posix", "descriptor-anchored capture requires POSIX")
    def test_artifact_snapshot_rejects_growth(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / "app"
            app.mkdir()
            model = app / "model.bin"
            model.write_bytes(b"stable")
            def grow() -> None:
                with model.open("ab") as stream:
                    stream.write(b"changed")
            with self.assertRaisesRegex(ValueError, "changed or was replaced"):
                snapshot_artifact(root, root / "snapshot.bin", 100, grow)

    @unittest.skipUnless(os.name == "posix", "POSIX replacement semantics required")
    def test_artifact_snapshot_rejects_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / "app"
            app.mkdir()
            model = app / "model.bin"
            model.write_bytes(b"stable")
            replacement = app / "replacement.bin"
            replacement.write_bytes(b"stable")
            def replace() -> None:
                os.replace(replacement, model)
            with self.assertRaisesRegex(ValueError, "changed or was replaced"):
                snapshot_artifact(root, root / "snapshot.bin", 100, replace)

    @unittest.skipUnless(os.name == "posix", "descriptor-anchored capture requires POSIX")
    def test_artifact_snapshot_rejects_symlinked_app_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "candidate"
            candidate.mkdir()
            outside = root / "outside"
            outside.mkdir()
            (outside / "model.bin").write_bytes(b"escaped")
            (candidate / "app").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "must not be symlinks"):
                snapshot_artifact(candidate, root / "snapshot.bin", 100)

    def test_config_digest_is_external_and_checked_before_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"schema_version":1}', encoding="utf-8")
            expected = hashlib.sha256(path.read_bytes()).hexdigest()
            config, actual = load_bound_config(path, expected)
            self.assertEqual(1, config["schema_version"])
            self.assertEqual(expected, actual)
            path.write_text("not-json", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                load_bound_config(path, expected)
            with self.assertRaisesRegex(ValueError, "64 lowercase"):
                load_bound_config(path, expected.upper())

    def test_verifier_hash_is_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            verifier = Path(directory) / "fasttext"
            verifier.write_bytes(b"wrong")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                verify_bound_file(verifier, hashlib.sha256(b"expected").hexdigest(),
                                  "trusted fastText verifier")

    def test_official_cli_rounding_is_separate_from_exact_threshold(self) -> None:
        score = score_accuracy(24799, 40000, 0.62, 3)
        self.assertAlmostEqual(0.619975, score["exact_accuracy"])
        self.assertFalse(score["exact_threshold_pass"])
        self.assertEqual("0.62", score["official_cli_accuracy_text"])
        self.assertTrue(score["official_cli_threshold_pass"])

    def test_private_archive_extractor_accepts_one_safe_parquet_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.parquet"
            source.write_bytes(b"opaque")
            archive = root / "private.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                bundle.add(source, arcname="private.parquet")
            output = root / "out"
            output.mkdir()
            self.assertEqual(b"opaque", extract_private_dataset(archive, output).read_bytes())

    def test_private_archive_extractor_ignores_appledouble_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "private.txt"
            dataset.write_text("__label__0 example\n", encoding="utf-8")
            metadata = root / "metadata"
            metadata.write_bytes(b"not private data")
            archive = root / "private.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                bundle.add(metadata, arcname="._private.txt")
                bundle.add(dataset, arcname="private.txt")
            output = root / "out"
            output.mkdir()
            self.assertEqual(dataset.read_bytes(),
                             extract_private_dataset(archive, output).read_bytes())

    def test_repository_config_freezes_required_commitments(self) -> None:
        path = Path(__file__).resolve().parents[1] / "paired_training" / "config.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        digest_path = path.with_name("config.sha256")
        frozen = digest_path.read_text(encoding="ascii").strip()
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), frozen)
        self.assertEqual("7131e4375048a0e408a8fb404b5f499d726b695b",
                         config["benchmark"]["source_commit"])
        self.assertEqual("sha256:3c77da9617e7fa577de04aef73fb1ac3114be32d7ffef72609c83929d842b157",
                         config["benchmark"]["image_digest"])
        self.assertEqual(40000, config["oracle"]["private_examples"])
        self.assertEqual(157286400, config["oracle"]["maximum_model_bytes_exclusive"])
        self.assertEqual(3, config["oracle"]["official_cli_significant_digits"])
        self.assertEqual("c66126460a7e2182c378562cc3ef4c7709c244466300aecc4e9276377982d56e",
                         config["agent_environment"]["fasttext_binary_sha256"])
        self.assertEqual("102e04806838d168297f3c86d9c76a6ddc62c2395a4c8f7d933530fc0bf708b8",
                         config["agent_environment"]["base_rootfs_tree_id"])
        self.assertEqual("21217a4f600fc2e4730139f024a80b0e9ce55085eac6b673c6886282601d1a40",
                         config["agent_environment"]["rootfs_tree_id"])


if __name__ == "__main__":
    unittest.main()
