# Design: Router scenario evaluation

## Scenario matrix

The primary cases cover noisy investigation with a reusable receipt,
direct-to-delegate serial escalation, independent disjoint parallel work, and
architecture with distinct review. A typo correction remains only as direct
overhead calibration.

Each case has three treatments: forced direct, mandatory serial delegation, and
selective policy. Exact-tree fixtures freeze acceptance. Route expectations are
data, not inferred from run output.

## Evidence model

Terminal records bind to the manifest and include route transitions, timed
spans, dependencies, exclusive write paths, context and receipts, tool/log
volume, quality checks, reviewer identity, retries/conflicts, cost provenance,
and terminal outcome. Failed runs stay scheduled.

Dependency timestamps, controller/worker/reviewer session roles, artifact
authors, and review independence are derived from spans. Receipts bind canonical
content hashes to producer and consumer spans; required facts come from the
frozen case. Effective quality is recomputed from required case checks rather
than trusted record claims.
Artifact quality and treatment-specific process compliance are separate.
Cases freeze required artifact paths plus dependency and receipt-consumer edges
for every treatment. The first-delegation timestamp comes from route events and
controller spans may not cross it.
Completed runs match the entire frozen graph. Failed terminal records may carry
a downward-closed executed prefix, preserving early blockers without permitting
impossible downstream stages or invented edges.
Route events form a chronological ownership state machine over every business
span. Parallel overlap requires distinct worker sessions. Semantic adherence
excludes absolute elapsed timestamps, which remain contract-validated.

The bundled runner is deterministic and makes no model call. Its observations
exercise contract and report semantics only. A future live collector may emit
the same schema but must label observations and costs honestly.
It accepts synthetic manifests only and emits matching synthetic evidence. It
uses frozen route expectations rather than invoking the resolver, so it does not
measure routing accuracy.
