# Codex Model Router

Codex Model Router is a local Codex plugin that instructs the primary agent to
decide execution ownership before its first business action. Qualifying local,
bounded work may run directly. Nontrivial work is delegated, and only then does
the router select a suitable worker model and reasoning effort. The plugin
injects policy; it does not spawn agents itself.

## Controller/Worker Contract

Before inspecting business files, researching, running commands, editing,
testing, invoking a task Skill, or making a network call, the controller records
a validated routing decision contract and emits
`ROUTE: DIRECT — <rule/reason>` or
`ROUTE: DELEGATE — <topology/rule/model/effort/reason>`. Skills define HOW, not WHO.

DIRECT requires one local scope, one bounded known outcome, and no network or
synchronization, long-running work or monitoring, failure/recovery workflow,
substantive research/investigation, high risk, or independent review/validation.
Permissions, safety constraints, write scope, and a self-check plan must also be
known. Unknown signals do not qualify for DIRECT. DELEGATE
is required when any such nontrivial signal exists, including multiple
repositories, systems, or sources and named multi-step runbooks. A direct task
that reveals a delegation signal must be rerouted before its next business
action. The versioned result records ownership, topology, verification, rule,
reasons, limits, and any direct-bound escalation trigger.

The injected routing block includes the absolute bundled resolver path, the
workspace config path, and the complete compact request field inventory. The
controller sends one decision-request-v1 JSON object to
`execution_decision.py --config <routing.json>` on standard input and uses the
validated JSON result. The full request schema and example are documented in
[`skills/model-router/references/decision-contract.md`](skills/model-router/references/decision-contract.md).

A delegated decision resolves to `PARALLEL` only for multiple bounded,
independent, dependency-free tasks with disjoint write scopes. Any unknown,
dependency, shared state, or overlapping write scope resolves to
`ISOLATED_SERIAL`. Isolation describes a minimized packet and compact result
receipt; it does not erase parent history or provide a technical sandbox.
Depth, concurrency, retry, permission, safety, write-ownership, and verification
constraints apply to both direct and delegated execution. Configured numeric
limits are ceilings and must be lowered to the actual runtime capability; they
do not assert that a spawn API or a number of worker slots exists.

Only a DELEGATE route triggers worker capability discovery and model/effort
selection. If native spawn or wait/collect is unavailable, the controller
reports BLOCKED with the observed limitation. It must not fall back to doing
delegated work itself or create user-facing tasks as substitute workers.

Controller packet validation checks identity, fields, completeness, consistency,
evidence references, and reported acceptance/validation status. It does not
verify business correctness by opening artifacts or rerunning tests. Missing
evidence and substantive disagreements go back to workers. Integration workers
resolve file conflicts, and workers validate the integrated result.

Every dispatch also receives a deterministic user-visible name in the form
`<purpose>-<model>-<effort>`. The controller normalizes and validates the name
from the frozen task purpose and resolved routing inputs, passes it through a
native hyphen-capable `name` field when available, and writes the exact same
value as both `Worker name` and `Task ID` in every worker packet. An
underscore-only `task_name` receives a deterministic hyphen-to-underscore
transport adaptation, recorded separately as `Native task name`; it does not
replace the canonical Task ID. Invalid, oversized, or duplicate canonical and
adapted names are rejected. If native dispatch has neither supported naming
field, the packet Task ID and result remain the portable fallback; the native
worker-card title may still be platform-generated.

Router self-improvement, routing-policy review, evaluation design, and benchmark
selection require an independent reviewer separate from the author/implementer,
using the highest suitable available model. An implementation request must also
have an implementation worker; review advice alone does not complete it.

`SessionStart` and every `UserPromptSubmit` repeat the full controller contract,
explicitly scoped to the primary agent, including the meta-task rule even when
a follow-up lacks router keywords. Inherited controller context does not apply
to dispatched workers.

`SubagentStart` emits an explicit worker-role override: the worker is authorized
and required to perform assigned analysis, repository inspection, edits, commands,
tests, and task validation within its bounded packet. Verification workers must
inspect the relevant files and run their assigned checks. Workers do not apply
the controller's capability-discovery or missing-delegation rules and must not
dispatch subworkers. Lack of native multi-agent tools is not a reason for a
worker to refuse its assignment; actual task/tool/permission blockers must still
be reported. User scope, read-only assignments, and permissions remain binding.
The primary agent must not relabel itself as a worker to avoid a required
delegation.

## Workspace Routing Configuration

Every supported hook loads and validates an editable workspace file at
`.codex-model-router/routing.json` on every invocation. The emitted context
contains a deterministic `ROUTING_CONFIG_BEGIN`/`ROUTING_CONFIG_END` block, so
edits take effect on the next hook without rebuilding the plugin.

Discovery starts at the event `cwd`. An existing routing file at that directory
or any parent wins. If none exists, the closest parent containing a `.git` file
or directory is the initialization root. If there is no Git marker, the event
`cwd` is used. Discovery never shells out to Git.

