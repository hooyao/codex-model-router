# Codex Model Router

Codex Model Router is a local Codex plugin MVP for model-aware subagent
orchestration. It gives a primary agent concise routing guidance at session and
prompt boundaries, then gives each worker a bounded execution and reporting
contract.

The plugin is designed for a Terra Medium primary agent that coordinates the
workflow. The primary agent decides whether to delegate, decomposes suitable
work into bounded tasks, selects the lowest suitable available worker model,
verifies worker results, and produces the final response.

## What It Does

- Injects a delegation gate on `SessionStart` and `UserPromptSubmit`.
- Injects a bounded worker contract on `SubagentStart`.
- Routes clear and repeatable work toward Luna, everyday and read-heavy work
  toward Terra, complex open-ended work toward Sol, and high-risk or
  sustained-judgment work toward Astra.
- Starts with the lowest suitable reasoning effort and escalates only for
  ambiguity, risk, complexity, or failed verification.
- Keeps the primary agent responsible for dispatch, write-conflict management,
  verification, and final synthesis.

## MVP Boundaries

This MVP adds developer context; it does not directly spawn, interrupt, or
cancel subagents. It does not switch the primary thread's model, enforce
dispatch through `PreToolUse`, connect to an MCP server, call a network service,
or store credentials.

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

## OpenSpec

The completed MVP change is documented under
[`openspec/changes/add-router-mvp`](openspec/changes/add-router-mvp). Run
`openspec validate add-router-mvp --strict` to verify the planning artifacts.
