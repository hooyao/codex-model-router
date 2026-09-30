"""Offline validation and accounting for one matched selective-Astra pair.

This command never starts a model. It rejects incomplete telemetry instead of
turning absent child usage or scope evidence into a favorable cost estimate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

try:
    from .run_paired_arm import (RATES, SELECTOR_FEATURE, SessionMeter, native_lineage, calibrated_controller,
                                 negative_treatment_controller, pinned_prompt, submitted_prompt, sha256)
    from .dispatch_audit_reconcile import reconcile
except ImportError:
    from run_paired_arm import (RATES, SELECTOR_FEATURE, SessionMeter, native_lineage, calibrated_controller,
                                negative_treatment_controller, pinned_prompt, submitted_prompt, sha256)
    from dispatch_audit_reconcile import reconcile


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"expected JSON object: {path}")
    return value


def transcript_terminal(path: Path) -> tuple[str | None, int]:
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    terminals = [event["type"] for event in events if event.get("type") in
                 ("turn.completed", "turn.failed", "turn.cancelled")]
    require(len(terminals) <= 1, f"conflicting CLI terminal events: {path}")
    return (terminals[0] if terminals else None), sum(
        event.get("type") == "item.started" and
        (event.get("item") or {}).get("type") == "collab_tool_call" and
        (event.get("item") or {}).get("tool") == "spawn"
        for event in events)


def session_meta(path: Path) -> dict:
    with path.open(encoding="utf-8") as source:
        first = json.loads(source.readline())
    require(first.get("type") == "session_meta", f"missing session metadata: {path}")
    return first["payload"]


def native_spawns(path: Path) -> list[dict]:
    calls = []
    for line in path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        payload = event.get("payload") or {}
        if (event.get("type") == "response_item" and payload.get("type") == "function_call"
                and payload.get("name") == "spawn_agent"):
            arguments = json.loads(payload["arguments"])
            calls.append({"task_name": arguments.get("task_name"),
                          "model": arguments.get("model"),
                          "effort": arguments.get("reasoning_effort"),
                          "fork_turns": arguments.get("fork_turns")})
    return calls


def router_contexts(path: Path) -> list[str]:
    contexts = []
    for line in path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        payload = event.get("payload") or {}
        if (event.get("type") == "response_item" and payload.get("type") == "message"
                and payload.get("role") == "developer"):
            content = payload.get("content") or []
            text = "\n".join(item.get("text", "") for item in content if isinstance(item, dict))
            if "ROUTING_CONFIG_BEGIN" in text and "ROUTING_CONFIG_END" in text:
                contexts.append(text)
    return contexts


def worker_packet_digest(path: Path) -> str | None:
    final_message = None
    for line in path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        payload = event.get("payload") or {}
        if event.get("type") == "event_msg" and payload.get("type") in (
                "task_complete", "task_completed"):
            final_message = payload.get("last_agent_message")
    if not isinstance(final_message, str):
        return None
    matches = re.findall(r"(?m)^Packet SHA-256: ([0-9a-f]{64})\s*$", final_message)
    return matches[0] if len(matches) == 1 else None


def observed_patch_writes(path: Path) -> list[str]:
    """Extract explicit apply_patch paths; other tool writes remain unknown."""
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        payload = event.get("payload") or {}
        if event.get("type") != "response_item" or payload.get("type") != "custom_tool_call":
            continue
        source = payload.get("input") or ""
        if "tools.apply_patch" not in source:
            continue
        for name in re.findall(r"\*\*\* (?:Update|Add|Delete) File: ([^\r\n]+)", source):
            result.append(name.replace("\\", "/").strip())
    return result


def require_allowed_selectors(spawns: list[dict], children: list[dict], config: dict) -> None:
    permitted = {("gpt-6-astra", "xhigh")} | {
        (item["model"], item["effort"]) for item in
        config["arms"]["treatment"]["allowed_easy_worker_selectors"]}
    for observed in (*spawns, *children):
        identity = (observed.get("model"), observed.get("effort"))
        require(identity in permitted and observed.get("effort") not in ("none", "ultra"),
                "unapproved native worker model or effort")


def structural_role_attempts(audit: dict, role_contract_path: Path | None) -> bool:
    """Permit structural accounting of omitted selectors, never authorization."""
    return (role_contract_path is not None and
            audit.get("status") == "structurally-matched" and
            audit.get("role_contract_sha256") is not None and
            bool(audit.get("attempts")) and
            all(attempt.get("selector_state") == "omitted"
                for attempt in audit["attempts"]))


def check_stage_plan(path: Path, sessions: list[dict], parent_id: str,
                     audit: dict | None = None, require_v4_split: bool = False) -> dict:
    plan = read_json(path)
    require(plan.get("schema_version") == 1, "stage plan schema mismatch")
    require(set(plan) == {"schema_version", "stages"}, "stage plan contains unsupported metadata")
    stages = plan.get("stages")
    require(isinstance(stages, list) and len(stages) >= 2, "stage plan needs bounded stages")
    require(all(isinstance(stage, dict) for stage in stages), "stage entries must be objects")
    ids = [stage.get("id") for stage in stages]
    require(all(isinstance(item, str) and item for item in ids) and len(set(ids)) == len(ids),
            "stage IDs must be unique nonempty strings")
    if require_v4_split:
        by_stage = {stage.get("id"): stage for stage in stages}
        require(set(by_stage) == {"hard-kernel", "maintainer-docs", "verify-and-synthesize"},
                "v4 stage plan must declare the bounded hard, documentation, and verification stages")
        hard_stage = by_stage["hard-kernel"]
        doc_stage = by_stage["maintainer-docs"]
        verify_stage = by_stage["verify-and-synthesize"]
        require(isinstance(verify_stage.get("dependencies"), list) and
                all(isinstance(name, str) for name in verify_stage["dependencies"]) and
                len(verify_stage["dependencies"]) == 2 and
                hard_stage.get("dependencies") == [] and doc_stage.get("dependencies") == [] and
                set(verify_stage.get("dependencies") or []) ==
                {"hard-kernel", "maintainer-docs"}, "v4 stage dependencies differ")
        require(hard_stage.get("owner") == "benchmark-hard-kernel-gpt-6-astra-xhigh" and
                doc_stage.get("owner") != hard_stage["owner"] and
                verify_stage.get("owner") != hard_stage["owner"],
                "v4 easy stages must have a separate owner")
        require(isinstance(hard_stage.get("write_scope"), list) and
                all(isinstance(name, str) for name in hard_stage["write_scope"]) and
                set(hard_stage["write_scope"]) == {
                    "internal/oci/ecr/ecr.go", "internal/oci/ecr/ecr_test.go",
                    "internal/oci/options.go", "internal/oci/options_test.go"} and
                doc_stage.get("write_scope") == ["internal/oci/ecr/README.md"] and
                verify_stage.get("write_scope") == [], "v4 stage write scopes differ")
        require(all(type(stage.get("context_budget")) is int and
                    0 < stage["context_budget"] <= 65_536 and
                    isinstance(stage.get("self_check"), str) and stage["self_check"].strip()
                    for stage in stages), "v4 stage context budget or self-check missing")
    child_sessions = [session for session in sessions if session["id"] != parent_id]
    ownership = {}
    dependencies = {stage["id"]: stage.get("dependencies") for stage in stages}
    visited: set[str] = set()
    active: set[str] = set()

    def visit(stage_id: str) -> None:
        require(stage_id not in active, "cyclic stage dependencies")
        if stage_id in visited:
            return
        active.add(stage_id)
        for predecessor in dependencies[stage_id]:
            require(predecessor in dependencies, "unknown stage dependency")
            visit(predecessor)
        active.remove(stage_id)
        visited.add(stage_id)

    for stage_id in ids:
        require(isinstance(dependencies[stage_id], list), "stage dependencies missing")
        visit(stage_id)
    for stage in stages:
        require(isinstance(stage, dict) and
                set(stage) <= {"id", "owner", "dependencies", "write_scope", "acceptance",
                               "packet_sha256", "packet_bytes", "context_budget", "self_check"},
                "stage plan contains unsupported or plaintext packet metadata")
        owner = stage.get("owner")
        scope = stage.get("write_scope")
        require(isinstance(owner, str) and owner, "stage owner missing")
        require(isinstance(stage.get("acceptance"), str) and stage["acceptance"],
                "stage acceptance missing")
        require(isinstance(scope, list), "stage write scope missing")
        for name in scope:
            require(isinstance(name, str) and name and name not in (".", "*", "**", "/") and
                    not Path(name).is_absolute() and ".." not in Path(name).parts,
                    "stage write scope must be bounded repo-relative paths")
            key = name.replace("\\", "/").rstrip("/")
            for previous, previous_owner in ownership.items():
                if owner != previous_owner:
                    require(not (key == previous or key.startswith(previous + "/") or
                                 previous.startswith(key + "/")), "overlapping stage write scopes")
            ownership[key] = owner
        packet_digest = stage.get("packet_sha256")
        packet_bytes = stage.get("packet_bytes")
        if owner != "controller":
            require(isinstance(packet_digest, str) and re.fullmatch(r"[0-9a-f]{64}", packet_digest)
                    and type(packet_bytes) is int and 0 < packet_bytes <= 65_536,
                    "worker stage needs bounded packet commitment")
            matching_children = [session for session in child_sessions
                                 if session["agent_path"].split("/")[-1] == owner.replace("-", "_")]
            require(matching_children, "declared worker stage has no native child")
            require(any(worker_packet_digest(session["session_path"]) == packet_digest
                        for session in matching_children),
                    "worker did not attest the committed packet hash")
            if audit and audit.get("status") == "structurally-matched":
                require(any(item["native_name"] == owner.replace("-", "_") and
                            item["message_sha256"] == packet_digest and
                            item["message_bytes"] == packet_bytes
                            for item in audit["attempts"]),
                        "observed dispatch differs from stage packet commitment")
        else:
            require(packet_digest is None and packet_bytes is None,
                    "controller stage packet commitment must be null")
    hard = [session for session in child_sessions
            if session["model"] == "gpt-6-astra" and session["effort"] == "xhigh"]
    require(hard, "treatment requires an Astra/xhigh hard-kernel child")
    # The native agent path contains the adapted transport name, not the
    # canonical task ID. Match both and leave semantic scope to independent review.
    hard_paths = {session["agent_path"].split("/")[-1] for session in hard}
    require(len(hard_paths) == 1, "Astra must own only one hard-kernel stage")
    hard_path = next(iter(hard_paths))
    matching = [stage for stage in stages if stage["owner"].replace("-", "_") == hard_path]
    require(len(matching) == 1, "Astra session not bound to one declared stage")
    require(len(matching[0]["write_scope"]) < len(ownership),
            "Astra stage appears to own the entire task")
    declared_owners = {stage["owner"].replace("-", "_") for stage in stages
                       if stage["owner"] != "controller"}
    actual_owners = {session["agent_path"].split("/")[-1] for session in child_sessions}
    require(actual_owners == declared_owners, "undeclared or missing native worker stage")
    path_violations = []
    observed_count = 0
    for session in child_sessions:
        owner = session["agent_path"].split("/")[-1]
        declared = next(stage["write_scope"] for stage in stages
                        if stage["owner"].replace("-", "_") == owner)
        for name in observed_patch_writes(session["session_path"]):
            observed_count += 1
            if not any(name == scope.replace("\\", "/").rstrip("/") or
                       name.startswith(scope.replace("\\", "/").rstrip("/") + "/")
                       for scope in declared):
                path_violations.append({"owner": owner, "path": name})
    first_hard_start = min(datetime.fromisoformat(session["start_timestamp"].replace("Z", "+00:00"))
                           for session in hard if session.get("start_timestamp"))
    plan_predates_hard_child = datetime.fromtimestamp(path.stat().st_mtime,
        tz=first_hard_start.tzinfo) <= first_hard_start
    return {"status": "contradicted" if path_violations else "UNKNOWN",
            "native_packet_plaintext_available": False,
            "packet_hash_self_attestation_only": True,
            "plan_predates_hard_child": plan_predates_hard_child,
            "explicit_patch_write_count": observed_count,
            "observed_write_scope_violations": path_violations,
            "hard_kernel_stage_id": matching[0]["id"],
            "hard_kernel_attempts": len(hard),
            "hard_kernel_write_scope": matching[0]["write_scope"],
            "stage_count": len(stages)}


def validate_prompt_evidence(config_path: Path, config: dict, evidence: Path,
                             record: dict, expected_arm: str) -> None:
    """Validate the common task and any versioned treatment authorization."""
    task_hash = config["prompt"]["combined_sha256"]
    expected_prompt = submitted_prompt(config_path, config, expected_arm)
    expected_hash = hashlib.sha256(expected_prompt.encode("utf-8")).hexdigest()
    if expected_hash != task_hash:
        require(record.get("task_prompt_sha256") == task_hash,
                "frozen task prompt hash mismatch")
        require(record.get("treatment_execution_version") ==
                config["treatment_execution"]["version"],
                "treatment execution version mismatch")
    else:
        require(record.get("task_prompt_sha256", task_hash) == task_hash,
                "frozen task prompt hash mismatch")
        require(record.get("treatment_execution_version") is None,
                "unexpected treatment execution authorization")
    require(record.get("prompt_sha256") == expected_hash, "submitted prompt hash mismatch")
    require(sha256(evidence / "prompt.md") == record["prompt_sha256"],
            "saved prompt differs from submitted prompt")


def inspect_arm(config_path: Path, config: dict, evidence: Path, expected_arm: str,
                audit_path: Path | None = None, role_contract_path: Path | None = None) -> dict:
    record = read_json(evidence / "run.json")
    require(record.get("arm") in ({"astra", "baseline"} if expected_arm == "baseline"
                                  else {"router", "treatment"}), "arm label mismatch")
    require(record.get("config_sha256") == sha256(config_path), "configuration hash mismatch")
    require(record.get("initial_tree") == config["fixture"]["initial_tree"],
            "initial tree mismatch")
    require(record.get("initial_diff_sha256") == hashlib.sha256(b"").hexdigest(),
            "initial workspace was dirty")
    validate_prompt_evidence(config_path, config, evidence, record, expected_arm)
    require(sha256(evidence / "codex.jsonl") == record.get("transcript_sha256"),
            "CLI transcript hash mismatch")
    final_exists = (evidence / "final.txt").is_file()
    if record.get("final_sha256"):
        require(final_exists and sha256(evidence / "final.txt") == record["final_sha256"],
                "final answer artifact missing or changed")
    candidate_patch_ref = record.get("candidate_patch")
    if isinstance(candidate_patch_ref, dict):
        patch_path = evidence / candidate_patch_ref["path"]
        require(patch_path.is_file() and sha256(patch_path) == candidate_patch_ref["sha256"] and
                patch_path.stat().st_size == candidate_patch_ref["bytes"],
                "captured candidate patch mismatch")
    require(float(record.get("wall_seconds", 0)) > 0, "invalid end-to-end wall time")
    start = datetime.fromisoformat(record["started_at"])
    end = datetime.fromisoformat(record["ended_at"])
    require(abs((end - start).total_seconds() - record["wall_seconds"]) < 5,
            "wall time differs from run boundaries")
    command = record.get("command") or []
    if record.get("cli_sha256") is not None:
        require(record["cli_sha256"] == config["dispatch_runtime"]["cli_sha256"] and
                record.get("cli_version") == config["dispatch_runtime"]["cli_version"] and
                record.get("cli") == config["dispatch_runtime"]["cli"] and
                "--strict-config" in command,
                "paired CLI or strict configuration pin mismatch")
    require("-m" in command and command[command.index("-m") + 1] == record["model"],
            "CLI primary model was not explicit")
    require(f'model_reasoning_effort="{record["effort"]}"' in command,
            "CLI primary effort was not explicit")
    terminal, cli_spawns = transcript_terminal(evidence / "codex.jsonl")
    completed_claim = (record.get("stop_reason") == "completed" and
                       record.get("exit_code") == 0 and terminal == "turn.completed")
    if record.get("stop_reason") == "completed":
        require(completed_claim, "completed arm lacks successful terminal completion")
    expected = config["arms"]["baseline"] if expected_arm == "baseline" else None
    if expected:
        require((record.get("model"), record.get("effort")) ==
                (expected["model"], expected["effort"]), "Astra baseline selector mismatch")
    else:
        candidates = {(item["model"], item["effort"]) for item in
                      config["arms"]["treatment"]["controller_candidates"]}
        require((record.get("model"), record.get("effort")) in candidates,
                "treatment controller selector was not calibrated")
        require(record.get("plugin_manifest_sha256") ==
                config["plugin"]["installed_manifest_sha256"],
                "treatment plugin manifest mismatch")
        calibration_status = record.get("calibration_status")
        require(calibration_status in (None, "PASSED", "CALIBRATION_FAILED"),
                "unknown treatment calibration status")
        provenance = record.get("controller_provenance")
        if calibration_status == "CALIBRATION_FAILED":
            require(provenance == {"source": "operator-explicit-negative-treatment",
                                   "model": "gpt-6-sol", "effort": "low"},
                    "negative treatment controller provenance mismatch")
        elif provenance is not None:
            require(provenance == {"source": "calibration", "model": record["model"],
                                   "effort": record["effort"]},
                    "treatment controller provenance mismatch")
        calibration_ref = record.get("calibration_ref")
        if isinstance(calibration_ref, dict):
            calibration_path = evidence / calibration_ref["path"]
            require(sha256(calibration_path) == calibration_ref["sha256"],
                    "calibration source hash mismatch")
            if calibration_status == "CALIBRATION_FAILED":
                require(record.get("calibration_sha256") == calibration_ref["sha256"],
                        "negative treatment original calibration hash mismatch")
                require(negative_treatment_controller(calibration_path, config,
                        "gpt-6-sol/low") == (record["model"], record["effort"]),
                        "negative treatment controller mismatch")
            else:
                require(calibrated_controller(calibration_path, config) ==
                        (record["model"], record["effort"]),
                        "controller calibration selection mismatch")
        elif calibration_status == "CALIBRATION_FAILED":
            require(False, "negative treatment calibration evidence missing")
        # Missing ordinary calibration remains a process failure; cost remains counted.
    parent_id = record.get("parent_session_id")
    refs = record.get("session_files") or []
    require(isinstance(refs, list), "session evidence list is invalid")
    sessions = []
    for ref in refs:
        path = evidence / ref["path"]
        require(path.is_file() and sha256(path) == ref["sha256"], "session file hash mismatch")
        meta = session_meta(path)
        require(meta.get("id") == ref["id"], "session file identity mismatch")
        sessions.append({"id": ref["id"], "parent_id": meta.get("parent_thread_id"),
                         "agent_path": meta.get("agent_path") or "",
                         "start_timestamp": meta.get("timestamp"),
                         "session_path": path})
    require(len({session["id"] for session in sessions}) == len(sessions),
            "duplicate session evidence")
    explicit_route = expected_arm == "treatment" and record.get("dispatch_mode") == "explicit"
    meter = SessionMeter(evidence / "session-evidence", parent_id,
                         record["model"], record["effort"], explicit_route) if parent_id else None
    if meter:
        meter.refresh()
    summary = meter.summary() if meter else None
    usage_issues = meter.integrity_issues() if meter else ["parent session ID missing"]
    recorded_usage_superseded = False
    if summary and len(summary["sessions"]) != len(sessions):
        usage_issues.append("session lineage differs from retained files")
    if summary and isinstance(record.get("usage"), dict):
        recorded = record["usage"]
        for key in ("estimated_usd", "estimated_usd_lower_bound",
                    "estimated_usd_upper_bound"):
            actual = summary[key]
            saved = recorded.get(key, recorded.get("estimated_usd"))
            if not ((actual is None and saved is None) or
                    (actual is not None and saved is not None and abs(actual - saved) < 1e-8)):
                if completed_claim:
                    require(False, "recorded cost differs from raw sessions")
                recorded_usage_superseded = True
                break
        if summary["token_classes"] != recorded.get("token_classes"):
            if completed_claim:
                require(False, "recorded token classes differ from raw sessions")
            recorded_usage_superseded = True
    elif completed_claim:
        usage_issues.append("completed run lacks recorded usage")
    cost_status = "complete" if not usage_issues else "partial-or-unknown"
    observed_usd = (summary["estimated_usd_lower_bound"] if summary and meter and
                    not meter.unknown_usage else None)
    if not sessions or not parent_id or parent_id not in {item["id"] for item in sessions}:
        return {"arm": expected_arm, "model": record["model"], "effort": record["effort"],
                "task_prompt_sha256": config["prompt"]["combined_sha256"],
                "submitted_prompt_sha256": record["prompt_sha256"],
                "treatment_execution_version": record.get("treatment_execution_version"),
                "workspace": record["workspace"], "wall_seconds": record["wall_seconds"],
                "cli_version": record["cli_version"], "pricing_source": record["pricing_source"],
                "pricing_tier": record["pricing_tier"], "observed_usd_lower_bound": observed_usd,
                "estimated_usd": None, "estimated_usd_lower_bound": None,
                "estimated_usd_upper_bound": None, "cost_status": "partial-or-unknown",
                "cost_estimate_status": "UNKNOWN",
                "usage_issues": usage_issues, "usage": summary, "outcome": "failed",
                "stop_reason": record.get("stop_reason"), "cli_terminal": terminal,
                "child_count": 0, "retry_count": 0, "failed_child_attempts": 0,
                "scope": {"status": "UNKNOWN"}, "candidate_patch": candidate_patch_ref,
                "calibration_status": record.get("calibration_status"),
                "run_sha256": sha256(evidence / "run.json")}
    by_id = {session["id"]: session for session in sessions}
    parent_ref = next(ref for ref in refs if ref["id"] == parent_id)
    parent_path = evidence / parent_ref["path"]
    spawns = native_spawns(parent_path)
    native_activity = []
    if explicit_route and meter:
        native_activity, activity_issues = native_lineage(parent_path, meter.paths, parent_id)
        usage_issues.extend(activity_issues)
    contexts = router_contexts(parent_path)
    for measured in summary["sessions"]:
        session = by_id[measured["id"]]
        session.update(measured)
        if measured["terminal"] not in ("task_complete", "task_completed", "task_failed",
                                        "task_cancelled", "task_cancel"):
            usage_issues.append(f"session {measured['id']} lacks terminal result")
        if not measured["model"] or not measured["effort"] or measured["calls"] == 0:
            usage_issues.append(f"session {measured['id']} lacks model, effort, or usage")
    parent = by_id[parent_id]
    if completed_claim:
        require(parent["terminal"] in ("task_complete", "task_completed"),
                "completed parent lacks terminal result")
    require((parent["model"], parent["effort"]) == (record["model"], record["effort"]),
            "actual parent selector mismatch")
    children = [session for session in sessions if session["id"] != parent_id]
    process_issues: list[str] = []
    dispatch_audit = {"status": "not-applicable", "issues": [], "attempts": []}
    if expected_arm == "baseline":
        require(not children and not spawns and cli_spawns == 0, "baseline delegated work")
        require(not contexts and all(option in command for option in
                ("plugins", "hooks", "multi_agent")), "baseline router was not disabled")
        scope = {"status": "not-applicable"}
    else:
        if explicit_route:
            preflight = record.get("dispatch_preflight") or {}
            if (preflight.get("status") != "ready" or
                    preflight.get("schema_capture_sha256") !=
                    config["dispatch_runtime"]["schema_capture"]["sha256"] or
                    SELECTOR_FEATURE not in command or
                    any(str(item).startswith("agents.default=") for item in command)):
                process_issues.append("explicit dispatch preflight or launch proof missing")
            if len(native_activity) != len(children):
                process_issues.append("nested native activity did not cover children")
        else:
            process_issues.append("treatment did not use verified explicit dispatch mode")
        if record.get("calibration_status") == "CALIBRATION_FAILED":
            process_issues.append("controller calibration failed (negative treatment)")
        dispatch_audit = reconcile(audit_path, parent_path, parent_id, children,
                                   role_contract_path)
        if dispatch_audit["status"] != "structurally-matched":
            process_issues.extend(dispatch_audit["issues"])
        else:
            process_issues.append("hook output provenance requires independent review")
        if not any(config["plugin"]["version"] in context for context in contexts):
            process_issues.append("installed router hook context not observed")
        if len(spawns) != len(children):
            process_issues.append("native spawn count differs from child sessions")
        require(all(child["parent_id"] == parent["id"] for child in children),
                "unexpected delegation depth")
        role_route = structural_role_attempts(dispatch_audit, role_contract_path)
        if role_route:
            process_issues.append("role runtime authorization unverified")
        require_allowed_selectors([] if role_route else spawns, children, config)
        for child in children:
            native_name = child["agent_path"].split("/")[-1]
            matching_spawns = [spawn for spawn in spawns if spawn["task_name"] == native_name]
            matching_children = [item for item in children if
                                 item["agent_path"].split("/")[-1] == native_name]
            if len(matching_spawns) != len(matching_children):
                process_issues.append("child lacks matching native spawn attempts")
            if not role_route:
                require(all((spawn["model"], spawn["effort"]) ==
                            (child["model"], child["effort"]) for spawn in matching_spawns),
                        "spawn selectors differ from actual child")
            if not all(spawn["fork_turns"] == "none" for spawn in matching_spawns):
                process_issues.append("worker inherited full parent turns")
        stage_ref = record.get("stage_plan")
        scope = {"status": "UNKNOWN", "reason": "stage plan or completed workers unavailable"}
        if isinstance(stage_ref, dict) and children and all(child["terminal"] in
                ("task_complete", "task_completed") for child in children):
            stage_path = evidence / stage_ref["path"]
            require(sha256(stage_path) == stage_ref["sha256"], "stage plan hash mismatch")
            try:
                scope = check_stage_plan(stage_path, sessions, parent["id"], dispatch_audit,
                                         record.get("treatment_execution_version") ==
                                         "lifecycle-v1-selective-stage-plan-v4")
            except ValueError as error:
                process_issues.append(str(error))
                scope = {"status": "UNKNOWN", "reason": str(error)}
        elif completed_claim:
            process_issues.append("completed treatment lacks auditable stage plan and workers")
        if not isinstance(record.get("calibration_ref"), dict):
            process_issues.append("controller calibration evidence missing")
    attempts = {name: sum(spawn["task_name"] == name for spawn in spawns)
                for name in {spawn["task_name"] for spawn in spawns}}
    require(all(count - 1 <= config["limits"]["max_retries_per_stage"]
                for count in attempts.values()), "retry limit exceeded")
    retry_count = sum(count - 1 for count in attempts.values())
    if not isinstance(candidate_patch_ref, dict) and completed_claim:
        process_issues.append("completed arm lacks captured candidate patch")
    if completed_claim and not final_exists:
        process_issues.append("completed arm lacks final answer")
    cost_status = "complete" if not usage_issues else "partial-or-unknown"
    outcome = "completed" if completed_claim and not usage_issues and not process_issues else "failed"
    return {"arm": expected_arm, "model": parent["model"], "effort": parent["effort"],
            "task_prompt_sha256": config["prompt"]["combined_sha256"],
            "submitted_prompt_sha256": record["prompt_sha256"],
            "treatment_execution_version": record.get("treatment_execution_version"),
            "workspace": record["workspace"], "wall_seconds": record["wall_seconds"],
            "cli_version": record["cli_version"],
            "pricing_source": record["pricing_source"],
            "pricing_tier": record["pricing_tier"],
            "estimated_usd": summary["estimated_usd"] if cost_status == "complete" else None,
            "estimated_usd_lower_bound": (summary["estimated_usd_lower_bound"]
                                          if cost_status == "complete" else None),
            "estimated_usd_upper_bound": (summary["estimated_usd_upper_bound"]
                                          if cost_status == "complete" else None),
            "observed_usd_lower_bound": observed_usd,
            "recorded_usage_superseded": recorded_usage_superseded,
            "cost_status": cost_status, "usage_issues": usage_issues,
            "cost_estimate_status": ("UNKNOWN" if cost_status != "complete" else
                                     "exact" if summary["estimated_usd"] is not None else "interval"),
            "outcome": outcome, "process_issues": process_issues,
            "stop_reason": record.get("stop_reason"), "cli_terminal": terminal,
            "candidate_patch": candidate_patch_ref, "usage": summary,
            "child_count": len(children), "retry_count": retry_count,
            "failed_child_attempts": sum(child["terminal"] not in
                                         ("task_complete", "task_completed") for child in children),
            "scope": scope,
            "calibration_status": record.get("calibration_status"),
            "dispatch_audit": dispatch_audit,
            "run_sha256": sha256(evidence / "run.json")}


def validated_grade(path: Path, run_arm: dict, config: dict) -> dict:
    grade = read_json(path)
    require(grade.get("base_tree") == config["fixture"]["initial_tree"],
            "grade replay base mismatch")
    require(grade.get("test_patch_sha256") == config["benchmark"]["oracle_patch_sha256"],
            "grade replay oracle mismatch")
    require(grade.get("oracle_config_sha256") == config["benchmark"]["oracle_config_sha256"],
            "grade oracle config mismatch")
    require(grade.get("grader") == "local-hidden-test-replay-not-official-harbor",
            "grade method mismatch")
    require(type(grade.get("quality_pass")) is bool and
            grade["quality_pass"] == (grade.get("go_test_exit_code") == 0),
            "grade outcome mismatch")
    packages = config["benchmark"]["oracle_packages"]
    require(grade.get("packages") == packages, "grade used unrelated package list")
    command = grade.get("test_command")
    require(isinstance(command, list) and len(command) == 7 + len(packages) and
            command[0] == "wsl" and command[1] == "-d" and command[3] == "--" and
            command[5:] == ["test", "-count=1", *packages],
            "grade command differs from frozen oracle")
    patch_ref = run_arm["candidate_patch"]
    require(isinstance(patch_ref, dict), "grade lacks a captured run patch")
    require(grade.get("candidate_patch_sha256") == patch_ref["sha256"] and
            grade.get("candidate_patch_bytes") == patch_ref["bytes"],
            "grade patch differs from run capture")
    for name, field in (("grader-go.stdout", "stdout_sha256"),
                        ("grader-go.stderr", "stderr_sha256")):
        source = path.parent / name
        require(source.is_file() and sha256(source) == grade.get(field),
                "grade command output hash mismatch")
    return {"status": "graded", "quality_pass": grade["quality_pass"],
            "go_test_exit_code": grade["go_test_exit_code"],
            "packages": packages, "test_command": command,
            "evidence_sha256": sha256(path)}


def paired_cost_ratio(baseline: dict, treatment: dict) -> float | None:
    if (baseline["cost_status"] != "complete" or treatment["cost_status"] != "complete" or
            baseline["estimated_usd"] is None or treatment["estimated_usd"] is None or
            baseline["estimated_usd"] <= 0):
        return None
    return treatment["estimated_usd"] / baseline["estimated_usd"]


def paired_cost_ratio_interval(baseline: dict, treatment: dict) -> list[float] | None:
    if baseline["cost_status"] != "complete" or treatment["cost_status"] != "complete":
        return None
    base_low, base_high = (baseline.get("estimated_usd_lower_bound"),
                           baseline.get("estimated_usd_upper_bound"))
    treat_low, treat_high = (treatment.get("estimated_usd_lower_bound"),
                             treatment.get("estimated_usd_upper_bound"))
    if any(value is None for value in (base_low, base_high, treat_low, treat_high)) or base_low <= 0:
        return None
    return [treat_low / base_high, treat_high / base_low]


def pair_status(both_pass: bool, treatment: dict) -> str:
    if treatment.get("calibration_status") == "CALIBRATION_FAILED":
        return "CALIBRATION_FAILED"
    return "pending-independent-scope-review" if both_pass else "incomplete-or-failed-pair"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--treatment", type=Path, required=True)
    parser.add_argument("--baseline-grade", type=Path)
    parser.add_argument("--treatment-grade", type=Path)
    parser.add_argument("--treatment-audit", type=Path,
                        help="externally captured hook stdout JSONL; provenance requires independent review")
    parser.add_argument("--treatment-role-contract", type=Path,
                        help="hash-bound verified_role_config preflight; calibration and hook provenance require review")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), "output already exists")
    config = read_json(args.config)
    pinned_prompt(args.config, config)
    for model, rates in config["pricing"]["per_million"].items():
        require(tuple(rates) == RATES[model], "price card and collector rates differ")
    baseline = inspect_arm(args.config, config, args.baseline, "baseline")
    treatment = inspect_arm(args.config, config, args.treatment, "treatment",
                            args.treatment_audit, args.treatment_role_contract)
    require(baseline["workspace"] != treatment["workspace"], "arms shared one workspace")
    require(baseline["cli_version"] == treatment["cli_version"],
            "paired arms used different Codex builds")
    require(all(arm["pricing_source"] == config["pricing"]["source"] and
                arm["pricing_tier"] == config["pricing"]["tier"]
                for arm in (baseline, treatment)), "paired pricing basis mismatch")
    require((baseline["model"], baseline["effort"]) == ("gpt-6-astra", "xhigh"),
            "Astra baseline selector missing")
    grades = {}
    for arm, path in (("baseline", args.baseline_grade), ("treatment", args.treatment_grade)):
        if path is None:
            grades[arm] = {"status": "ungraded", "quality_pass": None}
            continue
        run_arm = baseline if arm == "baseline" else treatment
        grades[arm] = validated_grade(path, run_arm, config)
    if all(grade["status"] == "graded" for grade in grades.values()):
        require(grades["baseline"]["test_command"] == grades["treatment"]["test_command"],
                "arms were graded with different commands")
    both_completed = baseline["outcome"] == treatment["outcome"] == "completed"
    both_pass = (both_completed and all(grade["quality_pass"] is True for grade in grades.values()))
    result = {"schema_version": 1,
              "status": pair_status(both_pass, treatment),
              "task_id": config["benchmark"]["task_id"],
              "config_sha256": sha256(args.config), "prompt_sha256": config["prompt"]["combined_sha256"],
              "initial_tree": config["fixture"]["initial_tree"],
              "oracle_patch_sha256": config["benchmark"]["oracle_patch_sha256"],
              "baseline": baseline, "treatment": treatment,
              "grades": grades,
              "both_pass": both_pass,
              "cost_ratio": paired_cost_ratio(baseline, treatment),
              "cost_ratio_interval": paired_cost_ratio_interval(baseline, treatment),
              "wall_ratio": treatment["wall_seconds"] / baseline["wall_seconds"],
              "caveat": "Local replay is not Harbor; redacted dispatch commitments do not prove semantic hard-kernel scope, which remains UNKNOWN pending independent exact-packet review."}
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"status": result["status"], "output": str(args.output)}, indent=2))


if __name__ == "__main__":
    main()
