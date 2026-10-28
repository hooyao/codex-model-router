# Context-loading regression campaign

## Question and scope

Compare router commit `e75ef8785d960b12e6d1ce27ad4c6b184b8a0fc0` with
`a93ba3af99b9ad58c97f82c4938129b634d5c446` on the same live functional tasks.
Check artifact quality and routing behavior first, then complete session-set
token usage, API-price estimates, and end-to-end wall time. This campaign can
find regressions in these workloads; the small fixtures do not establish
long-horizon product performance or an Astra-relative advantage.

## Runtime portability

Resolve the CLI from PATH or an explicit caller path. Probe protocol methods,
model/effort availability, plugin installation, hook loading/trust, native
dispatch, cancellation, and usage reporting. Version strings and executable
hashes are observations, not a version whitelist. A missing required
capability stops live execution with its actual reason. Each matched pair
uses the same observed runtime, tool access, provider, and limits. Historical
campaign manifests and results are not relabeled or silently rebound.

The provider is the user's configured copilot-bridge. Separate private test
homes inherit only that provider definition. They install each pinned plugin
through the runtime's normal plugin interface, trust only its observed hook
definitions/content, and leave the user's configuration untouched. Private
configuration and raw rollout evidence are not published.

## Predeclared workload and order

Use the five existing hash-bound fixtures: direct-small-control,
investigation-reuse, serial-escalation, parallel-disjoint, architecture-review.
Use their existing prompts and initial/reference trees without exposing the
oracle to the solver. The direct-small-control session additionally receives
two fixed follow-ups in the same thread: verify the corrected file is still
unchanged, then report that verification without further edits. These test
repeated UserPromptSubmit behavior. Both arms receive identical follow-ups.

Use gpt-6.1-sol/low for both primary agents, after verifying runtime support.
Workers follow the unchanged routing matrix and observed runtime catalog.
Run three fresh matched repetitions per case, with before/after order AB, BA,
AB. Retain all attempts. First run a separate bounded activation and native
dispatch capability probe; failed probes are retained and cannot count as
scored passes. Do not start the full matrix until the probe has complete
parent/child usage and working cancellation.

## Acceptance and measurement

Artifact acceptance uses the same frozen oracle in both arms. Report
activation, route/ownership order, native child selector evidence, review,
dependencies, and topology separately from artifact quality. Missing process
proof is unknown or failed, even if the files are correct. Do not call an
unknown plaintext packet scope verified isolation.

Per matched task, report paired ratios for total input-plus-output tokens,
complete estimated USD, and wall time, with every repetition visible. The
exploratory regression thresholds are no quality/completion loss, median
paired token and API-estimated-cost ratios at most 1.05, and median paired
wall ratio at most 1.15. Three repeated samples cannot prove a stable general
advantage; high variance or missing evidence makes the result inconclusive.

Measure from immediately before request submission through final artifact
and answer completion, including activation, worker execution, waits, checks,
and retries. Include all parent and child responses. Keep token classes
separate; reasoning output is a subset of output and is not added twice.
Use one current dated official Standard rate card for exact model IDs in both
arms, including cache writes and long-context adjustments. Provider billing
and upstream identity remain unverified; estimates are not actual charges.

## Limits

This explicitly requested diagnostic uses an observed API-price safety stop of
$10 for the campaign, $1 per scored arm, 240 seconds per arm, and 5,400 seconds
overall, with no automatic whole-run retries. Capability probes are limited to
120 seconds each and are included in observed campaign spend. Stop dispatching
on incomplete usage or a failed prerequisite. Interruption and final usage
must be retained. Responses may be billed before usage becomes observable, so
the observed stop is not a guaranteed provider billing cap.

The campaign wall allowance was calibrated before scored runs from the
unscored native probes (roughly 75 seconds per simple delegated read). It is
shared by both arms and does not change any quality or regression threshold.

Publish only reduced evidence, public task artifacts, and reproducible
commands; keep original private sessions locally with hash references.
