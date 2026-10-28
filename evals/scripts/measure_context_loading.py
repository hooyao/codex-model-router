"""Measure emitted hook bytes against a pinned Git baseline; no model calls."""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
PLUGIN = REPO / "plugins" / "codex-model-router"


def measure(baseline: str) -> dict:
    revision = subprocess.run(["git", "rev-parse", baseline], cwd=REPO, check=True,
                              capture_output=True, text=True).stdout.strip()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        before, after = root / "before", root / "after_"
        for relative in ("hooks/router_hook.py", "hooks/routing_config.py", "defaults/default-routing.json"):
            target = before / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            source = subprocess.run(["git", "show", f"{revision}:plugins/codex-model-router/{relative}"],
                                    cwd=REPO, check=True, capture_output=True).stdout
            target.write_bytes(source)
        shutil.copytree(PLUGIN, after, ignore=shutil.ignore_patterns("__pycache__"))
        workspace = root / "workspace"
        workspace.mkdir()
        events = ["SessionStart", "UserPromptSubmit", "UserPromptSubmit", "UserPromptSubmit", "SubagentStart"]
        result = {"baseline": revision, "unit": "UTF-8 bytes, not tokens or billed usage",
                  "events": events, "measurements": {}}
        for label, plugin in (("before", before), ("after", after)):
            rows = []
            for event in events:
                payload = {"hook_event_name": event, "cwd": str(workspace),
                           "session_id": "context-measurement", "source": "startup", "model": "gpt-6.1-terra"}
                code = (f"import tempfile,runpy; tempfile.tempdir={str(root)!r}; "
                        f"runpy.run_path({str(plugin / 'hooks/router_hook.py')!r},run_name='__main__')")
                completed = subprocess.run([sys.executable, "-c", code], cwd=workspace,
                                           input=json.dumps(payload), text=True, capture_output=True,
                                           check=True, timeout=10)
                output = json.loads(completed.stdout)
                context = output.get("hookSpecificOutput", {}).get("additionalContext", "")
                rows.append({"event": event, "context_bytes": len(context.encode("utf-8")),
                             "output_bytes": len(completed.stdout.encode("utf-8"))})
            result["measurements"][label] = rows
        result["instruction_bytes"] = {
            "before_skill": len(subprocess.run(
                ["git", "show", f"{revision}:plugins/codex-model-router/skills/model-router/SKILL.md"],
                cwd=REPO, capture_output=True, check=True).stdout),
            "after_skill": len((PLUGIN / "skills/model-router/SKILL.md").read_bytes()),
            "before_policy": len(subprocess.run(
                ["git", "show", f"{revision}:plugins/codex-model-router/skills/model-router/references/routing-policy.md"],
                cwd=REPO, capture_output=True, check=True).stdout),
            "after_policy": len((PLUGIN / "skills/model-router/references/routing-policy.md").read_bytes()),
        }
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    result = measure(arguments.baseline)
    text = json.dumps(result, indent=2) + "\n"
    if arguments.output:
        with arguments.output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    print(text, end="")
