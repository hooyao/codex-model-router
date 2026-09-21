# Codex Model Router

Codex Model Router is a local Codex plugin that instructs the primary agent to
act as a pure orchestrator. It does not spawn agents itself; the primary agent
discovers capabilities, creates task DAGs, dispatches and waits for workers,
validates worker packets, coordinates conflicts, and synthesizes the final
response. Workers own all business analysis, repository inspection, file edits,
commands, tests, integration, and business-result validation.

## Controller/Worker Contract

Every business task, including a trivial edit or read-only question, must go
to a bounded native worker. If native spawn or wait/collect is unavailable,
the controller reports BLOCKED with the observed limitation. It must not fall
back to doing the work itself or create user-facing tasks as substitute workers.
Discovery uses exposed tool metadata; it does not authorize controller shell
commands or business-file inspection. Repository research needed to build a
plan is also assigned to a worker.

Controller packet validation checks identity, fields, completeness, consistency,
evidence references, and reported acceptance/validation status. It does not
verify business correctness by opening artifacts or rerunning tests. Missing
evidence and substantive disagreements go back to workers. Integration workers
resolve file conflicts, and workers validate the integrated result.

Every dispatch also receives a deterministic user-visible name in the form
`<purpose>-<model>-<effort>`. The controller normalizes and validates the name
from the frozen task purpose and resolved routing inputs, passes it through a
native `name` field when available, and writes the exact same value as both
`Worker name` and `Task ID` in every worker packet. The worker echoes both
fields in its result. Invalid, oversized, or duplicate planned names are
rejected. If native dispatch has no naming field or rejects the value, the
packet Task ID and result remain the portable fallback; the native worker-card
title may still be platform-generated.

Router self-improvement, routing-policy review, evaluation design, and benchmark
selection require an independent reviewer separate from the author/implementer,
using the highest suitable available model. An implementation request must also
have an implementation worker; review advice alone does not complete it.

`SessionStart` and every `UserPromptSubmit` repeat the full controller contract,
explicitly scoped to the primary agent, including the meta-task rule even when
a follow-up lacks router keywords. Inherited controller context does not apply
to dispatched workers.

`SubagentStart` emits an explicit worker-role override: the worker is authorized
and required to perform assigned analysis, repository inspection, edits, commands,
tests, and task validation within its bounded packet. Verification workers must
inspect the relevant files and run their assigned checks. Workers do not apply
the controller's capability-discovery or missing-delegation rules and must not
dispatch subworkers. Lack of native multi-agent tools is not a reason for a
worker to refuse its assignment; actual task/tool/permission blockers must still
be reported. User scope, read-only assignments, and permissions remain binding.
The primary agent must not relabel itself as a worker to avoid delegation.

## Local Installation

This repository includes a local marketplace entry at
`../../.agents/plugins/marketplace.json`. From the repository root, add the
marketplace to Codex if it is not already available:

```powershell
codex plugin marketplace add .
```

Restart the Codex desktop app, select the local marketplace, and install
**Codex Model Router**. Codex installs local plugins from the marketplace entry
at `./plugins/codex-model-router`.

## Trust Hooks

Plugin hooks are not trusted automatically. After enabling the plugin, start a
new Codex session and use `/hooks` to inspect and trust the three lifecycle
handlers. The hooks add developer context for `SessionStart`,
`UserPromptSubmit`, and `SubagentStart` only. Every handler invokes the bundled
`hooks/router_hook.py` program.

The non-Windows hook command requires `python3`. The Windows override requires
the Python launcher command `py -3`. Install Python for the current user if
neither command is available.

## Offline Verification

The package uses only the Python standard library. From this plugin directory,
run:

```powershell
python scripts/validate_plugin.py
python -m unittest discover -s tests -v
```

The validation command checks package structure and representative lifecycle
outputs without a network connection or a live Codex session. Regression tests
check required/forbidden contract language, simple-task and meta-task routing
instructions, deterministic worker naming, worker responsibilities, and
configured context size. These are instruction-contract checks, not evidence
of live-model compliance or savings.

## MVP Boundaries

- No MCP server, credential, network call, telemetry store, or user interface.
- No automatic primary-model change.
- No direct subagent dispatch, cancellation, or strict `PreToolUse` enforcement.

This is policy enforcement at the agent-instruction layer. Context injection
cannot technically intercept tool calls and is not an OS/tool permission
barrier. This plugin registers only the three context hooks above; it does not
install tool-denial hooks, alter permissions, or guarantee model compliance.
Codex supports separate tool hooks, but their coverage has exceptions and they
are not implemented here. See the official [hook documentation](https://learn.chatgpt.com/docs/hooks#tool-coverage).

Source edits do not update an already installed plugin or an active task's
loaded instructions. Reinstall the updated package through its configured local
marketplace, review any changed hook definitions, and use a new task to test it.
