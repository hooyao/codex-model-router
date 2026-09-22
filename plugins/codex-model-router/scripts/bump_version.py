#!/usr/bin/env python3
"""Safely apply a stable SemVer patch, minor, or major release bump."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from validate_plugin import parse_semver


def bumped_version(current: str, release_type: str) -> str:
    """Return the next stable SemVer release for an explicit release type."""
    parsed = parse_semver(current)
    if parsed is None:
        raise ValueError(f"manifest version is not valid SemVer: {current!r}")
    (major, minor, patch), _prerelease = parsed
    if release_type == "major":
        return f"{major + 1}.0.0"
    if release_type == "minor":
        return f"{major}.{minor + 1}.0"
    if release_type == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"unsupported release type: {release_type}")


def bump(plugin_root: Path, release_type: str) -> tuple[str, str]:
    manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"plugin manifest is missing: {manifest_path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"plugin manifest is malformed JSON: {error}") from error
    if not isinstance(manifest, dict) or manifest.get("name") != "codex-model-router":
        raise ValueError("plugin manifest must identify codex-model-router")
    current = manifest.get("version")
    if not isinstance(current, str):
        raise ValueError("plugin manifest version must be a string")
    next_version = bumped_version(current, release_type)
    manifest["version"] = next_version
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return current, next_version


def main() -> int:
    parser = argparse.ArgumentParser(description="Bump the Codex Model Router stable SemVer release version.")
    parser.add_argument("release_type", choices=("patch", "minor", "major"))
    parser.add_argument("--plugin-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        previous, current = bump(args.plugin_root.resolve(), args.release_type)
    except ValueError as error:
        print(f"Version bump failed: {error}", file=sys.stderr)
        return 1
    print(f"Bumped plugin version: {previous} -> {current}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
