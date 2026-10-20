# 2026-10-20 OCI comparison

Both successful arms passed all ten independent semantic requirements and the
hidden behavior, race, and backend checks. Costs include every recorded solver,
controller, child, and independent evaluator response in each successful arm.
They are estimates under the experiment's 2026-09-24 price card, not billed USD.

| Metric | Astra/xhigh baseline d | Sol/low + Astra/xhigh treatment e |
| --- | ---: | ---: |
| Full quality | PASS | PASS |
| Estimated USD, complete | 10.6858765 | 5.9174195 |
| Runner wall seconds | 2537.360 | 1641.250 |
| Common quality-completion seconds | 2537.232 | 1641.044 |
| Responses, including evaluation | 74 | 132 |
| Input tokens | 5,721,758 | 7,244,381 |
| Cached input subset | 5,518,469 | 7,024,645 |
| Output tokens | 52,537 | 37,595 |
| Quality corrections | 0 | 0 |

The successful treatment's estimated cost was $4.768457 lower, or 44.6239%, for
this task. Input tokens increased 26.6%; output tokens decreased 28.4%. This
single baseline-first sample does not establish stable latency or general
savings. Repeated interleaved runs were not performed.

## Actor usage

| Arm / actor | Model / effort | Responses | Input | Cached input | Output | Estimated USD |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Baseline primary | Astra / xhigh | 62 | 5,137,566 | 4,997,769 | 48,665 | 9.1780165 |
| Baseline evaluator | Astra / xhigh | 12 | 584,192 | 520,700 | 3,872 | 1.5078600 |
| Treatment controller | Sol / low | 93 | 5,245,935 | 5,171,220 | 20,678 | 1.4259695 |
| Treatment worker | Astra / xhigh | 24 | 1,246,963 | 1,170,333 | 8,382 | 2.5268330 |
| Treatment evaluator | Astra / xhigh | 15 | 751,483 | 683,092 | 8,535 | 1.9646170 |

Cached input is a subset of input, and reasoning tokens are included in output.
Controller usage includes direct easy implementation and coordination; it is
not a measurement of pure orchestration overhead. Full token classes and
artifact SHA-256 values are in the [sanitized metrics](2026-10-20-comparison.json).

The same Astra worker completed two native turns. R1 used eight priced
responses, estimated $0.846738, and five native `exec` calls over 91.713 seconds.
R2 used sixteen priced responses, estimated $1.680095, and six `exec` calls over
237.384 seconds. Both completed after their public reveal and before the
corresponding accepted checkpoint. No target child count was imposed.

## Starting state, timing, and retained failures

Both arms used the same source and seed hashes, actual Git start tree, task
manifest, shared initial prompt, public reveals, actor policy, verified CLI
0.163.0-alpha.10.2, installed hook code, price card, and full quality profile.
Preparation commit IDs differ while the start trees match. Treatment e began
from the original R0 seed after a treatment-only source-association fix;
baseline d was retained because that fix does not affect its execution path.

The common boundary is runner start before App Server launch through the
retained `final-quality` event, including quality review and grading. Exact
durations are 2537.2319654 and 1641.0436867 seconds. Baseline d's original
end-to-end file retains 2537.865 seconds, including its short finalizer tail.
The comparison derives the same event boundary for both arms and excludes
later finalizer repair time. The original run and baseline wall receipts were
not rewritten.

| Earlier treatment | Observed failure | Known solver USD | Whole cost |
| --- | --- | ---: | --- |
| c, 2026-10-10 | Child upstream stream failed before reported usage; terminal error was initially obscured by the usage grace failure | 0.8382511 | UNKNOWN |
| d, 2026-10-20 | Validator assumed an undocumented plan basename and interrupted the spawn | 0.6787321 | UNKNOWN |

These failed attempts remain preserved. Total cost across all attempts is
UNKNOWN. Encrypted effective packet scope remains UNKNOWN, so the result does
not establish technical context isolation. Private prompts and raw transport
events remain local; the public summary alone cannot replay all measured
cost and quality evidence.

## Fixes exercised

- Classify terminal events carrying runtime errors as failures and report the
  concrete cause immediately while retaining UNKNOWN for missing usage.
- Bind a saved dispatch plan by unique local content, preserving identity,
  timestamp, size, and scope checks instead of requiring one basename.
- Resolve hard-stage membership from priced native turns and accepted parent
  rounds instead of model-authored private-ID placeholders.
- Bind the local observed stop, up to $30, through runner and evaluator guards.
- Use retained quality completion timestamps when replaying finalization.

The canonical manifest remains `live_enabled=false`. A fresh local config is
required for an authorized paid reproduction; no automatic whole-arm retry is
enabled.

## Reproduction and evidence

Run offline checks from the repository root:

```text
python plugins/codex-model-router/scripts/validate_plugin.py --baseline <target-merge-base>
python -m unittest discover -s plugins/codex-model-router/tests -v
python -m unittest discover -s evals/tests -v
```

For an authorized live reproduction, use a new ignored config under `_scratch`
with fresh preparation/evidence paths, `live_enabled: true`, and
`dispatch_stop_usd: 30.0`; set its absolute path in
`LONG_HORIZON_LOCAL_RUN_CONFIG`. The [benchmark README](../README.md) documents
the config fields. Prepare the original seed and bind the current runtime with
these entrypoints:

```text
python -m evals.long_horizon_v1.prepare --destination <fresh-preparation-root>
python -m evals.long_horizon_v1.live_preflight --no-model-rebind --expected-final-manifest-sha256 <manifest-sha256>
python -m evals.long_horizon_v1.quality_adapter --watch-root <arm-output>/quality --prepared <fresh-preparation-root> --cli <verified-cli> --session-root <sessions>
python -m evals.long_horizon_v1.run --prepared <fresh-preparation-root> --output <arm-output> --arm <baseline-or-treatment> --live --cli <verified-cli> --capability <arm-capability> --session-root <sessions>
python -m evals.long_horizon_v1.end_to_end --prepared <fresh-preparation-root> --arm <baseline-or-treatment>
```

Run the quality watcher in a second terminal with the same config environment.
Complete baseline and its full quality checks before starting treatment.
Each arm retains the original 4500-second clock and combined solver/evaluator
stop; reuse of the published successful baseline here is recorded explicitly.

Local evidence roots are `_scratch/fresh-pair-20261020-d-evidence` for baseline
and `_scratch/fresh-pair-20261020-e-evidence` for treatment. Their `run.json`
SHA-256 values are respectively
`8ef3384226b7538e32ad977a450945c4726f211dbe0b595d15f0eaf7d06f1c08`
and `a16891e66f7de8955ba180b60131798ec3a47c2756ba9dc1a3a94157999f4129`.
The metrics JSON contains the wall, request, response, semantic, hidden/race,
and backend receipt hashes and the two candidate patch hashes.
