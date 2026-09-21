# Spec Delta

## Purpose

Provide the primary Codex agent with consistent, compact routing guidance at
session and prompt boundaries so it can make delegation decisions deliberately.

## ADDED Requirements

### Requirement: Controller session context
The plugin SHALL provide the primary agent with developer context when a root
session starts. The context MUST identify the active controller model, state
that the controller retains responsibility for planning, verification, and
final synthesis, and require a delegation decision before parallel work begins.

#### Scenario: A root session starts
- **WHEN** an enabled router plugin receives a root-session start event
- **THEN** the primary agent receives concise developer context describing the delegation gate and controller responsibilities

### Requirement: Per-prompt delegation gate
The plugin SHALL provide the primary agent with developer context for every
submitted user prompt. The context MUST require the agent to decide whether the
request is better handled directly or decomposed into bounded tasks before it
starts delegated work.

#### Scenario: A user submits a request
- **WHEN** a user prompt is submitted in an enabled router session
- **THEN** the primary agent receives a concise instruction to evaluate delegation, dependencies, write conflicts, and the need for verification

### Requirement: Model-routing policy
The injected controller context SHALL direct the primary agent to select the
lowest-capability available worker that can reliably satisfy each bounded task.
It MUST map clear and repeatable tasks to Luna, everyday tool-using or
read-heavy tasks to Terra, complex open-ended work to Sol, and high-risk or
sustained-judgment work to Astra. The context MUST also direct the controller
to begin with the lowest suitable reasoning effort and escalate only when task
complexity, ambiguity, risk, or failed verification warrants it.

#### Scenario: The controller routes a clear task
- **WHEN** a bounded task has explicit inputs, an expected output, and no material ambiguity
- **THEN** the routing policy identifies Luna at low or medium reasoning effort as the preferred worker class

#### Scenario: The controller routes a high-risk task
- **WHEN** a bounded task requires sustained judgment or has a high cost of an incorrect decision
- **THEN** the routing policy identifies Astra with high or higher supported reasoning effort as the preferred worker class
