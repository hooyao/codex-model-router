## Why

The existing evaluation slice validates synthetic offline records but cannot
prepare a clean live Codex CLI comparison. A narrow reviewed benchmark needs
reproducible task identity, arm isolation, budget refusal before paid work,
complete-session usage accounting, and a grading boundary that never executes
generated code directly on the host.

## What Changes

- Add a pinned EvalPlus manifest for HumanEval+ v0.1.10 and MBPP+ v0.2.0 with
  exactly six reviewed tasks and two repetitions per baseline/router arm.
- Add a standard-library runner that creates fresh task directories outside the
  agent workspace and emits dry-run commands by default.
- Require explicit live mode, separate authenticated Codex homes, clean feature
  isolation, exact run/token/cost reservations, and a fresh dedicated
  provider-side hard-budget receipt before each invocation.
- Preserve raw JSONL outside task worktrees and collect final CLI usage into an
  offline ledger that is incomplete unless every declared session is terminal
  and priced.
- Add a hash-bound Docker/WSL EvalPlus adapter contract and refuse direct host
  execution of generated code.
- Correct the stale `cases.json` SHA-256 in the existing evaluation manifest.

## Capabilities

### New Capabilities

- `evalplus-cli-harness`: Guarded preparation, collection, and isolated grading
  contract for the selected EvalPlus campaign.

### Modified Capabilities

- None. The plugin routing policy and runtime package are unchanged.

## Impact

Adds evaluation manifest, runner, tests, documentation, and OpenSpec artifacts.
Preparation may download two pinned public datasets. Default execution performs
no model calls, requires no API key, and cannot run generated code. A future
operator must independently configure provider budgeting and Docker/WSL before
using live or grading paths.
