# Routing Policy

## Role scope

The discovery, dispatch, escalation, and meta-task routing rules in this document
are for the primary/controller agent only. A dispatched worker follows its
`SubagentStart` worker-role override and bounded packet, even when it inherits
controller context. Workers are authorized and required to perform assigned
analysis, repository inspection, edits, commands, tests, and task validation.
Verification workers execute their assigned checks directly. Worker execution
does not require native multi-agent tools; workers must not dispatch subworkers
or start subagents. Report actual task/tool/permission blockers to the controller.

## Execution ownership gate

Before its first business action, the controller records one explicit line:
`ROUTE: DIRECT — <rule/reason>` or
`ROUTE: DELEGATE — <rule/model/effort/reason>`. Skills define HOW work is
performed, not WHO performs it, so loading or selecting a Skill cannot replace
this decision. Static config and keyword matching cannot fully classify an
arbitrary natural-language request; the controller applies the complete
request to the policy signals.

DIRECT is eligible only when every condition is true:

- one local scope;
- one bounded, known outcome;
- no network access or synchronization;
- no long-running work or monitoring;
- no failure or recovery workflow;
- no substantive research or investigation; and
- no independent review or validation.

DELEGATE is required when any signal exists: multiple repositories, systems,
or sources; a named multi-step runbook; network access or synchronization;
long-running work or monitoring; failure handling or recovery; substantive
research or investigation; or independent review or validation. Delegate
signals override direct eligibility. A config example's `execution_mode` is
interpreted as follows: `direct` marks eligibility but does not waive any
criterion, `delegate` mandates delegation, and `evaluate` requires this semantic
gate.

Precedence is deterministic. Hard DELEGATE signals win first. With exactly one
matching config route, its `execution_mode` overrides the global
`execution_policy.default_mode`; with no route match, use the global default.
If multiple matching routes disagree, fail closed to DELEGATE. A `direct`
result from either level is only eligibility and never waives a direct
criterion.

If direct work reveals a delegate signal or otherwise ceases to meet every
direct criterion, the controller emits the DELEGATE line and must reroute before
the next business action. It does not continue directly through the escalation.
Re-evaluate after every direct tool result. Failed validation, a tool result
naming another repository/system/source, or a recovery instruction is a hard
barrier: before any inspection or repair, emit DELEGATE, resolve capability and
model, and dispatch. The controller does not diagnose the expanded scope or
perform recovery itself.

Only after choosing DELEGATE does the controller discover native spawn,
wait/collect, and supported model/effort options and apply model routing. If
native multi-agent capability is unavailable, report BLOCKED with the observed
missing capability. Do not silently execute delegated work or create
user-facing threads to simulate workers. If dispatch fails, use the declared
retry/escalation limit and then report the blocker; controller execution is
never the fallback. Model preferences below do not establish availability.

## Meta-task routing

Router self-improvement, routing-policy review, evaluation design, benchmark
selection, and auditing routing decisions require an independent review
worker separate from the author/implementer before finalization. Use the
highest suitable available model for that review, such as Astra/high when
supported. Resolve the actual model and effort from the runtime catalog.
The independent-review signal makes these DELEGATE routes.

Assign requested business analysis and implementation to workers as well.
A review-only worker does not fulfill an implementation request. The controller
accepts packets and synthesizes their evidence; it does not design the business
solution, edit files, or independently validate business results. Interpret
meta-tasks semantically, including follow-ups without explicit router keywords.

## Worker roles

| Task profile | Preferred role | Reasoning effort |
| --- | --- | --- |
| Clear, repeatable work, including small edits | Luna | Low, then Medium if needed |
| Read-heavy discovery, everyday implementation, routine validation | Terra | Medium; High for notable edge cases |
| Complex implementation, integration, or open-ended analysis | Sol | Medium or High |
| Architecture, security, critical decisions, independent meta-task review | Astra | High or the lowest suitable supported setting |

Use the lowest capable available model and effort that can meet the task's
acceptance criteria, subject to the meta-task review rule. Escalate when
worker-reported ambiguity, risk, or failed validation justifies it. If no
suitable available worker can complete a required task, report a blocker.

## Config-driven examples

