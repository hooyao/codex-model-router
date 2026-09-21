## MODIFIED Requirements

### Requirement: Bounded subagent scope
Workers MUST execute assigned work, including inspection and verification,
without controller capability discovery or recursive delegation. This supersedes
the recursive-delegation exception in the original MVP requirement and design. The controller
MUST arrange independent review for router meta-tasks; an implementation worker
MUST NOT create that reviewer itself.

#### Scenario: A verification worker receives controller context
- **WHEN** a worker is assigned scoped checks
- **THEN** it runs those checks directly and reports evidence without spawning workers
