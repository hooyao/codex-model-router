---
name: model-router
description: Route a complex Codex request into bounded subagent tasks with the lowest suitable available model and reasoning effort. Use when a request can benefit from parallel research, implementation, testing, or review work.
---

# Codex Model Router

Use this Skill to plan a reliable multi-agent workflow. The bundled lifecycle
hooks inject a concise version of this policy at session, prompt, and worker
boundaries; this file provides the fuller operating procedure.

## Controller Protocol

1. Apply the delegation gate: execute simple, low-risk work directly. Delegate
   only when a bounded task has a clear return format or meaningful independent
   work can run in parallel.
2. Describe delegated work as a dependency graph. Define an objective, inputs,
   acceptance criteria, allowed files or evidence, and an output contract for
   every task.
3. Choose the lowest suitable available worker role. Follow
   [the routing policy](references/routing-policy.md) and preserve the user's
   workspace, approval, and safety constraints.
4. Serialize tasks that write overlapping files. Parallelize read-heavy
   exploration, tests, triage, and independent write scopes only.
5. Collect concise worker summaries. The controller verifies results, resolves
   conflicts, and owns the final response.

## Worker Packet

Provide each worker only the context it needs:

```text
Objective:
Dependencies:
Allowed files or evidence:
Acceptance criteria:
Model and reasoning effort:
Return format: outcome, evidence, validation, risks or blockers.
```

Do not delegate to reduce work that is already simpler to complete directly.
Do not make the worker recursively delegate unless the parent explicitly asks.