The plugin bundles the canonical template at
`defaults/default-routing.json`. Both explicit init and lifecycle hooks copy
that template with exclusive creation only when the workspace file is absent.
They never overwrite an existing file.

For a project-facing manual setup, ask Codex **“Initialize Codex Model Router
in this project”**. The installed `initialize-router` Skill resolves the
installed plugin location itself and runs the bundled preflight against the
active project. You do not need to know or type a source-repository path. For
an explicit target, say **“Initialize Codex Model Router in
`C:\\work\\example`”** (or give an absolute POSIX path). The skill only uses a
target path explicitly provided by you.

There is no documented plugin slash-command capability in the current manifest
format, so this release deliberately exposes a discoverable Skill rather than
claiming `/codex-model-router-init` exists. The equivalent portable program is
`<installed-plugin-root>/scripts/init_router.py --workspace .`; it is intended
for the Skill or automation, not for users to locate by hand.

When the host supports plugin lifecycle hooks, the first `SessionStart`,
`UserPromptSubmit`, or `SubagentStart` event automatically creates the missing
file at the discovered project root, then parses and injects it. Use explicit
init and the live CLI activation gate when you want to establish that the
installed host actually executed the hook; synthetic smoke success alone is
not that evidence.

On success, init prints whether it created or validated the config, the actual
Python executable and version used for the smoke test, and the resolved config
path. It verifies Python 3.9+, standard-library runtime imports, the template,
workspace discovery, and config creation/loading. On Windows it runs every
configured `commandWindows` lifecycle command through `cmd.exe /d /s /c`, with
the installed plugin root supplied as `PLUGIN_ROOT` (the Codex-specific
lifecycle-hook runtime variable), and validates each event's
JSON output. This catches command parsing, variable expansion, quoting, PATH,
and interpreter failures. On POSIX it runs every event through the resolved
`python3` hook path. A script cannot diagnose a missing interpreter before it
launches, so `hooks.json` and the package validator also require the literal
`python` command on Windows.

Schema version 3 is a JSON object with exactly these fields:

- `schema_version`: integer `3`.
- `selection_principle` and `runtime_resolution`: non-empty strings.
- `execution_policy`: `default_mode` (`direct`, `delegate`, or `evaluate`),
  non-empty `direct_requires_all` and `delegate_if_any` arrays of supported
  machine-readable signals, `reroute_on_escalation: true`, explicit delegate
  topology rules, bounded depth/concurrency/retry limits, and verification
  minima. Required signal arrays are exact sets.
- `effort_guidance`: non-empty string guidance for `low`, `medium`, `high`, and
  `xhigh`.
- `official_sources`: a non-empty array of HTTPS URLs.
- `examples`: one to 64 objects with a unique lower-case hyphenated `id`, a
  non-empty `task_signals` string array, `execution_mode` set to `direct`,
  `delegate`, or `evaluate`, `preferred_model_class` set to `Astra`, `Sol`,
  `Terra`, or `Luna`, a supported `reasoning_effort`, and a non-empty `rationale`.

Obsolete schema v1 and v2 files are rejected rather than silently translated.
Regenerate or deliberately migrate them to schema v3. `direct` is an eligibility
hint subject to every direct criterion, `delegate` is mandatory, and `evaluate`
applies the structured semantic gate. These fields are machine-readable policy
inputs, not a claim that static config can completely classify arbitrary
natural-language tasks.

Precedence is: hard delegation signals first; one matching example overrides
`execution_policy.default_mode`; no match uses the global default; disagreeing
matches fail closed to delegation. A `direct` result from either level remains
subject to every direct criterion.

