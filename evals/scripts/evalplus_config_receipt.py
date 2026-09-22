"""Exact invocation config fingerprints and one path-scoped CLI trust append.

Never discard project tables or rewrite a config. CLI 0.144.1 preserves the
original text (normalizing CRLF to LF) and appends one trusted project table.
Receipts contain hashes and paths only, not provider/authentication values.
"""
import json
import os

try:
    from . import evalplus_runner as runner, evalplus_workspace as editor
except ImportError:
    import evalplus_runner as runner
    import evalplus_workspace as editor


def scope(home, workspace):
    editor.WorkspaceEditor(home)
    editor.WorkspaceEditor(workspace)
    return {"home": str(home.resolve()), "workspace": str(workspace.resolve()),
            "project_key": os.path.normcase(str(workspace.resolve()))}


def config_bytes(home):
    path = home / "config.toml"
    editor.WorkspaceEditor._plain(path)
    return path.read_bytes()


def fingerprint(home, workspace):
    value = config_bytes(home)
    normalized = value.replace(b"\r\n", b"\n")
    return dict(scope(home, workspace), schema_version=1, config_sha256=runner.sha256_bytes(value),
                config_bytes=len(value), lf_sha256=runner.sha256_bytes(normalized), lf_bytes=len(normalized))


def trust_suffixes(workspace):
    key = os.path.normcase(str(workspace.resolve()))
    # Both are TOML spellings of this exact key, not a path/glob match. Literal
    # quoting is what CLI 0.144.1 writes for ordinary Windows task roots.
    quoted = [json.dumps(key, ensure_ascii=False)]
    if "'" not in key and not any(ord(c) < 32 for c in key):
        quoted.append("'" + key + "'")
    return [("\n[projects." + q + ']\ntrust_level = "trusted"\n').encode() for q in quoted]


def validate(before, home, workspace, expected_sha256=None):
    expected_keys = {"schema_version", "home", "workspace", "project_key", "config_sha256",
                     "config_bytes", "lf_sha256", "lf_bytes"}
    if (set(before) != expected_keys or before["schema_version"] != 1
            or any(before[k] != v for k, v in scope(home, workspace).items())
            or (expected_sha256 is not None and before["config_sha256"] != expected_sha256)):
        raise runner.HarnessError("invocation config baseline or workspace binding changed")
    current = config_bytes(home)
    digest = runner.sha256_bytes(current)
    change = "unchanged"
    if digest != before["config_sha256"]:
        # Only the known line-ending conversion and an exact single-table
        # append are permitted. All pre-existing bytes remain hash-bound.
        length = before["lf_bytes"]
        if (type(length) is not int or length < 0
                or runner.sha256_bytes(current[:length]) != before["lf_sha256"]
                or current[length:] not in trust_suffixes(workspace)):
            raise runner.HarnessError("config changed beyond the exact task-scoped project trust append")
        change = "task-project-trust-added"
    return {"passed": True, "mutation": change, "config_sha256": digest,
            "baseline_sha256": before["config_sha256"], **scope(home, workspace)}


def begin(evidence, name, home, workspace):
    path = evidence / (name + ".config-before.json")
    if path.exists() or (evidence / (name + ".config-after.json")).exists():
        raise runner.HarnessError("refusing to overwrite invocation config evidence")
    runner.write_json(path, fingerprint(home, workspace))


def finish(evidence, name, home, workspace):
    before = evidence / (name + ".config-before.json")
    try:
        result = validate(runner.read_json(before), home, workspace)
    except (runner.HarnessError, OSError, ValueError) as error:
        result = {"passed": False, "error": str(error)}
    result["before_receipt_sha256"] = runner.sha256_file(before)
    runner.write_json(evidence / (name + ".config-after.json"), result)
    return result


def require(evidence, name, home, workspace, expected_sha256=None):
    before = evidence / (name + ".config-before.json")
    saved = runner.read_json(evidence / (name + ".config-after.json"))
    actual = validate(runner.read_json(before), home, workspace, expected_sha256)
    actual["before_receipt_sha256"] = runner.sha256_file(before)
    if saved != actual:
        raise runner.HarnessError("invocation config receipt changed or did not pass")
    return actual
