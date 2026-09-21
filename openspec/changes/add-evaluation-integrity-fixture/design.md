## Context

The implementation is schema v1 but the previous evaluation design describes an
unimplemented v2. Identity sets can hide duplicate scheduled slots; missing cost
can downgrade proven quality/safety failures to inconclusive. The original MVP
specification still allows direct controller work.

## Decisions

Continue Python standard library and unittest. Separate strict contract loading,
tree grading, statistical decisions, and deterministic campaign generation.
Document the exact v2 object shapes in `evals/CONTRACT.md`; validators reject
unknown fields, duplicate keys, booleans in numeric fields, and non-finite values.

Pin cases, fixture manifests, evidence, candidate trees, and the experiment with
SHA-256. Use canonical relative POSIX paths confined to their declared root and
reject symbolic links/reparse points. Use one record for every scheduled slot,
globally unique run IDs, and a bijection between pair IDs and case/repetition.
Require matching environment controls and disjoint session inventories per run.

Keep the first slice intentionally narrow: one deterministic documentation typo
task. Grade the complete file and directory inventory plus byte hashes; do not
execute candidate code. Failed oracle results override reported success. A
hash/provenance error invalidates the input before statistical decisions.

Use paired exploratory bootstrap intervals for pass-rate differences and ratios
of mean cost/latency. Undefined or overflowing ratios emit null and a reason.
Release pass remains disabled in this integrity slice: fixtures do not establish
power, representative sampling, authentic accounting, or live policy compliance.
Draft/synthetic campaigns can fail or be inconclusive. Demonstrated gate failures
precede missing evidence; absent accounting is not a measured cost failure.

Complete accounting requires a hash-bound exclusive session ledger matching the
trace inventory and run total. ccusage observations never qualify, regardless of
user flags. Ledger provenance is validated, not authenticated; synthetic ledgers
exercise the implementation without claiming provider measurements.

## Alternatives and trade-offs

Reject v1 rather than infer missing identities. Use explicit validators rather
than add a JSON Schema dependency. Keep statistical claims exploratory rather
than ship the unimplemented cluster/Hoeffding design. Preserve historical specs
and mark this change as superseding their conflicting requirements.

## Risks and boundaries

Local hashes detect mismatch, not malicious replacement of all inputs or runtime
TOCTOU attacks. A future trusted harness must authenticate provider receipts,
observe complete descendants and terminal runs, freeze campaigns before execution,
and add independent clusters, holdouts, and a reviewed statistical release gate.
