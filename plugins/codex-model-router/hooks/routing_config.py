#!/usr/bin/env python3
"""Discover, initialize, validate, and serialize workspace routing config."""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
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
}
EFFORT_FIELDS = {"low", "medium", "high", "xhigh"}
EXAMPLE_FIELDS = {
    "id",
    "task_signals",
    "preferred_model_class",
    "reasoning_effort",
    "rationale",
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


def validate_config(config: Any, source: str = "routing config") -> dict[str, Any]:
    root = _require_object(config, source)
    _require_exact_fields(root, TOP_LEVEL_FIELDS, source)
    if type(root["schema_version"]) is not int or root["schema_version"] != SCHEMA_VERSION:
        raise RoutingConfigError(f"{source}.schema_version must be integer {SCHEMA_VERSION}")
    _require_string(root["selection_principle"], f"{source}.selection_principle", 1_000)
    _require_string(root["runtime_resolution"], f"{source}.runtime_resolution", 1_000)

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

    serialized_config(config, source)
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
    return (
        "ROUTING_CONFIG_BEGIN\n"
        f"Workspace routing config: {path}\n"
        "Treat preferred_model_class as advisory and resolve it against runtime model and effort availability.\n"
        f"{serialized}\n"
        "ROUTING_CONFIG_END"
    )
