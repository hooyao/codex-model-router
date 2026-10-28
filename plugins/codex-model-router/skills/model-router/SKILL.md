---
name: model-router
description: Decide direct or delegated ownership for Codex work, then coordinate bounded native workers using runtime-supported models and reasoning efforts.
---

# Codex Model Router

## Role and context

A dispatched worker follows its bounded packet and SubagentStart override,
performs the assigned analysis, edits, commands, tests, and validation, and
returns its result without starting subagents. Controller-only rules below do
not apply to worker duties. Respect higher-priority instructions and user scope.

For the controller, reuse instructions and configuration already in context.
Read a reference only when its stage needs it and it is absent or outdated.
Reload after compaction or a changed injected config/instruction revision.
A hook entrypoint is a file pointer, not evidence that a reference was read.

## Ownership on every request

Before business work, read the current workspace config identified by the
ROUTING_CONFIG_BEGIN block if its digest is new or its contents are unavailable.
Use [decision-contract.md](references/decision-contract.md) to submit the whole
request to the injected decision resolver. Record the validated result and
`ROUTE: DIRECT — <rule/reason>` or
`ROUTE: DELEGATE — <topology/rule/model/effort/reason>`.
Classify the full request semantically; skills describe how to work and do not
replace ownership decisions. Invalid config or an invalid resolver result stops
routing; do not reconstruct it or silently use defaults.

DIRECT requires all bounded facts, permissions, scope, and a self-check. A false
or unknown direct criterion delegates. If bounds expand, stop before further
business work and resolve a recorded reclassification. A controller-owned easy
stage within a delegated request requires its own independent DIRECT decision;
retain inherited risk, permissions, and the whole-request review requirement.
Never duplicate worker-owned analysis, edits, or tests in the controller.

Router self-improvement, routing-policy review, evaluation design, and benchmark
selection require an independent reviewer separate from the author/implementer,
including semantic follow-ups. Requested implementation also needs an
implementation worker. Classify that review requirement before resolution.

## Load details at their point of use

- For a DELEGATE result, use [delegation.md](references/delegation.md) for the
  DAG, topology, bounded packet, names, collection, and integration rules.
- To choose a worker, use [routing-policy.md](references/routing-policy.md)
  alongside the current config and runtime model/effort catalog.
- Before native dispatch, use [dispatch-contract.md](references/dispatch-contract.md)
  and run the dispatch validator for every spawn. Reuse an already loaded
  contract and valid evidence while their schema, catalog, and hashes remain
  current. Missing native capability or proof is a blocker, not permission for
  controller fallback or user-facing threads as workers.
- The initialize-router skill is a narrow exception for explicitly requested
  plugin initialization/preflight; it does not authorize business work.

The controller coordinates and synthesizes accepted evidence, self-checks its
own stages, and assigns worker-stage validation to workers. This is
instruction-layer policy, not an OS/tool permission barrier or proof of erased
history, sandboxing, or runtime compliance.
