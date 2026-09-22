# Codex Model Router

Codex Model Router is a local Codex plugin MVP for model-aware subagent
orchestration. It gives a primary agent concise routing guidance at session and
prompt boundaries, then gives each worker a bounded execution and reporting
contract.

The plugin separates execution ownership from model routing. Before the first
business action, the primary agent chooses DIRECT for genuinely local, bounded
work or DELEGATE for nontrivial work. Only delegated work is matched to an
available worker model and effort. A direct task that reveals complexity must
be rerouted before the next business action.

## What It Does

- Repeats an explicit pre-business-action DIRECT/DELEGATE gate on
  `SessionStart` and `UserPromptSubmit`.
- Injects a bounded worker contract on `SubagentStart`.
- Loads, validates, and injects editable repo-local routing examples on every
  supported lifecycle event.
- Gives every dispatch a deterministic user-visible
  `<purpose>-<model>-<effort>` name, with a packet/result fallback when native
  dispatch cannot name the worker card.
- Routes clear and repeatable work toward Luna, everyday and read-heavy work
  toward Terra, complex open-ended work toward Sol, and high-risk or
  sustained-judgment work toward Astra.
- Starts with the lowest suitable reasoning effort and escalates only for
  ambiguity, risk, complexity, or failed verification.
- Limits controller validation to worker-packet completeness and consistency;
  workers own substantive review, testing, and integration.
- Requires an independent high-capability reviewer for router meta-tasks and
  an implementation worker for requested changes.
- Permits direct execution only for one local scope and one bounded known
  outcome without network, monitoring, recovery, substantive investigation, or
  independent-validation signals.
- Requires delegation for multi-repository/system/source work, named multi-step
  runbooks, network synchronization, monitoring, failure/recovery, substantive
  investigation, and independent review or validation.
- Reports a blocked limitation when delegation is required but native
  multi-agent capability is unavailable; it does not convert that task to direct.

## MVP Boundaries

This MVP adds developer context; it does not directly spawn, interrupt, or
cancel subagents. It does not switch the primary thread's model, enforce
dispatch through `PreToolUse`, connect to an MCP server, call a network service,
or store credentials.

The execution-ownership contract is policy enforcement at the agent-instruction
layer. Context injection cannot intercept tool calls and is not an OS/tool
permission barrier. Offline tests check emitted instructions and schema behavior,
not live compliance.
When a native dispatch API has no supported name field, the canonical worker
name remains the worker packet Task ID and appears in the result, but the
platform-generated worker-card title cannot be changed by this plugin. A
native underscore-only `task_name` uses a deterministic hyphen-to-underscore
transport adaptation while the human-facing Task ID stays canonical.

## Repository Layout

```text
plugins/codex-model-router/  # Installable Codex plugin
.codex-model-router/         # Editable workspace routing policy and examples
openspec/                    # Proposal, specifications, design, and tasks
.agents/plugins/             # Repository-local marketplace entry
```

The generated `.agents/skills/` directory is intentionally local-only and is
ignored by Git. The OpenSpec change records are versioned.

## Local Installation

From the repository root, add the local marketplace if needed:

```powershell
codex plugin marketplace add .
```

Restart the Codex desktop app, install **Codex Model Router** from the local
marketplace, then start a new session and use `/hooks` to review and trust the
plugin's lifecycle hooks. See the plugin-specific
[README](plugins/codex-model-router/README.md) for details.

Windows hooks require Python 3.9+ through the `python` command. In any target
project, ask Codex **“Initialize Codex Model Router in this project”**. The
installed `initialize-router` Skill resolves its own installed files and runs
the preflight for the active project; users do not need a path into this source
repository. A first lifecycle hook also initializes automatically, so explicit
init is primarily for an immediate diagnostic before trusting hooks.

