#!/usr/bin/env python3
"""Opt-in serial EvalPlus execution with usage-based soft admission.

All observed threads use the user's frozen Astra comparison card, regardless of
the actual worker model. These amounts are estimates, never provider invoices.
Generated code is collected as text only; this module never executes it.
"""

import json
import shutil
from decimal import Decimal
from pathlib import Path

try:
    from . import evalplus_runner as runner
    from . import evalplus_isolation as isolation
except ImportError:
    import evalplus_runner as runner
    import evalplus_isolation as isolation


def validate_pricing(pricing):
    expected = {"input_per_million_usd": "10.00", "cached_input_per_million_usd": "1.00",
                "output_per_million_usd": "50.00"}
    if pricing.get("rate_model") != "gpt-6-astra" or pricing.get("rates") != expected:
        raise runner.HarnessError("soft campaign requires the frozen Astra 10/1/50 rate card")
    if pricing.get("soft_cap_usd") != "50.00":
        raise runner.HarnessError("authorized soft cap must be USD 50.00")
    if not pricing.get("pricing_version"):
        raise runner.HarnessError("pricing version is required")
    return pricing


def _objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _objects(child)


def collect_usage(paths, pricing):
    """Inventory distinct CLI threads and charge each final observation once.

Mirrored copies of the same final observation do not double-charge a thread.
Conflicting finals fail closed instead of assuming whether they are cumulative.
No child usage is inferred from the parent's totals or the worker's prose.
"""
    validate_pricing(pricing)
    sessions, raw_sources, conflicts = {}, [], set()

    def ensure(identifier):
        return sessions.setdefault(identifier, {"session_id": identifier, "role": "worker",
                                                "model": None, "usage": None, "sources": []})

    for path in sorted(set(p.resolve() for p in paths)):
        digest = runner.sha256_file(path)
        raw_sources.append({"path": str(path), "sha256": digest})
        active = None
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            for identifier in runner._session_ids(event):
                ensure(identifier)
            for obj in _objects(event):
                states = obj.get("agents_states")
                if isinstance(states, dict):
                    for identifier in states:
                        ensure(identifier)
            if event.get("type") == "thread.started":
                active = event["thread_id"]
                parent = ensure(active)
                parent.update({"role": "primary", "model": "gpt-6-astra"})
            for obj in _objects(event):
                if obj.get("type") != "turn.completed" or "usage" not in obj:
                    continue
                identifier = obj.get("thread_id") or obj.get("session_id") or active
                if not identifier:
                    raise runner.HarnessError("terminal CLI usage has no thread identity")
                session = ensure(identifier)
                usage = runner._usage(obj["usage"], identifier)
                if usage["cached_input_tokens"] > usage["input_tokens"]:
                    raise runner.HarnessError("cached tokens exceed input tokens")
                if session["usage"] is not None and session["usage"] != usage:
                    conflicts.add(identifier)
                else:
                    session["usage"] = usage
                if str(path) not in session["sources"]:
                    session["sources"].append(str(path))
    total = Decimal(0)
    missing = []
    for identifier, session in sorted(sessions.items()):
        usage = session["usage"]
        if usage is None or identifier in conflicts:
            missing.append(identifier)
            session["estimated_cost_usd"] = None
            continue
        rates = pricing["rates"]
        cost = (Decimal(usage["input_tokens"] - usage["cached_input_tokens"]) * Decimal(rates["input_per_million_usd"])
                + Decimal(usage["cached_input_tokens"]) * Decimal(rates["cached_input_per_million_usd"])
                + Decimal(usage["output_tokens"]) * Decimal(rates["output_per_million_usd"])) / Decimal(1000000)
        session["estimated_cost_usd"] = str(cost)
        total += cost
    return {"source": "distinct-thread-cli-terminal-usage", "pricing_version": pricing["pricing_version"],
            "rate_model": "gpt-6-astra", "cost_type": "API-equivalent estimate, not billed cost",
            "sessions": [sessions[key] for key in sorted(sessions)], "raw_sources": raw_sources,
            "observed_cost_usd": str(total), "estimated_cost_usd": str(total) if not missing else None,
            "cost_complete": not missing, "missing_usage_sessions": missing,
            "conflicting_usage_sessions": sorted(conflicts)}


def admit_next(ledger, pricing):
    if ledger["conflicting_usage_sessions"]:
        raise runner.HarnessError("ambiguous terminal usage; preserve evidence and stop")
    return Decimal(ledger["observed_cost_usd"]) < Decimal(pricing["soft_cap_usd"])


def clone_slot_home(template, destination, arm, identity, include_auth=True):
    """Only configuration, authentication, and the exact router package survive."""
    destination.mkdir(parents=True, exist_ok=False)
    (destination.parent / (destination.name + "-profile")).mkdir()
    for name in ("config.toml", "auth.json"):
        if name == "auth.json" and not include_auth:
            continue
        if (template / name).is_file():
            shutil.copyfile(template / name, destination / name)
    if runner.sha256_file(destination / "config.toml") != identity["config_sha256"][arm]:
        raise runner.HarnessError("slot config differs from preflight")
    if arm == "router":
        source = Path(identity["installed_path"])
        target = destination / source.relative_to(template)
        shutil.copytree(source, target)
        if isolation.tree_hash(target) != identity["candidate_sha256"]:
            raise runner.HarnessError("slot candidate differs from preflight")
    return destination


