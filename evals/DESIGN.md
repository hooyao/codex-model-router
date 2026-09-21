# Evaluation integrity design, version 2

## Implemented boundary

Continue Python's standard library and unittest, with no model calls or new
package manager. `contract.py` validates structured inputs, identity and
accounting; `fixture.py` verifies exact trees; `evaluate.py` validates campaigns
and computes decisions; `campaigns.py` generates deterministic test inputs.

The executable contract and [CONTRACT.md](CONTRACT.md) supersede v1 and the
previous aspirational v2 design. No defaults manufacture missing evidence. The
contract rejects unknown fields, duplicate JSON keys, non-finite measurements,
malformed thresholds, empty required data, unsafe paths, and mismatched hashes.

Cases pin fixture definitions; definitions pin initial/reference file hashes.
The experiment pins the cases file. Every record pins the experiment's bytes,
exact environment controls, result-tree digest, and evidence file hashes. Paths
are confined relative names, with links/reparse points rejected before reading.
Fixture hashes use LF bytes, enforced by `.gitattributes` for cross-platform
checkouts. Tree digests include file hashes and directory inventory, not mtimes
or permissions. Permissions/executable bits are outside this documentation task.

The exact Cartesian case/repetition/variant denominator must be present. Duplicate
slots cannot hide behind a set or overwrite one another. Pair IDs form a
bijection with case/repetition; run IDs are unique across the entire supplied
record file. Sessions cannot belong to multiple runs. Timeouts, cancellations,
errors, and blockers remain terminal records with failed quality and score zero.

## Accounting

The supplied ledger contains exclusive per-session USD totals. Its IDs must
equal the trace inventory for controller, workers, retries, verification, and
abandoned work. A session may have multiple trace roles but is billed once.
The controller cannot be listed as its own worker. In-session retries are
included in that session's exclusive total. Ledger provenance and totals must
match the run; compared ledgers must share provider, currency, method, and
pricing version. API-price estimates remain labeled estimates in the input.

The ccusage importer cannot assert completeness, identify a trusted provider,
or discover descendants. Its observations are never a complete ledger. Hashes
and ledger structure are integrity checks, not signatures or receipt
authentication. A supplied harness ledger still depends on a trusted collector;
synthetic ledgers exercise this boundary using invented numbers.

## Analysis and decisions

Use paired bootstrap resampling (2,000 samples, seed 17) over every scheduled
case/repetition, preserving both variants. Every pair has equal weight. Quality
uses the difference of mean effective pass indicators. Cost and latency use
ratios of means, including failed attempts, rather than means of individual
ratios. Elapsed time is intended to cover request-to-terminal time, including
worker, retry, and verification time; the offline evaluator cannot observe that
boundary itself.

The bootstrap is exploratory. Repeated runs of the same fixture are not
independent task clusters; all-success binary intervals can collapse to zero
width. No cluster model, Hoeffding gate, power calculation, rubric interval,
p95/median measurement, token aggregation, or routing-accuracy estimator is
implemented. These limits prevent any release pass in this slice, including for
a manifest labeled release. This replaces the earlier design's unimplemented
claims with behavior exercised by deterministic tests.

Undefined zero-denominator or overflowing ratios have null estimates/intervals
and reasons. If even one bootstrap resample is undefined, the interval is null;
the valid point estimate may remain. Incomplete cost yields no cost ratio. A
false measurement/policy gate always takes precedence over missing evidence or
release readiness. With no false gate the result is inconclusive. Invalid input
is a separate error before analysis, not a selected or omitted observation.

## Scope and policy

The baseline may execute business work directly; router business work always
belongs to workers. The oracle runs as an offline evaluation tool or assigned
verification-worker check; it does not authorize controller business inspection.
Router meta-task review remains the controller's separate assignment. This
implementation worker does not discover capabilities or delegate.

Exactly one public fixture-backed task is shipped. The grader verifies the
artifact; it cannot verify the claimed validation command, hidden thought
process, lifecycle-hook delivery, or truth of a route trace. Local hashes do not
prevent coordinated replacement of all artifacts or concurrent filesystem
mutation during a check. Trusted collection, immutable snapshots, authenticated
receipts, hidden holdouts, and independent review remain future harness work.
