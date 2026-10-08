# GPT-6.1 Sol preference

As of 2026-10-06, the advisory Sol role prefers `gpt-6.1-sol` for coding,
technical discovery, documentation, triage, integration, and routine review.
This follows the official [Codex model guidance](https://developers.openai.com/codex/models).
Astra and Luna retain their workload roles. Ownership, dependency, review,
budget, and capability-evidence gates are unchanged.

The controller must confirm the exact model and requested effort in the
current runtime catalog. When that preferred model/effort is unavailable,
`gpt-6-sol` is an explicit compatibility fallback only if the runtime confirms
that fallback model/effort. The router permits `low`, `medium`, `high`, `xhigh`,
and `max` subject to runtime support, and does not select `ultra`. Canonical
worker names normalize the dot, for example `edit-gpt-6-1-sol-high`, while the
native dispatch selector remains exactly `gpt-6.1-sol`.

## Workspace adoption

The template remains schema v3. Reinstalling creates a missing workspace
config but preserves an existing one. Review local customizations before
updating `runtime_resolution` to the new preference; preserve example IDs,
execution modes, efforts, rationales, and local policy. Legacy `Terra` model
classes still normalize to the Sol role in memory without rewriting the file.
Schema v1/v2 files require an explicit migration to v3 and an original backup;
the loader continues to reject obsolete schemas rather than silently migrate.

Source changes require the normal release bump, reinstall through the existing
marketplace, and a new task to load the updated skills and hooks. The plugin
does not change the active controller model or personal Codex configuration.
An operator may select the model in Codex or use `codex --model gpt-6.1-sol`;
official saved configuration uses `model = "gpt-6.1-sol"`.

## Evaluation boundaries

The general App Server probe now defaults to `gpt-6.1-sol/low`, while explicitly
accepting older `gpt-6-sol/low` and `gpt-6-luna/medium` parent selectors. Its
synthetic dry run reflects that default and proves no live capability. A
runtime response using a different model remains a mismatch, not evidence of
the requested model's availability.

Paired campaign configs, historical calibration, live transcripts, fixtures,
and the fixed-sentinel protocol retain their original model IDs and prices.
The paired explicit-capture checker remains pinned to its older GPT-6 Sol
capture. A new-default probe receipt cannot replace that hash-bound evidence.
Keep `--model gpt-6-sol --effort low` when reproducing that historical protocol.

Before evaluating GPT-6.1 Sol with the paired runner, create a separately
versioned campaign with fresh catalog, schema, calibration, plugin and CLI
pins, and verified exact-model pricing. The existing paired meter has no
GPT-6.1 Sol rate and marks an unpriced model as unknown; do not relabel older
results or copy GPT-6 Sol rates to the new model ID. This upgrade makes no
quality, cost, or latency improvement claim and runs no paid evaluation.
