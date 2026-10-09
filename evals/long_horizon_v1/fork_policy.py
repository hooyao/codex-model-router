"""Benchmark-only commitments and observation of native spawn context scope.

This observes rollout files after a native call. It cannot intercept or prevent
the native runtime from receiving a disallowed history-bearing call.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any


POLICY = "bounded-fork-v2"
ERROR = "context-policy-failure"
STRICT = "strict-selective"
DIAGNOSTIC = "diagnostic-feasibility"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_sha(value: dict) -> str:
    return _sha(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _canonical_json(value: dict) -> str:
    """Compare saved JSON argument structure without Python's bool/int coercion."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _valid_packet(packet: Any, owner: str) -> bool:
    return (isinstance(packet, dict) and set(packet) == {
        "stage_id", "owner", "dependencies", "write_scope", "context_budget",
        "acceptance_criteria", "self_check", "receipt"} and
        packet["owner"] == owner and
        all(isinstance(packet[key], str) and packet[key]
            for key in ("stage_id", "context_budget", "acceptance_criteria",
                        "self_check", "receipt")) and
        all(isinstance(packet[key], list) and
            all(isinstance(item, str) for item in packet[key])
            for key in ("dependencies", "write_scope")))


def planned_commitment(arguments: dict[str, Any], packet: dict[str, Any]) -> dict:
    """Commit exact planned native arguments without retaining packet text."""
    if not isinstance(arguments, dict) or arguments.get("fork_turns") != "none":
        raise ValueError(f"{ERROR}: planned spawn requires fork_turns=none")
    if (not isinstance(arguments.get("task_name"), str) or
            not arguments["task_name"] or
            not isinstance(arguments.get("message"), str) or
            not arguments["message"] or
            len(arguments["message"].encode("utf-8")) > 65_536):
        raise ValueError(f"{ERROR}: planned spawn identity or message invalid")
    if not _valid_packet(packet, arguments["task_name"]):
        raise ValueError(f"{ERROR}: bounded packet invalid")
    return {"schema_version": 2, "policy": POLICY,
            "task_name": arguments["task_name"],
            "fork_turns": "none", "planned_plaintext_sha256": _sha(arguments["message"]),
            "planned_arguments_sha256": _canonical_sha(arguments),
            "planned_plaintext_bytes": len(arguments["message"].encode("utf-8")),
            "observable_arguments": {key: value for key, value in arguments.items()
                                     if key != "message"},
            "packet_sha256": _canonical_sha(packet),
            "packet": packet}


def preflight_packet_scope(mode: str) -> None:
    """Fail before a paid turn when strict effective-input proof is unavailable.

    The current runtime exposes no trusted plaintext attestation at that boundary.
    Callers cannot promote a planned commitment or an encrypted child join into one.
    """
    if mode not in (STRICT, DIAGNOSTIC):
        raise ValueError(f"{ERROR}: packet scope mode missing or invalid")
    if mode == STRICT:
        raise ValueError(f"{ERROR}: strict mode lacks trusted effective-plaintext attestation")


def _child_initial_input(path: Path, child_id: str, parent_id: str,
                         agent_path: str, parent_turn: str) -> tuple[str, str] | None:
    """Read the first routed child input, with its native identity and turn chain."""
    session_ok = False
    child_turn = None
    context_ok = False
    trigger = False
    try:
        with path.open("rb") as stream:
            for raw in stream:
                if not raw.endswith(b"\n"):
                    break
                event = json.loads(raw)
                payload = event.get("payload")
                if not isinstance(payload, dict):
                    continue
                if event.get("type") == "session_meta":
                    source = payload.get("source", {})
                    subagent = source.get("subagent", {}) if isinstance(source, dict) else {}
                    spawn = subagent.get("thread_spawn", {}) if isinstance(subagent, dict) else {}
                    session_ok = (payload.get("id") == child_id and
                        payload.get("parent_thread_id") == parent_id and
                        payload.get("agent_path") == agent_path and
                        isinstance(spawn, dict) and
                        spawn.get("parent_thread_id") == parent_id and
                        spawn.get("agent_path") == agent_path)
                elif event.get("type") == "event_msg" and payload.get("type") == "task_started":
                    if not session_ok or child_turn is not None or payload.get("root_turn_id") != parent_turn:
                        return None
                    child_turn = payload.get("turn_id")
                elif event.get("type") == "turn_context" and child_turn is not None:
                    context_ok = (payload.get("turn_id") == child_turn and
                                  payload.get("root_turn_id") == parent_turn)
                elif event.get("type") == "inter_agent_communication_metadata":
                    trigger = context_ok and payload.get("trigger_turn") is True
                elif (event.get("type") == "response_item" and
                      payload.get("type") == "agent_message" and
                      payload.get("author") == "/root" and trigger):
                    metadata = payload.get("internal_chat_message_metadata_passthrough")
                    if (payload.get("recipient") != agent_path or
                            not isinstance(metadata, dict) or
                            metadata.get("turn_id") != child_turn):
                        return None
                    content = payload.get("content")
                    if not isinstance(content, list):
                        return None
                    encrypted = [item.get("encrypted_content") for item in content
                        if isinstance(item, dict) and item.get("type") == "encrypted_content"]
                    if len(encrypted) == 1 and isinstance(encrypted[0], str):
                        return "encrypted", encrypted[0]
                    plain = [item.get("text") for item in content
                        if isinstance(item, dict) and item.get("type") == "input_text"]
                    if len(encrypted) == 0 and len(plain) == 1 and isinstance(plain[0], str):
                        return "plaintext", plain[0]
                    return None
    except (OSError, ValueError, TypeError):
        return None
    return None


