# Router scenario benchmark contract v1

The executable contract is `scripts/contract.py`. Objects use exact keys;
missing and unknown fields fail. Duplicate JSON keys, NaN/Infinity, boolean
numbers, unsafe paths, links/reparse points, hash mismatches, duplicate run
slots, and incomplete treatment grids fail before analysis.

## Frozen benchmark

`benchmark.json` pins the exact `cases.json` bytes, treatment order, repetition
count, harness version, policy, environment, and synthetic/observed provenance.
Each case pins an exact-tree fixture and contains all three treatments:

- `direct`: forced controller execution baseline;
- `mandatory_delegate`: delegated serial baseline; and
- `selective`: product policy, including direct calibration, serial delegation,
  direct-to-delegate escalation, or safe parallel topology as appropriate.

The five categories are direct calibration, noisy investigation with receipt
reuse, dependent serial escalation, independent disjoint parallel work, and
architecture work with independent review. HumanEval/MBPP function generation
is not part of this contract.

## Terminal records

Every scheduled run records:

- identity, terminal outcome, treatment, and exact manifest provenance;
- initial/final ownership, topology, verification, escalation, and route events;
- controller/worker business actions and the first-delegation timestamp;
- timed controller/worker/reviewer spans, dependencies, exclusive artifact
  ownership, tool calls, and raw log volume;
- controller/worker input context plus hash-bound receipt content, producer,
  consumers, preserved facts, artifact references, and token size;
- exact-tree quality checks and score;
- distinct independent-review evidence when required;
- retries, conflicts, evidence hashes, result-tree hash; and
- cost kind (`measured`, `estimated`, or `unavailable`), completeness, and USD.

DIRECT cannot claim worker work or independent review. A controller may perform
bounded work before a recorded direct-to-delegate escalation, but zero controller
business spans may cross or follow the first-delegation timestamp. This is
derived from span timing rather than a submitted counter. Completed delegation requires a
worker span. Parallel topology requires overlapping worker spans with disjoint
artifact ownership and distinct worker session IDs. Serial topology forbids overlap. Completed independent
review requires a passing reviewer session distinct from controller and author.
Failed terminal runs may end before review and remain in the denominator.
Each case freezes dependency and receipt-consumer edges per treatment; records
must match them exactly and satisfy prerequisite timing. Artifact/semantic
quality is treatment-fair and recomputed from `required_quality_checks` and the
exact artifact grade. Route and delegate-only requirements are recomputed
separately from `required_process_checks`. Record-authored quality fields are
diagnostic claims and cannot raise effective quality.
Completed runs require the full frozen span inventory and dependency/receipt
graphs. Blocked, cancelled, timed-out, and errored runs may contain a
downward-closed executed prefix: supplied spans and edges must be frozen-valid,
every executed downstream stage must include its prerequisite and receipt edge,
and impossible later stages are rejected. These failures retain zero artifact
quality and remain scheduled.
Route events are strictly chronological. Each business span must start after an
ownership decision, match the active ownership state for its role, and finish
before the next transition. Semantic route adherence compares ownership,
topology, verification, and trigger—not absolute timestamps; elapsed timing is
validated independently.
Frozen cases support only a single stable route event or one DIRECT/NONE to
DELEGATE transition. DELEGATE-to-DELEGATE topology changes are rejected, so a
late PARALLEL event cannot retroactively authorize work that overlapped while
ISOLATED_SERIAL was active. Every worker/reviewer span must match the active
delegate topology.

## Reporting boundary

Measured and estimated costs are never combined. Receipt reuse is derived from
validated producer/consumer links and reports linked-consumer context without
inventing avoided-token counters. Synthetic campaigns cannot
support release, savings, latency, or quality claims. Failures, retries,
conflicts, route violations, and artifact failures remain visible.

## Observed live evidence v3

Live analysis parses only typed controller route events, native spawn calls,
child assignment boundaries, child result events, runtime turn context, and
final worker output. Inherited instructions and arbitrary nested text are not
identity or process evidence. Packet identity, native transport identity,
actual model/effort arguments, runtime metadata, and final echo are separate
dimensions. Missing or encrypted evidence is `unknown`; placeholders and
contradictory observed values fail. Unknown never satisfies acceptance.

