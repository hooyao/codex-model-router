# Invocation-scoped project trust receipts

CLI 0.144.1 writes a project trust table when it starts in a fresh task workspace.
The previous paid write preflight passed both marker checks and the native worker
gates, but revalidation correctly stopped on the changed seed-home config hash.
No formal slots ran. The only new setting was the exact task workspace's
`projects.<path>.trust_level = "trusted"`; CLI also converted CRLF to LF.
The [official config reference](https://developers.openai.com/codex/config-reference/)
documents this setting as project/worktree trust, not a filesystem permission.

## Exact allowance

Before each CLI `exec`, the harness records `<name>.config-before.json` with:

- canonical invocation home and task workspace;
- the exact config SHA-256 and byte count;
- the LF-normalized config SHA-256 and byte count;
- the one expected project key (Windows-normalized case on Windows).

The receipt contains no config values or credentials. The normal outcome is
either an unchanged byte hash or this exact append to the LF-normalized original:

```toml
[projects.'<exact canonical task workspace>']
trust_level = "trusted"
```

Only literal and JSON-compatible basic-string quoting of that identical key are
accepted. There is no general TOML reserialization allowance, project-table
filter, path-prefix match, wildcard, parent-directory trust, or global project
exception. Existing config text remains hash-bound. Added fields, duplicate or
other project tables, changed model/effort/approval/permission/hook/plugin
settings, and even unrelated comments fail. Line-ending conversion without the
expected trust append also fails. A future CLI serialization change requires
new evidence and an explicit harness update rather than silently widening this
allowance.

`<name>.config-after.json` binds the before receipt and records the observed
mutation and resulting hash. Later checks recompute against the current file;
changing the saved receipt or the config after capture cannot pass admission.
The original before hash must match the provisioned arm's immutable config hash.
Invocation package and benchmark-profile tree hashes are checked independently.

## Immutable seed homes

Paid preflight now clones each arm into `treatment-preflight/homes/<arm>`, just as
formal slots use fresh `slot-homes/<run-id>`. The seed homes retain their exact
original hashes. Trust for one task is neither copied to another task nor
imported from the host. This applies to soft-cap and legacy guarded admission.

The named permission profile, task-only editor, server-scoped MCP approvals,
disabled shell/network tools, exact Astra/xhigh requirement, candidate plugin,
and compact router contracts are unchanged. A trusted project does not expand
the editor root or enable generated-code execution on the host.

Offline hook, worker-event simulation, and scripted MCP fixtures all record and
revalidate config receipts. The paid receipt binds both arms' before/after
proofs, and formal admission requires them. Old paid receipts are not rewritten
or grandfathered in. The earlier infrastructure attempts stay excluded from
quality scores while all their costs remain historical.

## Zero-model validation

The real CLI still targets only a rejecting or scripted loopback endpoint with
no model implementation or forwarding. The direct/code-mode fixtures prove
inside-root writes and outside-root refusals while observing the actual trust
append. Unit tests simulate successful paid-report artifacts without calling
models and reject config, scope, receipt, candidate, and profile tampering.

Fresh evidence is stored under
`Q:\MyProjects\codex-model-router-benchmarks\project-trust-repair-20260922\release-verification`.
The main receipt is `campaign/hook-delivery.json`; its referenced
`hook-delivery/*.config-{before,after}.json` and
`mcp-cli-proof/{direct,code-mode}/cli.config-{before,after}.json` retain the raw
config-integrity evidence. Reproduction uses the same documented `prepare-homes`
and offline `evalplus_hooks.py` commands as [the campaign guide](EVALPLUS.md),
with a new output directory and credential-free offline transport.

Validation passed: 163 evaluation tests (including all four real-CLI offline
tests), 52 plugin tests, case/manifest validators, repository and official plugin
validators, both official skill validators, and `git diff --check`. Saved receipt
revalidation confirmed `task-project-trust-added` in both arms and the worker
simulation, while both seed configuration hashes remained unchanged.

After all offline gates and validators pass, a fresh explicitly authorized paid
write preflight is warranted. No paid preflight or formal slot is part of this
repair, and the previous historical estimate remains USD 3.092479.
