# Explicit Astra xhigh request delivery

The benchmark requires the exact primary model `gpt-6-astra` and explicit
Responses request field `reasoning.effort = "xhigh"` in both arms. A configured
effort alone is not proof that the CLI sent it.

## Cause and supported configuration

CLI 0.144.1 reported missing metadata for `gpt-6-astra` and used fallback model
metadata. The saved native preflight commands already contained
`--model gpt-6-astra --config 'model_reasoning_effort="xhigh"'`, but every captured
Astra primary request contained `"reasoning": null`. The Luna worker, whose
metadata was present, did send its selected `low` effort.

The official [configuration reference](https://developers.openai.com/codex/config-reference/)
describes `model_supports_reasoning_summaries` as forcing Codex to send or not
send reasoning metadata. Despite the setting's name, in this CLI it gates the
whole Responses `reasoning` object, including `effort`.

The generated isolated configuration now contains these top-level settings:

```toml
model = "gpt-6-astra"
model_reasoning_effort = "xhigh"
model_supports_reasoning_summaries = true
model_provider = "copilot-bridge"

[model_providers.copilot-bridge]
wire_api = "responses"
# Connection/authentication fields come only from the explicit transport input.
```

The common command builder also supplies the metadata override explicitly:

```text
codex exec --model gpt-6-astra --config 'model_reasoning_effort="xhigh"' --config model_supports_reasoning_summaries=true
```

The actual harness additionally supplies its ephemeral, isolation, sandbox,
approval, task-directory, and JSONL flags. The override is not a model alias,
provider change, synthetic model catalog, or request-rewriting proxy. Native
workers retain their router-selected model and effort overrides.

## Zero-model evidence and admission gate

Both arms were tested under the `copilot-bridge` provider ID with
`wire_api = "responses"`. A credential-free loopback sink replaced the network
destination and rejected every request with HTTP 400. Nothing was forwarded to
the bridge or any model. The negative controls emitted:

```json
{"model":"gpt-6-astra","reasoning":null}
```

The same CLI with `model_supports_reasoning_summaries=true` emitted, in both arms:

```json
{"model":"gpt-6-astra","reasoning":{"effort":"xhigh","summary":"auto"}}
```

The mandatory offline receipt now validates this exact model and effort in the
raw request body before validating hook delivery. Missing/null reasoning,
another effort, case changes, model aliases, and another model all fail. The
saved receipt records the observed pair for each arm and is revalidated before
paid treatment probes or either formal admission path. Changed code/config
bindings invalidate earlier receipts.

The normal deterministic tests check both command constructions, parsed TOML,
and rejection of invalid captured request bodies. An opt-in integration test
runs the installed CLI only against the rejecting sink, checking both arms and
negative controls with the support flag forced false:

```powershell
$env:EVALPLUS_OFFLINE_CLI_TEST = '1'
py -3.13 -m unittest discover -s evals/tests -q
```

Local raw evidence is retained under
`Q:\MyProjects\codex-model-router-benchmarks\astra-xhigh-repair-20260922`:
`diagnosis` contains default/override comparisons; `verification/hook-delivery`
contains final baseline/router requests and the full compact-context and worker
simulation checks. `verification/hook-delivery.json` is the bound receipt.

This removes the request-serialization blocker on CLI 0.144.1. The missing
model-metadata warning can still appear; unrelated fallback capabilities are
not fabricated or repaired here. The sink verifies what Codex emits, not whether
the bridge/backend accepts or honors the requested effort. A fresh explicitly
authorized native paid preflight is warranted before formal slots. This repair
runs no paid preflight, model invocation, actual worker, or formal benchmark slot.
