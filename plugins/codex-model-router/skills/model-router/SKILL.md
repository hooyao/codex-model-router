---
name: model-router
description: Decide whether the primary agent should execute a bounded local task directly or delegate nontrivial work, then route delegated analysis, implementation, testing, and review to suitable native workers.
---

# Codex Model Router

## Role boundary

Determine your role before applying this Skill. The controller protocol and
meta-task dispatch rules below apply only to the primary/controller agent in
its `SessionStart`/`UserPromptSubmit` contexts. They do not apply to a dispatched
worker, even if the worker inherits the controller's context or reads this Skill.
`SubagentStart` supplies an explicit worker-role override for that assignment.

The primary agent decides execution ownership before its first business action.
It may execute genuinely simple work directly only while every direct criterion
below remains true. For delegated work it acts as the orchestrator: capability
discovery, task DAG creation, dispatch, waiting/collection, packet validation,
conflict coordination, and final synthesis.

The separate `initialize-router` Skill is plugin administration rather than
business work. If the user explicitly requests router initialization or
preflight for the current project, the controller may run that Skill's bundled
init program exactly as directed. This narrow exception does not allow direct
repository work, routing-policy edits, or any other business execution.

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

## Workspace routing config

Each `SessionStart`, `UserPromptSubmit`, and `SubagentStart` context contains a
delimited `ROUTING_CONFIG_BEGIN`/`ROUTING_CONFIG_END` block loaded from the
validated workspace `.codex-model-router/routing.json` on that invocation.
Treat that JSON as the source of execution-policy inputs and routing examples.
Decide execution ownership first. Only for a delegated route, resolve advisory
model classes against the runtime model catalog and supported efforts, then
choose the lowest capable available option. Do not treat an example as proof
that a model or effort is currently available. Static config and keyword
matching cannot completely classify arbitrary natural-language requests; apply
the declared criteria to the whole request.

The config is versioned and user-editable. Discovery prefers an existing file
at the event `cwd` or any parent, then initializes at the nearest `.git` ancestor
(file or directory), then at the event `cwd`. Initialization uses the bundled
template only when the file is absent and never overwrites an existing file.
Malformed, schema-invalid, oversized, unreadable, or uncreatable config is a
hard hook error; do not reconstruct examples from this Skill or silently fall
back to defaults.

## Controller protocol

1. Before the first business action, emit exactly one explicit decision:
   `ROUTE: DIRECT — <rule/reason>` or
   `ROUTE: DELEGATE — <rule/model/effort/reason>`. Skills define HOW work is
   performed, not WHO performs it; selecting this Skill does not decide the
   route or bypass the gate.
2. Select DIRECT only when all conditions hold: one local scope, one bounded
   known outcome, no network/synchronization, no long-running work/monitoring,
   no failure/recovery workflow, no substantive research/investigation, and no
   independent review/validation. The controller may then inspect, edit, run
   commands, and perform narrow validation within that scope.
3. Select DELEGATE when any condition exists: multiple repositories, systems,
   or sources; a named multi-step runbook; network access/synchronization;
   long-running work/monitoring; failure/recovery; substantive investigation;
   or independent review/validation. A delegate signal overrides direct
   eligibility. Per-route `direct` means eligible subject to every direct
   condition, `delegate` is mandatory, and `evaluate` applies this semantic
   gate.
   Resolve config precedence in this order: hard DELEGATE signals first; one
   matched route's `execution_mode` overrides global
   `execution_policy.default_mode`; no route match uses the global default; and
   disagreeing matched routes fail closed to DELEGATE. Neither global nor
   per-route `direct` waives a direct condition.
4. If a direct task reveals a delegate signal or stops satisfying every direct
   condition, emit the DELEGATE line and reroute before the next business
   action. Re-evaluate after every direct tool result. A failed validation, a
   tool result naming another repository/system/source, or a recovery
   instruction is a hard barrier: the only next steps are the DELEGATE line,
   capability/model resolution, and dispatch. Do not inspect the newly revealed
   scope, diagnose further, or perform the recovery directly.
5. Only for DELEGATE, discover native multi-agent spawn and matching
   wait/collect tools using exposed discovery interfaces. Inspect available
   worker models and supported efforts; do not invent them. Thread-management
   tools alone do not establish native capability. If native capability is
   unavailable, report BLOCKED rather than silently executing delegated work.
   A failed dispatch permits bounded retry/escalation, then a blocked report;
   it never permits controller fallback.
