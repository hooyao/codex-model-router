# Flipt ECR credential lifecycle v1

This is a separately versioned local follow-on task,
`flipt-ecr-credential-lifecycle-v1`. It is not an official SWE-bench task or
grade. Both arms start from the original Flipt source plus the hash-bound
pair-01 Astra baseline patch. The old fixture and pair-01 evidence are retained.

The issue in `assets/instruction.md` states externally observable lifecycle
requirements. It does not prescribe a cache, synchronization algorithm, stage
split, or model. The implementation problem combines expiry, registry-specific
authorization selection, concurrent refresh outcomes, independent caller
cancellation, and recovery. Focused regression tests and maintainer documentation
are also required. Independent review must decide whether this is a useful
discriminator; naming a task or counting its tests does not establish difficulty.

## Frozen inputs

`config.json` binds the upstream tarball, original source tree, seed patch,
seed tree, prompt, combined oracle, and oracle configuration. It retains the
reviewed installed plugin identity/hook hashes, CLI binary/capability pins,
2026-09-24 Standard rate card, 50-minute limit, and $10 Astra safety budget.
It adds Go executable hashes, versions, shared cache paths, and the seed's
`go.sum` hash. The source archive is reused by a hash-bound relative reference;
it is not modified or duplicated.

| Input | SHA-256 or Git tree |
| --- | --- |
| Original source tree | `20121a7049c58571664b87989d1fc7bab8562884` |
| Pair-01 baseline seed patch | `3d824219a77b11d8b6f6ec506403daad092594d07c9a697a3c2280a2c935c74b` |
| Lifecycle seed tree | `3354dd97a389bac41536cc1cfa4ffe4861912bc8` |
| Combined prompt | `f136aa48d15e263c6b07037adf5a466d9799f2194526c217946069dc03f86acd` |
| Combined oracle patch | `45951edfb7dc5a136cea8cac7150c7e64f52e1745afebf8ef807a7da37b6c680` |
| Positive control patch | `56e083d4e85cb48f08521bac9fdf4fa55b391518b8eae1801bc99bf9c4a7c103` |

`prepare.py` creates a clean seed and byte-equivalent clean baseline/treatment
trees. Oracle files and the positive implementation remain outside arm
workspaces. This is evaluator placement and an instruction boundary, not a
claim that the runtime prevents reads outside a workspace.

## Oracle

`assets/oracle.patch` restores the previous public task's hidden regression
tests against the new seed and adds the new tests in `oracle/`. Lifecycle tests
call the public ECR APIs through an AWS SDK loopback endpoint with dummy test
credentials; they do not depend on a candidate's new internal fields or methods.
The existing low-level decoding seam remains covered by the retained tests.
Static credentials and AWS option-level provider reuse are also checked.

The oracle checks reuse before expiry, refresh after expiry, endpoint selection,
registry isolation, concurrent success and failure, independent registry progress,
waiter and initiating-caller cancellation, already-cancelled calls, failed-refresh
retry without stale fallback, missing expiry, malformed data, and static behavior.
It allows a provider to retain correctly scoped records for several registries
from one response. It does not require a specific number of internal locks,
goroutines, fields, or client instances.

`grade.py` first runs the existing local replay grader, then requires
`go test -race -count=3 -run TestLifecycle ./internal/oci ./internal/oci/ecr`.
The supplemental `lifecycle-quality.json` binds both results and is the combined
quality gate. The old collector only reads `grade.json`; reviewers must also
check `lifecycle-quality.json` and the candidate's regression tests/documentation
before claiming quality parity. Oracle failures remain failures.

Expiry tests use 1.5-second tokens and an 80-millisecond post-expiry allowance.
Concurrent tests hold the loopback request open and use a 100-millisecond
join interval; prompt cancellation/independent progress have one-second bounds.
The repeated race run checks scheduling stability on this host. Severely
oversubscribed hosts can still make wall-clock tests noisy; retain the outputs
and investigate an observed failure before retrying. No model call is involved
in any oracle control.

## Preparation and G15 review

