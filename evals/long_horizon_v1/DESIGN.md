# OCI reference-aware snapshots: long-horizon benchmark v1

Status: design for implementation and independent review. No live model run,
oracle calibration, difficulty result, or performance claim is established by
this document. This is a local evolving Flipt task, not an official SWE-bench
task. Its stable task ID is `flipt-oci-reference-rollout-v1`.

## Question and chosen task

Can a lightweight controller complete a substantial repository change through
successive commissioning problems, using Astra/xhigh for bounded difficult
decisions as new evidence arrives, at equal quality and lower total USD than
Astra/xhigh doing the whole evolving task alone?

Implement reference-aware OCI feature snapshots in the existing Flipt fixture.
The initial change lets requests select a tag or immutable manifest digest
instead of silently receiving the configured default. A second commissioning
round introduces concurrent mutable tags, aliases, and failure isolation. A
third introduces cancellation, partially consumed streams, and shutdown during
active work. Each round includes implementation, local investigation, regression
checks, and a checkpoint. The final deliverable includes an integrated patch,
regression tests, maintainer documentation, and evidence-based release notes.

This is one evolving feature with interacting invariants, not a collection of
unrelated small edits. Later requirements are withheld until the preceding
checkpoint so neither arm can consume all evidence in one initial delegation.
The user-facing initial request explicitly discloses that two commissioning
reports will arrive. The reports describe behavior and supply reproductions;
they never prescribe locks, caching structures, goroutines, an algorithm, a
stage owner, or a number of Astra calls.

The difficulty hypothesis is that reference identity and compatibility require
an initial design, ordering and failure isolation require reanalysis of the
implemented design, and cancellation/resource ownership require a further
analysis across interfaces. Discovery, preparing fixtures, executing controlled
replays, selecting known test commands, extracting results, and documenting
settled behavior offer useful cheap work throughout. A pilot must test that
hypothesis. Three messages alone do not prove three difficult episodes.

## Fixed source and observed integration boundaries

Reuse these immutable inputs by hash; do not modify the prior fixture or its
evidence, and do not seed from either lifecycle pair-03 candidate:

| Input | Existing source | Identity |
| --- | --- | --- |
| Source archive | `evals/paired_selective/assets/source-layer.tar.gz` | SHA-256 `d936e33490bfbfc686871c236abfde86b629c4aa153cc6220a108d05c5827dfe` |
| Extracted original tree | Existing archive without image Git history | Git tree `20121a7049c58571664b87989d1fc7bab8562884` |
| Authentication seed patch | `evals/paired_selective_lifecycle_v1/assets/seed.patch` | SHA-256 `3d824219a77b11d8b6f6ec506403daad092594d07c9a697a3c2280a2c935c74b` |
| Task start tree | Original tree plus that seed patch | Git tree `3354dd97a389bac41536cc1cfa4ffe4861912bc8` |
| Dependency lock | Seed `go.sum` | SHA-256 `96e3e2be26f962e5e058ffcc7485f5d6dd2f99526423e041d8223943c08d4373` |

The seed already supports static/ECR authentication; this task does not ask for
the lifecycle cache from the old experiment. Keep ordinary authentication and
configuration regressions in the quality gate.

Inspection of the seed identifies concrete integration work:

| Seed location/symbol | Current behavior and relevance |
| --- | --- |
| `internal/storage/fs/store.go`, `ReferencedSnapshotStore` | Existing transaction interface accepts a `storage.Reference`; storage operations already forward it. |
| `internal/storage/fs/store/store.go`, OCI factory branch | Wraps OCI in `NewSingleReferenceStore`, which drops a nonempty request reference. |
| `internal/storage/fs/oci/store.go`, `SnapshotStore`, `View`, `update` | Holds one snapshot and one last digest; a reader callback holds the read lock. |
| `internal/storage/fs/git/store.go`, `View` | Existing reference-aware implementation provides compatibility context, not a required algorithm. |
| `internal/storage/fs/cache.go`, `SnapshotCache` | Existing reference/content cache may be reused or left alone; its behavior must be assessed before reuse under new concurrency. |
| `internal/oci/file.go`, `Fetch`, `fetchFiles` | Fetch returns a digest computed after removing manifest annotations; the registry manifest digest and this change-detection digest have different meanings. Fetch opens multiple layer streams. |
| `internal/storage/fs/snapshot.go`, `SnapshotFromFiles`, `SnapshotFromPaths` | Builds snapshots from multiple owned streams; early failure and cleanup affect all backends. |
| `internal/storage/fs/poll.go`, `Poll`, `Close` | Shared background lifecycle used by OCI, Git, local, and object stores. |

