#!/usr/bin/env python3
"""Discover, initialize, validate, and serialize workspace routing config."""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Iterable
from copy import deepcopy
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 3
CONFIG_DIRECTORY = ".codex-model-router"
CONFIG_FILENAME = "routing.json"
MAX_CONFIG_FILE_BYTES = 32_768
MAX_SERIALIZED_CONFIG_BYTES = 16_384
MAX_EXAMPLES = 64
MODEL_CLASSES = {"Astra", "Sol", "Terra", "Luna"}
REASONING_EFFORTS = {"low", "medium", "high", "xhigh", "max", "ultra"}
EXAMPLE_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TOP_LEVEL_FIELDS = {
    "schema_version",
    "selection_principle",
    "runtime_resolution",
    "effort_guidance",
    "official_sources",
    "examples",
    "execution_policy",
}
EFFORT_FIELDS = {"low", "medium", "high", "xhigh"}
EXAMPLE_FIELDS = {
    "id",
    "task_signals",
    "preferred_model_class",
    "reasoning_effort",
    "rationale",
}
EXAMPLE_FIELDS = EXAMPLE_FIELDS | {"execution_mode"}
EXECUTION_MODES = {"direct", "delegate", "evaluate"}
EXECUTION_POLICY_FIELDS = {
    "default_mode",
    "direct_requires_all",
    "delegate_if_any",
    "reroute_on_escalation",
    "delegate_topology",
    "limits",
    "verification",
}
DELEGATE_TOPOLOGY_FIELDS = {
    "parallel_enabled",
    "parallel_requires_all",
    "fallback_mode",
    "context_isolation",
}
LIMIT_FIELDS = {"max_depth", "max_concurrency", "max_retries"}
VERIFICATION_FIELDS = {"direct_minimum", "delegate_minimum", "independent_review_if_any"}
PARALLEL_REQUIRED_SIGNALS = {
    "multiple-bounded-tasks",
    "independent-tasks",
    "no-dependencies",
    "disjoint-write-scopes",
}
INDEPENDENT_REVIEW_SIGNALS = {"independent-review-required", "high-risk"}
DIRECT_REQUIRED_SIGNALS = {
    "one-local-scope",
    "one-bounded-known-outcome",
    "no-network-or-sync",
    "no-long-running-or-monitoring",
    "no-failure-or-recovery-workflow",
    "no-named-multistep-runbook",
    "no-substantive-research-or-investigation",
    "no-independent-review-or-validation",
    "no-high-risk",
    "permissions-confirmed",
    "safety-constraints-known",
    "verification-plan-present",
    "write-scope-known",
}
DELEGATE_SIGNALS = {
    "multiple-repositories-systems-or-sources",
    "named-multistep-runbook",
    "network-or-sync",
    "long-running-or-monitoring",
    "failure-or-recovery-workflow",
    "substantive-research-or-investigation",
    "independent-review-or-validation",
    "high-risk",
}
EXECUTION_SIGNALS = DIRECT_REQUIRED_SIGNALS | DELEGATE_SIGNALS
DEFAULT_EXECUTION_POLICY = {
    "default_mode": "evaluate",
    "direct_requires_all": [
        "one-local-scope",
        "one-bounded-known-outcome",
        "no-network-or-sync",
        "no-long-running-or-monitoring",
        "no-failure-or-recovery-workflow",
        "no-named-multistep-runbook",
        "no-substantive-research-or-investigation",
        "no-independent-review-or-validation",
        "no-high-risk",
        "permissions-confirmed",
        "safety-constraints-known",
        "verification-plan-present",
        "write-scope-known",
    ],
    "delegate_if_any": [
        "multiple-repositories-systems-or-sources",
        "named-multistep-runbook",
        "network-or-sync",
        "long-running-or-monitoring",
        "failure-or-recovery-workflow",
        "substantive-research-or-investigation",
        "independent-review-or-validation",
        "high-risk",
    ],
    "reroute_on_escalation": True,
    "delegate_topology": {
        "parallel_enabled": True,
        "parallel_requires_all": [
            "multiple-bounded-tasks",
            "independent-tasks",
            "no-dependencies",
            "disjoint-write-scopes",
        ],
        "fallback_mode": "isolated_serial",
        "context_isolation": "minimal-packet-compact-receipt",
    },
    "limits": {"max_depth": 1, "max_concurrency": 3, "max_retries": 1},
    "verification": {
        "direct_minimum": "self_check",
        "delegate_minimum": "self_check",
        "independent_review_if_any": ["independent-review-required", "high-risk"],
    },
}


