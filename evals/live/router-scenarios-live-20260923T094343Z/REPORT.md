# Live router scenario benchmark: 2026-09-23

## Outcome

The campaign is a validated **FAIL**, not an overall pass. Normal-trust activation passed, all five required scenarios reached successful terminal outcomes, and all five artifact trees matched their frozen references. Required process and worker-identity gates did not all pass.

The authoritative machine-readable result is `live-results-final.json` (SHA-256 `aeff49f0e763c60f063392507f17411dcc4a9f4f17b260a3cc2db11a274af6e0`). `campaign-summary.json` is the compact operational summary. Raw session copies remain local because they contain unrelated inherited runtime context.

## Runtime and activation

- Source commit: `763e21f`
- Installed plugin: `codex-model-router` 1.0.5
- CLI: `codex-cli 0.155.0-alpha.16`
- Controller: `gpt-5.6-sol`, high reasoning
- Sandbox: `workspace-write`
- Activation session: `01a0cda6-b756-7b53-894f-89e95be13d17`
- Hook trust: normal persisted trust; no bypass
- Activation: source-bound config/context, validated DIRECT route before the note read, unchanged artifact, and successful terminal result

The three trusted hook hashes were `358081…` (SessionStart), `303a53…` (UserPromptSubmit), and `cdf685…` (SubagentStart). The activation answer uniquely fenced the exact `unchanged` value and added explanatory prose; the validator records that formatting separately while retaining exact-byte, successful-read, unchanged-tree, final-agreement, and terminal checks.

## Scenario results

| Scenario | Artifact | Process | Identity | Observed behavior |
| --- | --- | --- | --- | --- |
| direct-small-control | PASS | PASS | PASS | DIRECT, no worker, only `plguin` → `plugin` |
| investigation-reuse | PASS | UNKNOWN | UNKNOWN | Two serial workers; producer returned `docs/ops.md`, value 45, and a handoff hash; persisted consumer packet is encrypted, so receipt consumption cannot be independently proven |
| serial-escalation | PASS | FAIL | UNKNOWN | DIRECT → ISOLATED_SERIAL with `scope-expanded`; one combined worker performed the edits instead of four required dependent stages |
| parallel-disjoint | PASS | PASS | UNKNOWN | Three primary Luna/low workers overlapped for 26,096 ms on disjoint files; a fourth later beta validator ran after the primary work |
| architecture-review | PASS | FAIL | UNKNOWN | Distinct Luna author and Astra/high reviewer; review was ordered and substantively passed, but the reviewer omitted the exact required `Verdict: PASS` line |

Delegated identity is UNKNOWN because current persisted parent spawn packets are encrypted and worker finals echoed Worker name and Task ID but omitted Native task name. Actual child runtime metadata still records the selected models and efforts.

## Controls, timing, and cost

The source-backed supported-selection control accepted `gpt-6-luna`/low. The negative-capability control rejected a nonexistent model with a nonzero exit. Both controls passed.

Across the five scenario parents, measured lifetime totals 1,067,632 ms. Their terminal usage records report 2,191,799 input tokens, 2,007,201 cached input tokens, 165,637 cache-write input tokens, 27,494 output tokens, and 9,571 reasoning tokens. These parent totals exclude the nine child sessions: the 123 response IDs are distinct across the 14 scenario sessions. Including children, the five scenarios used 3,193,668 input tokens (2,832,916 cached and 311,803 cache-write) and 43,896 output tokens.

Using the [official OpenAI API Standard rate card](https://developers.openai.com/api/docs/pricing) on 2026-09-23, the five scenarios have an **estimated API token cost of $2.715473**. The controller used `gpt-5.6-sol`; workers used `gpt-6-luna` and one `gpt-6-astra`. Every observed response had fewer than 272,000 input tokens, so the short-context rates apply. The calculation treats reasoning tokens as part of reported output tokens and calculates ordinary input as `input - cached input - cache-write input`.

| Scenario | Parent and child sessions | Estimated USD |
| --- | ---: | ---: |
| direct-small-control | 1 | $0.219784 |
| investigation-reuse | 3 | $0.460465 |
| serial-escalation | 2 | $0.429673 |
| parallel-disjoint | 5 | $0.634925 |
| architecture-review | 3 | $0.970626 |
| **Five scenarios** | **14** | **$2.715473** |

The successful activation probe adds an estimated $0.149307. Two failed activation attempts that reached a model add $0.181081; a pre-session CLI invocation failure consumed no observed model tokens. Across these identified attempts and the five scenarios, the estimated API token cost is **$3.045860**. The rate card, per-model token classes, and exact unrounded totals are in `campaign-summary.json`.

Formula per session: `(ordinary_input × input_rate + cached_input × cached_rate + cache_write_input × cache_write_rate + output × output_rate) / 1,000,000`. The Standard short-context USD rates per million tokens are `gpt-5.6-sol` $4/$0.40/$5/$20, `gpt-6-luna` $0.10/$0.01/$0.125/$0.50, and `gpt-6-astra` $10/$1/$12.50/$50, in the same input/cached/cache-write/output order. No Fast-mode override was present in the captured launch commands. These figures estimate API pricing from observed token usage; they are not a verified invoice. The campaign has no live forced-direct or mandatory-delegation comparison arm, so it does not establish cost savings.

## Repository defects fixed during the campaign

1. The activation preflight still expected the pre-1.0.5 Windows hook command and rejected the committed `cmd.exe /d /c python ...` launcher.
2. The live collector omitted direct-small and required child sessions for every case.
3. The collector did not parse current CLI `item_completed` route/user events, managed workspace metadata, dual lifecycle-hook contexts, or current subagent session metadata.
4. Required routing-policy, resolver, capability-evidence, and dispatch-preflight operations were incorrectly counted as controller business work.
5. Router capability scratch directories were incorrectly graded as business artifacts.
6. Parallel validation workers were incorrectly counted as primary artifact workers, and encrypted receipt evidence was reported as failure rather than unknown.

The plugin package itself did not change, so the tested installed version remained 1.0.5 and no SemVer bump or reinstall was required.

## Preserved attempts and remaining risks

Earlier roots remain unmodified. This campaign also preserved failed fresh attempts that exposed stale hook trust, the stale activation-preflight contract, a misplaced global CLI option, and one recovered resolver invocation failure. The selected activation root contains the clean first-try resolver result.

The remaining failures are product/controller behavior revealed by live execution: failure to preserve the frozen four-stage serial DAG, failure to produce the exact reviewer verdict contract, and incomplete/encrypted identity evidence. They are not converted into passes by correct artifacts.
