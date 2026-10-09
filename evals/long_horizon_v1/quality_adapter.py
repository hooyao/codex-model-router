"""Run one arm-blind quality request using pinned App Server and Go graders."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from evals.long_horizon_v1.accounting import account
from evals.long_horizon_v1.common import ASSET_ROOT, HERE, checked_run, file_sha, manifest, write_json_new
from evals.long_horizon_v1.assets.checkpoint_hash import capture_patch
from evals.long_horizon_v1.grade import REVIEW_REQUIREMENTS, record_grader_hazard, stop_wsl_grader
from evals.long_horizon_v1.runtime_binding import arm_execution, verify_cli
from evals.long_horizon_v1.transport import AppServerTransport, TransportError
from evals.long_horizon_v1.quality_bridge import publish_json_new, validate_diagnostics
from evals.scripts.run_paired_arm import SessionMeter


class EvaluatorIncomplete(RuntimeError):
    """The evaluator could not assess product behavior; booleans are unverified."""


def require_complete_assessment(report: object) -> tuple[dict, list]:
    if not isinstance(report, dict) or report.get("assessment_status") != "COMPLETE":
        raise EvaluatorIncomplete("independent product assessment incomplete")
    requirements, diagnostics = report.get("requirements"), report.get("diagnostics")
    if (not isinstance(requirements, dict) or set(requirements) != set(REVIEW_REQUIREMENTS) or
            any(type(value) is not bool for value in requirements.values()) or
            not isinstance(diagnostics, list)):
        raise EvaluatorIncomplete("independent semantic review output incomplete")
    return requirements, diagnostics


def _atomic_new(path: Path, value: dict) -> None:
    publish_json_new(path, value)


class EvaluatorBudgetGuard:
    """Check clock on every frame and scan native usage at a bounded cadence."""
    def __init__(self, meter: SessionMeter, deadline: float, cancel_path: Path,
                 solver_cost_upper: float, known_evaluator_cost_upper: float):
        self.meter = meter
        self.deadline = deadline
        self.cancel_path = cancel_path
        self.known_cost_upper = solver_cost_upper + known_evaluator_cost_upper
        self.last_refresh = 0.0

    def __call__(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if now >= self.deadline or self.cancel_path.exists():
            raise TransportError("evaluator original deadline, cost, or cancellation stop")
        if force or now - self.last_refresh >= 1.0:
            self.meter.refresh()
            self.last_refresh = now
        if (time.monotonic() >= self.deadline or self.meter.unknown_models or
                self.meter.unknown_usage or
                self.known_cost_upper + self.meter.cost_upper >= 15.0):
            raise TransportError("evaluator original deadline, cost, or cancellation stop")


def product_snapshot(product: Path) -> dict:
    """Record the candidate file set and content independently of Git status."""
    files = {}
    for path in product.rglob("*"):
        relative = path.relative_to(product)
        if ".git" in relative.parts:
            continue
        key = relative.as_posix()
        if path.is_symlink():
            files[key] = {"kind": "symlink", "target": os.readlink(path)}
        elif path.is_file():
            files[key] = {"kind": "file", "sha256": file_sha(path)}
        elif path.is_dir():
            files[key] = {"kind": "directory"}
    return {"files": files,
            "fileset_sha256": hashlib.sha256(json.dumps(sorted(files),
                separators=(",", ":")).encode()).hexdigest(),
            "content_sha256": hashlib.sha256(json.dumps(files, sort_keys=True,
                separators=(",", ":")).encode()).hexdigest()}


def _last_message(meter: SessionMeter, thread_id: str) -> str:
    paths = [path for path, state in meter.paths.items() if state.get("id") == thread_id]
    if len(paths) != 1:
        raise ValueError("evaluator parent rollout missing")
    messages = []
    for line in paths[0].read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        payload = row.get("payload") or {}
        if (row.get("type") == "response_item" and payload.get("type") == "message" and
                payload.get("role") == "assistant"):
            text = "".join(item.get("text", "") for item in payload.get("content", [])
                           if isinstance(item, dict))
            if text:
                messages.append(text)
    if not messages:
        raise ValueError("independent evaluator final response missing")
    return messages[-1]


def _completed_native_review(root: Path, revision: int, request_sha: str,
                             session_root: Path, neutral: Path, candidate_sha: str,
                             original_snapshot: dict, original_patch: bytes) -> tuple[str, SessionMeter, dict]:
    """Resume this exact request only after its original evaluator closed normally."""
    registration = json.loads((root / f"evaluator-r{revision}.json").read_text(encoding="utf-8"))
    evaluator_id = registration.get("thread_id")
    if (registration.get("schema_version") != 1 or
            registration.get("request_sha256") != request_sha or
            registration.get("session_root") != str(session_root.resolve()) or
            registration.get("model") != "gpt-6-astra" or
            registration.get("effort") != "xhigh" or
            not isinstance(evaluator_id, str) or not evaluator_id):
        raise ValueError("existing evaluator registration differs from request")
    observed = account(session_root, evaluator_id, "gpt-6-astra", "xhigh", False)
    usage = observed.get("summary") or {}
    sessions = usage.get("sessions") or []
    if (observed.get("status") != "complete" or observed.get("issues") or
            len(sessions) != 1 or sessions[0].get("id") != evaluator_id or
            sessions[0].get("model") != "gpt-6-astra" or
            sessions[0].get("effort") != "xhigh" or
            sessions[0].get("terminal") != "task_complete" or
            len(sessions[0].get("turns", [])) != 1 or
            sessions[0]["turns"][0].get("terminal") != "task_complete" or
            not usage.get("model_calls")):
        raise ValueError("existing evaluator native turn or usage incomplete")
    turn_id = sessions[0]["turns"][0]["turn_id"]
    meter = SessionMeter(session_root, evaluator_id, "gpt-6-astra", "xhigh")
    meter.refresh()
    rollouts = [path for path, state in meter.paths.items() if state.get("id") == evaluator_id]
    if len(rollouts) != 1 or len(meter.paths) != 1:
        raise ValueError("existing evaluator native rollout ambiguous")
    rows = [json.loads(line) for line in rollouts[0].read_text(encoding="utf-8").splitlines()]
    meta = rows[0].get("payload") if rows and rows[0].get("type") == "session_meta" else {}
    scratch = Path((meta or {}).get("cwd", ""))
    if (meta.get("id") != evaluator_id or scratch.name != "scratch" or
            scratch.resolve().parent.parent != neutral.resolve()):
        raise ValueError("existing evaluator workspace identity changed")
    prior_product = scratch.parent / "product"
    prompts = ["".join(item.get("text", "") for item in (row.get("payload") or {}).get("content", [])
                       if isinstance(item, dict))
               for row in rows if row.get("type") == "response_item" and
               (row.get("payload") or {}).get("type") == "message" and
               (row.get("payload") or {}).get("role") == "user"]
    terminals = [row for row in rows if row.get("type") == "event_msg" and
                 (row.get("payload") or {}).get("type") == "task_complete"]
    if (prompts.count(_review_prompt(prior_product)) != 1 or len(terminals) != 1 or
            terminals[0]["payload"].get("turn_id") != turn_id or
            capture_patch(prior_product) != original_patch or
            hashlib.sha256(original_patch).hexdigest() != candidate_sha or
            product_snapshot(prior_product) != original_snapshot):
        raise ValueError("existing evaluator prompt, candidate, or terminal differs")
    report = json.loads(_last_message(meter, evaluator_id))
    require_complete_assessment(report)
    return evaluator_id, meter, report


def _review_prompt(product: Path) -> str:
    reports = "\n\n".join((ASSET_ROOT / f"round{index}.md").read_text(encoding="utf-8")
                         for index in range(3))
    return ("Independently review the implementation at " + str(product.resolve()) +
        " against only these already revealed public requirements. Treat the candidate "
        "product as logically read-only even though this CLI profile has broad execution "
        "access. Put any narrow reproductions only in your separate scratch; "
        "do not edit the product, "
        "read hidden evaluator assets, infer a benchmark arm, or provide repair code. "
        "Return one JSON object with `assessment_status`: `COMPLETE` only after "
        "you actually inspect the product and can assess it, otherwise `INCOMPLETE`. "
        "For a complete assessment include `requirements`: exactly these keys mapped to "
        "booleans: " + ", ".join(REVIEW_REQUIREMENTS) + ". Also return `diagnostics`: "
        "a list of functional differences with exactly `public_requirement`, "
        "`observed_behavior`, and `expected_behavior` strings. Do not include test names, "
        "source paths, implementation prescriptions, or private evaluator details. "
        "Use an empty list only if all requirements pass. For `INCOMPLETE`, explain "
        "the infrastructure limitation and mark requirement values as unverified; do not "
        "claim product failures or functional differences.\n\n" + reports)


def _functional_diagnostics(transport: AppServerTransport, meter: SessionMeter,
                            evaluator_id: str, hidden: dict, backend: dict,
                            initial: object, guard) -> list[dict]:
    failures = []
    for label, grade in (("hidden behavior and race", hidden),
                         ("backend regressions", backend)):
        if grade.get("behavior_pass") is not True or grade.get("quality_pass") is not True:
            checks = grade.get("checks") or []
            failures.append({"component": label, "observations": [
                {"exit_code": row.get("exit_code"),
                 "stdout_tail": str(row.get("stdout_tail", ""))[-3000:],
                 "stderr_tail": str(row.get("stderr_tail", ""))[-1000:]}
                for row in checks if isinstance(row, dict)]})
    if failures or not initial:
        prompt = ("The separate evaluator checks below contain confidential failure evidence. "
            "Interpret it only as functional differences from the already revealed public "
            "requirements. Return one JSON object with `diagnostics`: one or more specific "
            "public_requirement, observed_behavior, expected_behavior triples for each failed "
            "component. Do not repeat hidden test identifiers, source or evaluator paths, "
            "assertion text, code, or implementation instructions. If the evidence cannot be "
            "translated into a concrete functional diagnostic, return an empty list so this "
            "quality request fails closed.\n" +
            json.dumps({"prior_semantic_diagnostics": initial,
                        "failed_components": failures}, sort_keys=True))
        turn = transport.turn(prompt, guard)
        guard()
        if turn.get("status") != "completed" or turn.get("thread_id") != evaluator_id:
            raise ValueError("evaluator diagnostic follow-up did not complete")
        meter.refresh()
        value = json.loads(_last_message(meter, evaluator_id))
        return validate_diagnostics(value.get("diagnostics"), "FAIL")
    return validate_diagnostics(initial, "FAIL")


def _publication_diagnostics(revision: int, passed: bool, initial: object,
                             transport: AppServerTransport | None, meter: SessionMeter,
                             evaluator_id: str, hidden: dict, backend: dict,
                             guard) -> list[dict]:
    if passed or revision == 1:
        return []  # A final failure has no remaining correction to explain.
    if transport is None:
        return validate_diagnostics(initial, "FAIL")
    return _functional_diagnostics(transport, meter, evaluator_id, hidden, backend,
                                   initial, guard)


def _grade(candidate: Path, prepared: Path, output: Path, *,
           hidden: bool, review: Path | None, deadline: float) -> dict:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("quality grade has no remaining original wall time")
    command = [sys.executable, str(HERE / "grade.py"), "--candidate", str(candidate),
               "--prepared", str(prepared), "--output", str(output), "--round", "2"]
    if hidden:
        command += ["--hidden", "--race", "--semantic-review", str(review)]
    else:
        command.append("--backend")
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=min(900.0, remaining))
    except subprocess.TimeoutExpired as error:
        if not stop_wsl_grader(output):
            record_grader_hazard(prepared, output, "quality grade cleanup unconfirmed")
            raise RuntimeError("quality grade timed out with WSL cleanup UNKNOWN") from error
        raise TimeoutError("quality grade exceeded original workflow deadline") from error
    if result.returncode or time.monotonic() >= deadline:
        if any((output / step / "wsl-pgid.txt").exists() for step in ("go-test", "race")):
            if not stop_wsl_grader(output):
                record_grader_hazard(prepared, output, "quality grade exit unconfirmed")
                raise RuntimeError("quality grade WSL cleanup UNKNOWN")
        raise RuntimeError("quality grade failed to produce a complete receipt")
    receipt = json.loads((output / "grade.json").read_text(encoding="utf-8"))
    if receipt.get("round") != 2:
        raise ValueError("quality grade round changed")
    return receipt


def _run_once(request_path: Path, prepared: Path, cli: Path,
              session_root: Path) -> dict:
    spec = manifest()
    verify_cli(cli, spec)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    root = request_path.parent
    revision = request.get("revision")
    candidate = Path(request.get("candidate_patch_path", ""))
    if (request.get("kind") != "quality-request" or revision not in (0, 1) or
            request_path.name != f"request-r{revision}.json" or
            request.get("manifest_sha256") != file_sha(HERE / "manifest.json") or
            request.get("preparation_sha256") != file_sha(prepared / "preparation.json") or
            not candidate.is_absolute() or file_sha(candidate) != request.get("candidate_patch_sha256") or
            (root / f"response-r{revision}.json").exists()):
        raise ValueError("quality request or candidate drift")
    request_sha = file_sha(request_path)
    remaining = (request["original_deadline_utc_ns"] - time.time_ns()) / 1e9
    deadline = time.monotonic() + remaining - 25.0
    if remaining <= 25.0:
        raise TimeoutError("quality request arrived after original deadline")
    options = arm_execution(spec, "baseline")["cli_options"]
    neutral = HERE / "_scratch/quality-evaluator"
    neutral.mkdir(parents=True, exist_ok=True)
    review_root = Path(tempfile.mkdtemp(prefix="review-", dir=neutral))
    product = review_root / "product"
    scratch = review_root / "scratch"
    scratch.mkdir()
    shutil.copytree(prepared / "seed", product)
    if candidate.stat().st_size:
        checked_run(["git", "apply", "--binary", "-"], cwd=product,
                    input=candidate.read_bytes())
    original_patch = capture_patch(product)
    original_snapshot = product_snapshot(product)
    if (file_sha(candidate) != request["candidate_patch_sha256"] or
            hashlib.sha256(original_patch).hexdigest() != request["candidate_patch_sha256"]):
        raise ValueError("evaluator product differs from requested candidate")
    transport = None
    meter = None
    evaluator_id = None
    review_data = None
    try:
        if (root / f"evaluator-r{revision}.json").exists():
            evaluator_id, meter, review_data = _completed_native_review(
                root, revision, request_sha, session_root, neutral,
                request["candidate_patch_sha256"], original_snapshot, original_patch)
        else:
            review_env = os.environ.copy()
            review_env.update(TEMP=str(scratch), TMP=str(scratch),
                              GOCACHE=str(scratch / "go-cache"))
            transport = AppServerTransport(cli, scratch, options,
                                           "gpt-6-astra", "xhigh", deadline,
                                           env=review_env)
            evaluator_id = transport.start()
            _atomic_new(root / f"evaluator-r{revision}.json", {
                "schema_version": 1, "request_sha256": request_sha,
                "thread_id": evaluator_id, "session_root": str(session_root.resolve()),
                "model": "gpt-6-astra", "effort": "xhigh"})
            meter = SessionMeter(session_root, evaluator_id, "gpt-6-astra", "xhigh")
        guard = EvaluatorBudgetGuard(meter, deadline, root / f"cancel-r{revision}.json",
            request["solver_cost_upper_at_request"],
            request["known_evaluator_cost_upper"])
        if transport is not None:
            turn = transport.turn(_review_prompt(product), guard)
            guard(force=True)
            if turn.get("status") != "completed" or turn.get("thread_id") != evaluator_id:
                raise ValueError("evaluator turn or parent identity incomplete")
            review_data = json.loads(_last_message(meter, evaluator_id))
        guard(force=True)
        requirements, diagnostics = require_complete_assessment(review_data)
        if capture_patch(product) != original_patch or product_snapshot(product) != original_snapshot:
            raise ValueError("read-only evaluator changed candidate product")
        review = {"schema_version": 1, "assessment_status": "COMPLETE",
            "reviewer_role": "independent",
            "arm_blind": True, "reviewer_id": evaluator_id,
            "candidate_patch_sha256": request["candidate_patch_sha256"],
            "manifest_sha256": request["manifest_sha256"],
            "preparation_sha256": request["preparation_sha256"],
            "requirements": requirements,
            "verdict": "pass" if all(requirements.values()) else "fail"}
        review_path = root / f"r{revision}-semantic-review.json"
        write_json_new(review_path, review)
        hidden = _grade(candidate, prepared, root / f"r{revision}-hidden-output",
                        hidden=True, review=review_path, deadline=deadline)
        backend = _grade(candidate, prepared, root / f"r{revision}-backend-output",
                         hidden=False, review=None, deadline=deadline)
        hidden_path, backend_path = (root / f"r{revision}-{kind}-grade.json"
                                     for kind in ("hidden", "backend"))
        shutil.copyfile(root / f"r{revision}-hidden-output/grade.json", hidden_path)
        shutil.copyfile(root / f"r{revision}-backend-output/grade.json", backend_path)
        passed = (hidden.get("behavior_pass") is True and hidden.get("quality_pass") is True and
                  backend.get("behavior_pass") is True and backend.get("quality_pass") is True and
                  review["verdict"] == "pass")
        diagnostics = _publication_diagnostics(revision, passed, diagnostics,
            transport, meter, evaluator_id, hidden, backend, guard)
        observed = account(session_root, evaluator_id, "gpt-6-astra", "xhigh", False)
        settle_deadline = min(deadline, time.monotonic() + 10.0)
        while observed.get("status") != "complete" and time.monotonic() < settle_deadline:
            time.sleep(0.25)
            observed = account(session_root, evaluator_id, "gpt-6-astra", "xhigh", False)
        summary = observed.get("summary") or {}
        if observed.get("status") != "complete" or observed.get("issues") or not summary.get("model_calls"):
            raise ValueError("independent evaluator usage incomplete")
        if capture_patch(product) != original_patch or product_snapshot(product) != original_snapshot:
            raise ValueError("read-only evaluator changed candidate product")
        response = {"schema_version": 1, "kind": "quality-response",
            "assessment_status": "COMPLETE",
            "workflow_id": request["workflow_id"], "request_id": request["request_id"],
            "request_sha256": request_sha, "revision": revision,
            "candidate_patch_sha256": request["candidate_patch_sha256"],
            "verdict": "PASS" if passed else "FAIL",
            "hidden_grade_sha256": file_sha(hidden_path),
            "backend_grade_sha256": file_sha(backend_path),
            "semantic_review_sha256": file_sha(review_path),
            "diagnostics": [] if passed else diagnostics,
            "evaluation_usage": {"status": "complete",
                "estimated_usd": summary["estimated_usd"],
                "estimated_usd_upper_bound": summary["estimated_usd_upper_bound"],
                "model_calls": summary["model_calls"]}}
        _atomic_new(root / f"response-r{revision}.json", response)
        return response
    except Exception as error:
        if transport is not None and meter is not None and not transport.active_turn_complete:
            try:
                transport.cancel_and_drain(meter, absolute_deadline=time.monotonic() + 15)
            except (RuntimeError, OSError, ValueError):
                pass
        report_sha = None
        if isinstance(review_data, dict):
            report_path = root / f"r{revision}-incomplete-model-report.json"
            _atomic_new(report_path, review_data)
            report_sha = file_sha(report_path)
        usage = {"status": "UNKNOWN", "estimated_usd": None,
                 "estimated_usd_upper_bound": meter.cost_upper if meter else None,
                 "model_calls": meter.calls if meter else None}
        if evaluator_id:
            try:
                observed = account(session_root, evaluator_id, "gpt-6-astra", "xhigh", False)
                summary = observed.get("summary") or {}
                if observed.get("status") == "complete" and observed.get("issues") == [] and \
                        summary.get("unknown_models") == [] and summary.get("unknown_usage") == []:
                    usage = {"status": "complete",
                        "estimated_usd": summary.get("estimated_usd"),
                        "estimated_usd_upper_bound": summary.get("estimated_usd_upper_bound"),
                        "model_calls": summary.get("model_calls")}
            except (RuntimeError, OSError, ValueError):
                pass
        after = product_snapshot(product) if product.is_dir() else None
        _atomic_new(root / f"failure-r{revision}.json", {
            "schema_version": 1, "request_sha256": request_sha,
            "assessment_status": "INCOMPLETE", "quality_status": "INFRA",
            "evaluator_thread_id": evaluator_id,
            "session_root": str(session_root.resolve()),
            "evaluation_usage": usage,
            "model_report_sha256": report_sha,
            "candidate_unchanged": after == original_snapshot,
            "product_before_fileset_sha256": original_snapshot["fileset_sha256"],
            "product_after_fileset_sha256": after["fileset_sha256"] if after else None,
            "product_before_content_sha256": original_snapshot["content_sha256"],
            "product_after_content_sha256": after["content_sha256"] if after else None,
            "reason": str(error)[:500]})
        raise
    finally:
        if transport is not None:
            transport.close()


def run_once(request_path: Path, prepared: Path, cli: Path,
             session_root: Path) -> dict:
    """Report even setup failures against the visible immutable request bytes."""
    try:
        return _run_once(request_path, prepared, cli, session_root)
    except Exception as error:
        name = request_path.name
        if name in ("request-r0.json", "request-r1.json"):
            revision = int(name[9])
            failure = request_path.parent / f"failure-r{revision}.json"
            if not failure.exists() and not (request_path.parent /
                    f"response-r{revision}.json").exists():
                digest = file_sha(request_path) if request_path.is_file() else None
                _atomic_new(failure, {"schema_version": 1,
                    "request_sha256": digest, "assessment_status": "INCOMPLETE",
                    "quality_status": "INFRA", "evaluator_thread_id": None,
                    "evaluation_usage": {"status": "UNKNOWN", "estimated_usd": None,
                        "estimated_usd_upper_bound": None, "model_calls": None},
                    "model_report_sha256": None, "candidate_unchanged": None,
                    "reason": str(error)[:500]})
        raise


def watch(root: Path, prepared: Path, cli: Path, session_root: Path) -> list[dict]:
    """Serve at most two immutable requests for one bounded workflow."""
    results = []
    startup_deadline = time.monotonic() + 4500.0
    for revision in (0, 1):
        request = root / f"request-r{revision}.json"
        while not request.is_file():
            if (root.parent / "run.json").is_file():
                return results
            if time.monotonic() >= startup_deadline:
                raise TimeoutError("quality adapter saw no request within one workflow wall")
            time.sleep(0.25)
        result = run_once(request, prepared, cli, session_root)
        results.append({"revision": revision, "verdict": result["verdict"]})
        if result["verdict"] == "PASS":
            break
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--request", type=Path)
    mode.add_argument("--watch-root", type=Path)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--cli", type=Path, required=True)
    parser.add_argument("--session-root", type=Path, required=True)
    args = parser.parse_args()
    if args.watch_root:
        print(json.dumps(watch(args.watch_root.resolve(), args.prepared.resolve(),
                               args.cli.resolve(), args.session_root.resolve()), indent=2))
    else:
        result = run_once(args.request.resolve(), args.prepared.resolve(),
                          args.cli.resolve(), args.session_root.resolve())
        print(json.dumps({"verdict": result["verdict"],
                          "response": str(args.request.with_name(
                              args.request.name.replace("request-", "response-")))}, indent=2))
