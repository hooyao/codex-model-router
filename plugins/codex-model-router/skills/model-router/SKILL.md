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
Use the absolute resolver/config paths and compact request schema included in
that block. Read the [decision contract](references/decision-contract.md), send
one decision-request-v1 JSON object to the bundled resolver, and use its
validated result before emitting the route line. Resolver invocation is a
routing-only preflight action, not business execution.
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

1. Before the first business action, construct a decision-contract version 1
   request using every field defined in the injected block. Invoke the bundled
   resolver with the injected workspace config path. Record its validated
   ownership, delegate topology,
   verification requirement, effective config mode, matched rule, reasons,
   inherited constraints, and reclassification trigger. Also emit
   `ROUTE: DIRECT — <rule/reason>` or
   `ROUTE: DELEGATE — <topology/rule/model/effort/reason>`.
2. DIRECT is eligible only when every bounded fact is explicitly known: one
   local scope, one bounded known outcome, no network/sync, monitoring,
   recovery, substantive research, independent review, or high risk;
   permissions and safety constraints are confirmed; and write scope and a
   self-check plan are known. A false or unknown required fact disqualifies the
   fast path. DIRECT performs the work and its self-check without waiving any
   scope, permission, safety, or write-ownership obligation.
3. Resolve precedence deterministically: a direct-bound escalation or hard
   DELEGATE/unknown signal wins; one matching example overrides the global
   default; no match uses the default; disagreeing matched modes resolve to
   DELEGATE. Config `direct` is eligibility only, `delegate` is mandatory, and
   `evaluate` applies the structured gate.
4. If DIRECT exceeds an approved bound, stop before the next business action.
   Create a reclassification result with `reclassified_from: DIRECT` and one
   explicit `escalation_trigger`, then route DELEGATE. Supported triggers cover
   scope/outcome expansion, network, monitoring, recovery, research, review,
   dependency or overlap discovery, failed validation, and permission/safety
   changes. Do not silently continue or omit the trigger record.
5. For DELEGATE, choose `PARALLEL` only when multiple bounded tasks,
   independence, absence of dependencies, and disjoint write scopes are all
   explicitly true. Otherwise choose `ISOLATED_SERIAL`; dependencies and
   overlapping or shared writes always serialize. Isolation here means a
   minimal worker packet plus compact receipt/artifact references. Never claim
   parent history was erased or a technical sandbox exists unless the runtime
   actually provides and verifies it.
6. Only for DELEGATE, discover native multi-agent spawn and matching
   wait/collect tools using exposed discovery interfaces. Inspect available
   worker models and supported efforts; do not invent them. Thread-management
   tools alone do not establish native capability. If native capability is
   unavailable, report BLOCKED rather than silently executing delegated work.
   A failed dispatch permits bounded retry/escalation, then a blocked report;
   it never permits controller fallback.
   Before each spawn, read the [dispatch preflight contract](references/dispatch-contract.md),
   build a hash-bound `dispatch-contract-v1` record from the observed spawn
   schema and model catalog (or an explicitly observed inheritance contract),
   and run the bundled `hooks/dispatch_contract.py`. Missing selectors,
   catalogs, or inheritance proof are recorded capability blockers. Later
   runtime metadata cannot retroactively authorize the dispatch.
7. Build a task DAG from the user's request and returned worker packets. If
   decomposition needs repository knowledge or domain analysis, dispatch a
   discovery/analysis worker first. Declare dependencies, acceptance criteria,
   write ownership, and bounded retries before dispatch. Read the
   [routing policy](references/routing-policy.md) for model selection,
   meta-task review, and integration rules.
8. Apply the configured maximum depth, concurrency, and retry count as ceilings,
   lowering them to actual limits exposed by the runtime. Never invent worker
   slots, isolation, or dispatch features. Preserve
   permissions, safety constraints, exclusive write ownership, and verification
   requirements in every packet. Wait for dependencies, collect compact
   receipts, and validate their structure and reported status. Worker-packet
   validation covers task identity, required
   fields, completeness, consistency, evidence references, and reported
   acceptance/validation status. It does not establish business correctness.
   Missing or inconsistent evidence requires a worker follow-up. Workers
   perform substantive validation; the controller does not reopen artifacts
   or rerun tests to check their claims.
9. Resolve conflicts by scheduling workers and selecting among their supported
   recommendations. Delegate substantive disagreements to a review worker
   and file conflicts to an integration worker, followed by worker validation
   of the integrated result. The controller does not repair or merge files.
10. Synthesize accepted worker evidence into the final user-facing response.
   Attribute validation to the workers and disclose unverified results or
   blockers. Do not invent analysis or claim checks the workers did not perform.

## Meta-tasks

For router self-improvement, routing-policy review, evaluation design, and
benchmark selection, independent review is mandatory before finalization.
That independent-validation signal makes these tasks DELEGATE routes and sets
the decision's verification requirement to `INDEPENDENT_REVIEW`.
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
Resolved topology position and context-isolation claim:
Acceptance criteria and required validation:
Model and reasoning effort:
Depth, concurrency, retry, and stop limits:
Return format: task ID, outcome, evidence, validation, risks or blockers.
```

Packets contain only required context, but this does not claim inherited parent
history was erased or that a sandbox exists. Evidence must identify inspected
sources or changed artifacts. Validation must
state checks and results, or explicitly say not run and why. The controller
routes incomplete packets back to workers rather than filling the gaps itself.
Results are compact receipts with artifact references, not raw working logs.

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
inject context; context injection cannot erase existing history, intercept tool
calls, create a sandbox, or act as an OS/tool permission barrier. No tool denial,
sandbox restriction, or dispatch guarantee is implemented. Compliance depends
on the agent following these instructions; technical enforcement would require
additional runtime controls.
