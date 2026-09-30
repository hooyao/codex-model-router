# Local ECR oracle controls, 2026-09-24

The pinned SWE-bench Pro V2 ECR task's published reference patch was replayed
from clean source tree `20121a7049c58571664b87989d1fc7bab8562884` with
the pinned hidden-test patch
`b32fa3b601051584a419624459df7f96dcd7edd57ff0efb80a3449d89a75f268`
and config
`0327174f5a9e2e7a021aeaa06b0c3b6bf66fbbab8a5240e4cc20d508180bf800`.
The local grader restored test code and changed tracked fixture data according
to the published V2 `tests/test.sh`, while retaining new candidate fixture
files. The test command ran exactly `./internal/config`, `./internal/oci`, and
`./internal/oci/ecr` under Linux Go 1.22.2 through WSL.

The reference patch passed (Go test exit 0); the empty patch failed (exit 1).
The reference `grade.json` SHA-256 is
`25366022638cdcfc56a0ef416263f55feb25688aeffd3088010b09164b9258e2`;
the empty `grade.json` SHA-256 is
`dd08d0651b48ba2df8224fd3a7278359e9f6660375cc9a60dde7ced0da9af8be`.
The paired stdout/stderr files are retained here and hash-bound by each report.
The reference patch itself remains outside agent workspaces and is not stored
in this directory. These are calibrated local controls, not official Harbor
scores.
