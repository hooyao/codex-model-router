from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator = load_module("router_validator", PLUGIN_ROOT / "scripts" / "validate_plugin.py")
hook = load_module("router_hook", PLUGIN_ROOT / "hooks" / "router_hook.py")


class RouterPluginTests(unittest.TestCase):
    def test_complete_package_validates_offline(self) -> None:
        self.assertEqual([], validator.validate_package(PLUGIN_ROOT))

    def test_all_fixtures_emit_valid_context(self) -> None:
        for event_name, fixture_name in validator.REQUIRED_HOOK_EVENTS.items():
            with self.subTest(event=event_name):
                self.assertEqual([], validator.run_hook_fixture(PLUGIN_ROOT, event_name, fixture_name))

    def test_controller_context_contains_policy_roles(self) -> None:
        fixture = json.loads((PLUGIN_ROOT / "tests" / "fixtures" / "session-start.json").read_text(encoding="utf-8"))
        context = hook.build_hook_output(fixture)["hookSpecificOutput"]["additionalContext"]
        for expected in ("Active controller model", "Luna", "Terra", "Sol", "Astra", "capability preflight", "independent review"):
            self.assertIn(expected, context)

    def controller_contexts(self):
        for event_name in ("SessionStart", "UserPromptSubmit"):
            fixture_name = validator.REQUIRED_HOOK_EVENTS[event_name]
            fixture = json.loads((PLUGIN_ROOT / "tests" / "fixtures" / fixture_name).read_text(encoding="utf-8"))
            yield event_name, hook.build_hook_output(fixture)["hookSpecificOutput"]["additionalContext"]

    def test_both_controller_events_allow_only_orchestration(self) -> None:
        allowed_actions = (
            "capability discovery", "task DAG creation", "dispatch", "waiting/collection",
            "worker-packet validation", "conflict resolution", "final user-facing synthesis",
        )
        for event_name, context in self.controller_contexts():
            with self.subTest(event=event_name):
                self.assertIn("pure orchestrator", context)
                allowed = next(line for line in context.splitlines() if line.startswith("Allowed controller actions ONLY:"))
                for action in allowed_actions:
                    self.assertIn(action, allowed)

    def test_both_controller_events_prohibit_business_execution(self) -> None:
        forbidden_actions = (
            "business-domain analysis", "repository/file inspection for business purposes",
            "file edits", "command execution", "testing", "business-result validation",
        )
        for event_name, context in self.controller_contexts():
            with self.subTest(event=event_name):
                prohibited = next(line for line in context.splitlines() if line.startswith("Prohibited controller actions:"))
                for action in forbidden_actions:
                    self.assertIn(action, prohibited)
                self.assertIn("Do not relabel yourself as a worker", context)

    def test_all_business_work_is_delegated_or_reported_blocked(self) -> None:
        for event_name, context in self.controller_contexts():
            with self.subTest(event=event_name):
                for expected in (
                    "exposed tool catalog", "spawn_agent", "matching wait/collect tools",
                    "available worker models/reasoning efforts",
                    "dispatch bounded workers for ALL business work, including simple tasks",
                    "report BLOCKED to the user with the missing capability",
                    "do not silently perform the business work yourself",
                    "never controller execution",
                ):
                    self.assertIn(expected, context)

    def test_packet_validation_and_conflict_resolution_do_not_allow_rework(self) -> None:
        for event_name, context in self.controller_contexts():
            with self.subTest(event=event_name):
                for expected in (
                    "task identity", "required fields", "evidence references",
                    "reported acceptance/validation status only",
                    "require a worker follow-up", "conflict resolution means coordinating workers",
                    "not inspecting artifacts, resolving code conflicts, or rerunning tests yourself",
                    "Serialize overlapping write scopes",
                ):
                    self.assertIn(expected, context)

    def test_meta_prompt_requires_high_capability_independent_review(self) -> None:
        for prompt in (
            "Optimize codex-model-router evaluation and benchmark selection.",
            "Implement the routing-policy fix.",
            "Select benchmarks for this router.",
            "Fix the issues from that review.",
            "那么去修复及evaluation本身吧",
            "",
        ):
            with self.subTest(prompt=prompt):
                context = hook.build_hook_output({
                    "hook_event_name": "UserPromptSubmit", "prompt": prompt,
                })["hookSpecificOutput"]["additionalContext"]
                for expected in (
                    "router self-improvement", "routing-policy review", "evaluation design",
                    "benchmark selection", "independent review is mandatory before finalization",
                    "separate from the author/implementer", "highest suitable available model",
                    "implementation must also have an implementation worker",
                    "a review-only worker does not fulfill it",
                ):
                    self.assertIn(expected, context)

    def test_simple_prompt_and_missing_prompt_keep_full_contract(self) -> None:
        for event in (
            {"hook_event_name": "UserPromptSubmit", "prompt": "Fix one documentation typo."},
            {"hook_event_name": "UserPromptSubmit"},
        ):
            context = hook.build_hook_output(event)["hookSpecificOutput"]["additionalContext"]
            self.assertIn("dispatch bounded workers for ALL business work, including simple tasks", context)
            self.assertIn("Prohibited controller actions:", context)
            self.assertIn("independent review is mandatory", context)

    def test_worker_context_requires_structured_result(self) -> None:
        fixture = json.loads((PLUGIN_ROOT / "tests" / "fixtures" / "subagent-start.json").read_text(encoding="utf-8"))
        context = hook.build_hook_output(fixture)["hookSpecificOutput"]["additionalContext"]
        for expected in ("Do not start subagents", "Task ID:", "Outcome:", "Evidence:", "Validation:", "Risks or blockers:"):
            self.assertIn(expected, context)

    def test_worker_owns_execution_and_validation_within_scope(self) -> None:
        context = hook.build_hook_output({"hook_event_name": "SubagentStart"})["hookSpecificOutput"]["additionalContext"]
        for expected in (
            "bounded worker, not the controller", "Controller-only prohibitions do not apply",
            "business-domain analysis", "repository/file inspection", "command execution",
            "file edits", "testing", "business-result validation",
            "read-only review does not permit edits", "implementation task requires implementation",
            "preserve unrelated changes", "do not send execution or testing back to the controller",
            "explicitly not run with a reason",
        ):
            self.assertIn(expected, context)
        self.assertNotIn("Allowed controller actions ONLY:", context)

    def test_controller_contract_excludes_dispatched_workers(self) -> None:
        for event_name, context in self.controller_contexts():
            with self.subTest(event=event_name):
                self.assertIn("CONTROLLER ROLE ONLY: SessionStart/UserPromptSubmit", context)
                self.assertIn("does not apply to dispatched workers, including verification workers", context)
                self.assertIn("even if this controller context was inherited", context)
                self.assertIn("Controller discovery, mandatory dispatch, and missing-delegation rules do not apply to workers", context)

    def test_worker_role_override_authorizes_work_without_controller_preflight(self) -> None:
        fixture = json.loads((PLUGIN_ROOT / "tests" / "fixtures" / "subagent-start.json").read_text(encoding="utf-8"))
        # Exercise the executable hook for the role that exposed the regression.
        fixture["agent_type"] = "verification"
        result = subprocess.run(
            [sys.executable, str(PLUGIN_ROOT / "hooks" / "router_hook.py")],
            input=json.dumps(fixture), capture_output=True, text=True, timeout=10,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        output = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual("SubagentStart", output["hookEventName"])
        context = output["additionalContext"]
        self.assertTrue(context.startswith("WORKER ROLE OVERRIDE (SubagentStart)"))
        for expected in (
            "supersedes inherited Codex Model Router controller-only restrictions",
            "authorized and required", "business-domain analysis", "repository/file inspection",
            "file edits", "command execution", "testing", "task validation",
            "within your bounded packet", "verification worker must inspect",
            "run the assigned checks/tests", "does not depend on native multi-agent tooling",
            "Do not dispatch subworkers.", "Do not start subagents.",
        ):
            self.assertIn(expected, context)
        for forbidden in (
            "capability discovery", "capability preflight", "BLOCKED",
            "CONTROLLER ROLE ONLY", "Allowed controller actions ONLY:",
            "Prohibited controller actions:", "independent review is mandatory",
            "unless the parent explicitly instructs", "highest suitable available model",
        ):
            self.assertNotIn(forbidden.lower(), context.lower())

    def test_worker_role_does_not_depend_on_agent_type_or_prompt(self) -> None:
        for agent_type in ("worker", "verification", "review", "implementation"):
            with self.subTest(agent_type=agent_type):
                context = hook.build_hook_output({
                    "hook_event_name": "SubagentStart", "agent_type": agent_type,
                    "prompt": "Review codex-model-router and run its tests.",
                })["hookSpecificOutput"]["additionalContext"]
                self.assertTrue(context.startswith("WORKER ROLE OVERRIDE (SubagentStart)"))
                self.assertIn("authorized and required", context)
                self.assertNotIn("CONTROLLER ROLE ONLY", context)
                self.assertNotIn("BLOCKED", context)

    def test_skill_and_policy_disallow_worker_recursive_delegation(self) -> None:
        for relative_path in (
            "skills/model-router/SKILL.md",
            "skills/model-router/references/routing-policy.md",
        ):
            with self.subTest(path=relative_path):
                content = " ".join((PLUGIN_ROOT / relative_path).read_text(encoding="utf-8").lower().split())
                self.assertIn("worker-role override", content)
                self.assertIn("workers must not dispatch subworkers", content)
                self.assertNotIn("unless their parent explicitly authorizes", content)
                self.assertNotIn("unless the parent explicitly authorizes", content)

    def test_full_context_fits_configured_output_limits(self) -> None:
        config = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        for event_name, fixture_name in validator.REQUIRED_HOOK_EVENTS.items():
            fixture = json.loads((PLUGIN_ROOT / "tests" / "fixtures" / fixture_name).read_text(encoding="utf-8"))
            context = hook.build_hook_output(fixture)["hookSpecificOutput"]["additionalContext"]
            for group in config["hooks"][event_name]:
                for handler in group["hooks"]:
                    with self.subTest(event=event_name):
                        self.assertLessEqual(len(context.encode("utf-8")), handler["additionalContextLimit"])

    def test_policy_documents_remove_direct_execution_escape_hatches(self) -> None:
        paths = (
            PLUGIN_ROOT / "skills" / "model-router" / "SKILL.md",
            PLUGIN_ROOT / "skills" / "model-router" / "references" / "routing-policy.md",
        )
        texts = list(self.controller_contexts()) + [(str(path), path.read_text(encoding="utf-8")) for path in paths]
        for label, text in texts:
            with self.subTest(source=label):
                normalized = " ".join(text.lower().split())
                for forbidden in (
                    "handle simple work directly", "execute simple, low-risk work directly",
                    "verify the combined result yourself", "the controller still owns synthesis and verification",
                    "do not delegate to reduce work", "if it is unavailable, continue safely",
                ):
                    self.assertNotIn(forbidden, normalized)
                for required in (
                    "including simple tasks", "blocked", "business-result validation",
                    "highest suitable available model", "os/tool permission barrier",
                ):
                    self.assertIn(required, normalized)

    def test_hook_context_is_instruction_policy_not_tool_interception(self) -> None:
        for event_name, context in self.controller_contexts():
            with self.subTest(event=event_name):
                self.assertIn("policy enforcement at the agent-instruction layer", context)
                self.assertIn("Context injection cannot intercept tool calls", context)
                self.assertIn("not an OS/tool permission barrier", context)
                output = hook.build_hook_output({"hook_event_name": event_name})
                self.assertEqual({"hookSpecificOutput"}, set(output))
                self.assertEqual({"hookEventName", "additionalContext"}, set(output["hookSpecificOutput"]))

    def test_missing_manifest_reference_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            copied_root = Path(temporary_directory) / "codex-model-router"
            shutil.copytree(PLUGIN_ROOT, copied_root)
            manifest_path = copied_root / ".codex-plugin" / "plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["skills"] = "./missing-skills/"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            errors = validator.validate_package(copied_root, run_fixtures=False)
        self.assertTrue(any("manifest path 'skills' does not exist" in error for error in errors))

    def test_malformed_hook_output_is_reported(self) -> None:
        errors = validator.validate_hook_output("not json", "SessionStart", "malformed fixture")
        self.assertTrue(any("malformed fixture: invalid JSON hook output" in error for error in errors))

    def test_unsupported_hook_event_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            hook.build_hook_output({"hook_event_name": "Stop"})


if __name__ == "__main__":
    unittest.main()
