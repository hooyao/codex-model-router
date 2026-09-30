# Host feasibility record

Checked on 2026-09-24 with Codex CLI `0.144.1`, native Windows, and WSL2
Ubuntu 24.04.

## Proven components

- `skopeo inspect` resolved the pinned source as Linux/amd64 with digest
  `sha256:3c77da9617e7fa577de04aef73fb1ac3114be32d7ffef72609c83929d842b157`.
- `skopeo copy` plus `umoci unpack` fetched all layers without Docker Desktop.
  Docker Desktop's Linux daemon was unavailable on this host.
- The unpacked image contains public input hashes and sizes exactly matching
  `config.json`; its full-root hash is
  `102e04806838d168297f3c86d9c76a6ddc62c2395a4c8f7d933530fc0bf708b8`.
- The derived offline agent rootfs contains the pinned static fastText CLI at
  `/usr/local/bin/fasttext` and has full-root hash
  `21217a4f600fc2e4730139f024a80b0e9ce55085eac6b673c6886282601d1a40`.
  Its `/app` hash is
  `4a6c82dc42a27aef09281598ba000bd633a06c0cbc934854434e47b95feb49df`.
- WSL2 permits user, mount, PID, and network namespaces. The repository
  namespace probe observed only loopback, no `/host`, and the pinned training
  input, and executed the pinned fastText CLI. Baseline and treatment copies
  both reproduced the full-root hash above.
- The private archive was transferred opaquely to root-owned WSL storage and
  matched its supplied SHA-256. It was not copied into either agent arm.

## Codex command-sandbox boundary

Official OpenAI documentation says `workspace-write` keeps command network
access off by default and that the policy applies to programs and subprocesses:

- <https://learn.chatgpt.com/docs/agent-approvals-security#network-access>
- <https://learn.chatgpt.com/docs/config-file/config-reference>

A no-model local probe with `codex sandbox` established:

- native `windows.sandbox="unelevated"` plus the `:workspace` profile and
  `--sandbox-state-disable-network` blocked `curl https://example.com`;
- the host process retained network access;
- native `windows.sandbox="elevated"` failed with
  `CreateProcessWithLogonW failed: 2`; and
- `wsl.exe` launched from the unelevated sandbox failed with
  `Wsl/Service/E_ACCESSDENIED`.

Therefore native Windows Codex cannot operate inside the pinned Linux rootfs
on this host through its built-in shell sandbox. The WSL namespace launcher is
an enforceable boundary for commands passed to it, but no evidence shows that
it mediates every tool call from a native Codex session. A paid pair remains
blocked until either:

1. a Linux Codex runtime runs inside WSL2 and its `bwrap` sandbox is probed; or
2. an app-server/MCP runner disables unrestricted built-in command and browser
   tools and exposes only namespace-backed operations.

The second design still needs a capability audit before implementation. A
native `:workspace` probe could also write elsewhere under `%TEMP%`, matching
the documented inclusion of temporary directories. Any future runner must use
a dedicated `CODEX_HOME`, set
`sandbox_workspace_write.exclude_tmpdir_env_var=true`, avoid additional
writable roots, leave web search disabled, and prove the resolved sandbox
state before spending on model calls.

## Resource boundary

The launcher binds the process tree to one CPU and applies a 4-GiB address-space
limit, but it also requires a filesystem capped at 10 GiB. The current WSL
filesystem reports capacity `1081101176832` bytes. `resource-probe` therefore
fails closed with:

```text
rootfs filesystem capacity 1081101176832 exceeds enforced storage limit 10737418240
```

Two capped filesystems are now provisioned beneath
`/var/lib/cmr-train-fasttext/resource-sandbox-v1`. Their rootfs copies both
match `21217a4f600fc2e4730139f024a80b0e9ce55085eac6b673c6886282601d1a40`;
the authoritative live identities are stored outside the agent roots in
`resources.json`. A subsequent read-only verification found both mountpoints
unmounted even though `/dev/loop0` and `/dev/loop1` retained the expected
backing files and ext4 UUIDs. No recovery mutation was attempted. The capped
arms therefore remain unavailable pending an explicit mount-persistence
decision. The original uncapped arm directories remain unsuitable.

A harmless transient-scope probe confirmed `cpu.max=100000 100000`,
`memory.max=4294967296`, `memory.swap.max=0`, and `pids.max=512`. This proves
the resource wrapper, not Linux Codex tool routing or readiness for paid runs.
