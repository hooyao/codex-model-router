#!/usr/bin/env python3
"""Prepare and account for a clean, guarded Codex CLI EvalPlus campaign.

The default paths perform validation or dry-run planning only. The sole path
that invokes ``codex exec`` requires ``run --live`` and a provider-side budget
guard adapter. Generated Python is never executed by this module.
"""

import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "evals" / "evalplus" / "manifest.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SESSION_KEY_RE = re.compile(r"(?:^|_)(?:thread|session)_id$")
SESSION_LIST_KEYS = {
    "thread_ids",
    "session_ids",
    "receiver_thread_ids",
    "worker_sessions",
    "retry_sessions",
    "verification_sessions",
    "abandoned_sessions",
}
REQUIRED_USAGE_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)


class HarnessError(ValueError):
    """A deterministic validation or safety refusal."""


def read_json(path: Path) -> Any:
    def reject_duplicate(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise HarnessError("duplicate JSON key: " + key)
            result[key] = value
        return result

    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicate)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise HarnessError("cannot read JSON %s: %s" % (path, error))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _exact_keys(value: Mapping[str, Any], expected: Set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise HarnessError(
            "%s keys differ; missing=%s unknown=%s"
            % (label, sorted(expected - actual), sorted(actual - expected))
        )


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HarnessError(label + " must be a non-empty string")
    return value


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise HarnessError(label + " must be a positive integer")
    return value


def _sha(value: Any, label: str) -> str:
    text = _string(value, label)
    if not SHA256_RE.fullmatch(text):
        raise HarnessError(label + " must be a lowercase SHA-256")
    return text


def validate_manifest(path: Path = DEFAULT_MANIFEST) -> Dict[str, Any]:
    manifest = read_json(path)
    if not isinstance(manifest, dict):
        raise HarnessError("manifest must be an object")
    _exact_keys(
        manifest,
        {
            "schema_version",
            "campaign_id",
            "reviewed_selection",
            "datasets",
            "tasks",
            "variants",
            "execution",
            "grading",
            "stop_rules",
        },
        "manifest",
    )
    if manifest["schema_version"] != 1:
        raise HarnessError("manifest schema_version must be 1")
    _string(manifest["campaign_id"], "campaign_id")

    reviewed = manifest["reviewed_selection"]
    if not isinstance(reviewed, dict):
        raise HarnessError("reviewed_selection must be an object")
    _exact_keys(
        reviewed,
        {
            "benchmarks",
            "tasks_per_benchmark",
            "repetitions_per_arm",
            "review_model",
            "review_reasoning_effort",
        },
        "reviewed_selection",
    )
    if reviewed != {
        "benchmarks": ["HumanEval+", "MBPP+"],
        "tasks_per_benchmark": 3,
        "repetitions_per_arm": 2,
        "review_model": "gpt-6-astra",
        "review_reasoning_effort": "xhigh",
    }:
        raise HarnessError("reviewed benchmark selection changed")

    datasets = manifest["datasets"]
    if not isinstance(datasets, list) or len(datasets) != 2:
        raise HarnessError("exactly two datasets are required")
    dataset_ids: Set[str] = set()
    for index, dataset in enumerate(datasets):
        if not isinstance(dataset, dict):
            raise HarnessError("dataset %d must be an object" % index)
        _exact_keys(
            dataset,
            {"id", "name", "version", "url", "filename", "sha256", "size_bytes"},
            "dataset %d" % index,
        )
        dataset_id = _string(dataset["id"], "dataset id")
        if dataset_id in dataset_ids:
            raise HarnessError("duplicate dataset id: " + dataset_id)
        dataset_ids.add(dataset_id)
        if not _string(dataset["url"], "dataset url").startswith("https://github.com/evalplus/"):
            raise HarnessError("dataset URL must use the official EvalPlus GitHub organization")
        _sha(dataset["sha256"], "dataset sha256")
        _positive_int(dataset["size_bytes"], "dataset size_bytes")
    versions = {dataset["id"]: dataset["version"] for dataset in datasets}
    if versions != {"humaneval-plus": "v0.1.10", "mbpp-plus": "v0.2.0"}:
        raise HarnessError("dataset identities or versions changed")

    expected_ids = {
        "HumanEval/0",
        "HumanEval/63",
        "HumanEval/90",
        "Mbpp/11",
        "Mbpp/12",
        "Mbpp/67",
    }
    tasks = manifest["tasks"]
    if not isinstance(tasks, list) or len(tasks) != 6:
        raise HarnessError("exactly six selected tasks are required")
    task_ids: Set[str] = set()
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise HarnessError("task %d must be an object" % index)
        _exact_keys(
            task,
            {"task_id", "dataset_id", "entry_point", "record_sha256", "prompt_sha256"},
            "task %d" % index,
        )
        task_id = _string(task["task_id"], "task_id")
        if task_id in task_ids:
            raise HarnessError("duplicate task_id: " + task_id)
        task_ids.add(task_id)
        if task["dataset_id"] not in dataset_ids:
            raise HarnessError("task references unknown dataset: " + str(task["dataset_id"]))
        _string(task["entry_point"], "entry_point")
        _sha(task["record_sha256"], "record_sha256")
        _sha(task["prompt_sha256"], "prompt_sha256")
    if task_ids != expected_ids:
        raise HarnessError("selected task IDs differ: " + repr(sorted(task_ids)))

    variants = manifest["variants"]
    if not isinstance(variants, list) or [item.get("id") for item in variants if isinstance(item, dict)] != [
        "baseline",
        "router",
    ]:
        raise HarnessError("variants must be ordered baseline, router")
    for variant in variants:
        _exact_keys(variant, {"id", "router_enabled", "codex_home_environment"}, "variant")
        if not isinstance(variant["router_enabled"], bool):
            raise HarnessError("router_enabled must be boolean")
        _string(variant["codex_home_environment"], "codex_home_environment")
    if variants[0]["router_enabled"] is not False or variants[1]["router_enabled"] is not True:
        raise HarnessError("router arm isolation flags changed")
    if [variant["codex_home_environment"] for variant in variants] != [
        "EVALPLUS_BASELINE_CODEX_HOME",
        "EVALPLUS_ROUTER_CODEX_HOME",
    ]:
        raise HarnessError("Codex home environment names changed")

    execution = manifest["execution"]
    _exact_keys(
        execution,
        {
            "codex_cli_minimum_version",
            "model",
            "reasoning_effort",
            "sandbox",
            "repetitions",
            "maximum_parallel_runs",
            "planned_run_ceiling",
            "token_reservation_per_run",
            "campaign_token_ceiling",
            "campaign_cost_ceiling_usd",
            "router_plugin",
            "required_flags",
        },
        "execution",
    )
    repetitions = _positive_int(execution["repetitions"], "repetitions")
    if execution["codex_cli_minimum_version"] != "0.144.1":
        raise HarnessError("Codex CLI minimum version changed")
    if execution["model"] != "gpt-6-astra" or execution["reasoning_effort"] != "xhigh":
        raise HarnessError("top-level model or reasoning effort changed")
    if execution["sandbox"] != "workspace-write" or repetitions != 2:
        raise HarnessError("sandbox or repetition contract changed")
    planned = len(tasks) * len(variants) * repetitions
    if execution["planned_run_ceiling"] != planned:
        raise HarnessError("planned_run_ceiling must equal the exact schedule size")
    if execution["maximum_parallel_runs"] != 1:
        raise HarnessError("maximum_parallel_runs must be 1 for the guarded campaign")
    per_run = _positive_int(execution["token_reservation_per_run"], "token reservation")
    if execution["campaign_token_ceiling"] != per_run * planned:
        raise HarnessError("campaign_token_ceiling must equal all run reservations")
    try:
        cost = Decimal(_string(execution["campaign_cost_ceiling_usd"], "cost ceiling"))
    except InvalidOperation:
        raise HarnessError("campaign_cost_ceiling_usd must be decimal text")
    if cost != Decimal("44.24"):
        raise HarnessError("campaign cost ceiling must remain USD 44.24")
    router_plugin = execution["router_plugin"]
    if not isinstance(router_plugin, dict):
        raise HarnessError("router_plugin must be an object")
    _exact_keys(router_plugin, {"name", "version"}, "router_plugin")
    if router_plugin["name"] != "codex-model-router":
        raise HarnessError("router_plugin name mismatch")
    _string(router_plugin["version"], "router_plugin version")
    required_flags = execution["required_flags"]
    if required_flags != ["--ignore-rules", "--json", "--strict-config"]:
        raise HarnessError("required_flags changed from the clean CLI contract")

    grading = manifest["grading"]
    if not isinstance(grading, dict):
        raise HarnessError("grading must be an object")
    _exact_keys(
        grading,
        {
            "evalplus_package_version",
            "selected_task_count",
            "samples_per_task_arm_repetition",
            "required_backend",
            "direct_host_execution",
            "score_rule",
        },
        "grading",
    )
    if grading["evalplus_package_version"] != "0.3.1":
        raise HarnessError("EvalPlus package version changed")
    if grading.get("direct_host_execution") is not False:
        raise HarnessError("direct host grading must remain disabled")
    if grading.get("required_backend") != ["docker", "wsl"]:
        raise HarnessError("grading backends must be docker and wsl")
    if grading.get("selected_task_count") != len(tasks):
        raise HarnessError("selected_task_count mismatch")
    if grading["samples_per_task_arm_repetition"] != 1:
        raise HarnessError("each scheduled run must produce one sample")
    _string(grading["score_rule"], "score_rule")

    stop_rules = manifest["stop_rules"]
    if not isinstance(stop_rules, dict):
        raise HarnessError("stop_rules must be an object")
    _exact_keys(
        stop_rules,
        {
            "no_paid_retry",
            "stop_on_provider_guard_failure",
            "stop_before_run_or_token_or_cost_reservation_ceiling",
            "stop_on_dataset_or_workspace_isolation_failure",
            "stop_on_missing_terminal_usage",
            "missing_terminal_usage_effect",
        },
        "stop_rules",
    )
    for key in (
        "no_paid_retry",
        "stop_on_provider_guard_failure",
        "stop_before_run_or_token_or_cost_reservation_ceiling",
        "stop_on_dataset_or_workspace_isolation_failure",
    ):
        if stop_rules[key] is not True:
            raise HarnessError("required stop rule is disabled: " + key)
    if stop_rules["stop_on_missing_terminal_usage"] is not False:
        raise HarnessError("missing usage must preserve the run instead of discarding it")
    _string(stop_rules["missing_terminal_usage_effect"], "missing_terminal_usage_effect")
    return manifest


def task_slug(task_id: str) -> str:
    return task_id.lower().replace("/", "-")


def build_schedule(manifest: Mapping[str, Any]) -> List[Dict[str, Any]]:
    schedule: List[Dict[str, Any]] = []
    repetitions = int(manifest["execution"]["repetitions"])
    for repetition in range(1, repetitions + 1):
        variant_order = ["baseline", "router"] if repetition % 2 else ["router", "baseline"]
        for task in manifest["tasks"]:
            for variant in variant_order:
                schedule.append(
                    {
                        "run_index": len(schedule) + 1,
                        "run_id": "r%02d-%s-%s" % (repetition, task_slug(task["task_id"]), variant),
                        "pair_id": "r%02d-%s" % (repetition, task_slug(task["task_id"])),
                        "task_id": task["task_id"],
                        "dataset_id": task["dataset_id"],
                        "variant": variant,
                        "repetition": repetition,
                    }
                )
    ceiling = int(manifest["execution"]["planned_run_ceiling"])
    if len(schedule) != ceiling:
        raise HarnessError("schedule exceeds or misses planned run ceiling")
    return schedule


def cost_reservations_microusd(total_usd: str, run_count: int) -> List[int]:
    if run_count <= 0:
        raise HarnessError("run_count must be positive")
    try:
        total = Decimal(total_usd)
    except InvalidOperation:
        raise HarnessError("invalid USD reservation")
    micros_decimal = total * Decimal(1000000)
    micros = int(micros_decimal.to_integral_value(rounding=ROUND_DOWN))
    if Decimal(micros) != micros_decimal or micros < 0:
        raise HarnessError("USD reservation must have at most six decimal places")
    base, remainder = divmod(micros, run_count)
    result = [base + (1 if index < remainder else 0) for index in range(run_count)]
    if sum(result) != micros:
        raise AssertionError("reservation distribution lost microdollars")
    return result


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def require_outside_workspace(path: Path, workspace: Path = ROOT) -> Path:
    resolved = path.resolve()
    root = workspace.resolve()
    if resolved == root or _is_relative_to(resolved, root) or _is_relative_to(root, resolved):
        raise HarnessError("campaign and asset paths must be outside the agent workspace")
    return resolved


def _verify_dataset_file(path: Path, dataset: Mapping[str, Any]) -> None:
    if not path.is_file():
        raise HarnessError("dataset asset missing: " + str(path))
    if path.stat().st_size != dataset["size_bytes"]:
        raise HarnessError("dataset size mismatch: " + str(path))
    if sha256_file(path) != dataset["sha256"]:
        raise HarnessError("dataset SHA-256 mismatch: " + str(path))


def ensure_datasets(manifest: Mapping[str, Any], asset_root: Path, download: bool) -> Dict[str, Path]:
    asset_root.mkdir(parents=True, exist_ok=True)
    paths: Dict[str, Path] = {}
    for dataset in manifest["datasets"]:
        destination = asset_root / dataset["filename"]
        if not destination.exists():
            if not download:
                raise HarnessError("dataset asset absent; rerun prepare with --download: " + str(destination))
            partial = destination.with_suffix(destination.suffix + ".partial")
            try:
                urllib.request.urlretrieve(dataset["url"], partial)
                _verify_dataset_file(partial, dataset)
                partial.replace(destination)
            finally:
                if partial.exists():
                    partial.unlink()
        _verify_dataset_file(destination, dataset)
        paths[dataset["id"]] = destination
    return paths


def select_records(
    manifest: Mapping[str, Any], dataset_paths: Mapping[str, Path]
) -> Dict[str, Dict[str, Any]]:
    expected = {task["task_id"]: task for task in manifest["tasks"]}
    selected: Dict[str, Dict[str, Any]] = {}
    for dataset in manifest["datasets"]:
        path = dataset_paths[dataset["id"]]
        with gzip.open(str(path), "rb") as source:
            for raw_line in source:
                stripped = raw_line.rstrip(b"\r\n")
                try:
                    record = json.loads(stripped.decode("utf-8"))
                except (UnicodeError, json.JSONDecodeError) as error:
                    raise HarnessError("invalid dataset JSONL in %s: %s" % (path, error))
                task_id = record.get("task_id") if isinstance(record, dict) else None
                if task_id not in expected:
                    continue
                task = expected[task_id]
                if task["dataset_id"] != dataset["id"]:
                    raise HarnessError("selected task appears in the wrong dataset: " + str(task_id))
                if task_id in selected:
                    raise HarnessError("duplicate selected task in datasets: " + str(task_id))
                if sha256_bytes(stripped) != task["record_sha256"]:
                    raise HarnessError("selected record hash mismatch: " + str(task_id))
                prompt = record.get("prompt")
                if not isinstance(prompt, str) or sha256_bytes(prompt.encode("utf-8")) != task["prompt_sha256"]:
                    raise HarnessError("selected prompt hash mismatch: " + str(task_id))
                if record.get("entry_point") != task["entry_point"]:
                    raise HarnessError("selected entry point mismatch: " + str(task_id))
                selected[task_id] = record
    if set(selected) != set(expected):
        raise HarnessError("dataset subset is incomplete: " + repr(sorted(set(expected) - set(selected))))
    return selected


def _task_instruction(task_id: str) -> str:
    return (
        "Complete the EvalPlus task in solution.py. Preserve the supplied function signature "
        "and write only the implementation needed for task %s. Do not access parent directories, "
        "network resources, hidden tests, grader assets, or other run directories. Do not execute "
        "or import hidden grader code. Do not execute generated code on the host, including "
        "solution.py or any implementation you generate. Grading runs separately in a container "
        "after you finish. You may inspect and edit files in this task directory."
    ) % task_id


def prepare_campaign(
    manifest_path: Path, campaign_root: Path, asset_root: Path, download: bool
) -> Dict[str, Any]:
    manifest = validate_manifest(manifest_path)
    campaign_root = require_outside_workspace(campaign_root)
    asset_root = require_outside_workspace(asset_root)
    if campaign_root.exists():
        raise HarnessError("campaign root must not already exist: " + str(campaign_root))
    if (
        campaign_root == asset_root
        or _is_relative_to(asset_root, campaign_root)
        or _is_relative_to(campaign_root, asset_root)
    ):
        raise HarnessError("grader assets and campaign tasks must use disjoint directory trees")
    dataset_paths = ensure_datasets(manifest, asset_root, download)
    records = select_records(manifest, dataset_paths)
    schedule = build_schedule(manifest)

    campaign_root.mkdir(parents=True)
    (campaign_root / "tasks").mkdir()
    (campaign_root / "raw").mkdir()
    (campaign_root / "artifacts").mkdir()
    for run in schedule:
        task_root = campaign_root / "tasks" / run["run_id"]
        task_root.mkdir()
        prompt = records[run["task_id"]]["prompt"]
        (task_root / "solution.py").write_text(prompt, encoding="utf-8")
        (task_root / "TASK.md").write_text(_task_instruction(run["task_id"]) + "\n", encoding="utf-8")
        completed = subprocess.run(
            ["git", "init", "--quiet"], cwd=str(task_root), capture_output=True, text=True
        )
        if completed.returncode:
            raise HarnessError("git init failed for %s: %s" % (task_root, completed.stderr.strip()))

    prepared = {
        "schema_version": 1,
        "campaign_id": manifest["campaign_id"],
        "manifest_path": str(manifest_path.resolve()),
        "manifest_sha256": sha256_file(manifest_path),
        "asset_root": str(asset_root),
        "dataset_assets": {
            dataset_id: {"path": str(path), "sha256": sha256_file(path)}
            for dataset_id, path in sorted(dataset_paths.items())
        },
        "schedule": schedule,
        "state": {
            "attempted_run_ids": [],
            "completed_run_ids": [],
            "reserved_token_total": 0,
            "reserved_cost_microusd": 0
        },
    }
    write_json(campaign_root / "campaign.json", prepared)
    return prepared


def build_codex_command(
    manifest: Mapping[str, Any], run: Mapping[str, Any], task_root: Path
) -> List[str]:
    execution = manifest["execution"]
    command = [
        "codex",
        "exec",
        "--ignore-rules",
        "--json",
        "--strict-config",
        "--model",
        execution["model"],
        "--config",
        'model_reasoning_effort="%s"' % execution["reasoning_effort"],
        "--config",
        'approval_policy="never"',
        "--sandbox",
        execution["sandbox"],
        "--cd",
        str(task_root.resolve()),
        "--disable",
        "browser_use",
        "--disable",
        "computer_use",
        "--disable",
        "in_app_browser",
        "--disable",
        "apps",
        "--disable",
        "remote_plugin",
        "--disable",
        "memories",
        "--config",
        "skills.bundled.enabled=false",
        "--config",
        "memories.use_memories=false",
        "--config",
        "memories.generate_memories=false",
        "--config",
        "project_doc_max_bytes=0",
    ]
    if run["variant"] == "baseline":
        command.extend(["--disable", "plugins", "--disable", "hooks", "--disable", "multi_agent"])
    elif run["variant"] == "router":
        command.extend(
            [
                "--enable",
                "plugins",
                "--enable",
                "hooks",
                "--enable",
                "multi_agent",
                "--dangerously-bypass-hook-trust",
            ]
        )
    else:
        raise HarnessError("unknown variant: " + str(run["variant"]))
    command.append("-")
    return command


def _load_prepared(campaign_root: Path, manifest_path: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    campaign_root = require_outside_workspace(campaign_root)
    manifest = validate_manifest(manifest_path)
    prepared = read_json(campaign_root / "campaign.json")
    if prepared.get("campaign_id") != manifest["campaign_id"]:
        raise HarnessError("prepared campaign identity mismatch")
    if prepared.get("manifest_sha256") != sha256_file(manifest_path):
        raise HarnessError("prepared campaign manifest hash mismatch")
    if prepared.get("schedule") != build_schedule(manifest):
        raise HarnessError("prepared schedule mismatch")
    state = prepared.get("state")
    if not isinstance(state, dict):
        raise HarnessError("prepared campaign state must be an object")
    _exact_keys(
        state,
        {
            "attempted_run_ids",
            "completed_run_ids",
            "reserved_token_total",
            "reserved_cost_microusd",
        },
        "prepared campaign state",
    )
    attempted = state["attempted_run_ids"]
    completed = state["completed_run_ids"]
    if not isinstance(attempted, list) or not isinstance(completed, list):
        raise HarnessError("prepared run inventories must be arrays")
    if len(attempted) != len(set(attempted)) or len(completed) != len(set(completed)):
        raise HarnessError("prepared run inventories contain duplicates")
    if not set(completed).issubset(set(attempted)):
        raise HarnessError("completed runs must be attempted")
    scheduled_ids = {run["run_id"] for run in prepared["schedule"]}
    if not set(attempted).issubset(scheduled_ids):
        raise HarnessError("prepared state contains an unscheduled run")
    token_per_run = manifest["execution"]["token_reservation_per_run"]
    if state["reserved_token_total"] != len(attempted) * token_per_run:
        raise HarnessError("prepared token reservation state mismatch")
    reservations = cost_reservations_microusd(
        manifest["execution"]["campaign_cost_ceiling_usd"], len(prepared["schedule"])
    )
    run_indexes = {run["run_id"]: run["run_index"] for run in prepared["schedule"]}
    expected_cost = sum(reservations[run_indexes[run_id] - 1] for run_id in attempted)
    if state["reserved_cost_microusd"] != expected_cost:
        raise HarnessError("prepared cost reservation state mismatch")
    for run in prepared["schedule"]:
        task_root = campaign_root / "tasks" / run["run_id"]
        if not task_root.is_dir() or not (task_root / ".git").exists():
            raise HarnessError("fresh task worktree missing: " + str(task_root))
    return manifest, prepared


def _microusd_to_text(value: int) -> str:
    return format(Decimal(value) / Decimal(1000000), ".6f")


def preflight_plan(
    manifest: Mapping[str, Any], prepared: Mapping[str, Any], campaign_root: Path, run_limit: int
) -> Dict[str, Any]:
    if run_limit <= 0:
        raise HarnessError("run_limit must be positive")
    attempted = set(prepared["state"]["attempted_run_ids"])
    remaining = [run for run in prepared["schedule"] if run["run_id"] not in attempted]
    selected = remaining[:run_limit]
    if not selected:
        raise HarnessError("no remaining scheduled runs")
    execution = manifest["execution"]
    if len(prepared["schedule"]) > execution["planned_run_ceiling"]:
        raise HarnessError("planned run ceiling exceeded")
    token_per_run = execution["token_reservation_per_run"]
    prior_tokens = prepared["state"]["reserved_token_total"]
    requested_tokens = token_per_run * len(selected)
    if prior_tokens + requested_tokens > execution["campaign_token_ceiling"]:
        raise HarnessError("campaign token reservation ceiling exceeded")
    reservations = cost_reservations_microusd(
        execution["campaign_cost_ceiling_usd"], len(prepared["schedule"])
    )
    requested_cost = sum(reservations[run["run_index"] - 1] for run in selected)
    prior_cost = prepared["state"]["reserved_cost_microusd"]
    total_cost_micros = sum(reservations)
    if prior_cost + requested_cost > total_cost_micros:
        raise HarnessError("campaign cost reservation ceiling exceeded")
    return {
        "mode": "dry-run",
        "campaign_id": manifest["campaign_id"],
        "selected_runs": [
            {
                **run,
                "task_root": str((campaign_root / "tasks" / run["run_id"]).resolve()),
                "raw_jsonl": str((campaign_root / "raw" / (run["run_id"] + ".jsonl")).resolve()),
                "token_reservation": token_per_run,
                "cost_reservation_usd": _microusd_to_text(reservations[run["run_index"] - 1]),
                "command": build_codex_command(
                    manifest, run, campaign_root / "tasks" / run["run_id"]
                ),
            }
            for run in selected
        ],
        "requested_token_reservation": requested_tokens,
        "requested_cost_reservation_usd": _microusd_to_text(requested_cost),
        "provider_budget_guard_required_for_live": True,
    }


def _parse_timestamp(value: Any, label: str) -> dt.datetime:
    text = _string(value, label)
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise HarnessError(label + " must be an ISO-8601 timestamp")
    if parsed.tzinfo is None:
        raise HarnessError(label + " must include a timezone")
    return parsed.astimezone(dt.timezone.utc)


def verify_provider_guard(
    receipt: Mapping[str, Any], campaign_id: str, required_microusd: int, now: Optional[dt.datetime] = None
) -> Dict[str, Any]:
    expected = {
        "schema_version",
        "source",
        "campaign_id",
        "guard_id",
        "provider",
        "enforcement",
        "dedicated",
        "status",
        "currency",
        "hard_limit_usd",
        "remaining_usd",
        "checked_at",
        "expires_at",
    }
    if not isinstance(receipt, dict):
        raise HarnessError("provider guard response must be an object")
    _exact_keys(receipt, expected, "provider guard response")
    if receipt["schema_version"] != 1 or receipt["source"] != "provider-budget-guard":
        raise HarnessError("untrusted provider budget guard source")
    if receipt["campaign_id"] != campaign_id:
        raise HarnessError("provider budget guard campaign mismatch")
    if receipt["enforcement"] != "provider-side-hard-limit" or receipt["dedicated"] is not True:
        raise HarnessError("live mode requires a dedicated provider-side hard limit")
    if receipt["status"] != "active" or receipt["currency"] != "USD":
        raise HarnessError("provider budget guard is not active in USD")
    _string(receipt["guard_id"], "guard_id")
    _string(receipt["provider"], "provider")
    try:
        hard_limit = Decimal(_string(receipt["hard_limit_usd"], "hard_limit_usd"))
        remaining = Decimal(_string(receipt["remaining_usd"], "remaining_usd"))
    except InvalidOperation:
        raise HarnessError("provider guard amounts must be decimal text")
    if hard_limit > Decimal("44.24") or hard_limit < Decimal("0"):
        raise HarnessError("provider hard limit exceeds the campaign ceiling")
    required = Decimal(required_microusd) / Decimal(1000000)
    if remaining < required or remaining > hard_limit:
        raise HarnessError("provider guard remaining budget cannot cover the reservation")
    checked = _parse_timestamp(receipt["checked_at"], "checked_at")
    expires = _parse_timestamp(receipt["expires_at"], "expires_at")
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    current = current.astimezone(dt.timezone.utc)
    if checked > current + dt.timedelta(minutes=1) or current - checked > dt.timedelta(minutes=15):
        raise HarnessError("provider budget guard check is stale or from the future")
    if expires <= current:
        raise HarnessError("provider budget guard receipt expired")
    return dict(receipt)


def invoke_provider_guard(
    config_path: Optional[Path], request: Mapping[str, Any], now: Optional[dt.datetime] = None
) -> Dict[str, Any]:
    if config_path is None:
        raise HarnessError(
            "live mode refused: no trusted provider-side budget guard adapter was configured"
        )
    config = read_json(config_path)
    if not isinstance(config, dict):
        raise HarnessError("provider guard config must be an object")
    _exact_keys(
        config,
        {"schema_version", "command", "executable_sha256", "expected_provider"},
        "provider guard config",
    )
    command = config["command"]
    if config["schema_version"] != 1 or not isinstance(command, list) or not command:
        raise HarnessError("provider guard command must be a non-empty argv array")
    if any(not isinstance(item, str) or not item for item in command):
        raise HarnessError("provider guard command entries must be non-empty strings")
    expected_provider = _string(config["expected_provider"], "expected_provider")
    executable = Path(command[0])
    if not executable.is_absolute() or not executable.is_file():
        raise HarnessError("provider guard executable must be an existing absolute file")
    if sha256_file(executable) != _sha(config["executable_sha256"], "guard executable_sha256"):
        raise HarnessError("provider guard executable hash mismatch")
    completed = subprocess.run(
        command,
        input=json.dumps(request, separators=(",", ":")) + "\n",
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise HarnessError("provider budget guard adapter failed: " + completed.stderr.strip())
    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise HarnessError("provider budget guard returned invalid JSON: " + str(error))
    receipt = verify_provider_guard(
        response, str(request["campaign_id"]), int(request["required_microusd"]), now
    )
    if receipt["provider"] != expected_provider:
        raise HarnessError("provider budget guard provider mismatch")
    return receipt


def _session_ids(value: Any) -> Set[str]:
    found: Set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if SESSION_KEY_RE.search(key) and isinstance(child, str) and child:
                found.add(child)
            elif key in SESSION_LIST_KEYS and isinstance(child, list):
                found.update(item for item in child if isinstance(item, str) and item)
            found.update(_session_ids(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_session_ids(child))
    return found


def _usage(value: Any, label: str) -> Dict[str, int]:
    if not isinstance(value, dict):
        raise HarnessError(label + " usage must be an object")
    result: Dict[str, int] = {}
    for key in REQUIRED_USAGE_KEYS:
        token_count = value.get(key)
        if isinstance(token_count, bool) or not isinstance(token_count, int) or token_count < 0:
            raise HarnessError("%s.%s must be a non-negative integer" % (label, key))
        result[key] = token_count
    return result


def parse_usage_events(
    raw_paths: Sequence[Path], declared_sessions: Sequence[Mapping[str, Any]], pricing: Mapping[str, Any]
) -> Dict[str, Any]:
    declared: Dict[str, Mapping[str, Any]] = {}
    for session in declared_sessions:
        if not isinstance(session, dict) or set(session) != {"session_id", "role", "model"}:
            raise HarnessError("each declared session requires session_id, role, and model")
        session_id = _string(session["session_id"], "session_id")
        role = _string(session["role"], "session role")
        model = _string(session["model"], "session model")
        if session_id in declared:
            raise HarnessError("duplicate declared session: " + session_id)
        declared[session_id] = {"session_id": session_id, "role": role, "model": model}
    if not declared:
        raise HarnessError("at least one session must be declared")

    final_usage: Dict[str, Dict[str, int]] = {}
    discovered: Set[str] = set()
    for raw_path in raw_paths:
        active_session: Optional[str] = None
        with raw_path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, 1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as error:
                    raise HarnessError("invalid JSONL %s:%d: %s" % (raw_path, line_number, error))
                if not isinstance(event, dict):
                    raise HarnessError("JSONL event must be an object: %s:%d" % (raw_path, line_number))
                discovered.update(_session_ids(event))
                if event.get("type") == "thread.started":
                    active_session = event.get("thread_id")
                if event.get("type") == "turn.completed" and "usage" in event:
                    session_id = event.get("thread_id") or event.get("session_id") or active_session
                    if not isinstance(session_id, str) or not session_id:
                        raise HarnessError("terminal usage event has no session identity")
                    if session_id in final_usage:
                        raise HarnessError("multiple terminal usage events for session: " + session_id)
                    final_usage[session_id] = _usage(event["usage"], session_id)

    undeclared = (discovered | set(final_usage)) - set(declared)
    missing = set(declared) - set(final_usage)
    if undeclared:
        missing.update(undeclared)
    pricing_models = pricing.get("models") if isinstance(pricing, dict) else None
    if not isinstance(pricing_models, dict):
        pricing_models = {}
    pricing_version = pricing.get("pricing_version") if isinstance(pricing, dict) else None
    entries: List[Dict[str, Any]] = []
    all_priced = True
    if not isinstance(pricing_version, str) or not pricing_version.strip():
        all_priced = False
    total_cost = Decimal("0")
    for session_id in sorted(declared):
        session = declared[session_id]
        usage = final_usage.get(session_id)
        model_rates = pricing_models.get(session["model"])
        estimated: Optional[Decimal] = None
        if usage is not None and isinstance(model_rates, dict):
            try:
                input_rate = Decimal(str(model_rates["input_per_million_usd"]))
                cached_rate = Decimal(str(model_rates["cached_input_per_million_usd"]))
                output_rate = Decimal(str(model_rates["output_per_million_usd"]))
            except (KeyError, InvalidOperation):
                all_priced = False
            else:
                if min(input_rate, cached_rate, output_rate) < 0:
                    raise HarnessError("pricing rates must be non-negative")
                uncached = usage["input_tokens"] - usage["cached_input_tokens"]
                if uncached < 0:
                    raise HarnessError("cached input tokens exceed total input for " + session_id)
                estimated = (
                    Decimal(uncached) * input_rate
                    + Decimal(usage["cached_input_tokens"]) * cached_rate
                    + Decimal(usage["output_tokens"]) * output_rate
                ) / Decimal(1000000)
                total_cost += estimated
        else:
            all_priced = False
        entries.append(
            {
                "session_id": session_id,
                "role": session["role"],
                "model": session["model"],
                "usage": usage,
                "estimated_cost_usd": None if estimated is None else format(estimated, "f"),
            }
        )
    complete = not missing and not undeclared and all_priced
    return {
        "schema_version": 1,
        "source": "codex-cli-final-usage-events",
        "declared_session_count": len(declared),
        "sessions": entries,
        "missing_terminal_usage_sessions": sorted(set(declared) - set(final_usage)),
        "undeclared_sessions": sorted(undeclared),
        "cost_complete": complete,
        "estimated_cost_usd": format(total_cost, "f") if complete else None,
        "pricing_version": pricing_version,
    }


def make_grader_request(
    manifest_path: Path, campaign_root: Path, backend: str, output: Path
) -> Dict[str, Any]:
    if backend == "host":
        raise HarnessError("direct host execution of generated code is refused; use docker or wsl")
    if backend not in ("docker", "wsl"):
        raise HarnessError("grader backend must be docker or wsl")
    manifest, prepared = _load_prepared(campaign_root, manifest_path)
    output_resolved = output.resolve()
    samples: List[Dict[str, Any]] = []
    for run in prepared["schedule"]:
        task_root = campaign_root / "tasks" / run["run_id"]
        if _is_relative_to(output_resolved, task_root.resolve()):
            raise HarnessError("grader request must remain outside every task worktree")
        task_attributes = getattr(os.lstat(str(task_root)), "st_file_attributes", 0)
        if task_root.is_symlink() or task_attributes & 0x400:
            raise HarnessError("task worktree symlink/reparse point is forbidden: " + str(task_root))
        solution = task_root / "solution.py"
        if not solution.is_file():
            raise HarnessError("solution missing for grader request: " + str(solution))
        attributes = getattr(os.lstat(str(solution)), "st_file_attributes", 0)
        if solution.is_symlink() or attributes & 0x400:
            raise HarnessError("solution symlink/reparse point is forbidden: " + str(solution))
        destination = campaign_root / "artifacts" / "solutions" / (run["run_id"] + ".py")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(solution), str(destination))
        samples.append(
            {
                "run_id": run["run_id"],
                "task_id": run["task_id"],
                "variant": run["variant"],
                "repetition": run["repetition"],
                "solution_path": str(destination.resolve()),
                "solution_sha256": sha256_file(destination),
            }
        )
    request = {
        "schema_version": 1,
        "contract": "evalplus-isolated-subset-v1",
        "campaign_id": manifest["campaign_id"],
        "backend": backend,
        "direct_host_execution": False,
        "network_allowed": False,
        "read_only_dataset_assets": prepared["dataset_assets"],
        "selected_task_ids": [task["task_id"] for task in manifest["tasks"]],
        "samples": samples,
        "required_result_fields": ["run_id", "task_id", "base_pass", "plus_pass"],
    }
    write_json(output, request)
    return request


def verify_grader_result(request_path: Path, result_path: Path) -> Dict[str, Any]:
    request = read_json(request_path)
    result = read_json(result_path)
    if not isinstance(result, dict):
        raise HarnessError("grader result must be an object")
    _exact_keys(
        result,
        {"schema_version", "contract", "request_sha256", "backend", "runtime_identity", "results"},
        "grader result",
    )
    if result["schema_version"] != 1 or result["contract"] != "evalplus-isolated-subset-v1":
        raise HarnessError("grader result contract mismatch")
    if result["request_sha256"] != sha256_file(request_path):
        raise HarnessError("grader result request hash mismatch")
    if result["backend"] not in ("docker", "wsl") or result["backend"] != request["backend"]:
        raise HarnessError("grader result does not prove an allowed isolated backend")
    runtime = _string(result["runtime_identity"], "runtime_identity")
    if result["backend"] == "docker" and "@sha256:" not in runtime:
        raise HarnessError("Docker runtime identity must include an image digest")
    expected = {(sample["run_id"], sample["task_id"]) for sample in request["samples"]}
    observed: Set[Tuple[str, str]] = set()
    passed = 0
    if not isinstance(result["results"], list):
        raise HarnessError("grader results must be an array")
    for item in result["results"]:
        if not isinstance(item, dict):
            raise HarnessError("grader result entry must be an object")
        _exact_keys(item, {"run_id", "task_id", "base_pass", "plus_pass"}, "grader entry")
        key = (item["run_id"], item["task_id"])
        if key in observed:
            raise HarnessError("duplicate grader result: " + repr(key))
        observed.add(key)
        if not isinstance(item["base_pass"], bool) or not isinstance(item["plus_pass"], bool):
            raise HarnessError("grader pass fields must be booleans")
        passed += int(item["base_pass"] and item["plus_pass"])
    if observed != expected:
        raise HarnessError("grader result does not cover the exact scheduled sample set")
    return {
        "scheduled_runs": len(expected),
        "passed_runs": passed,
        "pass_rate": passed / len(expected),
        "complete": True,
    }


def validate_plugin_inventories(
    manifest: Mapping[str, Any], baseline_payload: Any, router_payload: Any
) -> None:
    target = manifest["execution"]["router_plugin"]
    for variant, payload in (("baseline", baseline_payload), ("router", router_payload)):
        if not isinstance(payload, dict) or not isinstance(payload.get("installed"), list):
            raise HarnessError("%s plugin inventory is malformed" % variant)
        matches = [
            plugin
            for plugin in payload["installed"]
            if isinstance(plugin, dict) and plugin.get("name") == target["name"]
        ]
        if variant == "baseline" and payload["installed"]:
            raise HarnessError("baseline CODEX_HOME must not install any plugin")
        if variant == "router":
            if len(matches) != 1:
                raise HarnessError("router CODEX_HOME must install exactly one codex-model-router")
            plugin = matches[0]
            if plugin.get("enabled") is not True or plugin.get("version") != target["version"]:
                raise HarnessError("router CODEX_HOME has the wrong router plugin version or state")
            contaminating = [
                plugin.get("name")
                for plugin in payload["installed"]
                if isinstance(plugin, dict)
                and plugin.get("name") != target["name"]
            ]
            if contaminating:
                raise HarnessError(
                    "router CODEX_HOME has unrelated enabled or disabled plugins: "
                    + repr(sorted(contaminating))
                )


def _version_tuple(text: str) -> Tuple[int, int, int]:
    match = re.search(r"(?:^|\s)(\d+)\.(\d+)\.(\d+)(?:\s|$)", text)
    if not match:
        raise HarnessError("cannot parse Codex CLI version: " + text.strip())
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def validate_codex_homes(manifest: Mapping[str, Any], homes: Mapping[str, Path]) -> None:
    try:
        from . import evalplus_isolation as isolation
    except ImportError:
        import evalplus_isolation as isolation
    isolation.validate_homes(homes)
    isolation.validate_runtime_flags(manifest)
    version = subprocess.run(["codex", "--version"], capture_output=True, text=True)
    if version.returncode:
        raise HarnessError("cannot read Codex CLI version: " + version.stderr.strip())
    if _version_tuple(version.stdout) < _version_tuple(
        "codex " + manifest["execution"]["codex_cli_minimum_version"]
    ):
        raise HarnessError("Codex CLI is below the pinned minimum version")
    inventories: Dict[str, Any] = {}
    for variant in ("baseline", "router"):
        environment = isolation.arm_environment(homes[variant])
        plugins = subprocess.run(
            ["codex", "plugin", "list", "--json"],
            capture_output=True,
            text=True,
            env=environment,
            cwd=str(homes[variant].parent),
        )
        if plugins.returncode:
            raise HarnessError("cannot inspect %s CODEX_HOME plugins: %s" % (variant, plugins.stderr.strip()))
        try:
            inventories[variant] = json.loads(plugins.stdout)
        except json.JSONDecodeError as error:
            raise HarnessError("%s plugin inventory is invalid JSON: %s" % (variant, error))
        # The model-visible preflight validates the configured transport. A custom
        # command-auth provider need not have an OpenAI login in auth.json.
    validate_plugin_inventories(manifest, inventories["baseline"], inventories["router"])


def execute_live(
    manifest_path: Path,
    campaign_root: Path,
    baseline_home: Optional[Path],
    router_home: Optional[Path],
    guard_config: Optional[Path],
    run_limit: int,
) -> Dict[str, Any]:
    manifest, prepared = _load_prepared(campaign_root, manifest_path)
    if baseline_home is None or router_home is None:
        raise HarnessError("live mode requires separate baseline and router CODEX_HOME directories")
    homes = {
        "baseline": require_outside_workspace(baseline_home),
        "router": require_outside_workspace(router_home),
    }
    if homes["baseline"] == homes["router"]:
        raise HarnessError("baseline and router CODEX_HOME directories must differ")
    for variant, home in homes.items():
        if not home.is_dir():
            raise HarnessError("%s CODEX_HOME is missing: %s" % (variant, home))
    validate_codex_homes(manifest, homes)
    # Inventory alone does not prove that exec loads the installed treatment.
    try:
        from . import evalplus_isolation as isolation
    except ImportError:
        import evalplus_isolation as isolation
    isolation.require_treatment_receipt(campaign_root, manifest, homes)
    isolation.require_grader_receipt(campaign_root)
    plan = preflight_plan(manifest, prepared, campaign_root, run_limit)
    reservations = cost_reservations_microusd(
        manifest["execution"]["campaign_cost_ceiling_usd"], len(prepared["schedule"])
    )
    completed_now: List[str] = []
    for item in plan["selected_runs"]:
        run_id = item["run_id"]
        required_micros = reservations[item["run_index"] - 1]
        guard_request = {
            "schema_version": 1,
            "campaign_id": manifest["campaign_id"],
            "run_id": run_id,
            "required_microusd": required_micros,
            "campaign_ceiling_microusd": sum(reservations),
        }
        receipt = invoke_provider_guard(guard_config, guard_request)
        write_json(campaign_root / "raw" / (run_id + ".budget-guard.json"), receipt)
        run = next(entry for entry in prepared["schedule"] if entry["run_id"] == run_id)
        task_root = campaign_root / "tasks" / run_id
        raw_path = campaign_root / "raw" / (run_id + ".jsonl")
        stderr_path = campaign_root / "raw" / (run_id + ".stderr.txt")
        if raw_path.exists() or stderr_path.exists():
            raise HarnessError("raw output already exists; refusing to overwrite run " + run_id)
        prepared["state"]["attempted_run_ids"].append(run_id)
        prepared["state"]["reserved_token_total"] += manifest["execution"][
            "token_reservation_per_run"
        ]
        prepared["state"]["reserved_cost_microusd"] += required_micros
        write_json(campaign_root / "campaign.json", prepared)
        environment = isolation.arm_environment(homes[run["variant"]])
        prompt = (task_root / "TASK.md").read_text(encoding="utf-8")
        with raw_path.open("wb") as raw_output, stderr_path.open("wb") as error_output:
            process = subprocess.run(
                build_codex_command(manifest, run, task_root),
                input=prompt.encode("utf-8"),
                stdout=raw_output,
                stderr=error_output,
                cwd=str(task_root),
                env=environment,
            )
        status = {
            "run_id": run_id,
            "exit_code": process.returncode,
            "raw_jsonl_sha256": sha256_file(raw_path),
            "stderr_sha256": sha256_file(stderr_path),
            "token_reservation": manifest["execution"]["token_reservation_per_run"],
            "cost_reservation_microusd": required_micros,
        }
        write_json(campaign_root / "raw" / (run_id + ".status.json"), status)
        if process.returncode:
            raise HarnessError("codex exec failed for %s; no paid retry is permitted" % run_id)
        prepared["state"]["completed_run_ids"].append(run_id)
        write_json(campaign_root / "campaign.json", prepared)
        completed_now.append(run_id)
    return {"mode": "live", "completed_run_ids": completed_now}


def command_prepare(args: argparse.Namespace) -> Dict[str, Any]:
    return prepare_campaign(args.manifest, args.campaign_root, args.asset_root, args.download)


def command_run(args: argparse.Namespace) -> Dict[str, Any]:
    manifest, prepared = _load_prepared(args.campaign_root, args.manifest)
    if not args.live:
        return preflight_plan(manifest, prepared, args.campaign_root, args.run_limit)
    return execute_live(
        args.manifest,
        args.campaign_root,
        args.baseline_codex_home,
        args.router_codex_home,
        args.provider_guard_config,
        args.run_limit,
    )


def command_collect(args: argparse.Namespace) -> Dict[str, Any]:
    declarations = read_json(args.sessions)
    pricing = read_json(args.pricing)
    if not isinstance(declarations, dict) or set(declarations) != {"schema_version", "sessions"}:
        raise HarnessError("session declarations require schema_version and sessions")
    if declarations["schema_version"] != 1 or not isinstance(declarations["sessions"], list):
        raise HarnessError("invalid session declarations")
    ledger = parse_usage_events(args.raw, declarations["sessions"], pricing)
    ledger["raw_sources"] = [
        {"path": str(path.resolve()), "sha256": sha256_file(path)} for path in args.raw
    ]
    ledger["session_declarations_sha256"] = sha256_file(args.sessions)
    ledger["pricing_sha256"] = sha256_file(args.pricing)
    write_json(args.output, ledger)
    return ledger


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate-manifest")
    validate.set_defaults(handler=lambda args: {"campaign_id": validate_manifest(args.manifest)["campaign_id"]})

    prepare = commands.add_parser("prepare")
    prepare.add_argument("--campaign-root", type=Path, required=True)
    prepare.add_argument("--asset-root", type=Path, required=True)
    prepare.add_argument("--download", action="store_true")
    prepare.set_defaults(handler=command_prepare)

    run = commands.add_parser("run")
    run.add_argument("--campaign-root", type=Path, required=True)
    run.add_argument("--run-limit", type=int, default=1)
    run.add_argument("--live", action="store_true")
    run.add_argument("--baseline-codex-home", type=Path)
    run.add_argument("--router-codex-home", type=Path)
    run.add_argument("--provider-guard-config", type=Path)
    run.set_defaults(handler=command_run)

    collect = commands.add_parser("collect")
    collect.add_argument("--raw", type=Path, action="append", required=True)
    collect.add_argument("--sessions", type=Path, required=True)
    collect.add_argument("--pricing", type=Path, required=True)
    collect.add_argument("--output", type=Path, required=True)
    collect.set_defaults(handler=command_collect)

    grader = commands.add_parser("make-grader-request")
    grader.add_argument("--campaign-root", type=Path, required=True)
    grader.add_argument("--backend", choices=("docker", "wsl", "host"), required=True)
    grader.add_argument("--output", type=Path, required=True)
    grader.set_defaults(
        handler=lambda args: make_grader_request(
            args.manifest, args.campaign_root, args.backend, args.output
        )
    )

    verify = commands.add_parser("verify-grader-result")
    verify.add_argument("--request", type=Path, required=True)
    verify.add_argument("--result", type=Path, required=True)
    verify.set_defaults(handler=lambda args: verify_grader_result(args.request, args.result))
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        result = args.handler(args)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except HarnessError as error:
        print("BLOCKED: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