def observed_spawns(parent_rollout: Path, parent_id: str,
                    *, closed_snapshot: bool = False,
                    retain_message: bool = False) -> tuple[list[dict], list[str]]:
    """Scan every complete raw parent event, including unlinked/late spawns."""
    calls: list[dict] = []
    starts: list[dict] = []
    issues: list[str] = []
    seen_calls: set[str] = set()
    current_session = None
    current_turn = None
    try:
        with parent_rollout.open("rb") as stream:
            for line_no, raw in enumerate(stream, 1):
                if not raw.endswith(b"\n") and not closed_snapshot:
                    break
                try:
                    event = json.loads(raw)
                    payload = event["payload"]
                    if not isinstance(event, dict) or not isinstance(payload, dict):
                        raise ValueError("invalid event")
                except (UnicodeError, ValueError, KeyError, TypeError):
                    issues.append(f"{ERROR}: unreadable raw event at line {line_no}")
                    continue
                kind = event.get("type")
                if kind == "session_meta":
                    current_session = payload.get("id")
                    if current_session != parent_id:
                        issues.append(f"{ERROR}: parent identity differs at line {line_no}")
                elif kind == "turn_context":
                    current_turn = payload.get("turn_id") or event.get("turn_id")
                if kind == "response_item" and payload.get("type") == "function_call" \
                        and payload.get("name") == "spawn_agent":
                    call_id = payload.get("call_id")
                    try:
                        args = json.loads(payload["arguments"])
                    except (ValueError, KeyError, TypeError):
                        args = None
                    if not isinstance(args, dict):
                        issues.append(f"{ERROR}: unreadable native spawn arguments at line {line_no}")
                        continue
                    if args.get("fork_turns") != "none":
                        issues.append(f"{ERROR}: native spawn line {line_no} fork_turns=" +
                                      repr(args.get("fork_turns", "<omitted>")))
                    if (not isinstance(call_id, str) or not call_id or call_id in seen_calls or
                            current_session != parent_id or not isinstance(current_turn, str) or
                            not current_turn):
                        issues.append(f"{ERROR}: duplicate or wrong-parent native spawn at line {line_no}")
                    else:
                        seen_calls.add(call_id)
                    metadata = payload.get("internal_chat_message_metadata_passthrough")
                    if (payload.get("namespace") != "collaboration" or
                            not isinstance(metadata, dict) or
                            metadata.get("turn_id") != current_turn or
                            payload.get("thread_id", parent_id) != parent_id or
                            payload.get("turn_id", current_turn) != current_turn):
                        issues.append(f"{ERROR}: native spawn parent turn identity mismatch at line {line_no}")
                    timestamp = event.get("timestamp")
                    event_ns = None
                    if isinstance(timestamp, str):
                        try:
                            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                            if parsed.tzinfo is not None and parsed.utcoffset() is not None:
                                event_ns = int(parsed.timestamp() * 1_000_000_000)
                        except (ValueError, OverflowError, OSError):
                            pass
                    if event_ns is None:
                        issues.append(f"{ERROR}: native spawn timestamp missing or invalid at line {line_no}")
                    calls.append({"call_id": call_id, "call_line": line_no,
                                  "task_name": args.get("task_name"),
                                  "turn_id": current_turn,
                                  "fork_turns": args.get("fork_turns"),
                                  "native_arguments_sha256": _canonical_sha(args),
                                  "native_message_sha256": _sha(args["message"])
                                  if isinstance(args.get("message"), str) else None,
                                  "native_message_bytes": len(args["message"].encode("utf-8"))
                                  if isinstance(args.get("message"), str) else None,
                                  "observable_arguments": {key: value for key, value in args.items()
                                                           if key != "message"},
                                  "_message": args.get("message"),
                                  "timestamp": timestamp, "time_ns": event_ns})
                if kind == "event_msg":
                    item = (payload.get("item") if payload.get("type") == "item_completed"
                            else payload if payload.get("type") == "sub_agent_activity"
                            else None)
                    if isinstance(item, dict) and item.get("type") in (
                            "SubAgentActivity", "sub_agent_activity") and item.get("kind") == "started":
                        starts.append({"call_id": item.get("id") or item.get("event_id"),
                                       "child_id": item.get("agent_thread_id"),
                                       "agent_path": item.get("agent_path")})
    except OSError as error:
        return [], [f"{ERROR}: parent rollout unreadable: {error}"]
    if closed_snapshot:
        for call in calls:
            matching = [row for row in starts if row["call_id"] == call["call_id"]]
            if (len(matching) != 1 or matching[0]["agent_path"] !=
                    "/root/" + str(call["task_name"]) or
                    not isinstance(matching[0]["child_id"], str)):
                issues.append(f"{ERROR}: native spawn has missing, duplicate, or wrong child: "
                              f"{call['call_id']}")
            else:
                call["child_id"] = matching[0]["child_id"]
                call["agent_path"] = matching[0]["agent_path"]
        if len(starts) != len(calls):
            issues.append(f"{ERROR}: native child starts differ from spawn calls")
    if not retain_message:
        for call in calls:
            call.pop("_message", None)
    return calls, issues


