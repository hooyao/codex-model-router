# Selective Astra pilot: operator runbook

The commands below describe the historical role route and are superseded by
`CLI-DISPATCH-20260929.md`. Two bounded paid integration probes have run;
the full pair has not. Packet-scope evidence remains `UNKNOWN`, so do not
launch the full treatment or claim a matched selective-routing result.
Run the full pair only after independent review of `DESIGN.md`, the pinned
assets, the $10 Astra cap, and a trusted packet-scope capture.
The current host's `codex` on `PATH` reports 0.144.1; choose one explicit CLI
binary that exposes all three GPT-6 selectors and use it for both arms. Do not
assume the plugin changes the primary model. Verify the installed plugin's
manifest hash and version before treatment launch.

## App Server capability probe

The isolated hook inventory makes no model call and changes no global hook
trust. It launches the exact 0.144.1 binary with plugins disabled, hooks
enabled, one absolute `dispatch_audit.py` command for each exact
`^collaborationspawn_agent$` PreToolUse/PostToolUse matcher, and the frozen
Astra/xhigh `[agents.default]` role binding. It discovers the actual hook keys
and `sha256:` definition hashes through `hooks/list`, applies only those two
hashes as process-local trust, and rechecks the inventory:

```powershell
py -3 evals/scripts/app_server_probe.py --hook-inventory --codex-cli <absolute-codex.exe> --expected-cli-sha256 <pinned-cli-sha256> --audit-script <absolute-dispatch_audit.py> --expected-audit-sha256 <pinned-audit-sha256> --role-config <absolute-astra-hard-kernel-role.toml> --expected-role-sha256 <pinned-role-sha256> --timeout 15
```

The receipt emits bounded keys, definition/command hashes, source classes,
events, matchers, trust flags, and the effect each extra hook could have on a
spawn call. It never emits commands or hook output. `ready_for_hook_live` stays
false if another tool-use hook can match the spawn or any managed lifecycle
hook is present. On this host, managed system PreToolUse,
PermissionRequest, and PostToolUse hooks use `.*` matchers, so the live gate is
closed even though the two session hooks become trusted in the second process.
The hook-live model turn is not launched by inventory mode.

After reviewing the complete six-hook managed Windows Defender inventory,
one fixed-sentinel experiment can use `--fixed-sentinel-live` with the same
pinned CLI, audit script, and role file arguments. This separate gate requires
their exact hashes, the two trusted session hooks, and all six managed hook
keys, source classes, events, matchers, definition hashes, command hashes, and
enabled/managed flags to match the observed pin. The live App Server process
rechecks `hooks/list` before starting a turn; any drift stops before a model
call. Managed hooks remain enabled and can deny or rewrite tool use.

```powershell
& 'C:\Users\yahu2\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' 'Q:\MyProjects\codex-model-router\evals\scripts\app_server_probe.py' --fixed-sentinel-live --codex-cli 'C:\Users\yahu2\AppData\Local\Programs\OpenAI\Codex\bin\codex.exe' --expected-cli-sha256 cbacbb9726262ef558b4af0438a1b2a5bba9076132401d947b5b4d2bf92ab0e4 --audit-script 'Q:\MyProjects\codex-model-router\plugins\codex-model-router\hooks\dispatch_audit.py' --expected-audit-sha256 428d08554712878bcc9fa84224413ca1253fc4430dbbc0a547ba786fcf8e8e99 --role-config 'Q:\MyProjects\codex-model-router\evals\paired_selective\astra-hard-kernel-role.toml' --expected-role-sha256 cb5ae4d38b859b51b9429fe199606acaeb181d6428fbb53b67ff8083d965fcb0 --timeout 90
```

That live option asks one Sol/low parent to use the `default` Astra/xhigh role
for a harmless child message with `task_name: capability_probe` and
`fork_turns: none`; it does not ask for hidden model or effort overrides. It
generates an ephemeral 3072-bit RSA private key and separate run/launch IDs
before process start. Only the public key and combined run ID enter the hook
process environment. The combined ID is 63 characters (120-bit run plus
128-bit launch, hex-encoded with one separator), within the hook's 64-character
limit. The private key and decrypted PreToolUse `message` stay
in supervisor memory. The bundled Python executable is pinned by path and
SHA-256 and supplies `cryptography` 50.0.1; a non-bundled interpreter requires
an explicit `--expected-python-sha256` matching its executable and the same
crypto version. `py -3` is not sufficient for this live mode.