6. Build a task DAG from the user's request and returned worker packets. If
   decomposition needs repository knowledge or domain analysis, dispatch a
   discovery/analysis worker first. Declare dependencies, acceptance criteria,
   write ownership, and bounded retries before dispatch. Read the
   [routing policy](references/routing-policy.md) for model selection,
   meta-task review, and integration rules.
7. Wait for dependencies, collect packets, and validate their structure and
   reported status. Worker-packet validation covers task identity, required
   fields, completeness, consistency, evidence references, and reported
   acceptance/validation status. It does not establish business correctness.
   Missing or inconsistent evidence requires a worker follow-up. Workers
   perform substantive validation; the controller does not reopen artifacts
   or rerun tests to check their claims.
8. Resolve conflicts by scheduling workers and selecting among their supported
   recommendations. Delegate substantive disagreements to a review worker
   and file conflicts to an integration worker, followed by worker validation
   of the integrated result. The controller does not repair or merge files.
9. Synthesize accepted worker evidence into the final user-facing response.
   Attribute validation to the workers and disclose unverified results or
   blockers. Do not invent analysis or claim checks the workers did not perform.

## Meta-tasks

For router self-improvement, routing-policy review, evaluation design, and
benchmark selection, independent review is mandatory before finalization.
That independent-validation signal makes these tasks DELEGATE routes.
Dispatch a reviewer separate from the author/implementer to the highest suitable
available model. Requested implementation must also have an implementation
worker; dispatching only a reviewer does not fulfill a coding request. These
rules apply to follow-ups as well as explicitly named meta-tasks.

## Worker packet

Supply only the context required for the assigned task:

```text
Worker name: <canonical purpose-model-effort name>
Task ID: the identical canonical purpose-model-effort name.
Native task name: the actual transport value: canonical for a hyphen-capable `name`, underscore-adapted for underscore-only `task_name`, or `unavailable`.
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

## Deterministic worker names

Before every dispatch, record and freeze a short English purpose label in the
task DAG. Construct the user-visible worker name as
`<purpose>-<model>-<effort>` from that label, the exact resolved native model
identifier, and the exact resolved reasoning effort.

Normalize each component in this order:

1. Apply Unicode NFKD normalization.
2. Discard non-ASCII code points and lowercase the result.
3. Replace each maximal run outside `[a-z0-9]` with one hyphen.
4. Remove leading and trailing hyphens.

Reject an empty normalized purpose, model, or effort; all three are invalid
routing inputs and MUST stop dispatch until corrected. Normalized purpose,
model, and effort are limited to 48, 48, and 24 characters, and the complete
name is limited to 128 characters. Join the three normalized components with
single hyphens. For example, `Implement / Naming`,
`GPT-5.6 Sol`, and `High` become
`implement-naming-gpt-5-6-sol-high`.

Validate before dispatch by recomputing from the recorded inputs, requiring
exact equality, matching `^[a-z0-9]+(?:-[a-z0-9]+)*$`, enforcing the limits,
and checking uniqueness across the planned DAG. Do not add generic fallbacks,
random values, timestamps, retry counters, or suffixes. A retry with unchanged
inputs keeps the name; a model or effort escalation produces a recomputed name.

Use the native dispatch `name` field only when it is exposed and accepts the
canonical value. When the native schema instead exposes an underscore-only
`task_name`, deterministically replace every canonical hyphen with one
underscore, require `^[a-z0-9]+(?:_[a-z0-9]+)+$`, preserve the 128-character
limit, verify uniqueness after adaptation, and pass that adapted value as
`task_name`. Do not try the rejected hyphenated canonical value in an
underscore-only field.

Regardless of native support, put identical
`Worker name: <canonical-name>` and `Task ID: <canonical-name>` lines at the
start of every packet, add `Native task name: <actual-transport-value>`, and
require the worker to echo the two canonical fields in the final result. The
native transport name may differ and is not the canonical Task ID. If the API
lacks both naming fields, omit the unsupported argument, record `unavailable`,
and use the packet Task ID and result echo as the user-visible fallback. The
plugin cannot force the title of a native worker card in that case.

## Enforcement limit

This is policy enforcement at the agent-instruction layer. The lifecycle hooks
inject context; context injection cannot intercept tool calls and is not an
OS/tool permission barrier. No tool denial, sandbox restriction, or dispatch
guarantee is implemented. Compliance depends on the agent following these
instructions; technical enforcement would require additional runtime controls.
