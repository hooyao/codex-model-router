## ADDED Requirements

### Requirement: Strict versioned campaign identity
The evaluator MUST require schema v2, exact typed fields, finite numeric
thresholds, non-empty required records, and duplicate-key rejection in every JSON
input. It MUST reject globally reused run IDs, duplicate slots, pair IDs spanning
slots, mismatched paired IDs, missing or extra scheduled records, unsafe paths,
and hash or environment provenance mismatches.

#### Scenario: A duplicate hides in the planned denominator
- **WHEN** two runs occupy one case/repetition/variant with different pair IDs
- **THEN** the campaign is invalid before analysis

#### Scenario: Evidence escapes or changes
- **WHEN** a reference traverses outside its root, follows a link, or has a different SHA-256
- **THEN** validation fails with a diagnostic

### Requirement: Failure precedence and release boundary
Demonstrated safety, quality, or measured cost/latency gate failures MUST remain
fail when another measurement is missing. Undefined ratios MUST be null with a
reason, never epsilon-adjusted. Draft or synthetic campaigns MUST NOT emit
release pass; this first integrity slice MUST NOT authorize release pass even
for release-labeled inputs before a reviewed statistical release gate exists.

#### Scenario: Unsafe run lacks cost data
- **WHEN** a router safety incident is recorded and cost is incomplete
- **THEN** the result is fail

#### Scenario: A complete successful draft is compared
- **WHEN** all exploratory gates pass on synthetic data
- **THEN** the result remains inconclusive for release

### Requirement: Accounting evidence
ccusage MUST remain an unverified observation source. Complete accounting MUST
require a hash-bound ledger matching all declared sessions, provenance, accounting
convention, and run total, including retries and abandoned work. Assertions or
ccusage command-line flags alone MUST NOT establish completeness.

#### Scenario: A user attempts to verify ccusage
- **WHEN** an observation is imported or a ccusage record claims complete cost
- **THEN** the importer remains incomplete and the complete record is rejected
