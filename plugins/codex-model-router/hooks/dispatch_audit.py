#!/usr/bin/env python3
"""Opt-in, stateless dispatch observation for an external trusted hook capture.

This hook never opens an output path. Its bounded stdout contains no packet text.
The benchmark must capture that stdout independently of the agent before using it.
"""
from __future__ import annotations

import hashlib
import base64
import json
import os
import re
import sys
from typing import Any

MAX_INPUT_BYTES = 131_072
MAX_MESSAGE_BYTES = 65_536
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_:/.-]{0,127}\Z")
SELECTOR = re.compile(r"[a-z0-9][a-z0-9_.-]{0,127}\Z")
EVENTS = {"PreToolUse": "pre", "PostToolUse": "post"}
SPAWN_TOOL_NAMES = {"Agent", "spawn_agent", "collaborationspawn_agent"}
CANARY_PREFIX = "CODEX_PACKET_CANARY_V1 "
MAX_PUBLIC_KEY_B64 = 8_192
MAX_CANARY_OUTPUT_BYTES = 131_072
RUN_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


class AuditError(ValueError):
    pass


class CanaryError(AuditError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise AuditError("duplicate JSON key")
        result[key] = value
    return result


def _identity(value: Any, label: str) -> str:
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise AuditError(f"invalid {label}")
    return value


def _selector(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SELECTOR.fullmatch(value):
        raise AuditError(f"invalid {label}")
    return value


def _message_commitment(arguments: Any) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise AuditError("missing tool_input")
    message = arguments.get("message")
    if not isinstance(message, str):
        raise AuditError("missing message")
    raw = message.encode("utf-8")
    if not 0 < len(raw) <= MAX_MESSAGE_BYTES:
        raise AuditError("message exceeds audit bound or is empty")
    return {"message_sha256": hashlib.sha256(raw).hexdigest(), "message_bytes": len(raw)}


def _canary_output(event: dict[str, Any]) -> dict[str, str]:
    """Encrypt only the exact PreToolUse message; stdout contains no plaintext."""
    run_id = os.environ.get("CODEX_MODEL_ROUTER_PACKET_CANARY_RUN_ID")
    encoded_key = os.environ.get("CODEX_MODEL_ROUTER_PACKET_CANARY_PUBLIC_KEY_B64")
    if not isinstance(run_id, str) or RUN_ID.fullmatch(run_id) is None:
        raise CanaryError("invalid_run_id")
    if not isinstance(encoded_key, str) or not 0 < len(encoded_key) <= MAX_PUBLIC_KEY_B64:
        raise CanaryError("invalid_public_key")
    arguments = event.get("tool_input")
    try:
        commitment = _message_commitment(arguments)
    except (AuditError, UnicodeError):
        raise CanaryError("invalid_message") from None
    raw = arguments["message"].encode("utf-8")
    header = {
        "run_id": run_id,
        "phase": "pre",
        "session_id": _identity(event.get("session_id"), "session_id"),
        "turn_id": _identity(event.get("turn_id"), "turn_id"),
        "call_id": _identity(event.get("tool_use_id"), "tool_use_id"),
        **commitment,
    }
    aad = json.dumps(header, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except Exception:
        raise CanaryError("crypto_unavailable") from None
    try:
        public_der = base64.b64decode(encoded_key, validate=True)
        if not 0 < len(public_der) <= 6_144:
            raise ValueError("key bound")
        public_key = serialization.load_der_public_key(public_der)
        if (not isinstance(public_key, rsa.RSAPublicKey) or
                public_key.key_size < 3_072 or public_key.key_size > 8_192 or
                public_key.public_numbers().e != 65_537):
            raise ValueError("key type")
    except Exception:
        raise CanaryError("invalid_public_key") from None
    try:
        content_key = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(12)
        ciphertext = AESGCM(content_key).encrypt(nonce, raw, aad)
        wrapped_key = public_key.encrypt(content_key, padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(), label=None))
    except Exception:
        raise CanaryError("encryption_failed") from None
    envelope = {
        "schema_version": 1,
        "suite": "RSA-OAEP-SHA256+AES-256-GCM",
        "header": header,
        "wrapped_key_b64": base64.b64encode(wrapped_key).decode("ascii"),
        "nonce_b64": base64.b64encode(nonce).decode("ascii"),
        "ciphertext_b64": base64.b64encode(ciphertext).decode("ascii"),
    }
    message = CANARY_PREFIX + json.dumps(envelope, sort_keys=True, separators=(",", ":"))
    if len(message.encode("utf-8")) > MAX_CANARY_OUTPUT_BYTES:
        raise CanaryError("output_too_large")
    return {"systemMessage": message}


def build_record(event: dict[str, Any]) -> dict[str, Any] | None:
    """Return allowlisted facts only; absent runtime fields remain absent/unknown."""
    kind = EVENTS.get(event.get("hook_event_name"))
    if kind is None or event.get("tool_name") not in SPAWN_TOOL_NAMES:
        return None
    record: dict[str, Any] = {
        "schema_version": 1, "kind": kind,
        "tool_name": event["tool_name"],
        "session_id": _identity(event.get("session_id"), "session_id"),
        "turn_id": _identity(event.get("turn_id"), "turn_id"),
        "attempt_id": _identity(event.get("tool_use_id"), "tool_use_id"),
    }
    if kind == "pre":
        arguments = event.get("tool_input")
        commitment = _message_commitment(arguments)
        has_model = "model" in arguments
        has_effort = "reasoning_effort" in arguments
        if has_model != has_effort:
            raise AuditError("partial selector pair")
        selector_state = "explicit" if has_model else "omitted"
        record.update({
            "native_name": _selector(arguments.get("task_name"), "task_name"),
            "selector_state": selector_state,
            "model": _selector(arguments["model"], "model") if has_model else None,
            "effort": _selector(arguments["reasoning_effort"], "reasoning_effort") if has_effort else None,
            "fork_turns": arguments.get("fork_turns"),
            **commitment,
        })
        if record["fork_turns"] not in ("none", "all") and not (
                isinstance(record["fork_turns"], str) and
                record["fork_turns"].isdigit() and 0 < int(record["fork_turns"]) <= 100):
            raise AuditError("invalid fork_turns")
    else:
        response = event.get("tool_response")
        child = None
        if isinstance(response, dict):
            for field in ("child_thread_id", "thread_id", "agent_id"):
                if field in response:
                    child = _identity(response[field], field)
                    break
        record["child_id"] = child
        record["result_observed"] = isinstance(response, dict)
        if event["tool_name"] == "collaborationspawn_agent":
            # This is the post hook's observed input, not proven effective arguments.
            record["post_observed_input"] = _message_commitment(event.get("tool_input"))
    return record


def main() -> int:
    if os.environ.get("CODEX_MODEL_ROUTER_DISPATCH_AUDIT") != "1":
        if os.environ.get("CODEX_MODEL_ROUTER_PACKET_CANARY") == "1":
            print("CODEX_PACKET_CANARY_ERROR:audit_disabled", file=sys.stderr)
            return 2
        print("{}")
        return 0
    canary_flag = os.environ.get("CODEX_MODEL_ROUTER_PACKET_CANARY")
    canary_requested = canary_flag == "1"
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if not 0 < len(raw) <= MAX_INPUT_BYTES:
            raise AuditError("hook input exceeds bound or is empty")
        event = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
        if not isinstance(event, dict):
            raise AuditError("hook input must be an object")
        canary_event = (event.get("hook_event_name") == "PreToolUse" and
                        event.get("tool_name") == "collaborationspawn_agent")
        if (canary_event and canary_flag != "1" and
                (canary_flag not in (None, "") or
                 os.environ.get("CODEX_MODEL_ROUTER_PACKET_CANARY_PUBLIC_KEY_B64") or
                 os.environ.get("CODEX_MODEL_ROUTER_PACKET_CANARY_RUN_ID"))):
            raise CanaryError("invalid_opt_in")
        if canary_requested and canary_event:
            print(json.dumps(_canary_output(event), separators=(",", ":")))
            return 0
        record = build_record(event)
        if record is None:
            print("{}")
        else:
            compact = json.dumps(record, sort_keys=True, separators=(",", ":"))
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": event["hook_event_name"],
                "additionalContext": "CODEX_DISPATCH_AUDIT_V1 " + compact,
            }}, separators=(",", ":")))
    except CanaryError as error:
        print(f"CODEX_PACKET_CANARY_ERROR:{error.code}", file=sys.stderr)
        return 2
    except (AuditError, UnicodeError, ValueError, TypeError, RecursionError):
        # No input fragments or exception detail can escape to hook logs.
        print("CODEX_PACKET_CANARY_ERROR:invalid_hook_input" if canary_requested
              else "dispatch audit input unavailable", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
