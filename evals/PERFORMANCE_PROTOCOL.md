# Paired performance evaluation protocol

## Decision question

Does Codex Model Router complete the **same user task** with quality no worse
than a single-agent `gpt-6-astra` baseline, while reducing both end-to-end wall
time and estimated USD token cost? After a plugin change, does the candidate
also avoid quality, latency, and cost regressions relative to the last accepted
plugin version? Scenario coverage and route compliance are separate diagnostics;
neither is evidence of these performance claims.

The September 2026 selective-only five-scenario live campaign is a router
behavior check. Its five dollar amounts are for five different tasks and must
not be compared with each other to infer a performance gain. It contains no
live Astra baseline and cannot answer the decision question above.

## Paired arms

For every benchmark task and repetition, run the applicable arms from byte-identical
copies of the same initial fixture, with the exact same user request, acceptance
criteria, Codex build, tool permissions, network policy, and time limit:

1. **Astra baseline:** `gpt-6-astra` performs the whole task without the router
   plugin or delegated workers. Its own planning, tools, verification, and
   retries remain in the measured session.
2. **Router treatment:** the configured primary agent uses the installed router
   plugin and may perform work directly or dispatch native workers according to
   its policy. Activation, routing, planning, worker execution, review,
   synthesis, verification, and retries remain in the measured session set.
3. **Previous-plugin regression control:** the last accepted plugin artifact
   runs with the same primary model, worker catalog, efforts, and environment as
   the candidate. This arm is required for change regression, not a substitute
   for the Astra baseline. Run the previous artifact anew; do not compare the
   candidate against a historical result collected under different runtime
   conditions.

The model selection and effort for both arms must be recorded before execution.
Do not change prompts, oracle, timeouts, or quality criteria to favor an arm.
Randomize arm order per repetition and balance order across
repetitions to reduce temporal and cache effects. Use fresh isolated workspaces
and sessions; never reuse a completed arm's generated files or conversation
history. Run at least three paired repetitions per task for an exploratory
result and more if variance prevents a stable conclusion.

## Workload and quality

The performance suite targets long-horizon repository work: tasks for which an
unassisted Astra pilot naturally requires on the order of 100 model calls, not
one or two file edits. A call count is a **calibration observation**, never a
requested behavior or a success criterion. Do not pad prompts, force extra
calls, or discard a later efficient run merely because it uses fewer calls.
The task must require substantial investigation, implementation across
interacting components, test discovery and execution, diagnosis of failures,
and final integration. Include different dependency structures: a noisy
read-heavy investigation, a dependent cross-component migration, genuinely
independent work with integration, and a design change with meaningful
correctness and safety tradeoffs. Both arms must be able to solve each task.

Construct these tasks from realistic pre-change repository snapshots and
issue-level requests, not exact-output recipes or tiny synthetic fixtures.
Remove solution-bearing history and hindsight documents from the runnable
workspace. Freeze an independent behavioral oracle with tests and a semantic
rubric before either arm runs; avoid requiring byte-for-byte equality with one
historical patch when multiple correct implementations exist. Freeze each
fixture, prompt, oracle, and task hash. Pilot difficulty and feasibility on a
separate calibration task or held-out snapshot so the scored suite is not tuned
to one arm's observed behavior.

The existing five tiny scenarios remain a functional/overhead suite only.
Their artifact checks, route checks, and dollar totals must be excluded from
the long-horizon performance aggregate. A one-token typo can remain a negative
control for dispatch overhead, but it cannot establish the product's main
benefit.

Apply the same artifact, test, and semantic rubric to both arms. Grade outputs
without revealing the arm when human judgment is required. Keep process
compliance (activation, routing, reviewer identity, dependency order) separate
from output quality; it is useful diagnosis but cannot compensate for failed
task quality. Failed, timed-out, cancelled, and retried runs stay in the
denominator. A treatment that has materially worse completion or quality does
not pass, regardless of its speed or cost.

## Reusable regression campaign

Keep a frozen, versioned task suite with task IDs, fixture hashes, prompt
hashes, acceptance-oracle hashes, and a separately versioned grading rubric.
Record the candidate and previous plugin commit, packaged artifact digest,
activation configuration, Codex build, model IDs and efforts, worker catalog,
rate-card version, machine, and run order. The harness must select plugin
artifacts by these pinned identities, not whichever plugin happens to be
installed in the operator's cache. Reject missing or mismatched provenance.

Run deterministic unit, contract, activation, and replay checks on every plugin
change. Run a small functional smoke suite on routing-related changes; run the
paid long-horizon matched matrix for release candidates and for changes expected
to affect routing economics. Never infer long-horizon performance from the
offline or smoke suite. Estimate and approve the live campaign's maximum spend
before starting it; preserve a hard per-run and campaign cap, and record
budget-related cancellations as such rather than silently dropping them.
Re-run the previous-plugin and candidate arms close together
on every selected task. Refresh the Astra arm in the same campaign when making
the Astra-relative claim or when the Codex runtime, model, prompt, fixture,
permissions, or pricing basis changes. Keep old campaign results for trend
analysis, not as a mismatched release gate.

Report two distinct paired comparisons: candidate versus Astra for product
value, and candidate versus previous plugin for regression. A release report
must show every task and repetition, including failures, and cannot pool arms
from different tasks or campaigns. Quality/completion regression is a hard
failure. Time and cost regression thresholds and the aggregation statistic
must be declared before the campaign; an inconclusive noisy result is not a
pass. Compare both raw token usage and USD recalculated under the *same*
rate card for candidate and previous plugin, so a price change cannot masquerade
as a routing improvement or regression. Also show current-card USD as the
expected operating cost.

## Measurement and release claim

Wall time starts immediately before submitting the request and ends only when
the final answer and required artifacts are complete. Include startup,
activation, controller idle/wait time, workers, review, verification, retries,
and failures. Cost is calculated from observed token classes for **all** parent
and child sessions using one dated official API rate card. Report the rate
card, model and effort, cache classes, and exact formula. Label these figures
API-price estimates unless account billing provides measured charges. If any
required usage is missing, mark the pair's cost unknown; do not silently omit
it or substitute a zero.

For each matched pair, report quality and completion for both arms, wall
seconds, USD estimate, and treatment/baseline ratios. Summarize paired ratios
across repetitions and tasks, with spread and failures visible. Do not compare
absolute costs from different tasks. A performance win requires quality parity
and both time and cost below baseline on the predeclared task mix; report
regressions and uncertainty rather than selecting only favorable cases. Keep
the raw evidence and grader results sufficient to reproduce every figure.

No performance claim may be made from synthetic runs, selective-only runs,
different-task totals, route compliance alone, or an unpaired historical Astra
session.
