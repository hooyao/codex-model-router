## Context

An independent Astra xhigh review selected HumanEval/0, /63, /90 and Mbpp/11,
/12, /67. The planned comparison uses two repetitions per arm and a conditional
maximum campaign cost of USD 44.24. The repository must prepare that campaign
without making paid calls or accepting local assertions as sufficient budget
control.

## Decisions

Keep Python 3.9 standard-library compatibility. Pin exact EvalPlus release asset
URLs, byte sizes, archive SHA-256 values, selected decompressed record hashes,
prompt hashes, task IDs, entry points, package version, and router plugin version.
Hash record payloads without the JSONL line terminator.

Create 24 fresh Git task directories under a new campaign root outside the
repository. Keep the reusable dataset asset root disjoint from the campaign.
Place only the public prompt and task instruction in each task. Store raw CLI
JSONL, stderr, budget receipts, state, snapshots, and grader artifacts outside
the individual task worktrees.

Build both arms with `codex exec --ephemeral --ignore-user-config --ignore-rules
--json`, explicit Astra model, xhigh effort, approval policy, workspace-write
sandbox, and current directory. Disable plugins/hooks/multi-agent for baseline;
enable them for router. Require separate Codex homes, router absence in baseline,
exact enabled router version in the router home, and saved authentication.

Make dry-run the default. Live mode requires a hash-pinned absolute adapter that
returns a fresh provider-side hard-budget receipt for a dedicated guard. Reserve
200,000 tokens per attempt and distribute exactly 44,240,000 microdollars over
24 slots. Record an attempt and reservation before invocation. Never retry a
paid failure.

Parse only terminal `turn.completed.usage` events. Require an explicit complete
session inventory and price table. Discovered undeclared sessions, missing final
usage, or missing prices force incomplete cost. Preserve raw source bytes and do
not invoke ccusage.

Do not embed an unsafe host grader. Produce a hash-bound request for an external
Docker/WSL adapter using pinned read-only assets, no network, and exactly the
selected task set. Verify request binding, isolated runtime identity, exact
scheduled result inventory, and base/plus booleans. Docker results require an
immutable image digest.

## Schedule and scoring

Each of six tasks has baseline and router runs in repetitions one and two. The
first repetition orders baseline/router; the second orders router/baseline to
counterbalance order. Runs remain serial. A run passes only when both base and
plus tests pass. All scheduled terminal outcomes remain in the denominator.
Report arm pass rates and paired differences. The campaign is too small to
support broad quality, cost, or latency claims.

## Alternatives and trade-offs

Do not use a user API key or a local cost warning as budget enforcement. Do not
call ccusage because it cannot prove descendant-session completeness. Do not
copy full EvalPlus records into task worktrees because they contain grader and
canonical-solution material. Do not run EvalPlus on the host. The external
adapter contract adds operator setup but keeps untrusted generated code behind
an explicit isolation boundary.

## Risks and boundaries

The harness verifies an adapter executable hash and receipt shape, not the
provider's internal billing implementation. The operator remains responsible
for trusting an adapter that queries a real dedicated provider control. CLI JSON
may not expose terminal usage for every child session; such campaigns correctly
remain cost-incomplete until all declared streams are supplied. Docker/WSL
isolation quality depends on the external adapter and runtime configuration.
