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

Before its first business action, the controller validates and records a
decision-contract version 1 result, then records one explicit line:
`ROUTE: DIRECT — <rule/reason>` or
`ROUTE: DELEGATE — <topology/rule/model/effort/reason>`. Skills define HOW work is
performed, not WHO performs it, so loading or selecting a Skill cannot replace
this decision. The result separates `ownership`, `delegate_topology`, and
`verification_requirement`, and carries matched rule, reasons, limits, and any
reclassification trigger. Static keyword matching does not replace structured
signal classification.

DIRECT is eligible only when every condition is true:

- one local scope;
- one bounded, known outcome;
- no network access or synchronization;
- no long-running work or monitoring;
- no failure or recovery workflow;
- no substantive research or investigation; and
- no independent review or high-risk validation;
- confirmed permissions and known safety constraints; and
- a known write scope and declared self-check plan.

Every required fact must be explicit. A false or unknown signal cannot qualify
for the bounded direct fast path. DIRECT performs its own implementation and
required self-check; it does not bypass review, permission, safety,
write-ownership, or verification obligations.

The first decision covers the whole request. For a DELEGATE request, the
controller may execute a separately declared easy stage only after a second
decision for that stage independently resolves DIRECT. Record its stable ID,
owner, dependencies, exclusive write scope, context budget, acceptance
criteria, and self-check. Do not omit inherited permissions or risk to make a
stage appear eligible. The whole-request independent-review requirement still
applies. Reclassify a stage that exceeds its bounds before further business
work. Worker-owned stages must not be duplicated by the controller.

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

If direct work exceeds an approved bound, the controller stops before the next
business action and records `reclassified_from: DIRECT` with one explicit
`escalation_trigger`. Triggers cover scope or outcome expansion, network,
monitoring, recovery, research, review, dependency or overlap discovery,
failed validation, and permission/safety changes. The controller then emits the
DELEGATE line. Reclassification is never an implicit fallback.

## Delegate topology and context boundary

DELEGATE resolves to `PARALLEL` only when all four signals are explicitly true:
multiple bounded tasks, independent tasks, no dependencies, and disjoint write
scopes. Any false or unknown topology signal resolves to `ISOLATED_SERIAL`.
Dependencies, overlapping writes, and shared mutable state prohibit parallel
topology even when latency would improve.

`ISOLATED_SERIAL` describes bounded packet flow, not a technical sandbox. Send
only the context needed for the task and collect a compact result receipt with
artifact references. Do not claim that inherited parent history was erased or
that OS/tool isolation exists unless the runtime exposes and verifies it. Both
topologies inherit the configured depth, concurrency, retry, safety,
permission, write-ownership, and verification constraints. Configured numeric
limits are ceilings: lower them to the actual capability exposed by the runtime,
and never invent slots or APIs when the runtime does not report them.

Only after choosing DELEGATE does the controller discover native spawn,
wait/collect, and supported model/effort options and apply model routing. If
native multi-agent capability is unavailable, report BLOCKED with the observed
missing capability. Do not silently execute delegated work or create
user-facing threads to simulate workers. If dispatch fails, use the declared
retry/escalation limit and then report the blocker; controller execution is
never the fallback. Model preferences below do not establish availability.

Partition a delegated request by capability. Use a lightweight controller for
coordination and a qualifying, low-context easy stage. Use a cheap worker for
an easy stage when its tool output would consume too much controller context.
Reserve GPT-6 Astra/xhigh for a demanding hard kernel when justified; do not
route an entire mixed request to Astra solely because it contains one hard
stage. Keep `max` for exceptional cases. Record stage dependencies and write
ownership before dispatch, wait for prerequisites, and respect the runtime's
actual parallel capacity. Packet minimization does not erase inherited context.

Before each native spawn, validate the structured dispatch preflight described
in `dispatch-contract.md`. Keep its dimensions separate: planned packet
identity, native naming transport, actual selector arguments, capability
evidence, later runtime metadata, and final worker echo. Explicit selection
requires observed model and reasoning-effort arguments plus a current runtime
catalog. Inheritance is allowed only when the runtime contract is captured and
hash-bound; omitted arguments alone do not prove inheritance. Placeholder or
unresolved model/effort values block dispatch.
For CLI runtimes whose spawn schema hides selectors, the
`verified_role_config` structure records a frozen `[agents.default]` binding,
referenced model/effort role file, exact runtime binary, and calibration
artifacts. Keep `fork_turns` at `none` and omit `agent_type` and selector
arguments. This mode currently blocks dispatch authorization: hash-matched
self-assertions do not prove the runtime loaded that role. Do not route work
through it until independently verifiable calibration and trusted hook
capture are implemented and reviewed.

## Meta-task routing

Router self-improvement, routing-policy review, evaluation design, benchmark
selection, and auditing routing decisions require an independent review
worker separate from the author/implementer before finalization. Use the
highest suitable available model for that review, such as Astra/high when
supported. Resolve the actual model and effort from the runtime catalog.
The independent-review signal makes these DELEGATE routes and resolves their
verification requirement to `INDEPENDENT_REVIEW`.

Assign requested business analysis and implementation to workers as well.
A review-only worker does not fulfill an implementation request. The controller
accepts packets and synthesizes their evidence; it does not design the business
solution, edit files, or independently validate business results. Interpret
meta-tasks semantically, including follow-ups without explicit router keywords.

