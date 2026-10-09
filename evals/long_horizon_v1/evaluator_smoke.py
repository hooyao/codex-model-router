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
    calls, outputs, native = {}, {}, []
    turn_ids, completed_turns, session_ids = set(), set(), set()
    duplicate_call_id = False
    for line in rollout.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        payload = row.get("payload") or {}
        if row.get("type") == "session_meta":
            session_ids.add(payload.get("id"))
        if row.get("type") == "response_item":
            turn_id = (payload.get("internal_chat_message_metadata_passthrough") or {}).get("turn_id")
            if payload.get("type") == "custom_tool_call":
                duplicate_call_id |= payload.get("call_id") in calls
                calls[payload.get("call_id")] = (str(payload.get("input", "")), turn_id)
            elif payload.get("type") == "custom_tool_call_output":
                duplicate_call_id |= payload.get("call_id") in outputs
                value = payload.get("output")
                content = "\n".join(item.get("text", "") for item in value
                                    if isinstance(item, dict)) if isinstance(value, list) else str(value)
                outputs[payload.get("call_id")] = (content, turn_id)
            if turn_id:
                turn_ids.add(turn_id)
        elif row.get("type") == "event_msg" and payload.get("type") == "item_completed":
            item = payload.get("item") or {}
            if item.get("type") == "CommandExecution":
                native.append((item, payload.get("thread_id"), payload.get("turn_id")))
        elif row.get("type") == "event_msg" and payload.get("type") == "task_complete":
            completed_turns.add(payload.get("turn_id"))
    expected_path = str(go_binary).lower().replace("\\", "")
    proofs = []
    for call_id, (command, turn_id) in calls.items():
        normalized = command.lower().replace("\\", "")
        if not (expected_path in normalized and re.search(r"\btest\s+-count=1\s+\./\.\.\.", normalized)):
            continue
        if call_id not in outputs or outputs[call_id][1] != turn_id or not turn_id:
            continue
        start_output = outputs[call_id][0]
        starts = [json.loads(line) for line in start_output.splitlines()
                  if line.startswith("{") and '"session_id"' in line]
        if len(starts) != 1 or type(starts[0].get("session_id")) is not int:
            continue
        session_id = starts[0]["session_id"]
        matching_native = [(item, thread, native_turn) for item, thread, native_turn in native
                           if str(item.get("process_id")) == str(session_id)]
        polls = [(poll_id, poll_command, outputs[poll_id][0])
                 for poll_id, (poll_command, poll_turn) in calls.items()
                 if poll_turn == turn_id and poll_id in outputs and
                 outputs[poll_id][1] == turn_id and
                 re.search(r"tools\.write_stdin\(\{\s*session_id\s*:\s*" +
                           str(session_id) + r"\b", poll_command)]
        origins = [origin_id for origin_id, (_, origin_turn) in calls.items()
                   if origin_turn == turn_id and origin_id in outputs and
                   any(line.startswith("{") and
                       re.search(r'"session_id"\s*:\s*' + str(session_id) + r'\b', line)
                       for line in outputs[origin_id][0].splitlines())]
        if (len(origins) != 1 or origins[0] != call_id or len(polls) != 1 or
                len(matching_native) != 1 or turn_id not in completed_turns or
                len(turn_ids) != 1 or len(session_ids) != 1 or duplicate_call_id):
            continue
        item, thread_id, native_turn = matching_native[0]
        poll_id, _, final_output = polls[0]
        final_results = [json.loads(line) for line in final_output.splitlines()
                         if line.startswith("{") and '"exit_code"' in line]
        native_command = "\n".join(item.get("command", []))
        if (native_turn != turn_id or thread_id not in session_ids or
                item.get("status") != "completed" or item.get("exit_code") != 0 or
                expected_path not in native_command.lower().replace("\\", "") or
                not re.search(r"\btest\s+-count=1\s+\./\.\.\.", native_command) or
                len(final_results) != 1 or final_results[0].get("exit_code") != 0 or
                "example.com/evaluator-smoke" not in item.get("stdout", "") or
                "example.com/evaluator-smoke" not in final_results[0].get("output", "")):
            continue
        proofs.append({"call_id": call_id, "continuation_call_id": poll_id,
            "native_exec_session_id": session_id, "thread_id": thread_id,
            "turn_id": turn_id,
            "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
            "output_sha256": hashlib.sha256(final_output.encode()).hexdigest(),
            "go_exit_code": 0})
    if len(proofs) == 1:
        return proofs[0]
    raise ValueError("pinned Go test exit-zero native command proof missing")