def campaign_ledger(campaign, pricing, prior_paths=()):
    paths = list((campaign / "treatment-preflight").glob("*.jsonl"))
    paths += list((campaign / "raw").glob("*.jsonl"))
    paths += list(prior_paths)
    ledger = collect_usage(paths, pricing)
    runner.write_json(campaign / "cost-ledger.json", ledger)
    runner.write_json(campaign / "session-inventory.json", {"sessions": ledger["sessions"]})
    return ledger


def execute_soft_campaign(manifest_path, campaign, baseline_home, router_home, pricing_path,
                          run_limit=24, prior_paths=()):
    manifest, prepared = runner._load_prepared(campaign, manifest_path)
    if baseline_home is None or router_home is None or run_limit <= 0:
        raise runner.HarnessError("soft campaign requires both isolated homes and a positive run limit")
    homes = {"baseline": baseline_home.resolve(), "router": router_home.resolve()}
    runner.validate_codex_homes(manifest, homes)
    isolation.require_treatment_receipt(campaign, manifest, homes)
    isolation.require_grader_receipt(campaign)
    identity = isolation.validate_homes(homes)
    pricing = validate_pricing(runner.read_json(pricing_path))
    frozen = campaign / "frozen-pricing.json"
    if frozen.exists() and runner.sha256_file(frozen) != runner.sha256_file(pricing_path):
        raise runner.HarnessError("campaign pricing cannot change after admission")
    if not frozen.exists():
        shutil.copyfile(pricing_path, frozen)
    state = prepared["state"]
    if set(state["attempted_run_ids"]) != set(state["completed_run_ids"]):
        raise runner.HarnessError("a prior terminal failure exists; no automatic continuation")
    completed, stop = [], None
    reservations = runner.cost_reservations_microusd(manifest["execution"]["campaign_cost_ceiling_usd"], 24)
    for run in prepared["schedule"]:
        if run["run_id"] in state["attempted_run_ids"]:
            continue
        if len(completed) >= run_limit:
            break
        ledger = campaign_ledger(campaign, pricing, prior_paths)
        if not admit_next(ledger, pricing):
            stop = "observed API-equivalent estimate reached USD 50 soft cap"
            break
        run_id, arm = run["run_id"], run["variant"]
        home = clone_slot_home(homes[arm], campaign / "slot-homes" / run_id, arm, identity)
        task = campaign / "tasks" / run_id
        prompt_hash = runner.sha256_bytes((task / "solution.py").read_text(encoding="utf-8").encode("utf-8"))
        if prompt_hash != next(t["prompt_sha256"] for t in manifest["tasks"] if t["task_id"] == run["task_id"]):
            raise runner.HarnessError("unstarted task was modified")
        state["attempted_run_ids"].append(run_id)
        # Legacy reservations are schedule metadata only, not soft-cap admission.
        state["reserved_token_total"] += manifest["execution"]["token_reservation_per_run"]
        state["reserved_cost_microusd"] += reservations[run["run_index"] - 1]
        runner.write_json(campaign / "campaign.json", prepared)
        status = isolation.capture(campaign / "raw", run_id,
                                   runner.build_codex_command(manifest, run, task), task,
                                   isolation.arm_environment(home), (task / "TASK.md").read_text(encoding="utf-8"), 900)
        ledger = campaign_ledger(campaign, pricing, prior_paths)
        run_ledger = collect_usage([campaign / "raw" / (run_id + ".jsonl")], pricing)
        status["usage_ledger"] = run_ledger
        status["solution_sha256"] = runner.sha256_file(task / "solution.py")
        status["variant"] = arm
        status["run_id"] = run_id
        runner.write_json(campaign / "raw" / (run_id + ".status.json"), status)
        if status["exit_code"] != 0:
            stop = "CLI failure; no paid retry"
            break
        if (arm == "router" and len(run_ledger["sessions"]) < 2) or (arm == "baseline" and len(run_ledger["sessions"]) > 1):
            stop = "router worker dispatch or baseline isolation not observed"
            break
        state["completed_run_ids"].append(run_id)
        completed.append(run_id)
        runner.write_json(campaign / "campaign.json", prepared)
        print(json.dumps({"completed": run_id, "observed_cost_usd": ledger["observed_cost_usd"],
                          "cost_complete": ledger["cost_complete"]}), flush=True)
    ledger = campaign_ledger(campaign, pricing, prior_paths)
    result = {"mode": "live-soft-cap", "completed_run_ids": completed, "stop_reason": stop,
              "attempted_count": len(state["attempted_run_ids"]), "completed_count": len(state["completed_run_ids"]),
              "observed_cost_usd": ledger["observed_cost_usd"], "cost_complete": ledger["cost_complete"]}
    runner.write_json(campaign / "live-status.json", result)
    return result
