# Context loading

## Decision

Use the existing Python hook runtime to emit a short skill/config entrypoint.
Keep the workflow in a concise `SKILL.md` and load decision, delegation, model
selection, and dispatch references only when their stage needs them. Reuse
instructions already in context while their inputs remain current.

The official [skills guide](https://developers.openai.com/codex/skills)
describes progressive disclosure: metadata first, the skill body when used,
and supporting resources as needed. The official
[hooks guide](https://developers.openai.com/codex/hooks#large-hook-output)
warns that accumulated hook context can degrade performance. Its
[`SessionStart` contract](https://developers.openai.com/codex/hooks#sessionstart)
delivers `source: compact` before the immediate continuation after root
compaction. The [plugin guide](https://developers.openai.com/plugins/build/plugins)
defines packaging; plugin installation does not make all references mandatory
context. Sources consulted on 2026-10-28.

## Emission lifecycle

- `SessionStart` always emits the short controller entrypoint, including on
  startup, resume, clear, fork, and compact. No matcher excludes recovery.
- `UserPromptSubmit` still reloads and validates workspace configuration. It
  emits nothing when the same session and config path already received the
  same config/instruction revision. A changed config or instruction bundle
  emits a fresh entrypoint; a missing session ID or unavailable cache also
  emits it.
- `SubagentStart` always emits a short worker-role override. It neither reads
  nor initializes controller config and does not change controller emission
  state, including in read-only worker workspaces.
- Executing the hook does not create Python bytecode in the installed plugin.
- Dispatch audit hooks retain their separate opt-in behavior.

The best-effort cache is under the system temporary directory. Filenames hash
the session ID and absolute config path; records contain only a version and
content signature. No prompt, decision, credential, or raw session ID is
stored. State is written atomically only after valid JSON has been flushed to
stdout. Cache failures cause another emission, not a routing failure.
Concurrent events may both emit; deduplication is an optimization, not a
dispatch authorization or proof that the model retained instructions.
The entrypoint exposes the instruction revision as well as the config digest
so an instruction-only update is observable to the controller.

The hook never caches a routing decision. Every request and separately owned
stage still uses the decision resolver; config validation, permission checks,
review obligations, and runtime capability checks remain binding. If required
instructions are absent after a context change, load their referenced files
again. The cache is reset in effect by every `SessionStart`, including compact.
Automatic recovery relies on the host delivering that documented lifecycle
event; a hook cannot detect an unreported context reset.

## Budgets and validation

The complete JSON output has a strict byte cap. The handler's
`additionalContextLimit` is a separate approximate-token threshold, as defined
by OpenAI; it is not a byte limit. Tests check suppression across separate hook
processes, config/instruction invalidation, recovery, missing/corrupt cache,
worker-role separation, invalid config, and bounded output. Report emitted
bytes separately from token usage, billing, and end-to-end performance.

## Reproducible byte measurement

The [retained first probe](../../../evals/context-loading-bytes.json) compares commit
`e75ef8785d960b12e6d1ce27ad4c6b184b8a0fc0` with
`a93ba3af99b9ad58c97f82c4938129b634d5c446` in isolated
temporary plugin directories of equal-length paths. It executes each hook in
a separate process; it makes no model calls. Absolute installation paths affect
the entrypoint size.

| Model-visible context | Before, bytes | After a93ba3a, bytes |
| --- | ---: | ---: |
| SessionStart | 8,274 | 734 |
| Each unchanged UserPromptSubmit | 8,214 | 0 |
| SubagentStart | 5,059 | 565 |
| Skill entrypoint file | 16,452 | 3,464 |
| Model-selection reference file | 17,746 | 3,588 |

The delegation-specific rules are now a separate reference, loaded only after
DELEGATE. Reproduce the hook measurement with a new output file:

```powershell
python evals/scripts/measure_context_loading.py --baseline e75ef8785d960b12e6d1ce27ad4c6b184b8a0fc0 --output <new-context-bytes.json>
```

These are emitted/file byte counts, not measured token savings, quality parity,
cost savings, or a stable speed advantage. Behavioral live validation and the
repository performance protocol remain separate requirements.

## Cold-path refinement

The subsequent live paired campaign found that fewer hook bytes did not
guarantee fewer model calls. The multi-turn direct control regressed in wall
time while the controller read the skill and decision schema before resolving
a simple task. A pure status reply also sometimes triggered another resolver
round. The original measurements and live results remain attached to their
original commit; they are not results for this refinement.

For new business work, the read-only `describe_router.py` program supplies the
validated workspace config, a request template, and conditional reference
paths in one compact response. DIRECT needs no skill/reference read. DELEGATE
loads the coordination skill and relevant references after ownership is known.
Only a status/synthesis reply based on already accepted results may reuse an
existing decision without new business work. A fresh read, edit, or test still
needs its own decision. No resolver predicate, inherited constraint, model
policy, or dispatch validation is relaxed.
