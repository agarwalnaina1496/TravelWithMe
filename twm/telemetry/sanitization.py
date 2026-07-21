"""Redaction and bounded-value normalization applied before telemetry sinks."""

from collections.abc import Mapping, Sequence
from datetime import date, datetime
import json
import re
from typing import Any


REDACTED = "[REDACTED]"
TRUNCATED_SUFFIX = "...[TRUNCATED]"
_SENSITIVE_KEY = re.compile(
    r"(?:authorization|password|passwd|secret|token|api[_-]?key|cookie|"
    r"database[_-]?url|db[_-]?url|connection[_-]?string|webhook[_-]?url)",
    re.IGNORECASE,
)


def sanitize(value: Any, max_field_size: int, key: str | None = None) -> Any:
    if key is not None and _SENSITIVE_KEY.search(key):
        return REDACTED
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _truncate(value, max_field_size)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return _truncate(value.decode("utf-8", errors="replace"), max_field_size)
    if isinstance(value, Mapping):
        return {
            _safe_string(item_key): sanitize(
                item_value, max_field_size, key=_safe_string(item_key)
            )
            for item_key, item_value in value.items()
        }
    if isinstance(value, Sequence):
        return [sanitize(item, max_field_size) for item in value]
    return _truncate(_safe_string(value), max_field_size)


def payload_metadata(payload: Any) -> dict[str, Any]:
    metadata: dict[str, Any] = {"type": type(payload).__name__}
    try:
        metadata["size_bytes"] = len(
            json.dumps(payload, default=_safe_string, ensure_ascii=False).encode("utf-8")
        )
    except Exception:
        metadata["size_bytes"] = None
    return metadata


def _truncate(value: str, max_field_size: int) -> str:
    if len(value) <= max_field_size:
        return value
    if max_field_size <= len(TRUNCATED_SUFFIX):
        return TRUNCATED_SUFFIX[:max_field_size]
    keep = max(0, max_field_size - len(TRUNCATED_SUFFIX))
    return f"{value[:keep]}{TRUNCATED_SUFFIX}"


def _safe_string(value: Any) -> str:
    try:
        return str(value)
    except Exception:
        return f"<unprintable:{type(value).__name__}>"
