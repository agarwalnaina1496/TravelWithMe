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
_NON_SECRET_TOKEN_METRICS = {
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "total_tokens",
}
_SENSITIVE_TEXT_VALUE = re.compile(
    r"(?i)([\"']?(?:authorization|password|passwd|secret|token|access[_-]?token|"
    r"refresh[_-]?token|api[_-]?token|api[_-]?key|cookie|database[_-]?url|"
    r"db[_-]?url|connection[_-]?string|webhook[_-]?url)[\"']?\s*[:=]\s*)"
    r"(Bearer\s+[^\s,;}]+|\"[^\"]*\"|'[^']*'|[^\s,;}]+)"
)


def sanitize(value: Any, max_field_size: int, key: str | None = None) -> Any:
    if (
        key is not None
        and key.lower() not in _NON_SECRET_TOKEN_METRICS
        and _SENSITIVE_KEY.search(key)
    ):
        return REDACTED
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _truncate(_redact_sensitive_text(value), max_field_size)
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


def _redact_sensitive_text(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        original_value = match.group(2)
        quote = (
            original_value[0]
            if len(original_value) >= 2
            and original_value[0] in {'"', "'"}
            and original_value[-1] == original_value[0]
            else ""
        )
        return f"{match.group(1)}{quote}{REDACTED}{quote}"

    return _SENSITIVE_TEXT_VALUE.sub(replace, value)


def _safe_string(value: Any) -> str:
    try:
        return str(value)
    except Exception:
        return f"<unprintable:{type(value).__name__}>"
