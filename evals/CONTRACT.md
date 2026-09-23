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

## Observed live evidence v2

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
