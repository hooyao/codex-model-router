# GPT-6.1 Terra routing

## Evidence and recommendation

On 2026-10-28, the official [Terra model page](https://developers.openai.com/api/docs/models/gpt-6.1-terra),
[GPT-6 guide](https://developers.openai.com/api/docs/guides/latest-model), and
[Codex model guide](https://developers.openai.com/codex/models) described
`gpt-6.1-terra` as a fast, efficient model for visual reasoning, precise
computer use, and natural writing. The guide recommends starting at medium
effort and increasing it when quality justifies the extra time and tokens.
It also identifies Sol with complex work, Astra with the most demanding work,
and Luna with focused, high-volume work. The Codex CLI guide requires version
0.159.0 or later for Terra.

The recommended Terra examples are bounded visual comparisons, screen-based
work, and nontechnical prose. They are **provisional, documentation-based
hypotheses**, not demonstrated quality, latency, or cost advantages. Ambiguous
or consequential visual work, complex workflows, and code-heavy writing have
separate higher-capability routes. Actual selection still requires the live
runtime catalog, the requested effort, the required image/UI tools, and the
task's risk and verification requirements.

The current desktop runtime advertised `gpt-6.1-terra` with low, medium, high,
xhigh, and max. On 2026-10-28 CLI 0.165.0-alpha.1.2 successfully completed a
fixed no-tool response request with `--model gpt-6.1-terra` and medium effort
using the configured `copilot-bridge` provider. The CLI reports the requested
selector and successful response; this does not independently establish the
bridge's upstream model mapping. An earlier attempt with `--ignore-user-config`
failed with HTTP 401 because it bypassed the configured bridge; it is not an
availability result for the user's provider. The API model page lists `none`
as well, but this plugin only routes low through max and must follow the
runtime's actual supported efforts.

The model pages on that date listed Standard text input/output prices per
million tokens: [Luna](https://developers.openai.com/api/docs/models/gpt-6-luna)
$0.10/$0.50, Terra $1/$5,
[Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol) $2/$10, and
[Astra](https://developers.openai.com/api/docs/models/gpt-6-astra) $10/$50.
These are API token rates, not Codex subscription charges or measured task
costs. Cached input, cache writes, long contexts, and service tier can change
the calculation; see each current model page before use. Price per token does
not establish cost per completed task, quality, or speed.

## Workspace adoption

New workspaces receive schema v4. Existing workspace files are never
overwritten: v3 Terra examples keep their historical in-memory mapping to Sol.
To adopt v4, back up the customized file, change each legacy Terra preference
to Sol to preserve its prior behavior, set `schema_version` to 4, then add the
reviewed Terra examples and `runtime_resolution` from the new template. Keep
local execution modes, rationale, limits, and other customizations. Reinstall
the updated plugin and start a new task so its hooks and skills are loaded.

## Performance validation still required

A small [bridge diagnostic](../../../evals/terra_matrix_smoke/REPORT.md)
compares four requested selectors on four simple tasks. It is not a
representative performance validation and does not establish upstream model
identity or billed cost. Before calling the matrix performance-validated, run
predeclared, representative tasks for the three new categories: visual
comparison with independently checked labels, permitted screen interaction
with independently checked final state and recovery, and natural writing with
blinded rubric-based review. Include fixed-format and technical-writing
boundary cases, as well as ambiguous/high-risk visual cases. Compare Terra
medium against the current candidate route on identical inputs, tools, starting
state, and acceptance criteria; record all failures and escalation. Use
repeated interleaved runs, observed usage for every session, current dated
model-specific prices, and one end-to-end timing boundary. Report quality,
completion, latency, and total cost with uncertainty. Do not infer speed or
savings from the rate card or one run.

For whole-router claims, also follow the existing
[performance protocol](../../../evals/PERFORMANCE_PROTOCOL.md), including
the matched Astra/xhigh baseline and last accepted plugin comparison. The
existing frozen selective pilot has an unresolved packet-scope evidence gate;
its historical results must not be relabeled as Terra measurements.
