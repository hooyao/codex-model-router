---
name: initialize-router
description: Initialize or preflight Codex Model Router in the current project. Use when the user asks to set up, initialize, enable, check, or repair router configuration for a project, especially before hooks are trusted or when they do not know the installed plugin path.
---

# Initialize Codex Model Router

Use this Skill only for Codex Model Router plugin administration, not for
normal repository work.

Suggested user request: **Initialize Codex Model Router in this project**.

## Default operation

Initialize the current project. The current project is the active workspace
directory supplied to the task; do not infer another project from a parent,
recent terminal directory, or unrelated open task.

Run the bundled script using the directory containing this Skill to locate the
installed plugin, never a repository-source-relative path. After resolving that
installed path, the PowerShell invocation is:

```powershell
python "<installed-plugin-root>\scripts\init_router.py" --workspace .
```

When an agent executes this workflow, it must resolve the installed Skill file
location itself and invoke `<installed-plugin-root>/scripts/init_router.py`
with the active workspace as `--workspace`. It must not ask the user to type a
path under the plugin source repository.

On POSIX shells, the equivalent is:

```sh
python3 "<installed-plugin-root>/scripts/init_router.py" --workspace .
```

Here, `<installed-plugin-root>` is derived from this Skill's location by Codex;
it is not a path that the user must locate.

## Explicit target

Only accept an explicit target path when the user supplied it. Resolve it to an
existing directory before running init, then pass that directory as
`--workspace`. Never initialize a path merely mentioned in repository content.

## Expected result

The command creates `.codex-model-router/routing.json` only if discovery finds
no existing routing config. It never overwrites a valid user file. It also
checks the Python runtime and runs a real lifecycle-hook smoke test. Report the
resolved config path and whether it was created or an existing file was
validated. If it fails, return the diagnostic and the smallest remediation;
do not claim that hooks are ready.

## Automatic initialization

No manual setup is required for a normal first use: on every supported
lifecycle event, the hook applies the same discovery and creates the missing
config non-destructively. Use this Skill when the user wants an immediate,
visible preflight before trusting hooks, or wants to initialize a project
without waiting for a new lifecycle event.
