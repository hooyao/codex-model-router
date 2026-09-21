# Routing Policy

## Delegation Gate

Before spawning a worker, the controller decides whether the request is small,
clear, and low risk enough to complete directly. Delegate only if a bounded
task has an independently useful result, needs a specialist role, or can run
in parallel without write conflicts.

## Worker Roles

| Task profile | Preferred role | Reasoning effort |
| --- | --- | --- |
| Clear, repeatable, high-volume work with known outputs | Luna | Low, then Medium if tool use or execution needs more follow-through |
| Read-heavy exploration, supporting research, everyday implementation, or pragmatic tool use | Terra | Medium; use High for notable edge cases |
| Ambiguous, complex, open-ended, or high-value implementation and analysis | Sol | Medium or High |
| Architecture, security, critical decisions, or sustained judgment across a difficult workflow | Astra | High or the lowest higher supported setting |

Use the lowest capable model and effort that can plausibly meet acceptance
criteria. The mapping is role guidance, not an assumption about account
availability. If the preferred model or effort is unavailable, choose the
nearest available option and state that decision briefly.

## Task Packet

Every worker task includes:

- A stable task identifier and single objective.
- Declared dependencies and a clear completion condition.
- Only the files, symbols, sources, or artifacts needed for the task.
- An allowed write scope, or an explicit read-only instruction.
- A requested model and reasoning effort with a brief rationale.
- A required return packet: outcome, evidence, validation, and risks or
  blockers.

## Dependencies and Writes

Treat tasks as a directed acyclic graph. Do not start a task before its
dependencies return the information it needs. Run read-only exploration,
independent tests, and non-overlapping file changes in parallel. Serialize
tasks that edit the same file or share an order-sensitive external state.

## Verification and Escalation

The controller, not a worker, accepts the final result. Verify acceptance
criteria after merging worker output. Escalate one step at a time when a worker
encounters ambiguity, a high-risk decision, or failed validation; avoid
unbounded retries and recursive delegation.
