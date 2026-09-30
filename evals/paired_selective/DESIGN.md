# Selective Astra paired pilot design

This pilot tests a single frozen, public SWE-bench Pro V2 HARD-51 task:
`instance_flipt-io__flipt-c188284ff0c094a4ee281afebebd849555ebee59`.
It is an OCI authentication change that crosses configuration defaults and
validation, credential resolution, AWS ECR token decoding, storage integration,
and regression behavior. The published task's reference patch touches 15 paths.
That count is only a scope indicator. HARD-51 membership and patch size do not
prove Astra-only difficulty. The narrow hypothesis is that resolving the
security and lifecycle semantics is a demanding kernel, while inventorying
call sites, preparing simple fixtures or mocks, and running focused checks are
bounded lower-cost stages. If an Astra pilot again completes in roughly 22
calls, or Sol/low can own the security design without loss of quality, this
task is a poor discriminator and must be replaced before any broad claim.

The task comes from the [pinned V2 release](https://github.com/scaleapi/SWE-bench_Pro-os/tree/66f92766bba642462d4bbe5479e83f91f9211862/v2).
The public image index digest is
`sha256:b6a9cbf8ec8a1a250524433f3fcaa3afa8634bb1299880aac54b808948ef5bfa`;
its Linux/amd64 manifest is
`sha256:8e2458836acf6d14616949c3573584a86449c29eb4a0bb09fab9a9b5ec8b53e9`.
The included 6.1 MB `/app` source layer is pinned in `config.json` and is
extracted without image-internal Git metadata or Docker whiteouts. The public
verifier's hidden-test patch and config are frozen separately and are never
placed in an agent workspace. The V2 release reports reference-patch pass and
empty-patch fail for all 642 tasks; a local replay remains a separate,
non-official oracle until it is calibrated on this exact task.

## Comparison

Both arms receive byte-identical prompt bytes, a clean copy of the same Git
tree, the same toolchain/cache, permissions, and 50-minute limit. The baseline
CLI is launched explicitly as `gpt-6-astra/xhigh`, with plugins, hooks, and
multi-agent disabled. The treatment CLI is launched explicitly as either
`gpt-6-luna/medium` or `gpt-6-sol/low`; the plugin cannot change the primary
model. The primary choice is blocked until a paid, separate controller
calibration checks task decomposition, dependency order, native dispatch,
bounded packets, receipt collection, verification, and recovery on frozen
controller scenarios. Generic function-generation performance is irrelevant.
No `none` effort is allowed. The treatment must show an actual
`gpt-6-astra/xhigh` child assigned only the authentication-design/implementation
kernel and bounded easy work owned by the cheap primary or another cheap
worker. If it sends the whole request to Astra, overlaps writes, or lacks
inspectable packet evidence, selective-route compliance fails even if output
quality passes. The protocol never inserts treatment-only stage instructions
into the user request; stage boundaries must come from the task and plugin.

The published hidden tests, relevant package regression tests, and a semantic
check of security behavior apply identically to both patches after replay on
fresh copies. Process compliance is reported separately from quality. The
official Harbor verifier is preferred. A local Go replay is labeled as local
and must first pass on the published reference patch and fail on an empty
patch; Docker was unavailable for the earlier Flipt pilot. A failure or timeout
remains in the denominator.

Quality parity and total API-price USD across every parent and child session
are the primary decisions. End-to-end wall time runs from request submission
through terminal answer and required artifact completion in each arm; stage
spans are diagnostics only. Use at least three interleaved matched pairs before
making a stable latency claim. One pair is exploratory. A previous-plugin
control must be added before claiming a release regression result.

## Cost and feasibility

The [official Standard rates](https://developers.openai.com/api/docs/pricing)
retrieved on 2026-09-24 are, per 1M ordinary/cached/cache-write/output tokens,
$10/$1/$12.50/$50 for Astra, $2/$0.20/$2.50/$10 for Sol, and
$0.10/$0.01/$0.125/$0.50 for Luna. A response with more than 272K total
input tokens prices the *entire request* at 2x input and cache rates and 1.5x
output. `reasoning_output_tokens` are a subset of output, not an extra charge.
The collector fails closed on missing usage, model, or effort. These are
API-price estimates unless an invoice is available.

The previous Flipt Astra/high run used 22 calls and cost $3.386786, about
$0.154 per call. At unchanged usage, $10 supports about 65 calls; xhigh may
increase reasoning tokens, and a natural 100-call horizon is likely over cap.
The working Astra cap is $10 until changed by the user. The runner stops near
that cap, but a response is billed before its usage arrives, so it cannot
guarantee an exact hard USD ceiling. Budget cancellations count as failures.
No performance or cost benefit is claimed from this design or offline tests.

The current CLI rollout encrypts the `spawn_agent.message` body, and the child
rollout exposes only the `NEW_TASK` transport header plus encrypted payload.
The CLI `--json` transcript does not include that packet. Therefore the
agent-authored stage plan and child packet-hash echo are corroborating claims,
not independent proof of the hard-kernel boundary. The collector must report
that boundary as `UNKNOWN`, even when model selectors and observable edits
match the plan. This pilot cannot substantiate a selective-ownership result in
the current setup.

Official [Codex hook documentation](https://learn.chatgpt.com/docs/hooks)
lists only identity and permission fields for `SubagentStart`; it does not
expose the assignment packet there. The same documentation says `PreToolUse`
can observe `spawn_agent` as `Agent` and receives its `tool_input` arguments.
An opt-in `PreToolUse`/`PostToolUse` hook now emits only a bounded packet digest,
selectors, attempt IDs, and an available child ID to stdout. It makes no file
writes. The runner cannot currently capture hook stdout independently of the
agent, and the runtime has not been tested to prove the plaintext `message`,
matching tool-use IDs, and child ID reach those hooks. The offline collector
reconciles externally captured records only when supplied, and reports UNKNOWN
when identifiers or capture are missing. Even a matched transport audit does
not establish that the packet was semantically limited to the hard kernel;
that requires independent review of the exact packet through a separate secure
evaluator channel. No technical sandbox or history erasure is inferred.

The frozen `astra-hard-kernel-role.toml` is a candidate CLI role file for an
explicit `[agents.default]` binding. Source review indicates CLI 0.144.1 can
select its model and effort when native spawn selectors are unavailable, but
this project has not completed an empirical calibration of that precedence.
The `verified_role_config` parser records exact runtime/config evidence, but
the dispatch-authorizing preflight currently blocks this mode because a
hash-matched review assertion cannot prove the CLI loaded the role. Unit tests
validate structure only. Independently verifiable runtime calibration and
trusted hook capture are required before a live treatment can use it; the
fixture alone changes no CLI configuration.

Python 3.9+ standard library is used for the harness because the existing
evaluation harness, cost collector, and tests are Python. This adds no new
runtime dependency or language choice to the plugin itself.

## Stronger candidates considered

Teleport's HARD-51 auditd task (`...7744f72...`) tests Linux netlink status,
audit semantics, privilege handling, and integration across 11 files. It is
more security-intensive, but its published prompt specifies many exact header,
payload, and error details and its much larger build image threatens the $10
and local-oracle constraints. Teleport's key-store task (`...f432a71...`) has
cryptographic judgment but only two new files and little natural cheap work.
[SWE-Marathon v1.1](https://github.com/abundant-ai/swe-marathon) targets
ultra-long-horizon software work and uses Harbor/Modal; its scale makes a
$10 Astra/xhigh paired pilot infeasible without a revised budget and setup.
The OCI/ECR task is therefore a cost-aware exploratory candidate, not proof
of the product's long-horizon thesis.
