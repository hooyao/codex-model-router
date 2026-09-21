# Spec Delta

## Purpose

Keep delegated Codex work bounded, inspectable, and inexpensive by supplying
each subagent with a consistent execution and result-reporting contract.

## ADDED Requirements

### Requirement: Bounded subagent scope
The plugin SHALL provide developer context whenever the primary agent starts a
subagent. The context MUST direct the subagent to work only on its assigned
objective, honor the parent task's workspace and safety constraints, and avoid
starting further subagents unless the parent explicitly instructs otherwise.

#### Scenario: A worker subagent starts
- **WHEN** the primary agent starts a subagent in an enabled router session
- **THEN** the subagent receives context that restricts it to its assigned objective and prohibits recursive delegation by default

### Requirement: Structured worker result
The subagent context SHALL require the worker's final response to include a
concise outcome summary, the files or evidence it inspected or changed, the
validation it performed, and any remaining risks or blockers. The worker MUST
not return raw intermediate logs unless the parent explicitly requests them.

#### Scenario: A worker completes successfully
- **WHEN** a subagent finishes an assigned task
- **THEN** its final response includes the required structured summary and validation evidence

#### Scenario: A worker cannot complete its task
- **WHEN** a subagent encounters a blocker or a failed validation it cannot resolve within scope
- **THEN** its final response identifies the blocker, the attempted validation, and the smallest next decision required from the parent
