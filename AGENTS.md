# Codex Model Router

## Project Purpose

Build a Codex plugin that helps the primary agent:

1. Decompose a user request into small, independently executable tasks.
2. Identify dependencies and safe opportunities for parallel execution.
3. Dispatch each task to a suitable subagent model and reasoning effort.
4. Reduce end-to-end latency and token cost without sacrificing correctness.
5. Collect, verify, and synthesize subagent results into one coherent outcome.

## Language Policy

- Use English for all source code, documentation, configuration, prompts, tests, examples, UI text, issue text, and commit messages.
- Use clear, direct technical English.
- Preserve non-English text only when it is user-provided test data or is required for localization coverage.

## Product Principles

- Optimize for correctness first, then latency and token cost.
- Keep task decomposition explicit, bounded, and easy to inspect.
- Dispatch only work that can be completed independently or has clearly declared dependencies.
- Keep the primary agent responsible for coordination, conflict resolution, verification, and the final response.
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

- The repository is intentionally initialized with instructions only.
- The implementation stack, plugin capabilities, schemas, and routing policy are still to be designed.
- Do not infer build, test, lint, or release commands until the corresponding tooling is added.