Go remains the product language. The evaluator uses Python 3.9+ standard library
and Go tests, following the existing harness; no new framework or package
manager is needed. Loopback OCI HTTP fixtures and local OCI layouts replace
external services. There are no real cloud credentials or live registry calls.

Create a fresh seed and two arm copies without solution history. Benchmark
instructions and revealed replay files are a separately hashed overlay, outside
the product tree identity. Freeze archive, patch, prompt, round files, oracle,
control patches, replay schedule, toolchain, dependency cache image, CLI,
plugin, rate card, and schemas in a new manifest. Paths to host tools are
resolved during preparation and checked by hash/version rather than copied
blindly from the old Windows checkout.

## Shared user request: round 0

The following is the task text to freeze, followed by an identical environment
and checkpoint note for both arms:

> Add reference-aware OCI snapshots to Flipt. Today, storage requests carrying
> a reference silently use the configured OCI snapshot. The OCI SnapshotStore
> must support the existing ReferencedSnapshotStore contract, and storage
> construction must pass references through to it. Update affected callers and
> tests. An empty reference selects the configured default. A nonempty reference
> selects a tag or a complete sha256 manifest digest within the configured
> repository. Reject malformed references and inputs that attempt to change
> the registry, repository, or transport. A digest means the registry manifest
> digest, including its annotations.
>
> Initialize the configured default before serving reads and keep polling it.
> An explicit tag request must resolve the tag when that request starts a new
> fetch; overlapping callers may share a fetch already in progress. Immutable
> digest requests may reuse verified content. A View callback must see one
> complete snapshot for its entire duration. Preserve existing feature parsing,
> OCI manifest versions, static/ECR authentication, and non-OCI backends.
>
> Keep the default reference resident and retain at most three additional
> inactive reference entries after operations settle. Active Views may retain
> their snapshots until their callbacks finish. Eviction must not invalidate
> an active View. Document what is cached and how a tag differs from a digest.
>
> Investigate, implement, and test this feature in the supplied checkout.
> Commissioning has three rounds: after each of the first two checkpoints the
> evaluator supplies the next fixed report and a runnable reproduction. Those
> reports add acceptance requirements. Submit your first checkpoint when this
> initial contract works; do not wait for a guessed future requirement. At the
> final checkpoint supply the integrated patch, regression tests, maintainer
> documentation, and a concise account of checks and remaining limitations.

The initial overlay includes a tiny loopback registry/local-layout replay tool,
usage documentation, and round-0 smoke tests for default, tag, digest, invalid
reference, and factory pass-through. It contains no future scenario fixtures.
The replay tool reports HTTP events and observed feature values; its output
does not recommend implementation changes. It uses the existing public APIs
and compile-safe interface assertions so the unchanged seed can fail a behavior
assertion without failing to compile solely because a new API is absent.

The retention bound concerns reference metadata and parsed snapshots. It does
not require replacing ORAS's underlying blob store. The public contract may
use a test-only diagnostic to observe settled retention, but the hidden oracle
must not depend on a candidate's field names, eviction policy, or exact object
count. If no neutral retention observation is feasible, evaluate this bound
with the frozen semantic rubric and label it accordingly, not with a guessed
heap threshold.

## Deterministic commissioning reports

Freeze the complete text, test data, and replay schedules before either arm
runs. Every report says "commissioning scenario" rather than claiming that a
specific candidate failed. Include the actual replay result separately. A
candidate which already meets a newly revealed requirement may retain its code
and demonstrate why; never manufacture a regression to force another worker.

