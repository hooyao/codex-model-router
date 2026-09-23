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

#### Scenario: Capability metadata names a nonexistent or contradictory source

- **WHEN** a capability path is absent, empty, zero-hashed, hash-mismatched, malformed, or its parsed facts do not support the selected model/effort
- **THEN** preflight blocks before dispatch
- **AND** claimed metadata does not substitute for opened source evidence

### Requirement: Role-bounded observed evidence

The live collector SHALL keep packet identity, native transport, selector
arguments, runtime metadata, and final worker echo separate. It MUST NOT derive
them from aggregate inherited text.

#### Scenario: Skill template contains identity labels

- **WHEN** inherited instructions contain worker-packet template fields
- **THEN** those fields do not satisfy packet or final-echo evidence

#### Scenario: Identity dimensions contradict

- **WHEN** packet Task ID/name, native name, selected or inherited model/effort, runtime metadata, and final echo do not describe one canonical worker
- **THEN** identity validation fails
- **AND** missing facts remain unknown rather than passing

### Requirement: Evidence-derived acceptance and chronology

Validation SHALL hash all transcript, index, session, and artifact references,
recompute acceptance, and use assignment spans for work overlap. Session
lifetime overlap SHALL be reported separately.

#### Scenario: Acceptance flag is tampered

- **WHEN** an authored campaign flag disagrees with derived statuses
- **THEN** validation rejects the report

#### Scenario: A leaf status or frozen oracle is tampered

- **WHEN** an authored leaf differs from reparsed raw events or a referenced original, index, session, artifact, or oracle hash changes
- **THEN** validation rejects the report before computing campaign acceptance

### Requirement: Source-bound activation and fresh publication

The activation gate SHALL parse the formal probe, environment, routing config,
CLI, manifest, capability, and hook-provenance sources in one canonical `raw/`
layout. A route SHALL count only when its controller message begins with
`ROUTE:` before business action. Collection SHALL preflight every destination,
stage outputs, publish fresh with atomic renames, and roll back on validation
failure.

#### Scenario: Route text is quoted or output already exists

- **WHEN** `ROUTE:` occurs only in an example/quote or any report/result destination exists
- **THEN** activation or collection fails without mutating existing evidence

### Requirement: Dependency and receipt provenance

Required process checks SHALL bind producer-authored receipt hashes to the
dependent consumer packet and SHALL derive dependency stage order from paired
assignment spans. Every worker dispatch SHALL follow a role-bounded route.

#### Scenario: Receipt or follow-up linkage is missing

- **WHEN** a consumer does not quote its producer's observed receipt hash or a dependent assignment begins before its producer ends
- **THEN** the process check fails

### Requirement: Immutable historical reprocessing

Reprocessing SHALL write a versioned output linked to the original and SHALL
refuse to overwrite existing evidence. Missing environment evidence MUST leave
root cause unknown.

#### Scenario: Historical failed campaign is reprocessed

- **WHEN** corrected analysis still contains failed or unknown required checks
- **THEN** the campaign remains failed
- **AND** the original result remains unchanged
