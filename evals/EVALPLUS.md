# Clean Codex CLI EvalPlus campaign

This layer prepares a small, reproducible comparison without making a model
call by default. It uses exactly HumanEval+ v0.1.10 and MBPP+ v0.2.0, with the
reviewed task subset `HumanEval/0`, `HumanEval/63`, `HumanEval/90`, `Mbpp/11`,
`Mbpp/12`, and `Mbpp/67`. Each task has two baseline and two router runs, for 24
top-level runs. The checked-in [manifest](evalplus/manifest.json) pins release
URLs, archive sizes and SHA-256 values, decompressed record and prompt hashes,
the exact schedule controls, and the router plugin version.

[Treatment isolation decision and repair result](EVALPLUS-ISOLATION.md) records
the current preflight limitation and the absence of formal benchmark results.

This is campaign preparation, not a benchmark result. The recorded repair
attempt started no formal slots. The original guarded runner's conditional
maximum is USD 44.24; the harness cannot establish account billing truth itself.

## Arms and schedule

Both top-level arms use `gpt-6-astra` at `xhigh` effort and the confined
`evalplus-task` permission profile. The baseline disables plugins, hooks, and native
multi-agent support. The router arm enables those capabilities and requires
exactly `codex-model-router` v0.1.3 in a separate `CODEX_HOME`. The first
repetition orders each pair baseline then router; the second reverses that order.
Runs are serial to avoid cross-run write or budget races.

Each command is built with:

```text
codex exec --ephemeral --ignore-rules --json --strict-config
```