| Gate | Newly revealed evidence and cumulative acceptance | Hard decision exposed |
| --- | --- | --- |
| G0: initial checkpoint | Admit the round-0 contract and its focused checks. Reveal report R1 and its public reproducer. | Initial interface, reference identity, cache ownership, and factory integration. |
| G1: after R1 | Concurrent callers access tags `stable` and `canary`, initially aliases for D1; `stable` later moves to D2 while `canary` remains D1. A delayed old result and a later successful observation are reordered. One unrelated tag returns a broken multi-namespace manifest. | Reconcile resolution order, alias identity, atomic publication, isolation, and last-good state across the first implementation. |
| G2: after R2 | Hold one View open while polling and shutdown proceed; cancel the initiator and another waiter independently; inject a layer read/open failure after other resources were acquired. Then restore the same reference and replay successfully. | Reconsider in-flight ownership, cancellation, callback lifetime, cleanup, and compatibility with the shared parser/poller. |

R1 states these precise observable requirements:

1. Tags aliasing the same digest may share immutable snapshot data. Updating
   one tag must not redirect another tag or a digest request. Manifest identity
   must retain the distinction between registry digest and any internal
   annotation-insensitive change detector. Include two manifests with identical
   layer descriptors but different valid annotations and thus different digests.
2. After a newer successful resolution/publication for a tag is observable,
   completion of older work must not make subsequent readers of that tag see
   the older result. Serialization or coalescing is allowed; the oracle must
   not require overlapping remote fetches for the same tag. A View already
   holding an old snapshot remains valid.
3. A candidate is published only after every namespace validates. Failed
   polling keeps the default's last complete good snapshot, and later polling
   can recover. An explicit request whose necessary fetch fails returns an
   error instead of silently substituting a cached older tag or the default.
   A failed reference must not corrupt a different reference or publish only
   part of the failed bundle.
4. A blocked fetch for one reference does not prevent an unrelated cached
   digest/default read or a fetch for another reference from making progress.
   The limit of three extra inactive reference entries still applies after
   concurrent requests settle. No exact registry request count is required.

R2 states these precise observable requirements:

1. A context cancelled before View begins produces that context error and does
   not call the callback. Cancellation during acquisition returns promptly
   with that caller's context error. If acquisition is shared, cancelling its
   initiator or a waiter does not abort another active caller. Sharing is
   optional; correctness and bounded cleanup are mandatory.
2. The lifetime of an entered callback is owned by its caller. The store does
   not attempt to terminate user code. That callback retains a stable snapshot;
   it cannot prevent a newly ready default snapshot from becoming visible to
   new readers. Shutdown must not wait for a callback whose only remaining
   dependency is an evaluator-controlled latch.
3. Close is safe to call concurrently and repeatedly. It cancels background
   acquisition, waits for background work to quiesce, and leaves existing
   callbacks valid. A View beginning after Close returns a stable documented
   closed-store error. Calls still acquiring when Close begins fail promptly;
   the oracle accepts either the documented closed error or context cancellation.
   No new polling requests begin after Close returns.
4. Fetch failure closes every stream acquired by that failed fetch. On successful
   transfer of stream ownership, snapshot construction closes all supplied
   streams on success and on any Stat/read/validation failure, including streams
   it had not yet parsed. Cleanup must not obscure the originating error or
   prevent a later attempt from succeeding. Existing non-OCI parser behavior
   and backend tests must continue to pass.

These are cumulative requirements, not secret requirements introduced only by
the final grader. A fourth model-facing round is not added based on observed
performance. A future requirement change creates a new fixture version.

## Admission, reveal, and session mechanics

Use one persistent parent conversation per arm throughout G0-G2. Restarting the
parent with only a hand-written summary would change the context experiment.
The current `run_paired_arm.py` starts one `codex exec` turn and closes stdin;
it is not a staged runner. Implement a new runner that uses a runtime-verified
multi-turn transport. The App Server probe already exercises `thread/start`
and `turn/start`, but multi-turn completion, child accounting, plugin binding,
and cancellation need dedicated tests. A CLI resume alternative is acceptable
only after proving it preserves the same session and selectors. Never infer
that a fresh process is a fresh conversation or that a resumed conversation
has complete usage accounting.

The shared protocol is:

1. Submit the exact round-0 task bytes and environment overlay. Start the one
   wall clock immediately before that submission. Each arm uses the same
   transport and controller-facing checkpoint protocol.
