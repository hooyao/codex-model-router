"""Synthetic raw captures for the live validator. These tests make no model calls."""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from xml.sax.saxutils import escape

from evals.scripts import activation_preflight as preflight, live_evidence as live
from router_hook import controller_session_context
from routing_config import routing_context_block


ROOT = Path(__file__).resolve().parents[2]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def write_lines(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(value) for value in values) + "\n", encoding="utf-8")


def event(kind, payload, second=0):
    return {"type": kind, "payload": payload, "timestamp": f"2099-01-01T00:00:{second:02d}Z"}


def message(role, text):
    return event("response_item", {"type": "message", "role": role,
                                   "content": [{"type": "input_text", "text": text}]})


def route(text):
    return event("event_msg", {"type": "agent_message", "message": text})


def metadata(thread_id, cwd, parent=None, agent_path=None):
    return {"id": thread_id, "timestamp": "2099-01-01T00:00:00Z", "cwd": str(cwd),
            "parent_thread_id": parent, "agent_path": agent_path}


def index_record(path, relation):
    meta = live.session_meta(path)
    return {"relation": relation, "thread_id": meta["id"], "parent_thread_id": meta["parent_thread_id"],
            "agent_path": meta["agent_path"], "source_path": str(path), "source_sha256": live.sha256(path),
            "source_evidence_lines": [1]}


class LiveEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.transcripts, self.pairs, self.actual = {}, {}, {}
        for case in live.CASES:
            self.make_case(case)
        self.make_activation()

    def make_case(self, case):
        workspace = self.root / "workspaces" / case
        shutil.copytree(ROOT / "evals" / "fixtures" / case / "reference", workspace)
        shutil.copytree(workspace, self.root / "results" / case)
        self.actual[case] = workspace
        session_dir = self.root / "session-evidence" / case
        names = {"direct-small-control": [],
                 "investigation-reuse": ["investigate", "implement"],
                 "serial-escalation": ["plan", "schema", "api", "contract"],
                 "parallel-disjoint": ["alpha", "beta", "gamma"],
                 "architecture-review": ["author", "review"]}[case]
        topology = "PARALLEL" if case == "parallel-disjoint" else "ISOLATED_SERIAL"
        route_text = "ROUTE: DIRECT — bounded" if case == "direct-small-control" else \
            "ROUTE: DELEGATE — " + topology
        if case == "architecture-review":
            route_text += " INDEPENDENT_REVIEW"
        parent_id = case + "-parent"
        parent_meta = metadata(parent_id, workspace)
        parent = [event("session_meta", parent_meta),
                  event("turn_context", {"model": "gpt-5.6-sol", "effort": "high"}),
                  event("event_msg", {"type": "task_started", "turn_id": case + "-turn"}, 1)]
        if case == "serial-escalation":
            parent += [route("ROUTE: DIRECT — bounded"),
                       event("response_item", {"type": "function_call", "name": "exec_command",
                                               "arguments": '{"cmd":"read request.md"}', "call_id": "direct"})]
            route_text += " scope-expanded"
        parent.append(route(route_text))
        child_pairs = []
        for number, name in enumerate(names):
            canonical = name + "-gpt-5-6-sol-high"
            native = canonical.replace("-", "_")
            agent_path = "/root/" + native
            packet = f"Worker name: {canonical}\nTask ID: {canonical}\nNative task name: {native}"
            if case == "investigation-reuse" and name == "implement":
                packet += "\nReceipt: " + "a" * 64
            call = "spawn-" + str(number)
            parent.append(event("response_item", {"type": "function_call", "name": "spawn_agent", "call_id": call,
                          "arguments": json.dumps({"task_name": native, "message": packet,
                                                   "model": "gpt-5.6-sol", "reasoning_effort": "high"})}))
            parent.append(event("response_item", {"type": "function_call_output", "call_id": call,
                                                   "output": json.dumps({"task_name": agent_path})}))
            child_id = case + "-" + str(number)
            child_meta = metadata(child_id, workspace, parent_id, agent_path)
            final = packet + "\nOutcome: completed"
            if case == "parallel-disjoint":
                final += f"\nArtifact: reports/{name}.md"
            if name == "review":
                final += "\nVerdict: PASS"
            start = 2 if case == "parallel-disjoint" else 2 + number * 5
            child = [event("session_meta", child_meta),
                     event("turn_context", {"model": "gpt-5.6-sol", "effort": "high"}),
                     event("event_msg", {"type": "task_started", "turn_id": "turn"}, start),
                     event("event_msg", {"type": "task_complete", "turn_id": "turn",
                                         "last_agent_message": final}, start + 4)]
            path = session_dir / (name + ".jsonl")
            write_lines(path, child)
            child_pairs.append((path, child_meta))
            if case == "investigation-reuse" and name == "investigate":
                parent.append(event("response_item", {"type": "agent_message", "author": agent_path,
                               "content": [{"type": "text", "text": "Receipt: " + "a" * 64}]}))
        final = "Completed " + case + "."
        parent.append(event("event_msg", {"type": "task_complete", "turn_id": case + "-turn",
                                          "last_agent_message": final}, 30))
        parent_path = session_dir / "parent.jsonl"
        write_lines(parent_path, parent)
        write_json(session_dir / "session-index.json", [index_record(parent_path, "parent"),
                   *[index_record(path, "child") for path, _meta in child_pairs]])
        transcript = self.root / "raw" / (case + ".jsonl")
        compact = [{"type": "thread.started", "thread_id": parent_id}]
        compact += [{"type": "item.completed", "item": {"type": "agent_message", "text": item["text"]}}
                    for item in live.route_events_from_items(live.json_lines(parent_path))]
        compact.append({"type": "item.completed", "item": {"type": "agent_message", "text": final}})
        compact.append({"type": "turn.completed", "usage": {"input_tokens": 2, "output_tokens": 3}})
        write_lines(transcript, compact)
        self.transcripts[case], self.pairs[case] = transcript, ((parent_path, parent_meta), child_pairs)

    def make_activation(self):
        raw = self.root / "raw"
        self.spec = live.parse_json(ROOT / "evals" / "live" / "fresh-activation-probe-v3.json")
        shutil.copyfile(ROOT / "evals" / "live" / "fresh-activation-probe-v3.json", raw / "activation-spec.json")
        self.workspace = self.root / "activation-workspace"
        self.workspace.mkdir()
        note = self.spec["expected_note_contents"]
        (self.workspace / "note.txt").write_bytes(note.encode())
        (raw / "activation-note-before.txt").write_bytes(note.encode())
        config = live.parse_json(ROOT / "plugins" / "codex-model-router" / "defaults" / "default-routing.json")
        self.config_path = self.workspace / ".codex-model-router" / "routing.json"
        write_json(self.config_path, config)
        shutil.copyfile(self.config_path, raw / "activation-routing.json")
        manifest = ROOT / "plugins" / "codex-model-router" / ".codex-plugin" / "plugin.json"
        shutil.copyfile(manifest, raw / "plugin-manifest.json")
        (raw / "codex-version.txt").write_text("codex-test-version", encoding="utf-8")
        facts = {"executable": sys.executable, "version": ".".join(map(str, sys.version_info[:3])),
                 "encodings_imported": True}
        with mock.patch.object(preflight.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, json.dumps(facts), "")):
            capture = preflight.capture_runtime(self.workspace)
        write_json(raw / "activation-python-preflight.json", capture)
        write_json(raw / "activation-environment.json", {
            "resolved_python_executable": sys.executable,
            "resolved_python_executable_sha256": live.sha256(Path(sys.executable)),
            "python_version": facts["version"], "python_encodings_import_exit_code": 0,
            "sandbox_mode_requested": "workspace-write", "sandbox_probe_exit_code": 0,
            "codex_version": "codex-test-version", "plugin_manifest_sha256": live.sha256(manifest)})
        write_json(raw / "activation-hook-provenance.json", {"schema_version": 1, "hook_event": "SessionStart",
                   "thread_id": "activation", "source_config_path": str(self.config_path),
                   "source_config_sha256": live.sha256(self.config_path),
                   "captured_config_sha256": live.sha256(self.config_path)})
        write_json(raw / "spawn-schema.json", {"schema_version": 1, "kind": "spawn_schema", "tool": "spawn_agent",
                   "supported_arguments": ["task_name", "message", "model", "reasoning_effort"]})
        write_json(raw / "model-catalog.json", {"schema_version": 1, "kind": "model_catalog",
                   "models": [{"id": "gpt-5.6-sol", "reasoning_efforts": ["high"]}]})
        self.activation_items = [event("session_meta", metadata("activation", self.workspace)),
            message("developer", controller_session_context("gpt-5.6-sol", routing_context_block(self.config_path, config))),
            message("user", self.spec["prompt"]),
            event("event_msg", {"type": "user_message", "message": self.spec["prompt"]}),
            route("ROUTE: DIRECT — bounded"),
            event("event_msg", {"type": "task_started", "turn_id": "activation-turn"}, 1),
            event("response_item", {"type": "function_call", "name": "exec_command",
                                    "arguments": '{"cmd":"Get-Content note.txt"}', "call_id": "read"}),
            event("event_msg", {"type": "task_complete", "turn_id": "activation-turn",
                                "last_agent_message": "unchanged"}, 5)]
        self.write_activation_session()
        self.activation_transcript = [{"type": "thread.started", "thread_id": "activation"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "ROUTE: DIRECT — bounded"}},
            {"type": "item.completed", "item": {"type": "command_execution", "command": "Get-Content note.txt",
             "status": "completed", "exit_code": 0, "aggregated_output": note}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "unchanged"}},
            {"type": "turn.completed", "usage": {"input_tokens": 2, "output_tokens": 3}}]
        write_lines(raw / "activation.jsonl", self.activation_transcript)
        (raw / "activation-final.txt").write_text("unchanged", encoding="utf-8")

    def write_activation_session(self):
        path = self.root / "session-evidence" / "activation" / "parent.jsonl"
        write_lines(path, self.activation_items)
        write_json(path.parent / "session-index.json", [index_record(path, "parent")])

    def rewrite_case(self, case, parent_items=None, compact=None):
        parent_pair, children = self.pairs[case]
        if parent_items is not None:
            write_lines(parent_pair[0], parent_items)
        write_json(parent_pair[0].parent / "session-index.json", [index_record(parent_pair[0], "parent"),
                   *[index_record(path, "child") for path, _meta in children]])
        if compact is not None:
            write_lines(self.transcripts[case], compact)

    def assert_case_fails(self, case, check, terminal_failure=False):
        report = self.report()
        observation = next(item for item in report["observations"] if item["case_id"] == case)
        self.assertIn(check, [item["name"] for item in observation["process_validation"]["checks"]
                              if item["status"] == "fail"])
        self.assertFalse(report["acceptance"]["campaign_pass"])
        self.assertEqual(len(live.CASES), report["acceptance"]["scheduled_runs"])
        if terminal_failure:
            self.assertEqual("pass", observation["artifact_validation"]["status"])
            self.assertEqual("failed", observation["outcome"])
            self.assertEqual(len(live.CASES) - 1, report["acceptance"]["completed_runs"])
        self.assertFalse(self.validate_report(report)["campaign_pass"])
        return report

    def report(self):
        return live.build_report(self.root, ROOT, self.transcripts, self.pairs, self.actual, False)

    def validate_report(self, report):
        path = self.root / "report.json"
        write_json(path, report)
        return live.validate(path)

    def assert_activation_fails(self, check=None):
        gate = live.activation_diagnostics(self.root)
        self.assertEqual("fail", gate["status"], gate)
        if check:
            self.assertIn(check, [item["name"] for item in gate["checks"] if item["status"] == "fail"])

    def test_complete_source_derived_campaign_passes(self):
        report = self.report()
        self.assertTrue(self.validate_report(report)["campaign_pass"], report["activation_gate"])

    def test_report_cannot_swap_arbitrary_oracle_and_artifact_refs(self):
        report = self.report()
        arbitrary = self.root / "arbitrary"
        arbitrary.mkdir()
        (arbitrary / "fake.txt").write_text("wrong scenario", encoding="utf-8")
        files = live.snapshot(arbitrary)
        for field in ("artifact_tree", "oracle_tree"):
            with self.subTest(field=field):
                altered = copy.deepcopy(report)
                altered["observations"][0]["evidence"][field] = {
                    "path": str(arbitrary), "files": files, "tree_sha256": live.tree_sha256(files)}
                with self.assertRaisesRegex(live.LiveEvidenceError, "canonical scenario|frozen case oracle"):
                    self.validate_report(altered)

    def test_cross_case_oracle_and_artifact_reference_swaps_fail(self):
        report = self.report()
        for field in ("artifact_tree", "oracle_tree"):
            altered = copy.deepcopy(report)
            altered["observations"][0]["evidence"][field] = report["observations"][1]["evidence"][field]
            with self.subTest(field=field), self.assertRaises(live.LiveEvidenceError):
                self.validate_report(altered)

    def test_frozen_reference_tree_tamper_is_rejected(self):
        repository = self.root / "oracle-repository"
        shutil.copytree(ROOT / "evals" / "fixtures", repository / "evals" / "fixtures")
        for name in ("cases.json", "benchmark.json"):
            shutil.copyfile(ROOT / "evals" / name, repository / "evals" / name)
        reference = repository / "evals" / "fixtures" / "architecture-review" / "reference" / "decision.json"
        reference.write_text("{}", encoding="utf-8")
        with mock.patch.object(live, "REPO_ROOT", repository), self.assertRaises(ValueError):
            live.frozen_case("architecture-review")

    def test_extra_empty_directory_is_part_of_artifact_grade(self):
        case = "architecture-review"
        (self.actual[case] / "unexpected").mkdir()
        (self.root / "results" / case / "unexpected").mkdir()
        report = self.report()
        result = self.validate_report(report)
        self.assertFalse(result["artifact_pass"])
        self.assertFalse(result["campaign_pass"])

    def test_cross_transcript_session_set_swap_fails(self):
        report = self.report()
        target, other = report["observations"][:2]
        for key in ("session_sources", "session_index"):
            target["evidence"][key] = other["evidence"][key]
        # Even putting the other valid index at the target's canonical path cannot bind it.
        target_index = self.root / "session-evidence" / target["case_id"] / "session-index.json"
        shutil.copyfile(self.root / other["evidence"]["session_index"]["path"], target_index)
        target["evidence"]["session_index"] = live.evidence_ref(self.root, target_index)
        with self.assertRaisesRegex(live.LiveEvidenceError, "metadata/ancestry|transcript parent"):
            self.validate_report(report)

    def test_child_parent_and_native_result_must_match(self):
        case = "architecture-review"
        parent_pair, children = self.pairs[case]
        parent = live.session_record(*parent_pair)
        sessions = [live.session_record(*pair) for pair in children]
        items = live.json_lines(parent_pair[0])
        spawns = live.spawn_events(items)
        sessions[0]["parent_thread_id"] = "different-parent"
        with self.assertRaisesRegex(live.LiveEvidenceError, "ancestry"):
            live.validate_session_ancestry(parent, sessions, items, spawns)
        sessions[0]["parent_thread_id"] = parent["thread_id"]
        altered = copy.deepcopy(items)
        for _line, value in altered:
            if value.get("payload", {}).get("type") == "function_call_output":
                value["payload"]["output"] = '{"task_name":"/root/unrelated"}'
                break
        with self.assertRaisesRegex(live.LiveEvidenceError, "result/path"):
            live.validate_session_ancestry(parent, sessions, altered, spawns)

    def test_direct_then_spawns_then_late_delegate_fails(self):
        routes = [{"line": 2, "text": "ROUTE: DIRECT — bounded"},
                  {"line": 20, "text": "ROUTE: DELEGATE — ISOLATED_SERIAL scope-expanded"}]
        self.assertFalse(live.routes_precede_spawns(routes, [{"line": 10}], 3, "ISOLATED_SERIAL"))
        self.assertTrue(live.routes_precede_spawns(routes, [{"line": 21}], 3, "ISOLATED_SERIAL"))
        self.assertFalse(live.routes_precede_spawns(routes, [{"line": 21}], 3, "PARALLEL"))
        self.assertFalse(live.routes_precede_spawns(routes, [{"line": 21}], [3, 21], "ISOLATED_SERIAL"))
        routes.append({"line": 22, "text": "ROUTE: DIRECT — resumed"})
        self.assertFalse(live.routes_precede_spawns(routes, [{"line": 23}], 3, "ISOLATED_SERIAL"))

    def test_bundled_decision_resolver_is_routing_transport(self):
        resolver = r'python C:\plugin\hooks\execution_decision.py --config C:\work\routing.json'
        items = [
            (1, event("response_item", {"type": "custom_tool_call", "name": "exec", "input": resolver})),
            (2, route("ROUTE: DIRECT — bounded")),
            (3, event("response_item", {"type": "custom_tool_call", "name": "exec",
                                         "input": "Get-Content note.txt"})),
        ]
        self.assertEqual([3], live.parent_business_lines(items))
        transcript = self.root / "routing-transport.jsonl"
        write_lines(transcript, [
            {"type": "item.completed", "item": {"type": "command_execution", "command": resolver,
                                                   "status": "completed", "exit_code": 0}},
            {"type": "item.completed", "item": {"type": "agent_message",
                                                   "text": "ROUTE: DIRECT — bounded"}},
            {"type": "item.completed", "item": {"type": "command_execution",
                                                   "command": "Get-Content note.txt",
                                                   "status": "completed", "exit_code": 0}},
        ])
        observed = live.transcript_observations(transcript)
        self.assertEqual(2, observed["route_events"][0]["line"])
        self.assertEqual(3, observed["first_business_line"])

    def test_delegation_cannot_return_controller_business_to_direct(self):
        for case in (item for item in live.CASES if item != "direct-small-control"):
            original = [value for _line, value in live.json_lines(self.pairs[case][0][0])]
            compact = [value for _line, value in live.json_lines(self.transcripts[case])]
            for action in (False, True):
                with self.subTest(case=case, business_action=action):
                    parent = copy.deepcopy(original)
                    resumed = "ROUTE: DIRECT — verify/recover locally"
                    parent.insert(-1, route(resumed))
                    if action:
                        parent.insert(-1, event("response_item", {"type": "function_call", "name": "exec_command",
                            "arguments": '{"cmd":"repair and verify artifacts"}', "call_id": "direct-recovery"}))
                    altered = copy.deepcopy(compact)
                    altered.insert(-2, {"type": "item.completed", "item": {"type": "agent_message", "text": resumed}})
                    self.rewrite_case(case, parent, altered)
                    self.assert_case_fails(case, "route-before-worker-business")
            self.rewrite_case(case, original, compact)

    def test_delegate_direct_delegate_sandwich_cannot_hide_controller_recovery(self):
        case = "serial-escalation"
        parent = [value for _line, value in live.json_lines(self.pairs[case][0][0])]
        compact = [value for _line, value in live.json_lines(self.transcripts[case])]
        delegated = next(i for i, value in enumerate(parent) if
                         value.get("payload", {}).get("message", "").startswith("ROUTE: DELEGATE"))
        direct = "ROUTE: DIRECT — repair the expanded scope"
        parent[delegated + 1:delegated + 1] = [route(direct),
            event("response_item", {"type": "function_call", "name": "exec_command",
                                    "arguments": '{"cmd":"repair artifacts"}', "call_id": "repair"}),
            copy.deepcopy(parent[delegated])]
        compact[3:3] = [{"type": "item.completed", "item": {"type": "agent_message", "text": text}}
                        for text in (direct, parent[delegated]["payload"]["message"])]
        self.rewrite_case(case, parent, compact)
        self.assert_case_fails(case, "route-before-worker-business")

    def test_every_case_requires_successful_consistent_terminal_evidence(self):
        for case in live.CASES:
            original = [value for _line, value in live.json_lines(self.transcripts[case])]
            for mode in ("turn.failed", "turn.cancelled", "turn.canceled", "error", "missing", "duplicate",
                         "failed-then-completed", "cancelled-then-completed", "mismatched-final",
                         "mismatched-turn", "contradictory-status", "post-terminal-business"):
                with self.subTest(case=case, mode=mode):
                    compact = copy.deepcopy(original)
                    if mode == "missing":
                        compact.pop()
                    elif mode == "duplicate":
                        compact.append(copy.deepcopy(compact[-1]))
                    elif mode.endswith("-then-completed"):
                        compact.insert(-1, {"type": "turn." + mode.split("-")[0]})
                    elif mode == "mismatched-final":
                        compact[-2]["item"]["text"] = "Different completion."
                    elif mode == "mismatched-turn":
                        compact[-1]["turn_id"] = "unrelated-turn"
                    elif mode == "contradictory-status":
                        compact[-1]["status"] = "failed"
                    elif mode == "post-terminal-business":
                        compact.append({"type": "item.started", "item": {"type": "command_execution"}})
                    else:
                        compact[-1]["type"] = mode
                    self.rewrite_case(case, compact=compact)
                    report = self.assert_case_fails(case, "successful-terminal-outcome", terminal_failure=True)
                    if mode == "missing":
                        observation = next(item for item in report["observations"] if item["case_id"] == case)
                        observation["outcome"] = "completed"
                        report["acceptance"] = live.recompute_acceptance(report)
                        with self.assertRaisesRegex(live.LiveEvidenceError, "outcome disagrees"):
                            self.validate_report(report)
            self.rewrite_case(case, compact=original)

    def test_cli_success_requires_matching_successful_parent_completion(self):
        case = "architecture-review"
        original = [value for _line, value in live.json_lines(self.pairs[case][0][0])]
        for mode in ("missing", "task_failed", "task_cancelled", "turn_aborted", "error", "duplicate",
                     "wrong-turn", "missing-start", "wrong-final", "contradictory-status", "post-completion-action"):
            with self.subTest(mode=mode):
                parent = copy.deepcopy(original)
                if mode == "missing":
                    parent.pop()
                elif mode == "duplicate":
                    parent.append(copy.deepcopy(parent[-1]))
                elif mode == "missing-start":
                    parent = [value for value in parent if value.get("payload", {}).get("type") != "task_started"]
                elif mode == "wrong-turn":
                    parent[-1]["payload"]["turn_id"] = "unrelated-turn"
                elif mode == "wrong-final":
                    parent[-1]["payload"]["last_agent_message"] = "Different completion."
                elif mode == "contradictory-status":
                    parent[-1]["payload"]["status"] = "failed"
                elif mode == "post-completion-action":
                    parent.append(event("response_item", {"type": "function_call", "name": "wait_agent"}))
                else:
                    parent[-1]["payload"]["type"] = mode
                self.rewrite_case(case, parent_items=parent)
                self.assert_case_fails(case, "successful-terminal-outcome", terminal_failure=True)

    def test_reviewer_fail_with_pass_substring_never_passes(self):
        for text in ("FAIL: This must not PASS", "Verdict: FAIL", "PASS", "Verdict: PASS\nVerdict: FAIL",
                     "Verdict: PASS\nActually FAIL", "Verdict: PASS\nVerdict: PASS", "Verdict: PASS maybe"):
            with self.subTest(text=text):
                self.assertEqual("fail", live.reviewer_verdict(text))
        self.assertEqual("pass", live.reviewer_verdict("Worker name: reviewer\nVerdict: PASS\nEvidence: checked."))

    def test_activation_spec_cannot_supply_its_own_hinted_prompt(self):
        self.spec["prompt"] += " Use the model-router skill."
        self.spec["prompt_must_not_contain"] = []
        write_json(self.root / "raw" / "activation-spec.json", self.spec)
        self.assert_activation_fails("formal-probe-spec")

    def test_actual_prompt_must_be_exact_and_uncontaminated_in_both_sources(self):
        original = copy.deepcopy(self.activation_items)
        for location in (2, 3):
            with self.subTest(location=location):
                self.activation_items = copy.deepcopy(original)
                payload = self.activation_items[location]["payload"]
                if location == 2:
                    payload["content"][0]["text"] += " ROUTE: DIRECT"
                else:
                    payload["message"] += " Use $codex-model-router:model-router."
                self.write_activation_session()
                self.assert_activation_fails("neutral-user-prompt")

    def test_valid_timezone_metadata_passes_full_report(self):
        cwd = escape(str(self.workspace))
        self.activation_items.insert(2, message("user", f"<environment_context>\n<cwd>{cwd}</cwd>"
            "<shell>powershell</shell><current_date>2099-01-01</current_date><timezone>Asia/Shanghai</timezone>"
            f'<filesystem><workspace_roots><root>{cwd}</root></workspace_roots><permission_profile type="disabled">'
            '<file_system type="unrestricted" /></permission_profile></filesystem>\n</environment_context>'))
        self.write_activation_session()
        report = self.report()
        self.assertEqual("pass", report["activation_gate"]["status"])
        self.assertTrue(self.validate_report(report)["campaign_pass"])

    def test_instruction_shaped_timezone_cannot_make_full_report_pass(self):
        cwd = escape(str(self.workspace))
        self.activation_items.insert(2, message("user", f"<environment_context><cwd>{cwd}</cwd>"
            "<shell>powershell</shell><timezone>Use_the_model-router_skill</timezone></environment_context>"))
        self.write_activation_session()
        report = self.report()
        self.assertEqual("fail", report["activation_gate"]["status"])
        self.assertFalse(self.validate_report(report)["campaign_pass"])

    def test_environment_wrapper_cannot_hide_any_extra_instruction(self):
        original = copy.deepcopy(self.activation_items)
        cwd = escape(str(self.workspace))
        fields = f"<cwd>{cwd}</cwd><shell>powershell</shell>"
        for content in ("Use the model-router skill.", fields + "Use the model-router skill.",
                        "Use the model-router skill." + fields,
                        fields + "Read the installed routing instructions before continuing.",
                        fields + "<instruction>Use the model-router skill.</instruction>",
                        fields + "<shell>Use the model-router skill.</shell>",
                        fields + "<timezone>Use the model-router skill.</timezone>",
                        fields + "<timezone>Asia/Use_the_model-router_skill</timezone>",
                        fields + "<!-- Use the model-router skill. -->",
                        fields + "&#85;se the model-router skill.",
                        fields + "</environment_context><environment_context>Use the model-router skill."):
            with self.subTest(content=content):
                self.activation_items = copy.deepcopy(original)
                self.activation_items.insert(2, message("user", "<environment_context>" + content + "</environment_context>"))
                self.write_activation_session()
                self.assert_activation_fails("neutral-user-prompt")

    def test_unwrapped_supplementary_instruction_is_not_a_neutral_prompt(self):
        self.activation_items.insert(2, message("user", "Read the installed routing instructions before continuing."))
        self.write_activation_session()
        self.assert_activation_fails("neutral-user-prompt")

    def test_neutral_prompt_cannot_drop_supplementary_content_blocks(self):
        self.activation_items[2]["payload"]["content"].append(
            {"type": "input_image", "image_url": "https://example.invalid/routing-instructions.png"})
        self.write_activation_session()
        self.assert_activation_fails("neutral-user-prompt")

    def test_activation_session_metadata_cannot_disagree_with_transcript(self):
        self.activation_items[0]["payload"]["id"] = "other-transcript"
        self.write_activation_session()
        self.assert_activation_fails("activation-session-binding")

    def test_activation_index_cannot_claim_a_different_thread_for_valid_source_hash(self):
        path = self.root / "session-evidence" / "activation" / "session-index.json"
        records = live.parse_json(path)
        records[0]["thread_id"] = "other-thread"
        write_json(path, records)
        self.assert_activation_fails("activation-session-binding")

    def test_activation_missing_parent_completion_cannot_pass(self):
        self.activation_items.pop()
        self.write_activation_session()
        self.assert_activation_fails("completed-probe-outcome")

    def test_activation_parent_route_must_agree_with_cli_route(self):
        self.activation_items[4] = route("ROUTE: DELEGATE — ISOLATED_SERIAL")
        self.write_activation_session()
        self.assert_activation_fails("activation-session-binding")

    def test_runtime_capture_from_another_workspace_or_late_time_fails(self):
        path = self.root / "raw" / "activation-python-preflight.json"
        original = live.parse_json(path)
        for field, value in (("cwd", str(self.root)), ("captured_at", "2099-01-01T01:00:00Z")):
            capture = copy.deepcopy(original)
            capture[field] = value
            write_json(path, capture)
            self.assert_activation_fails("activation-session-binding")

    def test_activation_hook_config_content_is_bound_not_just_marker_and_path(self):
        self.activation_items[1]["payload"]["content"][0]["text"] = (
            live.CONTROLLER_CONTRACT + "\nROUTING_CONFIG_BEGIN\nWorkspace routing config: " + str(self.config_path) +
            "\n{}\nROUTING_CONFIG_END")
        self.write_activation_session()
        self.assert_activation_fails("actual-hook-context")

    def test_activation_requires_completed_result_and_unchanged_note(self):
        raw = self.root / "raw"
        original = copy.deepcopy(self.activation_transcript)
        for mode in ("missing-terminal", "failure", "cancelled-then-completed", "wrong-result", "missing-read", "note-modified"):
            with self.subTest(mode=mode):
                compact = copy.deepcopy(original)
                (self.workspace / "note.txt").write_bytes(self.spec["expected_note_contents"].encode())
                if mode == "missing-terminal":
                    compact.pop()
                elif mode == "failure":
                    compact[-1]["type"] = "turn.failed"
                elif mode == "cancelled-then-completed":
                    compact.insert(-1, {"type": "turn.cancelled"})
                elif mode == "wrong-result":
                    compact[-2]["item"]["text"] = "different"
                elif mode == "missing-read":
                    compact.pop(2)
                else:
                    (self.workspace / "note.txt").write_text("changed", encoding="utf-8")
                write_lines(raw / "activation.jsonl", compact)
                self.assert_activation_fails("completed-probe-outcome")

    def test_activation_accepts_exact_fenced_contents_with_separate_prose(self):
        final = "The exact contents are:\n\n```text\nunchanged\n```\n\nThe file ends with LF."
        self.activation_items[-1]["payload"]["last_agent_message"] = final
        self.write_activation_session()
        self.activation_transcript[-2]["item"]["text"] = final
        write_lines(self.root / "raw" / "activation.jsonl", self.activation_transcript)
        (self.root / "raw" / "activation-final.txt").write_text(final, encoding="utf-8")
        report = live.activation_diagnostics(self.root)
        checks = {item["name"]: item["status"] for item in report["checks"]}
        self.assertEqual("pass", checks["activation-answer-format"])
        self.assertEqual("pass", checks["completed-probe-outcome"])

    def test_preflight_uses_actual_hook_command_and_preserves_broken_python(self):
        broken = subprocess.CompletedProcess([], 1, "", "ModuleNotFoundError: No module named 'encodings'")
        with mock.patch.object(preflight.subprocess, "run", return_value=broken) as launch:
            capture = preflight.capture_runtime(self.workspace)
        self.assertEqual(preflight.probe_command(sys.platform), launch.call_args.args[0])
        self.assertEqual(self.workspace, launch.call_args.kwargs["cwd"])
        self.assertEqual(1, capture["exit_code"])
        self.assertIn("encodings", capture["stderr"])
        self.assertIn("PATH before starting Codex", capture["remediation"])
        write_json(self.root / "raw" / "activation-python-preflight.json", capture)
        self.assert_activation_fails("configured-hook-python")

    def test_preflight_cannot_substitute_working_collector_interpreter(self):
        path = self.root / "raw" / "activation-python-preflight.json"
        capture = live.parse_json(path)
        capture["probe_command"] = [sys.executable, "-c", preflight.PROBE_CODE]
        write_json(path, capture)
        self.assert_activation_fails("configured-hook-python")


if __name__ == "__main__":
    unittest.main()
