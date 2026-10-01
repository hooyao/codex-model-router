"""Evaluator-owned staged admission and deterministic reveal state."""
from __future__ import annotations

import json
from pathlib import Path
import re
import time
from typing import Callable

from .common import HERE, START_TREE, TASK_ID, capture_patch, copy_assets, file_sha, manifest, sha


class ProtocolError(ValueError):
    pass


CHECK_COMMANDS = {
    "diff": "git diff --check",
    "public": "go test -vet=off -overlay .benchmark/public-overlay.json -count=1 -timeout=90s ./internal/storage/fs/oci",
    "packages": "go test -count=1 -timeout=90s ./internal/oci/... ./internal/storage/fs/oci ./internal/storage/fs/store",
}
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _field(condition: bool, name: str, expected: str) -> None:
    if not condition:
        raise ProtocolError(f"checkpoint.{name}: expected {expected}")


def checkpoint(path: Path, expected_round: int, actual_patch_hash: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ProtocolError("checkpoint.json: missing or invalid JSON") from error
    _field(isinstance(value, dict), "root", "JSON object")
    _field(type(value.get("schema_version")) is int and value["schema_version"] == 1,
           "schema_version", "integer 1")
    _field(type(value.get("round")) is int and value["round"] == expected_round,
           "round", f"integer {expected_round}")
    _field(value.get("requested_action") == "submit", "requested_action", "submit")
    _field(value.get("candidate_patch_sha256") == actual_patch_hash,
           "candidate_patch_sha256", "the current frozen-seed-relative patch SHA-256")
    _field(isinstance(value.get("checks"), list) and bool(value["checks"]),
           "checks", "nonempty list")
    seen = set()
    workspace = path.parent.parent
    for index, item in enumerate(value["checks"]):
        prefix = f"checks[{index}]"
        _field(isinstance(item, dict), prefix, "object")
        label = next((name for name, command in CHECK_COMMANDS.items()
                      if item.get("command") == command), None)
        _field(label is not None and label not in seen, f"{prefix}.command",
               "distinct arm-visible allowlisted command")
        seen.add(label)
        _field(type(item.get("exit_code")) is int, f"{prefix}.exit_code", "integer")
        for stream in ("stdout", "stderr"):
            expected_path = f".benchmark/checks/round{expected_round}/{label}.{stream}"
            name = f"{prefix}.{stream}_path"
            _field(item.get(f"{stream}_path") == expected_path, name, expected_path)
            output_path = workspace / expected_path
            _field(output_path.is_file() and not any(parent.is_symlink()
                   for parent in (output_path, *output_path.parents)
                   if parent == workspace or workspace in parent.parents),
                   name, "regular arm-local check output")
            digest = item.get(f"{stream}_sha256")
            _field(isinstance(digest, str) and SHA256.fullmatch(digest) is not None,
                   f"{prefix}.{stream}_sha256", "64 lowercase hexadecimal characters")
            _field(file_sha(output_path) == digest, f"{prefix}.{stream}_sha256",
                   "SHA-256 of recorded output bytes")
        status_relative = f".benchmark/checks/round{expected_round}/{label}.status.json"
        _field(item.get("status_path") == status_relative, f"{prefix}.status_path",
               status_relative)
        status_path = workspace / status_relative
        _field(status_path.is_file() and not any(parent.is_symlink()
               for parent in (status_path, *status_path.parents)
               if parent == workspace or workspace in parent.parents),
               f"{prefix}.status_path", "regular arm-local status record")
        status_digest = item.get("status_sha256")
        _field(isinstance(status_digest, str) and SHA256.fullmatch(status_digest) is not None,
               f"{prefix}.status_sha256", "64 lowercase hexadecimal characters")
        _field(file_sha(status_path) == status_digest, f"{prefix}.status_sha256",
               "SHA-256 of recorded status bytes")
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ProtocolError(f"checkpoint.{prefix}.status_path: invalid JSON") from error
        _field(isinstance(status, dict) and
               status == {key: item[key] for key in ("command", "exit_code",
                                                    "stdout_sha256", "stderr_sha256")},
               f"{prefix}.exit_code", "integer matching recorded process status")
    _field(isinstance(value.get("summary"), str) and
           0 < len(value["summary"].strip()) <= 4000,
           "summary", "nonempty string of at most 4000 characters")
    return value


class StageMachine:
    """One parent lineage, one repair per round, append-only hash-linked events."""

    def __init__(self, workspace: Path, arm: str, thread_id: str,
                 public_gate: Callable[[bytes, int, int], dict], base_tree: str = START_TREE):
        manifest()
        if arm not in ("baseline", "treatment") or not thread_id:
            raise ProtocolError("invalid-arm-or-thread")
        self.workspace = workspace
        self.arm = arm
        self.thread_id = thread_id
        self.public_gate = public_gate
        self.base_tree = base_tree
        self.round = 0
        self.repair = 0
        self.complete = False
        self.events: list[dict] = []
        self._previous = "0" * 64

    def event(self, kind: str, **data) -> dict:
        payload = {"seq": len(self.events), "time_ns": time.time_ns(), "kind": kind,
                   "arm": self.arm, "round": self.round, "thread_id": self.thread_id,
                   "previous_sha256": self._previous, **data}
        digest = sha(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
        payload["sha256"] = digest
        self._previous = digest
        self.events.append(payload)
        return payload

    def submit(self, claimed_thread_id: str, checkpoint_path: Path) -> dict:
        if self.complete:
            raise ProtocolError("duplicate-final-submission")
        if claimed_thread_id != self.thread_id:
            raise ProtocolError("parent-lineage-changed")
        patch = capture_patch(self.workspace, self.base_tree)
        patch_hash = sha(patch)
        try:
            authored = checkpoint(checkpoint_path, self.round, patch_hash)
        except ProtocolError as error:
            self.event("protocol_failure", reason=str(error), patch_sha256=patch_hash)
            return self._failure(str(error), patch_hash)
        self.event("submit", patch_sha256=patch_hash, patch_bytes=len(patch),
                   checkpoint_sha256=file_sha(checkpoint_path), repair=self.repair)
        receipt = self.public_gate(patch, self.round, self.repair)
        if (not isinstance(receipt, dict) or receipt.get("round") != self.round or
                receipt.get("candidate_patch_sha256") != patch_hash or
                type(receipt.get("behavior_pass")) is not bool):
            raise ProtocolError("unbound-public-gate-receipt")
        self.event("gate", receipt_sha256=sha(json.dumps(receipt, sort_keys=True).encode()),
                   behavior_pass=receipt["behavior_pass"], patch_sha256=patch_hash)
        if not receipt["behavior_pass"]:
            return self._failure("public-gate-failed", patch_hash, receipt)
        if self.round == 2:
            self.complete = True
            self.event("accepted-final", patch_sha256=patch_hash)
            return {"action": "final", "receipt": receipt, "patch": patch}
        self.round += 1
        self.repair = 0
        revealed = copy_assets(self.round, self.workspace)
        self.event("reveal", assets=revealed)
        return {"action": "continue", "receipt": receipt, "revealed": revealed,
                "report": (self.workspace / ".benchmark" / f"round{self.round}.md").read_text(encoding="utf-8")}

    def _failure(self, reason: str, patch_hash: str, receipt: dict | None = None) -> dict:
        if self.repair >= 1:
            self.event("round-exhausted", reason=reason)
            self.complete = True
            return {"action": "stop", "reason": reason, "receipt": receipt}
        self.repair += 1
        self.event("repair-allowed", reason=reason, patch_sha256=patch_hash)
        feedback = []
        if receipt is not None:
            for check in receipt.get("checks", []):
                full_stdout = str(check.get("stdout_tail", ""))
                stdout = full_stdout[-1500:]
                stderr = str(check.get("stderr_tail", ""))[-1500:]
                cases = sorted(set(re.findall(r"--- FAIL: (\S+)", full_stdout)))
                feedback.append({"exit_code": check.get("exit_code"),
                                 "failed_case_ids": cases,
                                 "stdout_tail": stdout, "stderr_tail": stderr})
        message = f"Round {self.round} {reason}. Repair allowance: final attempt."
        if reason.startswith("checkpoint."):
            message += (f"\nRegenerate this round's checkpoint with "
                        f"`python .benchmark/submit_checkpoint.py --round {self.round} "
                        f"--summary \"<work completed and limitations>\"`. "
                        "It runs visible checks, records integer exit codes and output hashes, "
                        "and binds the current candidate patch.")
        if feedback:
            message += "\nPublic gate failures:\n" + json.dumps(feedback, sort_keys=True)
        return {"action": "repair", "reason": reason, "receipt": receipt,
                "message": message}

    def receipt(self) -> dict:
        return {"schema_version": 1, "task_id": TASK_ID, "arm": self.arm,
                "thread_id": self.thread_id, "round": self.round,
                "complete": self.complete, "events": self.events,
                "chain_sha256": self._previous,
                "manifest_sha256": file_sha(HERE / "manifest.json")}
