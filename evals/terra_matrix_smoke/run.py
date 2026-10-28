#!/usr/bin/env python3
"""Run a small, explicitly enabled, serial model-matrix diagnostic."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MODELS = (("gpt-6-luna", "low"), ("gpt-6.1-terra", "medium"),
          ("gpt-6.1-sol", "medium"), ("gpt-6-astra", "high"))
CASES = {
    "visual_match": {
        "prompt": "Examine the image. Which option exactly matches the target? Reply with only its single letter. Do not use tools.",
        "image": "visual-match.png", "expected": "C",
    },
    "classification": {
        "prompt": "Classify each item as urgent if its severity is high OR its deadline is at most 2 days away; otherwise routine. Items: A severity low deadline 3 days; B severity high deadline 8 days; C severity low deadline 2 days; D severity medium deadline 4 days. Return only a JSON object mapping each letter to urgent or routine. Do not use tools.",
        "expected": {"A": "routine", "B": "urgent", "C": "urgent", "D": "routine"},
    },
    "code_reading": {
        "prompt": "For the Python function below, what is the exact returned value for the given data? Return only a JSON array, using nested arrays for tuples. Do not use tools.\n\ndef summarize(records):\n    totals = {}\n    for name, value in records:\n        totals[name] = totals.get(name, 0) + value\n    return sorted(totals.items(), key=lambda item: (-item[1], item[0]))\n\nrecords = [('pear', 4), ('apple', 2), ('lime', 1), ('apple', 3), ('plum', 4)]",
        "expected": [["apple", 5], ["pear", 4], ["plum", 4], ["lime", 1]],
    },
    "natural_writing": {
        "prompt": "Write a concise, natural customer notice of 40 to 70 words using only these facts: a migration is scheduled for 12 November 2026 at 09:00 UTC; the service will be read-only for 15 minutes; a backup has been created; Maya is the contact for questions. Do not promise zero disruption or invent other details. Return only the notice. Do not use tools.",
        "expected": None,
    },
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def grade(case: dict, answer: str | None) -> bool | None:
    expected = case["expected"]
    if expected is None:
        return None  # The retained notices require independent human review.
    if answer is None:
        return False
    if isinstance(expected, str):
        return answer.strip() == expected
    try:
        return json.loads(answer) == expected
    except (ValueError, TypeError):
        return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Explicitly authorize model calls.")
    parser.add_argument("--output", type=Path, required=True, help="New JSONL evidence file.")
    parser.add_argument("--workdir", type=Path, required=True, help="Existing empty or neutral working directory.")
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    if not args.live or not 1 <= args.repetitions <= 3:
        parser.error("--live and 1 to 3 repetitions are required")
    if not args.workdir.is_dir():
        parser.error("workdir must exist")
    executable = shutil.which("codex")
    if not executable:
        parser.error("codex CLI not found")
    version = subprocess.run([executable, "--version"], capture_output=True, text=True, check=True).stdout.strip()
    image = ROOT / "visual-match.png"
    manifest = {"type": "manifest", "version": 1, "provider": "configured Codex provider",
                "cli_version": version, "cli_sha256": sha256(Path(executable)),
                "runner_sha256": sha256(Path(__file__)), "image_sha256": sha256(image),
                "models": MODELS, "cases": CASES, "repetitions": args.repetitions,
                "wall_boundary": "before process launch through process exit",
                "scope": "small direct-call diagnostic; upstream model and billing unverified"}
    with args.output.open("x", encoding="utf-8", newline="\n") as output:
        def emit(record: dict) -> None:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
            output.flush()

        emit(manifest)
        for repetition in range(args.repetitions):
            for case_index, (case_id, case) in enumerate(CASES.items()):
                order = (repetition + case_index) % len(MODELS)
                for model, effort in MODELS[order:] + MODELS[:order]:
                    command = [executable, "exec", "--ephemeral", "--skip-git-repo-check",
                               "--sandbox", "read-only", "--model", model,
                               "-c", f'model_reasoning_effort="{effort}"']
                    if case.get("image"):
                        command += ["--image", str(image)]
                    command += ["--json", case["prompt"]]
                    start = datetime.now(timezone.utc).isoformat()
                    before = time.monotonic()
                    try:
                        result = subprocess.run(command, cwd=args.workdir, stdin=subprocess.DEVNULL,
                                                capture_output=True, text=True, encoding="utf-8",
                                                errors="replace", timeout=120, check=False)
                        elapsed = time.monotonic() - before
                        events = []
                        for line in result.stdout.splitlines():
                            try:
                                events.append(json.loads(line))
                            except ValueError:
                                continue
                        messages = [e["item"]["text"] for e in events
                                    if e.get("type") == "item.completed" and
                                    e.get("item", {}).get("type") == "agent_message"]
                        completed = [e for e in events if e.get("type") == "turn.completed"]
                        item_types = {e.get("item", {}).get("type") for e in events
                                      if e.get("type", "").startswith("item.")}
                        tool_used = bool(item_types - {None, "agent_message", "reasoning", "error"})
                        answer = messages[-1] if messages else None
                        complete = (result.returncode == 0 and len(completed) == 1 and
                                    answer is not None and not tool_used)
                        record = {"type": "run", "case": case_id, "repetition": repetition + 1,
                                  "model": model, "effort": effort, "started_utc": start,
                                  "wall_seconds": round(elapsed, 3), "complete": complete, "tool_used": tool_used,
                                  "answer": answer, "usage": completed[0].get("usage") if complete else None,
                                  "automatic_grade": grade(case, answer) if complete else False,
                                  "failure": None if complete else "incomplete_or_nonzero_exit"}
                    except subprocess.TimeoutExpired:
                        record = {"type": "run", "case": case_id, "repetition": repetition + 1,
                                  "model": model, "effort": effort, "started_utc": start,
                                  "wall_seconds": round(time.monotonic() - before, 3),
                                  "complete": False, "answer": None, "usage": None,
                                  "automatic_grade": False, "failure": "timeout"}
                    emit(record)
                    print(f"{case_id} {repetition + 1} {model} complete={record['complete']}", flush=True)
                    if not record["complete"]:
                        raise SystemExit("Incomplete run; preserve the evidence and diagnose before retrying.")


if __name__ == "__main__":
    main()
