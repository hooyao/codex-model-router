# Proposal

## Why

Codex Skills are selected by the model and may not be invoked for every suitable request. The router MVP needs a reliable way to place delegation policy in the primary agent's context so that a Terra Medium controller consistently evaluates task decomposition and model selection before it starts work.

## What Changes

- Add an installable Codex plugin that bundles routing policy, lifecycle hooks, and supporting documentation.
- Inject concise routing instructions when a root session starts and immediately before every user prompt is processed.
- Inject a bounded worker contract whenever the primary agent starts a subagent, including a structured result format and a prohibition on recursive delegation.
- Provide a deterministic, testable routing policy that maps task characteristics to Luna, Terra, Sol, or Astra and an appropriate reasoning effort.
- Add an MVP validation path that verifies the plugin manifest, hook configuration, hook output, and policy documents without requiring live model calls.

## Capabilities

### New Capabilities

- `controller-routing-context`: Supplies the primary Codex agent with a concise delegation gate, task-routing policy, and coordination responsibilities at session and prompt boundaries.
- `subagent-worker-contract`: Supplies each spawned subagent with scoped execution rules and a structured result contract that keeps work bounded and prevents recursive delegation.
- `router-plugin-validation`: Validates the distributable plugin structure and deterministic hook output so the MVP can be evaluated without a live multi-agent run.

### Modified Capabilities

- None.

## Impact

- Adds a local Codex plugin manifest, bundled Skill, lifecycle hook configuration, hook command scripts, policy references, and offline validation checks.
- Relies on Codex lifecycle hooks and the native subagent workflow; it does not introduce an MCP server, external service, credential, or network dependency.
- Requires users to review and trust the plugin's hook definitions before hooks can run.
