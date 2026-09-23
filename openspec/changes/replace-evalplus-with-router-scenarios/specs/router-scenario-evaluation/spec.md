# Router scenario evaluation specification

## ADDED Requirements

### Requirement: Product-centered scenario matrix

The benchmark SHALL include frozen fixture-backed cases for investigation
receipt reuse, serial escalation, independent parallel work, architecture
review, and a direct calibration control.

#### Scenario: Primary coverage

- **WHEN** the benchmark manifest is validated
- **THEN** all five categories and all three treatments are present

### Requirement: Inspectable topology evidence

Every run SHALL record route transitions, timed dependency spans, exclusive
artifact ownership, context and receipt volume, and critical-path time.

#### Scenario: Parallel treatment

- **WHEN** selective topology is PARALLEL
- **THEN** at least two worker spans overlap
- **AND** their artifact ownership is disjoint

#### Scenario: Serial escalation

- **WHEN** selective execution exceeds direct scope
- **THEN** the trace records DIRECT then DELEGATE with a trigger
- **AND** no controller business action occurs after delegation

#### Scenario: Dependency timing

- **WHEN** a dependent span starts before its prerequisite finishes
- **THEN** contract validation rejects the run

#### Scenario: Frozen dependency graph

- **WHEN** a record omits or substitutes a case-required dependency or receipt edge
- **THEN** campaign validation rejects the run

#### Scenario: Controller after delegation

- **WHEN** a controller business span crosses or follows the first delegation timestamp
- **THEN** contract validation rejects the run

### Requirement: Review and failure integrity

Completed independent-review work SHALL name a passing reviewer distinct from
controller and author. Every scheduled terminal failure SHALL remain in the
analysis denominator.

#### Scenario: Missing reviewer

- **WHEN** completed architecture work lacks distinct review
- **THEN** contract validation fails

#### Scenario: Blocked run

- **WHEN** a run terminates blocked
- **THEN** it remains a zero-quality scheduled observation
- **AND** its executed spans and edges form a downward-closed prefix of the frozen graph

#### Scenario: Impossible failed prefix

- **WHEN** a failed record supplies a downstream stage without its frozen prerequisite or receipt edge
- **THEN** campaign validation rejects the record

### Requirement: Evidence-derived quality and context reuse

Required quality checks SHALL come from the frozen case. Receipt reuse SHALL
come from hash-bound producer/consumer links containing the required facts.

#### Scenario: Omitted claimed check

- **WHEN** a record omits or overrides a required quality claim
- **THEN** analysis still recomputes the required check and effective score

#### Scenario: Forced direct quality

- **WHEN** forced DIRECT produces the exact required artifacts
- **THEN** artifact quality passes without requiring delegate-only receipt, review, or worker spans
- **AND** route/process compliance is reported separately

#### Scenario: Forged receipt

- **WHEN** receipt content, facts, or consumers do not match its hash and span links
- **THEN** validation or the derived quality check fails

### Requirement: Honest reporting

Measured and estimated costs SHALL be reported separately. Synthetic results
SHALL NOT authorize performance, savings, or release claims.

#### Scenario: Synthetic campaign

- **WHEN** the bundled deterministic campaign is analyzed
- **THEN** release claim support is false
- **AND** limitations distinguish estimated cost from measured data
- **AND** evidence provenance matches the synthetic manifest
