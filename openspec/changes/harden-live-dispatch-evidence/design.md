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

### Parse only role-bounded events

Routes come from controller messages, transport from spawn calls, runtime data
from child turn context, and echoes from child task completion. Evidence that
is absent or encrypted is unknown.

### Recompute rather than trust

The validator hashes every referenced artifact and recomputes campaign
acceptance. Versioned reprocessing links the immutable original report.

## Risks / Trade-offs

- More historical checks become unknown; this is preferable to false passes.
- External session paths limit portability; hashes make absence explicit.

## Open Questions

- None.