Preparation evidence is in
`../live/paired-benchmark-20260929/lifecycle-v1-prep/`. `preparation.json`
records the arm workspaces and empty initial diffs. `negative-final/` and
`positive-final/` contain the frozen combined control grades. The earlier
`negative-grade/` and `positive-grade/` retain the first successful calibration
before loosening a per-registry request-count assertion to allow multi-record
caching. The final oracle is identified by the hashes above.

Review the issue's observable requirements, oracle coverage and scheduling
bounds, controls, task difficulty, and the natural division between concurrency
design/implementation and bounded test/documentation work. The positive control
is a feasibility check, not a model solution or evidence of an Astra advantage.
The initial seed and positive implementation both pass the seed's own package
regression tests. G14 makes no difficulty, cost, or latency claim.

To reconstruct another fresh pair without paid execution:

```powershell
py -3 evals/paired_selective_lifecycle_v1/prepare.py --destination evals/live/paired-benchmark-20260929/lifecycle-fresh
```

## G16 invocation after G15 review

The existing calibrated-controller artifact is still `CALIBRATION_FAILED` and
was captured on the old CLI. The explicit `--negative-treatment-controller`
option is therefore retained to select Sol/low without rewriting calibration
evidence. The current CLI/plugin binding probe passed with zero model calls.
This makes a fresh empirical comparison runnable; it does not establish
controller calibration, plaintext packet scope, or a successful selective route.
An actual Astra/xhigh hard-kernel child and bounded easy work must be observed.

Run from the repository root. The following uses the already prepared clean
pair and requires the hash-pinned CLI, Go toolchain, installed plugin, and
calibration evidence described above. Set the four `CODEX_BENCH_*` environment
variables to paths on the current host before running it. Stop on a failed arm
or route; retain its evidence.

```powershell
$fixture = 'evals/paired_selective_lifecycle_v1'
$prep = 'evals/live/paired-benchmark-20260929/lifecycle-v1-prep'
$out = 'evals/live/paired-benchmark-20260929/lifecycle-pair-01'
$codexCli = $env:CODEX_BENCH_CLI
$goBin = $env:CODEX_BENCH_GO_BIN
$goCacheRoot = $env:CODEX_BENCH_GO_CACHE_ROOT
$pluginRoot = $env:CODEX_BENCH_PLUGIN_ROOT
if (-not $codexCli -or -not $goBin -or -not $goCacheRoot -or -not $pluginRoot) { throw 'Set all CODEX_BENCH_* paths first' }
$shared = @('--live', '--config', "$fixture/config.json",
  '--codex-cli', $codexCli, '--go-bin', $goBin, '--go-cache-root', $goCacheRoot)
py -3 evals/scripts/run_paired_arm.py @shared --arm baseline --workspace "$prep/baseline-workspace" --evidence "$out-baseline-evidence"
py -3 evals/scripts/run_paired_arm.py @shared --arm treatment --workspace "$prep/treatment-workspace" --evidence "$out-treatment-evidence" --plugin-root $pluginRoot --calibration evals/live/paired-flipt-matched-20260925/calibration/calibration.json --negative-treatment-controller gpt-6-sol/low --dispatch-audit
```

Grade both captured candidates outside their workspaces:

```powershell
py -3 "$fixture/grade.py" --seed "$prep/seed" --arm "$prep/baseline-workspace" --candidate-patch "$out-baseline-evidence/candidate.patch" --grade-workspace "$out-baseline-grade"
py -3 "$fixture/grade.py" --seed "$prep/seed" --arm "$prep/treatment-workspace" --candidate-patch "$out-treatment-evidence/candidate.patch" --grade-workspace "$out-treatment-grade"
py -3 evals/scripts/collect_selective_pair.py --config "$fixture/config.json" --baseline "$out-baseline-evidence" --treatment "$out-treatment-evidence" --baseline-grade "$out-baseline-grade/grade.json" --treatment-grade "$out-treatment-grade/grade.json" --output "$out-collected.json"
```

