# Commissioning scenario R1: concurrent reference consistency

Concurrent callers access tags `stable` and `canary`, initially aliases for D1. `stable` later moves to D2 while `canary` stays at D1. An older delayed fetch and a later successful observation may complete in a different order. Two valid manifests have identical layer descriptors but different annotations and different registry digests. An unrelated tag points to a broken multi-namespace manifest.

The cumulative acceptance requirements are:

1. Aliased tags may share immutable snapshot data, but updating one tag must not redirect another tag or a digest request. A digest identifies the complete registry manifest, including annotations.
2. Once a newer successful tag publication is observable, older work must not make later readers see the old result. Serialization or coalescing is allowed; overlapping remote fetches for the same tag are not required. A View already holding an old snapshot remains valid.
3. Publish only after every namespace validates. Failed polling retains the last complete good default. A necessary failed explicit fetch returns an error, without substituting an older tag or default. Other references remain intact and a later poll can recover.
4. A blocked fetch for one reference does not stop an unrelated cached digest/default read or another reference's fetch. The three inactive extra-reference limit still applies after concurrent requests settle. Exact registry request counts are not required.

Run the newly materialized public reproducer, retain the result, and submit checkpoint round 1 when the cumulative public contract passes.
