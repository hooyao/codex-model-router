# Refined context-loading live regression, 2026-10-28

**Verdict: NOT PASSED for plugin 1.3.2.** All 30 runs completed and passed
this cohort's artifact oracle. Four scenarios met the predeclared exploratory
performance thresholds, but investigation/reuse regressed in median paired
wall time (+45.9%) and total tokens (+7.1%). The optimization therefore does
not yet satisfy the requested overall time/token preservation requirement.

## Compared subjects and controls

- Before: `e75ef8785d960b12e6d1ce27ad4c6b184b8a0fc0`, plugin 1.3.0.
- After: `6f925d760a6c820a282041b43ec847e6d8281818`, plugin 1.3.2.
- Five frozen scenarios, three fresh matched pairs each, sequential AB / BA /
  AB order. The direct scenario includes three user turns in one thread.
- Both arms used gpt-6.1-sol/low controllers, the configured copilot-bridge,
  equivalent security/tool settings, and fresh normal plugin installations.
- CLI `0.165.0-alpha.1.2` and its schema/hash were observed, not whitelisted.
  Admission discovers the actual capabilities from PATH or `--cli`; matching
  runtime identity is a within-cohort control, not a required Codex version.
- The approved additional observed API-estimate stop was $7, with $1 and
  240-second per-arm stops. No solver rerun or paid setup probe occurred.

The [predeclared plan](PLAN.md), [zero-model preflight](preflight.json), and
[campaign controls/rate card](scored/campaign.json) identify the exact subjects,
fixture hashes, runner, capabilities, and acceptance rules. Native dispatch and
cancellation evidence was reused only for the matching platform ABI, with
original hashes recorded in preflight and newline-independent JSON bindings
in [capability-provenance.json](capability-provenance.json). It is not new plugin behavior evidence
and its previously reported expense is not charged to this cohort again.

Four fixtures retain exact-tree acceptance. The architecture fixture uses
the exact typed compact JSON object plus unchanged unrelated files, allowing
unspecified terminal line breaks. This rule was declared before the first
call and applied equally to both arms. The first cohort's six strict byte
failures, lost timer, and failed performance verdict remain unchanged in its
[separate report](../context-regression-20261028/REPORT.md). The cohorts are
not pooled.

## Paired performance

Values are median after/before ratios for matched task/start-state pairs,
expressed as percentage changes. Total tokens are input plus output;
reasoning is a subset of output and is never added twice. Thresholds require
no artifact/completion loss, wall ratio at most 1.15, and total-token and
API-estimated-cost ratios at most 1.05, with all three pairs complete.

| Scenario | Wall time | Total tokens | API-estimated USD | Exploratory result |
| --- | ---: | ---: | ---: | --- |
| Multi-turn direct control | -10.7% | -10.4% | -19.5% | Thresholds met |
| Investigation/reuse | +45.9% | +7.1% | -13.8% | Wall and token regression |
| Serial escalation | +10.5% | -9.2% | -7.2% | Thresholds met |
| Parallel disjoint work | -8.2% | -15.3% | -8.9% | Thresholds met |
| Independent artifact review | +9.1% | -5.5% | -33.4% | Thresholds met |

[analysis.json](analysis.json) contains every paired ratio, range, and arm's
wall time, tokens, requests, worker count, and cost. Independent arithmetic
replay agrees with the runner's [summary](scored/summary.json). The direct
wall ratios span 0.800 to 1.529; investigation spans 0.942 to 2.470. Three small
pairs do not establish stable latency gains or general workload performance.
No pooled absolute cost comparison across different scenarios is used as a
performance claim. This suite also does not replace the Astra-only versus
selective hard-kernel benchmark or establish a Terra quality/cost frontier.

## Context and execution observations

All observed after-arm `UserPromptSubmit` events emitted zero context bytes,
including the same-thread direct follow-ups. Session entrypoints were
1,431-1,434 bytes versus 8,460-8,463 before; worker overrides were 565 bytes
versus 5,247-5,249. Absolute installation paths affect those byte counts.
Reduced injection is directly observed, but later tool output and additional
model rounds still contribute to token usage.

The supplementary [process audit](process-audit.json) binds its observations
to each original raw-event hash. The scored recorder parsed a resolver result
only when the entire command output was JSON; combined descriptor/resolver or
resolver/reference commands therefore undercounted decisions. The offline
audit also decodes line-start resolver JSON in those mixed outputs. Original
scored receipts are preserved; an absent entry in their narrower observation
list is not treated as proof that no resolver ran.

- All six direct runs had two observed DIRECT decisions, no workers, and
  three completed user turns. The final status-only turn emitted no route
  declaration or command; the fresh middle verification received a decision.
- Investigation before used one DIRECT decision in each repetition. After
  used two DIRECT decisions in repetitions 2 and 3, and one extraction worker
  plus two subsequent DIRECT decisions in repetition 1. That first pair used
  21 versus 6 requests and 3.415 times the total tokens. Extra decisions and
  ownership changes coincide with the regression; this is diagnostic evidence,
  not a controlled attribution of every elapsed second. Artifact equality
  does not establish full hash-bound receipt/handoff compliance.
- All six serial runs recorded DIRECT followed by DELEGATE with
  `scope-expanded` and used one native worker. All six parallel runs recorded
  PARALLEL delegation and used three Luna/low workers.
- All six review runs used two distinct native worker sessions for author
  and reviewer. After used Sol/medium reviewers twice and Luna/low once;
  before used Sol/medium twice and Astra/low once. Those choices contribute
  to the cost difference; it is not solely an injection-size effect.

The supplementary audit records command failures and worker messages
containing pass text as observations, not an automatic review-compliance
oracle. Worker counts, artifact correctness, and route declarations alone
cannot prove ownership, minimal packet scope, independent reasoning, or
technical context isolation. Full process compliance remains unproven.

## Accounting and reproducibility

All 521 scored parent/child responses have complete reconciled usage. The
independent replay sums response classes into each session, compares native
session totals, and sums sessions into runs. It recomputes every response's
cost using the retained dated Standard rate card, including cache writes.
Reasoning tokens are already included in output. All 15 pairs have wall timers;
there are no dropped runs, unknown usage classes, or automatic solver retries.

This cohort's API-price estimate is **$4.789826465**. Preparation made zero
model calls and reused capability receipts incurred no additional calls.
The earlier cohort's separately retained $6.75964038 brings the two
experiments' observed estimates to $11.549466845; these are experiment spend,
not evidence of savings on an individual workload. Actual bridge charges and
upstream model identity remain unverified.

Replay the published arithmetic, medians, thresholds, and retained failure
checks without model calls:

```powershell
python -m unittest evals.tests.test_context_live_evidence -v
```

Fresh live execution uses the portable setup in the prior
[runbook](../context-regression-20261028/RUNBOOK.md), with this plan's exact
subject refs, `--semantic-architecture`, and `--campaign-stop-usd 7`.
Provider/auth configuration, native prompt-bearing events, and private runtime
homes remain local. Public reduced receipts and hashes support arithmetic
replay, not reconstruction of private prompts. The scored subjects, runner,
tasks, and acceptance rules were unchanged during this campaign.
