# Plugin 1.3.3 repair validation, 2026-10-29

**Overall verdict: NOT PASSED.** The original investigation/reuse regression
meets the predeclared thresholds in this fresh cohort, with no unnecessary
worker and one observed decision in each after run. However, serial escalation
fails the total-token (+9.0%) and API-estimated-cost (+18.3%) thresholds. PR #10
still does not satisfy overall performance preservation.

## Subjects and protocol

- Before: `e75ef8785d960b12e6d1ce27ad4c6b184b8a0fc0`, plugin 1.3.0.
- After: `9f72e6aead1b28e11e7e8da385609cf1ba11f698`, plugin 1.3.3.
- Five frozen cases, three fresh matched pairs each, sequential AB / BA / AB.
  Both controllers use gpt-6.1-sol/low through the configured copilot-bridge.
- The user approved this cohort separately with a $7 observed API-estimate
  stop, $1/240-second per-arm stops, and 5,400-second matrix wall allowance.
- Admission discovered actual capabilities from PATH, without a Codex-version
  whitelist. Observed CLI `0.165.0-alpha.1.2`, binary/schema, native options,
  provider definition, and security settings matched the prior platform proofs.
- Both subjects were normally installed in fresh private homes and separately
  verified against Git bytes, with five uniquely loaded trusted hooks each.
  Platform native/cancellation receipts were reused with explicit provenance;
  new plugin behavior was scored afresh. Setup made zero model calls.

See the approved [plan](PLAN.md), [preflight](preflight.json), and
[capability provenance](capability-provenance.json). [Postflight](postflight.json)
confirms unchanged installed source bytes, CLI, runner, adapter, and fixture
definitions. The same dated Standard API rate card is in
[campaign.json](scored/campaign.json). No solver arm was retried or dropped.

Four fixtures use exact-tree-v2. Architecture uses the same exact typed compact
JSON object and unchanged-file oracle for both arms, allowing unspecified
terminal line breaks. Earlier failed cohorts are preserved separately:
[1.3.1](../context-regression-20261028/REPORT.md) and
[1.3.2](../context-refinement-20261028/REPORT.md). Their strict scores, lost timer,
and costs are not overwritten or pooled with this cohort.

## Paired results

Values are median after/before ratios for the same task and start state,
expressed as percentage changes. Total tokens are input plus output;
reasoning is a subset of output. Thresholds require all three pairs complete,
no completion/artifact loss, wall ratio <=1.15, and token/cost ratios <=1.05.

| Scenario | Wall time | Total tokens | API-estimated USD | Exploratory result |
| --- | ---: | ---: | ---: | --- |
| Multi-turn direct control | -23.4% | -26.0% | -26.3% | Thresholds met |
| Investigation/reuse | -1.6% | -23.1% | -23.1% | Thresholds met |
| Serial escalation | +13.4% | +9.0% | +18.3% | Token and cost regression |
| Parallel disjoint work | -0.3% | -19.1% | -14.0% | Thresholds met |
| Independent artifact review | -1.8% | -8.1% | -8.5% | Thresholds met |

[analysis.json](analysis.json) retains every arm, paired ratio, range, cost
component, and recomputed median; it agrees with the runner's
[summary](scored/summary.json). Direct wall ratios span 0.710-1.225;
investigation spans 0.833-1.170; serial spans 0.894-1.419. Stable/general speed
improvement cannot be inferred from these three small-task pairs. Absolute
experiment costs across different scenarios are not pooled into a savings
claim. This suite is not the Astra-only versus selective hard-kernel benchmark
and does not establish a Terra quality/cost frontier.

Per-arm wall covers transport startup through the final solver turn, including
task self-checks. The external artifact oracle and recorder cleanup follow that
boundary for both arms. Matrix wall was approximately 3,709 seconds.

## Original regression and observed behavior

