# Offline evaluation integrity

This is the first fixture and integrity slice for a future release benchmark.
It validates and compares supplied baseline/router campaigns offline. No model
is called. All bundled campaigns are synthetic; their numbers are test inputs,
not evidence of router quality, cost savings, or latency improvement.

There is exactly one executable task: `small-edit`. Eight other entries in
`cases.json` remain draft scenarios. The mixed suite and `experiment.json` remain
draft. The `release` label on the small-edit case means its artifact can be
graded reproducibly; it does not make the campaign ready for release claims.

## Policy and task

The baseline has the router disabled and may perform the task directly. The
router variant must assign business work to a bounded worker, including simple
reads and typo edits. Delegation overhead is expected and included in elapsed
time/cost. Controller business actions are a policy failure. Missing dispatch
is a terminal blocked run retained in the scheduled denominator.

For `small-edit`, copy `fixtures/small-edit/initial/` to a fresh candidate
directory and supply the exact prompt from `fixture.json`. Correct `plguin` to
`plugin` in README.md and preserve all other bytes and files. The exact-tree
oracle checks every file hash and directory, including extra empty directories.
It rejects unchanged results, unrelated edits, extra/missing files, symlinks,
Windows reparse points, and hash mismatches. It never runs candidate code.
The reference tree and grader must remain outside the worker's assigned scope;
this public fixture is not a hidden holdout.

## Reproduce synthetic campaigns

Python 3.9 or later and the standard library are sufficient. Run from the
repository root; the output directory must not exist:

```powershell
python evals/scripts/campaigns.py --output evals/generated
python evals/scripts/evaluate.py validate-cases --cases evals/cases.json
python evals/scripts/evaluate.py validate-manifest --manifest evals/experiment.json
python evals/scripts/evaluate.py validate-records --records evals/generated/complete-draft.runs.jsonl --manifest evals/generated/complete-draft.manifest.json
python evals/scripts/evaluate.py compare --records evals/generated/complete-draft.runs.jsonl --manifest evals/generated/complete-draft.manifest.json
python -m unittest discover -s evals/tests -v
```

The generator copies the sole fixture and writes ten deterministic campaigns,
including missing cost, safety/quality failure with missing cost, unchanged
artifacts, controller edits, cost/latency regression, and zero denominators.
`expected.json` records expected statuses. Tests compare all campaigns and verify
that a second generation produces identical bytes. The former v1
`runs.example.jsonl` is replaced by these complete, reproducible examples.

For standalone grading, obtain the candidate's canonical tree digest (an
independent harness should freeze this at collection time), then run:

```powershell
python -c "from pathlib import Path; from evals.scripts.fixture import tree_snapshot, tree_digest; print(tree_digest(tree_snapshot(Path('evals/fixtures/small-edit/reference'))))"
python evals/scripts/evaluate.py grade --fixture evals/fixtures/small-edit/fixture.json --candidate evals/fixtures/small-edit/reference --sha256 <digest>
```

Exit codes: validation or successful standalone grading returns **0**; invalid
input returns **1**; comparison decisions and failed grading return **2**.
Comparison intentionally cannot return release pass in this milestone. CLI
argument errors also return 2 with usage text rather than a comparison report.

## Contract and emitted metrics

[CONTRACT.md](CONTRACT.md) documents the strict v2 object shapes. V1 inputs are
rejected. Every slot in the frozen case/repetition/variant grid must have exactly
one terminal record. Both variants must share one pair ID per case/repetition;
pair IDs cannot be reused. Run IDs and session ownership are unique throughout
the supplied campaign. Cases, experiment bytes, fixture inputs, result trees,
environment controls, and evidence references are checked before analysis.

The comparison emits only these measures:

| Output | Meaning |
| --- | --- |
| `pairs` | Complete scheduled baseline/router pairs |
| `quality_pass_rate` | Mean effective pass indicator per variant; oracle failure overrides reported success |
| `quality_difference_router_minus_baseline` | Paired mean difference with exploratory percentile bootstrap interval |
| `latency_ratio_router_over_baseline` | Ratio of mean elapsed milliseconds with paired bootstrap interval |
| `cost_ratio_router_over_baseline` | Ratio of mean per-run USD totals with paired bootstrap interval, when accounting is complete |
| `cost_complete` | All declared ledgers passed structural, provenance, inventory, and total checks |
| `safety_by_category` | Per-variant counts for each declared safety flag |
| `router_policy_failures` | Router records reporting controller work or completed work without worker execution |
| `oracle_results` | Exact-tree diagnostics by run ID |

Reports also include gates, failed/missing gate names, status, and release
blockers. `quality_score`, retries, and delegation counts are validated record
fields, not separately aggregated report metrics. Median/p95 time, token usage,
routing accuracy, rubric confidence intervals, and context overhead are not
emitted. No performance or savings improvement is established here.

Any demonstrated gate failure yields `fail`, even if cost is absent or a ratio
is undefined. With no demonstrated failure, missing evidence or release
readiness yields `inconclusive`. Zero baseline means and overflowing ratios have
null intervals and explicit reasons. No epsilon denominators or discarded
resamples are used. Draft/synthetic campaigns never produce a release pass;
release-labeled inputs remain blocked by the missing statistical release gate.

## Accounting observations

```powershell
python evals/scripts/evaluate.py ccusage --thread-id <id> --output cost-observation.json
```

This optionally invokes the locally installed ccusage command, requires exactly
one matching session per ID, and records finite non-negative observations. It
always emits `verified: false`, `cost_complete: false`, and `cost_usd: null`,
returning 2. There is no `--provider-verified` override. No cost collection is
needed to run the tests; importer subprocesses are mocked.

Complete supplied accounting requires a separate hash-bound session ledger that
reconciles with the trace and total. This validates the contract, not provider
authenticity or the truth of the declared session inventory. A future trusted
harness must establish those facts before release cost claims.

## Remaining work

Live hook/dispatch observation, authenticated billing, append-only collection,
preregistration, representative independent tasks, hidden holdouts, and a reviewed
statistical release gate are not implemented. The eight draft scenarios need
their own fixtures and graders. See [DESIGN.md](DESIGN.md) for analysis limits.
