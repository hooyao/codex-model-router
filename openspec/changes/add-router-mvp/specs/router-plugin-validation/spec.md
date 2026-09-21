# Spec Delta

## Purpose

Enable repeatable offline verification that the router plugin package and its
deterministic lifecycle-hook outputs are safe to evaluate before installation.

## ADDED Requirements

### Requirement: Plugin package validation
The MVP SHALL provide an offline validation command that checks the plugin
manifest, the lifecycle-hook configuration, the bundled routing policy, and all
referenced local files. The command MUST return a non-zero exit status and name
the failing item when a required artifact is missing or malformed.

#### Scenario: The package is complete
- **WHEN** the validation command runs against a complete plugin package
- **THEN** it exits successfully without requiring network access or live model calls

#### Scenario: A required plugin file is missing
- **WHEN** the validation command finds a manifest reference to a missing local file
- **THEN** it exits with a non-zero status and identifies the missing file

### Requirement: Hook output validation
The MVP SHALL provide offline checks for representative SessionStart,
UserPromptSubmit, and SubagentStart events. Each check MUST verify that the
corresponding hook produces valid JSON containing model-visible developer
context and the correct hook event name.

#### Scenario: A representative hook event is evaluated
- **WHEN** the validation command evaluates a supported representative hook event
- **THEN** it confirms that the hook output is valid JSON with matching event metadata and non-empty additional context
