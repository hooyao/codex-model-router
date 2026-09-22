# Validation and handoff

Validated on Windows from `origin/main` commit
`95952f53c8f48bd04761e67b2f644270fb6af354`. No model was invoked, no API key
was requested, ccusage was not called, and generated benchmark code was not
executed.

## Results

| Check | Observed result |
| --- | --- |
| `python -m unittest discover -s evals/tests -v` | 84 tests passed under Python 3.9.5 |
| Plugin tests with bundled Python 3.12.14 and its directory first on `PATH` | 52 tests passed, including hook subprocess smoke tests |
| `python plugins/codex-model-router/scripts/validate_plugin.py --baseline origin/main` | Passed; no plugin source changed |
| Official plugin-creator `validate_plugin.py plugins/codex-model-router` | Plugin validation passed |
| Official skill-creator `quick_validate.py plugins/codex-model-router/skills/model-router` | Skill is valid |
| `python evals/scripts/evalplus_runner.py validate-manifest` | Validated `evalplus-clean-codex-cli-v1` |
| Real pinned-asset `prepare --download` in temporary disjoint roots | Both archive hashes and all six task record/prompt hashes passed; 24 fresh Git task directories created |
| Default `run --run-limit 2` on the prepared campaign | Emitted clean baseline/router dry-run commands; made no model call |
| Existing `validate-cases` and `validate-manifest` | Nine cases and `codex-model-router-v2-draft` passed after correcting the stale cases hash |
| Regenerate, validate, and compare all synthetic campaigns | 10 campaigns validated; every compare returned the expected decision exit code |
| `openspec validate --all --strict --no-interactive` | Four changes passed, zero failed |
| `git diff --check` | No whitespace errors |

The default and live command constructors include `codex exec --ephemeral
--ignore-user-config --ignore-rules --json`, explicit `gpt-6-astra`, `xhigh`,
`workspace-write`, approval policy, and current task directory. Unit tests cover
the 24-slot counterbalanced schedule, clean arm flags, exact microdollar and token
reservations, provider-guard refusals, no-router/router plugin inventories,
dataset subset integrity, complete and incomplete terminal-usage ledgers, host
grader refusal, and exact hash-bound grader results.

## Boundaries

The live path was deliberately not exercised because the task forbids paid
calls and no dedicated provider budget guard was configured. Docker/WSL grading
was not run; the checked adapter contract was generated and tested without
executing candidate code. The harness can verify adapter identity, hashes,
receipt shape, session inventory, and prices, but cannot authenticate a
provider's internal billing system or compensate for child-session usage that
the supplied raw streams do not expose. Such accounting remains explicitly
incomplete.

Published as [pull request #5](https://github.com/hooyao/codex-model-router/pull/5)
from `codex/evalplus-clean-cli-harness`.
