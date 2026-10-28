---
name: model-router
description: Coordinate delegated Codex stages after an ownership decision, choosing runtime-supported worker models, validating dispatch, and collecting bounded results.
---

# Codex Model Router

## Role and context

A worker follows its bounded packet and SubagentStart override, performs its
own work and validation, and returns results without subagents. Controller-only
rules do not apply; permissions, safety, and user scope remain binding.

For the controller, reuse instructions and configuration already in context.
Read a reference only when its stage needs it and it is absent or outdated.
Reload after compaction or a changed injected config/instruction revision.
A hook entrypoint is a file pointer, not evidence that a reference was read.

## Ownership before business work

Run the advertised `describe_router.py` with the workspace `--config` path
only when decision context is missing or revised. It supplies config and the
request template; reuse them instead of rereading config or
[decision-contract.md](references/decision-contract.md). Send classified JSON
on stdin to the advertised resolver with the same `--config`, record its
validated result and
`ROUTE: DIRECT — <rule/reason>` or
`ROUTE: DELEGATE — <topology/rule/model/effort/reason>`.
Classify the full request; skills do not replace ownership. Invalid config or
resolver results stop routing; do not guess or silently use defaults.
One decision covers the same owner's declared reads, hash-bound receipt,
bounded edit, and self-check. A file, receipt, or tool call is not a stage.
Resolve new business requests or separately owned stages; reassess changed
config, inputs, scope, permissions, risk, or failed validation before acting.
Keep independent units, review owners, and real prerequisite/recovery gates
separate. Status-only replies reuse accepted results. Never claim fresh
validation from memory. DIRECT needs no skill/reference read.

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
