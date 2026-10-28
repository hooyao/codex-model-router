# Terra matrix diagnostic, 2026-10-28

This is a small live diagnostic of requested model selectors through the
configured `copilot-bridge`, not a validation of overall router performance.
It uses CLI 0.165.0-alpha.1.2, a neutral local working directory, read-only
sandbox, and the same configured provider for each call. Successful responses
establish that the bridge accepted the selectors; the CLI evidence does not
independently establish which upstream models or efforts the bridge actually
used. Model charges and provider billing are unknown.

## Method and results

Four fixed cases were each run three times per selector, with model order
rotated across cases and repetitions. The selectors and efforts were Luna/low,
Terra/medium, Sol/medium, and Astra/high. The visual case asks for a geometric
match in a generated image; the other cases test fixed-label classification,
the result of a small code snippet, and a short customer notice. The first
three use exact-answer grading. The notice was reviewed by the operator for
the four specified facts, absence of an invented promise, and a 40–70 word
limit, counting whitespace-separated words. This is not an independent or
blinded writing review.

| Requested model | Visual | Classification | Code reading | Writing |
| --- | ---: | ---: | ---: | ---: |
| Luna/low | 3/3 | 3/3 | 3/3 | 0/3 |
| Terra/medium | 3/3 | 3/3 | 3/3 | 3/3 |
| Sol/medium | 3/3 | 3/3 | 3/3 | 3/3 |
| Astra/high | 3/3 | 3/3 | 3/3 | 2/3 |

Every notice contained the specified facts and made no zero-disruption
promise. Luna's notices had 34 words each; Astra's third had 38. Terra's had
41, 43, and 41; Sol's had 42, 42, and 43.

Median end-to-end wall seconds, measured from CLI process launch through exit:

| Requested model | Visual | Classification | Code reading | Writing |
| --- | ---: | ---: | ---: | ---: |
| Luna/low | 5.833 | 4.600 | 4.925 | 5.064 |
| Terra/medium | 5.663 | 5.673 | 5.383 | 7.304 |
| Sol/medium | 5.451 | 6.058 | 5.475 | 6.606 |
| Astra/high | 6.256 | 5.466 | 6.339 | 12.598 |

All 48 measured calls completed without a tool call. CLI startup and user
configuration loading are included; CLI-reported token counts and the full
responses are retained in [results.jsonl](results.jsonl). No model identity or
cost can be inferred from those counts alone. Each cell has only one task
repeated three times. These observations support testing Terra for bounded
visual and writing work but do not establish that it is the fastest, cheapest,
or most reliable choice for a broader workload. They do not measure actual
computer interaction, hard-kernel capability, quality across a representative
task distribution, selective routing, or the long-horizon Astra/xhigh baseline.
Follow the [performance protocol](../PERFORMANCE_PROTOCOL.md) before making
router quality, cost, or speed claims.

## Evidence and reproduction

The run used [run.py](run.py) and [visual-match.png](visual-match.png); the
manifest in `results.jsonl` binds their SHA-256 hashes, prompts, answer keys,
CLI binary hash, model selectors, and order. The results SHA-256 is
`c00026f48bdac5db358467d44aa2f155ab826a6263cec4a3fdd7fad4cdab7080`.
All inputs were synthetic and contained no user workspace material. To repeat,
use a configured provider and a new evidence path:

```powershell
python evals/terra_matrix_smoke/run.py --live --output <new-results.jsonl> --workdir <neutral-existing-directory>
```

The prior [aborted attempt](results-aborted-cli-args.jsonl) is retained. Its
four visual invocations exited before a model request because the CLI consumed
the prompt as an image argument; two subsequent classification requests
completed before the run was stopped. After fixing the argument order, the
complete campaign started afresh. Before it, the four selectors each received
a trivial availability probe and Terra received one visual probe. Those
warmups and the incomplete attempt are excluded from the table, but may have
affected caching; they are not evidence of performance. The run deliberately
stops on an incomplete call rather than treating it as a valid comparison.
