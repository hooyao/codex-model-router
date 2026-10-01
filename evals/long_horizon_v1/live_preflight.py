"""Bind reviewed cancellation evidence to fresh pilot arms without model turns."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import re
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from evals.long_horizon_v1.common import HERE, file_sha, manifest, write_json_new
from evals.long_horizon_v1.prepare import verify_prepared
from evals.long_horizon_v1.run import (
    _canonical_sha, _canary_runtime_equivalent, _reject_comparator_fields,
    _standalone_plan_version, pilot_plan)
from evals.long_horizon_v1.runtime_binding import (
    arm_execution, verify_arm_config, verify_arm_runtime, verify_cli)


EVIDENCE = {
    "baseline": (
        "_scratch/cancellation-canary/baseline-parent-stop-20260930-1/cancellation.json",
        "7ce99436daf02a8a417d8a691be3e05f1ba9db47c35c79dcde17f59765e651f0",
        "_scratch/cancellation-canary/baseline-parent-stop-20260930-1/adjudication-v2.json",
        "41764a66cde0bd8a8983b512a686ac6829fb8d3b4985fba7a1603fa8da7863d1"),
    "treatment": (
        "_scratch/cancellation-canary/retry-20260930120001-96e403dd/canary/cancellation.json",
        "40b99a0ed0dda9bb19807f9e466661fc7fe7120d203c887a59ca0c589608e8d5",
        "_scratch/cancellation-canary/retry-20260930120001-96e403dd/adjudication-v2.json",
        "7eba0edc2e714d98dd6506fbb0fecd92a2629158d87c9b4f57e0f5034d31519c")}


@contextmanager
def forbid_model_turn_start():
    """Fail before sending a paid turn from the zero-turn capability probe."""
    from evals.long_horizon_v1.transport import AppServerTransport
    original = AppServerTransport._send

    def guarded(self, method, *args, **kwargs):
        if method == "turn/start":
            raise ValueError("no-model capability rebind forbids turn/start")
        return original(self, method, *args, **kwargs)

    with patch.object(AppServerTransport, "_send", guarded):
        yield


def _final_manifest(expected_sha256: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("exact final manifest SHA-256 required")
    if file_sha(HERE / "manifest.json") != expected_sha256:
        raise ValueError("final manifest SHA-256 mismatch")
    spec = manifest()
    if spec.get("live_enabled") is not True:
        raise ValueError("final manifest is not live-enabled")
    return spec


def _requires_no_model_rebind(spec: dict) -> bool:
    pilot = spec.get("pilot")
    return isinstance(pilot, dict) and (
        pilot.get("path") == "pilot-plan-v11.json" or
        _standalone_plan_version(pilot.get("path")) is not None or
        pilot.get("schema_version") == 4)


def _verify_standalone_rebind(spec: dict, plan: dict, rebinding: bool) -> None:
    pilot = spec.get("pilot", {})
    version = _standalone_plan_version(pilot.get("path"))
    if (version is not None or
            pilot.get("schema_version") == 4 or
            plan.get("mode") == "standalone-feasibility"):
        if not rebinding:
            raise ValueError("standalone feasibility requires no-model rebind and exact final manifest hash")
        _reject_comparator_fields(plan)
        if (version is None or
                pilot.get("schema_version") != 4 or
                plan.get("schema_version") != 4 or
                plan.get("pilot_id") != f"flipt-oci-long-horizon-pilot-{version:02d}" or
                plan.get("mode") != "standalone-feasibility" or
                plan.get("arm_order") != ["treatment"] or
                plan.get("matched_pairs") != 0 or
                plan.get("live_rebind") != {"mode": "no-model-rebind-v1",
                    "expected_final_manifest_sha256_required": True,
                    "turn_start_forbidden": True}):
            raise ValueError("standalone feasibility rebind scope drift")


def bind_capabilities(*, expected_final_manifest_sha256: str | None = None) -> dict:
    if expected_final_manifest_sha256 is not None:
        with forbid_model_turn_start():
            return _bind_capabilities(expected_final_manifest_sha256)
    return _bind_capabilities(None)


def _bind_capabilities(expected_final_manifest_sha256: str | None) -> dict:
    spec = manifest()
    rebinding = expected_final_manifest_sha256 is not None
    if rebinding:
        spec = _final_manifest(expected_final_manifest_sha256)
    elif _requires_no_model_rebind(spec):
        if spec["pilot"].get("path") == "pilot-plan-v11.json":
            raise ValueError("pilot-11 requires no-model rebind and exact final manifest hash")
        raise ValueError("standalone feasibility requires no-model rebind and exact final manifest hash")
    elif spec.get("live_enabled") is not False:
        raise ValueError("capability freeze requires live-disabled manifest")
    plan = pilot_plan(spec)
    _verify_standalone_rebind(spec, plan, rebinding)
    prepared = HERE / plan["preparation_root"]
    cli = Path(spec["runtime"]["cli"])
    cli_binding = verify_cli(cli, spec)
    sessions = Path.home() / ".codex" / "sessions"
    if not sessions.is_dir():
        raise ValueError("reviewed cancellation sessions missing")
    results = {}
    for arm in plan["arm_order"]:
        source_relative, source_sha, decision_relative, decision_sha = EVIDENCE[arm]
        source_path, decision_path = HERE / source_relative, HERE / decision_relative
        if file_sha(source_path) != source_sha or file_sha(decision_path) != decision_sha:
            raise ValueError(f"{arm} cancellation evidence drift")
        source = json.loads(source_path.read_text(encoding="utf-8"))
        decision = json.loads(decision_path.read_text(encoding="utf-8"))
        if (source.get("status") != "UNKNOWN" or source.get("arm") != arm or
                decision.get("status") != "verified" or
                decision.get("source_receipt_sha256") != source_sha):
            raise ValueError(f"{arm} cancellation adjudication incomplete")
        preparation = verify_prepared(prepared, arm, spec)
        policy = arm_execution(spec, arm)
        routing = (verify_arm_config(prepared / arm, spec)
                   if arm == "treatment" else None)
        if rebinding:
            _final_manifest(expected_final_manifest_sha256)
            runtime = verify_arm_runtime(cli, prepared / arm, spec, arm)
            _final_manifest(expected_final_manifest_sha256)
        else:
            runtime = verify_arm_runtime(cli, prepared / arm, spec, arm)
        if (not _canary_runtime_equivalent(source.get("runtime_binding"), runtime, plan) or
                source.get("runtime_binding_sha256") !=
                    _canonical_sha(source.get("runtime_binding"))):
            raise ValueError(f"{arm} current runtime differs from reviewed canary")
        cancellation = {"status": "verified", "interrupt_ack": True,
            "turn_completed": True, "usage_drained": True,
            "descendants_drained": True,
            "real_child_observed": arm == "treatment"}
        if arm == "treatment":
            cancellation.update({"child_model": "gpt-6-astra", "child_effort": "xhigh"})
        proof = {"status": "verified", "cli_sha256": cli_binding["cli_sha256"],
            "code_mode_host_sha256": cli_binding["code_mode_host_sha256"],
            "routing_config_canonical_sha256":
                routing["canonical_sha256"] if routing else None,
            "cli_options": policy["cli_options"],
            "arm_execution_policy_sha256": policy["policy_sha256"],
            "prompt_suffix_sha256": policy["prompt_suffix_sha256"],
            "native_spawn_available": arm == "treatment",
            "router_hooks_verified": arm == "treatment",
            "preparation_sha256": preparation["preparation_sha256"],
            "multi_turn_same_thread": True, "child_usage_complete": True,
            "cancellation_verified": True, "selector_verified": True,
            "model": policy["model"], "effort": policy["effort"],
            "capability": {"status": "verified", "arm": arm,
                "model": policy["model"], "effort": policy["effort"],
                "canary_id": source["parent_thread_id"],
                "runtime_binding_sha256": _canonical_sha(runtime),
                "cancellation": cancellation,
                "cancellation_receipt_path": str(source_path.resolve()),
                "cancellation_receipt_sha256": source_sha,
                "cancellation_adjudication_path": str(decision_path.resolve()),
                "cancellation_adjudication_sha256": decision_sha,
                "cancellation_sessions_path": str(sessions.resolve())}}
        destination = HERE / plan["capabilities"][arm]
        if rebinding:
            _final_manifest(expected_final_manifest_sha256)
        write_json_new(destination, proof)
        if rebinding:
            _final_manifest(expected_final_manifest_sha256)
        results[arm] = {"capability_path": str(destination.resolve()),
                        "capability_sha256": file_sha(destination),
                        "preparation_sha256": preparation["preparation_sha256"],
                        "runtime_binding_sha256": _canonical_sha(runtime),
                        "model_turns": 0}
    if rebinding:
        _final_manifest(expected_final_manifest_sha256)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-model-rebind", action="store_true")
    parser.add_argument("--expected-final-manifest-sha256")
    args = parser.parse_args()
    if args.no_model_rebind != (args.expected_final_manifest_sha256 is not None):
        parser.error("--no-model-rebind requires --expected-final-manifest-sha256")
    print(json.dumps(bind_capabilities(
        expected_final_manifest_sha256=args.expected_final_manifest_sha256), indent=2))
