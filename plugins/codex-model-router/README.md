# Codex Model Router

Codex Model Router is a local Codex plugin that adds concise lifecycle-hook
context for task decomposition and model-aware subagent routing. It does not
spawn agents itself; the primary Codex agent remains responsible for dispatch,
verification, and final synthesis.

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
outputs without a network connection or a live Codex session.

## MVP Boundaries

- No MCP server, credential, network call, telemetry store, or user interface.
- No automatic primary-model change.
- No direct subagent dispatch, cancellation, or strict `PreToolUse` enforcement.
