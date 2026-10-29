"""Best-effort suppression of unchanged controller entrypoints."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def state_path(event: dict[str, Any], config_path: Path) -> Path | None:
    session = event.get("session_id")
    if not isinstance(session, str) or not 1 <= len(session) <= 512:
        return None
    key = hashlib.sha256(json.dumps([session, str(config_path)], ensure_ascii=True).encode()).hexdigest()
    try:
        return Path(tempfile.gettempdir()) / "codex-model-router-context-v1" / f"{key}.json"
    except OSError:
        return None


def already_emitted(path: Path | None, signature: str) -> bool:
    if path is None:
        return False
    try:
        if path.stat().st_size > 256:
            return False
        record = json.loads(path.read_text(encoding="utf-8"))
        return (isinstance(record, dict) and type(record.get("version")) is int and
                record == {"version": 1, "signature": signature})
    except (OSError, ValueError, UnicodeError):
        return False


def remember_emission(path: Path | None, signature: str) -> None:
    if path is None:
        return
    temporary = None
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".context-", dir=path.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"version": 1, "signature": signature}, stream, separators=(",", ":"))
        os.replace(temporary, path)
    except OSError:
        pass  # A missed cache write causes a repeat, never a lost entrypoint.
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
