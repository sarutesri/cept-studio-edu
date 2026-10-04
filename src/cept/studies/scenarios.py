"""Scenario override helpers for reproducible batch studies."""

from __future__ import annotations

import re
from typing import Any

from cept.schema.case import Case, Scenario


def _tokens(path: str) -> list[str | int]:
    out: list[str | int] = []
    for part in path.split("."):
        match = re.fullmatch(r"([A-Za-z0-9_-]+)(?:\[(\d+)\])?", part)
        if not match:
            raise ValueError(f"Unsupported scenario override path '{path}'")
        out.append(match.group(1))
        if match.group(2) is not None:
            out.append(int(match.group(2)))
    return out


def apply_scenario(case: Case, scenario: Scenario) -> Case:
    payload: dict[str, Any] = case.model_dump(mode="json")
    for path, value in scenario.overrides.items():
        tokens = _tokens(path)
        cursor: Any = payload
        for index, token in enumerate(tokens[:-1]):
            if isinstance(token, str) and isinstance(cursor, list):
                match = next(
                    (
                        item
                        for item in cursor
                        if isinstance(item, dict) and item.get("id", item.get("name")) == token
                    ),
                    None,
                )
                if match is None:
                    raise ValueError(f"Scenario path '{path}' could not find '{token}'")
                cursor = match
            else:
                cursor = cursor[token]
        last = tokens[-1]
        if isinstance(last, str) and isinstance(cursor, list):
            match = next(
                (
                    item
                    for item in cursor
                    if isinstance(item, dict) and item.get("id", item.get("name")) == last
                ),
                None,
            )
            if match is None:
                raise ValueError(f"Scenario path '{path}' could not find '{last}'")
            raise ValueError(f"Scenario path '{path}' ends at an element, not a field")
        cursor[last] = value
    return Case.model_validate(payload)


__all__ = ["apply_scenario"]
