# Commissioning scenario R2: cancellation and resource recovery

Hold a View callback open while polling and shutdown proceed. Cancel an initiating acquisition caller and another waiter independently. Inject a layer open/read failure after other streams have been acquired; then restore the same reference and replay successfully.

The cumulative acceptance requirements are:

1. A context cancelled before View begins returns that context error without entering the callback. Cancellation during acquisition returns promptly with that caller's context error. If acquisition is shared, cancelling the initiator or a waiter does not abort another active caller. Sharing is optional.
2. An entered callback is owned by its caller, sees a stable snapshot, and does not block publication for new readers. Shutdown must not wait for a callback held only by the evaluator's latch.
3. Close is concurrent and repeat safe. It cancels background acquisition, waits for background work, and preserves entered callbacks. A later View returns a stable documented closed-store error. Calls still acquiring fail promptly with that error or context cancellation. No polling request starts after Close returns.
4. A failed Fetch closes every stream it acquired. After ownership transfer, snapshot construction closes all supplied streams on success and on Stat/read/validation failures, including streams not yet parsed. Cleanup preserves the originating error and allows a later successful attempt. Non-OCI parser and backend behavior remains compatible.

Run the newly materialized public reproducer, retain the result, and submit final checkpoint round 2 with the integrated patch, regression tests, maintainer documentation, and check receipts.

The public reproducer includes a three-stream Stat failure and a loopback
registry that holds acquisition cleanup across Close. Both cases exercise the
requirements above through public APIs. The final public gate returns failing
case identifiers and bounded Go output. If it fails, diagnose the failure,
record a concise repair plan, rerun the affected checks, and submit the one
allowed repaired round-2 checkpoint. The same cost and wall limits continue
through that repair; a second failure ends the arm.
