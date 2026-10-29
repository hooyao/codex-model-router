# User acceptance, 2026-10-29

**Decision: accepted with a documented serial-performance exception for PR #10.**

After reviewing the completed five-scenario, three-pair comparison, the user
stated that the serial result is within an acceptable range. This accepts the
measured serial tradeoff for plugin 1.3.3, source
`9f72e6aead1b28e11e7e8da385609cf1ba11f698`:

- Median paired wall time: +13.4%, within the original 15% tolerance.
- Median paired total tokens: +9.0%, accepted despite the original 5% tolerance.
- Median paired API-estimated cost: +18.3%, accepted despite the original 5% tolerance.

The other four scenarios meet the predeclared thresholds, and all 30 artifact
checks passed. The original investigation/reuse regression meets its thresholds
in the repaired cohort.

This is a subsequent user acceptance decision for this PR and measured cohort.
The [report](REPORT.md), [analysis](analysis.json), original thresholds, and raw
scored receipts retain their original benchmark verdict:
`NOT_PASSED_SERIAL_TOKENS_AND_COST`. Future benchmark thresholds are unchanged.
Acceptance does not establish general performance gains, actual bridge billing,
or full routing/receipt compliance beyond the evidence described in the report.

No additional model calls or paid experiments were performed for this decision.

The accepted-reference change and earlier candidates are indexed in
[BASELINE_HISTORY.md](../../BASELINE_HISTORY.md). Historical comparison arms
remain pinned to 1.3.0; the next plugin comparison selects the accepted 1.3.3
source explicitly and collects fresh matched evidence.
