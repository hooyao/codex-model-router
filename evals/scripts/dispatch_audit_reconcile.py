"""Fail-closed reconciliation of externally captured, redacted dispatch hook output."""
from __future__ import annotations

import json
import re
import hashlib
import sys
from pathlib import Path
from typing import Any

HOOKS = Path(__file__).resolve().parents[2] / "plugins" / "codex-model-router" / "hooks"
if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))
from dispatch_contract import parse_json, validate_dispatch_structure  # noqa: E402

MAX_AUDIT_BYTES = 2_097_152
MAX_ROLE_CONTRACT_BYTES = 1_048_576
MAX_EVENTS = 1_000
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_:/.-]{0,127}\Z")
PRE_FIELDS = {"schema_version", "kind", "tool_name", "session_id", "turn_id", "attempt_id",
              "native_name", "selector_state", "model", "effort", "fork_turns",
              "message_sha256", "message_bytes"}
POST_FIELDS = {"schema_version", "kind", "session_id", "turn_id", "attempt_id",
               "tool_name", "child_id", "result_observed"}


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate audit JSON key")
        result[key] = value
    return result


def _id(value: Any) -> bool:
    return isinstance(value, str) and ID.fullmatch(value) is not None


def native_event_matches(event: dict[str, Any], payload: dict[str, Any],
                         session_id: str | None, turn_id: str | None,
                         current_session_id: str | None,
                         current_turn_id: str | None,
                         activity: dict[str, Any] | None = None) -> bool:
    """Check every explicit identity, falling back to the current rollout context."""
    rows = (event, payload, activity) if activity is not None else (event, payload)
    sessions = [row[key] for row in rows
                for key in ("thread_id", "session_id") if key in row]
    turns = [row["turn_id"] for row in rows if "turn_id" in row]
    return (all(value == session_id for value in (sessions or [current_session_id])) and
            all(value == turn_id for value in (turns or [current_turn_id])))


def read_audit(path: Path) -> list[dict[str, Any]]:
    """Parse only the marker emitted by the trusted hook, never arbitrary prose."""
    with path.open("rb") as source:
        raw = source.read(MAX_AUDIT_BYTES + 1)
    if not 0 < len(raw) <= MAX_AUDIT_BYTES:
        raise ValueError("dispatch audit source is empty or oversized")
    lines = raw.decode("utf-8").splitlines()
    if not 0 < len(lines) <= MAX_EVENTS:
        raise ValueError("dispatch audit event count exceeds bound")
    records = []
    for line in lines:
        envelope = json.loads(line, object_pairs_hook=_pairs)
        if not isinstance(envelope, dict) or set(envelope) != {"hookSpecificOutput"}:
            raise ValueError("invalid dispatch audit envelope")
        output = envelope["hookSpecificOutput"]
        if not isinstance(output, dict) or set(output) != {"hookEventName", "additionalContext"}:
            raise ValueError("invalid dispatch audit hook output")
        context = output["additionalContext"]
        prefix = "CODEX_DISPATCH_AUDIT_V1 "
        if not isinstance(context, str) or not context.startswith(prefix):
            raise ValueError("dispatch audit marker missing")
        record = json.loads(context[len(prefix):], object_pairs_hook=_pairs)
        if not isinstance(record, dict) or record.get("schema_version") != 1:
            raise ValueError("invalid dispatch audit record")
        kind = record.get("kind")
        expected = (PRE_FIELDS if kind == "pre" else
                    POST_FIELDS | ({"post_observed_input"} if record.get("tool_name") ==
                                   "collaborationspawn_agent" else set()) if kind == "post" else None)
        if expected is None or set(record) != expected:
            raise ValueError("dispatch audit fields differ from allowlist")
        if output["hookEventName"] != ("PreToolUse" if kind == "pre" else "PostToolUse"):
            raise ValueError("dispatch audit event kind mismatch")
        if not all(_id(record[field]) for field in ("session_id", "turn_id", "attempt_id")):
            raise ValueError("invalid dispatch audit identity")
        if record["tool_name"] not in ("Agent", "spawn_agent", "collaborationspawn_agent"):
            raise ValueError("unrelated audit tool")
        if kind == "pre":
            if (not _id(record["native_name"]) or
                    not isinstance(record["message_sha256"], str) or
                    SHA256.fullmatch(record["message_sha256"]) is None or
                    type(record["message_bytes"]) is not int or
                    not 0 < record["message_bytes"] <= 65_536 or
                    not isinstance(record["fork_turns"], str)):
                raise ValueError("invalid dispatch audit pre attempt")
            state = record["selector_state"]
            if state not in ("explicit", "omitted") or (
                    state == "explicit" and not (_id(record["model"]) and _id(record["effort"]))) or (
                    state == "omitted" and (record["model"] is not None or record["effort"] is not None)):
                raise ValueError("invalid dispatch audit selector state")
        elif not ((record["child_id"] is None or _id(record["child_id"])) and
                  type(record["result_observed"]) is bool):
            raise ValueError("invalid dispatch audit post attempt")
        if kind == "post" and record["tool_name"] == "collaborationspawn_agent":
            observed = record["post_observed_input"]
            if (not isinstance(observed, dict) or set(observed) !=
                    {"message_sha256", "message_bytes"} or
                    not isinstance(observed["message_sha256"], str) or
                    SHA256.fullmatch(observed["message_sha256"]) is None or
                    type(observed["message_bytes"]) is not int or
                    not 0 < observed["message_bytes"] <= 65_536):
                raise ValueError("invalid post observed input commitment")
        records.append(record)
    return records


