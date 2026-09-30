Evaluation environment note (identical for both arms): the repository is the
current working directory, not `/app`. The Go toolchain and module cache are
configured in the launch environment. Do not inspect benchmark solution files,
the hidden-test patch, or external implementations. Work only in this checkout.
Run relevant package checks before finishing. The final answer should summarize
the change and the checks actually run.
If you delegate, record each stage's owner, dependencies, exact write scope,
acceptance criteria, and a SHA-256 commitment to the worker packet in
`.codex-model-router/eval-stage-plan.json` before dispatch. This is an
evaluation audit record; it does not specify how to split the task.
Use JSON with `schema_version: 1` and a `stages` array. Each stage must have
`id`, `owner`, `dependencies`, `write_scope` (repo-relative paths),
`acceptance`, `packet_sha256`, and `packet_bytes` (null for the controller).
Do not put plaintext worker packets or secrets in the plan or hook metadata.
Ask each worker to end its receipt with `Packet SHA-256: <digest>`, computed
from the UTF-8 bytes of the exact assignment payload it received. Do not
include the app's `Message Type` transport wrapper in that hash.
