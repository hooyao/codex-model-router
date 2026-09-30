"""Build the benchmark's pinned fastText verifier before private-data injection."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

try:
    from evals.scripts.prepare_training_fixture import load_config, sha256_file
except ModuleNotFoundError:  # Direct script execution from evals/scripts.
    from prepare_training_fixture import load_config, sha256_file


def run(args: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"{args!r}: {result.stderr.strip()}")
    return result.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    config = load_config(args.config)
    if args.destination.exists():
        parser.error(f"destination already exists: {args.destination}")
    args.destination.mkdir(parents=True)
    source = args.destination / "source"
    run(["git", "clone", "--filter=blob:none", "--no-checkout",
         config["fasttext"]["repository"], str(source)])
    run(["git", "checkout", "--detach", config["fasttext"]["commit"]], source)
    if run(["git", "rev-parse", "HEAD"], source) != config["fasttext"]["commit"]:
        raise RuntimeError("fastText source commit mismatch")
    compiler = run(["g++", "--version"]).splitlines()[0]
    # v0.9.2 uses uint64_t transitively. Modern GCC no longer guarantees that
    # include. Build statically so the verifier does not depend on the host's
    # newer glibc or libstdc++ when copied into the pinned Debian image.
    flags = "-pthread -std=c++11 -O3 -funroll-loops -include cstdint -static"
    run(["make", "-j1", f"CXXFLAGS={flags}"], source)
    built_binary = source / "fasttext"
    if "statically linked" not in run(["file", str(built_binary)]):
        raise RuntimeError("fastText verifier is not statically linked")
    binary = args.destination / "fasttext"
    shutil.copy2(built_binary, binary)
    binary.chmod(0o755)
    receipt = {"schema_version": 1, "source_commit": config["fasttext"]["commit"],
               "build_flags": flags,
               "compiler": compiler,
               "statically_linked": True,
               "binary_sha256": sha256_file(binary), "binary": str(binary.resolve())}
    (args.destination / "receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
