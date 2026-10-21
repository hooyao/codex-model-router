"""Offline checks for the one native Go command continuation chain."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from evals.long_horizon_v1.evaluator_smoke import _go_command_proof


THREAD = "smoke-thread"
TURN = "smoke-turn"
SESSION = 8251


def chain() -> list[dict]:
    meta = {"turn_id": TURN}
    def response(kind: str, call_id: str, **values: object) -> dict:
        return {"type": "response_item", "payload": {"type": kind,
            "call_id": call_id, "internal_chat_message_metadata_passthrough": meta,
            **values}}
    return [
        {"type": "session_meta", "payload": {"id": THREAD}},
        response("custom_tool_call", "start", input='text(await tools.exec_command({cmd:"& C:\\go.exe test -count=1 ./..."}));'),
        response("custom_tool_call_output", "start", output=[{"text": json.dumps({"session_id": SESSION})}]),
        {"type": "event_msg", "payload": {"type": "item_completed", "thread_id": THREAD,
            "turn_id": TURN, "item": {"type": "CommandExecution", "process_id": str(SESSION),
                "command": ["pwsh", "-Command", "& C:\\go.exe test -count=1 ./..."],
                "status": "completed", "exit_code": 0,
                "stdout": "ok  example.com/evaluator-smoke"}}},
        response("custom_tool_call", "poll", input=f"text(await tools.write_stdin({{session_id:{SESSION},chars:''}}));"),
        response("custom_tool_call_output", "poll", output=[{"text": json.dumps({
            "exit_code": 0, "output": "ok  example.com/evaluator-smoke"})}]),
        {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": TURN}},
    ]


class EvaluatorSmokeChainTests(unittest.TestCase):
    def check(self, rows: list[dict], valid: bool) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rollout.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            if valid:
                self.assertEqual(_go_command_proof(path, Path("C:\\go.exe"))["go_exit_code"], 0)
            else:
                with self.assertRaises(ValueError):
                    _go_command_proof(path, Path("C:\\go.exe"))

    def test_valid_native_completion_before_poll(self) -> None:
        self.check(chain(), True)

    def test_reject_broken_or_ambiguous_chains(self) -> None:
        changes = (
            lambda rows: rows[3]["payload"].update(thread_id="other-thread"),
            lambda rows: rows[4]["payload"]["internal_chat_message_metadata_passthrough"].update(turn_id="other-turn"),
            lambda rows: rows[3]["payload"]["item"].update(exit_code=1),
            lambda rows: rows[5]["payload"].update(call_id="unrelated"),
            lambda rows: rows.pop(),
            lambda rows: rows.insert(4, copy.deepcopy(rows[3])),
            lambda rows: rows.insert(4, copy.deepcopy(rows[2])),
        )
        for change in changes:
            with self.subTest(change=change):
                rows = chain()
                change(rows)
                self.check(rows, False)


if __name__ == "__main__":
    unittest.main()
