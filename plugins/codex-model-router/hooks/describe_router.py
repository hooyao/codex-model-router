#!/usr/bin/env python3
"""Read-only compact decision context; no business request is evaluated."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

if __name__ == "__main__":
    sys.dont_write_bytecode = True

from execution_decision import CONTRACT_VERSION, ESCALATION_TRIGGERS, SIGNAL_FIELDS
from routing_config import RoutingConfigError, load_config


def describe_context(config_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    """Describe the existing contract without evaluating a business request."""
    from routing_config import serialized_config
    root = Path(__file__).resolve().parents[1]
    skill = root / "skills" / "model-router"
    return {
        "schema_version": CONTRACT_VERSION,
        "kind": "decision_context",
        "evaluated_request": False,
        "config_path": str(config_path),
        "resolver_program": str(root / "hooks" / "execution_decision.py"),
        "config_sha256": hashlib.sha256(serialized_config(config).encode("utf-8")).hexdigest(),
        "config": config,
        "request_template": {"schema_version": CONTRACT_VERSION, "decision_id": "request",
            "phase": "initial", "prior_ownership": None, "escalation_trigger": None,
            "matched_example_ids": [], "signals": {field: None for field in sorted(SIGNAL_FIELDS)}},
        "controller_rules": [
            "Before new business work, classify the full request semantically, match examples from config, "
            "set every signal to true/false/null from actual facts, and send this template on stdin to resolver_program with --config=config_path. "
            "Unknown direct facts require delegation. Use a stable request/stage decision_id. "
            "Record the validated result and emit ROUTE: DIRECT or ROUTE: DELEGATE before acting.",
            "Router self-improvement, routing-policy review, evaluation design and benchmark selection need "
            "independent_review_required=true, an independent reviewer, and an implementation worker for requested code changes.",
            "For each business stage record a stable ID, owner, dependencies, exclusive write scope, context budget, "
            "acceptance and self-check. DIRECT performs its bounded implementation and self-check. If bounds expand, stop and resolve "
            "phase=reclassification, prior_ownership=DIRECT, with an explicit escalation_trigger. "
            "A controller-owned stage within DELEGATE needs its own DIRECT decision and inherits whole-request risk and review.",
            "DELEGATE loads the coordination skill and relevant references before business work. "
            "Never duplicate worker-owned work or substitute controller execution when required native capability is missing.",
            "A pure status/coordination reply using already accepted results may reuse its decision. "
            "Any new inspection, edit, test, or changed scope requires a fresh decision; do not claim new validation from memory.",
            "Invalid config or resolver output stops routing. Preserve user intent, permissions, safety, and workspace constraints.",
        ],
        "delegate_references": {"skill": str(skill / "SKILL.md"),
            "delegation": str(skill / "references/delegation.md"),
            "models": str(skill / "references/routing-policy.md"),
            "dispatch": str(skill / "references/dispatch-contract.md")},
        "escalation_triggers": sorted(ESCALATION_TRIGGERS),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        path = args.config.resolve(strict=True)
        context = describe_context(path, load_config(path))
        print(json.dumps(context, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
        return 0
    except (OSError, RoutingConfigError, ValueError) as error:
        print(f"codex-model-router context error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
