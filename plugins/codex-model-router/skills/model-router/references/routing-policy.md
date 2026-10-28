# Worker model selection

Use this reference only when selecting or escalating a delegated worker.
Ownership and topology come from the decision resolver; packet/collection
rules are in [delegation.md](delegation.md).

The validated workspace config is canonical for examples, efforts, and local
rationales. Read it when its injected digest changes or it is unavailable in
context. Model classes are preferences, not runtime identifiers or proof of
availability. Verify the exact model, effort, modalities, and tools against the
current runtime before dispatch.

| Task profile | Preferred role | Starting effort |
| --- | --- | --- |
| Clear repeatable work, fixed-format summaries | GPT-6 Luna | Low, then Medium if needed |
| Bounded visual comparison, precise screen work, natural writing (schema v4) | GPT-6.1 Terra | Medium |
| Everyday coding, repository discovery, technical writing, triage, routine review | GPT-6.1 Sol | Medium; High for difficult cases |
| Complex implementation, integration, open-ended analysis | GPT-6.1 Sol | High; Xhigh if needed |
| Architecture, security, critical judgment, independent router meta-task review | GPT-6 Astra | High; Xhigh for demanding work |

Use the least expensive available model and lowest supported effort that can
meet the acceptance criteria. Reserve Astra/xhigh for demanding hard kernels;
max is exceptional after xhigh is inadequate. The plugin selects only low,
medium, high, xhigh, and max, subject to runtime support. Escalate for ambiguity,
risk, or failed verification. No capable available worker means a blocker.

Sol prefers gpt-6.1-sol; gpt-6-sol is a compatibility fallback only when the
preferred model/effort is unavailable and the runtime confirms the fallback.
Resolve Terra to gpt-6.1-terra, Luna to gpt-6-luna, and Astra to gpt-6-astra only
when exposed with the requested effort. If Terra is absent, choose another
available model with the task's required capabilities or report a blocker.

Schema-v3 Terra examples keep their legacy in-memory mapping to Sol; apply the
Terra role and fallback only to schema v4. Do not rewrite local customizations.
With no matching example, use the capability-based roles above; v3 retains the
Luna/Sol/Astra fallback. High-stakes or ambiguous visual judgments and demanding
multi-application workflows warrant Astra. Fixed-format writing remains Luna
work; technical writing with repository judgment remains Sol work.

For router self-improvement, routing-policy review, evaluation design, and
benchmark selection, select an independent reviewer separate from the
implementer, using the highest suitable available model. This review signal
sets INDEPENDENT_REVIEW and applies to follow-ups as well.

The Terra guidance follows the official [model page](https://developers.openai.com/api/docs/models/gpt-6.1-terra),
[GPT-6 guide](https://developers.openai.com/api/docs/guides/latest-model), and
[Codex models guide](https://developers.openai.com/codex/models), consulted on
2026-10-28. These are workload hypotheses; the [Terra note](../../../docs/terra-support.md)
records diagnostic evidence and limitations. Verify current exact-model prices
before estimating evaluation cost; API rates do not establish Codex billing.
For performance claims, use the repository's [matched performance protocol](../../../../../evals/PERFORMANCE_PROTOCOL.md),
including quality parity, all-session cost, and repeated interleaved runs with
one end-to-end timing boundary. Synthetic spans and call counts do not prove
speed or savings.
