# Strict offline contract v2

`scripts/contract.py` is the executable schema, using explicit validators rather
than an external JSON Schema dependency. Every listed object has exactly the
listed keys: missing and unknown keys are errors. JSON duplicate keys (including
nested keys), NaN/Infinity, boolean numbers, non-finite numbers, and empty required
strings are invalid. Whitespace-only JSONL is an empty-record error. Blank lines
between records are ignored; records themselves cannot be empty.

All paths are relative to the experiment directory except fixture references
(relative to the cases file) and initial/reference paths (relative to the fixture
definition). Use non-empty `/`-separated ASCII alphanumeric, underscore, dot, or
hyphen components. Dot/parent components, trailing dots, Windows device names,
absolute/drive/UNC paths, backslashes, and links/reparse points are forbidden.
`{path, sha256}` means an exact-key reference with a lowercase 64-hex digest.
JSON file hashes cover exact bytes, not normalized JSON.

## Experiment manifest

| Field | Contract |
| --- | --- |
| `schema_version` | Integer 2 |
| `experiment_id` | Non-empty string |
| `status` | `draft` or `release`; release requires all cases release-labeled |
| `data_origin` | `synthetic` or `observed` |
| `cases_file`, `cases_sha256` | Existing confined cases file and matching SHA-256 |
| `variants` | Unique array containing exactly `baseline`, `router` |
| `repetitions_per_case` | Integer 1 through 10,000 |
| `analysis` | Exact threshold object below |
| `controls` | Exact environment object below |

`analysis` contains `quality_margin` in [-1, 0], `minimum_router_quality` in
[0, 1], `maximum_cost_ratio` in [0, 1], and `maximum_latency_ratio` in [1, 100].
These are finite numbers, never strings or booleans. There is no comparison-time
override for the planned denominator or thresholds.

`controls` contains non-empty `repository_revision`, `harness_version`,
`environment_id`, and `policy`. Revision is a full 40/64-hex Git ID or 64-hex
fixture-tree digest. When a fixture is present, revision must equal its canonical
initial-tree digest, checked by manifest and campaign validation. Policy is
exactly `pure-orchestrator-v1`. The environment ID
identifies the harness's externally frozen settings; this slice does not verify
actual model availability, model settings, approval configuration, or service tier.

## Cases and fixtures

The cases file is a non-empty array of objects with `id`, `status`, `category`,
`prompt`, `expected_mode`, `acceptance`, `failure_signals`, and `fixture`.
IDs are unique. Status is draft/release. Category, prompt, and expected mode are
non-empty strings; direct router execution is disallowed. Acceptance and failure
signals are non-empty unique string arrays. Fixture is null for draft scenarios
or a `{path, sha256}` reference. Release cases require a fixture.

This slice supports only fixture ID `small-edit`. Its definition has
`schema_version: 2`, `id`, `grader_version: "exact-tree-v1"`, `prompt`, `initial`,
and `reference`. Case ID/prompt must equal fixture ID/prompt. Each tree descriptor
has exactly `path` and `tree`. A tree has `files` (non-empty path-to-SHA-256 map)
and `directories` (unique path array, possibly empty). Both trees must match the
actual fixture files and differ from each other. Canonical tree digest is
SHA-256 of UTF-8 `json.dumps(tree, sort_keys=True, separators=(",", ":"))`, with
sorted directory names and no newline. File content is never normalized.

## Terminal records

Each JSONL object has exactly these fields:

| Fields | Contract |
| --- | --- |
| `schema_version` | Integer 2 |
| `experiment_id`, `manifest_sha256` | Match the experiment identity and byte hash |
| `run_id`, `pair_id`, `case_id` | Non-empty strings; campaign-wide run uniqueness and pair/slot bijection |
| `variant`, `repetition` | Scheduled baseline/router slot and positive integer repetition |
| `outcome` | `completed`, `timeout`, `cancelled`, `error`, or `blocked` |
| `passed` | Boolean; must be false for non-completed outcomes |
| `quality_score` | Finite [0, 4]; zero for non-completed outcomes; not aggregated |
| `duration_ms` | Finite non-negative request-to-terminal duration |
| `delegated_tasks`, `retries` | Non-negative integers |
| `recursive_delegation`, `write_conflicts`, `unrecovered_partial_failure`, `scope_leak`, `prompt_injection_violation` | Boolean incident flags |
| `evidence` | Non-empty array of `{path, sha256}` file references with unique paths |
| `route_trace` | Exact trace object below |
| `environment` | Exact equality to manifest controls |
| `result_tree` | Null or `{path, sha256}` directory reference using canonical tree digest; required for completed fixture runs |
| `cost` | Exact accounting object below |

Every case/repetition/variant is required exactly once. Pair IDs identify exactly
one case/repetition and must agree between variants. IDs and inventories are
validated across the whole supplied campaign, not across a global database.
Evidence files are opaque byte-hashed artifacts, not executable instructions.

`route_trace` contains `controller_session` (non-empty string), `worker_sessions`,
`retry_sessions`, `verification_sessions`, `abandoned_sessions` (unique string
arrays, possibly empty), and `controller_business_actions`,
`worker_business_actions` (non-negative integers). `delegated_tasks` must equal
the worker inventory length. Role overlap is allowed except controller-as-worker;
sessions cannot be reused across runs. In-session retries need not create new
session IDs. Router controller actions, or completed router work without a worker
and worker business actions, fail the policy gate.

## Accounting objects

`cost` contains exactly `source`, `complete`, `cost_usd`, and `ledger`. Source is
`unavailable`, `ccusage`, `synthetic`, or `harness-ledger-v1`. Complete is boolean.
Cost is null or finite non-negative USD. Incomplete accounting requires a null
ledger. Complete accounting requires a cost and a `{path, sha256}` ledger;
only synthetic/harness-ledger-v1 sources qualify. Synthetic accounting requires
a synthetic campaign. ccusage cannot qualify by any completeness assertion.

The ledger has exactly `schema_version: 2`, `experiment_id`, `run_id`, `source`,
`provider`, `currency: "USD"`, `method`, `pricing_version`, and `sessions`.
Experiment/run/source match the record. Provider/pricing version are non-empty.
Method is `billed`, `api-price-estimate`, or `synthetic`; synthetic method and
source must agree. Complete ledgers must share provider/currency/method/pricing
version across the campaign.

Sessions is a non-empty array of `{session_id, exclusive_cost_usd, evidence}`.
IDs are unique and equal the trace's union of all roles. Costs are finite and
non-negative. Evidence is a checked file reference. Exclusive totals must sum
to the record total (`math.isclose`, relative tolerance 1e-9, absolute 1e-12).
Each session is counted once even if listed in several roles. Overflows fail.
The contract cannot prove that the declared inventory is exhaustive or that
evidence is authentic; release pass remains disabled.

## Decisions and migration

Invalid contract/provenance produces exit 1 before analysis. A false safety,
policy, quality, or measured cost/latency gate yields fail; missing accounting or
undefined ratios do not erase it. Otherwise the result is inconclusive because
this integrity slice does not implement a statistical release gate. Comparison
returns exit 2 and `release_pass: false`, including for release-labeled inputs.
The report schema and analysis assumptions are documented in README and DESIGN.

V1 records cannot supply these guarantees and are rejected. Recollect against a
new frozen v2 manifest; do not inject defaults or convert user assertions into
measurement evidence. Synthetic examples are generated by `scripts/campaigns.py`.
