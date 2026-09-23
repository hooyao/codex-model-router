# Design: Selective execution topology

## Decision sequence

1. Validate structured facts and config matches.
2. Resolve ownership: escalation and hard/unknown signals, then effective
   configured mode, with DIRECT allowed only for fully known bounded work.
3. For DELEGATE, select PARALLEL only when multiple tasks are bounded,
   independent, dependency-free, and write-disjoint; otherwise select
   ISOLATED_SERIAL.
4. Resolve SELF_CHECK or INDEPENDENT_REVIEW independently from ownership.
5. Copy depth, concurrency, retry, write, and context-transfer constraints into
   the decision receipt.

The resolver has no I/O, clock, randomness, model call, or runtime-capability
assumption. Model and effort resolution remains a later DELEGATE-only runtime
step. Configured concurrency is a ceiling; the controller lowers it to the
actual exposed runtime capacity and does not infer missing spawn features.
The bundled CLI wrapper is the only I/O boundary: it loads the validated config,
accepts one request on stdin, calls the pure resolver, and emits one result.

## Context isolation

Isolation means minimized worker packets and compact receipts with artifact
references. Hooks cannot erase inherited parent history or create a technical
sandbox, so policy text makes neither claim. Actual runtime isolation may be
reported only when the runtime exposes and verifies it.

## Safety invariants

DIRECT and DELEGATE retain user intent, permissions, safety constraints,
exclusive write ownership, verification, retry, depth, and concurrency limits.
Dependencies, shared mutable state, and overlapping writes prohibit PARALLEL.
Every reclassification from DIRECT records one bounded trigger.
Named multi-step runbooks are mandatory-delegation signals. Evaluation traces
also reject controller business actions on DELEGATE, DIRECT with independent
review, and completed independent review without distinct reviewer-session
evidence. Failed runs remain observable when they terminate before review.
