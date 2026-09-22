# Treatment isolation decision and bounded repair result

## Decision

The CLI must load a harness-created config in each fresh arm home. An installed
plugin inventory is insufficient: `--ignore-user-config` suppresses the config
that enables the treatment. Baseline has no installed packages; router installs
the exact candidate through a local marketplace and verifies its file-tree hash.
Separate child user profiles also isolate implicit user skill discovery. Only
explicit provider connection/authentication settings enter the generated config.

Keep session files in those isolated homes and disable memory generation/use.
This retains evidence needed to investigate parent/child usage without importing
personal session history. Preflight and generation share command/environment
construction. Before any model probe, discover installed CLI feature flags;
the published config schema can be newer than the host CLI. The harness pins
child hook lookup to the Python interpreter running the harness.

Formal admission requires saved treatment reports and a successful restricted
Docker grader import. A baseline report must have no skills or hook context;
router must report its skill and both lifecycle contexts. Receipts bind raw
evidence, config, candidate, and harness code. Model reports are observational
evidence, not independent proof of instruction enforcement. No generated sample
may execute on the host.

## September 22 repair result

The single authorized treatment preflight tried each arm once. CLI 0.144.1
rejected `skip_host_skill_discovery` before starting either model request.
The flag was subsequently removed and runtime flag discovery added. No second
model preflight was attempted. All 24 formal slots remain unstarted.

Offline `codex debug prompt-input` rendering observed zero skills in baseline
and exactly `codex-model-router:initialize-router` and
`codex-model-router:model-router` in router. The installed candidate matched
package hash `1536c1d802842afd5ff289532b4959bc59cbf106becf5004c808190310a8dd97`.
This establishes skill/config isolation, but does not establish SessionStart
or UserPromptSubmit delivery to a live model.

EvalPlus 0.3.1 imports successfully in the restricted Docker image
`evalplus-router-repair@sha256:52bd61039553f4936570794cbe03cb54f2aea95bed4ae3a7c5770d0262e79705`.
No generated code was executed. This is a grader readiness check, not a scored
benchmark execution. The repair attempt made zero model requests, started zero
parent/child threads, and incurred USD 0.00 in model API-equivalent estimates.
Earlier diagnostic costs are outside that amount. There are no pass@1 or
latency results and no evidence of statistical superiority.

Validation: the full evaluation suite passed 93 tests; after adding the live
admission regression, the updated isolation suite passed all 10 tests (94
distinct evaluation tests in total). All 52 plugin tests, repository package
validation, official plugin validation, both official skill validators, and
`git diff --check` passed. Validators used Python 3.13 because the host's default
Python 3.9 installation was missing its `codecs` module; no system installation
was changed.

The campaign is blocked on a newly authorized live treatment preflight. The
existing `run --live` path still uses its original provider guard; the requested
USD 50 soft-cap campaign was not entered or substituted for that guard after
the treatment stop. Any continuation must apply the user's current budget
instruction rather than treat the legacy guard as a user requirement. Raw
execution artifacts, terminal refusals, offline context renderings, the empty
repair-session inventory, and cost ledger are retained outside the source tree.
