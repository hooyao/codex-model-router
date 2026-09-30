"""Offline exact-packet canary checks; run with the bundled crypto Python."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


HOOK = Path(__file__).resolve().parents[1] / "hooks" / "dispatch_audit.py"
PREFIX = "CODEX_PACKET_CANARY_V1 "
SECRET = "private canary sentinel 雪\r\nline two"


class DispatchCanaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            from cryptography.exceptions import InvalidTag
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding, rsa
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError:
            raise unittest.SkipTest("cryptography is only needed for opt-in canary tests") from None
        cls.InvalidTag = InvalidTag
        cls.hashes = hashes
        cls.serialization = serialization
        cls.padding = padding
        cls.rsa = rsa
        cls.AESGCM = AESGCM
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        public_der = cls.private_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        cls.canary_env = {**os.environ, "CODEX_MODEL_ROUTER_DISPATCH_AUDIT": "1",
                          "CODEX_MODEL_ROUTER_PACKET_CANARY": "1",
                          "CODEX_MODEL_ROUTER_PACKET_CANARY_RUN_ID": "synthetic-run-1",
                          "CODEX_MODEL_ROUTER_PACKET_CANARY_PUBLIC_KEY_B64":
                              base64.b64encode(public_der).decode("ascii")}

    def event(self, phase: str = "PreToolUse", message: str = SECRET) -> dict:
        value = {"session_id": "parent-synthetic", "turn_id": "turn-synthetic",
                 "hook_event_name": phase, "tool_name": "collaborationspawn_agent",
                 "tool_use_id": "call-synthetic", "tool_input": {
                     "task_name": "capability_probe", "fork_turns": "none", "message": message}}
        if phase == "PostToolUse":
            value["tool_response"] = {"task_name": "/root/capability_probe"}
        return value

    def invoke(self, value: dict | str, env: dict | None = None) -> subprocess.CompletedProcess:
        raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        result = subprocess.run([sys.executable, str(HOOK)], input=raw.encode("utf-8"),
                                capture_output=True, env=env or self.canary_env)
        return subprocess.CompletedProcess(result.args, result.returncode,
                                           result.stdout.decode("utf-8"),
                                           result.stderr.decode("utf-8"))

    def envelope(self) -> dict:
        result = self.invoke(self.event())
        self.assertEqual(0, result.returncode)
        self.assertEqual("", result.stderr)
        self.assertNotIn(SECRET, result.stdout)
        output = json.loads(result.stdout)
        self.assertEqual({"systemMessage"}, set(output))
        self.assertTrue(output["systemMessage"].startswith(PREFIX))
        return json.loads(output["systemMessage"][len(PREFIX):])

    def decrypt(self, envelope: dict, private_key=None) -> bytes:
        private_key = private_key or self.private_key
        header = envelope["header"]
        aad = json.dumps(header, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True).encode("ascii")
        key = private_key.decrypt(base64.b64decode(envelope["wrapped_key_b64"]),
            self.padding.OAEP(mgf=self.padding.MGF1(algorithm=self.hashes.SHA256()),
                              algorithm=self.hashes.SHA256(), label=None))
        return self.AESGCM(key).decrypt(base64.b64decode(envelope["nonce_b64"]),
                                        base64.b64decode(envelope["ciphertext_b64"]), aad)

    def test_unicode_crlf_exact_roundtrip_and_no_plaintext_output(self) -> None:
        envelope = self.envelope()
        self.assertEqual({"schema_version", "suite", "header", "wrapped_key_b64",
                          "nonce_b64", "ciphertext_b64"}, set(envelope))
        self.assertEqual("RSA-OAEP-SHA256+AES-256-GCM", envelope["suite"])
        self.assertEqual(SECRET.encode("utf-8"), self.decrypt(envelope))
        self.assertEqual(hashlib.sha256(SECRET.encode("utf-8")).hexdigest(),
                         envelope["header"]["message_sha256"])
        self.assertEqual(len(SECRET.encode("utf-8")), envelope["header"]["message_bytes"])
        self.assertEqual({"run_id": "synthetic-run-1", "phase": "pre",
                          "session_id": "parent-synthetic", "turn_id": "turn-synthetic",
                          "call_id": "call-synthetic", "message_sha256":
                          envelope["header"]["message_sha256"],
                          "message_bytes": envelope["header"]["message_bytes"]},
                         envelope["header"])

    def test_tamper_wrong_key_run_and_call_fail_authentication(self) -> None:
        envelope = self.envelope()
        other = self.rsa.generate_private_key(public_exponent=65537, key_size=3072)
        with self.assertRaises(ValueError):
            self.decrypt(envelope, other)
        for field in ("run_id", "call_id"):
            altered = copy.deepcopy(envelope)
            altered["header"][field] = "different"
            with self.assertRaises(self.InvalidTag):
                self.decrypt(altered)
        altered = copy.deepcopy(envelope)
        ciphertext = bytearray(base64.b64decode(altered["ciphertext_b64"]))
        ciphertext[-1] ^= 1
        altered["ciphertext_b64"] = base64.b64encode(ciphertext).decode("ascii")
        with self.assertRaises(self.InvalidTag):
            self.decrypt(altered)

    def test_bounds_duplicate_keys_and_bad_key_fail_without_secret(self) -> None:
        cases = ((self.event(message=SECRET + "X" * 65_537), self.canary_env,
                  "invalid_message"),
                 ('{"hook_event_name":"PreToolUse","hook_event_name":"PreToolUse"}',
                  self.canary_env, "invalid_hook_input"),
                 (self.event(), {**self.canary_env,
                                 "CODEX_MODEL_ROUTER_PACKET_CANARY_PUBLIC_KEY_B64": "bad"},
                  "invalid_public_key"),
                 (self.event(), {**self.canary_env,
                                 "CODEX_MODEL_ROUTER_PACKET_CANARY_RUN_ID": "bad/run"},
                  "invalid_run_id"),
                 (self.event(), {**self.canary_env,
                                 "CODEX_MODEL_ROUTER_DISPATCH_AUDIT": "0"},
                  "audit_disabled"))
        for payload, environment, code in cases:
            with self.subTest(code=code):
                result = self.invoke(payload, environment)
                self.assertEqual(2, result.returncode)
                self.assertEqual("", result.stdout)
                self.assertEqual(f"CODEX_PACKET_CANARY_ERROR:{code}", result.stderr.strip())
                self.assertNotIn(SECRET, result.stdout + result.stderr)

    def test_post_remains_compact_and_non_canary(self) -> None:
        result = self.invoke(self.event("PostToolUse"))
        self.assertEqual(0, result.returncode)
        self.assertNotIn(SECRET, result.stdout)
        output = json.loads(result.stdout)
        self.assertEqual({"hookSpecificOutput"}, set(output))
        self.assertIn("CODEX_DISPATCH_AUDIT_V1 ",
                      output["hookSpecificOutput"]["additionalContext"])
        self.assertNotIn("systemMessage", output)


if __name__ == "__main__":
    unittest.main()
