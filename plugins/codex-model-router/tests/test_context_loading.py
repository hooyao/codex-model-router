"""Exercise context emission across independent hook processes."""

import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "hooks"))
import router_hook as hook


class ContextLoadingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.config = self.workspace / ".codex-model-router" / "routing.json"
        self.plugin = PLUGIN

    def invoke(self, event="UserPromptSubmit", session="session-one", **fields):
        payload = {"hook_event_name": event, "cwd": str(self.workspace), **fields}
        if session is not None:
            payload["session_id"] = session
        # Isolate only this process's temporary cache; do not modify user config.
        program = (f"import tempfile,runpy; tempfile.tempdir={str(self.root)!r}; "
                   f"runpy.run_path({str(self.plugin / 'hooks/router_hook.py')!r},run_name='__main__')")
        return subprocess.run([sys.executable, "-c", program], input=json.dumps(payload),
                              capture_output=True, text=True, timeout=10)

    def output(self, *args, **kwargs):
        result = self.invoke(*args, **kwargs)
        self.assertEqual(0, result.returncode, result.stderr)
        return json.loads(result.stdout)

    def test_unchanged_controller_context_is_not_repeated(self):
        first = self.output("SessionStart", source="startup")
        self.assertIn("Router skill:", first["hookSpecificOutput"]["additionalContext"])
        self.assertLess(len(json.dumps(first).encode()), 1800)
        self.assertEqual({}, self.output())
        self.assertEqual({}, self.output(prompt="A follow-up without routing keywords"))

    def test_recovery_sources_restore_entrypoint_before_next_turn(self):
        self.output("SessionStart")
        for source in ("startup", "resume", "clear", "fork", "compact"):
            with self.subTest(source=source):
                restored = self.output("SessionStart", source=source)
                self.assertIn("hookSpecificOutput", restored)
                if source in {"compact", "clear"}:
                    self.assertIn("Context reset:", restored["hookSpecificOutput"]["additionalContext"])
                self.assertEqual({}, self.output())

    def test_changed_config_refreshes_and_invalid_config_never_uses_cache(self):
        self.output("SessionStart")
        data = json.loads(self.config.read_text(encoding="utf-8"))
        data["selection_principle"] = "Changed workspace policy."
        self.config.write_text(json.dumps(data), encoding="utf-8")
        self.assertIn("hookSpecificOutput", self.output())
        self.assertEqual({}, self.output())
        self.config.write_text("{", encoding="utf-8")
        result = self.invoke()
        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertIn("malformed JSON", result.stderr)

    def test_instruction_changes_refresh_existing_session(self):
        self.plugin = self.root / "plugin-copy"
        shutil.copytree(PLUGIN, self.plugin, ignore=shutil.ignore_patterns("__pycache__"))
        first = self.output("SessionStart")
        self.assertEqual({}, self.output())
        policy = self.plugin / "skills/model-router/references/routing-policy.md"
        policy.write_text(policy.read_text(encoding="utf-8") + "\nUpdated policy.\n", encoding="utf-8")
        updated = self.output()
        self.assertIn("hookSpecificOutput", updated)
        before_text = first["hookSpecificOutput"]["additionalContext"]
        after_text = updated["hookSpecificOutput"]["additionalContext"]
        before_revision = next(line for line in before_text.splitlines() if line.startswith("Instructions revision: "))
        after_revision = next(line for line in after_text.splitlines() if line.startswith("Instructions revision: "))
        self.assertNotEqual(before_revision, after_revision)
        self.assertEqual(next(line for line in before_text.splitlines() if line.startswith("Validated config SHA256")),
                         next(line for line in after_text.splitlines() if line.startswith("Validated config SHA256")))
        self.assertEqual({}, self.output())

    def test_workspace_and_session_identity_do_not_share_suppression(self):
        self.output("SessionStart")
        self.assertIn("hookSpecificOutput", self.output(session="session-two"))
        self.workspace = self.root / "other-workspace"
        self.workspace.mkdir()
        self.assertIn("hookSpecificOutput", self.output())

    def test_missing_session_and_corrupt_cache_repeat_safely(self):
        for _ in range(2):
            self.assertIn("hookSpecificOutput", self.output(session=None))
        self.output("SessionStart")
        entries = list((self.root / "codex-model-router-context-v1").glob("*.json"))
        self.assertEqual(1, len(entries))
        for damaged in ("{", "{}", "x" * 1024):
            entries[0].write_text(damaged, encoding="utf-8")
            self.assertIn("hookSpecificOutput", self.output())

    def test_unwritable_cache_does_not_drop_entrypoint(self):
        (self.root / "codex-model-router-context-v1").write_text("not a directory", encoding="utf-8")
        for _ in range(2):
            self.assertIn("hookSpecificOutput", self.output())

    def test_workers_always_get_role_without_touching_controller_cache(self):
        worker = self.output("SubagentStart")
        self.assertFalse(self.config.exists())
        self.assertEqual(worker, self.output("SubagentStart", agent_id="another-worker"))
        context = worker["hookSpecificOutput"]["additionalContext"]
        self.assertLess(len(context.encode()), 800)
        self.assertNotIn("ROUTING_CONFIG", context)
        self.assertIn("hookSpecificOutput", self.output())
        self.assertEqual({}, self.output())
        self.output("SubagentStart")
        self.assertEqual({}, self.output())

    def test_cache_stores_no_prompt_session_or_workspace_text(self):
        self.output("SessionStart", session="private-session-value", prompt="private prompt value")
        entries = list((self.root / "codex-model-router-context-v1").glob("*.json"))
        self.assertEqual(1, len(entries))
        record = entries[0].read_text(encoding="utf-8")
        self.assertEqual({"version", "signature"}, set(json.loads(record)))
        self.assertLess(len(record.encode()), 256)
        for value in ("private-session-value", "private prompt value", str(self.workspace)):
            self.assertNotIn(value, entries[0].name + record)

    def test_worker_override_survives_invalid_controller_config_without_writes(self):
        self.config.parent.mkdir()
        self.config.write_text("{", encoding="utf-8")
        before = {p.relative_to(self.workspace): p.read_bytes()
                  for p in self.workspace.rglob("*") if p.is_file()}
        self.assertIn("hookSpecificOutput", self.output("SubagentStart"))
        after = {p.relative_to(self.workspace): p.read_bytes()
                 for p in self.workspace.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_hook_does_not_create_bytecode_in_installed_plugin(self):
        self.plugin = self.workspace / "installed-plugin"
        shutil.copytree(PLUGIN, self.plugin, ignore=shutil.ignore_patterns("__pycache__"))
        before = {p.relative_to(self.plugin): p.read_bytes()
                  for p in self.plugin.rglob("*") if p.is_file()}
        self.output("SubagentStart")
        after = {p.relative_to(self.plugin): p.read_bytes()
                 for p in self.plugin.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_failed_stdout_never_marks_entrypoint_delivered(self):
        event = {"hook_event_name": "SessionStart", "cwd": str(self.workspace), "session_id": "one"}
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(event))), \
             mock.patch("builtins.print", side_effect=BrokenPipeError), \
             mock.patch.object(hook, "remember_emission") as remember:
            with self.assertRaises(BrokenPipeError):
                hook.main()
        remember.assert_not_called()


if __name__ == "__main__":
    unittest.main()