It also supplies the model, reasoning effort, named permission profile, approval policy, current
task directory, feature isolation, and prompt on stdin. This follows the
[official Codex non-interactive mode documentation](https://learn.chatgpt.com/docs/non-interactive-mode),
including the documented terminal `turn.completed.usage` event. Raw JSONL and
stderr are written beside, never inside, the task worktree.

`--ignore-user-config` must not be used: installed plugin enablement lives in the
isolated arm's config, and ignoring it previously removed the treatment. The
authorized continuation uses `--ephemeral` and fresh per-slot homes, profiles,
and workspaces. CLI JSON streams retain observable parent/child evidence;
missing descendant usage is explicitly incomplete rather than inferred. Memory
injection/generation is disabled. Task instructions prohibit executing generated
code on the host.

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

The isolation helper creates two new homes and separate user profiles. It strips
inherited desktop task, pipe, SQLite, and Python context from child environments.
It disables bundled skills and remote plugins, and imports no user instructions,
memory, hooks, skills, or behavioral config. An explicit transport JSON contains
only `provider_id` and a `provider` object (connection/auth settings). Credentials
are never printed by the helper. Keep transport and home files private and out of
Git. An optional `--auth` points to an authentication file; no whole user home is
copied.

- Baseline home: no installed plugin.
- Router home: exactly one enabled `codex-model-router` v0.1.3 installation,
  installed by CLI from a fresh local marketplace containing the candidate
  package. The source, snapshot, and installed file-tree hashes must match.
  Its three event groups use a bounded benchmark adapter in the generated user
  config; duplicate plugin hook entries are disabled. The adapter preserves the
  complete original contracts and uses a minimal valid routing file only in the
  isolated home. See the [benchmark profile](BENCHMARK-PROFILE.md).

The harness loads only the new arm's config and ignores execpolicy rules. It
checks installed CLI flags before probes, since current documentation can describe
flags that CLI 0.144.1 does not support. The same command constructor and isolated
environment are used for probes and formal runs. Provider request and stream
retries are zero; maximum worker depth is one and concurrent workers are bounded.

```powershell
python evals/scripts/evalplus_isolation.py prepare-homes `
  --state-root C:\absolute\new-eval-state `
  --candidate C:\checkout\plugins\codex-model-router `
  --transport C:\private\transport.json
python evals/scripts/evalplus_hooks.py `
  --campaign-root $campaign --state-root C:\absolute\new-eval-state
python evals/scripts/evalplus_isolation.py check-grader `
  --campaign-root $campaign --image <image-containing-evalplus-0.3.1>
python evals/scripts/evalplus_isolation.py preflight `
  --campaign-root $campaign --state-root C:\absolute\new-eval-state
```

`evalplus_hooks.py` is a zero-model delivery check. It clones the exact per-slot
config/package without authentication, inspects the CLI `hooks/list` registry,
then sends each arm's first outgoing request to a rejecting loopback HTTP sink.
The sink implements no model and forwards nothing. It captures separate developer
messages from SessionStart and UserPromptSubmit, the routing block, skill catalog,
and native dispatch schema. A third capture simulates SubagentStart through the
CLI context injector without spawning a worker. All three contexts must exactly
match the original contracts, contain the complete schema-valid routing JSON,
and fit the 8,000-byte ceiling. A nonzero CLI exit is expected because the sink
rejects the request. No generated code runs. Its receipt is mandatory before
the paid treatment preflight and before either formal-run admission path.

The `evalplus_isolation.py preflight` command makes one diagnostic invocation per arm, sequentially, and refuses
to overwrite or repeat its evidence. These requests can incur cost and are not
benchmark slots. It saves raw CLI events, stderr, exact commands, timings, and
model reports. Baseline must report no skills or hook context. Router must report
its skill, excerpts of both SessionStart and UserPromptSubmit, routing config,
and native model/effort-selectable dispatch. Reports are model observations, not
proof that instructions mechanically enforce policy. Offline `debug prompt-input`
can show the skill catalog but is not proof of lifecycle hook delivery. Failed
or missing probes prohibit formal generation. Receipts bind candidate/config,
runner code, and raw evidence; changing these requires a new authorized preflight.
It additionally requires a fresh, exact write-probe artifact in each workspace;
the router must dispatch a native worker for its write. Saved artifact hashes are
revalidated before formal slots. Both arms expose only the bounded file editor,
with shell execution and task-tool networking disabled. See
[write capability](WRITE-CAPABILITY.md) for the profile, boundaries, and evidence.
The offline receipt additionally binds the CLI and Python executable hashes and
the benchmark profile/adapter. Presence-only receipts from earlier revisions
are insufficient. The bundled model catalog resolves worker examples without
contacting a model; backend entitlement is still a later live-validation check.

Both primary commands/configs set `model_supports_reasoning_summaries=true` as
well as exact `gpt-6-astra`/`xhigh`. This is required for CLI 0.144.1 to serialize
reasoning when Astra metadata is missing. The offline gate rejects either arm
unless the captured body contains that exact model and `reasoning.effort=xhigh`.
See [Astra request delivery](ASTRA-REASONING.md) for raw evidence and regression
checks; the flag's presence alone cannot satisfy admission.

The grader gate imports EvalPlus 0.3.1 in an immutable Docker image with networking
off, a read-only filesystem, dropped capabilities, and resource limits. It does
not run generated samples. Formal generation requires successful offline hook,
paid treatment, and grader receipts. WSL remains an accepted grading-result backend, but automated
grader admission currently implements Docker only.

## Authorized USD 50 soft-cap mode

For copilot-bridge, use the explicitly authorized soft path instead of a provider
budget guard. Both admission receipts are still required. No billing API or
ccusage process is called:

```powershell
python evals/scripts/evalplus_runner.py run --live --run-limit 24 `
  --campaign-root $campaign `
  --baseline-codex-home C:\absolute\new-eval-state\baseline `
  --router-codex-home C:\absolute\new-eval-state\router `
  --soft-budget-pricing evals/evalplus/astra-soft-pricing.json
```

The frozen Astra Standard short-context card (10/1/50 USD per million uncached
input/cached input/output tokens) applies to every distinct observed thread,
including router-selected models. `--prior-usage <jsonl>` can include earlier
diagnostic streams in admission. Current preflight usage is always included.
Before each serial slot, the runner stops if observed cumulative cost is at
least USD 50; an in-flight slot can exceed the soft cap. Legacy monetary
reservations remain schedule metadata and do not control this mode's admission.

Identical mirrored final usage is counted once per thread; conflicting totals
stop admission. Missing child totals remain in the inventory and set
`cost_complete: false`, with a lower-bound observed subtotal and no complete
cost estimate. An ephemeral CLI may not expose descendant usage, so this limit
cannot guarantee complete spend coverage. No paid slot is retried. Fresh slot
homes copy only isolated config/auth and the exact candidate package. A router
slot must show a separate worker thread; baseline must not show one.

## Legacy provider guard mode

A dedicated provider-side hard budget guard remains mandatory for the original
`run --live` entry point. A local warning,
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

Only after those prerequisites and both receipts are present should an operator
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
plus tests pass. Only attempted, infrastructure-valid samples enter the scoring
denominator; ordinary failed implementations remain failures. The four declared
historical infrastructure failures stay in the cost ledger but never count
toward pass@1. Unstarted samples are unscored and incomplete campaigns cannot
claim a full result. Report exclusions explicitly and do not infer a cost or
latency improvement from six tasks and two repetitions.