Read both supplemental lifecycle quality receipts even when the stock collector
accepts the ordinary grades. Preserve any collector failure without broadening
selectors or changing pinned evidence. The existing collector can still reject
follow-up-turn lifecycle or repeated terminal usage-event shapes; the pair-01
diagnostic is precedent, not permission to ignore integrity checks.

One successful pair is exploratory. Quality parity and total parent-plus-child
USD are primary; repeated interleaved matched pairs are required before treating
latency differences as stable. Use fresh workspaces with the same seed, prompt,
environment, and measurement boundaries for each pair. Packet-scope evidence
remains `UNKNOWN` where the runtime provides only opaque transport.

## Treatment dispatch authorization v3

Pair 01 diagnosed an instruction gap. The Sol/low rollout included a developer
instruction that permits spawning only when the user or project instructions
explicitly ask for delegation. The shared task note said only "If you delegate";
the controller reported delegation unavailable and implemented the hard kernel
itself. The successful hook-enabled smoke had the same developer instruction,
but its user prompt explicitly authorized a native spawn.

For pair 02, `config.json` had `treatment_execution.version` set to
`lifecycle-v1-dispatch-authorization-v3`. The runner appends its authorization
suffix to the treatment's submitted user prompt. The frozen task text and its
`prompt.combined_sha256` remain identical for both arms. New `run.json` records
both `task_prompt_sha256` and the hash of the full submitted prompt, plus the
execution version; the collector reconstructs the latter and rejects any
unexpected suffix or hash. This changes treatment execution instructions, so
pair 01 remains diagnostic and must not be counted as a matched v3 run. The
authorization names the exact hard-worker identity for which the launcher
validated a source-backed dispatch contract. A different worker requires its
own complete contract before spawning. The authorization does not choose a
stage split or prove an Astra child; native lineage and selectors still have
to be observed.

For pair 02, fresh workspaces and a new evidence prefix were used.
The same commands above were run with `$prep` set to the new preparation directory
and `$out` set to `evals/live/paired-benchmark-20260929/lifecycle-pair-02`.
Both arms used the v3 `config.json` with the same Go and CLI pins.

One bounded hook-enabled canary for v3 is in
`../live/paired-benchmark-20260929/lifecycle-v1-dispatch-fix/`. The first
authorization-only attempt selected `DELEGATE` but invoked the dispatch
validator with an empty record, so it retained a failed receipt and did not
spawn. The single retry prevalidated an exact child contract and observed one
native Astra/xhigh child with a matching completion marker. This proves the
CLI and hooked session can dispatch under the versioned authorization; the
next full pair must still prove the actual hard-kernel stage, stage plan,
packet scope, quality, and cost with its own evidence.

## V4 selective stage plan and next matched pair

Pair 02 is retained as v3 diagnostic evidence. Reconciliation of its raw
parent and child rollouts removes one repeated child token snapshot: the
observed 57 distinct responses reconcile to $3.7215083 at the pinned Standard
rates, versus the complete $2.0785415 Astra baseline. The reduced accounting
receipt is `../live/paired-benchmark-20260929/lifecycle-v4-prep/pair-02-usage-reconciled.json`.
The same directory retains `config-v3-recovered.json`, reconstructed from the
saved pair-02 treatment prompt and unchanged frozen fields; its SHA-256 matches
the original pair-02 config hash in both run receipts.
Its Sol/low calibration remains failed, hook provenance remains `UNKNOWN`, and
its single Astra-owned stage included documentation. It is not a qualifying
selective pair or evidence of a stable cost or latency difference.

The treatment execution version is now `lifecycle-v1-selective-stage-plan-v4`.
The suffix SHA-256 is
`c548cdd203f1a7eee02f831c07c66f4cb72d3ca529db9f5a479935be0ec41c9b`;
the runner checks this digest before launch. The config SHA-256 is
`1795de12140e7cead20a3ce225d17252ef5b5a359de3d987d9e8d5ab2b67a8bb`.
The frozen task SHA-256 stays
`f136aa48d15e263c6b07037adf5a466d9799f2194526c217946069dc03f86acd`;
the full v4 treatment prompt SHA-256 is
`05ff72b072bab0ea74bd86742f97b53a6c8c90fddb10975a05f290dd81073d73`.
The baseline still receives only the frozen task. The v4 suffix requires:

