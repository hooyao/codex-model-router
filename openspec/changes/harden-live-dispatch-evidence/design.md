## Context

Historical spawn calls exposed native names but omitted model and effort
selectors. Worker packets were encrypted, while child runtime metadata and
final prose were visible later. Recursive text search also matched inherited
Skill templates.

## Goals / Non-Goals

**Goals:** fail closed before dispatch, retain evidence dimensions, derive
chronology from typed events, and preserve historical failure evidence.

**Non-Goals:** infer hidden packets, claim runtime inheritance without a
captured contract, or rerun paid live models.

## Decisions

### Validate a dispatch contract before spawn

Explicit selectors require captured spawn-schema and model-catalog evidence.
Inheritance requires a separately captured inheritance contract. Missing
evidence produces a capability blocker.

Capability records contain file paths and hashes. Preflight opens each source,
rejects empty/zero/mismatched evidence, parses a kind-specific schema, and
derives supported arguments, model/effort compatibility, and inheritance.

### Parse only role-bounded events

Routes come from controller messages, transport from spawn calls, runtime data
from child turn context, and echoes from child task completion. Evidence that
is absent or encrypted is unknown.

### Recompute rather than trust

The validator hashes every referenced artifact and recomputes campaign
acceptance. Versioned reprocessing links the immutable original report.

Validation treats the report as an untrusted cache: it reparses raw transcript
and session events, validates index/source links, snapshots the actual result
and frozen oracle, rebuilds every leaf, and then compares authored fields.
Activation uses one formal `raw/` layout and source-bound hook provenance.
Collection preflights all destinations, stages result copies, validates, and
publishes atomically or rolls back fresh output.

### Bind authority before comparing hashes

The validator chooses its own repository-pinned benchmark/case/fixture chain;
the report cannot choose an oracle. Artifact and index locations are canonical
per case. The transcript ID selects the parent unconditionally, and index
metadata plus native spawn call/result identities bind each child to that
parent. Hash equality proves content identity only after these authority and
ancestry checks.

The formal activation spec is copied unchanged and its neutral prompt is
checked against actual user events. Full injected controller/config content,
workspace and session identity, completed read results, and unchanged business
state are required. A separate offline capture runs the configured hook Python
command in the launch environment, preserving failures. It never substitutes
the collector's interpreter. PATH remediation must happen before Codex starts.

Routing chronology uses the active preceding decision for every dispatch and
controller business action. Review receipts use an exact verdict line rather
than a substring search. Unsupported or ambiguous evidence formats fail closed.

## Risks / Trade-offs

- More historical checks become unknown; this is preferable to false passes.
- External session paths limit portability; hashes make absence explicit.

## Open Questions

- None.
