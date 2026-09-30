# ECR credential lifecycle under concurrent OCI synchronization

OCI authentication now supports AWS ECR, but every credential lookup fetches a
new authorization token. Busy synchronizers make redundant requests, and a slow
lookup can hold up unrelated registries. Implement credential reuse and refresh
for long-running, concurrent use of the existing OCI authentication APIs.

For one ECR provider, repeated lookups for the same registry must reuse a
successfully decoded token while its AWS `ExpiresAt` is in the future. Once it
expires, the next lookup must refresh before returning credentials. Concurrent
lookups that need a refresh for that registry must share one in-flight fetch
and its outcome. This includes calls through different credential functions
created from the same provider, direct `Credential` calls, and credential
functions obtained from one configured AWS ECR store option. A token with no
expiry may satisfy the current lookup but must not be retained for reuse.
An already expired token must not be returned as usable credentials.

Treat each requested registry as a separate authentication and concurrency
boundary. When ECR returns authorization records for several registries, use
the record whose `ProxyEndpoint` identifies the requested registry, regardless
of record order. Reject a response that has no matching endpoint; do not return
another registry's credentials. Requests use registry host names, and AWS
endpoints use HTTPS URLs with an optional trailing slash. A blocked or failing
refresh for one registry must not block a different registry or substitute its
token. A credential function bound to one registry must keep returning empty
credentials without an AWS request when called for a different host.

Cancellation belongs to each caller. A caller whose context is cancelled while
waiting for credentials must return promptly with that context error, even if
the AWS request is still blocked. This applies to the caller that initiated the
shared refresh as well as callers that joined it. Cancelling one caller must
not cancel a refresh still needed by another active caller, corrupt the cache,
or make that other caller start a duplicate fetch. An already cancelled caller
must not initiate a fetch or receive cached credentials. You may choose the
policy for a fetch that has no remaining callers.

Failures must leave the provider usable: all callers sharing a failed refresh
must observe an error, and a subsequent lookup must be able to retry and cache
a successful result. Never return an expired cached token as a fallback when a
refresh fails. Malformed tokens, missing authorization records, AWS errors,
and configuration errors must remain errors and must not poison future
lookups. Preserve the existing low-level token decoding/error contracts and
the existing public authentication APIs. Existing static authentication,
configuration loading and validation, and unauthenticated storage must keep
working. Concurrent use must be safe under Go's race detector.

Add focused regression tests for the behavior and document the lifecycle
semantics for maintainers. Choose the implementation and test structure that
best fits the repository. Do not look up existing solutions or external PRs.
