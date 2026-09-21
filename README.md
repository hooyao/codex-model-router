# Codex Model Router

Codex Model Router is a local Codex plugin MVP for model-aware subagent
orchestration. It gives a primary agent concise routing guidance at session and
prompt boundaries, then gives each worker a bounded execution and reporting
contract.

The plugin is designed for a Terra Medium primary agent that coordinates the
workflow as a pure orchestrator. It discovers capabilities, creates task DAGs,
dispatches all business work to bounded native workers, selects suitable
available worker models, collects and validates worker packets, coordinates
conflicts, and synthesizes the final response. Workers perform business
analysis, repository inspection, edits, commands, tests, and result validation.

## What It Does

- Repeats a strict pure-orchestrator contract on `SessionStart` and
  `UserPromptSubmit`, including for simple tasks.
- Injects a bounded worker contract on `SubagentStart`.
- Gives every dispatch a deterministic user-visible
  `<purpose>-<model>-<effort>` name, with a packet/result fallback when native
  dispatch cannot name the worker card.
- Routes clear and repeatable work toward Luna, everyday and read-heavy work
  toward Terra, complex open-ended work toward Sol, and high-risk or
  sustained-judgment work toward Astra.
- Starts with the lowest suitable reasoning effort and escalates only for
  ambiguity, risk, complexity, or failed verification.
- Limits controller validation to worker-packet completeness and consistency;
  workers own substantive review, testing, and integration.
- Requires an independent high-capability reviewer for router meta-tasks and
  an implementation worker for requested changes.
- Reports a blocked limitation when native multi-agent capability is unavailable;
  it does not permit the controller to execute the business task as a fallback.

## MVP Boundaries

This MVP adds developer context; it does not directly spawn, interrupt, or
cancel subagents. It does not switch the primary thread's model, enforce
dispatch through `PreToolUse`, connect to an MCP server, call a network service,
or store credentials.

The pure-orchestrator contract is policy enforcement at the agent-instruction
layer. Context injection cannot intercept tool calls and is not an OS/tool
permission barrier. Offline tests check the emitted policy, not live compliance.
When a native dispatch API has no supported name field, the canonical worker
name remains the worker packet Task ID and appears in the result, but the
platform-generated worker-card title cannot be changed by this plugin.

## Repository Layout

```text
plugins/codex-model-router/  # Installable Codex plugin
openspec/                    # Proposal, specifications, design, and tasks
.agents/plugins/             # Repository-local marketplace entry
```

The generated `.agents/skills/` directory is intentionally local-only and is
ignored by Git. The OpenSpec change records are versioned.

## Local Installation

From the repository root, add the local marketplace if needed:

```powershell
codex plugin marketplace add .
```

Restart the Codex desktop app, install **Codex Model Router** from the local
marketplace, then start a new session and use `/hooks` to review and trust the
plugin's lifecycle hooks. See the plugin-specific
[README](plugins/codex-model-router/README.md) for details.

## Validation

The plugin uses only the Python standard library.

```powershell
python plugins/codex-model-router/scripts/validate_plugin.py
python -m unittest discover -s plugins/codex-model-router/tests -v
```

The first command validates package structure and representative hook outputs
without requiring a network connection or a live Codex session.

## Evaluation

Task-level evaluation lives under [`evals`](evals/README.md). It compares
paired baseline and router runs for quality, provider-measured cost, latency,
and operational safety. Validate the case matrix with:

```powershell
python evals/scripts/evaluate.py validate --cases evals/cases.json
```

The evaluation does not treat missing or provider-incompatible cost data as a
saving. See the evaluation README for the live-run record format and the
`ccusage` adapter.

## OpenSpec

The completed MVP change is documented under
[`openspec/changes/add-router-mvp`](openspec/changes/add-router-mvp). Run
`openspec validate add-router-mvp --strict` to verify the planning artifacts.
