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

CLI 0.144.1 also requires explicit MCP approval: `approval_policy="never"`
does not automatically approve MCP calls. With prompt approval, both arms
returned `user cancelled MCP tool call` without writing their markers. The
command builder now scopes approval to exactly these two server tools:

```toml
[mcp_servers.evalplus_workspace]
enabled_tools = ["read_file", "write_file"]
tools = { read_file = { approval_mode = "approve" }, write_file = { approval_mode = "approve" } }
```

There is no global MCP auto-approval or interactive confirmation. The
[official MCP configuration documentation](https://developers.openai.com/codex/mcp)
defines per-tool `approval_mode`; the scripted real-CLI fixture below verifies
its behavior rather than relying on config intent alone.

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

[Invocation-scoped config receipts](PROJECT-TRUST-RECEIPTS.md) preserve the exact
pre-launch baseline and permit only the CLI's expected trust entry for that task.
Preflight uses cloned homes; seed configs remain immutable. All other config,
package, and router-profile changes still fail admission.

The zero-model request capture now requires the editor's two tools, restricted
networking, and exactly one effective writable root in the CLI's filesystem
context. It rejects shell/execution tools, unrelated MCP tools, and extra write
roots. The controller and SubagentStart simulation use this same gate.

Native code-mode workers instead supply a developer `additional_tools` item,
whose custom `exec` tool explicitly describes a V8 runtime with no Node,
filesystem, or network access. This dispatcher is not a shell. The checker
parses actual nested tool declarations and matched `ALL_TOOLS` discovery
outputs, including flattened `mcp__evalplus_workspace__write_file` names.
Illustrative prose and user messages cannot establish tool visibility. A
partial/deferred catalog is recorded as such, never as proof that both editor
tools were seen; the physical write-artifact gate still must pass. Shell,
network, and unrelated MCP capabilities in any parsed representation fail.

For each arm, the offline check also invokes the real stdio MCP server directly:
it writes and reads an in-workspace proof file, attempts 11 invalid/out-of-scope
writes (including source and host paths), and verifies the outside sentinel is
unchanged. Requests, responses, hashes, and source bindings are retained in
`baseline.editor.json` and `router.editor.json`. No model runs or successful
model responses are involved. Deterministic tests additionally cover hard links,
reparse points, oversized text, root aliasing, and tampered proofs.

The offline hook gate additionally requires `mcp-cli-proof/direct` and
`mcp-cli-proof/code-mode`. These run the actual CLI against a loopback scripted
Responses fixture with no model implementation, forwarding, credentials, or
delegation. Fixed tool calls attempt an inside-root write and an outside-root
write; code mode first discovers `ALL_TOOLS`. Expected CLI exit 1 comes from the
final deliberate HTTP 400, after the tool results have been captured. Exact
artifact bytes, the unchanged outside sentinel, raw request/tool results,
source hashes, and evidence inventory are checked and bound into the hook
receipt. A separate prompt-approval negative control reproduces cancellation.
No generated solution code is run.

Run these checks without any paid provider:

```powershell
$env:EVALPLUS_OFFLINE_CLI_TEST = '1'
py -3.13 -m unittest discover -s evals/tests -q
py -3.13 evals/scripts/evalplus_mcp_probe.py --evidence <new-outside-repo-directory>
py -3.13 evals/scripts/evalplus_mcp_probe.py --evidence <another-new-directory> --code-mode
py -3.13 evals/scripts/evalplus_mcp_probe.py --evidence <negative-control-directory> --prompt-control
```

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
`Q:\MyProjects\codex-model-router-benchmarks\mcp-approval-repair-20260922\release-verification`.
The current bound receipt is `campaign/hook-delivery.json`; successful
CLI-mediated writes/refusals are in `campaign/mcp-cli-proof/{direct,code-mode}`,
and the cancellation negative control is in `prompt-control`.
After its bound receipt and all validators pass, one fresh explicitly authorized
paid write preflight is warranted. Actual native worker writes remain a live
validation item; this repair does not spawn workers or call models.

Validation passed: 149 evaluation tests (including all four opt-in real-CLI
offline tests), 52 plugin tests, manifest and repository/official plugin validation,
both official skill validators, and `git diff --check`. The corrected historical
score has four excluded infrastructure attempts, zero eligible samples, and
null pass@1; the cost ledger and raw grading hashes are unchanged.
