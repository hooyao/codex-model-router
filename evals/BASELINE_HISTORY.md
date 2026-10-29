# Plugin regression baseline history

## Current accepted reference

As of 2026-10-29, the latest accepted plugin reference for subsequent plugin
regression comparisons is **1.3.3**, source
`9f72e6aead1b28e11e7e8da385609cf1ba11f698`, accepted with the documented
[serial-performance exception](live/context-repair-20261029/ACCEPTANCE.md).
The acceptance was recorded in commit
`3a36396046dbb055cc01fe053c145d73feb4f1c2` for PR #10.

This reference selection does not relabel earlier runs. All three context
campaigns below actually compared against plugin **1.3.0**,
`e75ef8785d960b12e6d1ce27ad4c6b184b8a0fc0`. Their measured before arms, original
scores, acceptance rules, and source identities remain unchanged. The accepted
reference and the historical comparator are distinct records.

## Recorded lineage

| Date | Candidate / source | Measured comparator | Benchmark result | Reference decision |
| --- | --- | --- | --- | --- |
| 2026-10-28 | 1.3.1 / `a93ba3af99b9ad58c97f82c4938129b634d5c446` | 1.3.0 / `e75ef8785d960b12e6d1ce27ad4c6b184b8a0fc0` | Direct wall, serial tokens, and review cost regressions; strict artifacts 24/30 | Not promoted; [original report](live/context-regression-20261028/REPORT.md) |
| 2026-10-28 | 1.3.2 / `6f925d760a6c820a282041b43ec847e6d8281818` | Same 1.3.0 comparator | Investigation wall +45.9%, tokens +7.1%; artifacts 30/30 under that cohort's predeclared oracle | Not promoted; [report](live/context-refinement-20261028/REPORT.md) |
| 2026-10-29 | 1.3.3 / `9f72e6aead1b28e11e7e8da385609cf1ba11f698` | Same 1.3.0 comparator | Four cases meet thresholds; serial wall +13.4%, tokens +9.0%, API-estimated cost +18.3%; artifacts 30/30 | Accepted by the user with the serial exception; [report](live/context-repair-20261029/REPORT.md), [acceptance](live/context-repair-20261029/ACCEPTANCE.md) |

The effective accepted-reference change recorded on 2026-10-29 is
**1.3.0 (`e75ef87`) -> 1.3.3 (`9f72e6a`)**, with 1.3.1 and 1.3.2 retained as
unaccepted intermediate candidates. The 1.3.3 experiment's original verdict
remains `NOT_PASSED_SERIAL_TOKENS_AND_COST`; user acceptance is a subsequent
scoped decision, not a rescore or a change to the original 5% token/cost limits.

## Using and updating the reference

The portable context runner requires explicit `--before-ref` and `--after-ref`;
it has no implicit plugin-baseline default. For the next plugin regression
comparison, select the accepted 1.3.3 source as `--before-ref`, pin the new
candidate separately, and collect fresh matched runs under the selected plan.
Do not reuse old arm timings or costs as fresh evidence. Record any deliberate
alternative comparator in that plan. Historical 1.3.0 remains available for
separate original-baseline comparisons.

Keep task/start-state controls, pricing provenance, actual runtime capabilities,
quality oracles, and claim limits explicit. Discover the CLI from PATH or an
operator path; observed versions/hashes are cohort controls, not admission
against a version whitelist. This small functional/overhead history does not
promote a Terra frontier claim or replace the separate long-horizon
Astra-versus-selective protocol and its fixed historical baselines.

For subsequent accepted changes, append a dated lineage entry with source
commits, actual comparator, report, benchmark verdict, and acceptance/exception
record before updating the current accepted reference. Preserve earlier rows,
failed runs, scores, and evidence. This documentation update made no model calls
and did not start or authorize a new paid campaign.
