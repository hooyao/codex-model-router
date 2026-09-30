"""Run the frozen public-only fastText calibration plan inside a trusted rootfs."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
import subprocess
import time
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    if path.exists() or temporary.exists():
        raise FileExistsError(f"refusing to overwrite calibration evidence: {path}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def validate_plan(plan: dict) -> list[dict]:
    configurations = plan.get("configurations")
    if plan.get("schema_version") != 1 or not isinstance(configurations, list):
        raise ValueError("invalid public calibration plan")
    if len(configurations) != 4 or plan.get("maximum_configurations") != 4:
        raise ValueError("public calibration plan must contain exactly four predeclared configurations")
    ids = [item.get("id") for item in configurations]
    if len(set(ids)) != 4 or any(not isinstance(item, str) for item in ids):
        raise ValueError("configuration identifiers must be unique strings")
    return configurations


def convert_public_data(train: Path, validation: Path, destination: Path) -> tuple[Path, Path, list[int]]:
    import pandas as pd

    train_frame = pd.read_parquet(train, columns=["label", "text"])
    validation_frame = pd.read_parquet(validation, columns=["label", "text"])
    if len(train_frame) != 650000 or len(validation_frame) != 10000:
        raise ValueError("unexpected public train/validation row count")
    training_text = destination / "train.txt"
    with training_text.open("x", encoding="utf-8", newline="\n") as output:
        for row in train_frame.itertuples(index=False):
            text = str(row.text).replace("\r", " ").replace("\n", " ")
            output.write(f"__label__{int(row.label)} {text}\n")
    validation_text = destination / "validation-input.txt"
    with validation_text.open("x", encoding="utf-8", newline="\n") as output:
        for text in validation_frame["text"]:
            output.write(str(text).replace("\r", " ").replace("\n", " ") + "\n")
    return training_text, validation_text, [int(label) for label in validation_frame["label"]]


def train_command(fasttext: Path, training: Path, prefix: Path, config: dict) -> list[str]:
    return [str(fasttext), "supervised", "-input", str(training), "-output", str(prefix),
            "-epoch", str(config["epoch"]), "-lr", str(config["learning_rate"]),
            "-dim", str(config["dimension"]), "-bucket", str(config["bucket"]),
            "-minCount", str(config["minimum_count"]),
            "-wordNgrams", str(config["word_ngrams"]),
            "-minn", str(config["minimum_character_ngram"]),
            "-maxn", str(config["maximum_character_ngram"]),
            "-loss", config["loss"], "-thread", "1"]


def public_accuracy(fasttext: Path, model: Path, validation: Path, labels: list[int]) -> tuple[int, float]:
    result = subprocess.run([str(fasttext), "predict", str(model), str(validation)],
                            capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"public prediction failed: {result.stderr[-1000:]}")
    predictions = result.stdout.splitlines()
    if len(predictions) != len(labels):
        raise ValueError("public prediction count mismatch")
    correct = 0
    for prediction, expected in zip(predictions, labels, strict=True):
        label = prediction.removeprefix("__label__")
        correct += int(label) == expected
    return correct, correct / len(labels)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--train", type=Path, required=True)
    parser.add_argument("--validation", type=Path, required=True)
    parser.add_argument("--fasttext", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f"output already exists: {args.output}")
    plan_bytes = args.plan.read_bytes()
    plan = json.loads(plan_bytes)
    configurations = validate_plan(plan)
    if sha256(args.train) != plan["train_sha256"]:
        raise ValueError("public training parquet hash mismatch")
    if sha256(args.validation) != plan["validation_sha256"]:
        raise ValueError("public validation parquet hash mismatch")
    if sha256(args.fasttext) != plan["fasttext_binary_sha256"]:
        raise ValueError("fastText binary hash mismatch")
    args.output.mkdir(parents=True, mode=0o700)
    data = args.output / "public-data"
    data.mkdir(mode=0o700)
    training, validation, labels = convert_public_data(args.train, args.validation, data)
    results = []
    for config in configurations:
        directory = args.output / config["id"]
        directory.mkdir(mode=0o700)
        prefix = directory / "model"
        command = train_command(args.fasttext, training, prefix, config)
        started = time.monotonic()
        trained = subprocess.run(command, capture_output=True, text=True)
        elapsed = time.monotonic() - started
        (directory / "train.stderr").write_text(trained.stderr, encoding="utf-8")
        if trained.returncode:
            raise RuntimeError(f"training failed for {config['id']}: {trained.stderr[-1000:]}")
        vector = prefix.with_suffix(".vec")
        if vector.exists():
            vector.unlink()
        model = prefix.with_suffix(".bin")
        metadata = model.lstat()
        if not stat.S_ISREG(metadata.st_mode) or model.is_symlink():
            raise ValueError(f"configuration did not create a regular model: {config['id']}")
        correct, accuracy = public_accuracy(args.fasttext, model, validation, labels)
        record = {"id": config["id"], "configuration": config,
                  "model_sha256": sha256(model), "model_bytes": metadata.st_size,
                  "public_correct": correct, "public_examples": len(labels),
                  "public_accuracy": accuracy, "elapsed_seconds": elapsed,
                  "size_pass": metadata.st_size < plan["maximum_model_bytes_exclusive"],
                  "public_accuracy_pass": accuracy >= plan["minimum_public_accuracy"]}
        atomic_json(directory / "public-result.json", record)
        results.append(record)
    eligible = [record for record in results if record["size_pass"] and record["public_accuracy_pass"]]
    if not eligible:
        summary = {"schema_version": 1, "status": "NO_ELIGIBLE_CANDIDATE",
                   "plan_sha256": hashlib.sha256(plan_bytes).hexdigest(), "results": results}
        atomic_json(args.output / "selection.json", summary)
        print(json.dumps(summary, indent=2, sort_keys=True))
        raise SystemExit(2)
    selected = sorted(eligible, key=lambda item: (-item["public_accuracy"],
                                                  item["model_bytes"], item["id"]))[0]
    selected_directory = args.output / "selected"
    selected_directory.mkdir(mode=0o700)
    source_model = args.output / selected["id"] / "model.bin"
    selected_model = selected_directory / "model.bin"
    shutil.copyfile(source_model, selected_model, follow_symlinks=False)
    selected_model.chmod(0o400)
    selection = {"schema_version": 1, "status": "SELECTED",
                 "plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
                 "selection_basis": "highest exact public-validation accuracy, then size and id",
                 "selected": {**selected, "selected_model_sha256": sha256(selected_model)},
                 "results": results}
    atomic_json(args.output / "selection.json", selection)
    print(json.dumps(selection, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
