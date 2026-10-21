"""Prepare or run one bounded native canary retry using the corrected bindings.

The default check makes no model call. ``--run`` invokes the historical two-turn
canary in a new evidence directory; its original failed receipt stays intact.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from evals.long_horizon_v1.common import HERE, file_sha, manifest, write_json_new
from evals.long_horizon_v1.runtime_binding import (CONFIG_RELATIVE, fixture_config,
    verify_arm_config, verify_cli)

RETRY_ROOT = HERE / "_scratch" / "native-episodes" / "canary-retry-1"
HISTORICAL_PROBE = HERE / "_scratch" / "native-episodes" / "probe.py"
HISTORICAL_PROBE_SHA256 = "4eabc34f2ef787b5c9c502a0f46dd27105c04fc7c37affa6b331dd87eb4cfc96"


def check() -> dict:
    spec = manifest()
    descriptor = fixture_config(spec)
    binary = verify_cli(Path(spec["runtime"]["cli"]), spec)
    RETRY_ROOT.mkdir(parents=True, exist_ok=True)
    target = RETRY_ROOT / CONFIG_RELATIVE
    if target.exists():
        if file_sha(target) != descriptor["raw_sha256"]:
            raise ValueError("retry routing config differs from versioned fixture")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(HERE / descriptor["path"], target)
    routing = verify_arm_config(RETRY_ROOT, spec)
    if not HISTORICAL_PROBE.is_file() or file_sha(HISTORICAL_PROBE) != HISTORICAL_PROBE_SHA256:
        raise ValueError("historical canary probe missing or changed")
    if (RETRY_ROOT / "receipt-corrected.json").exists():
        raise ValueError("canary retry already attempted; no automatic repeat")
    result = {"schema_version": 1, "status": "ready", "model_turns": 0,
              "retry_directory": str(RETRY_ROOT.resolve()), "cli": binary,
              "routing_config": routing,
              "historical_probe_sha256": HISTORICAL_PROBE_SHA256,
              "live_enabled": spec["live_enabled"]}
    return result


def run() -> None:
    ready = check()
    module_spec = importlib.util.spec_from_file_location("historical_native_canary", HISTORICAL_PROBE)
    if module_spec is None or module_spec.loader is None:
        raise ValueError("historical canary probe cannot be loaded")
    probe = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(probe)
    probe.HERE = RETRY_ROOT
    probe.CLI = Path(ready["cli"]["cli"])
    probe.ROUTING = RETRY_ROOT / CONFIG_RELATIVE
    probe.EXPECTED_CLI = ready["cli"]["cli_sha256"]
    probe.main()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", help="spends on the bounded native canary")
    args = parser.parse_args()
    if args.run:
        run()
    else:
        result = check()
        output = RETRY_ROOT / "preflight.json"
        if output.exists():
            if json.loads(output.read_text(encoding="utf-8")) != result:
                raise ValueError("previous retry preflight differs")
        else:
            write_json_new(output, result)
        print(json.dumps({"status": result["status"], "model_turns": 0,
                          "routing_config_canonical_sha256": result["routing_config"]["canonical_sha256"],
                          "preflight": str(output)}))
