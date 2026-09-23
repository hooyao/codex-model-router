## ADDED Requirements

### Requirement: Capability-backed dispatch preflight

Before native delegation, the controller SHALL validate a structured dispatch
record against captured spawn-schema and model capability evidence. It MUST use
explicit resolved selectors or verified inheritance and MUST reject placeholder
or unresolved model/effort values.

#### Scenario: Selector capability is absent

- **WHEN** the captured spawn schema lacks a required explicit selector
- **THEN** dispatch is blocked with a capability-evidence record
- **AND** no model route is fabricated

### Requirement: Role-bounded observed evidence

The live collector SHALL keep packet identity, native transport, selector
arguments, runtime metadata, and final worker echo separate. It MUST NOT derive
them from aggregate inherited text.

#### Scenario: Skill template contains identity labels

- **WHEN** inherited instructions contain worker-packet template fields
- **THEN** those fields do not satisfy packet or final-echo evidence

### Requirement: Evidence-derived acceptance and chronology

Validation SHALL hash all transcript, index, session, and artifact references,
recompute acceptance, and use assignment spans for work overlap. Session
lifetime overlap SHALL be reported separately.

#### Scenario: Acceptance flag is tampered

- **WHEN** an authored campaign flag disagrees with derived statuses
- **THEN** validation rejects the report

### Requirement: Immutable historical reprocessing

Reprocessing SHALL write a versioned output linked to the original and SHALL
refuse to overwrite existing evidence. Missing environment evidence MUST leave
root cause unknown.

#### Scenario: Historical failed campaign is reprocessed

- **WHEN** corrected analysis still contains failed or unknown required checks
- **THEN** the campaign remains failed
- **AND** the original result remains unchanged
