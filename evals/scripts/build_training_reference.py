"""Build an independent public-data model used only to calibrate the oracle."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from prepare_training_fixture import sha256_file


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train-parquet", type=Path, required=True)
    parser.add_argument("--fasttext-binary", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    if args.destination.exists():
        parser.error(f"destination already exists: {args.destination}")
    args.destination.mkdir(parents=True, mode=0o700)

    import pandas as pd
    frame = pd.read_parquet(args.train_parquet, columns=["label", "text"])
    if len(frame) != 650000:
        raise ValueError(f"public training row count mismatch: {len(frame)}")
    training = args.destination / "train.txt"
    with training.open("w", encoding="utf-8", newline="\n") as stream:
        for row in frame.itertuples(index=False):
            text = str(row.text).replace("\r", " ").replace("\n", " ")
            stream.write(f"__label__{int(row.label)} {text}\n")
    prefix = args.destination / "reference"
    command = [str(args.fasttext_binary), "supervised", "-input", str(training),
               "-output", str(prefix), "-epoch", "10", "-lr", "0.5",
               "-wordNgrams", "2", "-dim", "64", "-bucket", "100000",
               "-minCount", "5", "-thread", "1", "-loss", "softmax"]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"independent reference training failed: {result.stderr[-1000:]}")
    training.unlink()
    model = prefix.with_suffix(".bin")
    receipt = {"schema_version": 1, "public_rows": len(frame),
               "train_sha256": sha256_file(args.train_parquet),
               "command": command, "model_sha256": sha256_file(model),
               "model_bytes": model.stat().st_size}
    (args.destination / "receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