def native_attempts(path: Path) -> tuple[list[dict[str, Any]], bool]:
    """Read parent native calls; unknown call IDs or follow-ups block full audit."""
    calls = []
    unknown_operation = False
    parent_session_id = None
    current_turn_id = None
    outputs: dict[str, dict[str, Any]] = {}
    activities: dict[str, tuple[str, str]] = {}
    call_contexts: dict[str, tuple[str | None, str | None]] = {}
    completed_children: set[str] = set()
    completed_ids: set[str] = set()
    seen_call_ids: set[str] = set()
    with path.open(encoding="utf-8") as source:
        for line in source:
            event = json.loads(line, object_pairs_hook=_pairs)
            if not isinstance(event, dict):
                raise ValueError("native parent event is not an object")
            payload = event.get("payload")
            if not isinstance(payload, dict):
                raise ValueError("native parent payload is not an object")
            if event.get("type") == "session_meta":
                next_session_id = payload.get("id")
                if parent_session_id is not None and next_session_id != parent_session_id:
                    raise ValueError("native parent session context changed")
                parent_session_id = next_session_id
            if event.get("type") == "turn_context":
                current_turn_id = payload.get("turn_id") or event.get("turn_id")
            nested = payload.get("item") if payload.get("type") == "item_completed" else None
            activity = (nested if isinstance(nested, dict) and nested.get("type") == "SubAgentActivity"
                        else payload if payload.get("type") == "sub_agent_activity" else None)
            if event.get("type") == "event_msg" and activity:
                call_id = activity.get("id") or activity.get("event_id")
                child_id = activity.get("agent_thread_id")
                agent_path = activity.get("agent_path")
                if (not _id(call_id) or not _id(child_id) or
                        not isinstance(agent_path, str)):
                    raise ValueError("native subagent activity is invalid")
                if activity.get("kind") == "started":
                    if (call_id not in seen_call_ids or call_id in activities or
                            call_id in completed_ids):
                        raise ValueError("native subagent start is invalid or out of order")
                    origin_session, origin_turn = call_contexts[call_id]
                    if not native_event_matches(event, payload, origin_session, origin_turn,
                                                parent_session_id, current_turn_id, activity):
                        raise ValueError("native subagent start differs from spawn context")
                    activities[call_id] = (child_id, agent_path)
                elif activity.get("kind") == "completed":
                    starts = [start_id for start_id, start in activities.items()
                              if start == (child_id, agent_path)]
                    if (len(starts) != 1 or
                            child_id in completed_children or call_id in activities or
                            call_id in completed_ids or call_id in seen_call_ids):
                        raise ValueError("native subagent completion has no unique start")
                    origin_session, origin_turn = call_contexts[starts[0]]
                    if not native_event_matches(event, payload, origin_session, origin_turn,
                                                parent_session_id, current_turn_id, activity):
                        raise ValueError("native subagent completion differs from spawn context")
                    completed_children.add(child_id)
                    completed_ids.add(call_id)
                else:
                    raise ValueError("unsupported native subagent activity")
            if event.get("type") != "response_item" or payload.get("type") not in (
                    "function_call", "custom_tool_call"):
                if event.get("type") == "response_item" and payload.get("type") == "function_call_output":
                    call_id = payload.get("call_id") or payload.get("id")
                    if call_id not in seen_call_ids:
                        continue
                    if call_id in outputs:
                        raise ValueError("native spawn result is duplicated")
                    output = payload.get("output")
                    if isinstance(output, str) and len(output) <= 65_536:
                        try:
                            output = json.loads(output, object_pairs_hook=_pairs)
                        except json.JSONDecodeError:
                            output = None
                    child_id = None
                    result_path = None
                    if isinstance(output, dict):
                        for field in ("child_thread_id", "thread_id", "agent_id"):
                            if field in output:
                                child_id = output[field]
                                break
                        result_path = output.get("task_name")
                        if isinstance(result_path, str):
                            activity = activities.get(call_id)
                            if activity and result_path != activity[1]:
                                raise ValueError("native spawn result path differs from started activity")
                    outputs[call_id] = {"child_id": child_id if _id(child_id) else None,
                                        "task_name": result_path,
                                        "v2_task_name_only": isinstance(output, dict) and
                                                             set(output) == {"task_name"}}
                continue
            name = payload.get("name")
            if name in ("followup_task", "send_message", "send_message_to_thread"):
                unknown_operation = True
            if payload.get("type") != "function_call" or name != "spawn_agent":
                continue
            if payload.get("namespace") not in (None, "collaboration"):
                raise ValueError("native spawn namespace differs from collaboration")
            arguments = json.loads(payload["arguments"], object_pairs_hook=_pairs)
            if not isinstance(arguments, dict):
                raise ValueError("native spawn arguments invalid")
            message = arguments.get("message")
            if not isinstance(message, str) or not 0 < len(message.encode("utf-8")) <= 65_536:
                raise ValueError("native spawn message unavailable or oversized")
            message_bytes = message.encode("utf-8")
            call_id = payload.get("call_id") or payload.get("id")
            if _id(call_id):
                if call_id in seen_call_ids:
                    raise ValueError("duplicate native attempt ID")
                seen_call_ids.add(call_id)
            if not native_event_matches(event, payload, parent_session_id, current_turn_id,
                                        parent_session_id, current_turn_id):
                raise ValueError("native call differs from parent context")
            if _id(call_id):
                call_contexts[call_id] = (parent_session_id, current_turn_id)
            calls.append({"attempt_id": call_id, "session_id": parent_session_id,
                          "turn_id": current_turn_id,
                          "tool_name": name,
                          "native_name": arguments.get("task_name"),
                          "model": arguments.get("model"),
                          "effort": arguments.get("reasoning_effort"),
                          "selector_fields": sorted({"model", "reasoning_effort"} & set(arguments)),
                          "agent_type_present": "agent_type" in arguments,
                          "message_sha256": hashlib.sha256(message_bytes).hexdigest(),
                          "message_bytes": len(message_bytes),
                          "fork_turns": arguments.get("fork_turns")})
    for call in calls:
        activity = activities.get(call["attempt_id"])
        output = outputs.get(call["attempt_id"], {})
        output_child = output.get("child_id")
        if activity and output_child and activity[0] != output_child:
            raise ValueError("native child identities disagree")
        call["child_id"] = activity[0] if activity else output_child
        call["agent_path"] = activity[1] if activity else None
        call["activity_observed"] = activity is not None
        # V2's successful result can contain only task_name. Bind it to the
        # started activity's exact call ID and path before using its child ID.
        call["v2_result_matched"] = bool(activity and output.get("v2_task_name_only") and
                                          output.get("task_name") == activity[1])
    return calls, unknown_operation


