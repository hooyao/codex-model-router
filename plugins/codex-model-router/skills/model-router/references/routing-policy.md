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

## Mandatory worker routing

The controller's role is orchestration only. After discovering native spawn,
wait/collect, and supported model/effort options, dispatch bounded workers for
all business work, including simple tasks. Repository discovery and domain
analysis needed for planning are themselves worker tasks. Do not execute
business work in the controller to save delegation overhead.

If native multi-agent capability is unavailable, report BLOCKED with the
observed missing capability. Do not silently execute the task or create
user-facing threads to simulate workers. If dispatch fails, use the declared
retry/escalation limit and then report the blocker; controller execution is
never the fallback. Model preferences below do not establish availability.

## Meta-task routing

Router self-improvement, routing-policy review, evaluation design, benchmark
selection, and auditing routing decisions require an independent review
worker separate from the author/implementer before finalization. Use the
highest suitable available model for that review, such as Astra/high when
supported. Resolve the actual model and effort from the runtime catalog.

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

## Platform boundary

The controller/worker distinction is an agent-instruction policy. The current
plugin only injects context; it cannot technically intercept tool calls or
provide an OS/tool permission barrier. Packet checks and runtime compliance
are not mechanically enforced by these hooks.
