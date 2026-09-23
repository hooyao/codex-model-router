# Corrected role-bounded analysis (v2)

This file supplements, and does not replace, the original `live-results.json`,
`SUMMARY.md`, or `ANALYSIS.md`. The raw transcripts and historical result remain
unchanged. The versioned derived report is `corrected-analysis-v2.json` and
hash-links the original result plus every transcript, session index, session
source, and artifact tree it validates.

The historical campaign remains **FAIL** under the corrected analysis:

- exact artifact trees: PASS (4/4);
- required route/process evidence: FAIL overall because the architecture
  `INDEPENDENT_REVIEW` requirement is unknown in role-bounded route evidence;
- worker identity/dispatch contract: FAIL;
- automatic activation: FAIL; and
- overall campaign acceptance: FAIL.

The v2 collector does not search inherited Skill text or aggregate session
prose. It treats encrypted/unavailable packet identity as unknown, records the
actual native `task_name`, observes that all historical spawn calls omitted
explicit model and reasoning-effort arguments, records child runtime metadata
separately, and validates the final worker echo independently. Later runtime
metadata does not retroactively prove selector intent or verified inheritance.

The parallel case has 15,034 ms of three-way first-assignment overlap. Its
session-lifetime overlap is reported separately as 16,915 ms. Serial/review
chronology likewise uses assignment boundaries rather than whole session
lifetime. Activation root cause remains unknown because the historical run did
not capture the required Python/sandbox preflight artifact; observed symptoms
are not promoted to a platform-cause claim.
