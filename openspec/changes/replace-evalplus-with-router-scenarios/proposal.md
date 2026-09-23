# Change: Replace function-generation evaluation with router scenarios

## Why

The active HumanEval/MBPP harness primarily measured generic code generation,
transport isolation, and provider controls. It did not exercise the router's
main product decisions: context isolation and reuse, escalation, dependency
topology, safe parallelism, or independent review.

## What changes

- Retire the active EvalPlus-specific runner, adapters, documentation, and tests.
- Add five frozen exact-tree scenarios centered on router behavior.
- Compare direct, mandatory serial delegation, and selective policy treatments.
- Record route events, spans, dependencies, artifact ownership, context,
  receipts, tools/logs, failures, retries, conflicts, review, and cost provenance.
- Report measured and estimated quantities separately and prohibit claims from
  bundled synthetic observations.
