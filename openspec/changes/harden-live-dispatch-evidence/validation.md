# V3 adversarial correction validation

Validated on 2026-09-23 without live model calls. Historical files under
`evals/live/router-scenarios-live-20260923T031600Z` were not modified; a scoped
`git diff --exit-code` confirms this. No historical status or failure was upgraded.

The working test interpreter was
`C:\Users\yahu2\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`
(Python 3.12). For commands that test the configured Windows hook, its directory
was explicitly prepended to PATH in the test process only. The host/global PATH
and installed Python environments were not changed.

| Check | Result |
| --- | --- |
| `python -m unittest discover -s evals/tests -q` | 52 tests passed |
| `python -m unittest discover -s plugins/codex-model-router/tests -q` | 68 tests passed |
| Repository `plugins/codex-model-router/scripts/validate_plugin.py` | Passed, including hook fixtures and version policy |
| Official plugin-creator `scripts/validate_plugin.py plugins/codex-model-router` | Passed |
| Official skill-creator `scripts/quick_validate.py` for both bundled skills | Both passed |
| `openspec validate --all --strict` | 7 changes passed |
| `python -m py_compile` for changed implementation and regression modules | Passed |
| `git diff --check` | Passed |

The bundled interpreter initially lacked PyYAML, so the official validators
initially failed with `ModuleNotFoundError: No module named 'yaml'`. PyYAML
6.0.3 was installed with `pip --target` into a fresh temporary directory and
added only to the validator process's module path. No package was installed
into the bundled runtime or a global environment.

## Default hook interpreter failure remains visible

With the original PATH, the working launcher ran the prior 67-test plugin suite
and obtained 7 failures and 2 errors. The configured hook command resolved to
`C:\Python\Python39\python.exe`, which failed startup with
`ModuleNotFoundError: No module named 'encodings'`. This result was not hidden
by substituting the launcher's interpreter inside production preflight code.

The new offline `activation_preflight.py` was exercised against that actual
default environment. It saved a failure capture and exited 2. A separate capture
with the explicit test-process PATH succeeded and exited 0. Both remain at:

```text
C:\Users\yahu2\AppData\Local\Temp\router-hook-python-49819bb992554c3ab0edf8d5ec70062f\default-path-failure.json
C:\Users\yahu2\AppData\Local\Temp\router-hook-python-49819bb992554c3ab0edf8d5ec70062f\explicit-process-path-success.json
```

These are local interpreter diagnostics, not live activation results. A fresh
authoritative run still requires a complete Python installation first on the
PATH inherited by Codex, a restart in that environment, and a passing formal
neutral activation probe. No automatic hook, model quality, cost, or latency
claim follows from the offline tests.

## Regression coverage

The new raw-capture fixture suite includes a fully accepted synthetic campaign
and rejects arbitrary/cross-case oracle and artifact references, modified frozen
snapshots, unexpected empty directories, cross-transcript session sets,
contradictory index/native ancestry, late or wrong-topology delegation, direct
business under delegation, `FAIL: This must not PASS`, duplicate/ambiguous
verdicts, hinted prompts in either actual prompt source, altered probe specs,
session/index ID mismatches, unbound hook config content, missing parent/CLI
completion, wrong read/final results, modified probe notes, and substituted,
late, or wrong-workspace interpreter captures.
