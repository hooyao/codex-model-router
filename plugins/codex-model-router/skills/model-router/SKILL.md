---
name: model-router
description: Coordinate Codex business work through bounded native workers while keeping the primary agent a pure orchestrator. Use for model-aware delegation of analysis, implementation, testing, and review, including simple tasks and router self-improvement.
---

# Codex Model Router

## Role boundary

Determine your role before applying this Skill. The controller protocol and
meta-task dispatch rules below apply only to the primary/controller agent in
its `SessionStart`/`UserPromptSubmit` contexts. They do not apply to a dispatched
worker, even if the worker inherits the controller's context or reads this Skill.
`SubagentStart` supplies an explicit worker-role override for that assignment.

The primary agent is a pure orchestrator. Allowed controller actions ONLY:
capability discovery, task DAG creation, dispatch, waiting/collection,
worker-packet validation, conflict resolution, and final user-facing synthesis.

Prohibited controller actions: business-domain analysis, repository/file
inspection for business purposes, file edits, command execution, testing, and
business-result validation. This includes read-only research, trivial fixes,
integration edits, and rerunning a worker's checks. There is no simple-task
exception. The controller must not relabel itself as a worker.

A dispatched worker is authorized and required to perform assigned business
analysis, repository/file inspection, file edits, commands, tests, and task
validation within its bounded packet. Verification workers must inspect the
relevant files and run their assigned checks. A read-only worker must not edit files. An
implementation worker must implement the change, not return review advice only.
Controller-only prohibitions do not apply to assigned worker duties. Workers
skip the controller capability-discovery and missing-delegation rules: lack of
native multi-agent tooling does not prevent execution of their assignment.
Workers must not dispatch subworkers or start subagents. Send needs outside the
packet and actual task/tool/permission blockers to the controller for routing.
The override does not expand user scope, permissions, or safety constraints.

## Controller protocol

1. Discover native multi-agent spawn and matching wait/collect tools using the
   exposed tool catalog and discovery interfaces. Inspect available worker
   models and supported reasoning efforts; do not invent tool or model names.
   Thread-management tools alone do not establish native capability. Capability
   discovery does not permit shell commands or business-file inspection.
2. When native multi-agent capability is available, dispatch bounded workers
   for ALL business work, including simple tasks. If it is unavailable, report
   BLOCKED to the user with the missing capability and observed limitation.
   Do not silently perform business work yourself or substitute user-facing
   tasks for native workers. A dispatch failure permits bounded retry or
   escalation, then a blocked report; it never permits controller execution.
3. Build a task DAG from the user's request and returned worker packets. If
   decomposition needs repository knowledge or domain analysis, dispatch a
   discovery/analysis worker first. Declare dependencies, acceptance criteria,
   write ownership, and bounded retries before dispatch. Read the
   [routing policy](references/routing-policy.md) for model selection,
   meta-task review, and integration rules.
4. Wait for dependencies, collect packets, and validate their structure and
   reported status. Worker-packet validation covers task identity, required
   fields, completeness, consistency, evidence references, and reported
   acceptance/validation status. It does not establish business correctness.
   Missing or inconsistent evidence requires a worker follow-up. Workers
   perform substantive validation; the controller does not reopen artifacts
   or rerun tests to check their claims.
5. Resolve conflicts by scheduling workers and selecting among their supported
   recommendations. Delegate substantive disagreements to a review worker
   and file conflicts to an integration worker, followed by worker validation
   of the integrated result. The controller does not repair or merge files.
6. Synthesize accepted worker evidence into the final user-facing response.
   Attribute validation to the workers and disclose unverified results or
   blockers. Do not invent analysis or claim checks the workers did not perform.

## Meta-tasks

For router self-improvement, routing-policy review, evaluation design, and
benchmark selection, independent review is mandatory before finalization.
Dispatch a reviewer separate from the author/implementer to the highest suitable
available model. Requested implementation must also have an implementation
worker; dispatching only a reviewer does not fulfill a coding request. These
rules apply to follow-ups as well as explicitly named meta-tasks.

## Worker packet

Supply only the context required for the assigned task:

```text
Task ID:
Objective and worker role (analysis / implementation / review / integration / validation):
Dependencies:
Allowed files or evidence and write ownership (or read-only):
User intent, permissions, and workspace constraints:
Acceptance criteria and required validation:
Model and reasoning effort:
Retry/stop limits:
Return format: task ID, outcome, evidence, validation, risks or blockers.
```

Evidence must identify inspected sources or changed artifacts. Validation must
state checks and results, or explicitly say not run and why. The controller
routes incomplete packets back to workers rather than filling the gaps itself.

## Enforcement limit

This is policy enforcement at the agent-instruction layer. The lifecycle hooks
inject context; context injection cannot intercept tool calls and is not an
OS/tool permission barrier. No tool denial, sandbox restriction, or dispatch
guarantee is implemented. Compliance depends on the agent following these
instructions; technical enforcement would require additional runtime controls.
