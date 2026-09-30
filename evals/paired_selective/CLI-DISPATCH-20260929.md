# Explicit CLI dispatch calibration

The CLI dispatch blocker is resolved for a separately pinned installed binary.
The `codex` command on PATH is 0.144.1; the desktop uses
`C:\Users\yahu2\AppData\Local\OpenAI\Codex\bin\faa963e871dd422c\codex.exe`,
version `codex-cli 0.158.0-alpha.2.1`, with SHA-256
`8f0554ede25bbc5450921897c468b2e84635aa513c5017457997af0954581f49`.

The older binary's v2 collaboration schema exposes only `task_name`, `message`,
and `fork_turns`. Its loaded catalog also omits Astra. Disabling the v2 feature
does not override the catalog's v2 selection for Sol. Its legacy backend exposes
selectors, but substituting that schema for the active v2 schema would be invalid.

The newer binary's live `debug models` catalog includes Astra, Sol, and Luna.
The following process-local setting exposes `model` and `reasoning_effort` on
its v2 spawn tool:

```text
-c features.multi_agent_v2={enabled=true,expose_spawn_agent_model_overrides=true}
```

This field is documented in the current
[official config schema](https://developers.openai.com/codex/config-schema.json).
Each evaluator launch must use the pinned executable, `--strict-config`, and
captured capabilities. This changes no user configuration or installed plugin.
The existing `dispatch_contract.py` authorizes its ordinary `explicit` route;
the `verified_role_config` authorization gate remains blocked.

## Evidence

Artifacts are under `../live/router-hook-canary-20260929/`:

- `probe_schema.py` captures the exact CLI-generated native schema at a
  loopback stub. It supplies the actual `debug models` catalog in memory and
  returns a deliberate HTTP 400 for the first Responses request. No paid model
  request is made. Prompt bodies, headers, and model default instructions are
  discarded; only public schema and reduced capability fields are retained.
- `schema-v1-complete.json` reproduces the old active schema even with the
  v2 feature disabled. `schema-desktop-explicit.json` captures the newer
  selector-capable schema and catalog.
- `explicit-contract.json`, `explicit-schema.json`, and `explicit-catalog.json`
  contain the hash-bound ordinary dispatch contract and derived capabilities.
  `explicit-preflight.json` records the unchanged validator's `ready` result.
- `explicit-canary-command.json` records the actual paid command, preflight,
  binary hash, and prompt/packet digests. `explicit-canary-receipt.json` retains
  reduced CLI events and source rollout references. `explicit-canary-verified.json`
  reopens those exact rollout files, checks their hashes, and joins the native
  call, started event, result, and completed child turn contexts.

The single canary used Sol/low as parent and Astra/xhigh as child with
`fork_turns="none"`. Plugins and hooks were disabled to calibrate native runtime
capability. The harmless child returned a fixed sentinel without tools. This
does not test router hook activation, semantic task boundaries, technical
isolation, controller competence, or benchmark quality/cost/latency.

The new runtime stores v2 activity as nested `event_msg.item_completed` items
whose type is `SubAgentActivity`. The started item's ID is the native spawn
call ID; `agent_thread_id` and `agent_path` link the child. The CLI JSON stream
alone did not expose this spawn event. Its parent rollout function call retains
the explicit model/effort and the result retains the canonical agent path.
The child's persisted `turn_context` independently records its effective model
and effort. Collectors must inspect these records, rather than use final text
or assume a missing CLI stream event means no child was created.

## Required benchmark integration

`evals/scripts/run_paired_arm.py` still binds `agents.default` and requires the
old role and hook pins. A dependent integration must add an explicit-selector
mode before running the pair; the existing role launch is not authorized by this
calibration. Use the same pinned newer binary for both arms, remove the default
role binding from explicit mode, apply selector exposure only to treatment,
and verify current installed plugin identity and hook activation independently.
Preserve byte-identical benchmark requests and separate controller calibration,
budget accounting, oracle grading, and semantic packet-scope proof.

No full benchmark was run in this calibration. The existing paired runbook is
not executable evidence that these remaining gates have passed.

## G4 integration status

The paired runner now defaults to the explicit-selector route for configured
treatment. It pins the reviewed 0.158.0-alpha.2.1 executable for both arms,
uses `--strict-config`, checks the provider-pinned zero-model schema capture,
derives the schema and catalog hash pins, and requires a ready result from the
ordinary dispatch-contract validator. The role mode remains available only
for a zero-model binding probe; a live role launch is rejected. The installed
plugin manifest, hook manifest, hook identities, trust state, and effective
selector feature are checked in a fresh App Server process before treatment.
The collector joins the nested `SubAgentActivity` start, native result, child
session lineage, and per-turn model and effort.

One bounded hook-enabled Sol/low to Astra/xhigh smoke completed. Its reduced
receipt is `../live/router-hook-canary-20260929/integration-smoke-receipt.json`;
`integration-smoke-analysis.json` records the packet limit. Router context,
native call/start/result, and child selectors were observed. The persisted
native `message` is opaque (`gAAAA…`), so it cannot prove exact packet scope.
An App Server retry using the installed hook's encrypted packet canary reached
a completed parent turn, but emitted no bindable native spawn or trusted audit
hook event for the reducer. See `integration-packet-receipt.json`. Packet scope
therefore remains `UNKNOWN`. The full paid benchmark is not ready to launch.
The G6 investigation below supersedes the proposed PreToolUse capture remedy.

## G6 packet boundary and reducer repair

The packet gate remains blocked for the pinned CLI. The intended 152-byte
plaintext has SHA-256 `0ac405785917a24262c34af590b5c550e6098dd9447809a5be15ed2a02a2342c`.
The native call, PreToolUse and PostToolUse commitments, and the exact child's
`agent_message.content.encrypted_content` carry the same 332-byte opaque value,
with SHA-256 `51de995ec0e11cfb5e6213e0644c8c396360db65a02fa7afe59243a3654408c1`.
That equality proves transport consistency. It provides no plaintext commitment.
Wrapping this value with a supervisor's canary encryption preserves that limit.
A child quoting the intended hash also cannot prove its complete received task.

The supported `thread/read` method with `includeTurns=true` was called for that
exact child without starting a turn. It returned reasoning and the final answer;
it did not expose the input packet. See `g6-thread-read-receipt.json`. A zero-model
schema capture with v2 disabled still advertised an encrypted `message` argument
for Sol in this pinned CLI (`g6-desktop-v1-schema.json`). The fetched official
configuration schema has no supported packet decryption or attestation setting.

Official Codex source at commit `c248f6d48b97eb4a2aa56147a0b11b7d763278b9`
marks the v2 message schema with `.with_encrypted()` and routes ordinary native
messages through `AgentMessage::Encrypted`. `DirectPlaintextMessage` is selected
only when the upstream response explicitly supplies an empty
`encrypted_function_args` list. This source inspection corroborates the runtime
observations; it is not a proof that the pinned binary was built from this commit.
The bounded read-only bridge source inspection found opaque agent-message
passthrough, not a local decryption/attestation boundary. Its checkout was not
established to match the running bridge binary. Source hashes are retained in
`g6-source-manifest.json` and `g6-bridge-inspection.json`.

Two deterministic collector failures are repaired:

- A v2 successful native result containing only `task_name` can now bind the
  child through a matching native call ID, started activity, and exact result
  path. Missing or conflicting evidence still fails. Hook, native, and post
  transport commitments must agree. This fallback reports its evidence source
  and leaves provenance `UNKNOWN`.
- App Server `subAgentActivity.kind=completed` is a lifecycle update, not a
  second spawn. Only `started` establishes lineage. Conflicting identities,
  unknown children/kinds, and follow-up activity keep the packet gate closed.

`g6-transport-replay.json` reproduces the successful structural join on the old
smoke evidence. Its audit markers are extracted from model-visible hook context
for diagnostics; they do not upgrade hook provenance. The fixture in
`evals/tests/fixtures/v2-dispatch-observed.json` replaces opaque text with a fixed
test value and recomputes commitments. No collector equates these commitments
with plaintext or authorizes the full benchmark.

The next stage is independent review of these repairs and the blocked packet
boundary. A useful subsequent capability stage must establish either an official
upstream attestation binding the complete decrypted packet to the native call and
child, or a supported native plaintext dispatch route with independently captured
child input. Search capability evidence before another paid canary. Any runtime
or provider change requires both benchmark arms to be repinned and recalibrated.
Changing schema flags or manufacturing `encrypted_function_args` in a proxy has
not been validated and is not an authorized shortcut. No new paid model call or
full benchmark was run in G6.

## G8 lineage hardening and plaintext capability

The file reconciler now checks each native subagent activity against the parent
thread and turn when those identities are present, rejects spawn calls in a
different namespace, and requires each completion to match a preceding start's
child ID and path. Duplicate or unsupported lifecycle records fail closed. The
App Server reducer treats subagent activity on a different parent thread or turn
as a conflict and rejects a second completion ID for the same child. The
observed v2 fixture now hashes its named source receipt. Negative tests cover
these cases; structural matches still leave provenance `UNKNOWN`.

The pinned `codex.exe` at SHA-256
`8f0554ede25bbc5450921897c468b2e84635aa513c5017457997af0954581f49`
was run against an in-process loopback Responses endpoint with retries disabled.
The endpoint returned a synthetic v2 `spawn_agent` function call with the
protocol's genuine `encrypted_function_args: []` metadata field and a harmless
plaintext marker. All three Responses requests went to loopback. The second
request selected `gpt-6-luna` and its input contained the exact marker; the
third request resumed the Sol parent. The probe retained request shapes and
marker matches, not request bodies. See `g8_plaintext_loopback_probe.py` and
`g8-plaintext-loopback-receipt.json`.

This establishes a pinned-CLI plaintext dispatch capability under a synthetic
provider response. It does not establish that the real provider emits an empty
`encrypted_function_args` list, that the installed bridge preserves that field,
or that the intended benchmark packet reached the actual child in plaintext.
Strict packet scope remains `UNKNOWN`; no paid model call or full benchmark was
run in G8. A subsequent route must obtain real-provider proof of the metadata
and exact child input, or an official post-decrypt commitment bound to the call
and child, before enabling the strict benchmark.

The bundled Python executable has also changed from the canary's expected hash
`4208e595bfa15ae7097fead82aca06eb935ef874aa373696f78d9cb408b42fcf`
to `a432275029de41f0ab824ed47ca77e356141c5cbf9bbbb2298955a0e0a7c002c`.
The live interpreter gate still rejects this mismatch. Unit tests now exercise
that expected rejection; no runtime pin was changed. A later live packet canary
also requires explicit review of this runtime update.
