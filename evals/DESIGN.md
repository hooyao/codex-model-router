# Router scenario benchmark design

## Product questions

The benchmark asks whether selective execution improves the work the router is
designed to coordinate:

1. Can a read-heavy investigator isolate noisy context and pass a compact,
   reusable receipt to a small implementation step?
2. Can an initially bounded direct task stop, record scope expansion, and move
   dependent stages to serial delegation?
3. Can independent tasks with disjoint artifacts overlap and shorten the
   critical path without conflicts?
4. Does architecture/high-risk work receive distinct independent review?
5. Does the router avoid delegation overhead on one small direct control?

Each scenario is compared across forced direct, mandatory serial delegation,
and selective policy. The direct control calibrates overhead; it is not the
benchmark's center of gravity.
Artifact quality is identical across treatments when their outputs are correct.
Route, review, receipt, escalation, dependency, and topology requirements are
reported separately as process compliance, so forced DIRECT is not penalized
for intentionally absent delegate machinery.

## Deterministic runner

`scripts/runner.py` is a no-model fixture runner. It copies exact reference
artifacts and emits deterministic, hash-bound synthetic observations for route,
span, context, receipt, log, tool, cost, retry, and conflict fields. This tests
the harness and analysis contract, not live agent performance. A future live
collector must emit the same terminal-record schema and mark observations and
costs honestly.
The runner does not invoke the plugin resolver: forced treatments and selective
route expectations are frozen scenario inputs. Route adherence therefore means
trace conformance to the scenario, not measured routing accuracy.

Span schedules make serial and parallel behavior inspectable. Artifact paths
have one owner. Critical path is the observed span schedule extent, while wall
time includes orchestration overhead. The evaluation does not substitute token
counts for time or estimated dollars for measured billing.

## Analysis

The report keeps every scheduled run and summarizes completion, exact quality,
route adherence, wall and critical-path time, context/receipt volume, tool/log
volume, retries, conflicts, and cost provenance per treatment and case. It also
surfaces validated receipt links and linked-consumer context. No statistical
release gate or performance claim is implemented for synthetic data.
Effective quality is recomputed from frozen artifact checks. Process compliance
is independently recomputed from the frozen route, required artifact paths,
dependency/receipt edges, span schedule, and review evidence. A record's quality
claims cannot remove or override artifact acceptance.
Completed observations prove the entire frozen graph. Failed observations may
stop at a valid executed prefix so an early planning blocker remains observable;
prefix validation prevents a downstream stage from appearing without its frozen
prerequisites or consumed receipt.
Route decisions form a time-indexed ownership state machine over business spans.
Parallel overlap must represent distinct worker sessions, not multiple spans in
one session. Route adherence intentionally excludes absolute elapsed values so a
scaled but correctly ordered execution remains semantically compliant.
