"""Synthetic CLI catalog plus real candidate profile fixtures; no model calls."""
import shutil
import xml.etree.ElementTree as ET

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


def add_write_capability(body, workspace):
    """Synthetic CLI capability evidence, distinct from router hook context."""
    filesystem = ET.Element("filesystem")
    roots = ET.SubElement(filesystem, "workspace_roots")
    ET.SubElement(roots, "root").text = str(workspace.resolve())
    policy = ET.SubElement(filesystem, "permission_profile", type="managed")
    fs = ET.SubElement(policy, "file_system", type="restricted")
    entry = ET.SubElement(fs, "entry", access="read")
    ET.SubElement(entry, "special").text = ":minimal"
    entry = ET.SubElement(fs, "entry", access="write")
    ET.SubElement(entry, "path").text = str(workspace.resolve())
    body.setdefault("input", []).append({"role": "user", "content": [{"text":
        "Network access is restricted.\n" + ET.tostring(filesystem, encoding="unicode")} ]})
    body.setdefault("tools", []).append({"name": "mcp__evalplus_workspace", "tools": [
        {"name": "read_file"}, {"name": "write_file"}]})
    return body
