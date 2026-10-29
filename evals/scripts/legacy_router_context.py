"""Frozen v1 hook text for validating retained evaluation evidence only.

This module is never imported by the plugin or injected by current hooks.
"""

import hashlib
import json
from pathlib import Path
from typing import Any
from routing_config import serialized_config, effective_execution_policy, plugin_root

CONTROLLER_CONTRACT = """CONTROLLER ROLE ONLY: SessionStart/UserPromptSubmit policy for the primary agent.
This contract does not apply to dispatched workers. Decide execution ownership before the first business action: construct the complete decision-request-v1 JSON described in the injected routing block, invoke its absolute resolver path with the injected workspace config, and record the validated decision-contract-v1 result, including ownership, delegate_topology, verification_requirement, matched_rule, reasons, constraints, and any reclassification trigger. Resolver invocation is a routing-only preflight action. Also emit one concise route line:
ROUTE: DIRECT — <rule/reason>
ROUTE: DELEGATE — <topology/rule/model/effort/reason>
Skills define HOW work is performed, not WHO performs it. Interpret the whole request as structured signals; keyword matching alone is insufficient.

DIRECT is a bounded fast path only when every fact is explicitly known: one local scope, one bounded known outcome, no network/sync, monitoring, recovery, substantive research, independent review, or high risk; permissions and safety constraints are known; and write scope and a self-check plan are declared. Unknown signals never qualify. A config `direct` value is eligibility only. A config `delegate` value mandates delegation. Hard delegate or unknown signals win before config precedence. One matching example overrides the default, no match uses the default, and disagreeing matches resolve to DELEGATE. DIRECT owns its implementation and required self-check; it never bypasses scope, permission, safety, write-ownership, or verification duties.

For a whole-request DELEGATE route, build a stage DAG before business execution. Record each stage's stable ID, controller or worker owner, dependencies, exclusive write scope, context budget, acceptance criteria, and self-check. A lightweight controller may own a genuinely easy, low-context stage only after a separate decision-request-v1 for that stage independently resolves DIRECT. Keep inherited permissions, risks, and the whole-request independent-review requirement. Reclassify any stage whose bounds expand. Use a cheap worker when an easy stage's tool output would burden controller context. Reserve Astra/xhigh for a demanding hard kernel when justified, with max only in exceptional cases; do not delegate the entire mixed request to Astra merely because one stage is hard. Never duplicate worker-owned analysis, edits, or tests in the controller.

If a DIRECT task exceeds approved bounds, stop before the next business action and record a reclassification from DIRECT to DELEGATE with one explicit trigger, such as scope-expanded, validation-failed, dependency-discovered, write-scope-overlap-discovered, permission-changed, or safety-constraint-changed. Reclassification is part of the decision record, not an unrecorded fallback.

For DELEGATE, choose PARALLEL only when there are multiple bounded tasks and independence, absence of dependencies, and disjoint write scopes are all explicitly true. Any dependency, overlapping/shared write scope, false value, or unknown value requires ISOLATED_SERIAL. This is logical context isolation: send a minimal bounded packet and receive a compact result receipt with artifact references. Do not claim existing parent history was erased or that a worker has a technical sandbox unless the runtime actually provides and verifies it.

Apply configured depth, concurrency, and retry values as ceilings; lower them to actual limits exposed by the runtime and never invent worker slots or APIs. Declare exclusive write ownership and serialize overlap. Preserve user intent, permissions, safety constraints, verification obligations, and workspace boundaries in both ownership paths. Independent-review or high-risk signals require an independent reviewer; otherwise every path still requires a self-check. Router self-improvement, routing-policy review, evaluation design, and benchmark selection carry that review signal.

Only after DELEGATE, inspect the exposed native spawn/wait tools and runtime model/effort catalog. Do not invent capabilities. If required worker capability is absent, report the observed blocker. Use only actual runtime isolation features. Route delegated work to the lowest capable available model/effort; do not change the controller model automatically. Before each spawn, validate a dispatch-contract-v1 record with the bundled hooks/dispatch_contract.py using hash-bound spawn-schema and model-catalog evidence, or an explicitly captured inheritance contract. Omitted selectors, later child runtime metadata, and placeholder values do not establish a route; missing evidence is a capability blocker.

Give each worker a bounded packet with task identity, dependencies, scope and write ownership, permissions/safety constraints, acceptance and verification criteria, topology position, limits, actual model/effort, and return contract. Use deterministic purpose-model-effort naming and the documented native transport adaptation. Workers return compact evidence receipts; retain successful receipts across bounded failures. The controller remains accountable for dependency order, conflict resolution, verification status, and synthesis; it self-checks controller-owned stages and assigns substantive worker-stage validation to workers. Integration and verification must respect the resolved topology and constraints.

The bundled initialize-router Skill is a narrow plugin-administration exception. This is instruction-layer policy: context injection cannot erase history, intercept tool calls, create a sandbox, or act as an OS/tool permission barrier."""


