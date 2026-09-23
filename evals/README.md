# Router scenario benchmark

This deterministic offline benchmark exercises Codex Model Router's actual
product value rather than generic function generation. It includes five frozen
fixture-backed scenarios:

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
not a self-reported savings counter. A future opt-in live collector should produce the same record
schema and retain all terminal failures.

See [CONTRACT.md](CONTRACT.md) for exact invariants and [DESIGN.md](DESIGN.md)
for the measurement rationale.
