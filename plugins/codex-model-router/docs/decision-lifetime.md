# Matched modes and decision lifetime

## Observed regression

The [1.3.2 investigation cohort](../../../evals/live/context-refinement-20261028/REPORT.md)
failed its median wall/token thresholds. The retained native trace identifies
two actionable causes; smaller injected context alone did not remove them.

In repetition 1, every direct predicate was satisfied, but the request matched
both `luna-extraction` (`evaluate`) and `luna-local-bounded-change` (`direct`).
`configured_execution_mode` treated any two distinct mode strings as a
conflict and returned `delegate`. This reproducibly caused
`configured-delegate`, delegated-work reference/capability reads, and an
extraction worker. The run used 21 requests versus the comparator's 6.
That outlier alone does not explain the median regression: repetition 2 still
used 7 versus 5 requests and regressed in wall time and tokens while staying
DIRECT. Its directory/help discovery and extra decision are separate targets.

In all three after repetitions, the controller split source extraction from
the receipt-consuming edit into separate decisions; the first also resolved
another decision between implementation inspection and editing. The hook,
descriptor, and skill said every new read/edit/test needed a fresh decision,
even though a DIRECT stage already included implementation and self-check.
That contradicted the same-owner stage-batching guidance, which was available
only after loading the delegation reference.

The cold entrypoint also named `describe_router.py` relative to the resolver
rather than exposing its absolute path. Directory/help/source discovery added
model rounds in observed direct and investigation runs.

## Decision

Compose matched modes before the unchanged hard-signal gate:

| Matched modes | Effective mode |
| --- | --- |
| No match | Workspace default |
| Only evaluate | evaluate |
| direct, optionally evaluate | direct |
| Any delegate | delegate |

`evaluate` defers to the ownership predicates; it is not an opposing ownership
directive. An explicit matched delegate still wins over direct. Unknown or
mandatory-delegation facts, independent review, reclassification, configuration
validation, and existing workspace overrides remain binding for schemas 3/4.
No workspace config is rewritten.

Resolve once before a request or separately owned stage. That decision covers
the same owner's declared source reads, hash-bound receipt preparation and
consumption, bounded edit, and self-check. A file, command, receipt, or tool
round is not itself a new stage. Preserve actual source/hash/preimage checks.

A new business request, separately owned stage, changed configuration or
decision inputs, expanded scope, failed validation, changed permissions, risk,
or a real prerequisite/recovery boundary requires a new decision or recorded
reclassification before further work. Separate independent units and review
owners remain separate. Existing whole-request DELEGATE and inherited review
obligations cannot be downgraded by coalescing steps. Status-only replies may
reuse accepted results; lost context is not evidence that a decision survives.

Advertise the exact descriptor path and its `--config` usage in the cold
entrypoint, together with the resolver's stdin contract. Current decision
context is reused until invalidated; unchanged prompt hooks remain suppressed.

## Validation boundary

Regression tests exercise the observed matched IDs through the real resolver
CLI, all mode combinations/orderings, legacy/current config schemas, unknown
and mandatory signals, explicit delegation, and scope escalation. An offline
trace replay can demonstrate the corrected deterministic choice and the hook
budget; it cannot measure model compliance, elapsed time, token savings, or
provider charges. Preserve the failed 1.3.2 scores and run a separately
authorized, predeclared matched live cohort before claiming performance repair.
