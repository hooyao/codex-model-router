# Dispatch preflight contract

Every delegated spawn requires a validated `dispatch-contract-v1` record before
the native tool call. Run the bundled `hooks/dispatch_contract.py` with the JSON
record on standard input. A nonzero exit is a capability blocker; it is not
permission to fabricate a route or dispatch with omitted selectors.

The record has these exact top-level fields:

- `schema_version`: `1`;
- `dispatch_id`: stable planned dispatch identifier;
- `purpose`: frozen task-purpose component;
- `canonical_name`: recomputed purpose/model/effort name;
- `packet`: exact `worker_name`, `task_id`, and `native_task_name` values;
- `native_dispatch`: tool name, naming field, supported argument names, native
  name, and the referenced spawn-schema evidence;
- `selection`: `explicit` or `verified_inheritance`, resolved model, resolved
  reasoning effort, and capability-evidence references; and
- `capability_evidence`: hash-bound runtime observations with an ID, kind,
  source, SHA-256, and capture timestamp.

Capability evidence kinds are `spawn_schema`, `model_catalog`, and
`inheritance_contract`. Explicit selection requires a captured spawn schema
that exposes both `model` and `reasoning_effort`, plus model-catalog evidence.
Verified inheritance requires evidence that the native runtime contract
actually defines inheritance and identifies the resolved inherited model and
effort. Silence, omitted arguments, a later child turn context, or a successful
spawn is not inheritance-contract evidence.

Values containing `unknown`, `unavailable`, `unexposed`, `unresolved`, or
`placeholder` are invalid model/effort inputs. Missing selector/catalog evidence
must produce a `runtime-capability-evidence` blocker. Runtime metadata and the
worker's final echo are post-dispatch observations and never retroactively
authorize a spawn.
