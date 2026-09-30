# Pinned Linux Codex runtime

Stage W installed the official OpenAI `rust-v0.144.1` x86-64 musl package at
`/opt/cmr-codex-linux-v0.144.1`. The archive SHA-256 is
`3fd50cf96809b1eea294bbfba0a5c3a576871b4876a1f0e91226e520c1923be1`.
The installation is root-owned and read-only; it preserves the packaged Codex
binary, code-mode host, ripgrep, bubblewrap, and zsh helper.

No existing Codex home or credential was read or copied. Probes run as the
dedicated `cmrprobe` system identity with an isolated home at
`/var/lib/cmr-codex-probe`. Its `CODEX_HOME` contained only the pinned probe
configuration before execution and remains unauthenticated.

The named `cmr-offline` permission profile passed no-model command probes for:

- outbound network denial;
- denial of two world-readable dummy protected files;
- workspace-only writes and denial of a user-writable outside directory;
- propagation to nested shell and curl processes;
- distinct mount, network, and PID namespaces;
- `NoNewPrivs=1` and seccomp filter mode `2`; and
- denial of a benign `/mnt/c/Windows/System32/cmd.exe` interop attempt.
- inability to execute a benign copied PE file from the writable workspace; and
- denial of connections to protected and workspace Unix-socket canaries.

The exact machine-readable result is `linux-runtime-probe.json`. It binds the
runtime tree, package files, probe script, permission profile, copied PE,
identity/groups, invocation projection, timestamp, and outside-sandbox positive
controls. Deliberate unconfined and permission-widened evidence projections
must be rejected before a result can pass.

The workspace canary socket pathname was visible to the command sandbox, but a
connection attempt was denied and no sandbox canary message reached the host
listener. Other inventoried host socket paths were not visible. The global WSL
interop setting was observed as enabled and was not changed.

The copied PE file is non-executable even outside the sandbox on the ext4
workspace (`Invalid argument`). Its inside denial is therefore
non-discriminating and recorded as `UNKNOWN`; the overall receipt is
`PARTIAL`, not `PASS`. The original `/mnt/c` interop route remains a valid
outside-positive/inside-denied control. Protected and workspace Unix sockets
are attempted independently and each has separate outside-connectable and
inside-denied evidence.

Official OpenAI documentation describes permission profiles as boundaries for
local sandboxed commands and states that Linux/WSL enforcement uses bubblewrap
and seccomp. It also notes that model/service traffic, MCP, browser, Computer
Use, and other tool surfaces require separate controls:
<https://learn.chatgpt.com/docs/permissions>.

The next prerequisite for any model-backed run is fresh authentication under
`cmrprobe` itself, preferably interactive `codex login --device-auth`, without
copying another user's `auth.json`. Authentication does not authorize a paid
run and does not resolve the capped-filesystem remount or tool-routing gates.
