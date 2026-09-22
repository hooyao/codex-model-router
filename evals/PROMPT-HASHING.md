# External helper prompt hashing

The manifest's initial prompt hash identifies normalized UTF-8 text, not the
platform-specific bytes produced by writing that text. Windows preparation can
write CRLF; the pinned runner uses universal-newline `Path.read_text()` when
verifying task files. One external campaign helper instead read bytes and
decoded/re-encoded them unchanged, causing admission to fail before any formal
model call despite identical prompt text.

External helpers must use `evalplus_prompt_hash.normalized_prompt_sha256` for
this comparison. It converts CRLF and lone CR to LF without trimming or changing
any other text. Content, indentation, and terminal-newline changes remain hash
mismatches. Configs, requests, solutions, datasets, and receipts retain exact-byte
hashes; this is not a global hash normalization change.

The helper and regression tests are separate from all files bound by the
already-passed preflight receipts. No paid preflight is repeated, no receipt is
rewritten, and no CLI model/config/permission/router treatment changes. The
external continuation revalidates the existing receipts and all 24 initial
prompt hashes before admission. Its one previously provisioned but never-used
slot home may be reused only after exact seed-file verification and confirmation
that no invocation evidence, other files, or profile state exists.
