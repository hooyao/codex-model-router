# Confined benchmark write capability

The CLI 0.144.1 diagnosis found two independent infrastructure failures. With
`windows.sandbox` unset, the requested legacy `workspace-write` mode became an
effective read-only profile. Separately, disabling the shell tool left Astra's
fallback model metadata without a native patch tool. Enabling the Windows
unelevated sandbox restored writes, but legacy workspace-write also exposed a
temporary directory. The benchmark now uses a named permission profile instead.

## Configuration and tool boundary

Both arms use this isolated base configuration:

```toml
default_permissions = "evalplus-task"
approval_policy = "never"

[windows]
sandbox = "unelevated"

[permissions.evalplus-task.filesystem]
":minimal" = "read"

[permissions.evalplus-task.network]
enabled = false
```

For each invocation, the command builder supplies the single absolute fresh task
root as the only `write` entry. `.git`, `.codex`, `.agents`, and
`.codex-model-router` under that root remain read-only. There is no legacy
`--sandbox`, `sandbox_mode`, temp-directory write grant, or `--add-dir`.
Minimal runtime read access supports the CLI/interpreter; the model's file tool
cannot read outside its assigned task root.

The same command starts the required `evalplus_workspace` stdio MCP server using
the harness Python interpreter in isolated mode (`-I`) and an immutable
`--root <task>` argument. Both controllers and native workers inherit that root.
The only server tools are `read_file` and `write_file`; the shell and tool-search
features are disabled. Browser/app/network tools remain disabled. Model-provider
transport is separate from task-tool networking.

This supported MCP edit path does not depend on Astra's native patch metadata.
It accepts bounded UTF-8 text and root-level relative filenames such as
`solution.py`. It rejects absolute paths, traversal, subdirectories, drive/UNC
paths, NTFS streams, reserved/protected names, symlinks, reparse points, and
hard-linked files. Writes use a temporary file inside the same root and atomic
replacement. It neither evaluates content nor exposes shell, network, or import
operations. Generated solutions still execute only in the Docker grader.

The MCP server is trusted harness code; its explicit path validation is the
boundary for its file operations. The named CLI profile also confines native
file tools. These checks constrain agent-accessible operations, not unrelated
privileged processes that could concurrently replace the workspace from outside
the benchmark. Normal plugin files and normal user configuration are unchanged.

## Offline and paid gates

The zero-model request capture now requires the editor's two tools, restricted
networking, and exactly one effective writable root in the CLI's filesystem
context. It rejects shell/execution tools, unrelated MCP tools, and extra write
roots. The controller and SubagentStart simulation use this same gate.

For each arm, the offline check also invokes the real stdio MCP server directly:
it writes and reads an in-workspace proof file, attempts 11 invalid/out-of-scope
writes (including source and host paths), and verifies the outside sentinel is
unchanged. Requests, responses, hashes, and source bindings are retained in
`baseline.editor.json` and `router.editor.json`. No model runs or successful
model responses are involved. Deterministic tests additionally cover hard links,
reparse points, oversized text, root aliasing, and tampered proofs.

The paid `preflight` creates a fresh random challenge without creating the target
`write-probe.txt`. Baseline must use its editor to create it. Router must dispatch
one native worker to write the marker. The harness verifies exact bytes/hash,
safe path type, and native spawn evidence. The receipt binds both challenges and
proofs; formal admission rechecks the actual artifacts. A model's success claim
or a presence-only receipt cannot pass this gate. Paid execution is still
separately opt-in and was not run during this repair.

## Historical infrastructure failures and scoring

The four attempts in `astra-xhigh-native-campaign-20260922` are invalid
infrastructure results. The [exclusion registry](evalplus/infrastructure-invalid.json)
binds their run IDs to the exact saved campaign-state hash and records the
unchanged generation/cost-ledger hashes. They remain in usage and cost accounting
but contribute to neither the numerator nor denominator of pass@1. Reused task/run
names in a new independent campaign are not automatically excluded.

Grader requests now bind `campaign_state_sha256` and `attempted_run_ids`.
Verification scores only attempted samples not excluded by that registry.
Unstarted slots never become scored failures; an empty eligible set has a null
pass rate. Ordinary incorrect implementations and timeouts remain failures when
they are eligible attempts. Incomplete campaigns cannot claim a complete score.

Raw offline evidence is under
`Q:\MyProjects\codex-model-router-benchmarks\cli-write-repair-20260922\release-verification`.
After its bound receipt and all validators pass, one fresh explicitly authorized
paid write preflight is warranted. Actual native worker writes remain a live
validation item; this repair does not spawn workers or call models.

Validation passed: 134 evaluation tests (including the opt-in real-CLI offline
test), 52 plugin tests, manifest and repository/official plugin validation,
both official skill validators, and `git diff --check`. The corrected historical
score has four excluded infrastructure attempts, zero eligible samples, and
null pass@1; the cost ledger and raw grading hashes are unchanged.
