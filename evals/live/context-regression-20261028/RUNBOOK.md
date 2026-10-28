# Portable runtime and evidence setup

This campaign uses `python -m evals.scripts.context_regression`, not the frozen
historical long-horizon launch configuration. The CLI is discovered from PATH
or `--cli`; admission checks actual required RPCs, models/efforts, hook trust,
dispatch, cancellation, and usage. Version and hash are recorded, not compared
against an approved-version list. Each matched pair still uses one unchanged
runtime. The plugin commit, task, and oracle pins identify the subjects being
compared and are independent of CLI version selection.

## Private preparation

Create fresh `before` and `after` runtime homes outside the solution fixtures.
Install the exact plugin snapshots from PLAN.md through the selected runtime's
normal `plugin add` interface, using local marketplaces `context-before` and
`context-after`. Only the configured provider definition and necessary security
settings belong in these private homes. Inherit the caller's Windows sandbox
implementation rather than assuming a sandbox setup under a fresh runtime home.
Keep provider/auth configuration and raw native rollouts private.

Use the runtime's hook inventory to obtain the actual declaration/content
trust identities. Review the pinned plugin files and trust only those five
observed hooks in each private test home. `hook_trust_fields` handles observed
declaration-only and declaration/content hash forms; an unknown representation
fails as a capability issue. Do not use blanket trust bypasses.

On Windows, verify that the sandbox can read the public plugin assets while
the private provider config remains restricted. The initial probes here found
an omitted Windows sandbox setting and owner-only plugin asset ACLs. Their
failed records remain separate from scored runs. The test fixture corrected
only public plugin read permissions, retaining workspace-write boundaries and
disabled command-network access.

## Capability exports and probes

The runtime's native `additional_tools` record contains the actual exposed
`collaboration.spawn_agent` parameter schema. Capture it from a neutral
activation session and derive the strict spawn-schema export from its property
names. Derive the model catalog export from `model/list`. Keep the original
source hashes and expose only these public capability exports read-only to
the solver. Do not author supported arguments from memory or infer them from
a version number.

Run a separate native-worker read and a cancellation probe for each arm:

```powershell
python -m evals.scripts.context_regression --runtime-root <private-root> --output <new-probe-directory> --live --mode native --arm before
python -m evals.scripts.context_regression --runtime-root <private-root> --output <new-probe-directory> --live --mode cancellation --arm before
```

Repeat for `after`. A cancellation probe waits for a real child-command marker,
then interrupts the observed child and parent turns and drains their events
and usage. The current native receipt can contain complete response usage
before a tool runs without a later aggregate snapshot when that tool is
interrupted. `RuntimeSessionMeter` reconciles that closed native receipt once;
open, foreign, malformed, or conflicting records remain unknown. Historical
meter defaults and original receipts are unchanged. Any derived cancellation
replay must bind the original event/rollout hashes and runtime acknowledgements.

## Scored matrix

Place the four completed capability receipts in one capability directory.
Run the predeclared five-case, three-pair matrix:

```powershell
python -m evals.scripts.context_regression --runtime-root <private-root> --output <new-results-directory> --live --mode matrix --capability-root <capability-directory> --before-ref <before-plugin-commit> --after-ref <after-plugin-commit>
```

All first-run and failed-probe usage contributes to the observed campaign
budget. Each scored receipt includes complete parent/child usage, artifact
hashes, observed resolver decisions, hook emissions, and reduced native-event
references. These are small functional/overhead tests. They do not replace the
long-horizon protocol or independently establish provider billing, upstream
model identity, or technical packet isolation.