The opt-in hook returns encrypted `systemMessage` as a PreToolUse Warning
entry, then a compact PostToolUse additional-context digest. The reducer reads
only the exact trusted sessionFlags audit hook's bounded `hook/completed`
entries, authenticates the envelope's AAD and run/session/turn/call/header,
decrypts in memory, verifies message digest and byte count, and joins the
compact Post digest to native activity and child selectors. Its ordinary
receipt contains only reduced IDs, digests, lengths, and booleans; no private
key, packet text, ciphertext, hook output, event stream, or stderr is saved.
`encrypted_pre_packet: OBSERVED` requires one complete matching chain;
missing, duplicate, truncated, replayed, or tampered entries remain `UNKNOWN`.
The Pre canary does not record `task_name`, `fork_turns`, or explicit-selector
omission, so `hook_context` remains `UNKNOWN` in canary mode. Even matching
observations leave `effective_dispatch_proof` `UNKNOWN`; they are not a claim
that the runtime used an unchanged effective argument packet.

The pinned 0.144.1 [PreToolUse parser](https://github.com/openai/codex/blob/44918ea10c0f99151c6710411b4322c2f5c96bea/codex-rs/hooks/src/events/pre_tool_use.rs)
maps `systemMessage` to Warning, while its [common context helper](https://github.com/openai/codex/blob/44918ea10c0f99151c6710411b4322c2f5c96bea/codex-rs/hooks/src/events/common.rs)
adds only `additionalContext` to model context. The reducer additionally
checks observed Context entries and agent messages for the canary marker and
reports any appearance as `UNKNOWN`; source inspection alone cannot replace
a live event observation.
For failed intended audit hooks, the receipt now reports only entry-kind counts
and a fixed failure category: command start/shell or timeout, non-2 exit,
invalid hook JSON output, serialization, or other/unknown. The 0.144.1 event
has no exit-code field, so ambiguous errors stay unknown; error text and its
hash are never emitted.
The compact Pre audit also requires `task_name` in PreToolUse input, which the
0.144.1 v1 spawn schema does not require; missing that field yields `UNKNOWN`.
No raw prompt, packet, event stream, hook output, or stderr is saved by the probe.

Run the synthetic check first: `py -3 evals/scripts/app_server_probe.py`. Its
`source: synthetic` receipt cannot establish runtime provenance. A live probe
launches exactly the supplied installed `codex.exe` through stdio, starts one
ephemeral parent turn in a fresh temporary directory, and asks for one harmless
Luna/low child call. It incurs model usage. The supervisor generates the prompt and an
exact-message sentinel in memory; it emits only IDs, selectors, field-presence
flags, SHA-256 digests/byte counts, and hook lifecycle status. It does not save
the raw event stream, prompt, or child packet. The requested read-only policy is
reported only as a request; this probe does not establish sandbox enforcement.
App Server stdio uses JSON lines and omits the `jsonrpc` header on the wire;
the probe accepts that form while rejecting an incorrect explicit header.

```powershell
$env:CODEX_MODEL_ROUTER_DISPATCH_AUDIT = '1'
py -3 evals/scripts/app_server_probe.py --live --codex-cli <absolute-installed-codex.exe> --expected-cli-sha256 <verified-sha256> --router-hook-config <absolute-installed-hooks.json> --router-audit-script <absolute-installed-dispatch_audit.py> --model gpt-6-sol --effort low
```

The receipt's `usable_for_dispatch_provenance` is false unless the launched
process supplies a completed parent turn, exact child message, spawn call ID,
child thread ID, child model/effort, and matching PreToolUse and PostToolUse
lifecycle pairs from the pinned router audit hook source. The requested parent
selector must be an approved Luna/medium or Sol/low pair and match the observed
thread selector. Missing selectors or hook identity produce partial capability.
The child selector must equal the requested Luna/low selector.
Missing fields remain a capability failure. A true result establishes those
observable fields for this probe only; it does not grade the treatment packet's
semantic hard-kernel scope. Do not feed an external JSONL file to this probe.
The receipt also includes bounded notification/item-type counts, whether a
terminal agent message was observed, its SHA-256/byte count when available,
and the loaded thread's `multi_agent` feature state. The feature flag is not a
tool inventory. `thread_configured_effort_at_start` may be null and is not
per-turn telemetry; `requested_turn_effort` is the supervisor's turn setting.
The collab item reports the spawned agent's requested selector, not independently
observed child execution settings. These reduced fields cannot alone prove why
the model did not call a tool.
The `collab_tool_counts`, `hook_event_counts`, and fixed shape-rejection counts
show which typed events passed the reducer's parent and turn checks. Validated
`subAgentActivity` IDs provide child lineage; `agentPath` is never emitted.
Hook run IDs are opaque strings in the installed schema. The receipt uses a
bounded SHA-256/byte-count pair to bind start and completion without emitting
their raw ID; source, handler, scope, and turn checks still govern identity.
The probe uses the installed CLI's App Server schema, not the separately saved
0.155.0-alpha.9.2 schema bundle in the evaluation evidence.
If the launched process fails, the receipt retains all reduced evidence observed
so far and includes fixed `failure` and `failure_stage` values. A cleanup error
after a completed turn is reported as `teardown_failed`; raw process output and
exception text are never included. Treat any failed receipt as partial capability.

## Before a paid run

1. Reconfirm the [GPT-6 model pages](https://developers.openai.com/api/docs/models/gpt-6-astra),
   [Standard prices](https://developers.openai.com/api/docs/pricing), and the
   active CLI model catalog. Update the frozen config and tests if they changed.
2. Materialize the source layer in a new directory with
   `prepare_selective_fixture.py`; it must print tree
   `20121a7049c58571664b87989d1fc7bab8562884` and an empty diff. The
   extracted image layer includes no gold patch or hidden tests in the
   runnable checkout. Make two fresh, isolated clones from this seed. Keep
   their Git trees and initial status identical.
3. Prewarm the same Go module/build cache for both arms in a disposable third
   clone. Confirm relevant Go packages build. Do not modify the frozen seed.
   Use the same PATH, cache, sandbox, network policy, and CLI build for both.
4. Calibrate `gpt-6-luna/medium` and `gpt-6-sol/low` as controllers on the
   existing frozen live routing scenarios (`investigation-reuse`,
   `serial-escalation`, `parallel-disjoint`, and `architecture-review`). Use
   fresh matched fixtures and actual CLI sessions for each. An independent
   reviewer scores six boolean capabilities from retained transcript/session
   evidence: decomposition, dependency order, explicit native dispatch,
   packet/receipt fidelity, verification, and recovery when validation fails.
   Save one evidence file per candidate and a `calibration.json` with
   `schema_version: 1`, `observations` for both configured candidates, and
   `selected: [model, effort]`. Each observation needs `model`, `effort`,
   `terminal: "completed"`, `evidence_path`, `evidence_sha256`, and `criteria`
   with all six booleans. The runner selects Luna if it passes every criterion;
   otherwise it selects Sol/low if it passes every criterion. Neither passing
   blocks the treatment. Calibration cost belongs in campaign spend, although
   the paired arm cost excludes the separate calibration tasks.
5. Confirm the published oracle. Prefer official Harbor patch replay on the
   pinned image digest. If unavailable, run the local `grade_swepro_go.py`
   against the reference patch and empty patch before grading either arm;
   reference must pass and empty must fail. The local grader is not an official
   SWE-bench Pro score. Do not expose reference or test patches to agents.

The following PowerShell shape uses separate paths. Replace `<...>` with
reviewed absolute paths, and do not reuse an evidence or grading directory:

```powershell
py -3 evals/scripts/prepare_selective_fixture.py --layer evals/paired_selective/assets/source-layer.tar.gz --expected-layer-sha256 d936e33490bfbfc686871c236abfde86b629c4aa153cc6220a108d05c5827dfe --destination <seed>
git clone --no-hardlinks <seed> <baseline-workspace>
git clone --no-hardlinks <seed> <treatment-workspace>

py -3 evals/scripts/run_paired_arm.py --live --config evals/paired_selective/config.json --arm baseline --workspace <baseline-workspace> --evidence <baseline-evidence> --codex-cli <same-codex.exe> --go-bin <go-bin-directory> --go-cache-root <shared-go-cache-root>
py -3 evals/scripts/run_paired_arm.py --live --dispatch-audit --config evals/paired_selective/config.json --arm treatment --workspace <treatment-workspace> --evidence <treatment-evidence> --codex-cli <same-codex.exe> --go-bin <go-bin-directory> --go-cache-root <shared-go-cache-root> --plugin-root <installed-plugin-root> --calibration <calibration.json>
```

The 2026-09-25 matched Flipt calibration has `selected: null`: both observed
controllers completed one scenario without verified native dispatch. The normal
treatment command above rejects it. To measure that failure mode on the same
frozen task, an operator may add `--negative-treatment-controller gpt-6-sol/low`
to the treatment command after reviewing the calibration evidence. This exact
opt-in requires both failed, hash-bound candidate trials and keeps the same
plugin, role, binding preflight, config, budget, and session accounting. The run
and pair report say `CALIBRATION_FAILED`; quality, wall time, and all-session
cost remain measurable, but the arm cannot count as selective Astra success.

Interleave arm order across at least three fresh matched pairs. Start each
arm's timer immediately before request submission and end after terminal
answer/artifact completion. The runner includes all parent/child usage,
long-context pricing, startup and wait time, failures, and retries. Its budget
stop sees usage only after a response is billed; leave a suitable margin.

For local replay, run the pinned V2 verifier's three Go packages and the same
test patch on both captured arm patches. The Go binary below is a Linux
executable path visible to WSL, not a Windows path. The grader retains its own
`grade.json` and stdout/stderr:

```powershell
py -3 evals/scripts/grade_swepro_go.py --seed <seed> --arm <baseline-workspace> --candidate-patch <baseline-evidence>/candidate.patch --test-patch evals/paired_selective/assets/hidden-test.patch --oracle-config evals/paired_selective/assets/test-config.json --grade-workspace <baseline-grade-workspace> --go-executable <wsl-linux-go> --packages ./internal/config ./internal/oci ./internal/oci/ecr
py -3 evals/scripts/grade_swepro_go.py --seed <seed> --arm <treatment-workspace> --candidate-patch <treatment-evidence>/candidate.patch --test-patch evals/paired_selective/assets/hidden-test.patch --oracle-config evals/paired_selective/assets/test-config.json --grade-workspace <treatment-grade-workspace> --go-executable <wsl-linux-go> --packages ./internal/config ./internal/oci ./internal/oci/ecr
py -3 evals/scripts/collect_selective_pair.py --config evals/paired_selective/config.json --baseline <baseline-evidence> --treatment <treatment-evidence> --treatment-audit <externally-captured-redacted-hook-stdout.jsonl> --baseline-grade <baseline-grade-workspace>/grade.json --treatment-grade <treatment-grade-workspace>/grade.json --output <new-pair-report.json>
```

The collector recomputes cost from retained sessions, binds grades to the
captured patch bytes and exact command, and checks actual native spawn
model/effort against child context. Interrupted runs retain their observed
cost as a lower bound; an in-flight model response can bill after the last
observed usage event, so the $10 stop is not a hard spend guarantee.
When CLI 0.144.1 omits `cache_write_input_tokens`, accounting spans zero through
all uncached input tokens billed as cache writes. The runner stops at the
conservative upper estimate. Complete sessions can retain a bounded USD
interval, while an exact USD value or ratio is reported only when the class is
known. Missing model, usage, or session evidence keeps total cost `UNKNOWN`.
If a native spawn is observed without a matching child rollout, the runner
stops after a short discovery grace period and the collector reports only the
observed USD lower bound; it emits no total-cost ratio.

The audit flag enables bounded `PreToolUse`/`PostToolUse` output without
hook-controlled file writes. The current runner cannot independently capture
that output: an operator must arrange trusted external hook-stdout capture,
verify its provenance, and pass its JSONL path to the collector. Do not pass an
agent-authored file. If the runtime omits tool-use IDs, child IDs, or hook
stdout, the collector reports UNKNOWN. No paid run is needed to establish this
capability; probe the CLI serialization separately first.

The native parent rollout encrypts spawn `message` arguments. The child
rollout exposes only the assignment transport header plus encrypted payload;
the CLI `--json` transcript does not include the spawn packet. A worker's hash
of its received packet is self-attestation, and an agent-authored plan is not
independent proof of the native packet. The collector reports the selective
hard-kernel scope as `UNKNOWN` even when those hashes, timestamps, selectors,
and observable patch writes align. This evidence can support debugging, but
it cannot support the product's selective-ownership claim until the runtime
provides a trustworthy plaintext packet capture or equivalent dispatch audit.

Report every attempt, including failures and budget stops. Do not claim speed
from one pair or from synthetic spans. Repeated interleaved matched pairs are
needed to evaluate wall-time stability; per-stage timing only diagnoses it.