| Stage | Owner | Dependencies | Exclusive write scope | Self-check |
| --- | --- | --- | --- | --- |
| `hard-kernel` | Exact Astra/xhigh worker | None | `ecr.go`, `ecr_test.go`, `options.go`, `options_test.go` at the paths named in the suffix | Focused code tests |
| `maintainer-docs` | Separately direct-gated Sol/low controller or cheapest valid easy worker | None | `internal/oci/ecr/README.md` | Compare with the frozen task |
| `verify-and-synthesize` | Separately direct-gated Sol/low controller or cheapest valid easy worker | Both prior stages | None | Run `go test -count=1 ./internal/config ./internal/oci ./internal/oci/ecr`; compare docs with final code and report results |

Every stage must record owner, dependencies, bounded context budget,
acceptance, self-check, and exact write scope in
`.codex-model-router/eval-stage-plan.json` before dispatch. Worker stages also
commit their exact packet hash and byte length. The collector rejects a v4 plan
that omits any of these stages or gives Astra the documentation file. Packet
contents and shell writes remain `UNKNOWN` unless separately evidenced; the
plan and observed patch paths alone do not prove semantic scope.

After independent review of the v4 suffix and accounting change, run one fresh
matched pair from the repository root, with the same `CODEX_BENCH_*` paths set
as above:

```powershell
$fixture = 'evals/paired_selective_lifecycle_v1'
$prep = 'evals/live/paired-benchmark-20260929/lifecycle-v4-pair-03-prep'
$out = 'evals/live/paired-benchmark-20260929/lifecycle-pair-03'
$codexCli = $env:CODEX_BENCH_CLI
$goBin = $env:CODEX_BENCH_GO_BIN
$goCacheRoot = $env:CODEX_BENCH_GO_CACHE_ROOT
$pluginRoot = $env:CODEX_BENCH_PLUGIN_ROOT
if (-not $codexCli -or -not $goBin -or -not $goCacheRoot -or -not $pluginRoot) { throw 'Set all CODEX_BENCH_* paths first' }
py -3 "$fixture/prepare.py" --destination $prep
$shared = @('--live', '--config', "$fixture/config.json",
  '--codex-cli', $codexCli, '--go-bin', $goBin, '--go-cache-root', $goCacheRoot)
py -3 evals/scripts/run_paired_arm.py @shared --arm baseline --workspace "$prep/baseline-workspace" --evidence "$out-baseline-evidence"
py -3 evals/scripts/run_paired_arm.py @shared --arm treatment --workspace "$prep/treatment-workspace" --evidence "$out-treatment-evidence" --plugin-root $pluginRoot --calibration evals/live/paired-flipt-matched-20260925/calibration/calibration.json --negative-treatment-controller gpt-6-sol/low --dispatch-audit
py -3 "$fixture/grade.py" --seed "$prep/seed" --arm "$prep/baseline-workspace" --candidate-patch "$out-baseline-evidence/candidate.patch" --grade-workspace "$out-baseline-grade"
py -3 "$fixture/grade.py" --seed "$prep/seed" --arm "$prep/treatment-workspace" --candidate-patch "$out-treatment-evidence/candidate.patch" --grade-workspace "$out-treatment-grade"
py -3 evals/scripts/collect_selective_pair.py --config "$fixture/config.json" --baseline "$out-baseline-evidence" --treatment "$out-treatment-evidence" --baseline-grade "$out-baseline-grade/grade.json" --treatment-grade "$out-treatment-grade/grade.json" --output "$out-collected.json"
```

Inspect both `lifecycle-quality.json` receipts and the v4 stage plan after
collection. Stop on a failed arm or route and retain its evidence. The explicit
negative treatment controller will still be labeled `CALIBRATION_FAILED` until
controller calibration is separately established; do not turn an `UNKNOWN`
hook or packet-scope result into verified routing. Repeated interleaved matched
runs are required before claiming stable latency or a selective-routing cost
advantage.
