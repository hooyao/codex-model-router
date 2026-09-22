# Clean Codex CLI EvalPlus campaign

This layer prepares a small, reproducible comparison without making a model
call by default. It uses exactly HumanEval+ v0.1.10 and MBPP+ v0.2.0, with the
reviewed task subset `HumanEval/0`, `HumanEval/63`, `HumanEval/90`, `Mbpp/11`,
`Mbpp/12`, and `Mbpp/67`. Each task has two baseline and two router runs, for 24
top-level runs. The checked-in [manifest](evalplus/manifest.json) pins release
URLs, archive sizes and SHA-256 values, decompressed record and prompt hashes,
the exact schedule controls, and the router plugin version.

This is campaign preparation, not a benchmark result. No paid model was called
while building or validating it. The conditional maximum campaign cost is USD
44.24; the harness cannot establish account billing truth by itself.

## Arms and schedule

Both top-level arms use `gpt-6-astra` at `xhigh` effort and a
`workspace-write` sandbox. The baseline disables plugins, hooks, and native
multi-agent support. The router arm enables those capabilities and requires
exactly `codex-model-router` v0.1.3 in a separate `CODEX_HOME`. The first
repetition orders each pair baseline then router; the second reverses that order.
Runs are serial to avoid cross-run write or budget races.

Each command is built with:

```text
codex exec --ephemeral --ignore-user-config --ignore-rules --json
```

