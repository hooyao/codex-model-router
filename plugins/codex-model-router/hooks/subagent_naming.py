"""Deterministic canonical names for Codex Model Router worker packets."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable


MAX_PURPOSE_LENGTH = 48
MAX_MODEL_LENGTH = 48
MAX_EFFORT_LENGTH = 24
MAX_NAME_LENGTH = 128
CANONICAL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+){2,}$")
NATIVE_TASK_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)+$")


def normalize_component(value: str, field: str, max_length: int | None = None) -> str:
    """Return one portable component or reject an invalid input."""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")

    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^a-z0-9]+", "-", ascii_value.lower()).strip("-")
    if not normalized:
        raise ValueError(f"{field} must normalize to at least one ASCII letter or digit")
    if max_length is not None and len(normalized) > max_length:
        raise ValueError(f"{field} must be at most {max_length} characters after normalization")
    return normalized


def build_subagent_name(purpose: str, model: str, effort: str) -> str:
    """Build a canonical ``purpose-model-effort`` identifier.

    ``model`` and ``effort`` must be actual values resolved from the runtime
    capability catalog, not router-tier aliases.
    """
    normalized_purpose = normalize_component(purpose, "purpose", MAX_PURPOSE_LENGTH)
    normalized_model = normalize_component(model, "model", MAX_MODEL_LENGTH)
    normalized_effort = normalize_component(effort, "effort", MAX_EFFORT_LENGTH)
    name = f"{normalized_purpose}-{normalized_model}-{normalized_effort}"
    validate_subagent_name(name)
    return name


def validate_subagent_name(
    name: str,
    purpose: str | None = None,
    model: str | None = None,
    effort: str | None = None,
) -> None:
    """Reject a malformed name or one that differs from recorded inputs."""
    if not isinstance(name, str):
        raise ValueError("subagent name must be a string")
    if len(name) > MAX_NAME_LENGTH:
        raise ValueError(f"subagent name must be at most {MAX_NAME_LENGTH} characters")
    if not CANONICAL_NAME_PATTERN.fullmatch(name):
        raise ValueError("subagent name must use lowercase ASCII purpose-model-effort segments")

    supplied_inputs = (purpose, model, effort)
    if any(value is not None for value in supplied_inputs):
        if any(value is None for value in supplied_inputs):
            raise ValueError("purpose, model, and effort must be supplied together")
        expected = build_subagent_name(purpose, model, effort)
        if name != expected:
            raise ValueError(f"subagent name must equal the canonical value: {expected}")


def validate_unique_subagent_names(names: Iterable[str]) -> None:
    """Reject duplicate canonical names in one planned dispatch DAG."""
    seen: set[str] = set()
    duplicates: set[str] = set()
    for name in names:
        validate_subagent_name(name)
        if name in seen:
            duplicates.add(name)
        seen.add(name)
    if duplicates:
        raise ValueError(
            "subagent names must be unique within a dispatch DAG: "
            + ", ".join(sorted(duplicates))
        )


def native_task_name(canonical_name: str) -> str:
    """Adapt a canonical human task ID to an underscore-only native task_name."""

    validate_subagent_name(canonical_name)
    adapted = canonical_name.replace("-", "_")
    if len(adapted) > MAX_NAME_LENGTH:
        raise ValueError(f"native task_name must be at most {MAX_NAME_LENGTH} characters")
    if not NATIVE_TASK_NAME_PATTERN.fullmatch(adapted):
        raise ValueError("native task_name must use lowercase ASCII underscore-delimited segments")
    return adapted


def validate_unique_native_task_names(canonical_names: Iterable[str]) -> None:
    """Reject native task_name collisions after deterministic adaptation."""

    adapted_names = [native_task_name(name) for name in canonical_names]
    if len(adapted_names) != len(set(adapted_names)):
        raise ValueError("native task_name values must be unique within a dispatch DAG")