Init validates the actual runtime and lifecycle hook command path and creates
`.codex-model-router/routing.json` only when absent. It never overwrites edits.
Hooks also initialize a missing file, discover existing config upward from a
nested `cwd`, and fail clearly rather than falling back on invalid config. See
the plugin README for the versioned schema, v1 compatibility, execution modes,
discovery precedence, size budgets, and failure behavior. The default model
routing policy follows official
[Codex model guidance](https://learn.chatgpt.com/docs/models) and the
[OpenAI model catalog](https://developers.openai.com/api/docs/models), consulted
on 2026-09-21.

## Validation

The plugin uses only the Python standard library.

```powershell
python plugins/codex-model-router/scripts/validate_plugin.py
python -m unittest discover -s plugins/codex-model-router/tests -v
```

The first command validates package structure and synthetic hook outputs
without requiring a network connection or a live Codex session. Neither this
smoke test nor the unit suite demonstrates live agent routing behavior. The
documented clean CLI experiment is
[`plugins/codex-model-router/docs/live-cli-validation.md`](plugins/codex-model-router/docs/live-cli-validation.md).
Its activation gate currently records `codex exec` plugin-hook support as a
prerequisite: CLI `0.155.0-alpha.9.2` did not execute installed lifecycle hooks,
while explicit non-ephemeral Skill dispatch did work. Automatic routing remains
unverified on that CLI rather than being inferred from synthetic output.
After source
changes, bump the plugin's SemVer release version and reinstall from the confirmed local
marketplace, then use a new task so Codex reloads the plugin. Reinstalling never
clobbers an existing workspace routing file. The validator enforces this for
tracked and untracked plugin changes relative to `HEAD`: it fails if plugin
implementation content changes without a strictly higher manifest SemVer version.
In CI, compare against the target merge-base explicitly with `--baseline <git-ref>`.

## Plugin Update Policy

Every change to content under `plugins/codex-model-router/`, including a new
untracked skill, hook, script, default, configuration, or documentation file,
must receive a strictly higher release version before validation, review, or reinstall. Do not edit
the marketplace entry for an update. Plugin release identity is its SemVer
version, not a Codex build/cachebuster token. From the repository root, run the
dedicated bump command and reinstall through the confirmed personal marketplace:

```powershell
python plugins/codex-model-router/scripts/bump_version.py patch
codex plugin add codex-model-router@personal
```

Use `patch` for bug fixes and small compatible changes, `minor` for
backward-compatible functionality, and `major` for breaking changes. The bump
script writes only the manifest version after validating the manifest name and
current SemVer. Pre-release identifiers are allowed by SemVer and compare below
the corresponding stable release; build metadata is valid syntax but never
changes release precedence and cannot satisfy the bump requirement.
Pull requests run this validator against the target merge-base, plus the plugin
and evaluation test suites. The workflow is read-only and uses no repository
secrets, so it is safe for fork pull requests.
The validation policy examines changed tracked files and relevant untracked
files. It ignores only cache artifacts (`__pycache__`, `.pytest_cache`, `.pyc`,
and `.pyo`). Relevant ignored files are reported as an error so an ignore rule
cannot hide a plugin change. It cannot compare an extracted source archive with
no Git history; run it from a checkout, or pass `--baseline <git-ref>` in CI.

## Evaluation

Offline evaluation lives under [`evals`](evals/README.md). The strict v2 contract
checks complete paired campaigns, hashes, provenance, accounting ledgers, and
failure precedence. Exactly one fixture-backed task (`small-edit`) has an
exact-tree grader; eight other scenarios remain draft. Validate the matrix with:

```powershell
python evals/scripts/evaluate.py validate-cases --cases evals/cases.json
```

The existing strict v2 synthetic campaign predates execution-ownership routing
and still models the earlier pure-orchestrator policy; it is retained as an
integrity fixture, not evidence for current DIRECT/DELEGATE behavior.
Ten reproducible synthetic campaigns test integrity and decision behavior without
live models. Reports include pass rates, exploratory paired quality differences,
ratios of mean cost/latency, and safety/policy counts. They do not establish router
savings or authorize a release pass. The `ccusage` importer always produces
unverified observations. See the evaluation README for commands and limitations.

The no-paid-call [EvalPlus campaign preparation layer](evals/EVALPLUS.md) adds a
pinned six-task HumanEval+/MBPP+ schedule, clean Codex CLI arm isolation, a
provider-budget refusal gate, terminal-usage accounting, and a Docker/WSL-only
grader adapter contract. It remains dry-run unless `--live` is explicit.

## OpenSpec

The completed MVP change is documented under
[`openspec/changes/add-router-mvp`](openspec/changes/add-router-mvp). Run
`openspec validate add-router-mvp --strict` to verify the planning artifacts.
The deterministic worker-name requirement is documented under
[`openspec/changes/add-subagent-naming`](openspec/changes/add-subagent-naming).
The earlier evaluation and pure-orchestrator reconciliation is documented in
[`openspec/changes/add-evaluation-integrity-fixture`](openspec/changes/add-evaluation-integrity-fixture).
Those records describe completed historical changes and do not override the
current execution-ownership policy.
The clean EvalPlus CLI campaign is specified under
[`openspec/changes/add-evalplus-cli-harness`](openspec/changes/add-evalplus-cli-harness).