def verify_original_smoke(output: Path, sessions: Path, receipt_path: Path) -> dict:
    """Record a corrected offline verdict without changing the original UNKNOWN receipt."""
    failure_path = output / "smoke-failure.json"
    review_path = output / "independent-adjudication.json"
    preparation_path = output.parent / "smoke-preparation.json"
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    review = json.loads(review_path.read_text(encoding="utf-8"))
    preparation = json.loads(preparation_path.read_text(encoding="utf-8"))
    evaluator_id = review["evaluator_thread_id"]
    if (failure.get("status") != "UNKNOWN" or
            failure.get("reason") != "pinned Go test exit-zero native command proof missing" or
            failure.get("evaluator_thread_id") != evaluator_id or
            file_sha(failure_path) != review.get("source_receipt_sha256") or
            file_sha(preparation_path) != review.get("smoke_preparation_sha256") or
            review.get("status") != "verified-capability-from-original-trace" or
            review.get("paid_rerun_required") is not False or
            review.get("paid_calls_in_review") != 0 or
            preparation.get("fixture_sha256") != fixture_hashes() or
            review.get("fixture_sha256") != fixture_hashes()):
        raise ValueError("original smoke or independent adjudication drift")
    rollout = Path(review["rollout_path"])
    if (not rollout.is_file() or file_sha(rollout) != review.get("rollout_sha256") or
            sessions.resolve() not in rollout.resolve().parents):
        raise ValueError("original smoke rollout identity drift")
    go_binary = Path(manifest()["toolchain"]["windows_path"])
    if file_sha(go_binary) != review["go_chain"]["go_binary_sha256"]:
        raise ValueError("pinned Go binary drift")
    proof = _go_command_proof(rollout, go_binary)
    if (proof["thread_id"] != evaluator_id or proof["turn_id"] != review["turn_id"] or
            proof["call_id"] != review["go_chain"]["start_call_id"] or
            proof["continuation_call_id"] != review["go_chain"]["continuation_call_id"] or
            proof["native_exec_session_id"] != review["go_chain"]["native_exec_session_id"] or
            proof["go_exit_code"] != review["go_chain"]["exit_code"]):
        raise ValueError("original smoke Go command chain differs from independent review")
    observed = account(sessions, evaluator_id, "gpt-6-astra", "xhigh", False)
    usage = observed.get("summary") or {}
    if (observed.get("status") != "complete" or observed.get("issues") or
            usage != failure.get("usage") or usage.get("model_calls") != review.get("model_calls") or
            usage.get("estimated_usd") != review.get("estimated_usd") or
            len(usage.get("sessions", [])) != 1 or
            usage["sessions"][0].get("id") != evaluator_id):
        raise ValueError("original smoke native usage incomplete or changed")
    product = output / "product"
    snapshot = product_snapshot(product)
    token_file = product / "unknown-token.txt"
    scratch_file = output / "scratch" / "observed-token.txt"
    if (set(snapshot["files"]) != set(fixture_hashes()) | {"unknown-token.txt"} or
            any(snapshot["files"][name].get("sha256") != digest
                for name, digest in fixture_hashes().items()) or
            snapshot["fileset_sha256"] != review["product_fileset_sha256"] or
            snapshot["content_sha256"] != review["product_content_sha256"] or
            file_sha(token_file) != review["token_file_sha256"] or
            file_sha(scratch_file) != review["scratch_output_sha256"] or
            token_file.read_bytes() != scratch_file.read_bytes() or
            hashlib.sha256(token_file.read_text(encoding="utf-8").strip().encode()).hexdigest()
                != review["unknown_token_sha256"]):
        raise ValueError("original smoke product or scratch evidence drift")
    result = {"schema_version": 1, "kind": "evaluator-smoke-derived-verification",
        "status": "PASS", "original_receipt_status": "UNKNOWN",
        "original_failure_sha256": file_sha(failure_path),
        "independent_adjudication_sha256": file_sha(review_path),
        "smoke_preparation_sha256": file_sha(preparation_path),
        "raw_rollout_sha256": file_sha(rollout),
        "checker_source_sha256": file_sha(Path(__file__)),
        "evaluator_thread_id": evaluator_id, "go_command": proof,
        "product_fileset_sha256": snapshot["fileset_sha256"],
        "product_content_sha256": snapshot["content_sha256"],
        "scratch_output_sha256": file_sha(scratch_file),
        "usage": usage, "benchmark_arm_cost_included": False,
        "paid_calls_in_verification": 0}
    write_json_new(receipt_path, result)
    return result


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
    parser.add_argument("--verify-original", action="store_true")
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    if args.verify_original:
        if args.output is None or args.sessions is None or args.receipt is None:
            parser.error("--verify-original requires --output, --sessions, and --receipt")
        result = verify_original_smoke(args.output.resolve(), args.sessions.resolve(),
                                       args.receipt.resolve())
        print(json.dumps({"status": result["status"], "receipt": str(args.receipt.resolve()),
                          "receipt_sha256": file_sha(args.receipt.resolve())}, indent=2))
    elif not args.run:
        print(json.dumps(plan(), indent=2))
    else:
        if args.output is None or args.sessions is None:
            parser.error("--run requires --output and --sessions")
        print(json.dumps(run(args.output.resolve(), args.sessions.resolve(),
                             args.approved_budget_usd), indent=2))
