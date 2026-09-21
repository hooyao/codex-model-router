# Tasks

## 1. Package and Policy

- [x] 1.1 Create the root plugin manifest and repository marketplace entry for `codex-model-router`, then verify that both JSON files parse and that the marketplace source resolves to the plugin root.
- [x] 1.2 Add the bundled `model-router` Skill and routing-policy reference covering the delegation gate, task packet, role-based Luna/Terra/Sol/Astra selection, dependency handling, and overlapping-write serialization; verify all required policy terms are present.
- [x] 1.3 Add installation and hook-trust guidance for the local MVP, then verify the documented paths and commands match the packaged files.

## 2. Lifecycle Context Injection

- [x] 2.1 Implement a standard-library Python hook program that accepts lifecycle event JSON on standard input and emits valid developer-context output for `SessionStart`, `UserPromptSubmit`, and `SubagentStart`; verify each representative fixture produces parsable JSON with matching event metadata.
- [x] 2.2 Implement concise controller contexts that observe the active model, require the delegation gate, route workers by role, and retain primary-agent responsibility; verify the SessionStart and UserPromptSubmit fixture outputs include these obligations.
- [x] 2.3 Implement the worker context that restricts scope, prohibits recursive delegation by default, and requires a concise structured final result; verify the SubagentStart fixture output includes all contract fields.
- [x] 2.4 Add plugin lifecycle-hook configuration with portable and Windows command forms for the three supported events; verify every configured command targets the packaged hook program.

## 3. Offline Validation

- [x] 3.1 Implement an offline validation command that checks manifest metadata, hook configuration, routing-policy files, and all referenced local package paths; verify a complete package exits successfully without network access.
- [x] 3.2 Add negative validation coverage for a missing referenced file and malformed hook output; verify each case exits non-zero and names the failing item.
- [x] 3.3 Add fixture-based automated tests for all three hook events and the package validator; verify the complete test suite passes with the standard library runtime.

## 4. Release Readiness

- [x] 4.1 Run the plugin-package validator and strict OpenSpec validation, then verify both commands complete successfully.
- [x] 4.2 Review the final file tree and documentation against the MVP non-goals, then verify no MCP service, credential, network requirement, or dispatch-enforcement hook was introduced.
