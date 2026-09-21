# Evaluation contract, version 2

## Decision

Continue using Python's standard library and unittest, matching the existing
offline evaluator. No model calls, new package manager, or live benchmark runner
are introduced. Validation, campaign integrity, analysis, and raw cost import
remain separate functions so deterministic tests can exercise each boundary.

Version 2 intentionally rejects version 1 records. Those records did not contain
enough evidence to validate accounting or experiment identity; silently supplying
defaults would manufacture evidence.

## Integrity and accounting

The manifest pins the case file by SHA-256 and defines the exact Cartesian grid
of cases, repetitions, and two variants. Records bind to the manifest's byte hash.
Every slot has one terminal record, including timeouts, cancellations, and errors.
Run IDs are globally unique; pair IDs identify exactly one case/repetition.
Duplicate JSON keys, duplicate slots, extra slots, and missing slots invalidate
the campaign before analysis. Comparisons never select a favorable subset.

The cost ledger contains exclusive per-session totals, with verified provider,
USD currency, billing/estimation method, pricing version, and evidence references.
Its session IDs must equal the trace inventory across controller, workers, retries,
verification, and abandoned work. Overlapping role membership is allowed; each
session is billed once. In-session retries belong in that session's exclusive
total. Coverage and provider verification are explicit harness attestations,
not facts that can be inferred from the number of supplied IDs. Ledger totals
must reconcile with the run total. All compared complete ledgers must use the
same accounting convention. API-price estimates are labeled as estimates.

The legacy ccusage command is an unverified observation importer only. Its output
cannot establish provider identity or discover descendants; no command-line flag
can turn those observations into complete run accounting.

## Statistical scope

The current suite consists of draft scenarios, not executable benchmark tasks.
Only draft manifests/cases are supported. A draft can fail a gate or be
inconclusive, but cannot produce a release pass. Supporting release campaigns
requires a separately reviewed fixture, grader, power, and provenance contract.

The declared exploratory estimand weights independent clusters equally, cases
equally within each cluster, and repetitions equally within each case. Related
tasks must share a cluster ID (for example, a repository family). The supplied
scenarios all share one cluster and therefore cannot estimate generalization.

Resample entire paired clusters, preserving both variants and every repetition.
Report deterministic percentile bootstrap intervals for paired differences and
ratios of weighted means. Cost uses total spending per submitted task, including
failed attempts, rather than mean individual ratios or median spending. Latency
uses the same request-to-terminal boundary in both arms, including timeouts;
it is mean elapsed time, not p95 or verified-success latency.

For binary quality, an all-success bootstrap would misleadingly have zero width.
Use bounded-mean Hoeffding intervals across independent clusters for the quality
gate and absolute success floor, and for the rubric-score difference. These
conservative intervals do not collapse on all-success samples. Their validity
still assumes independent, representative clusters; bootstrap intervals are
exploratory and are not a substitute for a preregistered power analysis. A minimum
cluster count is a reporting guard, never a power claim.

Undefined ratios have null intervals and an explicit reason, never epsilon
denominators or silently discarded resamples. Missing cost leaves quality and
latency usable. A demonstrated failure takes precedence over missing evidence.
Safety gates apply separately to each router category with zero tolerated
violations; baseline incidents cannot offset them.

## Remaining boundary

The evaluator validates a supplied contract and evidence references. It does not
authenticate receipts, execute fixtures, verify hook delivery, grade artifacts,
discover child sessions, enforce an append-only store, or prove preregistration.
These must be supplied by a trusted harness before industrial release claims.