2. At the end of a turn the agent submits `checkpoint.json` with schema version,
   current round, candidate tree/patch hash, relevant local check receipts,
   completion summary, and requested action `submit`. Pending workers and
   writes must finish first. The evaluator independently captures the patch;
   agent-authored hashes alone are not authoritative.
3. The evaluator runs the frozen cumulative *public* gate against a copy of
   that patch. Gate execution is measured inside the wall boundary. A gate
   checks the correct round, parseable checkpoint, build, and that round's
   public behavior tests; it never inspects model identity or delegation count.
4. On a passing G0/G1, append the next fixed report to the same conversation
   and materialize only that report's hashed public replay files. Include a
   machine-generated receipt describing the preceding gate. The report bytes,
   artifact bytes, and reveal order are identical across arms; actual failure
   details can differ because patches differ.
5. On a failed public gate, return its bounded stdout/stderr and structured
   failed case IDs. Allow one repair submission for that round. There is no
   automatic hidden-test hint and no advance to the next round on failure.
6. On a passing G2, capture the final response and required artifacts, stop the
   wall clock, and run the hidden final oracle offline on a clean replay tree.
   There is no model repair from hidden results. Include this grader's wall time
   separately; it is outside both arms' task-completion boundary.

An incomplete/no-checkpoint final response consumes the round's repair allowance
and receives the same deterministic protocol error in either arm. All admission
failures and partial work are retained. Do not gate progress on a long narrative,
line count, elapsed minimum time, model-call count, number of edits, or novelty
of the patch. A candidate that meets all public rounds but fails hidden tests
still has its complete measured cost and a failed quality outcome.

Gate state is evaluator-owned, append-only, and checksummed. The runnable
workspace contains only currently revealed assets. Keep this design, later
reports, positive controls, and hidden oracle outside both arms' visible task
material. Placement plus instruction boundaries are not technical isolation.
If the runtime permits broader reads, state that limitation and audit reads;
do not claim that withheld files were inaccessible.

## Arms and natural routing

Baseline: one `gpt-6-astra/xhigh` parent, no router and no delegated workers.
Treatment: one predeclared lightweight parent, initially `gpt-6-sol/low` unless
a separately evidenced calibration selects Luna, with the pinned router and
runtime-available Astra/xhigh workers. Resolve all selectors and supported
efforts against runtime evidence before the campaign; fail preflight if the
declared comparison is unavailable. Do not silently substitute another model.

Both arms receive the same business task, reports, permissions, public tools,
checkpoint protocol, and limits. Arm execution configuration is separately
versioned: baseline disables delegation; treatment explicitly authorizes native
delegation when policy calls for it. This authorization contains no mandated
hard-worker name, file split, quota, or solution plan. It must allow dynamically
declared, validated dispatch contracts at later rounds. Preserve exact submitted
prompt hashes and identify this unavoidable treatment configuration difference.

At every newly bounded stage the treatment records owner, dependencies, exact
write scope, numeric context budget, acceptance, and self-check. Stage plans may
evolve after feedback; retain every version. The controller directly performs
an easy stage only when that stage independently passes the direct gate. Do not
give it open-ended debugging merely because the next action uses a shell.
One possible, nonbinding division for analysis is:

| Work opportunity | Likely owner | Bounded result |
| --- | --- | --- |
| Inventory call sites and execute a known replay | Controller if direct-qualified, otherwise cheap worker | Paths, observable events, exit codes, compact evidence pointers |
| Initial reference semantics and core implementation | Astra when difficulty warrants it | Reviewed interface/invariant decision and bounded code/test patch |
| Mechanical caller updates and known fixture construction after interfaces settle | Controller or suitable cheap worker | Disjoint patch satisfying the settled interface |
| Diagnose R1 evidence against current patch | Newly bounded Astra assignment when warranted | Causal explanation, revised invariants, repair and focused checks |
| Diagnose R2 cancellation/resource failures | Newly bounded Astra assignment when warranted | Ownership/lifetime decision, repair and focused checks |
| Execute cumulative checks; write settled docs and final synthesis | Controller or cheap worker | Actual check receipts, documentation, integrated outcome |

