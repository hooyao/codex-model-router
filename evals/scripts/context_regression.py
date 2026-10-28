"""Run live context-loading regression against caller-prepared plugin homes.

Run with python -m evals.scripts.context_regression. CLI versions are recorded,
not used as an admission whitelist. Raw events remain under the private root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import shutil
import statistics
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from . import fixture
from .codex_runtime import (PortableTransport, RuntimeSessionMeter, discover_cli,
                            isolated_environment, runtime_capabilities)


REPO = Path(__file__).resolve().parents[2]
CASES = ("direct-small-control", "investigation-reuse", "serial-escalation",
         "parallel-disjoint", "architecture-review")
RATES = {"gpt-6.1-sol": (2.0, .1, 2.5, 10.0), "gpt-6-luna": (.1, .01, .125, .5),
         "gpt-6-astra": (10.0, 1.0, 12.5, 50.0), "gpt-6.1-terra": (1.0, .05, 1.25, 5.0)}
IGNORED = {".git", ".codex-model-router", ".router-dispatch-evidence"}


class ProbeInterrupted(RuntimeError):
    pass


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def business_tree(workspace: Path) -> dict:
    files, directories = {}, []
    for path in sorted(workspace.rglob("*")):
        relative = path.relative_to(workspace)
        if relative.parts[0] in IGNORED:
            continue
        if path.is_symlink():
            raise ValueError("unexpected symlink in business artifacts")
        if path.is_dir():
            directories.append(relative.as_posix())
        else:
            files[relative.as_posix()] = sha(path)
    return {"files": files, "directories": directories}


def runtime_instructions(root: Path, arm: str) -> str:
    caps = root / arm / "capabilities"
    return (f"Supervisor-captured read-only runtime evidence: spawn schema {caps / 'spawn-schema.json'}; "
            f"model catalog {caps / 'model-catalog.json'}; provenance {caps / 'provenance.json'}. "
            "Use these sources when dispatch preflight requires runtime evidence. Keep routing records "
            "under .router-dispatch-evidence and preserve business files. These files contain no task solution.")


def verified_hooks(transport: PortableTransport, workspace: Path, arm: str) -> list[dict]:
    inventory = transport.request("hooks/list", {"cwds": [str(workspace)]})
    rows = [hook for entry in inventory.get("data", []) for hook in entry.get("hooks", [])]
    expected = {"sessionStart", "userPromptSubmit", "subagentStart", "preToolUse", "postToolUse"}
    if len(rows) != 5 or {row.get("eventName") for row in rows} != expected or any(
            row.get("pluginId") != f"codex-model-router@context-{arm}" or
            row.get("trustStatus") != "trusted" or row.get("enabled") is not True for row in rows):
        raise ValueError("the requested plugin's five hooks are not uniquely loaded and trusted")
    return [{key: row.get(key) for key in ("eventName", "pluginId", "currentHash", "trustStatus")}
            for row in rows]


def run_arm(root: Path, output: Path, cli: Path, arm: str, case_id: str, repetition: int,
            model: str, effort: str, mode: str = "scored") -> dict:
    run_id = f"{mode}-{case_id}-r{repetition}-{arm}"
    workspace = root / arm / "runs" / run_id
    workspace.parent.mkdir(exist_ok=True)
    if mode == "scored":
        source = REPO / "evals" / "fixtures" / case_id / "fixture.json"
        definition = fixture.load_fixture(source)
        shutil.copytree(source.parent / "initial", workspace)
        expected = definition["reference"]["tree"]
        prompts = [definition["prompt"]]
        if case_id == "direct-small-control":
            prompts += ["Verify the correction remains in README.md and make no further edits.",
                        "Report the final verification status and preserve all current files."]
    else:
        workspace.mkdir()
        (workspace / "note.txt").write_bytes(b"unchanged\n")
        expected = business_tree(workspace)
        prompts = ["Use one native worker to read note.txt and report its exact contents. Do not edit business files."]
        config = json.loads((REPO / "plugins/codex-model-router/defaults/default-routing.json").read_text())
        config["execution_policy"]["default_mode"] = "delegate"
        (workspace / ".codex-model-router").mkdir()
        (workspace / ".codex-model-router/routing.json").write_text(json.dumps(config))
        if mode == "cancellation":
            prompts = ["Use one native worker to print CANCEL_READY, then wait 30 seconds in the same shell command. "
                       "This is a cancellation test; do not modify business files."]
    initial = business_tree(workspace)
    if mode == "scored" and initial != definition["initial"]["tree"]:
        raise ValueError("fresh business tree differs from the frozen fixture")
    private = root / arm / "raw" / run_id
    private.mkdir(parents=True)
    messages, hook_events, turns, decisions = [], [], [], []
    cancel_ready = False
    meter = None
    transport = None
    started = time.monotonic()
    record = {"run_id": run_id, "mode": mode, "arm": arm, "case_id": case_id,
              "repetition": repetition, "model": model, "effort": effort,
              "started_utc": datetime.now(timezone.utc).isoformat(), "complete": False}
    with (private / "events.jsonl").open("x", encoding="utf-8") as raw:
        try:
            transport = PortableTransport(cli, workspace, [], model, effort,
                started + (240 if mode == "scored" else 120),
                isolated_environment(root / arm / "home"), event_sink=raw)
            transport.initialize()
            record["hooks"] = verified_hooks(transport, workspace, arm)
            catalog = transport.request("model/list", {"includeHidden": True})
            matches = [row for row in catalog.get("data", []) if row.get("id") == model]
            if len(matches) != 1 or effort not in [e.get("reasoningEffort")
                    for e in matches[0].get("supportedReasoningEfforts", [])]:
                raise ValueError("requested controller model/effort is unavailable")
            parent = transport.start_thread(runtime_instructions(root, arm))["thread"]["id"]
            meter = RuntimeSessionMeter(root / arm / "home" / "sessions", parent, model, effort, rate_card=RATES)

            def frame(event):
                nonlocal cancel_ready
                params = event.get("params") or {}
                item = params.get("item") or {}
                if event.get("method") == "item/completed" and item.get("type") == "agentMessage":
                    messages.append({"thread_id": params.get("threadId"), "text": item.get("text", "")})
                if event.get("method") == "item/completed" and item.get("type") == "commandExecution" and \
                        "execution_decision.py" in item.get("command", "") and item.get("exitCode") == 0:
                    try:
                        value = json.loads(item.get("aggregatedOutput", ""))
                        if isinstance(value, dict) and value.get("schema_version") == 1 and \
                                value.get("ownership") in ("DIRECT", "DELEGATE"):
                            decisions.append({key: value.get(key) for key in
                                ("decision_id", "ownership", "delegate_topology", "verification_requirement",
                                 "escalation_trigger")})
                    except ValueError:
                        pass
                if event.get("method") == "hook/completed":
                    run = params.get("run") or {}
                    contexts = [e.get("text", "") for e in run.get("entries", []) if e.get("kind") == "context"]
                    hook_events.append({"thread_id": params.get("threadId"), "event": run.get("eventName"),
                                        "status": run.get("status"),
                                        "context_bytes": sum(len(text.encode()) for text in contexts)})
                if mode == "cancellation" and params.get("threadId") != parent and \
                        event.get("method") == "item/commandExecution/outputDelta" and \
                        "CANCEL_READY" in params.get("delta", ""):
                    cancel_ready = True

            def guard():
                meter.refresh()
                if cancel_ready:
                    raise ProbeInterrupted("observed child cancellation marker")
                if meter.cost_upper >= 1.0:
                    raise ValueError("observed per-arm cost stop")
                if meter.unknown_models or meter.unknown_usage:
                    raise ValueError("incomplete or unpriced response usage")

            for prompt in prompts:
                turn = transport.turn(prompt, guard, frame)
                turns.append(turn)
                if turn.get("status") != "completed":
                    raise ValueError("solver turn did not complete successfully")
            record["complete"] = True
        except ProbeInterrupted:
            record["cancellation"] = transport.cancel_and_drain(meter, timeout=15, descendants_first=True)
            record["complete"] = record["cancellation"].get("status") == "verified-drained"
        except Exception as error:
            record["failure"] = {"type": type(error).__name__, "reason": str(error)[:350]}
            if transport is not None and meter is not None:
                record["cancellation"] = transport.cancel_and_drain(meter, timeout=15, descendants_first=True)
        finally:
            record["wall_seconds"] = round(time.monotonic() - started, 3)
            if meter is not None:
                for _ in range(8):
                    meter.refresh()
                    time.sleep(.25)
                usage = meter.summary()
                record["usage_issues"] = meter.integrity_issues()
                record["usage"] = usage
                record["usage_complete"] = not record["usage_issues"] and \
                    not usage["unknown_models"] and not usage["unknown_usage"] and \
                    bool(usage["model_calls"]) and all(value is not None for value in usage["token_classes"].values())
                record["sessions"] = len(meter.paths)
                record["native_evidence"] = [{"sha256": sha(path), "bytes": path.stat().st_size}
                                             for path in meter.paths]
            if transport is not None:
                transport.close()
    actual = business_tree(workspace)
    record["artifact_pass"] = actual == expected
    record["initial_tree_sha256"] = fixture.tree_digest(initial)
    record["actual_tree_sha256"] = fixture.tree_digest(actual)
    record["hook_events"] = hook_events
    record["turns"] = turns
    record["observed_resolver_decisions"] = decisions
    record["raw_events_sha256"] = sha(private / "events.jsonl")
    parent_messages = [m["text"] for m in messages if meter and m["thread_id"] == meter.parent_id]
    record["route_lines"] = [line for text in parent_messages for line in text.splitlines()
                             if re.match(r"^ROUTE: (DIRECT|DELEGATE)\b", line)]
    record["final_answer"] = parent_messages[-1] if parent_messages else None
    record["native_workers"] = max(0, record.get("sessions", 1) - 1)
    # Raw text stays private. Publish only public fixture answers and reduced metadata.
    write_new(output / f"{run_id}.json", record)
    print(json.dumps({key: record.get(key) for key in
                      ("run_id", "complete", "artifact_pass", "wall_seconds", "native_workers", "failure")}), flush=True)
    return record


def summarize(output: Path) -> dict:
    records = [json.loads(path.read_text(encoding="utf-8")) for path in output.glob("scored-*.json")]
    cases = {}
    for case in CASES:
        pairs = []
        for repetition in range(1, 4):
            rows = {r["arm"]: r for r in records if r["case_id"] == case and r["repetition"] == repetition}
            if set(rows) != {"before", "after"}:
                pairs.append({"repetition": repetition, "status": "incomplete"})
                continue
            before, after = rows["before"], rows["after"]
            complete = all(r.get("complete") and r.get("usage_complete") and
                           not r.get("usage_issues") for r in rows.values())
            pair = {"repetition": repetition, "status": "complete" if complete else "incomplete",
                    "quality_parity": before["artifact_pass"] and after["artifact_pass"],
                    "wall_ratio": after["wall_seconds"] / before["wall_seconds"]}
            if complete:
                def tokens(row):
                    counts = row["usage"]["token_classes"]
                    return counts["input_tokens"] + counts["output_tokens"]
                pair["token_ratio"] = tokens(after) / tokens(before)
                b, a = before["usage"]["estimated_usd"], after["usage"]["estimated_usd"]
                pair["estimated_cost_ratio"] = a / b if a is not None and b else None
            pairs.append(pair)
        usable = [p for p in pairs if p["status"] == "complete"]
        medians = {key: statistics.median(p[key] for p in usable)
                   if len(usable) == 3 and all(p.get(key) is not None for p in usable) else None
                   for key in ("wall_ratio", "token_ratio", "estimated_cost_ratio")}
        cases[case] = {"pairs": pairs, "median_paired_ratios": medians,
                       "exploratory_artifact_and_economics_pass": len(usable) == 3 and
                       all(p["quality_parity"] for p in usable) and
                       medians["wall_ratio"] <= 1.15 and medians["token_ratio"] <= 1.05 and
                       medians["estimated_cost_ratio"] is not None and medians["estimated_cost_ratio"] <= 1.05}
    return {"scope": "small functional/overhead regression; process proof remains separate",
            "records": len(records), "cases": cases,
            "billing_status": "API-price estimates only; provider billing and upstream identity unverified"}


def previous_observed_spend(root: Path) -> dict:
    total, calls, issues = 0.0, 0, []
    for arm in ("before", "after"):
        sessions = root / arm / "home" / "sessions"
        for path in sessions.rglob("rollout-*.jsonl"):
            lines = path.read_text(encoding="utf-8").splitlines()
            if not lines:
                continue
            rows = [json.loads(line) for line in lines]
            meta = rows[0].get("payload") or {}
            if meta.get("parent_thread_id") or not any(row.get("type") == "token_usage_record" or
                    row.get("payload", {}).get("type") == "token_count" for row in rows):
                continue
            meter = RuntimeSessionMeter(sessions, meta["id"], "gpt-6.1-sol", "low", rate_card=RATES)
            meter.refresh()
            total += meter.cost_upper
            calls += meter.calls
            issues += sorted(meter.unknown_models) + meter.unknown_usage
    return {"estimated_usd_upper_bound": total, "responses": calls, "usage_issues": issues}


def reconcile_cancel(original: Path, root: Path, output: Path) -> dict:
    """Publish a derived replay receipt without changing the paid attempt."""
    record = json.loads(original.read_text(encoding="utf-8"))
    arm, run_id = record["arm"], record["run_id"]
    events_path = root / arm / "raw" / run_id / "events.jsonl"
    if sha(events_path) != record["raw_events_sha256"]:
        raise ValueError("original cancellation event evidence changed")
    proof = record.get("cancellation") or {}
    requests = proof.get("interrupt_requests") or []
    if not requests or not proof.get("interrupt_ack") or not proof.get("turn_completed") or \
            any(not row.get("acknowledged") for row in requests):
        raise ValueError("cancellation acknowledgements or terminal observation are incomplete")
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    completed = [(e["params"]["threadId"], e["params"]["turn"]["id"], e["params"]["turn"].get("status"))
                 for e in events if e.get("method") == "turn/completed"]
    for row in requests:
        if completed.count((row["thread_id"], row["turn_id"], "interrupted")) != 1:
            raise ValueError("requested interrupted turn does not match the runtime terminal event")
    parent = next(row["id"] for row in record["usage"]["sessions"] if not row.get("parent_id"))
    starts = {}
    marker = False
    for event in events:
        params = event.get("params") or {}
        item = params.get("item") or {}
        if event.get("method") == "item/started" and item.get("type") == "commandExecution":
            starts[item["id"]] = (params.get("threadId"), item.get("command", ""))
        if event.get("method") == "item/commandExecution/outputDelta" and "CANCEL_READY" in params.get("delta", ""):
            source = starts.get(params.get("itemId"))
            marker |= bool(source and source[0] != parent and source[0] == params.get("threadId") and
                           "sleep" in source[1].lower())
    if not marker:
        raise ValueError("no runtime child-command cancellation marker")
    meter = RuntimeSessionMeter(root / arm / "home/sessions", parent, record["model"], record["effort"], rate_card=RATES)
    meter.refresh()
    if meter.integrity_issues() or any(state.get("terminal") != "turn_aborted" for state in meter.paths.values()):
        raise ValueError("interrupted native usage or closure does not reconcile")
    if {sha(path) for path in meter.paths} != {row["sha256"] for row in record["native_evidence"]}:
        raise ValueError("original native rollout evidence changed")
    record["derived_from"] = {"sha256": sha(original), "original_status": proof.get("status")}
    record["usage"] = meter.summary()
    record["usage_issues"] = []
    record["usage_complete"] = True
    record["complete"] = True
    record["cancellation"] = {**proof, "status": "verified-native-replay"}
    write_new(output, record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cli")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--mode", choices=("native", "cancellation", "matrix"), required=True)
    parser.add_argument("--arm", choices=("before", "after"))
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--effort", default="low")
    parser.add_argument("--capability-root", type=Path)
    args = parser.parse_args()
    if not args.live:
        parser.error("--live explicitly enables model usage")
    args.output.mkdir(parents=True, exist_ok=False)
    cli = discover_cli(args.cli)
    metadata = runtime_capabilities(cli)
    write_new(args.output / "runtime.json", metadata)
    write_new(args.output / "campaign.json", {
        "before_ref": "e75ef8785d960b12e6d1ce27ad4c6b184b8a0fc0",
        "after_ref": "a93ba3af99b9ad58c97f82c4938129b634d5c446",
        "model": args.model, "effort": args.effort, "platform": platform.system(),
        "fixture_sha256": {case: sha(REPO / "evals/fixtures" / case / "fixture.json") for case in CASES},
        "runner_sha256": sha(Path(__file__)), "capability_adapter_sha256": sha(Path(__file__).with_name("codex_runtime.py")),
        "rate_card": {"date": "2026-10-28", "tier": "Standard", "per_million": RATES,
                      "sources": [f"https://developers.openai.com/api/docs/models/{model}" for model in RATES]},
        "billing": "API-price estimate; provider billing and upstream model identity not independently verified"})
    if args.mode != "matrix":
        if not args.arm:
            parser.error("a capability probe requires --arm")
        record = run_arm(args.runtime_root, args.output, cli, args.arm, "read-note", 1,
                         args.model, args.effort, args.mode)
        if not record.get("complete") or not record.get("usage_complete") or record.get("native_workers", 0) < 1:
            raise SystemExit("native capability probe did not establish complete execution and usage")
        return
    if not args.capability_root:
        parser.error("matrix requires --capability-root containing completed native and cancellation probes")
    for arm in ("before", "after"):
        for probe in ("native", "cancellation"):
            path = args.capability_root / f"{probe}-read-note-r1-{arm}.json"
            record = json.loads(path.read_text(encoding="utf-8"))
            if not record.get("complete") or not record.get("usage_complete") or record.get("native_workers", 0) < 1:
                raise SystemExit("required native/cancellation capability is incomplete")
    prior = previous_observed_spend(args.runtime_root)
    write_new(args.output / "prior-spend.json", prior)
    if prior["usage_issues"]:
        raise SystemExit("prior probe usage is incomplete; campaign spend cannot be bounded")
    start = time.monotonic()
    total = prior["estimated_usd_upper_bound"]
    try:
        for repetition in range(1, 4):
            for case in CASES:
                for arm in (("before", "after") if repetition % 2 else ("after", "before")):
                    if time.monotonic() - start > 5400 or total >= 10:
                        raise SystemExit("campaign observed budget or wall stop")
                    if hashlib.sha256(cli.read_bytes()).hexdigest() != metadata["cli_sha256"]:
                        raise SystemExit("runtime changed during matched campaign; remaining pairs are not comparable")
                    record = run_arm(args.runtime_root, args.output, cli, arm, case, repetition,
                                     args.model, args.effort)
                    usage = record.get("usage")
                    if usage is None:
                        raise SystemExit("missing usage; campaign whole cost is unknown")
                    total += usage["estimated_usd_upper_bound"]
                    if record.get("usage_issues") or not record.get("complete"):
                        raise SystemExit("incomplete scored run; preserve evidence and diagnose before continuing")
    finally:
        report = summarize(args.output)
        report["observed_campaign_usd_upper_bound"] = total
        write_new(args.output / "summary.json", report)


if __name__ == "__main__":
    main()