All transcript, session-index, external session-source, activation, and result
tree references are hash-bound. Validation recomputes acceptance from evidence
statuses and rejects authored acceptance flags that disagree. Assignment-span
overlap and session-lifetime overlap are distinct metrics. Historical reports
are immutable inputs to versioned reprocessing outputs.

Capability records are references, not evidence by themselves. Dispatch
preflight opens each nonempty source, verifies its nonzero hash, parses its
formal kind, derives spawn selectors and model/effort compatibility, and checks
verified inheritance against the exact inherited pair. Live validation rebuilds
every leaf from raw events, indexed session sources, actual artifacts, and a
hash-bound frozen oracle. Unknown, unavailable, partial, contradictory, or
quoted/example evidence never becomes a pass.

The activation gate uses only the v3 `raw/` layout. It parses environment,
routing config, CLI, manifest, hook provenance, spawn schema, and model catalog;
binds the copied config to its source and transcript thread; and requires a
controller message whose first characters are `ROUTE:` before business action.
Collection is fresh-only and staged; existing or partially published output is
never reused.

The validator's own `benchmark.json` pins `cases.json`, which pins each fixture
descriptor and its initial/reference snapshots. That chain alone defines the
case oracle. Report-authored oracle paths must resolve to that exact reference;
artifact and index paths must be their canonical case paths. Matching arbitrary
file hashes cannot substitute for case identity. Symlink/reparse-point trees,
missing trees, cross-case references, and modified frozen snapshots are rejected.

The transcript's unique `thread.started` ID always selects the parent, including
when its own parent ID is absent. The index must bind every source path/hash to
matching thread, parent, and native-path metadata. Each child must descend from
that parent and correspond one-to-one to a unique spawn call ID and its native
result path. Parent-session and CLI route sequences must agree. A worker spawn
requires the latest preceding route to be DELEGATE with the case topology;
controller business tools require an active DIRECT route before the first
delegation. Delegation is monotonic: subsequent DIRECT decisions, controller
business recovery, and delegated topology changes fail even if a later DELEGATE
line precedes the next worker. Later decisions have no retroactive authority.

Correct artifacts alone never establish completion. Every scenario requires
exactly one successful terminal CLI event, with no failure/cancellation/error or
later CLI event, and one parent task start/completion pair with the same turn ID.
The CLI final message must match the parent completion, and a parent failure,
missing/duplicate completion, or post-completion action fails the terminal check.
Such observations keep their independent artifact grade but have outcome
`failed`, remain scheduled, and cannot count as completed or pass the campaign.

Reviewer output requires exactly one anchored `Verdict: PASS` line and no other
case-insensitive PASS/FAIL token. `Verdict: FAIL`, missing/duplicate verdicts,
and contradictory text such as `FAIL: This must not PASS` fail review.

The activation spec is the unchanged repository file. Both actual prompt
sources must contain its exact neutral prompt, with no extra Skill/user request.
The only auxiliary user message allowed is one structurally parsed environment
record before routing: its unique fields are `cwd`, `shell`, optional ISO date,
timezone identifier, and the captured filesystem metadata. The workspace path
must match session metadata; the supported filesystem shape binds its single
root to that workspace and its disabled/unrestricted permission attributes.
Unknown fields, attributes, duplicate fields, mixed text, comments, declarations,
extra content blocks, and supplementary instructions fail closed. This grammar
does not exempt arbitrary text because it lacks known Skill or routing words.
Session/index/transcript IDs, config workspace, full controller contract and
serialized config, route order, successful read output, unchanged note/tree,
terminal completion, and all final outputs must agree. The configured-hook
Python capture must precede the session in the same workspace and use the
literal hook interpreter command. A different working collector interpreter
does not establish that command's availability. Failed preflights remain saved;
PATH remediation and a fresh capture are required before another attempt.