class RoutingConfigError(ValueError):
    """Raised when workspace routing configuration cannot be used safely."""


def plugin_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_template_path() -> Path:
    return plugin_root() / "defaults" / "default-routing.json"


def resolve_workspace_cwd(value: Any = None) -> Path:
    if value is None or value == "":
        candidate = Path.cwd()
    elif isinstance(value, str):
        candidate = Path(value).expanduser()
    else:
        raise RoutingConfigError("event cwd must be a string when provided")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise RoutingConfigError(f"workspace cwd is unavailable: {candidate}: {error}") from error
    if not resolved.is_dir():
        raise RoutingConfigError(f"workspace cwd is not a directory: {resolved}")
    return resolved


def _ancestors(start: Path):
    yield start
    yield from start.parents


def discover_config_path(workspace_cwd: Path) -> tuple[Path, bool]:
    """Return the config path and whether it already exists.

    Existing routing config wins at any ancestor. Otherwise the closest Git
    worktree/repository marker (file or directory) determines the init root.
    Without either, the event cwd is used.
    """

    for directory in _ancestors(workspace_cwd):
        candidate = directory / CONFIG_DIRECTORY / CONFIG_FILENAME
        try:
            if candidate.exists():
                return candidate, True
        except OSError as error:
            raise RoutingConfigError(f"cannot inspect routing config path {candidate}: {error}") from error

    for directory in _ancestors(workspace_cwd):
        marker = directory / ".git"
        try:
            if marker.is_dir() or marker.is_file():
                return directory / CONFIG_DIRECTORY / CONFIG_FILENAME, False
        except OSError as error:
            raise RoutingConfigError(f"cannot inspect Git marker {marker}: {error}") from error

    return workspace_cwd / CONFIG_DIRECTORY / CONFIG_FILENAME, False


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RoutingConfigError(f"{label} must be a JSON object")
    return value


def _require_exact_fields(value: dict[str, Any], expected: set[str], label: str) -> None:
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected)
    if missing:
        raise RoutingConfigError(f"{label} is missing required fields: {', '.join(missing)}")
    if unknown:
        raise RoutingConfigError(f"{label} contains unknown fields: {', '.join(unknown)}")


