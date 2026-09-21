# Codex Model Router evaluation

This evaluation compares the same task suite with the router disabled (`baseline`)
and enabled (`router`). It is designed to answer four separate questions:

1. **Task quality:** does the router preserve the baseline's user-visible result?
2. **API cost:** does model selection reduce measured provider cost?
3. **Latency:** does safe parallelism or cheaper routing reduce wall-clock time?
4. **Operational safety:** does the router avoid bad delegation, scope leaks,
   write conflicts, recursive delegation, and unrecovered partial failures?

Do not combine these into one score. A router run is a win only if quality is
non-inferior and the claimed cost or latency improvement is backed by complete,
provider-valid measurements.

## Evaluation matrix

The cases in `cases.json` are currently a **draft scenario matrix**, not yet a
release benchmark. They deliberately cover different routing decisions:

| Dimension | Cases | What it catches |
| --- | --- | --- |
| Direct work | `simple-direct`, `small-edit` | Over-delegation overhead and regressions |
| Parallel read work | `parallel-research` | Missed parallelism and synthesis quality |
| Dependency ordering | `sequential-implementation` | Workers started before prerequisites |
| Ambiguity | `ambiguous-requirements` | Premature low-tier routing and clarification quality |
| Risk boundary | `security-review` | Under-routing high-risk judgment |
| Partial failure | `worker-timeout-recovery` | Retry, escalation, and preservation of successful work |
| Workspace safety | `write-conflict-avoidance` | Overlapping writes and unrelated changes |
| Adversarial scope | `prompt-injection-boundary` | Preservation of user and workspace constraints |

The experiment contract is in `experiment.json`. It freezes the denominator,
variants, repetitions, controls, and analysis thresholds before runs begin.
The current draft asks for 20 paired repetitions per case; this is a planning
default, not a statistical power claim. A production benchmark must replace
these prompts with fixture-backed tasks, executable oracles, hidden holdouts,
and a preregistered power analysis.

Randomize baseline/router order within task and time blocks, with identical
model availability, repository snapshot, prompt, approval settings, and fresh
conversation state. Never omit a failed or timed-out scheduled run.

## Run records

The live Codex harness is intentionally outside this repository because it is
account- and client-specific. After each run, append one JSON object to a JSONL
file. The required shape is validated by `scripts/evaluate.py`:

Each run record must include a schema version, experiment/pair/run IDs, typed
outcomes, a route trace, environment provenance, and a cost coverage ledger.
See `runs.example.jsonl` for the minimum shape. Duplicate identities, unknown
cases, missing planned pairs, invalid types, and non-finite measurements are
invalid—not silently excluded.

`quality_score` is a rubric score from 0 to 4 and `passed` means all
case-specific acceptance criteria passed. Keep the final artifact, validation
commands, and evaluator judgment in `evidence`; token counts alone are not a
quality measure.

## Cost collection

The helper can import a cost for a thread using:

```powershell
python evals/scripts/evaluate.py ccusage --thread-id <id> --output cost.json
```

This invokes `ccusage session --id <id> --json --offline`. It now requires an
exact session-ID match and supports repeated `--thread-id` values for worker
sessions. Because the installed `ccusage` output is Claude-oriented, the
adapter only marks cost complete when `--provider-verified` is explicitly
provided and all controller/worker/retry coverage is asserted. Otherwise the
run remains usable for quality/latency analysis but cannot support a cost claim.

## Analysis

Validate and compare paired records with:

```powershell
python evals/scripts/evaluate.py validate-manifest --manifest evals/experiment.json
python evals/scripts/evaluate.py validate-records --records evals/runs.jsonl
python evals/scripts/evaluate.py compare --records evals/runs.jsonl --manifest evals/experiment.json
```

The comparator rejects incomplete planned runs and reports `pass`, `fail`, or
`inconclusive`. It uses paired bootstrap intervals for quality, cost ratio, and
latency ratio; point estimates alone cannot establish non-inferiority. The
bundled one-pair example is only a record-schema smoke test:

```powershell
python evals/scripts/evaluate.py validate-records --records evals/runs.example.jsonl
```

The comparison reports quality pass rate, median/p95 wall time, median cost,
cost completeness, delegation mistakes, and paired deltas. Default release
gates are intentionally conservative:

- the complete planned denominator must be present;
- the lower bootstrap bound for router quality must clear the declared
  non-inferiority margin and the router must meet its absolute quality floor;
- the upper bootstrap bound for the cost ratio must clear the declared cost
  threshold, with complete controller/worker/retry data;
- the upper bootstrap bound for latency ratio must clear the non-regression
  threshold;
- every router safety category must have zero critical incidents.

The script returns a non-zero exit code when a gate is not met. It does not
claim an improvement when there are too few paired samples or when cost data
is unavailable.

## Industrial benchmark portfolio

There is no single public benchmark that measures decomposition, model/effort
routing, scheduling, recovery, synthesis, safety, and cost together. Use a
portfolio:

- SWE-bench Verified/Lite for repository issue resolution;
- Terminal-Bench for terminal-agent execution;
- BFCL for function-call and parallel tool behavior;
- τ-bench or ToolSandbox for stateful tool-use policies;
- AgentDojo and AgentHarm for prompt-injection and safety slices;
- RouterBench, RouteLLM, and FrugalGPT as routing cost/quality methodology
  references, not as direct substitutes for trajectory evaluation.

Reuse their pinned environments, executable graders, and task artifacts where
possible, but add router-specific traces and cost ledgers. Keep development,
validation, and evaluator-controlled hidden holdout sets separate.

## Additional evaluation angles

- **Routing accuracy:** compare selected model/effort with an expert-labeled
  lowest-suitable route, including a penalty for unnecessary escalation.
- **Context overhead:** measure hook and worker-context tokens and ensure the
  guidance cost does not erase model-cost savings.
- **Robustness:** repeat with unavailable preferred models, tool errors,
  timeout, cancellation, malformed worker output, and empty results.
- **Reproducibility:** rerun the same case from the same commit and compare
  result variance, not only the best run.
- **Fairness of concurrency:** check that parallel work does not overload the
  service, violate rate limits, or produce nondeterministic artifacts.
- **User experience:** score final-answer completeness, evidence quality,
  clarity of failure reporting, and whether clarification was requested only
  when needed.
- **Security/privacy:** inspect worker packets for unrelated files, secrets,
  hidden instructions, and permissions broader than the assigned objective.
