# Codex Model Router

## Project Purpose

Build a Codex plugin that helps the primary agent:

1. Decompose a user request into bounded stages and identify the hard kernel that needs the strongest model.
2. Identify dependencies and safe opportunities for parallel execution.
3. Keep genuinely easy, low-risk stages with a lightweight primary when their local context cost is small; otherwise dispatch each stage to a suitable worker model and reasoning effort.
4. Reduce end-to-end latency and token cost without sacrificing correctness.
5. Collect, verify, and synthesize subagent results into one coherent outcome.

## Language Policy

- Use English for all source code, documentation, configuration, prompts, tests, examples, UI text, issue text, and commit messages.
- Use clear, direct technical English.
- Preserve non-English text only when it is user-provided test data or is required for localization coverage.

## Product Principles

- Optimize for correctness first, then total cost; seek latency gains where comparable end-to-end measurements support them.
- Keep task decomposition explicit, bounded, and easy to inspect.
- Dispatch only work that can be completed independently or has clearly declared dependencies.
- Keep the primary agent responsible for coordination, conflict resolution, verification, and the final response.
- Partition by capability: reserve GPT-6 Astra at xhigh for demanding hard-kernel work, use max only for exceptional cases, and route easy stages to the least expensive reliable executor. A lightweight controller may execute a stage only after that stage independently satisfies the bounded direct gate.
- Record each stage's owner, dependencies, write scope, context budget, acceptance criteria, and self-check. Do not duplicate worker-owned work in the controller or claim that a worker's context is technically isolated without runtime evidence.
- Prefer the least expensive model and lowest reasoning effort that can reliably complete a task.
- Escalate model capability or reasoning effort when task complexity, ambiguity, risk, or failed verification requires it.
- Discover available models and supported reasoning efforts at runtime when the platform exposes that information. Do not assume a hard-coded catalog is always current.
- Base model-routing guidance on current official OpenAI documentation and runtime capabilities.
- Make routing decisions explainable through concise, structured rationale and observable signals.
- Treat token savings as a constraint, not as permission to omit necessary context or validation.

## Plugin Structure

- Keep `.codex-plugin/plugin.json` present in the plugin root once the plugin scaffold is created.
- Keep the plugin directory name and the manifest `name` identical and normalized.
- Add only plugin capabilities that have corresponding implementation files.
- Prefer small, composable skills, scripts, and tools over a monolithic orchestration prompt.
- Separate task analysis, routing policy, dispatch, result collection, and verification so each part can be tested independently.

## Engineering Guidelines

- Do not choose a programming language, framework, or package manager until the relevant design decision is documented.
- Keep routing policy data-driven and deterministic where possible.
- Validate all structured model output before using it for dispatch.
- Define schemas for task plans, dependency graphs, routing decisions, execution results, and verification outcomes.
- Prevent recursive delegation loops and enforce explicit limits for depth, concurrency, retries, and token budgets.
- Preserve user intent, permissions, workspace boundaries, and safety constraints across every delegated task.
- Avoid sending unrelated repository context to subagents. Provide only the context required for their assigned task.
- Never expose secrets, credentials, private prompts, or unrelated user data in subagent inputs or logs.
- Design for partial failure: retain successful results, report failed tasks clearly, and retry or escalate only when justified.
- Use stable identifiers for tasks and record enough metadata to reproduce routing decisions during development.

## Testing and Evaluation

- Add unit tests for decomposition, dependency validation, routing rules, budget enforcement, and result synthesis.
- Add integration tests for multi-agent dispatch, cancellation, retry, timeout, and partial-failure behavior.
- Maintain representative evaluation cases covering simple, complex, ambiguous, high-risk, sequential, and parallelizable tasks.
- Measure answer quality, completion time, token usage, estimated cost, retry rate, and routing accuracy.
- Compare routing changes against a fixed baseline before claiming cost or latency improvements.
- For selective-routing performance claims, compare the same task and start state run by Astra/xhigh alone against a lightweight Luna or Sol/low controller with Astra/xhigh assigned only the hard kernel. Quality parity and total estimated or billed USD, including orchestration and easy-stage work, are the primary decision metrics. Report end-to-end wall time using the same start and end boundary; use per-stage and critical-path timings only to diagnose where time went. Do not claim a speed advantage from one run, synthetic spans, or model-call counts; require repeated interleaved matched runs before treating latency differences as stable. Keep regression checks for quality and cost. A prior whole-request Sol-plus-Sol comparison does not answer this question.
- Mock external model calls in deterministic tests. Keep live-model evaluations separate and explicitly enabled.
- Run the narrowest relevant checks during development and the full validation suite before release.

## Change Workflow

- Inspect existing files and repository instructions before editing.
- Keep changes scoped to the requested behavior and preserve unrelated user work.
- Update documentation and tests with behavior changes.
- Validate the plugin manifest and any skill metadata with the official scaffold validators before handoff.
- Document important architectural decisions and routing-policy changes.
- Do not claim performance or cost improvements without reproducible evidence.

## Current Repository State

- The plugin implementation, routing policy, tests, and evaluation harness are present. Use their documented commands and preserve live evaluation evidence.
