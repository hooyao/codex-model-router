# Codex Model Router

Codex Model Router is a local Codex plugin that instructs the primary agent to
act as a pure orchestrator. It does not spawn agents itself; the primary agent
discovers capabilities, creates task DAGs, dispatches and waits for workers,
validates worker packets, coordinates conflicts, and synthesizes the final
response. Workers own all business analysis, repository inspection, file edits,
commands, tests, integration, and business-result validation.

## Controller/Worker Contract

Every business task, including a trivial edit or read-only question, must go
to a bounded native worker. If native spawn or wait/collect is unavailable,
the controller reports BLOCKED with the observed limitation. It must not fall
back to doing the work itself or create user-facing tasks as substitute workers.
Discovery uses exposed tool metadata; it does not authorize controller shell
commands or business-file inspection. Repository research needed to build a
plan is also assigned to a worker.

Controller packet validation checks identity, fields, completeness, consistency,
evidence references, and reported acceptance/validation status. It does not
verify business correctness by opening artifacts or rerunning tests. Missing
evidence and substantive disagreements go back to workers. Integration workers
resolve file conflicts, and workers validate the integrated result.

Every dispatch also receives a deterministic user-visible name in the form
`<purpose>-<model>-<effort>`. The controller normalizes and validates the name
from the frozen task purpose and resolved routing inputs, passes it through a
native `name` field when available, and writes the exact same value as both
`Worker name` and `Task ID` in every worker packet. The worker echoes both
fields in its result. Invalid, oversized, or duplicate planned names are
rejected. If native dispatch has no naming field or rejects the value, the
packet Task ID and result remain the portable fallback; the native worker-card
title may still be platform-generated.

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
The primary agent must not relabel itself as a worker to avoid delegation.

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

No manual setup is required for ordinary use. The first `SessionStart`,
`UserPromptSubmit`, or `SubagentStart` event automatically creates the missing
file at the discovered project root, then parses and injects it. Use explicit
init when you want an immediate preflight before trusting hooks.

On success, init prints whether it created or validated the config, the actual
Python executable and version used for the smoke test, and the resolved config
path. It verifies Python 3.10+, standard-library runtime imports, the template,
workspace discovery, and config creation/loading. On Windows it runs every
configured `commandWindows` lifecycle command through `cmd.exe /d /s /c`, with
the installed plugin root supplied as `PLUGIN_ROOT`, and validates each event's
JSON output. This catches command parsing, variable expansion, quoting, PATH,
and interpreter failures. On POSIX it runs every event through the resolved
`python3` hook path. A script cannot diagnose a missing interpreter before it
launches, so `hooks.json` and the package validator also require the literal
`python` command on Windows.

Schema version 1 is a JSON object with exactly these fields:

- `schema_version`: integer `1`.
- `selection_principle` and `runtime_resolution`: non-empty strings.
- `effort_guidance`: non-empty string guidance for `low`, `medium`, `high`, and
  `xhigh`.
- `official_sources`: a non-empty array of HTTPS URLs.
- `examples`: one to 64 objects with a unique lower-case hyphenated `id`, a
  non-empty `task_signals` string array, `preferred_model_class` set to `Astra`,
  `Sol`, `Terra`, or `Luna`, a supported `reasoning_effort`, and a non-empty
  `rationale`.

Model classes are preferences, not assumed runtime identifiers. The controller
must resolve them against the current runtime catalog. The defaults include
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

The non-Windows hook command requires `python3`. The Windows override requires
`python` on `PATH` and Python 3.10 or newer. Ask Codex to initialize the router
in the project before trusting hooks when you want post-launch runtime,
template, workspace, config, and hook-smoke assumptions checked immediately.

## Offline Verification

The package uses only the Python standard library. From this plugin directory,
run:

```powershell
python scripts/validate_plugin.py
python -m unittest discover -s tests -v
```

The validation command checks package structure and representative lifecycle
outputs without a network connection or a live Codex session. Regression tests
check required/forbidden contract language, simple-task and meta-task routing
instructions, deterministic worker naming, worker responsibilities, and
configured context size. These are instruction-contract checks, not evidence
of live-model compliance or savings.

## MVP Boundaries

- No MCP server, credential, network call, telemetry store, or user interface.
- No automatic primary-model change.
- No direct subagent dispatch, cancellation, or strict `PreToolUse` enforcement.

This is policy enforcement at the agent-instruction layer. Context injection
cannot technically intercept tool calls and is not an OS/tool permission
barrier. This plugin registers only the three context hooks above; it does not
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
`0.2.0`). Build metadata is ignored for precedence, so `0.1.1+anything` cannot
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
