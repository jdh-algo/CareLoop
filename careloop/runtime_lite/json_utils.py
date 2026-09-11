from __future__ import annotations

"""Lenient JSON helpers for LLM-native runtime prompts.

runtime_lite asks LLMs for compact JSON only at boundaries where code needs to
route a decision.  These helpers tolerate prose around the object so prompts can
stay natural and debuggable.
"""

import json
from typing import Any


def extract_json_object(text: str) -> dict[str, Any]:
    """Return the first JSON object in *text* or an empty dict.

    The function deliberately does not enforce a schema.  It only extracts the
    nearest parseable object so callers can build a safe fallback.
    """

    if not text:
        return {}
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            value = json.loads(stripped)
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            pass
    start = stripped.find("{")
    while start >= 0:
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(stripped)):
            char = stripped[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidate = stripped[start : index + 1]
                    try:
                        value = json.loads(candidate)
                        return value if isinstance(value, dict) else {}
                    except json.JSONDecodeError:
                        break
        start = stripped.find("{", start + 1)
    return {}


def extract_json_array(text: str) -> list[Any]:
    """Return the first JSON array in *text* or an empty list."""

    if not text:
        return []
    stripped = text.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        try:
            value = json.loads(stripped)
            return value if isinstance(value, list) else []
        except json.JSONDecodeError:
            pass
    start = stripped.find("[")
    while start >= 0:
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(stripped)):
            char = stripped[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if depth == 0:
                    candidate = stripped[start : index + 1]
                    try:
                        value = json.loads(candidate)
                        return value if isinstance(value, list) else []
                    except json.JSONDecodeError:
                        break
        start = stripped.find("[", start + 1)
    return []
