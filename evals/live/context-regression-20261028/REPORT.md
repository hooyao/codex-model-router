# Context-loading live regression, 2026-10-28

**Verdict: NOT PASSED for the a93ba3a optimization.** Fewer injected bytes did
not preserve every performance metric. The multi-turn direct control regressed
in wall time, the serial case regressed in total tokens, and the review case
became more expensive under the common API rate card.

## Compared subjects and runtime

- Before: `e75ef8785d960b12e6d1ce27ad4c6b184b8a0fc0`, plugin 1.3.0.
- After: `a93ba3af99b9ad58c97f82c4938129b634d5c446`, plugin 1.3.1.
- Controller: gpt-6.1-sol/low for both arms; native worker choices and all
  parent/child responses are retained per run.
- Configured provider: copilot-bridge. The observed CLI was
  `codex-cli 0.165.0-alpha.1.2`; this is metadata, not an admission requirement.
  New runs discover CLI capabilities from PATH or `--cli`, with no version
  whitelist. Historical replay identities remain unchanged.
- Five frozen functional fixtures, three fresh matched repetitions per case,
  sequential arms in AB, BA, AB order. The direct case includes two same-thread
  follow-ups. See [PLAN.md](PLAN.md) and [RUNBOOK.md](RUNBOOK.md).

## Paired results

Each value is the median of after/before ratios for the same task and start
state, expressed as a percentage change. Total tokens mean input plus output;
reasoning output is not added twice. This is a small functional/overhead suite,
not the long-horizon or Astra-relative release benchmark.

| Case | Wall time | Total tokens | API-estimated USD | Result |
| --- | ---: | ---: | ---: | --- |
| Multi-turn direct control | +46.1% | +0.8% | -7.7% | Wall regression |
| Investigation/reuse | +10.9% | -4.3% | -14.3% | Artifact/economics thresholds met; process caveat below |
| Serial escalation | +7.2% | +16.0% | -0.4% | Token regression |
| Parallel disjoint work | -4.5% | -17.4% | -19.3% | Artifact/economics thresholds met |
| Independent review | Incomplete, 2/3 timed pairs | -16.3% | +104.1% | Cost regression and strict artifact failures |

The two available review timing ratios have a median increase of 18.6%; that
is diagnostic only, not a completed three-pair timing result. All repetitions
and raw paired ratios are in [analysis.json](analysis.json); individual reduced
receipts are under [scored/](scored/).

The predeclared tolerances were no quality/completion loss, median paired
token/cost ratios at most 1.05, and wall ratio at most 1.15. No overall pass or
stable speed/cost improvement follows from this campaign. Three samples also
do not establish general latency stability.

## Quality and behavior

All 30 solver sessions completed with complete reconciled parent/child usage.
The exact-tree oracle passed 24 runs. All six review failures are exactly the
absence of the final LF in `decision.json`; the JSON object and other files
match. This semantic observation is secondary and does not change the frozen
strict scores.

Some required route declarations were absent. Both arms performed the
investigation fixture directly rather than satisfying its frozen delegated
trace. Native delegation itself was demonstrated separately and in the scored
serial, parallel, and review cases. Artifact equality does not establish full
ownership, packet-scope, receipt-reuse, or independent-review compliance.

The review cost increase coincides with model selection: the after arm used
an Astra worker in all three repetitions; the before arm used one in only one
repetition. Lower total tokens therefore did not imply lower cost. The direct
control also used additional metadata/decision rounds after the byte-only
optimization. These observations motivate the separate cold-path refinement;
they are not performance results for that new code.

## Accounting and infrastructure

The 30 scored runs contain 547 priced responses and total $5.96326095 under
the common dated Standard API rate card. Including failed activation attempts
and native/cancellation capability probes, observed campaign usage estimates
total $6.75964038. These totals describe experiment spend, not a comparison of
different tasks. Per-response classes, requested model/effort, response-ID
hashes, source-rollout hashes, and the rate card are retained. The published
per-response costs and aggregate token classes were recomputed and reconciled.
The 52 unscored probe responses and their original/replayed cost distinction
are itemized in [probe-history.json](probe-history.json).

Bridge billing and upstream model identity could not be independently
established. The rate-limit interface did not expose a credit balance or bill.
API-price estimates are not actual Copilot/bridge charges.

The original recorder stopped when it could not read a sandbox-owned review
output. No solver retry occurred. The grader was repaired to read through the
same configured workspace sandbox without changing file ACLs or contents.
The successful native before/review run's usage and content were recovered,
but its lost wall timer remains unknown. Original summaries and failed probe
receipts remain retained; the resumed report is versioned. Timing journals
protect subsequent runs. Native terminal/delivery lag was checked separately
and was approximately 0.05–1.2 seconds in the inspected timed runs; it was not
substituted for the primary end-to-end wall boundary.

Original raw events and provider/auth configuration stay private locally.
Public receipts retain reduced evidence and original hashes; they support
independent arithmetic replay, not public reconstruction of private prompts
or a claim of technical context isolation.
