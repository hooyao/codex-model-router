## ADDED Requirements

### Requirement: Canonical dispatched-subagent name
Before every native subagent dispatch, the controller SHALL derive one
canonical identifier in the form `purpose-model-effort`. The identifier MUST
use a task-specific purpose and the actual model and reasoning effort selected
from the runtime capability catalog. The controller MUST use the same canonical
identifier as the worker packet Task ID and require it in the worker's result.

#### Scenario: The controller dispatches an Astra review worker
- **WHEN** the controller selects purpose `evaluation review`, model `Astra`,
  and effort `High`
- **THEN** it assigns `evaluation-review-astra-high` as the canonical
  subagent name and worker packet Task ID

### Requirement: Canonical component normalization and validation
The plugin SHALL normalize purpose, selected model, and selected effort by
performing Unicode NFKD decomposition, ASCII conversion, lowercase conversion,
and non-alphanumeric-run replacement with one hyphen, then removing leading
and trailing hyphens. A normalized purpose MUST be non-empty and at most 48
characters; normalized model and effort MUST be non-empty and at most 48 and
24 characters respectively; the complete identifier MUST be at most 128
characters. The controller MUST reject a planned dispatch with an invalid
component or duplicate canonical identifier instead of silently changing it.

#### Scenario: A purpose cannot form an ASCII identifier
- **WHEN** the controller supplies a purpose that normalizes to an empty value
- **THEN** the naming contract rejects the planned dispatch and requires a
  specific valid purpose

#### Scenario: Two planned workers produce the same canonical name
- **WHEN** the controller validates a DAG containing duplicate canonical
  identifiers
- **THEN** it rejects the dispatch plan before spawning either worker

### Requirement: Native name propagation and fallback
When the discovered native spawn schema accepts a `name` field, the controller
SHALL pass the canonical identifier through that field. When the schema lacks a
`name` field, the controller MUST NOT invent one; it MUST retain the canonical
identifier as the worker packet Task ID and disclose that the runtime cannot
provide a native user-visible subagent name.

#### Scenario: A native spawn schema supports names
- **WHEN** capability discovery confirms that native spawn accepts `name`
- **THEN** the controller passes the canonical identifier as the `name` value

#### Scenario: A native spawn schema does not support names
- **WHEN** capability discovery confirms that native spawn lacks `name`
- **THEN** the controller dispatches only with supported arguments, uses the
  canonical identifier in the packet and task ID, and reports the UI naming
  limitation