All 30 runs completed and passed artifact checks, with complete reconciled
parent/child usage. Every observed after-arm prompt hook emitted zero context
bytes. Worker overrides stayed at 565 bytes. Absolute installation paths affect
entrypoint byte counts. Reduced injection is observed separately from tokens,
cost, quality, and elapsed time.

The [supplementary audit](process-audit.json) binds its observations to original
raw-event hashes and decodes resolver JSON embedded in combined outputs,
which the scored recorder's whole-output JSON parser can miss. Original scored
records remain unchanged.

All three after investigation runs used one DIRECT decision, no worker, and
5/5/6 model responses. This removes the previously observed mixed-mode forced
delegation and repeated same-owner decisions. In the prior 1.3.2 cohort this
case failed wall/token thresholds; the fresh comparison now meets them.
These are separate cohorts, not matched 1.3.2-versus-1.3.3 measurements.

All six serial runs retained DIRECT -> DELEGATE with `scope-expanded` and one
native worker. All six parallel runs retained three workers and PARALLEL.
Review runs used separate author and reviewer sessions. Artifact equality,
worker counts, route declarations, and pass text still do not prove full
ownership, receipt, packet-scope, review, or technical isolation compliance.

## Remaining serial cost and token regression

| Pair | Before responses, controller + worker | After responses, controller + worker | Wall ratio | Token ratio | Cost ratio |
| --- | --- | --- | ---: | ---: | ---: |
| 1 | 13 + 5 | 17 + 11 | 1.419 | 1.373 | 1.020 |
| 2 | 15 + 9 | 15 + 9 | 0.894 | 0.847 | 1.275 |
| 3 | 16 + 8 | 20 + 8 | 1.134 | 1.090 | 1.183 |

Additional model rounds and implementation/validation recovery remain visible.
For example, after pair 1's worker executed nine commands versus four before.
After workers used PowerShell JSON guards that rejected valid integers because
they tested `[int]` storage width; a [read-only host check](powershell-json-observation.json)
confirms this PowerShell parses JSON 1 and 2 as System.Int64. The same class of
guard failure also appeared in before pair 2. Byte-precondition checks, receipt
work, and script corrections also occurred. Nonzero exits include harmless
no-match searches and absent Git metadata, so they are not solver failures.
All solver sessions recovered and passed the artifact oracle.

Response count or raw tokens alone cannot explain estimated cost. In pair 2,
after had fewer total tokens but cache-write cost increased from $0.093596 to
$0.156114. In pair 3, uncached-read cost increased from $0.004002 to $0.041771.
The per-arm component sums are retained in analysis.json. Worker model/effort,
generated tool code, and observed cache classes can vary even for the same
fixture. These traces identify remaining execution/accounting overhead; they
do not establish that one code change caused every difference or that the
underlying model/provider has a stable speed/cost regression.

## Accounting and release status

All **525** scored responses were replayed independently: response classes
sum to sessions, native session aggregates reconcile, and sessions sum to runs.
Every response price, including cache writes and long-context rules when
applicable, was recomputed from the retained common rate card. Reasoning was
not counted twice. All 15 pairs have wall timers and no unknown usage classes.

This cohort's estimate is **$4.759797715**, below the approved $7 observed stop.
There were no additional paid activation or retry experiments. Reused platform
proofs were not charged again. Actual copilot-bridge charges and upstream model
identity remain unverified; this is not a provider bill.

The original investigation defect is repaired and its fresh case meets the
declared thresholds. Overall performance acceptance remains open because the
serial token/cost regression fails. Correct artifacts and passing CI do not
override this result. No extra paid experiments are authorized by unused
estimate allowance from this completed cohort.

Replay public arithmetic, medians, provenance, and behavior observations:

```powershell
python -m unittest evals.tests.test_context_repair_evidence -v
```

Raw native events, provider/auth configuration, and private runtime homes remain
local. Reduced public receipts and source hashes support arithmetic replay;
they do not publish private prompts or prove technical packet isolation.
