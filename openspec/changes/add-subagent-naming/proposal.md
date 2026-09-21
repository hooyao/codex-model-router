## Why

Native worker surfaces can be difficult to distinguish when several agents run
in parallel, and dispatch APIs do not consistently expose a dedicated name
field. Every dispatch needs a deterministic, user-visible identity that makes
its purpose and routing choice inspectable across status updates and results.

## What Changes

- Require every dispatched worker to use the canonical name
  `<purpose>-<model>-<effort>`.
- Define deterministic component normalization and pre-dispatch validation.
- Use the native dispatch name field when available, with a worker-packet and
  result-echo fallback when the API cannot set a visible worker name.
- Document the instruction-layer enforcement and native-UI limitation.

## Capabilities

### New Capabilities

- `subagent-naming-contract`: Defines canonical worker names, validation,
  native dispatch behavior, and the user-visible fallback contract.

### Modified Capabilities

- None.

## Impact

- Updates the controller policy, worker packet contract, lifecycle-hook
  context, plugin documentation, and offline tests.
- Adds no dependency, network call, dispatch API wrapper, or persistent state.
- Cannot force the native worker card title when the runtime exposes no naming
  field; the canonical identity remains the packet Task ID and worker result.