It also supplies the model, reasoning effort, sandbox, approval policy, current
task directory, feature isolation, and prompt on stdin. This follows the
[official Codex non-interactive mode documentation](https://learn.chatgpt.com/docs/non-interactive-mode),
including the documented terminal `turn.completed.usage` event. Raw JSONL and
stderr are written beside, never inside, the task worktree.

## Safe preparation and dry run

Python 3.9+, Git, Codex CLI 0.144.1+, and network access to the two EvalPlus
GitHub release assets are needed for first-time preparation. Choose disjoint
campaign and dataset roots outside this repository and outside each other:

```powershell
$campaign = Join-Path $env:TEMP "evalplus-codex-campaign"
$assets = Join-Path $env:TEMP "evalplus-pinned-assets"
python evals/scripts/evalplus_runner.py validate-manifest
python evals/scripts/evalplus_runner.py prepare --campaign-root $campaign --asset-root $assets --download
python evals/scripts/evalplus_runner.py run --campaign-root $campaign --run-limit 24
```

`prepare` refuses an existing campaign root, verifies the downloaded bytes, and
creates one fresh Git directory per scheduled run. Only the public prompt and a
task instruction enter each task directory. Canonical solutions, tests, complete
records, grader assets, JSONL, and accounting artifacts stay outside it.

`run` is a dry-run preflight unless `--live` is explicitly present. Dry-run
output shows exact commands and reservations but never invokes `codex exec`.

## Live prerequisites and refusal rules

Live mode does not require this repository or harness to receive an API key.
The operator must configure two separate authenticated Codex homes, normally
using saved ChatGPT-managed CLI authentication:

- Baseline home: no `codex-model-router` installation.
- Router home: exactly one enabled `codex-model-router` v0.1.3 installation.

The harness checks both plugin inventories and `codex login status` before the
first call. The baseline command disables all plugins; the router command enables
plugins, hooks, and multi-agent support. User `config.toml` and execpolicy rules
are ignored in both arms.

A dedicated provider-side hard budget guard is mandatory. A local warning,
self-managed spreadsheet, or missing adapter is not a guard. The adapter config
contains a hash-pinned absolute executable, argv, and expected provider:

```json
{
  "schema_version": 1,
  "command": ["C:\\absolute\\provider-guard.exe", "check"],
  "executable_sha256": "<64 lowercase hex characters>",
  "expected_provider": "<provider identity>"
}
```

For every run, the adapter receives a one-line JSON request on stdin. It must
query a user-configured dedicated provider budget guard and return a fresh JSON
receipt with this exact shape:

```json
{
  "schema_version": 1,
  "source": "provider-budget-guard",
  "campaign_id": "evalplus-clean-codex-cli-v1",
  "guard_id": "<provider guard id>",
  "provider": "<provider identity>",
  "enforcement": "provider-side-hard-limit",
  "dedicated": true,
  "status": "active",
  "currency": "USD",
  "hard_limit_usd": "44.24",
  "remaining_usd": "44.24",
  "checked_at": "2026-09-22T12:00:00Z",
  "expires_at": "2026-09-22T12:10:00Z"
}
```

The check must be no more than 15 minutes old, unexpired, at or below USD 44.24,
and able to cover the next reservation. The harness distributes exactly
44,240,000 microdollars across 24 slots and reserves 200,000 tokens per slot,
with ceilings of 24 attempts and 4,800,000 reserved tokens. It marks a slot
attempted before invoking Codex. A failed attempt is retained and cannot be
retried; the campaign stops.

Only after those prerequisites are independently configured should an operator
use the explicit live form:

```powershell
python evals/scripts/evalplus_runner.py run `
  --campaign-root $campaign `
  --live `
  --baseline-codex-home C:\absolute\codex-baseline `
  --router-codex-home C:\absolute\codex-router `
  --provider-guard-config C:\absolute\provider-guard.json `
  --run-limit 1
```

## Offline usage and cost ledger

The collector reads preserved CLI JSONL only. It never invokes `ccusage`. Supply
an explicit inventory of every controller, worker, retry, verification, and
abandoned session, plus a separately pinned price table. Each declaration has
`session_id`, `role`, and `model`; each model price has input, cached-input, and
output USD per million tokens. Repeat `--raw` for every stream:

```powershell
python evals/scripts/evalplus_runner.py collect `
  --raw C:\campaign\raw\controller.jsonl `
  --raw C:\campaign\raw\worker.jsonl `
  --sessions C:\campaign\sessions.json `
  --pricing C:\campaign\pricing.json `
  --output C:\campaign\cost-ledger.json
```

Only final `turn.completed.usage` objects are counted. Discovered but undeclared
sessions, declared sessions without terminal usage, duplicate final usage, or
missing model prices force `cost_complete: false` and `estimated_cost_usd: null`.
Reasoning output is recorded but not charged twice because it is a subset of
output tokens in the CLI usage event. The ledger binds every raw stream, the
session declaration, and the price table by SHA-256. A complete estimate is
still an API-price estimate, not authenticated provider billing.

## Isolated EvalPlus grading contract

Generated code must never be run directly on the host. Grading requires Docker
or WSL isolation, no network, a read-only mount for the pinned dataset assets,
and a read-only sample input. EvalPlus 0.3.1 supplies the pinned HumanEval+ and
MBPP+ dataset versions used here. If Docker, WSL, or EvalPlus is absent, create
and retain the request but do not grade:

```powershell
python evals/scripts/evalplus_runner.py make-grader-request `
  --campaign-root $campaign `
  --backend docker `
  --output C:\campaign\artifacts\grader-request.json
```

An isolated adapter runs the exact six-task subset and returns one record for
every scheduled run with `run_id`, `task_id`, `base_pass`, and `plus_pass`. Its
result binds to the request SHA-256 and identifies the runtime; Docker identities
must include an immutable `@sha256:` image digest. Verify it offline with:

```powershell
python evals/scripts/evalplus_runner.py verify-grader-result `
  --request C:\campaign\artifacts\grader-request.json `
  --result C:\campaign\artifacts\grader-result.json
```

The harness has no host-execution option: `--backend host` is accepted only so it
can produce an explicit refusal. A run passes only when both EvalPlus base and
plus tests pass. Timeouts, blockers, errors, and missing completions remain
failures in the 24-run denominator. Report per-arm pass rates and paired
differences; do not infer a cost or latency improvement from six tasks and two
repetitions.
