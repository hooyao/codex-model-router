## Why

The first real campaign exposed that inherited prompt text, omitted selector
arguments, and session lifetime could be mistaken for dispatch evidence.

## What Changes

- Add a fail-closed structured dispatch preflight backed by runtime capability evidence.
- Replace aggregate-text live analysis with role-bounded event parsing and tri-state evidence.
- Separate assignment timing from session lifetime and preserve historical reports through versioned reprocessing.
- Define a fresh activation probe with environment and hook-source evidence.

## Capabilities

### New Capabilities

- `dispatch-evidence-integrity`: Validates dispatch capability and observed live evidence without inference from placeholders or inherited text.

### Modified Capabilities

- None.

## Impact

The plugin gains one standard-library preflight helper. Evaluation tooling,
tests, policy, and live-run documentation change. No live model calls are made.