def _require_string(value: Any, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RoutingConfigError(f"{label} must be a non-empty string")
    if len(value) > maximum:
        raise RoutingConfigError(f"{label} must be at most {maximum} characters")
    return value


def _require_execution_mode(value: Any, label: str) -> str:
    if not isinstance(value, str) or value not in EXECUTION_MODES:
        raise RoutingConfigError(
            f"{label} must be a string set to one of: {', '.join(sorted(EXECUTION_MODES))}"
        )
    return value


def _validate_execution_signals(value: Any, label: str) -> None:
    if not isinstance(value, list) or not value:
        raise RoutingConfigError(f"{label} must be a non-empty array")
    seen: set[str] = set()
    for index, signal in enumerate(value):
        if not isinstance(signal, str) or signal not in EXECUTION_SIGNALS:
            raise RoutingConfigError(
                f"{label}[{index}] must be a string set to one of: "
                f"{', '.join(sorted(EXECUTION_SIGNALS))}"
            )
        if signal in seen:
            raise RoutingConfigError(f"{label} must not contain duplicates")
        seen.add(signal)


def _validate_execution_policy(value: Any, label: str) -> None:
    policy = _require_object(value, label)
    _require_exact_fields(policy, EXECUTION_POLICY_FIELDS, label)
    _require_execution_mode(policy["default_mode"], f"{label}.default_mode")
    _validate_execution_signals(policy["direct_requires_all"], f"{label}.direct_requires_all")
    _validate_execution_signals(policy["delegate_if_any"], f"{label}.delegate_if_any")
    if set(policy["direct_requires_all"]) != DIRECT_REQUIRED_SIGNALS:
        raise RoutingConfigError(f"{label}.direct_requires_all must contain every direct requirement exactly once")
    if set(policy["delegate_if_any"]) != DELEGATE_SIGNALS:
        raise RoutingConfigError(f"{label}.delegate_if_any must contain every delegation signal exactly once")
    if policy["reroute_on_escalation"] is not True:
        raise RoutingConfigError(f"{label}.reroute_on_escalation must be true")

    topology = _require_object(policy["delegate_topology"], f"{label}.delegate_topology")
    _require_exact_fields(topology, DELEGATE_TOPOLOGY_FIELDS, f"{label}.delegate_topology")
    if topology["parallel_enabled"] is not True:
        raise RoutingConfigError(f"{label}.delegate_topology.parallel_enabled must be true")
    _validate_exact_string_set(
        topology["parallel_requires_all"], PARALLEL_REQUIRED_SIGNALS,
        f"{label}.delegate_topology.parallel_requires_all",
    )
    if topology["fallback_mode"] != "isolated_serial":
        raise RoutingConfigError(f"{label}.delegate_topology.fallback_mode must be isolated_serial")
    if topology["context_isolation"] != "minimal-packet-compact-receipt":
        raise RoutingConfigError(
            f"{label}.delegate_topology.context_isolation must be minimal-packet-compact-receipt"
        )

    limits = _require_object(policy["limits"], f"{label}.limits")
    _require_exact_fields(limits, LIMIT_FIELDS, f"{label}.limits")
    for field, maximum in (("max_depth", 4), ("max_concurrency", 8), ("max_retries", 3)):
        setting = limits[field]
        if type(setting) is not int or not 1 <= setting <= maximum:
            raise RoutingConfigError(f"{label}.limits.{field} must be an integer from 1 through {maximum}")

    verification = _require_object(policy["verification"], f"{label}.verification")
    _require_exact_fields(verification, VERIFICATION_FIELDS, f"{label}.verification")
    for field in ("direct_minimum", "delegate_minimum"):
        if verification[field] != "self_check":
            raise RoutingConfigError(f"{label}.verification.{field} must be self_check")
    _validate_exact_string_set(
        verification["independent_review_if_any"], INDEPENDENT_REVIEW_SIGNALS,
        f"{label}.verification.independent_review_if_any",
    )


def _validate_exact_string_set(value: Any, expected: set[str], label: str) -> None:
    if not isinstance(value, list) or any(type(item) is not str for item in value):
        raise RoutingConfigError(f"{label} must be a string array")
    if len(value) != len(set(value)):
        raise RoutingConfigError(f"{label} must not contain duplicates")
    if set(value) != expected:
        raise RoutingConfigError(f"{label} must contain every required value exactly once")


def effective_execution_policy(config: dict[str, Any]) -> dict[str, Any]:
    """Return an isolated effective policy for an already validated config."""

    return deepcopy(config["execution_policy"])


def effective_execution_mode(config: dict[str, Any], example: dict[str, Any]) -> str:
    """Return the configured execution mode for an example."""

    return example["execution_mode"]


def configured_execution_mode(
    config: dict[str, Any], matched_example_ids: Iterable[str]
) -> str:
    """Resolve config precedence after semantic matching, before hard-signal gating.

    One matching route overrides the global default. No match uses the global
    default. Conflicting matching routes fail closed to delegation.
    """

    if isinstance(matched_example_ids, (str, bytes)):
        raise RoutingConfigError("matched_example_ids must be an iterable of route IDs")
    route_ids = list(matched_example_ids)
    if any(not isinstance(route_id, str) for route_id in route_ids):
        raise RoutingConfigError("matched_example_ids entries must be strings")
    if len(route_ids) != len(set(route_ids)):
        raise RoutingConfigError("matched_example_ids must not contain duplicates")

    examples = {example["id"]: example for example in config["examples"]}
    unknown = sorted(set(route_ids) - set(examples))
    if unknown:
        raise RoutingConfigError(f"matched_example_ids contains unknown routes: {', '.join(unknown)}")
    if not route_ids:
        return effective_execution_policy(config)["default_mode"]

    modes = {effective_execution_mode(config, examples[route_id]) for route_id in route_ids}
    return next(iter(modes)) if len(modes) == 1 else "delegate"


def validate_config(config: Any, source: str = "routing config") -> dict[str, Any]:
    root = _require_object(config, source)
    version = root.get("schema_version")
    if type(version) is not int or version != SCHEMA_VERSION:
        raise RoutingConfigError(f"{source}.schema_version must be integer {SCHEMA_VERSION}")
    _require_exact_fields(root, TOP_LEVEL_FIELDS, source)
    _require_string(root["selection_principle"], f"{source}.selection_principle", 1_000)
    _require_string(root["runtime_resolution"], f"{source}.runtime_resolution", 1_000)

    _validate_execution_policy(root["execution_policy"], f"{source}.execution_policy")

    guidance = _require_object(root["effort_guidance"], f"{source}.effort_guidance")
    _require_exact_fields(guidance, EFFORT_FIELDS, f"{source}.effort_guidance")
    for effort in sorted(EFFORT_FIELDS):
        _require_string(guidance[effort], f"{source}.effort_guidance.{effort}", 500)

    sources = root["official_sources"]
    if not isinstance(sources, list) or not sources:
        raise RoutingConfigError(f"{source}.official_sources must be a non-empty array")
    if len(sources) > 16:
        raise RoutingConfigError(f"{source}.official_sources must contain at most 16 entries")
    for index, url in enumerate(sources):
        value = _require_string(url, f"{source}.official_sources[{index}]", 500)
        if not value.startswith("https://"):
            raise RoutingConfigError(f"{source}.official_sources[{index}] must use https://")

    examples = root["examples"]
    if not isinstance(examples, list) or not examples:
        raise RoutingConfigError(f"{source}.examples must be a non-empty array")
    if len(examples) > MAX_EXAMPLES:
        raise RoutingConfigError(f"{source}.examples must contain at most {MAX_EXAMPLES} entries")
    seen_ids: set[str] = set()
    for index, raw_example in enumerate(examples):
        label = f"{source}.examples[{index}]"
        example = _require_object(raw_example, label)
        _require_exact_fields(example, EXAMPLE_FIELDS, label)
        example_id = _require_string(example["id"], f"{label}.id", 64)
        if not EXAMPLE_ID_PATTERN.fullmatch(example_id):
            raise RoutingConfigError(f"{label}.id must match {EXAMPLE_ID_PATTERN.pattern}")
        if example_id in seen_ids:
            raise RoutingConfigError(f"{label}.id duplicates {example_id!r}")
        seen_ids.add(example_id)

        signals = example["task_signals"]
        if not isinstance(signals, list) or not signals:
            raise RoutingConfigError(f"{label}.task_signals must be a non-empty array")
        if len(signals) > 12:
            raise RoutingConfigError(f"{label}.task_signals must contain at most 12 entries")
        for signal_index, signal in enumerate(signals):
            _require_string(signal, f"{label}.task_signals[{signal_index}]", 200)

        model_class = example["preferred_model_class"]
        if not isinstance(model_class, str) or model_class not in MODEL_CLASSES:
            raise RoutingConfigError(
                f"{label}.preferred_model_class must be a string set to one of: "
                f"{', '.join(sorted(MODEL_CLASSES))}"
            )
        effort = example["reasoning_effort"]
        if not isinstance(effort, str) or effort not in REASONING_EFFORTS:
            raise RoutingConfigError(
                f"{label}.reasoning_effort must be a string set to one of: "
                f"{', '.join(sorted(REASONING_EFFORTS))}"
            )
        _require_string(example["rationale"], f"{label}.rationale", 500)

        _require_execution_mode(example["execution_mode"], f"{label}.execution_mode")

    serialized_config(root, source)
    return root


def serialized_config(config: Any, source: str = "routing config") -> str:
    try:
        serialized = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise RoutingConfigError(f"{source} cannot be serialized: {error}") from error
    size = len(serialized.encode("utf-8"))
    if size > MAX_SERIALIZED_CONFIG_BYTES:
        raise RoutingConfigError(
            f"{source} serialized size is {size} bytes; maximum is {MAX_SERIALIZED_CONFIG_BYTES} bytes"
        )
    return serialized


def load_config(path: Path) -> dict[str, Any]:
    try:
        metadata = path.stat()
    except OSError as error:
        raise RoutingConfigError(f"cannot read routing config metadata {path}: {error}") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise RoutingConfigError(f"routing config is not a regular file: {path}")
    if metadata.st_size > MAX_CONFIG_FILE_BYTES:
        raise RoutingConfigError(
            f"routing config {path} is {metadata.st_size} bytes; maximum file size is {MAX_CONFIG_FILE_BYTES} bytes"
        )
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise RoutingConfigError(f"cannot read routing config {path}: {error}") from error
    if len(raw) > MAX_CONFIG_FILE_BYTES:
        raise RoutingConfigError(
            f"routing config {path} is {len(raw)} bytes; maximum file size is {MAX_CONFIG_FILE_BYTES} bytes"
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RoutingConfigError(f"routing config {path} is not valid UTF-8: {error}") from error
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        raise RoutingConfigError(
            f"routing config {path} is malformed JSON at line {error.lineno}, column {error.colno}: {error.msg}"
        ) from error
    return validate_config(parsed, str(path))


def load_default_template() -> tuple[dict[str, Any], bytes]:
    path = default_template_path()
    config = load_config(path)
    try:
        template_bytes = path.read_bytes()
    except OSError as error:
        raise RoutingConfigError(f"cannot read default routing template {path}: {error}") from error
    return config, template_bytes


def initialize_config(path: Path) -> bool:
    _config, template_bytes = load_default_template()
    config_directory = path.parent
    try:
        config_directory.mkdir(mode=0o700, parents=False, exist_ok=True)
    except OSError as error:
        raise RoutingConfigError(f"cannot create routing config directory {config_directory}: {error}") from error
    if not config_directory.is_dir():
        raise RoutingConfigError(f"routing config directory is not a directory: {config_directory}")

    descriptor: int | None = None
    created = False
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(template_bytes)
            stream.flush()
    except FileExistsError:
        return False
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        if created:
            try:
                path.unlink()
            except OSError:
                pass
        raise RoutingConfigError(f"cannot create routing config {path}: {error}") from error
    return True


def load_workspace_config(workspace_cwd: Path) -> tuple[Path, dict[str, Any], bool]:
    path, existed = discover_config_path(workspace_cwd)
    created = False
    if not existed:
        created = initialize_config(path)
    config = load_config(path)
    return path, config, created


def routing_context_block(path: Path, config: dict[str, Any]) -> str:
    serialized = serialized_config(config, str(path))
    resolver = plugin_root() / "hooks" / "execution_decision.py"
    effective_policy = json.dumps(
        effective_execution_policy(config), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return (
        "ROUTING_CONFIG_BEGIN\n"
        f"Workspace routing config: {path}\n"
        "Routing decision contract version: 1. Ownership is resolved before delegate topology, verification, "
        "and model selection. Unknown decision signals cannot qualify for DIRECT.\n"
        f"Decision resolver program: {resolver}\n"
        f"Invocation: use an available Python 3.9+ runtime to run the resolver with --config {path}; "
        "send one decision-request-v1 JSON on stdin and use its validated JSON result.\n"
        "Request fields: schema_version=1, decision_id, phase, prior_ownership, escalation_trigger, "
        "matched_example_ids, and signals. Signals: one_local_scope, bounded_known_outcome, "
        "network_or_sync, long_running_or_monitoring, failure_or_recovery, named_multistep_runbook, "
        "substantive_research_or_investigation, independent_review_required, high_risk, "
        "multiple_bounded_tasks, tasks_independent, dependencies_absent, write_scopes_disjoint, "
        "permissions_confirmed, safety_constraints_known, verification_plan_present, write_scope_known. "
        "Every signal is true, false, or null; null is unknown.\n"
        f"EFFECTIVE_EXECUTION_POLICY:{effective_policy}\n"
        f"{serialized}\n"
        "ROUTING_CONFIG_END"
    )
