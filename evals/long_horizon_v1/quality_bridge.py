"""Hash-bound external quality feedback for one persistent benchmark attempt."""
from __future__ import annotations

import json
from datetime import datetime
import os
from pathlib import Path
import re
import secrets
import tempfile
import time
from typing import Callable

from .accounting import account
from .common import HERE, TASK_ID, file_sha, manifest, sha, write_json_new
from .grade import REVIEW_REQUIREMENTS
from evals.scripts.run_paired_arm import SessionMeter


HEX = re.compile(r"[0-9a-f]{64}\Z")
PRIVATE = re.compile(r"(?i)(benchmark[_-]?oracle|--- FAIL:|TestBenchmark|\.go\b|[/\\]|(?:patch|diff|fix|implement)\s*(?:with|by|in)\b)")


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("quality bridge JSON must be an object")
    return value


def _digest(value: object) -> bool:
    return isinstance(value, str) and HEX.fullmatch(value) is not None


class InfrastructureIncomplete(RuntimeError):
    """Evaluator could not establish product quality; solver correction is forbidden."""


def validate_diagnostics(diagnostics: object, verdict: str) -> list[dict]:
    if (not isinstance(diagnostics, list) or len(diagnostics) > 8 or
            (verdict == "FAIL" and not diagnostics) or
            (verdict == "PASS" and diagnostics)):
        raise ValueError("quality diagnostics missing, unexpected, or unbounded")
    for item in diagnostics:
        if not isinstance(item, dict) or set(item) != {
                "public_requirement", "observed_behavior", "expected_behavior"}:
            raise ValueError("quality diagnostic shape invalid")
        for value in item.values():
            if (not isinstance(value, str) or not 1 <= len(value) <= 1000 or
                    PRIVATE.search(value)):
                raise ValueError("quality diagnostic exposes private or prescriptive detail")
    return diagnostics


