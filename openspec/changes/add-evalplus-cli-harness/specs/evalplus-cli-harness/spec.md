## ADDED Requirements

### Requirement: Pinned reviewed campaign
The harness MUST use exactly HumanEval+ v0.1.10 and MBPP+ v0.2.0 with selected
tasks HumanEval/0, HumanEval/63, HumanEval/90, Mbpp/11, Mbpp/12, and Mbpp/67.
It MUST pin dataset and selected-record integrity, schedule two repetitions for
each baseline/router arm, and preserve all 24 scheduled runs in scoring.

#### Scenario: Dataset or subset bytes drift
- **WHEN** an archive, selected record, prompt, task ID, or entry point differs
  from the checked-in manifest
- **THEN** preparation fails before creating a campaign

#### Scenario: A run fails or is blocked
- **WHEN** any scheduled run does not produce a passing base and plus result
- **THEN** that run remains a failure in its arm denominator

### Requirement: Clean guarded CLI execution
The harness MUST create fresh task directories outside the agent workspace and
MUST keep datasets, grader material, raw events, and accounting artifacts outside
each task worktree. Commands MUST use `codex exec`, `--ephemeral`,
`--ignore-user-config`, `--ignore-rules`, `--json`, and explicit model, effort,
sandbox, and current-directory settings. Baseline MUST disable the router and
router MUST use a separate home with the exact enabled router plugin.

Live execution MUST require explicit `--live`, enforce planned run/token/cost
reservations before invocation, and obtain a fresh dedicated provider-side hard
budget receipt. Missing or local-only budget control MUST cause refusal. A paid
failure MUST NOT be retried.

#### Scenario: Operator omits live opt-in
- **WHEN** the run command is issued without `--live`
- **THEN** it emits a dry-run plan and does not invoke Codex

#### Scenario: Provider guard is absent
- **WHEN** live mode lacks a valid fresh provider-side hard-budget receipt
- **THEN** the harness refuses before a model call

### Requirement: Complete-session offline accounting
The collector MUST parse preserved final CLI usage events without invoking
ccusage. It MUST require an explicit inventory for controller, workers, retries,
verification, and abandoned sessions. It MUST NOT set `cost_complete` true when
any declared session lacks terminal usage, any discovered session is undeclared,
or any session lacks a pinned model price.

#### Scenario: Worker usage is missing
- **WHEN** a worker is declared or discovered without a final usage event
- **THEN** the ledger preserves diagnostics and emits incomplete null cost

### Requirement: Isolated EvalPlus grading
The harness MUST refuse direct host execution of generated code. It MUST produce
and verify a hash-bound adapter contract for Docker or WSL, preserve datasets and
tests outside task worktrees, require no network during grading, and accept
results only for the exact scheduled task/run set.

#### Scenario: Host grading is requested
- **WHEN** an operator selects the host backend
- **THEN** the harness refuses without executing generated code

#### Scenario: Adapter omits or adds a result
- **WHEN** a grader result does not cover exactly every scheduled run and task
- **THEN** result verification fails before scoring
