# Change: Add selective execution topology

## Why

Execution ownership is already the router's primary authority decision, but
delegation topology and verification are implicit prose. That makes direct
eligibility, safe parallelism, reclassification, and evaluation difficult to
validate consistently.

## What changes

- Add a strict versioned routing decision request/result contract and a pure,
  deterministic resolver.
- Preserve DIRECT versus DELEGATE as the primary decision, then resolve
  ISOLATED_SERIAL versus PARALLEL and verification separately.
- Fail closed when direct-path facts are unknown, and record explicit triggers
  when approved direct bounds are exceeded.
- Add topology, limits, verification, and honest context-transfer policy to
  routing schema v3 and hook/Skill guidance.
- Measure direct, isolated-serial, and parallel evaluation routes without
  automatically invalidating direct router work.

## Compatibility

This is a deliberate breaking configuration change. Obsolete routing schema v1
and v2 files are rejected instead of silently translated.