These rows are not injected into the task prompt or enforced by the grader.
Fresh bounded workers or successive bounded assignments to a returning worker
are both allowed; record their different context costs. A worker cannot remain
the implicit owner of all future commissioning work. Future evidence belongs
to the controller until it makes a new routing decision. Controller activity
between hard episodes should include real evidence collection/integration,
not only relaying "continue" to a worker that owns the whole request.

Depth is one, concurrency is at most three including the parent, and worker
retry allowance is one per bounded stage within the global budget. Parallel
work requires absent dependencies and disjoint writes; report recovery is
normally sequential. Reusing a worker is not a retry unless a failed assignment
is repeated. Do not retry a successful stage to obtain a nicer measurement.

Worker packet targets are 12,000 tokens of selected task/evidence context and
2,000 tokens per receipt; these are recorded planning budgets, not grounds for
dropping correctness-critical evidence. An increase requires a revised stage
record and counts in total cost. Use numeric `context_budget` values, avoiding
the descriptive-string failure in pair-03. Do not duplicate worker-owned code
analysis in the controller. Capture semantic packet scope through a trusted
evaluator channel if available; packet hashes and claimed scopes alone do not
prove contents or erased history. Do not retain secrets or unrelated prompts.

Report actual Astra episodes, dispatches, resumed assignments, billed responses,
and stage ownership. A successful treatment with zero or one Astra episode is
valid task evidence but does not establish the repeated-hard-kernel hypothesis.
Likewise, delegation of nearly all semantic work to one continuing Astra is an
orchestration failure for the intended mechanism, even if final quality passes.
No minimum child count or fixed Astra share is a quality gate.

## Hidden oracle and fixture controls

Freeze a held-out oracle before the first live pair. Use a separate clean
candidate replay, arm-blind semantic review, and a single combined quality
receipt; avoid the old split between `grade.json` and a supplemental gate that
the collector could overlook. Quality requires all cumulative requirements,
applicable seed regression tests, candidate tests, race checks, and semantic
review of retention, compatibility, and documentation. Route compliance is a
separate result and cannot compensate for a failed behavior check.

Go tests exercise existing public interfaces, local layouts, and an `httptest`
OCI registry with manifests/blobs generated by a fixed corpus builder. Keep
private helper-field names, exact lock counts, cache architecture, and exact
request counts out of the oracle. The public gate and hidden oracle use
different manifests, reference names, fault positions, and schedules while
testing the same revealed contract. Scenario seeds and allowed schedules are
fixed in the manifest; hidden assertions never leak through gate output.

Use channel/barrier events to control partial reads, withheld responses,
entered callbacks, cancellation, and release ordering. Deadline assertions are
generous failure bounds, not simulated latency or performance measurements.
Support both a same-reference fetch that is serialized/coalesced and overlapping
fetches: release an old request if the implementation legally serializes, then
continue the schedule. Do not deadlock while waiting for a second request that
the contract does not require. Assert published values and progress of unrelated
work. Test cleanup with instrumented `fs.File`/readers through existing parser
contracts and HTTP disconnect events; avoid global goroutine-count thresholds.

Required oracle groups are reference validation/factory wiring; tag versus
manifest identity; atomic multi-namespace snapshots; alias/order/isolation;
last-good default plus explicit-error behavior; retention/lifetime; cancellation;
close; resource release; recovery; and non-OCI compatibility. Focused checks use
`./internal/oci`, `./internal/storage/fs`, and `./internal/storage/fs/oci`.
Integration includes the storage factory and existing config/auth tests. Freeze
the precise offline backend regression command list during preparation after
confirming it needs no external services. Do not call an unexecuted whole-repo
suite a pass. Run focused race tests repeatedly with fixed schedule variants.

Minimum offline controls, run before any paid execution:

| Control | Required observation |
| --- | --- |
| Unchanged seed | Builds under the applicable original suite; the new reference feature gate fails for the intended behavior. |
| Complete evaluator-only reference patch | Every round, final oracle, and required regression/race check passes. It remains outside arm workspaces. |
| Round-0-only control patch | G0 passes, a documented R1 scenario fails, and later-round code is genuinely needed by this plausible implementation. |
| Round-1-only control patch | G0/G1 pass, a documented R2 cancellation/cleanup scenario fails. |
| Mutations of the positive control | Individually detect ignored references; wrong digest identity; stale/aliased publication or global blocking; swallowed explicit errors; leaked later stream; and cancellation/shutdown breakage. |
| Alternative legal strategy/control | At least one focused variant using legal per-reference serialization or no shared acquisition passes the relevant schedule tests. |

