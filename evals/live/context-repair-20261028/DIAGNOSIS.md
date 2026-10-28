# Context regression diagnosis and 1.3.3 repair

Status: deterministic repair verified; fresh live performance is not yet
measured. No model calls were made for this diagnosis or repair.

The original [1.3.2 cohort](../context-refinement-20261028/REPORT.md) remains
NOT PASSED. Its original scores, requests, costs, and receipts are unchanged.

## Evidence and causes

| Investigation pair | Before requests | After requests | Before wall, s | After wall, s |
| --- | ---: | ---: | ---: | ---: |
| 1 | 6 | 21 | 66.132 | 163.366 |
| 2 | 5 | 7 | 56.534 | 82.491 |
| 3 | 7 | 6 | 72.228 | 68.061 |

The first after request explicitly satisfied every direct predicate and
matched two valid examples: `luna-extraction` (evaluate) and
`luna-local-bounded-change` (direct). The old config-mode merger treated two
different mode strings as a conflict and returned delegate. The resolver then
reported configured-delegate, causing coordination/reference/capability work
and a native extraction worker. The workspace global mode was evaluate;
this was not an explicit user configuration demanding global delegation.

That outlier alone does not explain the paired median regression. The second
after run stayed DIRECT but still had two extra requests. It discovered the
descriptor directory, read descriptor context, requested resolver help, and
resolved a second decision after the first business read. All three after
runs split reading the authoritative value from the same owner's bounded
receipt-consuming edit; the first split implementation inspection and editing
once more. The hook/descriptor/skill's per-read/edit/test decision wording
contradicted the same-owner stage-batching rule hidden in delegated references.

The cold hook named the descriptor relative to the resolver without giving
its absolute path. Directory/help/source discovery was observed in direct and
investigation traces. The relationship between these extra rounds and the
elapsed regression is diagnostic, not a controlled attribution of each second.

## Changes and checks

Plugin 1.3.3 treats evaluate as neutral when merging matched modes; explicit
delegate still dominates. Every direct predicate, unknown fact, independent
review requirement, and escalation rule continues through the original gate.
Existing config files are preserved, including schemas 3 and 4 and explicit
workspace delegation preferences.

One approved decision now explicitly covers the same owner's declared source
reads, hash-bound receipt preparation/consumption, bounded edit, and self-check.
New business requests/owners, changed config/inputs/scope/permissions/risk,
failed checks, and real prerequisite/recovery gates still need reassessment.
Separate review owners and whole-request DELEGATE obligations remain binding.
The cold entrypoint advertises the exact descriptor path and invocation.

[decision-replay.json](decision-replay.json) retains the three original after
requests, their original raw-event/command hashes, and old/candidate resolver
outputs. Requests were decoded from the original native commands; parent/child
prompts and commands are not published. Running the old and candidate CLI
against the same validated config gives DELEGATE -> DIRECT for the mixed-mode
request and DIRECT -> DIRECT for the other two. This is an offline resolver
replay, not three new solver runs or a rewrite of their scores.

New regression tests cover mixed-mode composition/order, both config schemas,
the actual resolver CLI, unknown/mandatory facts, independent review, explicit
delegation, escalation, and executing the descriptor from its advertised path.
The existing context-cache and output-budget checks remain unchanged. The
[byte probe](../../context-loading-repair-bytes.json) still reports zero context
for unchanged prompts and a 565-byte worker override. Bytes are not tokens or
billed usage. The skill remains below its existing 4,000-byte text budget.

See the implementation [decision note](../../../plugins/codex-model-router/docs/decision-lifetime.md).
The next live comparison must use a new frozen cohort and an explicitly
approved paid scope. Until it passes, performance recovery and full routing/
receipt compliance remain unproven.
