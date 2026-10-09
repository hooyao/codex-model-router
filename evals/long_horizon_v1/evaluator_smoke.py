"""One separately budgeted public-fixture evaluator capability smoke."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import time

from .accounting import account
from .common import HERE, file_sha, manifest, write_json_new
from .quality_adapter import product_snapshot
from .runtime_binding import arm_execution, verify_cli
from .transport import AppServerTransport, TransportError
from evals.scripts.run_paired_arm import SessionMeter


FIXTURE = HERE / "smoke_fixture"
WALL_SECONDS = 180
OBSERVED_STOP_USD = 1.0
RESERVE_SECONDS = 25


def fixture_hashes() -> dict[str, str]:
    return {path.name: file_sha(path) for path in FIXTURE.iterdir() if path.is_file()}


def plan() -> dict:
    spec = manifest()
    return {"schema_version": 1, "kind": "evaluator-smoke-plan",
        "cli_sha256": spec["runtime"]["cli_sha256"],
        "adapter_sha256": file_sha(HERE / "quality_adapter.py"),
        "transport_sha256": file_sha(HERE / "transport.py"),
        "fixture_sha256": fixture_hashes(),
        "model": "gpt-6-astra", "effort": "xhigh",
        "execution_mode": "dangerFullAccess with separate scratch and logical read-only product",
        "maximum_wall_seconds": WALL_SECONDS,
        "observed_dispatch_stop_usd": OBSERVED_STOP_USD,
        "automatic_retries": 0,
        "spending": "--run --approved-budget-usd 1.0 requires separate authorization"}


def _go_command_proof(rollout: Path, go_binary: Path) -> dict:
    calls, outputs = {}, {}
    for line in rollout.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        payload = row.get("payload") or {}
        if row.get("type") != "response_item":
            continue
        if payload.get("type") == "custom_tool_call":
            calls[payload.get("call_id")] = str(payload.get("input", ""))
        elif payload.get("type") == "custom_tool_call_output":
            value = payload.get("output")
            outputs[payload.get("call_id")] = "\n".join(
                item.get("text", "") for item in value if isinstance(item, dict)) \
                if isinstance(value, list) else str(value)
    expected_path = str(go_binary).lower().replace("\\", "")
    for call_id, command in calls.items():
        normalized = command.lower().replace("\\", "")
        output = outputs.get(call_id, "")
        if (expected_path in normalized and "test" in normalized and
                "-count=1" in normalized and
                re.search(r'"exit_code"\s*:\s*0', output) and
                "example.com/evaluator-smoke" in output):
            return {"call_id": call_id,
                "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
                "output_sha256": hashlib.sha256(output.encode()).hexdigest(),
                "go_exit_code": 0}
    raise ValueError("pinned Go test exit-zero native command proof missing")


def run(output: Path, sessions: Path, approved_budget_usd: float) -> dict:
    if approved_budget_usd != OBSERVED_STOP_USD or output.exists():
        raise ValueError("exact $1 smoke budget and fresh output directory required")
    spec = manifest()
    cli = Path(spec["runtime"]["cli"])
    verify_cli(cli, spec)
    go_binary = Path(spec["toolchain"]["windows_path"])
    if not go_binary.is_file() or file_sha(go_binary) != spec["toolchain"]["sha256"]:
        raise ValueError("pinned Windows Go binary unavailable")
    output.mkdir(parents=True)
    product, scratch = output / "product", output / "scratch"
    shutil.copytree(FIXTURE, product)
    scratch.mkdir()
    token = secrets.token_hex(16)
    (product / "unknown-token.txt").write_text(token + "\n", encoding="utf-8")
    before = product_snapshot(product)
    started = time.monotonic()
    deadline = started + WALL_SECONDS - RESERVE_SECONDS
    transport = None
    meter = None
    evaluator_id = None
    try:
        environment = os.environ.copy()
        environment.update(TEMP=str(scratch), TMP=str(scratch),
                           GOCACHE=str(scratch / "go-cache"), GOPROXY="off", GOSUMDB="off")
        options = arm_execution(spec, "baseline")["cli_options"]
        transport = AppServerTransport(cli, scratch, options,
                                       "gpt-6-astra", "xhigh", deadline, env=environment)
        evaluator_id = transport.start()
        write_json_new(output / "evaluator-registration.json", {
            "schema_version": 1, "thread_id": evaluator_id,
            "model": "gpt-6-astra", "effort": "xhigh",
            "cli_sha256": spec["runtime"]["cli_sha256"]})
        meter = SessionMeter(sessions, evaluator_id, "gpt-6-astra", "xhigh")
        def guard():
            meter.refresh()
            if (time.monotonic() >= deadline or meter.cost_upper >= OBSERVED_STOP_USD or
                    meter.unknown_models or meter.unknown_usage):
                raise TransportError("evaluator smoke wall, observed-cost, or usage stop")
        prompt = ("This is a public-fixture evaluator capability smoke, separate from any "
            "benchmark arm. Treat the product at " + str(product.resolve()) +
            " as logically read-only. Find its unknown-token.txt without guessing the token; "
            "read it and write the exact token to " + str((scratch / "observed-token.txt").resolve()) +
            ". Execute this exact pinned Go binary against the public fixture with exit zero: "
            + str(go_binary.resolve()) + " test -count=1 ./... . Use the separate scratch "
            "for any temporary files. Do not edit the product. Report the observed token "
            "file and Go exit status.")
        turn = transport.turn(prompt, guard)
        guard()
        if turn.get("status") != "completed" or turn.get("thread_id") != evaluator_id:
            raise ValueError("smoke evaluator turn incomplete")
        settled = min(deadline, time.monotonic() + 10.0)
        observed = account(sessions, evaluator_id, "gpt-6-astra", "xhigh", False)
        while observed.get("status") != "complete" and time.monotonic() < settled:
            time.sleep(0.25)
            observed = account(sessions, evaluator_id, "gpt-6-astra", "xhigh", False)
        usage = observed.get("summary") or {}
        if (observed.get("status") != "complete" or observed.get("issues") or
                len(usage.get("sessions", [])) != 1 or
                usage["sessions"][0].get("id") != evaluator_id or
                not usage.get("model_calls") or
                usage.get("estimated_usd_upper_bound", OBSERVED_STOP_USD) >= OBSERVED_STOP_USD):
            raise ValueError("smoke evaluator native usage incomplete or over stop")
        after = product_snapshot(product)
        if after != before:
            raise ValueError("smoke evaluator changed candidate product")
        observed_token = scratch / "observed-token.txt"
        if not observed_token.is_file() or observed_token.read_text(encoding="utf-8").strip() != token:
            raise ValueError("unknown fixture token was not read into scratch")
        rollouts = [path for path, state in meter.paths.items()
                    if state.get("id") == evaluator_id]
        if len(rollouts) != 1:
            raise ValueError("smoke native parent rollout missing")
        go_proof = _go_command_proof(rollouts[0], go_binary)
        receipt = {"schema_version": 1, "kind": "evaluator-capability-smoke",
            "status": "PASS", "plan_sha256": hashlib.sha256(
                json.dumps(plan(), sort_keys=True).encode()).hexdigest(),
            "manifest_sha256": file_sha(HERE / "manifest.json"),
            "cli_sha256": spec["runtime"]["cli_sha256"],
            "adapter_sha256": file_sha(HERE / "quality_adapter.py"),
            "fixture_sha256": fixture_hashes(),
            "evaluator_thread_id": evaluator_id,
            "evaluator_registration_sha256": file_sha(output / "evaluator-registration.json"),
            "rollout_sha256": file_sha(rollouts[0]),
            "product_before": {key: before[key] for key in ("fileset_sha256", "content_sha256")},
            "product_after": {key: after[key] for key in ("fileset_sha256", "content_sha256")},
            "unknown_token_sha256": hashlib.sha256(token.encode()).hexdigest(),
            "scratch_output_sha256": file_sha(observed_token),
            "go_command": go_proof, "usage": usage,
            "wall_seconds": round(time.monotonic() - started, 3),
            "benchmark_arm_cost_included": False}
        write_json_new(output / "smoke.json", receipt)
        return receipt
    except Exception as error:
        if transport is not None and meter is not None and not transport.active_turn_complete:
            try:
                transport.cancel_and_drain(meter, absolute_deadline=time.monotonic() + 15)
            except (RuntimeError, OSError, ValueError):
                pass
        summary = meter.summary() if meter else None
        write_json_new(output / "smoke-failure.json", {
            "schema_version": 1, "status": "UNKNOWN", "reason": str(error)[:500],
            "evaluator_thread_id": evaluator_id, "usage": summary,
            "wall_seconds": round(time.monotonic() - started, 3)})
        raise
    finally:
        if transport is not None:
            transport.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--approved-budget-usd", type=float)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--sessions", type=Path)
    args = parser.parse_args()
    if not args.run:
        print(json.dumps(plan(), indent=2))
    else:
        if args.output is None or args.sessions is None:
            parser.error("--run requires --output and --sessions")
        print(json.dumps(run(args.output.resolve(), args.sessions.resolve(),
                             args.approved_budget_usd), indent=2))
