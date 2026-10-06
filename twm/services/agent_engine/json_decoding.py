"""Decode the JSON an agent returned, tolerating syntax slips that carry no meaning.

A retry costs the traveler a full generation, so a completion that is only
*spelled* wrong is read, not rejected: a markdown fence or prose around the
object, a raw newline inside a string, a trailing comma, a comment, or Python's
``True`` / ``False`` / ``None``. Repairs happen only outside string literals,
so text such as ``"None of these"`` is never altered, and only after the
strict parse has failed.

Anything that changes meaning is deliberately not attempted: unbalanced or
truncated output, single-quoted strings, a misplaced brace. Those are the
model's to correct on the retry.
"""

import json
import re
from typing import Any

_MARKDOWN_FENCE_RE = re.compile(r"```(?:json)?\s*\n(?P<body>.*?)\n```", re.DOTALL)
_WORD_RE = re.compile(r"[A-Za-z_]+")
_PYTHON_LITERALS = {"True": "true", "False": "false", "None": "null"}
_CLOSERS = "}]"


def _strict(text: str) -> Any:
    # strict=False accepts raw control characters (a literal newline) in strings.
    return json.loads(text, strict=False)


def _string_end(text: str, start: int) -> int:
    """Index of the quote that closes the string opened at ``start``."""

    end = start + 1
    while end < len(text) and text[end] != '"':
        end += 2 if text[end] == "\\" else 1
    return end


def _after_comment(text: str, index: int) -> int | None:
    """Index just past a ``//`` or ``/* */`` comment starting at ``index``."""

    if text.startswith("//", index):
        newline = text.find("\n", index)
        return len(text) if newline == -1 else newline
    if text.startswith("/*", index):
        close = text.find("*/", index + 2)
        return len(text) if close == -1 else close + 2
    return None


def relax_json(text: str) -> str:
    """Rewrite JSON-with-slips as JSON, touching nothing inside a string."""

    out: list[str] = []
    pending_comma = False  # held back: it is dropped if a closer follows it
    index = 0
    while index < len(text):
        char = text[index]
        skipped_to = _after_comment(text, index)
        if skipped_to is not None:
            index = skipped_to
        elif char == ",":
            if pending_comma:
                out.append(",")
            pending_comma = True
            index += 1
        elif char.isspace():
            out.append(char)
            index += 1
        else:
            if pending_comma and char not in _CLOSERS:
                out.append(",")
            pending_comma = False
            if char == '"':
                end = _string_end(text, index)
                out.append(text[index : end + 1])
                index = end + 1
                continue
            word = _WORD_RE.match(text, index)
            token = word.group() if word else char
            out.append(_PYTHON_LITERALS.get(token, token))
            index += len(token)
    return "".join(out)


def _candidates(raw_output: str) -> list[str]:
    """The raw text, a fenced block found anywhere in it, then the outermost
    ``{...}`` span."""

    candidates = [raw_output]
    fence = _MARKDOWN_FENCE_RE.search(raw_output)
    if fence:
        candidates.append(fence.group("body"))
    start, end = raw_output.find("{"), raw_output.rfind("}")
    if start != -1 and end > start:
        candidates.append(raw_output[start : end + 1])
    return candidates


def decode_agent_json(raw_output: str) -> Any:
    if not isinstance(raw_output, str):
        raise json.JSONDecodeError("Unable to decode agent output", str(raw_output), 0)
    candidates = _candidates(raw_output)
    for relax in (False, True):
        for candidate in candidates:
            try:
                return _strict(relax_json(candidate) if relax else candidate)
            except (TypeError, json.JSONDecodeError):
                continue
    raise json.JSONDecodeError("Unable to decode agent output", str(raw_output), 0)