Use the validated JSON inside the injected
`ROUTING_CONFIG_BEGIN`/`ROUTING_CONFIG_END` block as the canonical source of
execution-policy inputs, task examples, preferred model classes, efforts, and
rationales. Execution ownership is decided before model selection. Resolve a
model preference against the current runtime capability catalog only for a
DELEGATE route. The stable delegated fallback when no example matches is
capability-based: Luna for clear repeatable work,
Terra for everyday work, Sol for complex or open-ended work, and Astra for work
requiring the strongest sustained judgment. Choose the lowest sufficient
supported effort, using medium when more planning is needed and high or xhigh
for hard multi-step work, multiple sources, risk, or consequential tradeoffs.

The default examples are grounded in the official
[Codex model guidance](https://learn.chatgpt.com/docs/models) and
[OpenAI model catalog](https://developers.openai.com/api/docs/models), consulted
on 2026-09-21. The workspace file is editable and can replace those examples;
hard-coded prose in this policy must not override valid workspace values.

Every hook reloads and validates the workspace file. Existing config is found
upward before choosing the nearest `.git` ancestor or the event `cwd` for
exclusive first-time initialization. Invalid, malformed, oversized, unreadable,
or uncreatable config stops routing context generation with a clear error. Do
not truncate it or silently use bundled defaults.

## Dependencies, conflicts, and validation

Use stable task IDs and a directed acyclic graph. Wait for dependencies before
dispatch. Supply the worker packet defined in the Skill, including scope,
acceptance criteria, permissions, and retry/stop limits. Parallelize independent
work only; serialize overlapping write scopes and shared mutable state.

Worker-packet validation checks identity, completeness, consistency, evidence
references, and reported status. It must not turn into repository inspection,
command execution, testing, or business-result validation by the controller.

Workers own source inspection, business decisions and analysis, file edits,
commands, tests, and substantive validation within their assigned scope.
Resolve conflicting business recommendations through a review worker; assign
file conflicts and merging to an integration worker. Require worker validation
of the integrated artifact before the controller reports it as validated.

Retain successful packets across partial failures. Request bounded correction
or escalation for missing evidence, failed checks, and incomplete work. Report
remaining blockers truthfully. Workers must not dispatch subworkers; route any
additional tasks through the controller.

## Deterministic worker naming

Every dispatch has a canonical user-visible name in the form
`<purpose>-<model>-<effort>`. Freeze a short English purpose label in the DAG,
then use that label, the exact resolved native model identifier, and the exact
resolved effort as the three source components.

For each component, apply Unicode NFKD, discard non-ASCII code points,
lowercase, replace every maximal run outside `[a-z0-9]` with one hyphen, and
trim leading and trailing hyphens. Every normalized component must be non-empty;
purpose, model, and effort are limited to 48, 48, and 24 characters, and the
whole name is limited to 128 characters. Join the components with single
hyphens. Before dispatch, recompute the name, require exact equality, require a
match for `^[a-z0-9]+(?:-[a-z0-9]+)*$`, and check uniqueness across the DAG.
Retries with unchanged inputs retain the name. Never add generic fallbacks,
random values, timestamps, retry counters, or suffixes.

Pass the canonical value through a native dispatch `name` field when supported.
For a schema with underscore-only `task_name`, replace every canonical hyphen
with one underscore, require
`^[a-z0-9]+(?:_[a-z0-9]+)+$`, preserve the 128-character limit, check adapted
uniqueness, and pass that deterministic transport value. Do not try a
hyphenated value in an underscore-only field.

The first packet lines always include `Worker name: <canonical-name>`,
`Task ID: <canonical-name>`, and `Native task name: <actual-transport-value>`.
The first two stay identical; the native transport value may differ and is not
the canonical Task ID. If the API has neither supported naming field, omit the
argument, record `unavailable`, and use the packet Task ID and result echo as
the user-visible fallback. The native worker card may retain a
platform-generated title; instruction-layer policy cannot change that UI.

## Platform boundary

The controller/worker distinction is an agent-instruction policy. The current
plugin only injects context; it cannot technically intercept tool calls or
provide an OS/tool permission barrier. Packet checks and runtime compliance
are not mechanically enforced by these hooks.