def routing_context_block(path: Path, config: dict[str, Any]) -> str:
    serialized = serialized_config(config, str(path))
    config_digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    resolver = plugin_root() / "hooks" / "execution_decision.py"
    effective_policy = json.dumps(
        effective_execution_policy(config), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return (
        "ROUTING_CONFIG_BEGIN\n"
        f"Workspace routing config: {path}\n"
        f"Validated config SHA256 (canonical JSON): {config_digest}\n"
        "Read this config for routing examples and model guidance before selecting matched_example_ids. "
        "The resolver reads it too.\n"
        "Routing decision contract version: 1. Ownership is resolved before delegate topology, verification, "
        "and model selection. Unknown decision signals cannot qualify for DIRECT.\n"
        f"Decision resolver program: {resolver}\n"
        "Invocation: use Python 3.9+ to run the resolver with --config set to the workspace config path above; "
        "send one decision-request-v1 JSON on stdin and use its validated JSON result.\n"
        "Request fields: schema_version=1, decision_id, phase, prior_ownership, escalation_trigger, "
        "matched_example_ids, and signals. Signals: one_local_scope, bounded_known_outcome, "
        "network_or_sync, long_running_or_monitoring, failure_or_recovery, named_multistep_runbook, "
        "substantive_research_or_investigation, independent_review_required, high_risk, "
        "multiple_bounded_tasks, tasks_independent, dependencies_absent, write_scopes_disjoint, "
        "permissions_confirmed, safety_constraints_known, verification_plan_present, write_scope_known. "
        "Every signal is true, false, or null; null is unknown.\n"
        f"EFFECTIVE_EXECUTION_POLICY:{effective_policy}\n"
        "ROUTING_CONFIG_END"
    )


# Shipped v2 entrypoint identity for historical replay, never runtime admission.
ENTRYPOINT_V2 = 'CONTROLLER: Before business work on each request, use the router skill below to resolve ownership. Load its instructions and config when absent, changed, or after compaction; reuse current context. This is instruction-layer policy, not a permission barrier.'
MANIFEST_V2 = {'name': 'codex-model-router', 'version': '1.3.1', 'description': 'Choose direct execution or route bounded Codex worker tasks by complexity and risk.', 'author': {'name': 'Codex Model Router contributors'}, 'skills': './skills/', 'interface': {'displayName': 'Codex Model Router', 'shortDescription': 'Choose direct work or the right worker model.', 'longDescription': 'Injects execution-ownership, model-routing, and worker-contract context so a primary Codex agent can execute simple local work directly and delegate nontrivial work appropriately.', 'developerName': 'Codex Model Router contributors', 'category': 'Productivity', 'capabilities': ['Read'], 'defaultPrompt': ['Choose direct or delegated execution before acting, then route delegated tasks to suitable workers.']}}
SKILL_HASHES_V2 = {'063e150b4bb33affb63f4aa55985fc292704a48eb4f3855ce9665005485482bb', '2a84b49b51174a6409ee5f025aac22089f3ea0f8e05200ce261655f1cb70aa16'}
