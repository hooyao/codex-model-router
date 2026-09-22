# Compact benchmark routing profile

This profile repairs CLI 0.144.1 routing-JSON truncation without changing the
plugin package or a user's workspace configuration. It uses the existing Python
standard-library harness. The benchmark arm is the candidate plugin **with this
explicit profile**, not its general-purpose default routing configuration.

## Profile contents and isolation

`prepare-homes` generates `CODEX_HOME/benchmark-router/routing.json`,
`profile.json`, and a pinned copy of `evalplus_profile.py` as `hook.py` in the
router arm only. Fresh slot homes copy that exact tree and verify its hash.
The baseline has neither this directory nor router hooks or skills.

The generated routing file uses the normal candidate schema and validator,
including every required field. It contains three short examples for clear
Python functions, ordinary edge cases, and difficult algorithms or failed
verification. If the CLI exposes an Astra override, a fourth review example is
included. Exact model IDs and supported efforts are selected deterministically
from `codex debug models --bundled`, which makes no model request. The captured
native spawn-tool schema must also expose every resolved pair. Catalog metadata
is runtime capability evidence, not a paid check of backend entitlement.

On the measured CLI, the resolved pairs were `gpt-5.6-luna/low`,
`gpt-5.6-terra/medium`, and `gpt-5.6-sol/high`. The controller remains the
campaign's `gpt-6-astra`; the profile does not invent an Astra override missing
from the bundled worker catalog.

The adapter reuses the candidate's **entire original** controller and worker
contracts. This preserves orchestration-only duties, delegation of all business
work, independent review, exact deterministic naming/normalization/length limits,
bounded worker packets, and the explicit worker execution override. Only the
routing data and its short serialization wrapper differ. The adapter reads its
own isolated profile; it never searches for, initializes, or edits a workspace
`.codex-model-router/routing.json`. The installed candidate hash remains
`1536c1d802842afd5ff289532b4959bc59cbf106becf5004c808190310a8dd97`.

## Byte bound and exact delivery gate

The previous captured CLI messages reported approximately 2,800 original tokens
and were truncated to approximately 10,000 bytes of text. Their routing JSON was
invalid even though both routing delimiters survived. CLI 0.144.1 did not honor
the candidate's larger `additionalContextLimit`.

The benchmark adapter therefore refuses any event whose context **or complete
JSON output envelope** exceeds 8,000 UTF-8 bytes. This is a conservative bound
below the captured spill boundary, not a universal token-to-byte conversion.
Oversized future contracts, models, or profiles fail before output is emitted.
Exact request capture, rather than the byte bound alone, establishes that the
current CLI delivers this profile without truncation.

Observed complete delivery in CLI 0.144.1:

| Event | Context bytes | Routing JSON bytes |
| --- | ---: | ---: |
| SessionStart | 5,724 | 1,044 |
| UserPromptSubmit | 5,666 | 1,044 |
| SubagentStart simulation | 3,203 | 1,044 |

The gate parses the **entire** JSON between the two delimiters with duplicate-key
and non-finite-value rejection, validates it with the normal schema, and compares
it with the isolated profile. It also compares the complete context byte-for-byte
with the expected candidate contract and profile. Missing text, malformed JSON,
extra JSON, altered rules, duplicate delimiters, and spill previews all fail.

## Zero-model worker event simulation

The third offline capture clones the same candidate/profile, takes the registered
SubagentStart command, and adds `--simulate-subagent-start`. The adapter processes
a synthetic SubagentStart event with the original worker contract, then labels
its output for a SessionStart carrier so the host CLI's real context injector can
deliver it. A loopback HTTP sink captures and rejects the outgoing request.
Direct synthetic SubagentStart and carrier-mode output are also checked for
equality by deterministic tests.

No worker is spawned, no model runs, and no successful model response is
returned. This verifies the worker hook's generated output and CLI delivery,
not the native spawning/inheritance path or a model's compliance with the policy.
The receipt explicitly records the simulation, original handler key/hash, and
absence of a spawned worker. Modified or missing simulation evidence blocks
admission just like missing controller context.

## Reproduce and continue

Use fresh state and evidence directories with the commands in [EVALPLUS.md](EVALPLUS.md).
The zero-model gate runs via:

```powershell
py -3.13 evals/scripts/evalplus_hooks.py `
  --campaign-root C:\absolute\fresh-campaign `
  --state-root C:\absolute\fresh-state
```

The successful local evidence is under
`Q:\MyProjects\codex-model-router-benchmarks\compact-router-profile-20260922\verification`.
The receipt binds config, candidate, profile/adapter, CLI/Python executables, and
all controller and worker-simulation captures. A changed runtime or profile needs
fresh evidence. The paid treatment preflight and both formal admission paths
require this complete receipt; older presence-only receipts cannot pass.

A subsequent explicitly authorized paid preflight is now warranted. This repair
performs no paid preflight or formal slot and makes no benchmark-score or savings
claim. Actual worker dispatch and provider support remain live-validation items.

Validation passed: 117 evaluation tests, 52 plugin tests, the manifest and
repository/official plugin validators, both official skill validators, and
`git diff --check`. Tests include corrupted/duplicate JSON, changed contracts,
oversized UTF-8/envelopes, unknown runtime routes, profile isolation/relocation,
and missing or altered worker-simulation evidence.
