## Context

The plugin currently injects dispatch guidance and a bounded worker packet but
does not define how workers are named. Parallel workers therefore rely on
platform-generated labels, which may not expose their purpose, selected model,
or reasoning effort. The plugin is instruction-layer policy and cannot add a
field to a native dispatch API that does not provide one.

## Goals / Non-Goals

**Goals:**

- Give every dispatch a deterministic identity derived from its planned
  purpose and resolved routing decision.
- Make the identity available as the worker packet Task ID even when native
  dispatch does not support names.
- Define normalization and validation precisely enough for offline contract
  tests and consistent controller behavior.

**Non-Goals:**

- Change the native dispatch schema or worker-card rendering.
- Introduce a dispatch proxy, state store, or random suffix.
- Rename an already running worker after routing escalation.

## Decisions

### Compose names from stable dispatch inputs

The controller records a short English purpose label in the task DAG, freezes
it before dispatch, and combines it with the exact resolved model identifier
and reasoning effort as `<purpose>-<model>-<effort>`. Retries with unchanged
inputs retain the name; a changed model or effort produces a newly computed
name. Random values, timestamps, and retry counters are prohibited.

Alternative considered: use only the task ID. That is deterministic but hides
the routing decision required by this change. Adding the task ID to the
required format was rejected because it would no longer be the requested
three-part identity.

### Use one canonical slug algorithm

Each raw component is Unicode-normalized with NFKD, converted to lowercase
ASCII by discarding non-ASCII code points, then every maximal run outside
`[a-z0-9]` is replaced with one hyphen and leading or trailing hyphens are
removed. All components must be non-empty. Purpose, model, and effort are
limited to 48, 48, and 24 characters, and the full name to 128 characters.
The final name is valid only when it exactly equals a recomputation from the
recorded inputs, matches `^[a-z0-9]+(?:-[a-z0-9]+)*$`, and is unique within the
planned DAG.

Alternative considered: preserve arbitrary Unicode. ASCII slugs were selected
because native name-field character support is not known and the repository's
language policy already requires English operational labels.

A bundled standard-library helper implements this canonicalization and exact
input validation for deterministic offline tests and future dispatch
integration. The lifecycle hook communicates the contract as developer context
because it does not receive or invoke native dispatch calls.

### Make the packet echo the portable source of truth

When a native dispatch API exposes a `name` field, the controller supplies the
canonical value. Every packet records the same canonical value as both
`Worker name` and `Task ID`, and the worker echoes both fields in its final
structured result. If the API lacks a name field or rejects the value, the
controller does not invent an unsupported argument; the packet Task ID and
result echo are the documented fallback.

Alternative considered: omit the packet echo when a native field exists. The
always-present echo was chosen so logs and collected results retain the same
identity across runtime versions.

## Risks / Trade-offs

- [A native worker card may keep a platform-generated title] → Document that
  packet/result visibility is the fallback and do not claim UI enforcement.
- [Two tasks can share all three naming inputs] → Reject the dispatch plan and
  require a more task-specific purpose before spawning either worker.
- [A native API can impose undocumented length limits] → Fall back to the
  canonical packet/result identity instead of adding nondeterministic changes.
- [Instruction context cannot mechanically reject a bad name] → Add explicit
  recomputation rules and offline assertions for every injected controller
  context and the worker result contract.

## Migration Plan

1. Install or reinstall the updated plugin and start a new task so the revised
   hook contexts are loaded.
2. New dispatches use canonical names; already running workers are unchanged.
3. Roll back by installing the previous plugin version.

## Open Questions

- None for the instruction-layer implementation. A future native dispatch
  integration can replace the fallback when a stable name field is universal.
