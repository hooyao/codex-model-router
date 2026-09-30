# Critical analysis of the observed live campaign

Run ID: `router-scenarios-live-20260923T031600Z`

The live explicit-skill campaign completed all four required scenarios. Exact
business trees and required route/process checks passed 4/4. The campaign does
not pass overall because the automatic hook activation gate and worker identity
contract failed. This is observed runtime evidence, not a synthetic result and
not a comparative performance claim.

## Runtime and platform

- Automatic activation failed. `raw/activation.jsonl` records repeated
  `ignoring hooks` warnings, no `ROUTE: DIRECT`, and no automatically created
  routing config. The campaign therefore validates the explicit-skill path only.
- CLI `workspace-write` could not launch PowerShell and returned
  `CreateProcessWithLogonW failed: 2`. The four scenario runs used the CLI's
  unsandboxed mode in disposable nested Git repositories. This prevents any
  claim that the normal workspace sandbox was exercised.
- The host's first `python.exe` on `PATH` is an incomplete Python 3.9 install
  that fails before importing `encodings`. Because the Windows hook command is
  intentionally `python ...`, init and plugin tests fail under the default
  `PATH`; the unchanged 64-test plugin suite passes when the bundled Python
  3.12 directory is prepended. This is a concrete deployment prerequisite, not
  a routing-result failure.
- Native collaboration itself worked: persisted rollout metadata proves
  distinct child sessions, including real three-way overlap and a distinct
  author/reviewer pair. The compact `codex exec --json` stream omitted spawn
  arguments and reported empty receiver lists for waits, so persisted rollouts
  were required for trustworthy topology evidence.

## Routing and protocol

- Artifact ownership and topology behaved as intended: investigation used two
  serial children; escalation used four dependent children; parallel work used
  three write-disjoint children; architecture used distinct author and reviewer
  children in dependency order.
- Controller receipts were not reliable identity telemetry. The investigation
  controller read ambient `CODEX_SESSION_ID` and claimed both workers shared the
  parent ID, while persisted metadata shows two distinct child thread IDs.
- Persisted turn contexts identify the actual child runtime as
  `gpt-5.6-sol/high`, but route lines and task IDs claimed `gpt-5/medium` or used
  `model-unexposed`/`effort-unexposed` placeholders. Those placeholders violate
  the deterministic naming contract, so identity-contract validation fails.
  Runtime metadata must be surfaced to the dispatcher before the plugin can
  make or audit model-routing decisions faithfully.
- Receipt chaining was observable, but the campaign demonstrates information
  transfer rather than context deletion. Child rollouts still contain broad
  runtime/system context, so the product may claim logical minimal packets only.

## Benchmark design and measurement

- The pre-existing benchmark had no live collector. `live_evidence.py` now
  derives identities, timing, overlap, tokens, exact-tree results, and hashes
  from raw transcripts and persisted sessions instead of copied expected traces.
- The live run exposed a contradictory oracle: the architecture prompt listed
  `schema_version` first, while the exact-byte reference required `decision`
  first. The reference and pinned hashes were corrected to match the prompt,
  after which the affected case was rerun in a fresh repository. The post-fix
  author/reviewer run produced the corrected 209-byte reference hash and passed.
- The observed campaign is selective-only. The frozen synthetic evaluator
  requires all three treatments, so live evidence has a separate schema. No
  direct or mandatory-delegate live baseline was run.
- Token and cache counts are observed terminal telemetry. Measured USD was not
  exposed, so cost remains `unavailable`; pricing was not inferred. Likewise,
  observed overlap does not establish latency savings without a serial live
  baseline.
- Failed automatic activation and protocol checks remain in the denominator;
  correct artifacts do not convert them into passes.

## Product hypothesis

- Supported: native workers can execute the intended serial, parallel, receipt,
  and independent-review shapes while preserving exact artifacts. The parallel
  case has an interval where all three worker sessions overlap.
- Not established: automatic routing, routing accuracy, model/effort selection,
  token or cost savings, and latency improvement. Each scenario explicitly
  instructed the desired route because automatic hooks failed.
- The direct-to-delegate result proves protocol compliance under a staged
  prompt, not spontaneous scope discovery: the full prompt explicitly required
  the transition. A future routing-accuracy evaluation should hide the later
  dependency until the controller encounters fixture evidence.
- The direct-small control was not part of this minimum four-case campaign, so
  the hypothesis that the router avoids delegation overhead on trivial work
  remains untested live.

See `live-results.json`, `SUMMARY.md`, `raw/*.jsonl`, and
`session-evidence/*/session-index.json` for the hash-bound evidence.