The partial controls establish that staged feedback can expose real defects;
they do not prove that a particular model needs help. The complete control
establishes feasibility on this host and a measured test-runtime budget. If any
control is flaky, passes for the wrong reason, or only one implementation
strategy passes, repair and refreeze the fixture before live execution.

## Evidence and cost accounting

Measure one continuous task interval from first submission through accepted
G2 final artifacts. Include activation, every parent turn, children, routing,
public gates, resumed turns, retries, waiting, cancellation cleanup, and final
synthesis. Preserve wall time separately from summed tool or worker durations.
The final offline grader is excluded consistently and timed separately.

Each evidence bundle needs:

- Immutable run identity: start tree, overlay/report hashes, config/plugin/CLI
  identities, runtime catalog and selector evidence, tool/cache state, pricing
  date and service tier, arm order, preparation receipt, and all stop reasons.
- An append-only timeline: parent/child session and turn IDs, round IDs, model
  response IDs, native dispatch/follow-up joins, gate submit/reveal events, tool
  start/end/exit spans, ownership changes, patch hashes, and checkpoint receipts.
- Per-response and per-model ordinary input, cached input, cache-write input
  when exposed, output, and reasoning-output subset. Reasoning tokens are not
  charged a second time. Reconcile repeated cumulative usage snapshots once
  across turns, workers, reconnects, and resume events; count identical token
  amounts twice when they represent different real responses.
- USD for the entire baseline and treatment, plus parent/easy-worker/Astra
  subtotals, failures, retries, and any billed evaluator model work. Use one
  pinned official rate card and its per-request long-context rule. Label values
  API-price estimates unless billing is observed. Missing classes produce a
  conservative interval or UNKNOWN, never zero; do not claim a saving when
  uncertainty can reverse it.
- Packet/receipt byte and token sizes, included evidence identifiers, fork/resume
  configuration, trusted transport digest joins, declared and observed file
  access/write scopes, and scope-review status. If plaintext packet evidence is
  unavailable, record semantic scope as UNKNOWN. Do not publish private prompts.
- Quality by requirement and round, public repair count, worker retries,
  cancellation/timeouts, completion, final hidden result, semantic review,
  delegation episodes, and the nature of controller work between them.

Diagnose orchestration savings by comparing Astra's billed input/output and
stage coverage with the baseline's work in the same rounds, while adding all
cheap work and handoff overhead. Report whether repeated source/log/packet
content appears across sessions, using hashes of permitted artifact chunks;
this measures observed duplication, not hidden model context. Cached tokens
still cost money and do not erase duplicated context. Tool spans, time waiting
for workers, and the dependency critical path explain wall differences; they
are not substitutes for total elapsed time. Never sum overlapping durations
and label the result end-to-end latency.

The old pair-03 had one Astra child accounting for about 85.94% of treatment
cost ($2.510331 of $2.9209174), about 2.11M treatment input tokens versus 1.07M
baseline, and 774.057 versus 728.993 seconds. Merely spreading that child's
same work into three assignments could increase duplication again. This design
therefore needs evidence of recurring cheap orchestration and bounded new hard
decisions, alongside complete token/USD accounting. A lower call count or more
children is not the result being optimized.

## Limits, recovery, and comparison decisions

Default planning limits retain the prior ceiling: 3,000 wall seconds and $10
estimated total USD per arm, now explicitly including all descendants. Stop
dispatch at a conservative $9 upper-bound estimate and cancel active work at
the global limit; usage arriving after a response can overshoot, so this is a
safety threshold rather than a guaranteed billing ceiling. No live spending is
authorized by this design. A later campaign manifest must declare its total
cap before launching; three complete pairs imply up to $60 nominal allowance
before any separately authorized pilot or overshoot provision.

