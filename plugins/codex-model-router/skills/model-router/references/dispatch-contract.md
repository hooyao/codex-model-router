# Dispatch preflight contract

Every delegated spawn requires a validated `dispatch-contract-v1` record before
the native tool call. Run the bundled `hooks/dispatch_contract.py` with the JSON
record on standard input. A nonzero exit is a capability blocker. Omitted
selectors are permitted only by the separately evidenced role route below.

The record has these exact top-level fields:

- `schema_version`: `1`;
- `dispatch_id`: stable planned dispatch identifier;
- `purpose`: frozen task-purpose component;
- `canonical_name`: recomputed purpose/model/effort name;
- `packet`: exact `worker_name`, `task_id`, and `native_task_name` values;
- `native_dispatch`: tool name, naming field, native name, and the referenced
  spawn-schema evidence (supported arguments are derived from that source);
- `selection`: `explicit`, `verified_inheritance`, or `verified_role_config`, resolved model, resolved
  reasoning effort, and capability-evidence references; and
- `capability_evidence`: hash-bound runtime observations with an ID, kind,
  source, SHA-256, and capture timestamp.

Capability evidence kinds include `spawn_schema`, `model_catalog`,
`inheritance_contract`, `role_binding`, `role_file`, `role_runtime`, and
`role_calibration`. Explicit selection requires a captured spawn schema
that exposes both `model` and `reasoning_effort`, plus model-catalog evidence.
Verified inheritance requires evidence that the native runtime contract
actually defines inheritance and identifies the resolved inherited model and
effort. Silence, omitted arguments, a later child turn context, or a successful
spawn is not inheritance-contract evidence.

### CLI selector capability