def validate_observed_spawns(parent_rollout: Path, parent_id: str,
                             native_attempts: list[dict] | None = None,
                             *, closed_snapshot: bool = False,
                             plans: Path | None = None,
                             child_rollouts: dict[str, Path] | None = None,
                             mode: str = DIAGNOSTIC) -> tuple[list[dict], list[str]]:
    if mode not in (STRICT, DIAGNOSTIC):
        raise ValueError(f"{ERROR}: packet scope mode missing or invalid")
    calls, issues = observed_spawns(parent_rollout, parent_id,
                                    closed_snapshot=closed_snapshot,
                                    retain_message=True)
    if plans is not None:
        for call in calls:
            name = call["task_name"]
            path = plans / f"{name}.json" if isinstance(name, str) and name and \
                Path(name).name == name else None
            try:
                plan = json.loads(path.read_text(encoding="utf-8")) if path else None
                plan_mtime_ns = path.stat().st_mtime_ns if path else None
            except (OSError, ValueError):
                plan = None
                plan_mtime_ns = None
            source_path = plans / f"{name}.plan.json" if path else None
            try:
                source = json.loads(source_path.read_text(encoding="utf-8")) if source_path else None
                planned_args = source["arguments"]
                source_time = source_path.stat().st_mtime_ns
            except (OSError, ValueError, KeyError, TypeError):
                source, planned_args, source_time = None, None, None
            source_ok = (isinstance(source, dict) and
                set(source) == {"arguments", "packet"} and
                isinstance(planned_args, dict) and
                isinstance(plan, dict) and source["packet"] == plan.get("packet") and
                source_time is not None and call["time_ns"] is not None and
                source_time <= call["time_ns"] + 1_000_000)
            legacy = isinstance(plan, dict) and plan.get("schema_version") == 1
            if legacy:
                commitment_ok = (source_ok and
                    plan.get("policy") == "bounded-fork-v1" and
                    plan.get("arguments_sha256") == _canonical_sha(planned_args) and
                    plan.get("message_sha256") == _sha(planned_args.get("message", "")) and
                    plan.get("message_bytes") == len(planned_args.get("message", "").encode("utf-8")))
                planned_hash = plan.get("message_sha256")
                planned_bytes = plan.get("message_bytes")
                observable = ({key: value for key, value in planned_args.items()
                               if key != "message"} if isinstance(planned_args, dict) else None)
            else:
                commitment_ok = (source_ok and
                    plan.get("schema_version") == 2 and plan.get("policy") == POLICY and
                    isinstance(plan.get("observable_arguments"), dict) and
                    _canonical_json(plan["observable_arguments"]) == _canonical_json(
                        {key: value for key, value in planned_args.items()
                         if key != "message"}) and
                    plan.get("planned_arguments_sha256") == _canonical_sha(planned_args) and
                    isinstance(planned_args.get("message"), str) and
                    plan.get("planned_plaintext_sha256") == _sha(planned_args["message"]) and
                    plan.get("planned_plaintext_bytes") == len(planned_args["message"].encode("utf-8")))
                planned_hash = plan.get("planned_plaintext_sha256") if isinstance(plan, dict) else None
                planned_bytes = plan.get("planned_plaintext_bytes") if isinstance(plan, dict) else None
                observable = plan.get("observable_arguments") if isinstance(plan, dict) else None
            valid = (commitment_ok and plan.get("task_name") == name and
                plan.get("fork_turns") == "none" and
                isinstance(planned_hash, str) and len(planned_hash) == 64 and
                type(planned_bytes) is int and 0 < planned_bytes <= 65_536 and
                observable == call["observable_arguments"] and
                not isinstance(observable.get("message"), str) and
                not isinstance(observable.get("fork_turns"), bool) and
                _valid_packet(plan.get("packet"), name) and
                plan.get("packet_sha256") == _canonical_sha(plan["packet"]))
            if not valid:
                issues.append(f"{ERROR}: planned argument commitment missing or mismatched: {name}")
            call.update({"packet_evidence_schema_version": 2,
                         "planned_plaintext_sha256": planned_hash,
                         "planned_plaintext_bytes": planned_bytes,
                         "native_encrypted_sha256": None,
                         "native_encrypted_bytes": None,
                         "child_encrypted_sha256": None,
                         "child_encrypted_bytes": None,
                         "packet_scope": "PENDING"})
            if valid and (child_rollouts is not None and call.get("child_id") in child_rollouts):
                joined = _child_initial_input(child_rollouts[call["child_id"]],
                    call["child_id"], parent_id, call["agent_path"], call["turn_id"])
                if joined is None:
                    if closed_snapshot:
                        issues.append(f"{ERROR}: child effective input missing or identity mismatch: {name}")
                elif joined[0] == "encrypted":
                    cipher = joined[1]
                    call.update({"native_encrypted_sha256": call["native_message_sha256"],
                                 "native_encrypted_bytes": call["native_message_bytes"],
                                 "child_encrypted_sha256": _sha(cipher),
                                 "child_encrypted_bytes": len(cipher.encode("utf-8")),
                                 "packet_scope": "UNKNOWN"})
                    if call["_message"] != cipher:
                        issues.append(f"{ERROR}: native ciphertext differs from child input: {name}")
                elif call["_message"] == joined[1] and _sha(joined[1]) == planned_hash \
                        and len(joined[1].encode("utf-8")) == planned_bytes:
                    call["packet_scope"] = "VERIFIED"
                else:
                    issues.append(f"{ERROR}: plaintext effective input differs from plan: {name}")
            elif valid and (call["native_message_sha256"] == planned_hash and
                            call["native_message_bytes"] == planned_bytes):
                # Parent raw function-call arguments themselves expose plaintext.
                call["packet_scope"] = "VERIFIED"
            elif valid and closed_snapshot:
                issues.append(f"{ERROR}: opaque native message lacks child input join: {name}")
            if plan_mtime_ns is not None and call["time_ns"] is not None and \
                    plan_mtime_ns > call["time_ns"] + 1_000_000:
                issues.append(f"{ERROR}: planned commitment was not saved before spawn: {name}")
    if native_attempts is not None:
        if not isinstance(native_attempts, list):
            issues.append(f"{ERROR}: native attempt ledger absent")
        else:
            actual = {(row.get("call_id"), row.get("call_line"), row.get("task_name"),
                       row.get("turn_id"), row.get("fork_turns"), row.get("child_id"),
                       row.get("agent_path")) for row in calls}
            try:
                ledger = {(row["call_id"], row["call_line"], row["task_name"],
                           row["turn_id"], row["fork_turns"], row["child_id"],
                           row["agent_path"]) for row in native_attempts}
            except (KeyError, TypeError):
                ledger = set()
            if len(actual) != len(calls) or len(ledger) != len(native_attempts) or actual != ledger:
                issues.append(f"{ERROR}: raw spawns differ from native attempt ledger")
    if mode == STRICT and any(call.get("packet_scope") != "VERIFIED" for call in calls):
        issues.append(f"{ERROR}: strict packet scope unverified")
    for call in calls:
        call.pop("_message", None)
    return calls, issues


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True,
                        help="JSON with arguments and bounded packet")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = json.loads(args.plan.read_text(encoding="utf-8"))
    if not isinstance(source, dict) or set(source) != {"arguments", "packet"}:
        raise ValueError(f"{ERROR}: plan requires arguments and packet")
    receipt = planned_commitment(source["arguments"], source["packet"])
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(receipt, stream, indent=2, sort_keys=True)
        stream.write("\n")


if __name__ == "__main__":
    main()
