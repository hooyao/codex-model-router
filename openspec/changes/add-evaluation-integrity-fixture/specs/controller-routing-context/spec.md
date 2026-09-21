## MODIFIED Requirements

### Requirement: Controller session context
The plugin SHALL identify the controller as a pure orchestrator. Planning,
dispatch, worker-packet validation, conflict coordination, and synthesis remain
controller responsibilities. Workers MUST own repository inspection, business
analysis, edits, commands, tests, and substantive verification.

#### Scenario: A controller validates results
- **WHEN** a worker returns an implementation result
- **THEN** the controller checks the packet and assigns substantive verification to a worker

### Requirement: Per-prompt delegation gate
The controller MUST dispatch every business task, including a simple read or
small edit, to a bounded native worker. Missing native dispatch or collection
MUST produce a blocker, never direct controller execution. This requirement
supersedes the optional direct-work gate in `add-router-mvp`.

#### Scenario: A benchmark requests one typo correction
- **WHEN** the router variant receives the small-edit task
- **THEN** business work is assigned to a worker and delegation is expected

#### Scenario: Native dispatch is unavailable
- **WHEN** the controller cannot dispatch or collect a native worker
- **THEN** it reports blocked and the benchmark retains that scheduled record