Model classes are preferences, not assumed runtime identifiers. The controller
resolves them against the current runtime catalog only after choosing DELEGATE.
The defaults include
architecture, security, complex tool workflows, 3D modeling, image analysis,
code analysis, debugging, open-ended and everyday implementation, discovery,
documentation, test triage, detailed manual procedures, extraction,
classification, normalization, and structured summarization. They follow the
current official [Codex model guidance](https://learn.chatgpt.com/docs/models)
and [OpenAI model catalog](https://developers.openai.com/api/docs/models),
consulted on 2026-09-21.

The raw file limit is 32,768 bytes. Its validated deterministic serialization
must be no more than 16,384 bytes, smaller than the 24,576-byte hook context
limit to reserve space for the controller or worker contract. Malformed,
schema-invalid, oversized, unreadable, or uncreatable config fails the hook or
init with a non-zero exit and an actionable diagnostic. There is no fallback to
bundled defaults once a workspace config exists, and input is never truncated.

## Local Installation

This repository includes a local marketplace entry at
`../../.agents/plugins/marketplace.json`. From the repository root, add the
marketplace to Codex if it is not already available:

```powershell
codex plugin marketplace add .
```

Restart the Codex desktop app, select the local marketplace, and install
**Codex Model Router**. Codex installs local plugins from the marketplace entry
at `./plugins/codex-model-router`.

## Trust Hooks

Plugin hooks are not trusted automatically. After enabling the plugin, start a
new Codex session and use `/hooks` to inspect and trust the three lifecycle
handlers. The hooks add developer context for `SessionStart`,
`UserPromptSubmit`, and `SubagentStart` only. Every handler invokes the bundled
`hooks/router_hook.py` program.

The manifest intentionally relies on Codex's documented default discovery of
`hooks/hooks.json`. If a future manifest adds an explicit `hooks` entry, that
entry replaces default-file discovery rather than extending it.

The non-Windows hook command requires `python3`. The Windows override requires
`python` on `PATH` and Python 3.9 or newer. Hook commands resolve the installed
root through the Codex-specific runtime-provided `PLUGIN_ROOT`. Codex also sets
`CLAUDE_PLUGIN_ROOT` for compatibility, but this plugin uses the native Codex
contract documented in the official
[hook documentation](https://learn.chatgpt.com/docs/hooks#plugin-bundled-hooks).
Ask Codex to initialize the router
in the project before trusting hooks when you want post-launch runtime,
template, workspace, config, and hook-smoke assumptions checked immediately.

## Offline Verification

The package uses only the Python standard library. From this plugin directory,
run:

```powershell
python scripts/validate_plugin.py
python -m unittest discover -s tests -v
```

The validation command checks package structure and synthetic lifecycle outputs
without a network connection or a live Codex session. Regression tests check
schema v3 and obsolete-version rejection, ownership/topology precedence,
escalation records, verification, deterministic worker naming, worker
responsibilities, and context size. These are
instruction-contract checks, not evidence of live-model compliance or savings.

For live behavioral validation, follow
[`docs/live-cli-validation.md`](docs/live-cli-validation.md). It specifies clean
workspaces and retained evidence for a trivial DIRECT task, a two-repository
DELEGATE task, and a DIRECT-to-DELEGATE escalation. The repository does not
claim those experiments have passed until their transcripts and artifacts are
captured from an installed plugin in clean Codex CLI sessions.

Observed Codex CLI `0.155.0-alpha.9.2` `codex exec` sessions did not execute
installed plugin lifecycle hooks even when the plugin was enabled and trust was
bypassed for diagnosis. The live procedure therefore contains a fail-closed
activation gate. Explicit invocation of `$codex-model-router:model-router` in a
non-ephemeral session did successfully dispatch underscore-adapted native
`task_name` workers, but that is separate evidence and does not establish
automatic hook support or controller compliance.

## MVP Boundaries

- No MCP server, credential, network call, telemetry store, or user interface.
- No automatic primary-model change.
- No direct subagent dispatch, cancellation, or strict `PreToolUse` enforcement.

This is policy enforcement at the agent-instruction layer. Context injection
cannot erase history, technically intercept tool calls, create a sandbox, or
act as an OS/tool permission barrier. This plugin registers only the three context hooks above; it does not
install tool-denial hooks, alter permissions, or guarantee model compliance.
Codex supports separate tool hooks, but their coverage has exceptions and they
are not implemented here. See the official [hook documentation](https://learn.chatgpt.com/docs/hooks#tool-coverage).

Source edits do not update an already installed plugin or an active task's
loaded instructions. Reinstall the updated package through its configured local
marketplace, review any changed hook definitions, and use a new task to pick up
the new hooks, skill text, and bundled defaults. Reinstall does not replace an
existing workspace `routing.json`; edit or remove that file deliberately if you
want to adopt a newer template.

## Version-Bump Policy for Contributors

Every change under this plugin directory, including a new untracked source,
skill, hook, default, configuration, or documentation file, requires a fresh
strictly increasing SemVer release version before it is validated, reviewed, or
reinstalled. The plugin's version is its release identity; do not use a Codex
cachebuster or build metadata as a substitute. Run the dedicated bump command
from the repository root; do not hand-edit the marketplace entry:

```powershell
python plugins/codex-model-router/scripts/bump_version.py patch
codex plugin add codex-model-router@personal
```

Choose `patch` for a bug fix or small compatible change, `minor` for new
backward-compatible functionality, and `major` for a breaking change.
Pre-releases follow SemVer precedence (for example, `0.2.0-rc.1` is lower than
`0.2.0`). Build metadata is ignored for precedence, so a build-metadata-only change cannot
replace a release bump. `scripts/validate_plugin.py` enforces this against
`HEAD` for changed tracked and relevant untracked plugin content and reports
the changed paths when the manifest version is not strictly higher. Relevant Git-ignored plugin files fail with a
diagnostic, so an ignore rule cannot bypass the policy; only cache artifacts
(`__pycache__`, `.pytest_cache`, `.pyc`, and `.pyo`) are excluded.
Release/CI validation should compare the change target explicitly, for example
`python scripts/validate_plugin.py --baseline origin/main`. Generated cache
files are ignored. An unpacked source archive with no Git baseline cannot be
compared, so run the policy from a Git checkout when enforcement is required.
The repository pull-request workflow runs the validator against the target
merge-base and runs the plugin and evaluation test suites. It is read-only and
does not require secrets, including for fork pull requests.
