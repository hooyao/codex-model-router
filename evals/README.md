# Router scenario benchmark

The primary performance question is a paired comparison against a single-agent
`gpt-6-astra` baseline on **the same task and starting state**, with quality
parity required before comparing wall time and total USD cost. See
[PERFORMANCE_PROTOCOL.md](PERFORMANCE_PROTOCOL.md). The existing synthetic
matrix and selective-only live evidence are functional diagnostics, not a
performance result against Astra.

Plugin changes additionally require a fresh paired comparison against the last
accepted plugin artifact, using the same task suite to detect quality, time,
and cost regressions. The current live collector does not yet implement either
paired comparison or a release gate.

The performance suite must use long-horizon repository tasks calibrated near
the scale of roughly 100 natural Astra model calls; the five small fixtures
below remain smoke/functional diagnostics only.

A separate [paired SWE-bench Pro V2 pilot](live/paired-swepro-flipt-20260923/REPORT.md)
now compares one real task on identical starting trees. It is a single-task,
22-Astra-call pilot with a calibrated local hidden-test replay, not the
approximately 100-call release benchmark or an official Harbor score.

A [selective Astra/xhigh OCI/ECR pilot](paired_selective/DESIGN.md) is frozen
for independent review. Its [operator runbook](paired_selective/RUNBOOK.md)
defines the same-task Astra/xhigh versus calibrated lightweight-controller
pair, offline collector, scope audit, and identical local replay. No paid run
or performance result has been collected for this pilot.

The existing deterministic offline smoke suite checks Codex Model Router's
execution contracts rather than generic function generation. It includes five
frozen fixture-backed scenarios:

- `investigation-reuse`: noisy read-heavy evidence, a compact receipt, and a
  small downstream edit;
- `serial-escalation`: direct scope expands and dependent work is delegated
  serially;
- `parallel-disjoint`: three independent, write-disjoint artifacts expose
  critical-path benefit;
- `architecture-review`: high-risk design requires a distinct reviewer; and
- `direct-small-control`: one typo fix used only to calibrate overhead.

Every scenario runs under forced direct, mandatory serial delegation, and the
selective policy. Fixtures use exact-tree acceptance; route and span contracts
check escalation, topology, dependency order, write ownership, reviewer
independence, context/receipt totals, tools/logs, failures, retries, conflicts,
and cost provenance.
Artifact/semantic quality is computed identically across treatments. Frozen
route, escalation, receipt, dependency, review, and topology requirements are
reported separately as process compliance. Required artifact paths and
dependency/receipt edges are case data, not record-authored claims.

## Offline run

Python 3.9+ and the standard library are sufficient:

```powershell
python evals/scripts/evaluate.py validate-manifest --manifest evals/benchmark.json
python evals/scripts/runner.py --manifest evals/benchmark.json --output evals/generated
python evals/scripts/evaluate.py validate-records --manifest evals/generated/benchmark.json --records evals/generated/runs.jsonl
python evals/scripts/evaluate.py compare --manifest evals/generated/benchmark.json --records evals/generated/runs.jsonl
python -m unittest discover -s evals/tests -v
```

The output directory must not exist. The runner copies all pinned fixtures so
the generated campaign is self-contained and reproducible.

## Interpretation

The bundled observations are synthetic and make no model call. They validate
the benchmark, demonstrate report semantics, and provide deterministic
regressions only. They do not establish live quality, cost savings, latency
improvement, or routing accuracy. The runner uses frozen route expectations and
does not invoke the plugin resolver. Costs labeled `estimated` remain separate
from `measured`. Receipt reuse comes from hash-bound producer/consumer links,
not a self-reported savings counter.

## Observed live evidence

Live execution remains an explicit operator action; the repository never starts
paid model calls from the offline runner. After an operator has retained the
five required selective-treatment CLI transcripts, disposable workspaces, and
persisted Codex sessions under one evidence root, derive and validate the
observed record with:

```powershell
python evals/scripts/live_evidence.py collect --root <evidence-root> --sessions-root "$env:CODEX_HOME\sessions"
python evals/scripts/live_evidence.py validate --results <evidence-root>\live-results-v3.json
```

The collector uses role-bounded parent/child events for packet identity, native
transport, actual selectors, runtime metadata, result echo, and assignment
timing instead of searching aggregate inherited text. It grades
business trees while reporting hook-created `.codex-model-router` state
separately, hashes every transcript and session source, retains terminal
failures in the denominator, and records USD cost as unavailable unless the
runtime exposes measured billing. Its selective-only observed schema is kept
separate from the frozen three-treatment synthetic comparison contract.
Acceptance is recomputed during validation. Historical evidence can be
reprocessed to a new `--output` without overwriting its original result.

The canonical capture layout is `raw/` for the activation spec, transcript,
final output, routing config, environment, hook provenance, CLI/manifest, and
capability sources; `session-evidence/<case>/session-index.json` for session
indexes; and fresh `results/` plus `live-results-v3.json` for collector output.
Collection checks every destination before mutation, stages copies, validates
the report against raw sources, and atomically publishes or rolls back.
Validation reparses every hash-bound reference, including the frozen oracle;
authored status and acceptance fields have no authority.

The live oracle is selected by the validator's repository-pinned case manifest,
not by report-controlled paths. Activation additionally requires the exact
neutral user prompt, a matching completed session/result, and a successful
`activation_preflight.py` capture of the configured hook Python command in the
CLI launch environment. Historical failed captures remain immutable; the
offline regression suite does not establish a new live pass.
Environment metadata is parsed against a narrow structural grammar; wrapped
supplementary instructions invalidate activation. Correct artifacts with a
failed, cancelled, missing, or contradictory CLI/parent completion remain failed
scheduled runs. Once delegation begins, later DIRECT routes and controller
business recovery fail process validation.

See [CONTRACT.md](CONTRACT.md) for exact invariants and [DESIGN.md](DESIGN.md)
for the measurement rationale.
