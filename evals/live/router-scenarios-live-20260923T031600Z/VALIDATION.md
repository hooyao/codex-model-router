# Validation receipt

All commands ran from `Q:\MyProjects\codex-model-router` on 2026-09-23.

| Check | Result |
|---|---|
| `python evals/scripts/live_evidence.py collect ...` | 4 observed records; artifacts PASS; required route/process PASS; activation and identity contracts FAIL |
| `python evals/scripts/live_evidence.py validate ...` | 4 records and all transcript/session hashes validated |
| `python -m unittest discover -s evals/tests -v` | 26/26 PASS |
| `python -m unittest discover -s plugins/codex-model-router/tests -v` with bundled Python first on `PATH` | 64/64 PASS |
| `python plugins/codex-model-router/scripts/validate_plugin.py` with bundled Python first on `PATH` | PASS |
| `python evals/scripts/evaluate.py validate-manifest --manifest evals/benchmark.json` | PASS |
| synthetic runner + record validator after oracle fix | 30/30 records validated |
| synthetic comparison after oracle fix | report generated; `release_claim_supported=false` |
| post-fix `architecture-review` live rerun | exact 209-byte artifact and distinct author/reviewer PASS |
| `git diff --check` | PASS |

The initial plugin-suite attempt under the unchanged host `PATH` failed because
`C:\Python\Python39\python.exe` cannot import the standard-library `encodings`
module. Rerunning with Codex's bundled Python 3.12 first on `PATH` passed without
source changes.