## Worker roles

| Task profile | Preferred role | Reasoning effort |
| --- | --- | --- |
| Clear, repeatable work and fixed-format summaries | GPT-6 Luna | Low, then Medium if needed |
| Repository discovery, everyday coding, technical documentation, test triage, routine code review | GPT-6.1 Sol | Medium; High for notable edge cases |
| Complex implementation, integration, or open-ended analysis | GPT-6.1 Sol | High; Xhigh when High is insufficient |
| Architecture, security, critical decisions, independent meta-task review | GPT-6 Astra | High; Xhigh for demanding cases |

Use the lowest capable available model and effort that can meet the task's
acceptance criteria, subject to the meta-task review rule. Escalate when
worker-reported ambiguity, risk, or failed validation justifies it. If no
suitable available worker can complete a required task, report a blocker.

## Config-driven examples

Use the validated workspace JSON at the path in the injected
`ROUTING_CONFIG_BEGIN`/`ROUTING_CONFIG_END` block as the canonical source of
execution-policy inputs, task examples, preferred model classes, efforts, and
rationales. The block contains a digest of the canonical in-memory JSON and the
effective policy; it omits the full JSON to stay within the model-visible output
budget. Execution ownership is decided before model selection. Resolve a
model preference against the current runtime capability catalog only for a
DELEGATE route. The stable delegated fallback when no example matches is
capability-based: Luna for clear repeatable work,
Sol for everyday engineering and complex or open-ended work, and Astra for work
requiring the strongest sustained judgment. Sol prefers `gpt-6.1-sol` when the
runtime exposes that model and the requested effort. Use `gpt-6-sol` as a
compatibility fallback only when the preferred model/effort is unavailable and
the runtime confirms the fallback model/effort. Resolve Luna to `gpt-6-luna`
and Astra to `gpt-6-astra` only when the runtime exposes the model and effort.
An existing schema-v3 workspace example may still name
`Terra`; the loader normalizes that legacy preference to Sol in memory for
discovery, implementation, documentation, triage, and review without rewriting
the workspace file. Preserve the workspace's execution mode and any user-edited
rationale.
Choose the lowest sufficient supported effort: medium for planning, high for
difficult work, xhigh for demanding cases where added reasoning is justified,
and max only for extreme cases when xhigh is inadequate. Max is never a default.
This plugin routes only `low`, `medium`, `high`, `xhigh`, and `max`; it does not
select `none` or `ultra`. Supported efforts must also be checked against the
current runtime before dispatch.

Verify prices for the exact native model against current official API pricing
before estimating or spending evaluation budget. Historical `gpt-6-sol` rates
must not be copied to `gpt-6.1-sol` without official evidence. The runtime's
actual availability and task requirements remain binding. Published API prices
do not establish Codex subscription cost or measured savings.

For performance evaluation, compare the same task and start state under
Astra/xhigh alone and under a lightweight Luna or Sol/low controller with
Astra/xhigh assigned only the hard kernel. Treat quality parity and total
estimated or billed USD, including orchestration and easy stages, as primary.
Use end-to-end wall time with one common start/end boundary as the only
comparative speed metric. Per-stage and critical-path timings diagnose where
time went; neither synthetic spans nor model-call counts are speed proxies.
Require repeated interleaved matched runs before claiming a stable speed
difference. No routing change earns a savings claim from policy alone.

The Sol preference follows official
[Codex model guidance](https://developers.openai.com/codex/models), consulted
on 2026-10-06. The existing Astra/Luna workload examples follow the official
[GPT-6 migration guide](https://developers.openai.com/api/docs/guides/latest-model/gpt-6-astra.md#migration-quickstart)
and [model catalog](https://developers.openai.com/api/docs/models), consulted
on 2026-09-24. The workspace file is editable and can replace those examples;
hard-coded prose in this policy must not override valid workspace values.

Every hook reloads and validates the workspace file. Existing config is found
upward before choosing the nearest `.git` ancestor or the event `cwd` for
exclusive first-time initialization. Invalid, malformed, oversized, unreadable,
or uncreatable config stops routing context generation with a clear error. Do
not truncate it or silently use bundled defaults.

## Dependencies, conflicts, and validation

Use stable task IDs and a directed acyclic graph. Wait for dependencies before
dispatch. Supply the worker packet defined in the Skill, including scope,
acceptance criteria, permissions, topology position, write ownership, and all
depth/concurrency/retry/stop limits. Parallelize only when the resolver returns
`PARALLEL`; serialize dependencies, overlapping write scopes, and shared mutable
state.

Worker-packet validation checks identity, completeness, consistency, evidence
references, and reported status. The controller self-checks its own DIRECT
stages and owns overall verification status; workers validate substantive
worker-owned stages.

Workers own source inspection, business decisions and analysis, file edits,
commands, tests, and substantive validation within their assigned scope.
Resolve conflicting business recommendations through a review worker. Assign
file conflicts and merging to an integration worker unless a bounded
controller-owned resolution stage independently passes DIRECT. Require the
responsible stage owner to validate the integrated artifact before reporting it
as validated.

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
plugin only injects context; it cannot erase history, technically intercept tool
calls, create a sandbox, or provide an OS/tool permission barrier. Packet checks
and runtime compliance are not mechanically enforced by these hooks.