def publish_json_new(path: Path, value: dict) -> None:
    """Expose a complete immutable file at its final name, never partial bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".quality-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write((json.dumps(value, indent=2, sort_keys=True) + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)  # atomic, no-overwrite publication
    finally:
        temporary.unlink(missing_ok=True)


class QualityBridge:
    """One request per immutable revision; evaluator work remains external."""

    def __init__(self, root: Path, *, manifest_sha256: str,
                 preparation_sha256: str, workflow_id: str,
                 parent_thread_id: str, session_root: Path,
                 operational_deadline: float, original_deadline_utc_ns: int,
                 create: bool = True):
        self.root = root
        if create:
            self.root.mkdir(parents=True, exist_ok=False)
        elif not self.root.is_dir():
            raise ValueError("quality bridge evidence root missing")
        self.manifest_sha256 = manifest_sha256
        self.preparation_sha256 = preparation_sha256
        self.workflow_id = workflow_id
        self.parent_thread_id = parent_thread_id
        self.session_root = session_root.resolve()
        self.operational_deadline = operational_deadline
        self.original_deadline_utc_ns = original_deadline_utc_ns
        self.requests: dict[int, dict] = {}
        self.responses: dict[int, dict] = {}
        self.failures: dict[int, dict] = {}
        self.evaluator_meters: dict[int, SessionMeter] = {}
        self.evaluator_cost_upper = 0.0
        self.evaluator_cost = 0.0
        self.evaluation_usage_complete = True

    def request(self, revision: int, candidate_patch_sha256: str,
                candidate_patch_path: Path, solver_cost_upper: float) -> dict:
        if (revision not in (0, 1) or revision in self.requests or
                not _digest(candidate_patch_sha256) or
                file_sha(candidate_patch_path) != candidate_patch_sha256 or
                type(solver_cost_upper) not in (float, int) or solver_cost_upper < 0):
            raise ValueError("duplicate, invalid, or unbound quality revision")
        value = {"schema_version": 1, "kind": "quality-request",
            "workflow_id": self.workflow_id, "revision": revision,
            "request_id": secrets.token_hex(16),
            "parent_thread_id": self.parent_thread_id,
            "candidate_patch_sha256": candidate_patch_sha256,
            "candidate_patch_path": str(candidate_patch_path.resolve()),
            "solver_cost_upper_at_request": solver_cost_upper,
            "known_evaluator_cost_upper": self.evaluator_cost_upper,
            "manifest_sha256": self.manifest_sha256,
            "preparation_sha256": self.preparation_sha256,
            "original_deadline_utc_ns": self.original_deadline_utc_ns}
        path = self.root / f"request-r{revision}.json"
        publish_json_new(path, value)
        value["request_sha256"] = file_sha(path)
        self.requests[revision] = value
        return value

    def _meter(self, revision: int) -> SessionMeter | None:
        path = self.root / f"evaluator-r{revision}.json"
        if not path.is_file():
            return None
        registration = _json(path)
        if (registration.get("schema_version") != 1 or
                registration.get("request_sha256") != self.requests[revision]["request_sha256"] or
                registration.get("model") != "gpt-6-astra" or
                registration.get("effort") != "xhigh" or
                registration.get("session_root") != str(self.session_root) or
                not isinstance(registration.get("thread_id"), str) or
                not registration["thread_id"]):
            raise ValueError("evaluator registration is unbound")
        meter = self.evaluator_meters.get(revision)
        if meter is None:
            meter = SessionMeter(self.session_root, registration["thread_id"],
                                 "gpt-6-astra", "xhigh")
            self.evaluator_meters[revision] = meter
        meter.refresh()
        if meter.unknown_models or meter.unknown_usage:
            self.evaluation_usage_complete = False
        self.evaluator_cost_upper = sum(item.cost_upper for item in self.evaluator_meters.values())
        return meter

    def _usage(self, revision: int, response: dict) -> dict:
        reported = response.get("evaluation_usage")
        meter = self._meter(revision)
        if isinstance(reported, dict) and reported.get("status") == "UNKNOWN":
            self.evaluation_usage_complete = False
            return reported
        if meter is None or not isinstance(reported, dict):
            self.evaluation_usage_complete = False
            return {"status": "UNKNOWN", "estimated_usd": None,
                    "estimated_usd_upper_bound": self.evaluator_cost_upper}
        registration = _json(self.root / f"evaluator-r{revision}.json")
        observed = account(self.session_root, registration["thread_id"],
                           "gpt-6-astra", "xhigh", False)
        summary = observed.get("summary") or {}
        if (observed.get("status") != "complete" or observed.get("issues") or
                summary.get("unknown_usage") or summary.get("unknown_models") or
                any(session.get("id") != registration["thread_id"]
                    for session in summary.get("sessions", [])) or
                reported != {"status": "complete",
                    "estimated_usd": summary.get("estimated_usd"),
                    "estimated_usd_upper_bound": summary.get("estimated_usd_upper_bound"),
                    "model_calls": summary.get("model_calls")}):
            raise ValueError("evaluator usage does not reconcile with native session")
        self.evaluator_cost += summary["estimated_usd"]
        self.evaluator_cost_upper = max(self.evaluator_cost_upper,
                                        sum(item.cost_upper for item in self.evaluator_meters.values()))
        return reported

    def _validate(self, revision: int, response: dict) -> dict:
        request = self.requests[revision]
        if (revision in self.responses or
                response.get("schema_version") != 1 or
                response.get("kind") != "quality-response" or
                response.get("workflow_id") != self.workflow_id or
                response.get("request_id") != request["request_id"] or
                response.get("request_sha256") != request["request_sha256"] or
                response.get("revision") != revision or
                response.get("candidate_patch_sha256") != request["candidate_patch_sha256"] or
                response.get("assessment_status") != "COMPLETE" or
                response.get("verdict") not in ("PASS", "FAIL") or
                not all(_digest(response.get(key)) for key in
                        ("hidden_grade_sha256", "backend_grade_sha256",
                         "semantic_review_sha256"))):
            raise ValueError("stale, duplicate, or mismatched quality response")
        paths = {"hidden_grade": self.root / f"r{revision}-hidden-grade.json",
                 "backend_grade": self.root / f"r{revision}-backend-grade.json",
                 "semantic_review": self.root / f"r{revision}-semantic-review.json"}
        if any(file_sha(path) != response[f"{name}_sha256"]
               for name, path in paths.items()):
            raise ValueError("quality evidence file hash mismatch")
        hidden, backend, review = (_json(paths[name]) for name in paths)
        if (hidden.get("candidate_patch_sha256") != request["candidate_patch_sha256"] or
                backend.get("candidate_patch_sha256") != request["candidate_patch_sha256"] or
                review.get("candidate_patch_sha256") != request["candidate_patch_sha256"] or
                hidden.get("manifest_sha256") != self.manifest_sha256 or
                backend.get("manifest_sha256") != self.manifest_sha256 or
                review.get("manifest_sha256") != self.manifest_sha256 or
                review.get("assessment_status") != "COMPLETE" or
                review.get("reviewer_role") != "independent" or
                review.get("arm_blind") is not True or "arm" in review):
            raise ValueError("quality evidence does not bind candidate or blind review")
        if (not isinstance(review.get("reviewer_id"), str) or
                not review["reviewer_id"] or
                review["reviewer_id"] == self.parent_thread_id or
                not isinstance(review.get("requirements"), dict) or
                set(review["requirements"]) != set(REVIEW_REQUIREMENTS) or
                any(type(value) is not bool for value in review["requirements"].values())):
            raise ValueError("independent semantic review identity or rubric incomplete")
        passed = (hidden.get("behavior_pass") is True and
                  hidden.get("quality_pass") is True and
                  backend.get("behavior_pass") is True and
                  backend.get("quality_pass") is True and
                  review.get("verdict") == "pass" and
                  isinstance(review.get("requirements"), dict) and
                  set(review["requirements"]) == set(REVIEW_REQUIREMENTS) and
                  all(value is True for value in review["requirements"].values()))
        if (response["verdict"] == "PASS") is not passed:
            raise ValueError("quality verdict conflicts with grade or review evidence")
        if revision == 1 and response["verdict"] == "FAIL" and response.get("diagnostics") == []:
            diagnostics = []
        else:
            diagnostics = validate_diagnostics(response.get("diagnostics"),
                                               response["verdict"])
        response["evaluation_usage"] = self._usage(revision, response)
        response["response_sha256"] = file_sha(self.root / f"response-r{revision}.json")
        self.responses[revision] = response
        return response

    def wait(self, revision: int, check_budget: Callable[[float], None]) -> dict:
        if revision not in self.requests:
            raise ValueError("quality request missing")
        path = self.root / f"response-r{revision}.json"
        while True:
            if time.monotonic() >= self.operational_deadline:
                self._cancel(revision, "original workflow deadline reached")
                raise TimeoutError("quality-wait-deadline")
            self._meter(revision)
            try:
                check_budget(self.evaluator_cost_upper)
            except (RuntimeError, ValueError, TimeoutError):
                self._cancel(revision, "solver or evaluator budget stop")
                raise
            failure = self.root / f"failure-r{revision}.json"
            if failure.is_file():
                failed = _json(failure)
                if failed.get("request_sha256") != self.requests[revision]["request_sha256"]:
                    raise ValueError("stale or mismatched evaluator failure response")
                if (failed.get("assessment_status") != "INCOMPLETE" or
                        failed.get("quality_status") != "INFRA"):
                    raise ValueError("evaluator failure classification is unbound")
                evaluator_id = failed.get("evaluator_thread_id")
                if evaluator_id is not None:
                    registration = _json(self.root / f"evaluator-r{revision}.json")
                    if (registration.get("request_sha256") !=
                            self.requests[revision]["request_sha256"] or
                            registration.get("thread_id") != evaluator_id or
                            failed.get("session_root") != str(self.session_root)):
                        raise ValueError("evaluator failure native identity differs")
                    self._usage(revision, failed)
                else:
                    self.evaluation_usage_complete = False
                report_hash = failed.get("model_report_sha256")
                if report_hash is not None and file_sha(
                        self.root / f"r{revision}-incomplete-model-report.json") != report_hash:
                    raise ValueError("evaluator incomplete model report drift")
                failed["failure_sha256"] = file_sha(failure)
                self.failures[revision] = failed
                raise InfrastructureIncomplete("evaluator-infrastructure-incomplete")
            if path.is_file():
                return self._validate(revision, _json(path))
            time.sleep(0.25)

    def _cancel(self, revision: int, reason: str) -> None:
        path = self.root / f"cancel-r{revision}.json"
        if not path.exists():
            write_json_new(path, {"schema_version": 1,
                "request_sha256": self.requests[revision]["request_sha256"],
                "reason": reason})
        self.evaluation_usage_complete = False

    def recheck(self, request_hashes: dict, response_hashes: dict) -> dict:
        """Independently rebind the saved quality chain and native evaluator usage."""
        if (not isinstance(request_hashes, dict) or not isinstance(response_hashes, dict) or
                set(request_hashes) != set(response_hashes) or
                set(request_hashes) not in ({"0"}, {"0", "1"})):
            raise ValueError("quality revision chain missing or duplicated")
        for revision in range(len(request_hashes)):
            path = self.root / f"request-r{revision}.json"
            request = _json(path)
            digest = file_sha(path)
            if (request_hashes.get(str(revision)) != digest or
                    request.get("schema_version") != 1 or
                    request.get("kind") != "quality-request" or
                    request.get("workflow_id") != self.workflow_id or
                    request.get("parent_thread_id") != self.parent_thread_id or
                    request.get("manifest_sha256") != self.manifest_sha256 or
                    request.get("preparation_sha256") != self.preparation_sha256 or
                    request.get("original_deadline_utc_ns") != self.original_deadline_utc_ns or
                    request.get("revision") != revision or
                    not _digest(request.get("candidate_patch_sha256")) or
                    file_sha(Path(request.get("candidate_patch_path", ""))) !=
                        request["candidate_patch_sha256"]):
                raise ValueError("saved quality request identity or candidate drift")
            request["request_sha256"] = digest
            self.requests[revision] = request
            response_path = self.root / f"response-r{revision}.json"
            if file_sha(response_path) != response_hashes.get(str(revision)):
                raise ValueError("saved quality response hash drift")
            self._validate(revision, _json(response_path))
        if (self.responses[len(request_hashes) - 1]["verdict"] != "PASS" or
                not self.evaluation_usage_complete):
            raise ValueError("final quality or evaluator usage incomplete")
        return {"revision": len(request_hashes) - 1,
                "response": self.responses[len(request_hashes) - 1],
                "evaluator_estimated_usd": self.evaluator_cost,
                "evaluator_cost_upper": self.evaluator_cost_upper,
                "response_sha256": response_hashes}

    @staticmethod
    def correction_message(response: dict) -> str:
        if response.get("verdict") != "FAIL":
            raise ValueError("only a failed quality result can request correction")
        public = {"functional_diagnostics": response["diagnostics"],
                  "correction_allowance": "one episode",
                  "submission": ".benchmark/quality-revision1.json",
                  "required_fields": ["schema_version", "revision",
                                      "candidate_patch_sha256", "summary"]}
        return ("An independent quality review found these functional differences "
                "against the already revealed public requirements. Reanalyze the "
                "relevant invariants, make one bounded correction, run public checks, "
                "then write the separate revision-1 submission file. The original "
                "deadline and cost stop remain in force.\n" +
                json.dumps(public, sort_keys=True))


def verify_common_arm_quality(prepared: Path, plan: dict, run: dict,
                              arm: str, session_root: Path, *,
                              require_wall: bool) -> dict:
    """Verify the prospective quality result and original wall boundary."""
    if plan.get("mode") != "common-quality-recovery" or arm not in ("baseline", "treatment"):
        raise ValueError("common quality plan or arm missing")
    stage = run.get("stage") or {}
    events = stage.get("events") or []
    parent = run.get("parent_thread_id")
    bridge = run.get("quality_bridge") or {}
    preparation_sha = file_sha(prepared / "preparation.json")
    manifest_sha = file_sha(HERE / "manifest.json")
    if (run.get("stop_reason") != "accepted-final" or run.get("quality_status") != "PASS" or
            run.get("workflow_cost_status") != "complete" or
            run.get("cost_status") != "complete" or run.get("usage_issues") != [] or
            run.get("runtime_binding", {}).get("preparation_sha256") != preparation_sha or
            stage.get("complete") is not True or stage.get("round") != 2 or
            stage.get("manifest_sha256") != manifest_sha or
            not isinstance(events, list) or not events or
            not isinstance(parent, str) or not parent or stage.get("thread_id") != parent or
            bridge.get("root") != str((HERE / plan["run_outputs"][arm] / "quality").resolve())):
        raise ValueError("common quality run, usage, or stage incomplete")
    previous = "0" * 64
    for index, event in enumerate(events):
        payload = {key: value for key, value in event.items() if key != "sha256"}
        digest = sha(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
        if (event.get("seq") != index or event.get("previous_sha256") != previous or
                event.get("sha256") != digest or event.get("arm") != arm or
                event.get("thread_id") != parent):
            raise ValueError("common quality event chain changed")
        previous = digest
    if (stage.get("chain_sha256") != previous or
            [event.get("round") for event in events if event.get("kind") == "reveal"] != [1, 2] or
            len([event for event in events if event.get("kind") == "accepted-final"]) != 1 or
            events[-1].get("kind") != "final-quality" or
            len([event for event in events if event.get("kind") == "correction-submission"]) > 1 or
            len([event for event in events if event.get("kind") == "quality-feedback"]) > 1):
        raise ValueError("common quality public or correction chain incomplete")
    root = Path(bridge["root"])
    checker = QualityBridge(root, manifest_sha256=manifest_sha,
        preparation_sha256=preparation_sha, workflow_id=plan["pilot_id"] + ":" + parent,
        parent_thread_id=parent, session_root=session_root,
        operational_deadline=time.monotonic() + 1,
        original_deadline_utc_ns=run["started_utc_ns"] + 4500 * 1_000_000_000,
        create=False)
    quality = checker.recheck(bridge.get("request_sha256"), bridge.get("response_sha256"))
    revision = quality["revision"]
    patch_sha = quality["response"]["candidate_patch_sha256"]
    from .standalone_quality import _grade
    review = _json(root / f"r{revision}-semantic-review.json")
    if (_grade(root / f"r{revision}-hidden-output", patch_sha, manifest_sha,
               hidden=True, review=review) != quality["response"]["hidden_grade_sha256"] or
            _grade(root / f"r{revision}-backend-output", patch_sha, manifest_sha,
                   hidden=False) != quality["response"]["backend_grade_sha256"]):
        raise ValueError("final hidden/race/backend grade differs from raw evidence")
    final = events[-1]
    if (final.get("revision") != revision or final.get("patch_sha256") != patch_sha or
            bridge.get("evaluator_cost_status") != "complete" or
            bridge.get("evaluator_estimated_usd") != quality["evaluator_estimated_usd"] or
            run.get("workflow_estimated_usd") !=
                run["usage"]["estimated_usd"] + quality["evaluator_estimated_usd"] or
            run.get("workflow_estimated_usd_upper_bound") !=
                run["usage"]["estimated_usd_upper_bound"] + quality["evaluator_cost_upper"] or
            (revision == 1 and len([event for event in events if
                event.get("kind") == "correction-submission"]) != 1)):
        raise ValueError("final quality patch, correction, or whole-workflow cost differs")
    files = {"hidden_race_grade": quality["response"]["hidden_grade_sha256"],
             "backend_grade": quality["response"]["backend_grade_sha256"],
             "semantic_review": quality["response"]["semantic_review_sha256"]}
    result = {"revision": revision, "patch_sha256": patch_sha,
              "quality_files_sha256": files,
              "evaluator_estimated_usd": quality["evaluator_estimated_usd"]}
    if arm == "treatment":
        from .fixed_baseline import verify_hard_stages
        result["public_hard_stages"] = verify_hard_stages(
            manifest(), plan, run, prepared / "treatment")
        if revision == 1:
            result["quality_correction_astra_turn_ids"] = correction_astra_turns(run, events)
    if require_wall:
        wall_path = HERE / plan["end_to_end"]["receipts"][arm]
        wall = _json(wall_path)
        run_path = HERE / plan["run_outputs"][arm] / "run.json"
        started = run.get("started_utc_ns")
        elapsed = wall.get("end_to_end_wall_seconds")
        if (wall.get("schema_version") != 1 or wall.get("kind") != "arm-end-to-end-boundary" or
                wall.get("pilot_id") != plan["pilot_id"] or wall.get("task_id") != TASK_ID or
                wall.get("arm") != arm or wall.get("manifest_sha256") != manifest_sha or
                wall.get("pilot_plan_sha256") != file_sha(HERE / manifest()["pilot"]["path"]) or
                wall.get("preparation_sha256") != preparation_sha or
                wall.get("run_sha256") != file_sha(run_path) or
                wall.get("quality_files_sha256") != files or
                wall.get("started_utc_ns") != started or
                wall.get("run_wall_seconds") != run.get("wall_seconds") or
                type(elapsed) not in (int, float) or elapsed < run["wall_seconds"] - 1 or
                type(wall.get("quality_finished_utc_ns")) is not int or
                abs((wall["quality_finished_utc_ns"] - started) / 1e9 - elapsed) > .002):
            raise ValueError("common quality end-to-end receipt missing or stale")
        result.update(end_to_end_sha256=file_sha(wall_path),
                      end_to_end_wall_seconds=elapsed)
    return result


def correction_astra_turns(run: dict, events: list[dict]) -> list[str]:
    """Allow bounded expert follow-ups inside one correction parent turn."""
    attempts = [attempt for attempt in run.get("attempts", [])
                if attempt.get("quality_revision") == 1]
    if len(attempts) != 1:
        raise ValueError("treatment quality correction attempt missing or duplicated")
    correction = attempts[0]
    submitted_ns = events[correction["stage_event_start"]].get("time_ns")
    linked = [turn for item in run.get("native_attempts", [])
              for turn in item.get("turns", [])
              if turn.get("parent_turn_id") == correction.get("turn_id")]
    astra = [turn for turn in linked if turn.get("model") == "gpt-6-astra" and
             turn.get("effort") == "xhigh"]
    if not astra or type(submitted_ns) is not int:
        raise ValueError("treatment correction Astra hard analysis missing")
    ids = []
    for turn in astra:
        try:
            finished_ns = int(datetime.fromisoformat(
                turn["terminal_timestamp"].replace("Z", "+00:00")).timestamp() * 1e9)
        except (KeyError, AttributeError, ValueError, TypeError):
            raise ValueError("treatment correction Astra terminal time missing") from None
        if (turn.get("terminal") not in ("task_complete", "task_completed") or
                turn.get("parent_turn_id") != correction.get("turn_id") or
                not isinstance(turn.get("turn_id"), str) or not turn["turn_id"] or
                finished_ns >= submitted_ns or turn["turn_id"] in ids):
            raise ValueError("treatment Astra follow-up lineage or timing differs")
        ids.append(turn["turn_id"])
    return ids