Check the exact CLI binary used by an evaluator. A desktop installation and
the `codex` command on `PATH` can use different versions and catalogs. Current
[official config schema](https://developers.openai.com/codex/config-schema.json)
defines `features.multi_agent_v2.expose_spawn_agent_model_overrides`. A runtime
that supports this option can expose explicit selectors with this process-local
configuration:

```text
-c features.multi_agent_v2={enabled=true,expose_spawn_agent_model_overrides=true}
```

Use `--strict-config` and capture the resulting native spawn schema and model
catalog before constructing an `explicit` contract. The flag alone proves
neither selector exposure nor model availability. Do not copy newer model
catalog entries into an older runtime, infer inheritance, or enable the blocked
role route to work around missing fields. Record the binary hash, version,
configuration, and observed schema for the same launch. A selector-capable
launch still needs a preflight and observable native child lineage.

CLI 0.144.1 and 0.158.0-alpha.2.1 differed in a local calibration on
2026-09-29: the former's v2 spawn lacked selectors; the latter exposed them
with the option above and created an explicitly selected Astra/xhigh child.
This observation is version-specific and does not establish compatibility for
another binary, router-hook activation, or benchmark performance.

Each `source` is a local file path, not a URI-like label or prose claim. Role
binding and role file sources are restricted TOML; other sources are JSON. The
preflight requires the file to exist and contain at most 1 MiB, verifies its
nonzero lowercase SHA-256 over the same bytes it parses, rejects duplicate
JSON keys, and parses the exact formal
schema for its declared kind. A `spawn_schema` declares its tool and supported
arguments; a `model_catalog` declares model IDs with their compatible reasoning
efforts; an `inheritance_contract` declares the exact model/effort pair used
when selectors are omitted. Claimed arguments, nonexistent paths, zero hashes,
kind mismatches, absent models, and impossible model/effort pairs block.

Values containing `unknown`, `unavailable`, `unexposed`, `unresolved`, or
`placeholder` are invalid model/effort inputs. This plugin routes only `low`,
`medium`, `high`, `xhigh`, and `max` efforts; `none` and `ultra` remain unsupported
even if a runtime catalog lists them. Missing selector/catalog evidence
must produce a `runtime-capability-evidence` blocker. Runtime metadata and the
worker's final echo are post-dispatch observations and never retroactively
authorize a spawn.

## Verified CLI role configuration

This is a separate, narrow mode for the audited CLI 0.144.1
`collaborationspawn_agent` schema. The active binding source must contain only
`[agents.default]` with a description and an absolute `config_file` pointing
to the frozen role file. That role file must contain exactly `model` and
`model_reasoning_effort`. The benchmark fixture
`evals/paired_selective/astra-hard-kernel-role.toml` selects Astra/xhigh; its
presence does not prove the CLI loaded it.

The selection's `role` object names the binding, role file, runtime contract,
and calibration evidence refs, plus an absolute CLI binary path and SHA-256.
The runtime contract must specify the allowed CLI version/tool and the
source-backed precedence rule: the role file overrides explicit spawn and
`[agents]` defaults, and an omitted `agent_type` selects `default`. The
calibration record must bind the same CLI binary, binding, role file, omitted native
selectors and `agent_type`, `fork_turns: none`, and observed child model/effort.
It may carry a passed review assertion and a hash-bound review source, but
those fields can be self-authored. `validate_dispatch_structure` checks their
syntax and consistency only. The dispatch-authorizing
`validate_dispatch_contract` and CLI currently return a
`role-runtime-authorization-unverified` blocker for this mode; they never emit
`ready`. An independently verifiable calibration and trusted hook-output
provenance mechanism must be implemented and reviewed before lifting that
block. Synthetic parser tests do not satisfy the runtime gate.

For this mode `native_dispatch.planned_arguments` has `fork_turns: "none"`,
null `model`, `reasoning_effort`, and `agent_type` fields representing omitted
native arguments, plus the exact packet SHA-256 and UTF-8 byte count. Any
planned selector, changed config/role/binary, unsupported
version, missing calibration, or contradictory child selector blocks. This is
not an inheritance contract. Preflight validates the plan; a later native
attempt and child session still require separate observation and reconciliation.

## Optional dispatch audit

`PreToolUse` and `PostToolUse` hooks match exactly `Agent`, `spawn_agent`, or
the CLI 0.144.1 canonical `collaborationspawn_agent` tool name and run the stateless
`hooks/dispatch_audit.py` only when `CODEX_MODEL_ROUTER_DISPATCH_AUDIT=1`.
The hook emits an allowlisted record with session, turn, and tool-use IDs,
native name, explicit selectors, fork setting, packet SHA-256 and byte count,
and (for the post event) an observed child ID if the runtime exposes one.
For the exact `collaborationspawn_agent` PostToolUse event, the bounded
`post_observed_input` field records the SHA-256 and UTF-8 byte length of that
event's runtime-provided `tool_input.message`. It contains no message text and
does not prove which arguments the worker effectively received or executed.
It never writes a file or emits packet text. A preflight contract is a plan;
the hook record is a claimed observation requiring independently retained hook
stdout and reconciliation against the native parent session and turn context,
call and result IDs, native result child identity, and child sessions. A missing
hook event, parent turn ID, native result child ID, attempt ID, or per-turn
selector leaves the audit UNKNOWN.
For an omitted-selector attempt, the hook records null selector values, not an
inferred model. Reconciliation requires the validated role preflight, exact
packet digest, native `sub_agent_activity` child lineage, and every observed
child turn's model and effort. The collector accepts the optional
`--treatment-role-contract` file for structural reconciliation, labels role
authorization `UNVERIFIED`, and still reports hook capture provenance and
semantic packet scope as UNKNOWN without independent evidence. The attempt
receipt names `native_sub_agent_activity` when that event supplies the child ID.
The hook does not establish a technical sandbox, erase inherited history, or
prove the semantic bounds of a worker packet.

## Encrypted exact-packet canary

For a controlled CLI 0.144.1 probe, set `CODEX_MODEL_ROUTER_DISPATCH_AUDIT=1`
and `CODEX_MODEL_ROUTER_PACKET_CANARY=1`, with a bounded run ID in
`CODEX_MODEL_ROUTER_PACKET_CANARY_RUN_ID` and a base64 DER RSA public key in
`CODEX_MODEL_ROUTER_PACKET_CANARY_PUBLIC_KEY_B64`. Keep the matching private
key with the supervisor; never pass it to the hook. The exact
`collaborationspawn_agent` PreToolUse hook encrypts the UTF-8 bytes of
`tool_input.message` with a random AES-256-GCM key and nonce, wraps that key
using RSA-OAEP-SHA256, and authenticates a canonical public header containing
run, phase, session, turn, call ID, message digest, and byte count as AAD.
It emits the bounded envelope only as `systemMessage`, which CLI 0.144.1
records as a hook warning rather than model `additionalContext`; it emits no
plaintext packet and writes no file. PostToolUse remains a compact digest and
byte-count observation. Invalid input or key material produces fixed error
codes without exception text; requesting the canary while dispatch audit is
disabled also fails closed.

This is a canary for evaluating hook visibility, not a benchmark escrow or
proof of effective worker arguments. Decryption and exact-packet review remain
supervisor responsibilities, and the role authorization and semantic-scope
gates stay UNKNOWN until separately established.
