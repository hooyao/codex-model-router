# Approved 1.3.3 live repair validation

Completed: 30/30 artifact checks passed with complete response accounting.
The API-price estimate was $4.759797715. Overall performance did not pass
because serial escalation exceeded the token/cost thresholds. See the
[retained report](REPORT.md); acceptance rules below are unchanged.
The user subsequently [accepted the measured serial tradeoff](ACCEPTANCE.md)
for PR #10 as an exception; the original benchmark rules and scores remain fixed.

The user approved five scenarios, three fresh matched pairs per scenario
(30 arms), and an additional $7 observed API-price-estimate stop on 2026-10-29.
Actual copilot-bridge billing and upstream identity remain unverified. The
[original prepared plan](../context-repair-20261028/PLAN.md) supplies the frozen
protocol; no earlier estimate allowance or scored arm is reused.

- Before: `e75ef8785d960b12e6d1ce27ad4c6b184b8a0fc0`, plugin 1.3.0.
- After: `9f72e6aead1b28e11e7e8da385609cf1ba11f698`, plugin 1.3.3.
- Both controllers: gpt-6.1-sol/low, same configured provider and runtime.
- Cases: direct-small-control, investigation-reuse, serial-escalation,
  parallel-disjoint, architecture-review. Use frozen prompts/start states and
  sequential AB / BA / AB ordering in fresh isolated installations.
- Discover CLI capabilities from PATH or `--cli`, with no version whitelist.
  Reuse native/cancellation platform receipts only if observed binary/schema,
  exposed native options, and security configuration match their provenance.
  New plugin behavior is established only by this cohort's scored records.
- Limits: $1 observed per arm, 240 seconds per arm, 5,400 seconds matrix wall,
  and $7 observed total. Usage arrives after responses, so these estimates
  cannot guarantee a provider billing cap. No automatic whole-run retries,
  paid setup probes, or extra experiments are included.

Four fixtures retain exact-tree-v2. Architecture retains the exact typed
compact JSON object and unchanged-file oracle, allowing unspecified terminal
line breaks for both arms. Every pair must complete without artifact loss.
For each case require median paired after/before wall ratio <=1.15 and total-
token and common dated Standard API-price-estimate ratios <=1.05. Include all
parent/child input/output/cache-write classes; reasoning is part of output.
Keep the same wall boundary. Retain all failures and unknowns, and report
routing/receipt observations separately from artifact quality.

Freeze installed subject bytes, runner/adapter hashes, fixtures, capabilities,
rate card, and oracle before calls. Do not change them during the cohort.
Preserve the failed 1.3.1 and 1.3.2 cohorts unchanged. No general stable-latency
or Terra frontier claim follows from three small-task pairs. Provider/auth
configuration and native prompt-bearing events remain private locally.