def load_role_contract(path: Path) -> tuple[dict[str, Any], str]:
    with path.open("rb") as source:
        raw = source.read(MAX_ROLE_CONTRACT_BYTES + 1)
    if not 0 < len(raw) <= MAX_ROLE_CONTRACT_BYTES:
        raise ValueError("role contract is empty or oversized")
    contract = parse_json(raw.decode("utf-8"), str(path))
    validate_dispatch_structure(contract, path.resolve().parent)
    if contract["selection"]["mode"] != "verified_role_config":
        raise ValueError("role contract has a different selection mode")
    return contract, hashlib.sha256(raw).hexdigest()


def reconcile(path: Path | None, parent_path: Path, parent_id: str,
              children: list[dict[str, Any]], role_contract_path: Path | None = None) -> dict[str, Any]:
    """A match proves transport facts only; packet semantics need separate review."""
    if path is None:
        return {"status": "UNKNOWN", "provenance": "UNKNOWN",
                "issues": ["trusted hook output unavailable"], "attempts": []}
    try:
        records = read_audit(path)
        native, followups = native_attempts(parent_path)
        role_contract, role_digest = (load_role_contract(role_contract_path)
                                      if role_contract_path is not None else (None, None))
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, RecursionError):
        return {"status": "UNKNOWN", "provenance": "UNKNOWN",
                "issues": ["audit evidence is malformed or unavailable"], "attempts": []}
    issues = []
    if followups:
        issues.append("follow-up or retry operation lacks attempt audit")
    pre: dict[str, dict] = {}
    post: dict[str, dict] = {}
    for record in records:
        bucket = pre if record["kind"] == "pre" else post
        attempt_id = record["attempt_id"]
        if attempt_id in bucket:
            issues.append("duplicate audit attempt ID")
        bucket[attempt_id] = record
        if record["session_id"] != parent_id:
            issues.append("audit session differs from parent")
        if record["kind"] == "post" and attempt_id not in pre:
            issues.append("post attempt precedes pre attempt")
    if len(pre) != len(native) or len(post) != len(native):
        issues.append("pre/post count differs from native spawn count")
    if not isinstance(children, list) or any(
            not isinstance(child, dict) or not _id(child.get("id")) or
            not isinstance(child.get("agent_path"), str) or
            not isinstance(child.get("turns"), list) or
            any(not isinstance(turn, dict) for turn in child["turns"])
            for child in children):
        return {"status": "UNKNOWN", "provenance": "UNKNOWN",
                "issues": ["child session evidence is malformed"], "attempts": []}
    child_by_id = {child["id"]: child for child in children}
    if len(child_by_id) != len(children):
        issues.append("duplicate child session ID")
    seen_children = set()
    for call in native:
        attempt_id = call["attempt_id"]
        if not _id(attempt_id):
            issues.append("native attempt ID unavailable")
            continue
        before, after = pre.get(attempt_id), post.get(attempt_id)
        if before is None or after is None:
            issues.append("native attempt lacks matching pre/post event")
            continue
        if not _id(call["session_id"]) or call["session_id"] != parent_id:
            issues.append("native parent session context unavailable or mismatched")
        if not _id(call["turn_id"]) or before["turn_id"] != call["turn_id"] or \
                after["turn_id"] != call["turn_id"]:
            issues.append("pre/post turn differs from native parent context")
        if before["tool_name"] != after["tool_name"]:
            issues.append("pre/post tool name mismatch")
        v2_result_matched = (before["tool_name"] == "collaborationspawn_agent" and
                             call["v2_result_matched"])
        if after["result_observed"] is not True and not v2_result_matched:
            issues.append("post attempt lacks observed result")
        if any(before[field] != call[field] for field in ("native_name", "model", "effort", "fork_turns")):
            issues.append("pre attempt differs from native call")
        if any(before[field] != call[field] for field in ("message_sha256", "message_bytes")):
            issues.append("pre transport commitment differs from native call")
        if "post_observed_input" in after and any(
                after["post_observed_input"][field] != before[field]
                for field in ("message_sha256", "message_bytes")):
            issues.append("post transport commitment differs from pre attempt")
        role_mode = before["selector_state"] == "omitted"
        if role_contract is not None and not role_mode:
            issues.append("verified role contract requires omitted native selectors")
        if role_mode:
            if role_contract is None:
                issues.append("omitted selectors lack verified role contract")
            else:
                planned = role_contract["native_dispatch"]["planned_arguments"]
                if (before["tool_name"] != role_contract["native_dispatch"]["tool"] or
                        call["tool_name"] not in ("spawn_agent", "collaborationspawn_agent") or
                        before["native_name"] != role_contract["native_dispatch"]["native_name"] or
                        call["selector_fields"] or call["agent_type_present"] or
                        before["fork_turns"] != "none" or
                        before["message_sha256"] != planned["message_sha256"] or
                        before["message_bytes"] != planned["message_bytes"]):
                    issues.append("omitted-selector attempt differs from verified role commitment")
                if not call["activity_observed"]:
                    issues.append("role attempt lacks native subagent activity")
        elif call["selector_fields"] != ["model", "reasoning_effort"]:
            issues.append("explicit native selectors are missing")
        child_id = after["child_id"] or (call["child_id"] if role_mode or v2_result_matched else None)
        if call["child_id"] is None:
            issues.append("native spawn result lacks child identity")
        elif after["child_id"] is not None and after["child_id"] != call["child_id"]:
            issues.append("post child identity differs from native result")
        if child_id is None or child_id not in child_by_id:
            issues.append("post attempt lacks observed child identity")
            continue
        if child_id in seen_children:
            issues.append("child identity reused by multiple attempts")
        seen_children.add(child_id)
        child = child_by_id[child_id]
        if child.get("parent_id") != parent_id or child.get("agent_path", "").split("/")[-1] != before["native_name"]:
            issues.append("child lineage or native name mismatch")
        if call["agent_path"] is not None and call["agent_path"] != child.get("agent_path"):
            issues.append("native activity path differs from child session")
        turns = child.get("turns") or []
        expected_model = role_contract["selection"]["model"] if role_mode and role_contract else before["model"]
        expected_effort = role_contract["selection"]["reasoning_effort"] if role_mode and role_contract else before["effort"]
        if not turns or any((turn.get("model"), turn.get("effort")) !=
                            (expected_model, expected_effort) for turn in turns):
            issues.append("child turn model/effort missing or switched")
        if len(turns) != 1:
            issues.append("child follow-up turn lacks attempt audit")
    if len(seen_children) != len(children):
        issues.append("child session lacks unique audited attempt")
    native_by_id = {call["attempt_id"]: call for call in native if _id(call["attempt_id"])}
    attempts = []
    for attempt_id, before in pre.items():
        after = post.get(attempt_id)
        native_call = native_by_id.get(attempt_id)
        hook_child = after["child_id"] if after else None
        native_child = native_call["child_id"] if native_call else None
        child_id = hook_child or native_child
        source = ("hook_post" if hook_child else
                  "native_sub_agent_activity" if native_call and native_call["activity_observed"] and native_child else
                  "native_function_call_output" if native_child else None)
        native_result = bool(before["tool_name"] == "collaborationspawn_agent" and
                             native_call and native_call["v2_result_matched"])
        receipt = before | {"child_id": child_id, "child_id_source": source,
                            "result_source": ("hook_post" if after and after["result_observed"] else
                                              "native_result_path_and_activity" if native_result else None)}
        if after and "post_observed_input" in after:
            receipt["post_observed_input"] = after["post_observed_input"]
        attempts.append(receipt)
    return {"status": "structurally-matched" if not issues else "UNKNOWN",
            "provenance": "UNKNOWN",
            "role_contract_sha256": role_digest,
            "role_authorization": "UNVERIFIED" if role_contract is not None else None,
            "issues": sorted(set(issues)),
            "attempts": attempts}
