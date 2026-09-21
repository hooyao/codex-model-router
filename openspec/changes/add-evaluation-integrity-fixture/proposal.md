## Why

The shipped pure-orchestrator policy conflicts with the original MVP delegation
gate and direct-work benchmark expectations. The offline evaluator also accepts
ambiguous identities and unsupported accounting claims, while its design describes
checks that are not implemented.

## What Changes

- Supersede the direct-controller and recursive-worker exceptions in
  `add-router-mvp`; preserve that change as historical context.
- **BREAKING**: replace evaluation schema v1 with a strict, versioned v2 contract
  for manifests, cases, records, provenance, and accounting evidence.
- Reject duplicate identities, malformed thresholds, unsafe paths, and altered
  evidence; preserve demonstrated failures when other evidence is unavailable.
- Make ccusage an unverified observation importer with no verification override.
- Add exactly one fixture-backed task, `small-edit`, with an exact-tree oracle
  and reproducible synthetic campaigns. This is the first release-benchmark
  integrity slice, not a release effectiveness or savings claim.
- Align documentation with emitted metrics and explicitly block draft/synthetic
  release passes.

## Capabilities

### New Capabilities

- `evaluation-integrity`: Strict offline campaign identity and decision contract.
- `small-edit-fixture`: Reproducible task and exact-tree grading boundary.

### Modified Capabilities

- `controller-routing-context`: Require worker execution even for simple tasks;
  controller verification means packet validation only.
- `subagent-worker-contract`: Workers execute assigned work without discovery or
  recursive delegation, including verification assignments.

## Impact

Changes evaluation Python code, fixtures, deterministic tests, and documentation.
Existing v1 records require recollection, not default-filled migration. Plugin
runtime behavior already follows the superseding policy and remains unchanged.
No live models, new dependencies, installed-plugin mutations, or savings claims.