Enforce one public-gate repair per round, one worker retry per failed stage,
and the same global limits for both arms. Do not reset clocks or budgets at
report reveals. An ordinary failure consumes the repair allowance; an identified
evaluator/transport failure stops the run as infrastructure-invalid, preserves
its cost, and invalidates that matched repetition for performance comparison.
Replaying an invalid repetition requires a fresh pair, with the same frozen
fixture and explicit record of the invalid attempt. Do not repeat just the
unfavorable arm. A missing child usage record, unverified selector, oracle leak,
or drifted input is an integrity failure, not a cheap successful run.

After controls and independent review, a separately authorized matched pilot
can assess natural difficulty and whether the budget is feasible. The existing
protocol's order-of-100-call horizon is a calibration observation, never an
instruction or gate. If the task collapses to a short edit or requires no new
hard decisions after R1/R2, report it as a poor discriminator. If it consistently
times out under the current budget, report that limit; do not secretly raise
the cap, omit work, pad the task, or tune the frozen scenario to one arm. Any
revision becomes v2, retaining all v1 evidence.

For an exploratory result run at least three fresh interleaved matched pairs,
with a predeclared balanced arm order and identical start state, environment,
cache-preparation rule, and reveal process. Fresh sessions and workspaces cannot
consume prior answers. Use cloned/prewarmed Go caches from the same prepared
state or a documented equivalent reset rule, not one arm's build cache warmed
opportunistically for the other. Retain every failed/timed-out run in completion
and expenditure reports. Show per-pair values, ratios, and spread.

Quality parity and total USD are primary: any worse correctness/completion
precludes a positive routing claim. A preliminary cost result requires both
arms to pass all quality gates and complete accounting, with aggregate and
per-pair costs visible; a tiny or uncertain difference is inconclusive.
Latency is secondary and requires repeated matched evidence before being called
stable. This single task cannot establish a general suite-wide benefit. Compare
the candidate plugin with a rerun of the previous pinned plugin before claiming
release regression protection. Keep such an arm distinct from Astra-alone.

## Implementation handoff and acceptance

All new benchmark assets belong under `evals/long_horizon_v1/`, with new focused
harness tests under `evals/tests/` if implemented. Existing source/harness
helpers can be imported when compatible; do not rewrite prior fixtures or
evidence to fit the new protocol. The implementation sequence is:

1. Freeze initial task and R1/R2 texts, manifest/schema, source preparation,
   public corpus/replay helpers, evaluator-only hidden corpus, and controls.
2. Establish oracle positive/negative/partial/mutation controls and package
   feasibility with no model calls. This is necessary before live readiness.
3. Implement the new staged runner and reducer using tested usage/lineage
   helpers. Add deterministic mocked tests for reveal order, no future-file
   access, duplicate submissions, one repair, selector drift, complete all-model
   accounting, cancellation, transport interruption, and combined quality.
4. Add a dry-run replay that proves both arms get identical report bytes at
   corresponding gates, preserves one parent lineage, and binds every submitted
   patch. A mocked transcript validates the harness, not live model capability.
5. Independently review task semantics, controls, isolation claims, accounting,
   and runtime dispatch/turn evidence. Then request the separately authorized
   live pilot within the frozen cap; retain limitations if that evidence is
   unavailable.

Planned commands should be made real by the implementation, not documented as
already runnable: `prepare.py --destination <fresh-dir>`;
`controls.py --prepared <dir>`; `run.py --arm baseline|treatment --live ...`;
`grade.py --candidate <captured-patch> ...`; and `collect.py --pair <pair-dir>`.
Each command must refuse an existing output directory and emit hash-bound JSON
receipts. The B1 design alone adds none of these executables.

Self-check: the start tree is already reconstructable from pinned local inputs;
the requested feature addresses an observed reference-dropping boundary; two
new reports create dependent behavioral decisions after implementation; both
arms have identical admission/reveal semantics; public feedback and hidden
grading are separate; routing remains a choice; repeated context is measured;
and the old evidence is preserved. Outstanding implementation risks are a
fair oracle for concurrent ordering and retention, a positive control that
passes shared-backend regressions, verified persistent multi-turn transport,
trusted packet visibility, and whether the task fits the current live cap.
These are explicit readiness gates, not claims of completed validation.
