"""Synthetic CLI catalog plus real candidate profile fixtures; no model calls."""
import shutil

from evals.scripts import evalplus_isolation as isolation
from evals.scripts import evalplus_runner as runner


CATALOG = {"models": [{"slug": "gpt-test-" + name, "visibility": "list", "priority": index,
                       "supported_reasoning_levels": [{"effort": e} for e in ("low", "medium", "high", "xhigh")]}
                      for index, name in enumerate(("luna", "terra", "sol"))]}


def provision(home):
    installed = home / "plugins/cache/evalplus-candidate/codex-model-router/0.1.3"
    shutil.copytree(runner.ROOT / "plugins/codex-model-router", installed,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    profile_hash = isolation.provision_profile(home, installed, CATALOG)
    return {"homes": {"router": str(home)}, "installed_path": str(installed),
            "candidate_sha256": isolation.tree_hash(installed), "profile_sha256": profile_hash}
