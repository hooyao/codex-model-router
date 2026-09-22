"""Manifest prompt-text hashing for external campaign helpers only.

Do not use this for byte-bound configs, artifacts, requests, or receipts.
The pinned runner already normalizes file newlines with Path.read_text().
"""
import hashlib


def normalized_prompt_sha256(value):
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if not isinstance(value, str):
        raise TypeError("prompt must be UTF-8 bytes or text")
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
