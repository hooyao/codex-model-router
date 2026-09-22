# Treatment isolation decision and bounded repair result

## Initial repair decision

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

## Authorized continuation

The controller subsequently authorized one new paid preflight per arm and the
formal campaign on success. The continuation uses fresh homes/workspaces and
ephemeral sessions, and explicitly opts into the frozen Astra-card USD 50 soft
cap. The `--soft-budget-pricing` path requires no hard provider-budget guard.
CLI-visible descendant usage is inventoried once; absent usage is reported as
incomplete. This supersedes the initial session-persistence decision above.

At the end of the initial repair, the campaign was blocked on another authorized
live treatment preflight. The
existing `run --live` path still uses its original provider guard; the requested
USD 50 soft-cap campaign was not entered or substituted for that guard after
the treatment stop. Any continuation must apply the user's current budget
instruction rather than treat the legacy guard as a user requirement. Raw
execution artifacts, terminal refusals, offline context renderings, the empty
repair-session inventory, and cost ledger are retained outside the source tree.

## Offline CLI hook delivery repair

The installed executable is Codex CLI 0.144.1 (SHA-256
`cbacbb9726262ef558b4af0438a1b2a5bba9076132401d947b5b4d2bf92ab0e4`).
Its `exec --help` documents `--dangerously-bypass-hook-trust`; the official
[hook documentation](https://developers.openai.com/codex/hooks/) distinguishes
hook discovery from trusting an exact definition. `hooks/list` exposes the
definition key, source, event, enabled state, current hash, and trust status.
Non-managed entries start untrusted; the invocation flag explicitly activates
enabled entries after the harness verifies its isolated config/package hashes.
This does not persist trust or bypass tool approvals/sandboxing.

The preceding failed paid probe already supplied the trust flag. A new
zero-model diagnostic reproduced the failure: `hooks/list` listed all three
candidate plugin handlers as enabled and untrusted, while `codex exec` sent
skills but no lifecycle context. An inline user-layer control produced both
event messages. Thus plugin discovery alone is insufficient on this executable;
we do not claim that a newer CLI shares this observed behavior.

Provisioning now renders the exact candidate event groups in `[hooks]` in the
fresh router `config.toml`. It preserves matchers, timeout, and output-limit
metadata, resolves the unchanged `router_hook.py` under the child's `CODEX_HOME`,
and pins the Python interpreter running the harness. Windows commands use
PowerShell call/path syntax instead of `%PLUGIN_ROOT%`; POSIX commands use shell
quoting and `$CODEX_HOME`. The original plugin keys have `enabled = false` in
`hooks.state` to prevent double delivery. The baseline has no such registration
or package. No candidate files or routing-policy text change, and config bytes
remain identical when copied into fresh formal-slot homes.

The CLI's `--profile` option layers `<name>.config.toml` in this version. The
harness uses the generated base `config.toml` directly, with no named profile or
legacy `[profiles]` assumptions. Child HOME/USERPROFILE, XDG, APPDATA, and
LOCALAPPDATA paths are isolated. Mandatory system-managed Windows Defender
hooks are still visible in the CLI registry; they are not imported user config
and are neither modified nor bypassed by this repair.

`evalplus_hooks.py` proves delivery using the production command builder and
fresh per-slot clones. Read-only app-server RPCs inspect registration. A local
HTTP server captures and rejects `/responses`; it has no model implementation,
credentials, forwarding, or successful response. CLI retries and remote model
discovery are disabled. Two distinct developer messages must contain the
SessionStart/UserPromptSubmit contract and routing delimiters. Baseline must
have no router or other skill context. The receipt binds candidate, isolation,
CLI/Python executables, harness code, registry, command/status, and raw request
evidence. Missing, stale, changed, or incomplete evidence blocks both formal
paths and the paid treatment preflight before any model request.

The fixed offline verification passed on fresh homes with unchanged candidate
hash `1536c1d802842afd5ff289532b4959bc59cbf106becf5004c808190310a8dd97`.
Evidence is retained under
`Q:\MyProjects\codex-model-router-benchmarks\cli-hook-delivery-repair-20260922`.
The `diagnosis` directory contains the bundled-registration failure; `verification`
contains the successful user-layer registration and both outgoing hook contexts.
This repair invoked no model, delegated no worker, graded no Docker task, and
started no formal benchmark slot. It does not authorize a subsequent paid probe.

At that revision, CLI 0.144.1 spilled/truncated these approximately 2,800-token
hook outputs despite their `additionalContextLimit` metadata. Both lifecycle
messages and routing delimiters reach the outgoing request; full output is saved
by Codex, but this receipt does not prove that every policy sentence is delivered
inline or that a worker follows it. SubagentStart registration is checked without
dispatching a worker. A later authorized treatment probe must still pass before
formal generation; no benchmark quality, cost, or latency improvement is claimed.

## Compact profile follow-up

The subsequent authorized paid presence probe passed both arms, but inspection
of the captured routing blocks found invalid JSON after truncation. The campaign
stopped before any formal slot. The [benchmark-only compact profile](BENCHMARK-PROFILE.md)
now preserves the full candidate controller/worker contracts while reducing the
routing JSON to 1,044 bytes. Exact full-context and JSON checks pass for both
controller events and a zero-model worker event simulation. The original plugin
and normal user/workspace config remain unchanged. See that decision for the
byte bound, evidence, simulation limits, and next authorized validation step.
