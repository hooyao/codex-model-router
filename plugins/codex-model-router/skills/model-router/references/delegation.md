# Delegated work

Load for a DELEGATE decision; retain current instructions across later stages.
The worker follows its packet and role override, not these controller rules.

## Plan and capability

Build a DAG with stable stage IDs, owner, dependencies, exclusive write scope,
context budget, acceptance criteria, and self-check. Record bounded retries.
Use PARALLEL only when multiple bounded tasks are independent, dependency-free,
and write-disjoint, as confirmed by the decision resolver. Otherwise use
ISOLATED_SERIAL; dependencies, overlapping writes, and shared mutable state
serialize. Configured depth, concurrency, and retries are ceilings, lowered to
actual runtime limits. Never invent worker slots or APIs.

A controller-owned easy stage must independently resolve DIRECT, inheriting
the request's permissions, risks, and independent-review requirement. Use a
cheap worker when easy-stage tool output would burden controller context.
Worker-owned analysis, implementation, and testing remain with that worker.
Reserve Astra/xhigh for the demanding hard kernel, not the entire mixed task;
max is exceptional. Reclassify any stage that exceeds its approved bounds.

Discover actual native spawn/wait/collect tools and supported models/efforts.
Missing required capability blocks delegation; do not create user-facing
threads to simulate workers or silently perform their work in the controller.
A failed dispatch permits only the declared bounded retry/escalation, then a
blocker. Minimized packets and receipts are logical context boundaries, not
proof that inherited history was erased or technical sandboxing exists.

Before every spawn, run hooks/dispatch_contract.py with the hash-bound record
from [dispatch-contract.md](dispatch-contract.md). Validate actual selector and
naming support separately from packet identity, later metadata, and worker
echoes. Omitted selectors do not prove inheritance; unresolved placeholders
block dispatch. The verified_role_config mode remains blocked from dispatch
authorization pending independently verifiable calibration and trusted hook
capture. Reuse contract text and still-current evidence; do not reread them
merely because another spawn occurs.

## Packet and identity

Freeze a short English purpose label and construct a canonical
`<purpose>-<model>-<effort>` name from the actual selector values. Normalize each
component with Unicode NFKD, discard non-ASCII, lowercase, replace non-[a-z0-9]
runs by a hyphen, and trim hyphens. Reject empty components; purpose/model/effort
limits are 48/48/24 and the whole name limit is 128 characters. Recompute and
check uniqueness across the DAG. Unchanged retries retain the name; do not add
random values, timestamps, retry counters, or generic fallbacks.

Use a supported native name field for the canonical value. An underscore-only
`task_name` receives the deterministic hyphen-to-underscore adaptation, with the
same length and uniqueness checks. Never pass a rejected hyphenated name to
that field. If neither field exists, omit it and record unavailable. The
canonical ID still belongs in the packet and result; a native card title may
differ. The dispatch validator uses hooks/subagent_naming.py for these checks.

Include these packet fields, using only task-relevant context:

- Worker name and Task ID: the identical canonical purpose-model-effort value.
- Native task name: the actual transport value or unavailable.
- Objective and role: analysis, implementation, review, integration, or validation.
- Dependencies, allowed sources/files, exclusive write ownership, and topology.
- User intent, permissions, safety constraints, acceptance criteria, self-check.
- Resolved model/effort, context budget, depth/concurrency/retry/stop limits.
- Return contract: identity, outcome, evidence, validation, risks or blockers.

Workers perform the assigned analysis, inspection, edits, commands, tests,
and substantive validation directly. A read-only packet permits no edits;
implementation requires code, not review advice. Workers must not dispatch
subworkers; needs outside the packet return to the controller for routing.

## Collect, integrate, and verify

Wait for prerequisites and collect compact receipts with artifact references.
Validate identity, completeness, consistency, evidence references, and reported
acceptance status. Those structure checks do not prove business correctness.
Request bounded correction for missing evidence or failed checks; retain
successful results across partial failures and disclose unresolved blockers.

Workers validate their substantive stages; the controller self-checks its own
stages and owns overall verification status and final synthesis. An independent
reviewer must be distinct from the author/implementer when required, including
router meta-tasks. A review-only worker does not fulfill implementation.
Delegate substantive disagreements to a reviewer. Assign file conflicts to an
integration worker unless a separately approved DIRECT stage covers them;
require the responsible owner to validate the integrated artifact.

This is instruction-layer policy. Hooks cannot erase history, create a
sandbox, or establish an OS/tool permission barrier. Do not claim a runtime
guarantee from packet validation or a worker's self-attestation.
