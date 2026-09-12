"""Lazy fidelity package facade.

The per-unit intake gate is part of the neutral Case boundary.  The broader
source-to-Case receipt remains available through lazy compatibility exports so
the public package does not import research comparison code at import time.
"""

from __future__ import annotations

import importlib
from typing import Any

_FIDELITY_NAMES = {
    "RECEIPT_SCHEMA",
    "build_receipt",
    "check",
    "enforce_case_fidelity",
    "receipt_blockers",
    "resolve_source_inventory",
    "write_receipt",
}
_PERUNIT_NAMES = {"audit_perunit"}


def __getattr__(name: str) -> Any:
    if name in _FIDELITY_NAMES:
        return getattr(importlib.import_module("cept.fidelity.fidelity"), name)
    if name in _PERUNIT_NAMES:
        return getattr(importlib.import_module("cept.fidelity.perunit"), name)
    raise AttributeError(name)


__all__ = sorted(_FIDELITY_NAMES | _PERUNIT_NAMES)
