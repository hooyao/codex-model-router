from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator = load_module("router_validator", PLUGIN_ROOT / "scripts" / "validate_plugin.py")
hook = load_module("router_hook", PLUGIN_ROOT / "hooks" / "router_hook.py")
naming = load_module("subagent_naming", PLUGIN_ROOT / "hooks" / "subagent_naming.py")
sys.path.insert(0, str(PLUGIN_ROOT / "hooks"))
dispatch = load_module("dispatch_contract_under_test", PLUGIN_ROOT / "hooks" / "dispatch_contract.py")
dispatch_audit = load_module("dispatch_audit_under_test", PLUGIN_ROOT / "hooks" / "dispatch_audit.py")
config_module = load_module("routing_config_under_test", PLUGIN_ROOT / "hooks" / "routing_config.py")
decision = load_module("execution_decision_under_test", PLUGIN_ROOT / "hooks" / "execution_decision.py")
init_router = load_module("init_router_under_test", PLUGIN_ROOT / "scripts" / "init_router.py")


class RouterPluginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy_workspace_temporary_directory = tempfile.TemporaryDirectory()
        cls.policy_workspace = Path(cls.policy_workspace_temporary_directory.name).resolve()
        config_module.load_workspace_config(cls.policy_workspace)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.policy_workspace_temporary_directory.cleanup()

    def build_hook_output(self, event: dict):
        isolated_event = dict(event)
        if isolated_event.get("cwd") in (None, "."):
            isolated_event["cwd"] = str(self.policy_workspace)
        return hook.build_hook_output(isolated_event)

    def test_default_and_workspace_routing_configs_validate_independently(self) -> None:
        default_path = PLUGIN_ROOT / "defaults" / "default-routing.json"
        workspace_path = self.policy_workspace / ".codex-model-router" / "routing.json"
        default = config_module.load_config(default_path)
        workspace = config_module.load_config(workspace_path)
        for label, config in (("default", default), ("workspace", workspace)):
            with self.subTest(config=label):
                self.assertEqual(3, config["schema_version"])
                self.assertEqual("evaluate", config["execution_policy"]["default_mode"])
                self.assertLessEqual(
                    len(config_module.serialized_config(config).encode("utf-8")),
                    config_module.MAX_SERIALIZED_CONFIG_BYTES,
                )

    def test_obsolete_config_versions_are_rejected_instead_of_silently_migrated(self) -> None:
        template = json.loads(
            (PLUGIN_ROOT / "defaults" / "default-routing.json").read_text(encoding="utf-8")
        )
        for old_version in (1, 2):
            with self.subTest(version=old_version):
                obsolete = json.loads(json.dumps(template))
                obsolete["schema_version"] = old_version
                with self.assertRaisesRegex(config_module.RoutingConfigError, "must be integer 3"):
                    config_module.validate_config(obsolete)

    def test_v3_execution_policy_and_route_modes_are_strictly_validated(self) -> None:
        template = json.loads(
            (PLUGIN_ROOT / "defaults" / "default-routing.json").read_text(encoding="utf-8")
        )
        cases = (
            ("execution_policy.default_mode", lambda value: value["execution_policy"].__setitem__("default_mode", "sometimes")),
            ("execution_policy.reroute_on_escalation", lambda value: value["execution_policy"].__setitem__("reroute_on_escalation", 1)),
            ("execution_policy.delegate_topology.parallel_enabled", lambda value: value["execution_policy"]["delegate_topology"].__setitem__("parallel_enabled", False)),
            ("execution_policy.limits.max_concurrency", lambda value: value["execution_policy"]["limits"].__setitem__("max_concurrency", 0)),
            ("examples[0].execution_mode", lambda value: value["examples"][0].__setitem__("execution_mode", "controller")),
        )
        for expected, mutate in cases:
            with self.subTest(field=expected):
                config = json.loads(json.dumps(template))
                mutate(config)
                with self.assertRaisesRegex(config_module.RoutingConfigError, expected.replace("[", r"\[").replace("]", r"\]")):
                    config_module.validate_config(config)

    def test_default_config_includes_required_routing_examples(self) -> None:
        config = config_module.load_config(PLUGIN_ROOT / "defaults" / "default-routing.json")
        examples = {example["id"]: example for example in config["examples"]}
        expected = {
            "astra-3d-modeling": ("Astra", "high"),
            "astra-image-analysis": ("Astra", "high"),
            "sol-code-reading-analysis": ("Sol", "high"),
            "luna-detailed-manual-procedure": ("Luna", "medium"),
            "astra-architecture": ("Astra", "xhigh"),
            "astra-security": ("Astra", "xhigh"),
            "astra-complex-tools": ("Astra", "xhigh"),
            "astra-research-synthesis": ("Astra", "xhigh"),
            "sol-debugging": ("Sol", "high"),
            "sol-open-ended-implementation": ("Sol", "high"),
            "sol-repository-discovery": ("Sol", "medium"),
            "sol-everyday-implementation": ("Sol", "medium"),
            "sol-documentation": ("Sol", "medium"),
            "sol-test-triage": ("Sol", "medium"),
            "sol-routine-review": ("Sol", "medium"),
            "luna-extraction": ("Luna", "low"),
            "luna-classification": ("Luna", "low"),
            "luna-normalization": ("Luna", "low"),
        }
        for example_id, (model_class, effort) in expected.items():
            with self.subTest(example=example_id):
                self.assertEqual(model_class, examples[example_id]["preferred_model_class"])
                self.assertEqual(effort, examples[example_id]["reasoning_effort"])
        self.assertEqual("direct", examples["luna-local-bounded-change"]["execution_mode"])
        self.assertEqual("delegate", examples["astra-complex-tools"]["execution_mode"])
        self.assertEqual("evaluate", examples["sol-documentation"]["execution_mode"])
        self.assertEqual({"Astra", "Sol", "Luna"}, {example["preferred_model_class"] for example in examples.values()})
        self.assertIn("max", config["effort_guidance"])
        self.assertTrue(any(example["reasoning_effort"] == "xhigh" for example in examples.values()))
        self.assertTrue(all(example["reasoning_effort"] != "max" for example in examples.values()))

    def test_existing_v3_terra_config_and_optional_max_guidance_remain_valid(self) -> None:
        template = config_module.load_config(PLUGIN_ROOT / "defaults" / "default-routing.json")
        template["effort_guidance"].pop("max")
        template["examples"][0]["preferred_model_class"] = "Terra"
        self.assertEqual(template, config_module.validate_config(template))

        template["effort_guidance"]["max"] = "Use only for extreme supported cases."
        self.assertEqual(template, config_module.validate_config(template))
        template["effort_guidance"]["unsupported"] = "not a valid effort"
        with self.assertRaisesRegex(config_module.RoutingConfigError, "contains unknown fields"):
            config_module.validate_config(template)

    def test_sol_preference_requires_runtime_model_and_effort_evidence(self) -> None:
        config = config_module.load_config(PLUGIN_ROOT / "defaults" / "default-routing.json")
        guidance = config["runtime_resolution"]
        self.assertIn("Sol prefers gpt-6.1-sol", guidance)
        self.assertIn("requested effort", guidance)
        self.assertIn("gpt-6-sol is a compatibility fallback only", guidance)
        self.assertIn("runtime exposes the fallback model/effort", guidance)
        self.assertIn("https://developers.openai.com/codex/models", config["official_sources"])
        for relative in ("README.md", "skills/model-router/references/routing-policy.md"):
            with self.subTest(path=relative):
                content = (PLUGIN_ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("GPT-6.1 Sol", content)
                self.assertIn("gpt-6.1-sol", content)
                self.assertIn("compatibility fallback", content)

    def test_existing_terra_workspace_is_loaded_as_sol_without_rewriting_file(self) -> None:
        template = config_module.load_config(PLUGIN_ROOT / "defaults" / "default-routing.json")
        template["examples"][0]["preferred_model_class"] = "Terra"
        config_path = self.policy_workspace / "legacy-routing.json"
        config_path.write_text(json.dumps(template), encoding="utf-8")
        original = config_path.read_bytes()
        loaded = config_module.load_config(config_path)
        self.assertEqual("Sol", loaded["examples"][0]["preferred_model_class"])
        self.assertEqual(original, config_path.read_bytes())

    def test_config_rejects_none_and_ultra_efforts(self) -> None:
        for effort in ("none", "ultra"):
            with self.subTest(effort=effort):
                template = config_module.load_config(PLUGIN_ROOT / "defaults" / "default-routing.json")
                template["examples"][0]["reasoning_effort"] = effort
                with self.assertRaisesRegex(config_module.RoutingConfigError, "reasoning_effort"):
                    config_module.validate_config(template)

    def test_execution_mode_precedence_is_deterministic(self) -> None:
        config = config_module.load_config(PLUGIN_ROOT / "defaults" / "default-routing.json")
        config["execution_policy"]["default_mode"] = "delegate"
        self.assertEqual("delegate", config_module.configured_execution_mode(config, ()))
        self.assertEqual(
            "direct",
            config_module.configured_execution_mode(config, ("luna-local-bounded-change",)),
        )
        self.assertEqual(
            "delegate",
            config_module.configured_execution_mode(
                config, ("luna-local-bounded-change", "astra-complex-tools")
            ),
        )
        with self.assertRaisesRegex(config_module.RoutingConfigError, "unknown routes"):
            config_module.configured_execution_mode(config, ("missing-route",))
        with self.assertRaisesRegex(config_module.RoutingConfigError, "must not contain duplicates"):
            config_module.configured_execution_mode(
                config, ("astra-complex-tools", "astra-complex-tools")
            )

    def decision_request(self, **overrides):
        signals = {
            "one_local_scope": True,
            "bounded_known_outcome": True,
            "network_or_sync": False,
            "long_running_or_monitoring": False,
            "failure_or_recovery": False,
            "named_multistep_runbook": False,
            "substantive_research_or_investigation": False,
            "independent_review_required": False,
            "high_risk": False,
            "multiple_bounded_tasks": False,
            "tasks_independent": False,
            "dependencies_absent": False,
            "write_scopes_disjoint": False,
            "permissions_confirmed": True,
            "safety_constraints_known": True,
            "verification_plan_present": True,
            "write_scope_known": True,
        }
        signals.update(overrides.pop("signals", {}))
        request = {
            "schema_version": 1,
            "decision_id": "route-one",
            "phase": "initial",
            "prior_ownership": None,
            "escalation_trigger": None,
            "matched_example_ids": ["luna-local-bounded-change"],
            "signals": signals,
        }
        request.update(overrides)
        return request

    def test_decision_resolver_applies_rule_precedence_and_unknowns_fail_closed(self) -> None:
        config = config_module.load_config(PLUGIN_ROOT / "defaults" / "default-routing.json")
        config["execution_policy"]["default_mode"] = "delegate"
        direct = decision.resolve_routing_decision(config, self.decision_request())
        self.assertEqual("DIRECT", direct["ownership"])
        self.assertEqual("bounded-direct-fast-path", direct["matched_rule"])

        conflict = decision.resolve_routing_decision(
            config,
            self.decision_request(matched_example_ids=["luna-local-bounded-change", "astra-complex-tools"]),
        )
        self.assertEqual("DELEGATE", conflict["ownership"])
        self.assertEqual("configured-delegate", conflict["matched_rule"])

        unknown = decision.resolve_routing_decision(
            config, self.decision_request(signals={"one_local_scope": None})
        )
        self.assertEqual("DELEGATE", unknown["ownership"])
        self.assertIn("local scope is not known to be singular", unknown["reasons"])
        unknown_risk = decision.resolve_routing_decision(
            config, self.decision_request(signals={"high_risk": None})
        )
        self.assertEqual("INDEPENDENT_REVIEW", unknown_risk["verification_requirement"])

    def test_delegated_request_can_contain_a_separately_qualified_direct_stage(self) -> None:
        config = config_module.load_config(PLUGIN_ROOT / "defaults" / "default-routing.json")
        whole = decision.resolve_routing_decision(
            config,
            self.decision_request(decision_id="whole", matched_example_ids=[],
                                  signals={"network_or_sync": True, "multiple_bounded_tasks": True}),
        )
        stage = decision.resolve_routing_decision(
            config,
            self.decision_request(decision_id="easy-stage", matched_example_ids=["luna-local-bounded-change"]),
        )
        self.assertEqual("DELEGATE", whole["ownership"])
        self.assertEqual("DIRECT", stage["ownership"])
        self.assertEqual("SELF_CHECK", stage["verification_requirement"])

    def test_required_review_forces_delegate_and_independent_verification(self) -> None:
        config = config_module.load_config(PLUGIN_ROOT / "defaults" / "default-routing.json")
        result = decision.resolve_routing_decision(
            config,
            self.decision_request(signals={"independent_review_required": True}),
        )
        self.assertEqual("DELEGATE", result["ownership"])
        self.assertEqual("INDEPENDENT_REVIEW", result["verification_requirement"])
        self.assertEqual("ISOLATED_SERIAL", result["delegate_topology"])

    def test_direct_bound_escalation_is_recorded_and_reclassified(self) -> None:
        config = config_module.load_config(PLUGIN_ROOT / "defaults" / "default-routing.json")
        request = self.decision_request(
            phase="reclassification",
            prior_ownership="DIRECT",
            escalation_trigger="validation-failed",
        )
        result = decision.resolve_routing_decision(config, request)
        self.assertEqual("DELEGATE", result["ownership"])
        self.assertEqual("DIRECT", result["reclassified_from"])
        self.assertEqual("validation-failed", result["escalation_trigger"])
        self.assertIn("approved direct bounds exceeded", result["reasons"][0])

    def test_delegate_topology_selects_parallel_only_for_explicit_safe_signals(self) -> None:
        config = config_module.load_config(PLUGIN_ROOT / "defaults" / "default-routing.json")
        base = {
            "substantive_research_or_investigation": True,
            "multiple_bounded_tasks": True,
            "tasks_independent": True,
            "dependencies_absent": True,
            "write_scopes_disjoint": True,
        }
        parallel = decision.resolve_routing_decision(config, self.decision_request(signals=base))
        self.assertEqual("PARALLEL", parallel["delegate_topology"])

        for field, unsafe in (("tasks_independent", False), ("dependencies_absent", False),
                              ("write_scopes_disjoint", False), ("write_scopes_disjoint", None)):
            with self.subTest(field=field, value=unsafe):
                signals = dict(base)
                signals[field] = unsafe
                result = decision.resolve_routing_decision(
                    config, self.decision_request(signals=signals)
                )
                self.assertEqual("ISOLATED_SERIAL", result["delegate_topology"])

    def test_parallel_topology_respects_single_worker_concurrency_ceiling(self) -> None:
        config = config_module.load_config(PLUGIN_ROOT / "defaults" / "default-routing.json")
        config["execution_policy"]["limits"]["max_concurrency"] = 1
        result = decision.resolve_routing_decision(
            config,
            self.decision_request(signals={
                "substantive_research_or_investigation": True,
                "multiple_bounded_tasks": True,
                "tasks_independent": True,
                "dependencies_absent": True,
                "write_scopes_disjoint": True,
            }),
        )
        self.assertEqual("DELEGATE", result["ownership"])
        self.assertEqual("ISOLATED_SERIAL", result["delegate_topology"])
        self.assertEqual(1, result["constraints"]["max_concurrency"])

    def test_named_multistep_runbook_true_or_unknown_requires_delegation(self) -> None:
        config = config_module.load_config(PLUGIN_ROOT / "defaults" / "default-routing.json")
        for value in (True, None):
            with self.subTest(value=value):
                result = decision.resolve_routing_decision(
                    config,
                    self.decision_request(signals={"named_multistep_runbook": value}),
                )
                self.assertEqual("DELEGATE", result["ownership"])
                self.assertEqual("ISOLATED_SERIAL", result["delegate_topology"])
        self.assertIn("a named multi-step runbook is required", decision.resolve_routing_decision(
            config, self.decision_request(signals={"named_multistep_runbook": True})
        )["reasons"])
        missing_request_signal = self.decision_request()
        del missing_request_signal["signals"]["named_multistep_runbook"]
        with self.assertRaisesRegex(decision.DecisionContractError, "named_multistep_runbook"):
            decision.validate_decision_request(missing_request_signal)
        missing_config_signal = json.loads(json.dumps(config))
        missing_config_signal["execution_policy"]["direct_requires_all"].remove(
            "no-named-multistep-runbook"
        )
        with self.assertRaisesRegex(config_module.RoutingConfigError, "every direct requirement"):
            config_module.validate_config(missing_config_signal)

    def test_hook_exposes_and_cli_executes_decision_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory).resolve()
            context = self.build_hook_output({
                "hook_event_name": "UserPromptSubmit", "cwd": str(workspace),
            })["hookSpecificOutput"]["additionalContext"]
            config_path = workspace / ".codex-model-router" / "routing.json"
            resolver_path = PLUGIN_ROOT / "hooks" / "execution_decision.py"
            self.assertIn(f"Decision resolver program: {resolver_path}", context)
            self.assertIn("decision-request-v1 JSON on stdin", context)
            self.assertIn("named_multistep_runbook", context)
            completed = subprocess.run(
                [sys.executable, str(resolver_path), "--config", str(config_path)],
                input=json.dumps(self.decision_request()),
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        self.assertEqual(0, completed.returncode, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual("DIRECT", result["ownership"])
        self.assertEqual("NONE", result["delegate_topology"])

    def test_decision_contract_rejects_invalid_data_and_inconsistent_results(self) -> None:
        request = self.decision_request()
        request["signals"]["network_or_sync"] = "unknown"
        with self.assertRaisesRegex(decision.DecisionContractError, "boolean or null"):
            decision.validate_decision_request(request)
        result = {
            "schema_version": 1,
            "decision_id": "route-one",
            "ownership": "DIRECT",
            "delegate_topology": "PARALLEL",
            "verification_requirement": "SELF_CHECK",
            "configured_mode": "direct",
            "matched_rule": "bounded-direct-fast-path",
            "reasons": ["bounded"],
            "reclassified_from": None,
            "escalation_trigger": None,
            "constraints": {
                "max_depth": 1,
                "max_concurrency": 3,
                "max_retries": 1,
                "write_policy": "exclusive-ownership-serialize-overlap",
                "context_policy": "minimal-packet-compact-receipt",
            },
        }
        with self.assertRaisesRegex(decision.DecisionContractError, "DIRECT ownership requires NONE"):
            decision.validate_decision_result(result)

    def test_windows_hooks_use_python_and_coherent_limits(self) -> None:
        manifest = json.loads(
            (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("hooks", manifest)
        self.assertTrue((PLUGIN_ROOT / "hooks" / "hooks.json").is_file())
        hooks = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
        self.assertNotIn("matcher", hooks["SessionStart"][0])
        for event_name in validator.REQUIRED_HOOK_EVENTS:
            for group in hooks[event_name]:
                for handler in group["hooks"]:
                    with self.subTest(event=event_name):
                        self.assertEqual(
                            'cmd.exe /d /c python "%PLUGIN_ROOT%\\hooks\\router_hook.py"',
                            handler["commandWindows"],
                        )
                        self.assertEqual(
                            'python3 "$PLUGIN_ROOT/hooks/router_hook.py"',
                            handler["command"],
                        )
                        self.assertNotIn("CLAUDE_PLUGIN_ROOT", handler["command"])
                        self.assertNotIn("CLAUDE_PLUGIN_ROOT", handler["commandWindows"])
                        self.assertNotIn("py -3", handler["commandWindows"])
                        self.assertGreater(
                            handler["additionalContextLimit"],
                            config_module.MAX_SERIALIZED_CONFIG_BYTES,
                        )

    def test_existing_config_is_discovered_above_nested_cwd_before_git(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / ".codex-model-router").mkdir()
            target = root / ".codex-model-router" / "routing.json"
            shutil.copyfile(PLUGIN_ROOT / "defaults" / "default-routing.json", target)
            nested = root / "repo" / "src"
            nested.mkdir(parents=True)
            (root / "repo" / ".git").mkdir()
            actual, exists = config_module.discover_config_path(nested)
        self.assertTrue(exists)
        self.assertEqual(target, actual)

    def test_missing_config_targets_nearest_git_file_or_directory_then_cwd(self) -> None:
        for marker_kind in ("file", "directory"):
            with self.subTest(marker=marker_kind), tempfile.TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                nested = root / "a" / "b"
                nested.mkdir(parents=True)
                marker = root / ".git"
                marker.write_text("gitdir: elsewhere", encoding="utf-8") if marker_kind == "file" else marker.mkdir()
                target, exists = config_module.discover_config_path(nested)
                self.assertFalse(exists)
                self.assertEqual(root / ".codex-model-router" / "routing.json", target)
        with tempfile.TemporaryDirectory() as temporary_directory:
            cwd = Path(temporary_directory).resolve()
            target, exists = config_module.discover_config_path(cwd)
            self.assertFalse(exists)
            self.assertEqual(cwd / ".codex-model-router" / "routing.json", target)

    def test_initialization_is_exclusive_and_never_clobbers_valid_edits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory).resolve()
            (workspace / ".git").mkdir()
            path, config, created = config_module.load_workspace_config(workspace)
            self.assertTrue(created)
            self.assertEqual(
                (PLUGIN_ROOT / "defaults" / "default-routing.json").read_bytes(),
                path.read_bytes(),
            )
            config["selection_principle"] = "Keep this user edit."
            path.write_text(json.dumps(config, indent=2), encoding="utf-8")
            before = path.read_bytes()
            second_path, second_config, second_created = config_module.load_workspace_config(workspace)
            self.assertFalse(second_created)
            self.assertEqual(path, second_path)
            self.assertEqual("Keep this user edit.", second_config["selection_principle"])
            self.assertEqual(before, path.read_bytes())

    def test_every_hook_invocation_reloads_and_injects_workspace_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory).resolve()
            event = {"hook_event_name": "UserPromptSubmit", "cwd": str(workspace)}
            first = self.build_hook_output(event)["hookSpecificOutput"]["additionalContext"]
            path = workspace / ".codex-model-router" / "routing.json"
            config = json.loads(path.read_text(encoding="utf-8"))
            config["selection_principle"] = "Unique reloaded selection principle."
            path.write_text(json.dumps(config, indent=2), encoding="utf-8")
            second = self.build_hook_output(event)["hookSpecificOutput"]["additionalContext"]
            self.assertNotEqual(first, second)
            self.assertIn("Validated config SHA256 (canonical JSON): ", second)
            self.assertNotIn("Unique reloaded selection principle.", second)
            self.assertIn("Read this config for routing examples", second)
            self.assertIn(str(path), second)
            self.assertEqual(1, second.count("ROUTING_CONFIG_BEGIN"))
            self.assertEqual(1, second.count("ROUTING_CONFIG_END"))

    def test_all_supported_events_inject_the_same_compact_routing_block(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory).resolve()
            blocks = []
            for event_name in validator.REQUIRED_HOOK_EVENTS:
                context = self.build_hook_output({
                    "hook_event_name": event_name,
                    "cwd": str(workspace),
                })["hookSpecificOutput"]["additionalContext"]
                blocks.append(context.split("ROUTING_CONFIG_BEGIN\n", 1)[1].split("\nROUTING_CONFIG_END", 1)[0])
            self.assertEqual(blocks[0], blocks[1])
            self.assertEqual(blocks[1], blocks[2])

    def test_hook_payload_keeps_routing_contract_within_visible_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory).resolve()
            config_path = workspace / ".codex-model-router" / "routing.json"
            config_path.parent.mkdir()
            config = json.loads((PLUGIN_ROOT / "defaults" / "default-routing.json").read_text(encoding="utf-8"))
            config["selection_principle"] = "s" * 1_000
            config["runtime_resolution"] = "r" * 1_000
            for example in config["examples"][:12]:
                example["rationale"] = "r" * 500
            config_module.validate_config(config)
            self.assertGreater(len(config_module.serialized_config(config).encode("utf-8")), 12_000)
            config_path.write_text(json.dumps(config), encoding="utf-8")
            expected_digest = hashlib.sha256(
                config_module.serialized_config(config).encode("utf-8")
            ).hexdigest()
            resolver_path = PLUGIN_ROOT / "hooks" / "execution_decision.py"

            for event_name in ("SessionStart", "UserPromptSubmit", "SubagentStart"):
                with self.subTest(event=event_name):
                    result = subprocess.run(
                        [sys.executable, str(PLUGIN_ROOT / "hooks" / "router_hook.py")],
                        input=json.dumps({"hook_event_name": event_name, "cwd": str(workspace)}),
                        capture_output=True, text=True, timeout=10, check=False,
                    )
                    self.assertEqual(0, result.returncode, result.stderr)
                    raw_output = result.stdout.encode("utf-8")
                    self.assertLess(len(raw_output), 9_500)
                    visible_prefix = raw_output[:10_000].decode("utf-8")
                    for required in (
                        "ROUTING_CONFIG_BEGIN", "ROUTING_CONFIG_END",
                        str(config_path), str(resolver_path), expected_digest,
                        "decision-request-v1", "EFFECTIVE_EXECUTION_POLICY:",
                    ):
                        self.assertIn(json.dumps(required)[1:-1], visible_prefix)
                    output = json.loads(result.stdout)["hookSpecificOutput"]
                    self.assertEqual(event_name, output["hookEventName"])
                    self.assertNotIn('"examples":', output["additionalContext"])

    def test_long_workspace_path_keeps_complete_output_bounded_or_fails_closed(self) -> None:
        config = config_module.load_config(PLUGIN_ROOT / "defaults" / "default-routing.json")
        long_path = Path("C:/" + "/".join(["d" * 91] * 10) + "/.codex-model-router/routing.json")
        self.assertGreaterEqual(len(str(long_path)), 950)
        self.assertLessEqual(len(str(long_path)), 970)
        resolver_path = PLUGIN_ROOT / "hooks" / "execution_decision.py"

        with mock.patch.object(hook, "load_workspace_config", return_value=(long_path, config, False)):
            for event_name in ("SessionStart", "UserPromptSubmit", "SubagentStart"):
                with self.subTest(event=event_name):
                    output = self.build_hook_output({"hook_event_name": event_name})
                    serialized = json.dumps(output, separators=(",", ":")) + os.linesep
                    raw_output = serialized.encode("utf-8")
                    self.assertLessEqual(len(raw_output), hook.MAX_HOOK_OUTPUT_BYTES)
                    self.assertLess(len(raw_output), 10_000)
                    context = json.loads(serialized)["hookSpecificOutput"]["additionalContext"]
                    self.assertEqual(1, context.count(str(long_path)))
                    for required in (
                        "ROUTING_CONFIG_BEGIN", "ROUTING_CONFIG_END",
                        str(resolver_path), "decision-request-v1", "EFFECTIVE_EXECUTION_POLICY:",
                    ):
                        self.assertIn(required, context)

        oversized_path = Path("C:/" + "/".join(["d" * 91] * 50) + "/.codex-model-router/routing.json")
        with mock.patch.object(hook, "load_workspace_config", return_value=(oversized_path, config, False)):
            with self.assertRaisesRegex(hook.RoutingConfigError, "hook output is .* maximum is"):
                self.build_hook_output({"hook_event_name": "SessionStart"})

    def test_malformed_invalid_oversized_and_non_regular_config_fail_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            malformed = root / "malformed.json"
            malformed.write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(config_module.RoutingConfigError, "malformed JSON"):
                config_module.load_config(malformed)

            invalid = root / "invalid.json"
            invalid.write_text(json.dumps({"schema_version": 3}), encoding="utf-8")
            with self.assertRaisesRegex(config_module.RoutingConfigError, "missing required fields"):
                config_module.load_config(invalid)

            oversized = root / "oversized.json"
            oversized.write_bytes(b" " * (config_module.MAX_CONFIG_FILE_BYTES + 1))
            with self.assertRaisesRegex(config_module.RoutingConfigError, "maximum file size"):
                config_module.load_config(oversized)

            directory = root / "directory.json"
            directory.mkdir()
            with self.assertRaisesRegex(config_module.RoutingConfigError, "not a regular file"):
                config_module.load_config(directory)

    def test_serialized_config_budget_is_enforced_below_context_limit(self) -> None:
        config = json.loads(
            (PLUGIN_ROOT / "defaults" / "default-routing.json").read_text(encoding="utf-8")
        )
        prototype = config["examples"][0]
        config["examples"] = []
        for index in range(50):
            example = dict(prototype)
            example["id"] = f"large-example-{index}"
            example["task_signals"] = ["signal-" + ("x" * 180)]
            example["rationale"] = "r" * 450
            config["examples"].append(example)
        self.assertLess(config_module.MAX_SERIALIZED_CONFIG_BYTES, validator.MINIMUM_CONTEXT_LIMIT)
        with self.assertRaisesRegex(config_module.RoutingConfigError, "serialized size"):
            config_module.validate_config(config)

    def test_unreadable_and_uncreatable_config_fail_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "routing.json"
            path.write_text("{}", encoding="utf-8")
            with mock.patch.object(Path, "read_bytes", side_effect=PermissionError("denied")):
                with self.assertRaisesRegex(config_module.RoutingConfigError, "cannot read routing config"):
                    config_module.load_config(path)

        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            (workspace / ".codex-model-router").write_text("not a directory", encoding="utf-8")
            with self.assertRaisesRegex(config_module.RoutingConfigError, "cannot create routing config directory"):
                config_module.load_workspace_config(workspace)

    def test_hook_process_rejects_bad_workspace_config_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            config_directory = workspace / ".codex-model-router"
            config_directory.mkdir()
            (config_directory / "routing.json").write_text("not-json", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(PLUGIN_ROOT / "hooks" / "router_hook.py")],
                input=json.dumps({"hook_event_name": "SessionStart", "cwd": str(workspace)}),
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(2, result.returncode)
            self.assertIn("malformed JSON", result.stderr)
            self.assertEqual("", result.stdout)

    def test_invalid_enum_container_types_fail_cleanly_through_init_and_hook(self) -> None:
        cases = (
            ("preferred_model_class", []),
            ("reasoning_effort", {}),
        )
        programs = (
            (
                "init",
                [sys.executable, str(PLUGIN_ROOT / "scripts" / "init_router.py"), "--workspace"],
            ),
            (
                "hook",
                [sys.executable, str(PLUGIN_ROOT / "hooks" / "router_hook.py")],
            ),
        )
        for field, invalid_value in cases:
            for program_name, command_prefix in programs:
                with self.subTest(field=field, program=program_name), tempfile.TemporaryDirectory() as temporary_directory:
                    workspace = Path(temporary_directory).resolve()
                    config_directory = workspace / ".codex-model-router"
                    config_directory.mkdir()
                    config_path = config_directory / "routing.json"
                    config = json.loads(
                        (PLUGIN_ROOT / "defaults" / "default-routing.json").read_text(encoding="utf-8")
                    )
                    config["examples"][0][field] = invalid_value
                    config_path.write_text(json.dumps(config), encoding="utf-8")
                    if program_name == "init":
                        command = [*command_prefix, str(workspace)]
                        input_text = None
                    else:
                        command = command_prefix
                        input_text = json.dumps({"hook_event_name": "SessionStart", "cwd": str(workspace)})
                    result = subprocess.run(
                        command,
                        input=input_text,
                        text=True,
                        capture_output=True,
                        timeout=10,
                        check=False,
                    )
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn(str(config_path), result.stderr)
                    self.assertIn(f".examples[0].{field}", result.stderr)
                    self.assertIn("must be a string set to one of", result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertNotIn("TypeError", result.stderr)

    def test_all_invalid_enum_json_types_use_field_specific_config_errors(self) -> None:
        for field in ("preferred_model_class", "reasoning_effort"):
            for invalid_value in ([], {}, None, 17):
                with self.subTest(field=field, value=invalid_value):
                    config = json.loads(
                        (PLUGIN_ROOT / "defaults" / "default-routing.json").read_text(encoding="utf-8")
                    )
                    config["examples"][0][field] = invalid_value
                    with self.assertRaisesRegex(
                        config_module.RoutingConfigError,
                        rf"workspace[/\\]routing\.json\.examples\[0\]\.{field} "
                        r"must be a string set to one of",
                    ):
                        config_module.validate_config(config, "workspace/routing.json")

    def test_init_preflights_runtime_smoke_and_preserves_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory).resolve()
            (workspace / ".git").mkdir()
            path, created, runtime, version = init_router.initialize(str(workspace))
            self.assertTrue(created)
            self.assertTrue(path.is_file())
            self.assertTrue(runtime.is_file())
            self.assertGreaterEqual(tuple(map(int, version.split("."))), (3, 9))
            before = path.read_bytes()
            second_path, second_created, _runtime, _version = init_router.initialize(str(workspace))
            self.assertEqual(path, second_path)
            self.assertFalse(second_created)
            self.assertEqual(before, path.read_bytes())

    def test_broken_configured_python_requires_path_repair_not_collector_fallback(self) -> None:
        broken = subprocess.CompletedProcess([], 1, "", "No module named 'encodings'")
        with (
            mock.patch.object(init_router.shutil, "which", return_value="C:/broken/python.exe"),
            mock.patch.object(init_router.subprocess, "run", return_value=broken) as run,
            self.assertRaisesRegex(init_router.PreflightError, "encodings.*PATH before Codex starts.*another interpreter"),
        ):
            init_router._python_runtime()
        self.assertEqual("C:/broken/python.exe", run.call_args.args[0][0])
        self.assertIn("import encodings", run.call_args.args[0][2])

    @unittest.skipUnless(sys.platform == "win32", "Windows lifecycle command preflight")
    def test_windows_init_smoke_uses_each_configured_cmd_command(self) -> None:
        handlers = init_router._configured_handlers()
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory).resolve()
            config_path, _config, _created = config_module.load_workspace_config(workspace)
            original_run = subprocess.run
            calls: list[tuple[tuple, dict]] = []

            def spy(*args, **kwargs):
                calls.append((args, kwargs))
                return original_run(*args, **kwargs)

            with mock.patch.object(init_router.subprocess, "run", side_effect=spy):
                init_router._smoke_hook(Path(sys.executable), workspace, config_path, handlers)

        self.assertEqual(len(handlers), len(calls))
        for (event_name, handler), (args, kwargs) in zip(handlers, calls):
            with self.subTest(event=event_name):
                self.assertEqual(
                    handler["commandWindows"], args[0]
                )
                self.assertEqual(str(PLUGIN_ROOT), kwargs["env"]["PLUGIN_ROOT"])
                self.assertEqual(event_name, json.loads(kwargs["input"])["hook_event_name"])
                self.assertFalse(kwargs.get("shell", False))

    def test_windows_lifecycle_smoke_failure_is_actionable(self) -> None:
        workspace = Path.cwd().resolve()
        config_path = workspace / ".codex-model-router" / "routing.json"
        handler = {"commandWindows": init_router.WINDOWS_HOOK_COMMAND}
        failed = subprocess.CompletedProcess(
            ["cmd.exe", "/d", "/s", "/c", init_router.WINDOWS_HOOK_COMMAND],
            returncode=7,
            stdout="",
            stderr="python command was not found",
        )
        with (
            mock.patch.object(init_router.sys, "platform", "win32"),
            mock.patch.object(init_router.subprocess, "run", return_value=failed),
            self.assertRaisesRegex(
                init_router.PreflightError,
                r"Windows lifecycle smoke command for SessionStart.*exited 7.*python command was not found",
            ),
        ):
            init_router._smoke_hook(
                Path(sys.executable), workspace, config_path, [("SessionStart", handler)]
            )

    def test_project_init_skill_is_discoverable_and_documents_portable_execution(self) -> None:
        skill_path = PLUGIN_ROOT / "skills" / "initialize-router" / "SKILL.md"
        content = skill_path.read_text(encoding="utf-8")
        for expected in (
            "name: initialize-router",
            "Initialize Codex Model Router in this project",
            "<installed-plugin-root>\\scripts\\init_router.py",
            "<installed-plugin-root>/scripts/init_router.py",
            "--workspace .",
            "Automatic initialization",
            "never overwrites",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, content)

    def test_portable_explicit_init_creates_then_preserves_arbitrary_project_config(self) -> None:
        program = PLUGIN_ROOT / "scripts" / "init_router.py"
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory).resolve()
            first = subprocess.run(
                [sys.executable, str(program), "--workspace", "."],
                cwd=workspace,
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
            config_path = workspace / ".codex-model-router" / "routing.json"
            self.assertEqual(0, first.returncode, first.stderr)
            self.assertIn("created config", first.stdout)
            self.assertTrue(config_path.is_file())

            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["selection_principle"] = "Arbitrary project user edit."
            config_path.write_text(json.dumps(config), encoding="utf-8")
            before = config_path.read_bytes()
            second = subprocess.run(
                [sys.executable, str(program), "--workspace", "."],
                cwd=workspace,
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(0, second.returncode, second.stderr)
            self.assertIn("validated existing config", second.stdout)
            self.assertEqual(before, config_path.read_bytes())

    def test_hook_auto_initializes_arbitrary_project_on_first_event(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory).resolve()
            result = subprocess.run(
                [sys.executable, str(PLUGIN_ROOT / "hooks" / "router_hook.py")],
                input=json.dumps({"hook_event_name": "SessionStart", "cwd": str(workspace)}),
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
            config_path = workspace / ".codex-model-router" / "routing.json"
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue(config_path.is_file())
            context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertIn(str(config_path), context)

    def test_complete_package_validates_offline(self) -> None:
        self.assertEqual([], validator.validate_package(PLUGIN_ROOT))

    @unittest.skipUnless(shutil.which("git"), "Git is required for version-policy regression tests")
    def test_version_policy_requires_strict_semver_increase_for_tracked_plugin_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            plugin = repository / "plugins" / "codex-model-router"
            manifest_path = plugin / ".codex-plugin" / "plugin.json"
            source_path = plugin / "hooks" / "router_hook.py"
            manifest_path.parent.mkdir(parents=True)
            source_path.parent.mkdir(parents=True)
            manifest_path.write_text(json.dumps({"name": "codex-model-router", "version": "0.1.0"}), encoding="utf-8")
            source_path.write_text("print('before')\n", encoding="utf-8")
            subprocess.run(["git", "init"], cwd=repository, check=True, capture_output=True, text=True)
            subprocess.run(["git", "add", "."], cwd=repository, check=True, capture_output=True, text=True)
            subprocess.run(
                ["git", "-c", "user.name=Router test", "-c", "user.email=router@example.invalid", "commit", "-m", "baseline"],
                cwd=repository,
                check=True,
                capture_output=True,
                text=True,
            )
            source_path.write_text("print('after')\n", encoding="utf-8")

            errors: list[str] = []
            validator.validate_version_bump(plugin, errors)
            self.assertTrue(any("strictly increasing SemVer release version" in error for error in errors), errors)

            manifest_path.write_text(
                json.dumps({"name": "codex-model-router", "version": "0.1.1"}),
                encoding="utf-8",
            )
            errors = []
            validator.validate_version_bump(plugin, errors)
            self.assertEqual([], errors)

    @unittest.skipUnless(shutil.which("git"), "Git is required for version-policy regression tests")
    def test_version_policy_requires_strict_semver_increase_for_untracked_plugin_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            plugin = repository / "plugins" / "codex-model-router"
            manifest_path = plugin / ".codex-plugin" / "plugin.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(json.dumps({"name": "codex-model-router", "version": "0.1.0"}), encoding="utf-8")
            subprocess.run(["git", "init"], cwd=repository, check=True, capture_output=True, text=True)
            subprocess.run(["git", "add", "."], cwd=repository, check=True, capture_output=True, text=True)
            subprocess.run(
                ["git", "-c", "user.name=Router test", "-c", "user.email=router@example.invalid", "commit", "-m", "baseline"],
                cwd=repository,
                check=True,
                capture_output=True,
                text=True,
            )
            new_skill = plugin / "skills" / "new skill.md"
            new_skill.parent.mkdir(parents=True)
            new_skill.write_text("new plugin capability\n", encoding="utf-8")

            errors: list[str] = []
            validator.validate_version_bump(plugin, errors)
            self.assertTrue(any("strictly increasing SemVer release version" in error for error in errors), errors)
            self.assertTrue(any("new skill.md" in error for error in errors), errors)

            manifest_path.write_text(
                json.dumps({"name": "codex-model-router", "version": "0.1.1"}),
                encoding="utf-8",
            )
            errors = []
            validator.validate_version_bump(plugin, errors)
            self.assertEqual([], errors)

    def test_semver_precedence_and_validation(self) -> None:
        self.assertIsNotNone(validator.parse_semver("0.1.1"))
        self.assertIsNotNone(validator.parse_semver("0.2.0-rc.1+build.7"))
        self.assertIsNone(validator.parse_semver("01.2.3"))
        self.assertIsNone(validator.parse_semver("0.2.0-01"))
        self.assertIsNone(validator.parse_semver("1\N{ARABIC-INDIC DIGIT TWO}.2.3"))
        self.assertIsNone(validator.parse_semver("1.2.\N{ARABIC-INDIC DIGIT THREE}"))
        self.assertGreater(validator.compare_semver("0.1.1", "0.1.0"), 0)
        self.assertGreater(validator.compare_semver("0.2.0", "0.1.99"), 0)
        self.assertGreater(validator.compare_semver("1.0.0", "0.99.99"), 0)
        self.assertLess(validator.compare_semver("0.2.0-rc.1", "0.2.0"), 0)
        self.assertEqual(0, validator.compare_semver("0.1.1+first", "0.1.1+second"))

    def test_bump_command_rejects_unicode_digits_in_manifest_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            plugin = Path(temporary_directory) / "codex-model-router"
            manifest_path = plugin / ".codex-plugin" / "plugin.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(
                json.dumps({"name": "codex-model-router", "version": "1\N{ARABIC-INDIC DIGIT TWO}.2.3"}),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(PLUGIN_ROOT / "scripts" / "bump_version.py"),
                    "patch",
                    "--plugin-root",
                    str(plugin),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("not valid SemVer", result.stderr)

    @unittest.skipUnless(shutil.which("git"), "Git is required for version-policy regression tests")
    def test_version_policy_rejects_non_increasing_and_build_metadata_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            plugin = repository / "plugins" / "codex-model-router"
            manifest_path = plugin / ".codex-plugin" / "plugin.json"
            source_path = plugin / "hooks" / "router_hook.py"
            manifest_path.parent.mkdir(parents=True)
            source_path.parent.mkdir(parents=True)
            manifest_path.write_text(json.dumps({"name": "codex-model-router", "version": "0.1.1"}), encoding="utf-8")
            source_path.write_text("print('before')\n", encoding="utf-8")
            subprocess.run(["git", "init"], cwd=repository, check=True, capture_output=True, text=True)
            subprocess.run(["git", "add", "."], cwd=repository, check=True, capture_output=True, text=True)
            subprocess.run(["git", "-c", "user.name=Router test", "-c", "user.email=router@example.invalid", "commit", "-m", "baseline"], cwd=repository, check=True, capture_output=True, text=True)
            source_path.write_text("print('after')\n", encoding="utf-8")

            for version in ("0.1.1", "0.1.0", "0.1.1+build.9"):
                manifest_path.write_text(json.dumps({"name": "codex-model-router", "version": version}), encoding="utf-8")
                errors: list[str] = []
                validator.validate_version_bump(plugin, errors)
                self.assertTrue(any("strictly increasing SemVer release version" in error for error in errors), (version, errors))

            manifest_path.write_text(json.dumps({"name": "codex-model-router", "version": "0.2.0"}), encoding="utf-8")
            errors = []
            validator.validate_version_bump(plugin, errors)
            self.assertEqual([], errors)

    @unittest.skipUnless(shutil.which("git"), "Git is required for version-policy regression tests")
    def test_version_policy_reports_ignored_relevant_plugin_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            plugin = repository / "plugins" / "codex-model-router"
            manifest_path = plugin / ".codex-plugin" / "plugin.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(json.dumps({"name": "codex-model-router", "version": "0.1.0"}), encoding="utf-8")
            (repository / ".gitignore").write_text("plugins/codex-model-router/skills/*.md\n", encoding="utf-8")
            subprocess.run(["git", "init"], cwd=repository, check=True, capture_output=True, text=True)
            subprocess.run(["git", "add", "."], cwd=repository, check=True, capture_output=True, text=True)
            subprocess.run(
                ["git", "-c", "user.name=Router test", "-c", "user.email=router@example.invalid", "commit", "-m", "baseline"],
                cwd=repository,
                check=True,
                capture_output=True,
                text=True,
            )
            ignored_skill = plugin / "skills" / "ignored.md"
            ignored_skill.parent.mkdir(parents=True)
            ignored_skill.write_text("ignored capability\n", encoding="utf-8")

            errors: list[str] = []
            validator.validate_version_bump(plugin, errors)
            self.assertTrue(any("ignored by Git" in error for error in errors), errors)

    def test_all_fixtures_emit_valid_context(self) -> None:
        for event_name, fixture_name in validator.REQUIRED_HOOK_EVENTS.items():
            with self.subTest(event=event_name):
                self.assertEqual([], validator.run_hook_fixture(PLUGIN_ROOT, event_name, fixture_name))

    def test_live_cli_procedure_has_fail_closed_activation_and_complete_evidence(self) -> None:
        procedure = (PLUGIN_ROOT / "docs" / "live-cli-validation.md").read_text(encoding="utf-8")
        for expected in (
            "Required activation gate", "did not execute the router lifecycle hook",
            "Commit-TestBaseline", "diff --binary HEAD --",
            "--untracked-files=all", "stale-router", "expected validation failure",
            "hook-created routing file", "underscore-only", "no thread with id",
            "Capture-PersistedSessionTree", "session-index.json",
            "source_evidence_lines", "source_sha256", "copied_sha256",
            "parent_thread_id", "subsequent mirror inspection",
            "verify.ps1` invocation necessarily reads the mirror",
            "fresh-activation-probe-v3.json", "raw/activation-spec.json",
            "resolved_python_executable_sha256", "source-bound",
            "atomic renames", "frozen oracle",
        ):
            self.assertIn(expected, procedure)
        self.assertNotIn("codex exec --ephemeral", procedure)
        self.assertNotIn("DELEGATE then appears before any read or", procedure)

    def test_controller_context_contains_policy_roles(self) -> None:
        fixture = json.loads((PLUGIN_ROOT / "tests" / "fixtures" / "session-start.json").read_text(encoding="utf-8"))
        context = self.build_hook_output(fixture)["hookSpecificOutput"]["additionalContext"]
        for expected in ("Active controller model", "Astra", "native spawn/wait", "independent reviewer"):
            self.assertIn(expected, context)
        workspace_config = self.policy_workspace / ".codex-model-router" / "routing.json"
        self.assertIn(str(workspace_config), context)
        configured_classes = {
            example["preferred_model_class"] for example in config_module.load_config(workspace_config)["examples"]
        }
        self.assertTrue({"Luna", "Sol", "Astra"}.issubset(configured_classes))
        self.assertNotIn(str(PLUGIN_ROOT.parents[1] / ".codex-model-router" / "routing.json"), context)

    def test_controller_context_declares_selective_stage_boundary(self) -> None:
        for event_name, context in self.controller_contexts():
            with self.subTest(event=event_name):
                for required in (
                    "separate decision-request-v1 for that stage independently resolves DIRECT",
                    "exclusive write scope", "context budget", "self-check",
                    "hard kernel", "Never duplicate worker-owned",
                    "whole-request independent-review requirement",
                ):
                    self.assertIn(required, context)

    def controller_contexts(self):
        for event_name in ("SessionStart", "UserPromptSubmit"):
            fixture_name = validator.REQUIRED_HOOK_EVENTS[event_name]
            fixture = json.loads((PLUGIN_ROOT / "tests" / "fixtures" / fixture_name).read_text(encoding="utf-8"))
            yield event_name, self.build_hook_output(fixture)["hookSpecificOutput"]["additionalContext"]

    def test_both_controller_events_require_pre_action_execution_routing(self) -> None:
        for event_name, context in self.controller_contexts():
            with self.subTest(event=event_name):
                for expected in (
                    "Decide execution ownership before the first business action",
                    "validated decision-contract-v1 result",
                    "ROUTE: DIRECT — <rule/reason>",
                    "ROUTE: DELEGATE — <topology/rule/model/effort/reason>",
                    "Skills define HOW work is performed, not WHO performs it",
                    "structured signals; keyword matching alone is insufficient",
                ):
                    self.assertIn(expected, context)

    def test_controller_direct_and_delegate_criteria_are_explicit(self) -> None:
        for event_name, context in self.controller_contexts():
            with self.subTest(event=event_name):
                for expected in (
                    "DIRECT is a bounded fast path only when every fact is explicitly known",
                    "one local scope", "one bounded known outcome", "no network/sync",
                    "permissions and safety constraints are known", "self-check plan",
                    "Unknown signals never qualify", "DIRECT owns its implementation",
                    "PARALLEL only when", "ISOLATED_SERIAL",
                ):
                    self.assertIn(expected, context)

    def test_delegation_topology_and_reroute_are_ordered(self) -> None:
        for event_name, context in self.controller_contexts():
            with self.subTest(event=event_name):
                self.assertIn("exceeds approved bounds, stop before the next business action", context)
                self.assertIn("record a reclassification from DIRECT to DELEGATE", context)
                self.assertIn("validation-failed", context)
                self.assertIn("Any dependency, overlapping/shared write scope", context)
                self.assertIn("requires ISOLATED_SERIAL", context)
                self.assertIn("Only after DELEGATE", context)
                self.assertIn("dispatch-contract-v1", context)
                self.assertIn("missing evidence is a capability blocker", context)

    def test_packet_validation_and_conflict_resolution_do_not_allow_rework(self) -> None:
        for event_name, context in self.controller_contexts():
            with self.subTest(event=event_name):
                for expected in (
                    "task identity", "scope and write ownership", "permissions/safety constraints",
                    "acceptance and verification criteria", "topology position", "limits",
                    "compact evidence receipts", "retain successful receipts",
                    "exclusive write ownership and serialize overlap",
                ):
                    self.assertIn(expected, context)

    def test_controller_requires_canonical_user_visible_worker_names(self) -> None:
        for event_name, context in self.controller_contexts():
            with self.subTest(event=event_name):
                for expected in (
                    "purpose-model-effort", "documented native transport adaptation",
                    "task identity", "actual model/effort",
                ):
                    self.assertIn(expected, context)

    def test_worker_result_echoes_canonical_name(self) -> None:
        context = self.build_hook_output({"hook_event_name": "SubagentStart"})["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Worker name: the unchanged canonical purpose-model-effort identifier", context)
        self.assertIn("Task ID: the same canonical purpose-model-effort identifier", context)

    def test_naming_policy_documents_normalization_validation_and_fallback(self) -> None:
        for relative_path in (
            "skills/model-router/SKILL.md",
            "skills/model-router/references/routing-policy.md",
        ):
            with self.subTest(path=relative_path):
                content = (PLUGIN_ROOT / relative_path).read_text(encoding="utf-8")
                normalized_content = content.lower()
                for expected in (
                    "<purpose>-<model>-<effort>", "unicode nfkd",
                    "discard non-ASCII code points", "^[a-z0-9]+(?:-[a-z0-9]+)*$",
                    "worker name: <canonical-name>", "native worker card",
                    "native task name", "task_name", "underscore",
                ):
                    self.assertIn(expected.lower(), normalized_content)
        skill = (PLUGIN_ROOT / "skills" / "model-router" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("implement-naming-gpt-6-1-sol-high", skill)

    def test_current_sol_name_normalizes_dot_without_changing_selector(self) -> None:
        model = "gpt-6.1-sol"
        expected = "implement-naming-gpt-6-1-sol-high"
        actual = naming.build_subagent_name("Implement / Naming", model, "high")
        self.assertEqual(expected, actual)
        naming.validate_subagent_name(actual, "Implement / Naming", model, "high")
        self.assertEqual("implement_naming_gpt_6_1_sol_high", naming.native_task_name(actual))
        legacy = naming.build_subagent_name("Implement / Naming", "gpt-6-sol", "high")
        naming.validate_unique_subagent_names((actual, legacy))
        naming.validate_unique_native_task_names((actual, legacy))

    def test_worker_name_helper_builds_and_validates_canonical_name(self) -> None:
        expected = "implement-naming-gpt-6-sol-high"
        actual = naming.build_subagent_name("Implement / Naming", "GPT-6 Sol", "High")
        self.assertEqual(expected, actual)
        naming.validate_subagent_name(actual, "Implement / Naming", "GPT-6 Sol", "High")
        self.assertEqual(actual, naming.build_subagent_name("Implement / Naming", "GPT-6 Sol", "High"))
        self.assertEqual(
            "implement_naming_gpt_6_sol_high",
            naming.native_task_name(actual),
        )
        naming.validate_unique_native_task_names((actual, "review_naming_gpt_6_sol_high".replace("_", "-")))

    def test_worker_name_helper_rejects_invalid_components_and_duplicate_dag_names(self) -> None:
        for purpose, model, effort in (("修复", "gpt-6-sol", "low"), ("implement naming", "", "low"), ("implement naming", "gpt-6-sol", "修复")):
            with self.subTest(purpose=purpose, model=model, effort=effort):
                with self.assertRaises(ValueError):
                    naming.build_subagent_name(purpose, model, effort)
        for purpose, model, effort in (("a" * 49, "sol", "low"), ("review", "b" * 49, "low"), ("review", "sol", "c" * 25)):
            with self.subTest(purpose=purpose, model=model, effort=effort):
                with self.assertRaisesRegex(ValueError, "at most"):
                    naming.build_subagent_name(purpose, model, effort)
        with self.assertRaisesRegex(ValueError, "at most"):
            naming.validate_subagent_name(("a" * 126) + "-b-c")
        with self.assertRaises(ValueError):
            naming.validate_subagent_name(
                "review-naming-gpt-6-sol-high",
                "implement naming", "gpt-6-sol", "high",
            )
        name = naming.build_subagent_name("review naming", "gpt-6-sol", "high")
        with self.assertRaisesRegex(ValueError, "unique"):
            naming.validate_unique_subagent_names((name, name))
        with self.assertRaises(ValueError):
            naming.native_task_name("review_naming_gpt_6_sol_high")

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
                context = self.build_hook_output({
                    "hook_event_name": "UserPromptSubmit", "prompt": prompt,
                })["hookSpecificOutput"]["additionalContext"]
                for expected in (
                    "Router self-improvement", "routing-policy review", "evaluation design",
                    "benchmark selection", "Independent-review or high-risk signals",
                    "require an independent reviewer",
                ):
                    self.assertIn(expected, context)

    def test_simple_prompt_and_missing_prompt_keep_full_contract(self) -> None:
        for event in (
            {"hook_event_name": "UserPromptSubmit", "prompt": "Fix one documentation typo."},
            {"hook_event_name": "UserPromptSubmit"},
        ):
            context = self.build_hook_output(event)["hookSpecificOutput"]["additionalContext"]
            self.assertIn("ROUTE: DIRECT — <rule/reason>", context)
            self.assertIn("ROUTE: DELEGATE — <topology/rule/model/effort/reason>", context)
            self.assertIn("decision-contract-v1", context)

    def test_worker_context_requires_structured_result(self) -> None:
        fixture = json.loads((PLUGIN_ROOT / "tests" / "fixtures" / "subagent-start.json").read_text(encoding="utf-8"))
        context = self.build_hook_output(fixture)["hookSpecificOutput"]["additionalContext"]
        for expected in ("Do not start subagents", "Task ID:", "Outcome:", "Evidence:", "Validation:", "Risks or blockers:"):
            self.assertIn(expected, context)

    def test_worker_owns_execution_and_validation_within_scope(self) -> None:
        context = self.build_hook_output({"hook_event_name": "SubagentStart"})["hookSpecificOutput"]["additionalContext"]
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
                self.assertIn("does not apply to dispatched workers", context)
                self.assertIn("minimal bounded packet", context)

    def test_worker_role_override_authorizes_work_without_controller_preflight(self) -> None:
        fixture = json.loads((PLUGIN_ROOT / "tests" / "fixtures" / "subagent-start.json").read_text(encoding="utf-8"))
        # Exercise the executable hook for the role that exposed the regression.
        fixture["agent_type"] = "verification"
        fixture["cwd"] = str(self.policy_workspace)
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
                context = self.build_hook_output({
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
            context = self.build_hook_output(fixture)["hookSpecificOutput"]["additionalContext"]
            for group in config["hooks"][event_name]:
                for handler in group["hooks"]:
                    with self.subTest(event=event_name):
                        self.assertLessEqual(len(context.encode("utf-8")), handler["additionalContextLimit"])

    def test_policy_documents_define_bounded_direct_execution_without_escape_hatches(self) -> None:
        paths = (
            PLUGIN_ROOT / "skills" / "model-router" / "SKILL.md",
            PLUGIN_ROOT / "skills" / "model-router" / "references" / "routing-policy.md",
        )
        texts = list(self.controller_contexts()) + [(str(path), path.read_text(encoding="utf-8")) for path in paths]
        for label, text in texts:
            with self.subTest(source=label):
                normalized = " ".join(text.lower().split())
                for forbidden in (
                    "all business work, including simple tasks",
                    "there is no simple-task exception",
                    "if it is unavailable, continue safely",
                ):
                    self.assertNotIn(forbidden, normalized)
                for required in (
                    "route: direct — <rule/reason>",
                    "route: delegate — <topology/rule/model/effort/reason>",
                    "one local scope", "unknown", "isolated_serial", "parallel",
                    "overlapping", "self-check", "os/tool permission barrier",
                ):
                    self.assertIn(required, normalized)

    def test_hook_context_is_instruction_policy_not_tool_interception(self) -> None:
        for event_name, context in self.controller_contexts():
            with self.subTest(event=event_name):
                self.assertIn("instruction-layer policy", context)
                self.assertIn("context injection cannot erase history, intercept tool calls, create a sandbox", context)
                self.assertIn("OS/tool permission barrier", context)
                output = self.build_hook_output({"hook_event_name": event_name})
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

    def test_audit_hook_manifest_requires_exact_commands_matcher_and_program(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plugin_root = Path(directory)
            hook_dir = plugin_root / "hooks"
            hook_dir.mkdir()
            for name in ("hooks.json", "router_hook.py", "routing_config.py",
                         "execution_decision.py", "dispatch_audit.py"):
                shutil.copy2(PLUGIN_ROOT / "hooks" / name, hook_dir / name)
            hook_path = hook_dir / "hooks.json"
            original = json.loads(hook_path.read_text(encoding="utf-8"))

            def errors_for(value):
                hook_path.write_text(json.dumps(value), encoding="utf-8")
                errors = []
                validator.validate_hook_configuration(plugin_root, errors)
                return errors

            self.assertEqual([], errors_for(original))
            for event_name in ("PreToolUse", "PostToolUse"):
                value = json.loads(json.dumps(original))
                value["hooks"][event_name][0]["matcher"] = "Bash"
                self.assertTrue(any("matcher" in error for error in errors_for(value)))
                value = json.loads(json.dumps(original))
                value["hooks"][event_name][0]["matcher"] = ".*spawn_agent.*"
                self.assertTrue(any("matcher" in error for error in errors_for(value)))
                for field in ("command", "commandWindows"):
                    value = json.loads(json.dumps(original))
                    value["hooks"][event_name][0]["hooks"][0][field] = "python other.py"
                    self.assertTrue(any("exact allowlist" in error for error in errors_for(value)))
                value = json.loads(json.dumps(original))
                del value["hooks"][event_name]
                self.assertTrue(any("audit group" in error for error in errors_for(value)))
            self.assertEqual([], errors_for(original))
            (hook_dir / "dispatch_audit.py").unlink()
            self.assertTrue(any("audit hook program is missing" in error
                                for error in errors_for(original)))

    def test_v2_post_audit_commits_observed_input_without_plaintext(self) -> None:
        event = {"hook_event_name": "PostToolUse", "tool_name": "collaborationspawn_agent",
                 "session_id": "parent-1", "turn_id": "turn-1", "tool_use_id": "call-1",
                 "tool_input": {"message": "private assignment"},
                 "tool_response": {"task_name": "/root/worker"}}
        record = dispatch_audit.build_record(event)
        self.assertEqual({"message_sha256": hashlib.sha256(b"private assignment").hexdigest(),
                          "message_bytes": len(b"private assignment")},
                         record["post_observed_input"])
        self.assertNotIn("private assignment", json.dumps(record))
        with self.assertRaisesRegex(dispatch_audit.AuditError, "missing message"):
            dispatch_audit.build_record({**event, "tool_input": {}})
        with self.assertRaisesRegex(dispatch_audit.AuditError, "bound"):
            dispatch_audit.build_record({**event, "tool_input": {"message": "s" * 65_537}})
        self.assertIsNone(dispatch_audit.build_record({**event, "tool_name": "collaborationfollowup_task"}))

    def test_unsupported_hook_event_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            hook.build_hook_output({"hook_event_name": "Stop"})

    def dispatch_contract(self, mode="explicit", model="gpt-6-sol", efforts=None) -> dict:
        evidence_root = self.policy_workspace / "dispatch-evidence"
        evidence_root.mkdir(exist_ok=True)
        spawn_path = evidence_root / "spawn-schema.json"
        catalog_path = evidence_root / "model-catalog.json"
        inheritance_path = evidence_root / "inheritance.json"
        spawn_path.write_text(json.dumps({"schema_version": 1, "kind": "spawn_schema",
            "tool": "spawn_agent", "supported_arguments": ["task_name", "message", "model", "reasoning_effort"]}), encoding="utf-8")
        catalog_path.write_text(json.dumps({"schema_version": 1, "kind": "model_catalog", "models": [
            {"id": model, "reasoning_efforts": ["low", "high"] if efforts is None else efforts}]}), encoding="utf-8")
        inheritance_path.write_text(json.dumps({"schema_version": 1, "kind": "inheritance_contract",
            "tool": "spawn_agent", "when_omitted": True,
            "inherits": {"model": model, "reasoning_effort": "high"}}), encoding="utf-8")
        evidence = [
            {"id": "schema", "kind": "spawn_schema", "source": str(spawn_path),
             "sha256": hashlib.sha256(spawn_path.read_bytes()).hexdigest(), "captured_at": "2026-09-23T04:00:00Z"},
            {"id": "catalog", "kind": "model_catalog", "source": str(catalog_path),
             "sha256": hashlib.sha256(catalog_path.read_bytes()).hexdigest(), "captured_at": "2026-09-23T04:00:00Z"},
        ]
        refs = ["catalog"]
        if mode == "verified_inheritance":
            evidence.append({"id": "inheritance", "kind": "inheritance_contract",
                             "source": str(inheritance_path),
                             "sha256": hashlib.sha256(inheritance_path.read_bytes()).hexdigest(),
                             "captured_at": "2026-09-23T04:00:00Z"})
            refs = ["inheritance"]
        canonical = naming.build_subagent_name("edit", model, "high")
        native = naming.native_task_name(canonical)
        return {
            "schema_version": 1, "dispatch_id": "edit-worker", "purpose": "edit",
            "canonical_name": canonical,
            "packet": {"worker_name": canonical, "task_id": canonical,
                       "native_task_name": native},
            "native_dispatch": {"tool": "spawn_agent", "naming_field": "task_name",
                                "native_name": native, "schema_evidence_ref": "schema"},
            "selection": {"mode": mode, "model": model, "reasoning_effort": "high",
                          "evidence_refs": refs},
            "capability_evidence": evidence,
        }

    def test_current_sol_dispatch_preserves_exact_selector_and_supported_efforts(self) -> None:
        efforts = ["low", "medium", "high", "xhigh", "max"]
        for effort in efforts:
            with self.subTest(effort=effort):
                value = self.dispatch_contract(model="gpt-6.1-sol", efforts=efforts)
                canonical = naming.build_subagent_name("edit", "gpt-6.1-sol", effort)
                native = naming.native_task_name(canonical)
                value["canonical_name"] = canonical
                value["packet"] = {"worker_name": canonical, "task_id": canonical,
                                   "native_task_name": native}
                value["native_dispatch"]["native_name"] = native
                value["selection"]["reasoning_effort"] = effort
                checked = dispatch.validate_dispatch_contract(value)
                self.assertEqual("gpt-6.1-sol", checked["selection"]["model"])
                self.assertEqual(effort, checked["selection"]["reasoning_effort"])
        inherited = self.dispatch_contract("verified_inheritance", model="gpt-6.1-sol")
        self.assertEqual(inherited, dispatch.validate_dispatch_contract(inherited))

    def test_current_sol_dispatch_rejects_missing_model_or_effort_and_ultra(self) -> None:
        value = self.dispatch_contract(model="gpt-6.1-sol", efforts=["low"])
        with self.assertRaisesRegex(dispatch.DispatchContractError, "high is unsupported for model gpt-6.1-sol"):
            dispatch.validate_dispatch_contract(value)
        value = self.dispatch_contract(model="gpt-6.1-sol")
        catalog_item = next(item for item in value["capability_evidence"] if item["kind"] == "model_catalog")
        catalog_path = Path(catalog_item["source"])
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog["models"][0]["id"] = "gpt-6-sol"
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        catalog_item["sha256"] = hashlib.sha256(catalog_path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(dispatch.DispatchContractError, "selected model is absent from model catalog"):
            dispatch.validate_dispatch_contract(value)
        value = self.dispatch_contract(model="gpt-6.1-sol")
        value["selection"]["reasoning_effort"] = "ultra"
        with self.assertRaisesRegex(dispatch.DispatchContractError, "unsupported by the router"):
            dispatch.validate_dispatch_contract(value)

    def test_dispatch_contract_requires_observed_selectors_and_resolved_values(self) -> None:
        value = self.dispatch_contract()
        self.assertEqual(value, dispatch.validate_dispatch_contract(value))

        source = Path(value["capability_evidence"][0]["source"])
        source.write_text(json.dumps({"schema_version": 1, "kind": "spawn_schema", "tool": "spawn_agent",
                                     "supported_arguments": ["task_name", "message"]}), encoding="utf-8")
        value["capability_evidence"][0]["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
        with self.assertRaisesRegex(dispatch.DispatchContractError, "selectors are absent"):
            dispatch.validate_dispatch_contract(value)

        value = self.dispatch_contract()
        value["selection"]["model"] = "model-unexposed"
        with self.assertRaisesRegex(dispatch.DispatchContractError, "unresolved placeholder"):
            dispatch.validate_dispatch_contract(value)

    def test_none_effort_is_rejected_even_when_catalog_advertises_it(self) -> None:
        value = self.dispatch_contract()
        catalog_item = next(item for item in value["capability_evidence"] if item["kind"] == "model_catalog")
        catalog_path = Path(catalog_item["source"])
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog["models"][0]["reasoning_efforts"].append("none")
        catalog["models"][0]["reasoning_efforts"].append("ultra")
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        catalog_item["sha256"] = hashlib.sha256(catalog_path.read_bytes()).hexdigest()
        value["selection"]["reasoning_effort"] = "none"
        with self.assertRaisesRegex(dispatch.DispatchContractError, "unresolved placeholder"):
            dispatch.validate_dispatch_contract(value)
        value["selection"]["reasoning_effort"] = "high"
        value["selection"]["model"] = "none"
        with self.assertRaisesRegex(dispatch.DispatchContractError, "unresolved placeholder"):
            dispatch.validate_dispatch_contract(value)
        value["selection"]["model"] = "gpt-6-sol"
        value["selection"]["reasoning_effort"] = "default"
        with self.assertRaisesRegex(dispatch.DispatchContractError, "unresolved placeholder"):
            dispatch.validate_dispatch_contract(value)
        value["selection"]["reasoning_effort"] = "ultra"
        with self.assertRaisesRegex(dispatch.DispatchContractError, "unsupported by the router"):
            dispatch.validate_dispatch_contract(value)

    def test_dispatch_contract_requires_capability_proof_for_inheritance(self) -> None:
        value = self.dispatch_contract("verified_inheritance")
        self.assertEqual(value, dispatch.validate_dispatch_contract(value))
        value["selection"]["evidence_refs"] = ["catalog"]
        with self.assertRaisesRegex(dispatch.DispatchContractError, "inheritance_contract"):
            dispatch.validate_dispatch_contract(value)

    def test_dispatch_contract_rejects_nonexistent_tampered_and_impossible_capability_evidence(self) -> None:
        value = self.dispatch_contract()
        value["capability_evidence"][0]["source"] = str(self.policy_workspace / "missing.json")
        with self.assertRaisesRegex(dispatch.DispatchContractError, "does not exist"):
            dispatch.validate_dispatch_contract(value)

        value = self.dispatch_contract()
        value["capability_evidence"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(dispatch.DispatchContractError, "nonzero"):
            dispatch.validate_dispatch_contract(value)

        value = self.dispatch_contract()
        value["selection"]["reasoning_effort"] = "ultra"
        value["canonical_name"] = "edit-gpt-6-sol-ultra"
        value["packet"] = {"worker_name": value["canonical_name"], "task_id": value["canonical_name"],
                           "native_task_name": "edit_gpt_6_sol_ultra"}
        value["native_dispatch"]["native_name"] = "edit_gpt_6_sol_ultra"
        with self.assertRaisesRegex(dispatch.DispatchContractError, "unsupported"):
            dispatch.validate_dispatch_contract(value)

        value = self.dispatch_contract("verified_inheritance")
        value["selection"]["model"] = "gpt-6-astra"
        with self.assertRaisesRegex(dispatch.DispatchContractError, "contradict"):
            dispatch.validate_dispatch_contract(value)

    def test_dispatch_evidence_is_bounded_and_hashes_the_parsed_bytes(self) -> None:
        value = self.dispatch_contract()
        source = Path(value["capability_evidence"][0]["source"])
        original_read = dispatch.read_evidence
        reads = []

        def read_then_replace(path):
            raw = original_read(path)
            reads.append(path)
            if path == source:
                source.write_text('{"schema_version":1,"kind":"spawn_schema",'
                                  '"tool":"wrong","supported_arguments":["message"]}',
                                  encoding="utf-8")
            return raw

        with mock.patch.object(dispatch, "read_evidence", side_effect=read_then_replace):
            self.assertEqual(value, dispatch.validate_dispatch_contract(value))
        self.assertEqual(1, reads.count(source))

        value = self.dispatch_contract()
        source = Path(value["capability_evidence"][0]["source"])
        source.write_bytes(b"x" * (dispatch.MAX_EVIDENCE_BYTES + 1))
        value["capability_evidence"][0]["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
        with self.assertRaisesRegex(dispatch.DispatchContractError, "1\\.\\.1048576 bytes"):
            dispatch.validate_dispatch_contract(value)

        value = self.dispatch_contract()
        source = Path(value["capability_evidence"][0]["source"])
        source.write_text('{"schema_version":1,"kind":"spawn_schema",'
                          '"tool":"spawn_agent","tool":"spawn_agent",'
                          '"supported_arguments":["task_name","message","model","reasoning_effort"]}',
                          encoding="utf-8")
        value["capability_evidence"][0]["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
        with self.assertRaisesRegex(dispatch.DispatchContractError, "duplicate JSON key"):
            dispatch.validate_dispatch_contract(value)

    def role_dispatch_contract(self) -> dict:
        root = self.policy_workspace / "role-evidence"
        root.mkdir(exist_ok=True)
        role_file = root / "astra-role.toml"
        role_file.write_text('model = "gpt-6-astra"\nmodel_reasoning_effort = "xhigh"\n', encoding="utf-8")
        binding = root / "active-config.toml"
        binding.write_text('[agents.default]\ndescription = "Benchmark hard-kernel worker."\n'
                           f'config_file = "{role_file.as_posix()}"\n', encoding="utf-8")
        binary = root / "codex.exe"
        binary.write_bytes(b"synthetic-cli-for-contract-tests")
        runtime = root / "runtime.json"
        runtime.write_text(json.dumps({"schema_version": 1, "kind": "role_runtime",
            "cli_version": "codex-cli 0.144.1", "tool": "collaborationspawn_agent",
            "default_role": "default",
            "role_file_precedence": "role_file_over_explicit_spawn_and_agents_defaults",
            "agent_type_omitted_uses_default": True}), encoding="utf-8")
        schema = root / "spawn.json"
        schema.write_text(json.dumps({"schema_version": 1, "kind": "spawn_schema",
            "tool": "collaborationspawn_agent",
            "supported_arguments": ["task_name", "message", "fork_turns"]}), encoding="utf-8")
        review = root / "review.json"
        review.write_text('{"synthetic_test_only":true}', encoding="utf-8")
        binary_hash = hashlib.sha256(binary.read_bytes()).hexdigest()
        calibration = root / "calibration.json"
        calibration.write_text(json.dumps({"schema_version": 1, "kind": "role_calibration",
            "cli_version": "codex-cli 0.144.1", "tool": "collaborationspawn_agent",
            "role_name": "default", "binding_sha256": hashlib.sha256(binding.read_bytes()).hexdigest(),
            "role_file_sha256": hashlib.sha256(role_file.read_bytes()).hexdigest(),
            "binary_sha256": binary_hash,
            "spawn_arguments": {"fork_turns": "none", "selector_fields": [], "agent_type": None},
            "child": {"model": "gpt-6-astra", "reasoning_effort": "xhigh"},
            "review": {"status": "passed", "reviewer": "synthetic-test-reviewer",
                       "source": str(review), "sha256": hashlib.sha256(review.read_bytes()).hexdigest()}}),
            encoding="utf-8")
        paths = {"schema": ("spawn_schema", schema), "binding": ("role_binding", binding),
                 "role": ("role_file", role_file), "runtime": ("role_runtime", runtime),
                 "calibration": ("role_calibration", calibration)}
        evidence = [{"id": key, "kind": kind, "source": str(path),
                     "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                     "captured_at": "2026-09-24T00:00:00Z"}
                    for key, (kind, path) in paths.items()]
        canonical = "design-gpt-6-astra-xhigh"
        return {"schema_version": 1, "dispatch_id": "design-kernel", "purpose": "design",
                "canonical_name": canonical,
                "packet": {"worker_name": canonical, "task_id": canonical,
                           "native_task_name": "design_gpt_6_astra_xhigh"},
                "native_dispatch": {"tool": "collaborationspawn_agent",
                                    "naming_field": "task_name",
                                    "native_name": "design_gpt_6_astra_xhigh",
                                    "schema_evidence_ref": "schema",
                                    "planned_arguments": {"fork_turns": "none", "model": None,
                                                          "reasoning_effort": None,
                                                          "agent_type": None,
                                                          "message_sha256": hashlib.sha256(b"bounded packet").hexdigest(),
                                                          "message_bytes": len(b"bounded packet")}},
                "selection": {"mode": "verified_role_config", "model": "gpt-6-astra",
                              "reasoning_effort": "xhigh",
                              "evidence_refs": ["binding", "role", "runtime", "calibration"],
                              "role": {"name": "default", "binding_ref": "binding",
                                       "file_ref": "role", "runtime_ref": "runtime",
                                       "calibration_ref": "calibration",
                                       "binary_path": str(binary), "binary_sha256": binary_hash}},
                "capability_evidence": evidence}

    def test_verified_role_config_is_distinct_and_requires_exact_bound_evidence(self) -> None:
        value = self.role_dispatch_contract()
        self.assertEqual(value, dispatch.validate_dispatch_structure(value))
        with self.assertRaisesRegex(dispatch.RoleAuthorizationUnavailable, "structural evidence only"):
            dispatch.validate_dispatch_contract(value)
        contract_path = self.policy_workspace / "synthetic-role-contract.json"
        contract_path.write_text(json.dumps(value), encoding="utf-8")
        result = subprocess.run([sys.executable, str(PLUGIN_ROOT / "hooks" / "dispatch_contract.py"),
                                 "--input", str(contract_path)], capture_output=True, text=True)
        self.assertNotEqual(0, result.returncode)
        blocked = json.loads(result.stdout)
        self.assertEqual("blocked", blocked["status"])
        self.assertEqual("role-runtime-authorization-unverified", blocked["blocker_kind"])
        value["native_dispatch"]["planned_arguments"]["model"] = "gpt-6-astra"
        with self.assertRaisesRegex(dispatch.DispatchContractError, "omitted selectors"):
            dispatch.validate_dispatch_structure(value)
        value = self.role_dispatch_contract()
        value["native_dispatch"]["planned_arguments"]["fork_turns"] = "all"
        with self.assertRaisesRegex(dispatch.DispatchContractError, "fork_turns none"):
            dispatch.validate_dispatch_structure(value)
        value = self.role_dispatch_contract()
        value["native_dispatch"]["planned_arguments"]["agent_type"] = "reviewer"
        with self.assertRaisesRegex(dispatch.DispatchContractError, "omitted selectors"):
            dispatch.validate_dispatch_structure(value)
        value = self.role_dispatch_contract()
        value["selection"]["evidence_refs"].remove("calibration")
        with self.assertRaisesRegex(dispatch.DispatchContractError, "exactly four"):
            dispatch.validate_dispatch_structure(value)
        value = self.role_dispatch_contract()
        role_path = Path(next(item["source"] for item in value["capability_evidence"]
                              if item["kind"] == "role_file"))
        role_path.write_text('model = "gpt-6-sol"\nmodel_reasoning_effort = "high"\n', encoding="utf-8")
        with self.assertRaisesRegex(dispatch.DispatchContractError, "hash mismatch"):
            dispatch.validate_dispatch_structure(value)
        value = self.role_dispatch_contract()
        binding = next(item for item in value["capability_evidence"]
                       if item["kind"] == "role_binding")
        binding_path = Path(binding["source"])
        binding_path.write_text('[agents.default]\ndescription = "Benchmark hard-kernel worker."\n'
                                'config_file = "C:/different-role.toml"\n', encoding="utf-8")
        binding["sha256"] = hashlib.sha256(binding_path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(dispatch.DispatchContractError, "binding differs"):
            dispatch.validate_dispatch_structure(value)
        value = self.role_dispatch_contract()
        Path(value["selection"]["role"]["binary_path"]).write_bytes(b"changed")
        with self.assertRaisesRegex(dispatch.DispatchContractError, "binary is missing or changed"):
            dispatch.validate_dispatch_structure(value)

    def test_verified_role_config_rejects_unknown_runtime_and_wrong_child(self) -> None:
        value = self.role_dispatch_contract()
        runtime = next(item for item in value["capability_evidence"] if item["kind"] == "role_runtime")
        source = Path(runtime["source"])
        record = json.loads(source.read_text(encoding="utf-8"))
        record["cli_version"] = "codex-cli 0.155.0"
        source.write_text(json.dumps(record), encoding="utf-8")
        runtime["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
        with self.assertRaisesRegex(dispatch.DispatchContractError, "audited allowlist"):
            dispatch.validate_dispatch_structure(value)
        value = self.role_dispatch_contract()
        calibration = next(item for item in value["capability_evidence"]
                           if item["kind"] == "role_calibration")
        source = Path(calibration["source"])
        record = json.loads(source.read_text(encoding="utf-8"))
        record["child"]["reasoning_effort"] = "high"
        source.write_text(json.dumps(record), encoding="utf-8")
        calibration["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
        with self.assertRaisesRegex(dispatch.DispatchContractError, "child selectors"):
            dispatch.validate_dispatch_structure(value)
        value = self.role_dispatch_contract()
        calibration = next(item for item in value["capability_evidence"]
                           if item["kind"] == "role_calibration")
        source = Path(calibration["source"])
        record = json.loads(source.read_text(encoding="utf-8"))
        record["review"]["status"] = "pending"
        source.write_text(json.dumps(record), encoding="utf-8")
        calibration["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
        with self.assertRaisesRegex(dispatch.DispatchContractError, "independent review"):
            dispatch.validate_dispatch_structure(value)


if __name__ == "__main__":
    unittest.main()
