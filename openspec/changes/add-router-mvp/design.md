# Design

## Context

See `proposal.md` for motivation and the delta specs for behavior. The
repository contains only project instructions and OpenSpec planning artifacts,
so the MVP can establish a focused plugin package without adapting an existing
runtime or build system. Codex plugin hooks are opt-in through the normal hook
trust flow and can add developer context, but they cannot independently invoke
native subagent tools.

## Goals / Non-Goals

**Goals:**

- Package a local Codex plugin that injects controller and worker policy through
  supported lifecycle-hook events.
- Keep the injected context short, deterministic, and independent of network or
  live model access.
- Make the package verifiable offline with representative hook events.
- Keep the primary agent responsible for deciding whether to delegate and for
  invoking native subagent tools.

**Non-Goals:**

- Automatically changing the model of an already-running primary thread.
- Directly spawning, waiting for, or cancelling subagents from a hook.
- Persisting routing telemetry, measuring live token cost, or providing a UI.
- Enforcing every routing choice with a `PreToolUse` policy in the MVP.

## Decisions

### Ship a hooks-plus-skill plugin

The plugin will contain a required `.codex-plugin/plugin.json`, a `skills/`
directory, and the default `hooks/hooks.json` lifecycle configuration. The
Skill is the readable, detailed policy and fallback entry point; the hooks make
the concise operational rules available even if the model does not select that
Skill.

Alternative considered: a Skill-only plugin. This was rejected because Skill
selection is model-driven and does not guarantee that each prompt receives the
delegation gate. An MCP server was also rejected for the MVP because native
Codex subagent controls already perform dispatch and an external service would
add installation, credential, and reliability costs.

### Use one standard-library Python hook program

One Python program will read the lifecycle event JSON from standard input,
identify the event name, and emit the supported JSON output shape. The hook
configuration will invoke it for `SessionStart`, `UserPromptSubmit`, and
`SubagentStart`, with a Windows-specific command override and a portable
command for other supported local clients.

The program will produce:

- Controller context for `SessionStart`, including the active-model observation
  and controller responsibilities.
- A concise delegation-gate reminder for `UserPromptSubmit`.
- A bounded-worker contract for `SubagentStart`.

Python's standard library is sufficient for JSON handling and makes the MVP
portable without a package-install step. Alternatives considered were a shell
script, which is fragile across Windows and Unix hosts, and separate scripts
per hook, which would duplicate parsing and validation logic.

### Keep routing policy declarative and tiered

The bundled policy will state a stable four-tier mapping: Luna for clear,
repeatable work; Terra for everyday or read-heavy tool use; Sol for complex,
open-ended work; and Astra for high-risk or sustained-judgment work. It will
instruct the controller to use the lowest supported reasoning effort that can
reliably pass acceptance criteria, escalating after ambiguity, risk, or failed
verification.

The hook output will embed a compact version of the policy so it remains useful
without a file read. The Skill and reference document will contain the longer
explanation, task packet template, dependency rules, and write-conflict policy.
Alternative considered: dynamically querying a pricing or model catalog service.
This was rejected because availability is account-specific and the MVP must run
offline; future versions can add runtime capability discovery while retaining
safe fallbacks.

### Inject guidance, do not silently enforce dispatch

The MVP will inject developer context rather than deny or rewrite native
`spawn_agent` calls. The controller must make the final routing decision,
preserve user intent, and handle cases where a suggested model is unavailable.
The worker hook will prohibit recursive delegation by default but leaves an
explicit parent instruction as an exception.

Alternative considered: a `PreToolUse` hook that rejects noncompliant
`spawn_agent` calls. This is deferred because reliable validation needs a
stable view of the actual tool input schema and user-configured model catalog;
over-enforcement would make a first install unexpectedly brittle.

### Test through deterministic fixtures

An offline validator will parse the plugin manifest and hook configuration,
resolve every referenced local path within the plugin root, and run the hook
program against fixture JSON for the three supported events. It will assert a
matching hook event name and non-empty model-visible developer context. The
validator will use only the Python standard library and return non-zero with a
specific diagnostic for structural or output failures.

Alternative considered: requiring a real Codex session in automated tests. This
was rejected for the MVP because it is account-dependent, expensive, and does
not make a local package smoke test reproducible.

## Risks / Trade-offs

- [Hook output is advisory context, not an execution guarantee] → Keep the
  policy explicit and add offline tests for every injected event; defer strict
  dispatch guarding until real-run observations establish stable inputs.
- [Users must trust plugin hooks before they run] → Document the trust step and
  preserve a Skill fallback for manual use.
- [Static model mapping can become stale or unavailable] → Describe mappings as
  roles, instruct the controller to use available models, and avoid hard
  failures when a preferred model is missing.
- [Repeated context can consume tokens] → Keep per-event context concise and
  place detailed procedures in the Skill/reference files.
- [Parallel write tasks can conflict] → Require the controller to identify file
  ownership and serialize overlapping write scopes in the injected policy.

## Migration Plan

1. Create the plugin package and run its offline validation command.
2. Add the repository marketplace entry and install the local plugin.
3. Review and trust the plugin hooks in Codex, then start a new Terra Medium
   session for manual verification.
4. Disable the plugin or remove its marketplace entry to roll back; the MVP
   does not modify project code or external services.

## Open Questions

- The exact default concurrency limit will be determined after observing the
  first manual runs; it does not alter the routing behavior or MVP task
  breakdown.
