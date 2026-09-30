# Selective execution topology specification

## ADDED Requirements

### Requirement: Versioned deterministic routing decision

The router SHALL validate a versioned structured decision request and return a
validated deterministic result containing execution ownership, delegate
topology, verification requirement, effective configured mode, matched rule,
reasons, inherited constraints, and optional reclassification data.

#### Scenario: Unknown direct signal

- **WHEN** any fact required by the bounded direct path is unknown
- **THEN** ownership is DELEGATE
- **AND** the decision explains the unknown signal

#### Scenario: Callable resolution workflow

- **WHEN** the controller sends a valid request to the injected resolver path with the injected config
- **THEN** the program emits the validated deterministic result before business execution

### Requirement: Safe delegate topology

The router SHALL select PARALLEL only when multiple bounded tasks,
independence, absence of dependencies, and disjoint write scopes are all
explicitly true. Otherwise it SHALL select ISOLATED_SERIAL.

#### Scenario: Shared write scope

- **WHEN** delegated tasks have overlapping or unknown write scopes
- **THEN** topology is ISOLATED_SERIAL

#### Scenario: Single worker ceiling

- **WHEN** every task-independence signal is true but maximum concurrency is one
- **THEN** topology is ISOLATED_SERIAL

#### Scenario: Named runbook

- **WHEN** a named multi-step runbook signal is true or unknown
- **THEN** ownership is DELEGATE

### Requirement: Recordable direct escalation

A task that exceeds approved DIRECT bounds SHALL stop direct execution and
record a DELEGATE reclassification with an explicit supported trigger.

#### Scenario: Failed direct validation

- **WHEN** direct validation fails
- **THEN** the new decision records `reclassified_from: DIRECT`
- **AND** records `escalation_trigger: validation-failed`

### Requirement: Honest context boundary

The router SHALL describe isolation as minimized packet and compact receipt
flow. It SHALL NOT claim erased parent history or technical sandboxing without
verified runtime support.

#### Scenario: Instruction-only isolation

- **WHEN** the runtime does not expose verified technical isolation
- **THEN** the router describes only packet and receipt minimization

### Requirement: Topology-aware evaluation

The offline evaluator SHALL compare DIRECT/NONE, DELEGATE/ISOLATED_SERIAL, and
DELEGATE/PARALLEL traces with case expectations. Expected direct controller
work SHALL NOT be an automatic policy failure.

#### Scenario: Expected direct case

- **WHEN** a router record uses DIRECT/NONE for a case expecting direct
- **THEN** controller business work is not a policy failure

#### Scenario: Delegated controller work

- **WHEN** a DELEGATE trace reports any controller business action
- **THEN** the evaluation policy gate fails

#### Scenario: Independent review evidence

- **WHEN** a completed trace declares INDEPENDENT_REVIEW without a distinct verification session
- **THEN** contract validation rejects the record

#### Scenario: Failure before review

- **WHEN** an INDEPENDENT_REVIEW run terminates blocked, cancelled, timed out, or errored before review
- **THEN** the failed record remains valid without a verification session
