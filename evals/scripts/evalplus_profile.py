#!/usr/bin/env python3
"""Benchmark-only hook adapter; the candidate's user policy remains unchanged.

This standalone file is copied into the isolated CODEX_HOME/benchmark-router.
It reuses the candidate contracts and schema, but never discovers workspace config.
"""

import importlib.util
import json
import re
import sys
from pathlib import Path


PROFILE_DIRECTORY = "benchmark-router"
# CLI 0.144.1 captured previews were ~10,000 bytes (2,500 approximate tokens).
# Keep both UTF-8 context and the entire JSON envelope below a conservative cap.
SAFE_BYTES = 8000
EVENTS = ("SessionStart", "UserPromptSubmit", "SubagentStart")


def strict_json(text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key: " + key)
            result[key] = value
        return result
    def invalid(value):
        raise ValueError("non-finite JSON value: " + value)
    return json.loads(text, object_pairs_hook=unique, parse_constant=invalid)


def candidate_module(candidate):
    directory = candidate / "hooks"
    # The hash-verified candidate is the only non-standard-library dependency.
    sys.path.insert(0, str(directory))
    try:
        spec = importlib.util.spec_from_file_location("benchmark_candidate_hook", directory / "router_hook.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(directory))


def resolve_routes(catalog):
    """Resolve visible CLI model slugs/efforts deterministically; no invented IDs."""
    routes = {}
    for model_class, effort in (("Luna", "low"), ("Terra", "medium"), ("Sol", "high"), ("Astra", "xhigh")):
        matches = [m for m in catalog["models"] if m.get("visibility") == "list"
                   and isinstance(m.get("slug"), str) and m["slug"].endswith("-" + model_class.lower())
                   and effort in [r.get("effort") for r in m.get("supported_reasoning_levels", [])]]
        if matches:
            model = min(matches, key=lambda m: (m.get("priority", 10000), m["slug"]))["slug"]
            if not re.fullmatch(r"[a-z0-9.-]{1,48}", model):
                raise ValueError("unsupported model identifier")
            routes[model_class] = {"model": model, "effort": effort}
    if not all(name in routes for name in ("Luna", "Terra", "Sol")):
        raise ValueError("CLI catalog lacks the benchmark's simple/everyday/complex routes")
    return routes


def minimal_routing(routes):
    routes = {name: routes[name] for name in ("Luna", "Terra", "Sol", "Astra") if name in routes}
    signals = {"Luna": "clear short Python function", "Terra": "several edge cases",
               "Sol": "ambiguous algorithm or failed verification", "Astra": "high-risk review"}
    mapping = "; ".join(name + "=" + route["model"] + "/" + route["effort"] for name, route in routes.items())
    return {
        "schema_version": 1,
        "selection_principle": "Use the least expensive reliable route; escalate on ambiguity, risk or failed verification.",
        "runtime_resolution": "Resolved CLI catalog pairs: " + mapping + ". Check the exposed spawn schema; never invent a model/effort. Keep the parent model unchanged.",
        "effort_guidance": {"low": "Clear bounded work.", "medium": "Edge cases and planning.",
                            "high": "Complex reasoning or verification.", "xhigh": "High-risk review if available."},
        "official_sources": ["https://developers.openai.com/codex/models"],
        "examples": [{"id": name.lower(), "task_signals": [signals[name]], "preferred_model_class": name,
                      "reasoning_effort": route["effort"], "rationale": route["model"]}
                     for name, route in routes.items()],
    }


def load_profile(directory):
    metadata = strict_json((directory / "profile.json").read_text(encoding="utf-8"))
    relative = Path(metadata["candidate_relative"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("candidate must remain in the isolated home")
    candidate = (directory.parent / relative).resolve()
    candidate.relative_to(directory.parent.resolve())
    module = candidate_module(candidate)
    config = strict_json((directory / "routing.json").read_text(encoding="utf-8"))
    module.routing_context_block(directory / "routing.json", config)
    # Use the normal plugin validator, not a relaxed benchmark schema.
    from routing_config import validate_config
    validate_config(config)
    expected = minimal_routing(metadata["resolved_routes"])
    if config != expected:
        raise ValueError("routing config differs from resolved benchmark routes")
    return metadata, module, config


def build_output(directory, event):
    event_name = event.get("hook_event_name")
    if event_name not in EVENTS:
        raise ValueError("unsupported benchmark hook event")
    metadata, module, config = load_profile(directory)
    model = event.get("model", metadata["controller_model"])
    if not isinstance(model, str) or not re.fullmatch(r"[a-zA-Z0-9.-]{1,64}", model):
        raise ValueError("invalid event model")
    # Stable short label avoids unbounded workspace paths and contains JSON only
    # between delimiters, so consumers can parse the whole block exactly.
    routing = "ROUTING_CONFIG_BEGIN\n" + json.dumps(config, sort_keys=True, separators=(",", ":")) + "\nROUTING_CONFIG_END"
    context = module.additional_context(event_name, dict(event, model=model), routing)
    output = {"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": context}}
    if len(context.encode("utf-8")) > SAFE_BYTES or len(json.dumps(output).encode("utf-8")) > SAFE_BYTES:
        raise ValueError("benchmark hook exceeds the safe byte ceiling")
    return output


def parse_context(text, directory, event_name):
    """Accept exactly the complete candidate contract plus one valid JSON block."""
    if len(text.encode("utf-8")) > SAFE_BYTES:
        raise ValueError("delivered context exceeds the safe byte ceiling")
    if text.count("ROUTING_CONFIG_BEGIN\n") != 1 or text.count("\nROUTING_CONFIG_END") != 1:
        raise ValueError("routing delimiters missing or duplicated")
    prefix, rest = text.split("ROUTING_CONFIG_BEGIN\n")
    payload, suffix = rest.split("\nROUTING_CONFIG_END")
    if suffix:
        raise ValueError("unexpected suffix or spilled hook output")
    metadata, module, expected_config = load_profile(directory)
    parsed = strict_json(payload)
    if parsed != expected_config:
        raise ValueError("delivered routing JSON does not match the profile")
    expected = build_output(directory, {"hook_event_name": event_name, "model": metadata["controller_model"]})
    if text != expected["hookSpecificOutput"]["additionalContext"]:
        raise ValueError("candidate contract was changed or truncated")
    return {"event": event_name, "context_bytes": len(text.encode("utf-8")),
            "routing_bytes": len(payload.encode("utf-8")), "exact_contract": True, "routing_json_valid": True}


def main():
    try:
        event = strict_json(sys.stdin.read())
        if not isinstance(event, dict):
            raise ValueError("event must be an object")
        # A diagnostic can ask the CLI's SessionStart injector to carry the
        # exact SubagentStart result. This does not spawn or invoke any worker.
        simulate = sys.argv[1:] == ["--simulate-subagent-start"]
        if sys.argv[1:] and not simulate:
            raise ValueError("unknown benchmark hook argument")
        actual_event = event.get("hook_event_name")
        if simulate:
            if actual_event != "SessionStart":
                raise ValueError("worker simulation requires SessionStart transport")
            event["hook_event_name"] = "SubagentStart"
        output = build_output(Path(__file__).resolve().parent, event)
        if simulate:
            output["hookSpecificOutput"]["hookEventName"] = actual_event
        print(json.dumps(output, separators=(",", ":")))
        return 0
    except (ValueError, KeyError, OSError) as error:
        print("benchmark hook refused: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
