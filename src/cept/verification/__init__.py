"""Canonical verification namespace for CEPT engineering evidence.

This package is the stable home for the engine-neutral verification surface:
the run-set verdict owner, the artifact-derived audits, the generic error-index
row machinery, and the solver-independent dynamic parity contract.

Subpackages load lazily (PEP 562). They used to be imported eagerly here, which
meant importing the one verdict owner :mod:`cept.verification.run_set` also
pulled every audit, cross-engine comparison, parity lane, and the (private)
``benchmarks`` subtree. That made the run-set verifier unstageable on its own:
the public recipe wheel needs ``run_set`` but must not ship the private
``benchmarks`` tree or the PowerFactory comparison/audit modules. Attribute
access such as ``cept.verification.audits`` still resolves and imports the
subpackage on first use, so existing callers are unchanged.

``benchmarks`` now belongs to the CEPT Advance tier and resolves by capability
name. ``comparison`` stays here because the engine-neutral error-index machinery
is core; its PFD/PDF and cross-engine lanes moved to the Advance tier.
"""

from __future__ import annotations

import importlib
from typing import Any

from cept.adapters import capability

__all__ = ["audits", "benchmarks", "comparison", "parity"]

#: Subpackages that live in core.
_CORE_SUBPACKAGES = frozenset({"audits", "comparison", "parity"})

#: Subpackages that live in the CEPT Advance tier, keyed by attribute name and
#: mapped to the capability that resolves them.
_ADVANCE_SUBPACKAGES = {"benchmarks": "verification_benchmarks"}


def __getattr__(name: str) -> Any:
    if name in _CORE_SUBPACKAGES:
        return importlib.import_module(f"{__name__}.{name}")
    advance_capability = _ADVANCE_SUBPACKAGES.get(name)
    if advance_capability is not None:
        return capability(advance_capability)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)