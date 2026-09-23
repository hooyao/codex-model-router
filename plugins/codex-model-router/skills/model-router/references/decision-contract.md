# Routing decision contract v1

Use the bundled pure resolver before the first business action. The injected
`ROUTING_CONFIG_BEGIN` block provides the absolute resolver and workspace config
paths. Invoke the shown Python 3.9+ program with `--config <routing.json>`, send
one JSON request on standard input, and use its JSON result. Calling this
routing-only program is part of the ownership preflight, not a business action.

Example invocation in PowerShell:

```powershell
$request | python "<injected resolver path>" --config "<injected config path>"
```

The request has exactly these fields:

```json
{
  "schema_version": 1,
  "decision_id": "stable-lowercase-id",
  "phase": "initial",
  "prior_ownership": null,
  "escalation_trigger": null,
  "matched_example_ids": [],
  "signals": {
    "one_local_scope": true,
    "bounded_known_outcome": true,
    "network_or_sync": false,
    "long_running_or_monitoring": false,
    "failure_or_recovery": false,
    "named_multistep_runbook": false,
    "substantive_research_or_investigation": false,
    "independent_review_required": false,
    "high_risk": false,
    "multiple_bounded_tasks": false,
    "tasks_independent": false,
    "dependencies_absent": false,
    "write_scopes_disjoint": false,
    "permissions_confirmed": true,
    "safety_constraints_known": true,
    "verification_plan_present": true,
    "write_scope_known": true
  }
}
```

Every signal is `true`, `false`, or `null`; `null` means unknown. Unknown direct
or mandatory-delegation signals cannot qualify for DIRECT. A named multi-step
runbook set to true or unknown therefore delegates. The example-match IDs must
come from the injected config and must be unique.

For reclassification, set `phase` to `reclassification`,
`prior_ownership` to `DIRECT`, and `escalation_trigger` to one supported trigger
documented by the routing policy. Initial decisions require both fields to be
null.

The exact result fields are `schema_version`, `decision_id`, `ownership`,
`delegate_topology`, `verification_requirement`, `configured_mode`,
`matched_rule`, `reasons`, `reclassified_from`, `escalation_trigger`, and
`constraints`. A zero exit status and parseable result are required. Invalid
config/request data exits nonzero and must stop business execution rather than
being reconstructed or guessed.
