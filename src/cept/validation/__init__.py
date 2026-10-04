"""Validation layer — reproduce published test-feeder solutions and compare.

The package keeps validation-specific checks here while comparison machinery
is imported from its canonical :mod:`cept.verification.comparison` home.

Re-exports load lazily (PEP 562). They used to be imported eagerly here, which
meant importing a single check such as :mod:`cept.validation.sld_fidelity` also
loaded the cross-engine / PFD comparison machinery. The public recipe wheel
runs SLD-fidelity and grid-code checks but must not ship the cross-engine
comparison surface, so each name now resolves its source module on first
attribute access. ``from cept.validation import <name>`` is unchanged.

The PFD/PDF and cross-engine comparison lanes belong to the CEPT Advance tier.
Those names are listed here as **public names only** and resolved through
:func:`cept.ports.advanced.capability`; when the tier is absent the access
raises an :class:`ImportError` naming the capability rather than pretending the
name does not exist.
"""

from __future__ import annotations

import importlib
from typing import Any

from cept.adapters import AdvanceUnavailable, capability

#: Re-exported name -> the module that owns it.
_NAME_SOURCES: dict[str, str] = {
    "validate_energy": "cept.validation.harness",
    "validate_voltages": "cept.validation.harness",
    "PAPER_CROSSCHECKS": "cept.validation.papers",
    "paper_crosschecks": "cept.validation.papers",
    "IEEE13_KERSTING_MATCH": "cept.validation.references",
    "IEEE13_PUBLISHED": "cept.validation.references",
    "IEEE34_KERSTING_MATCH": "cept.validation.references",
    "IEEE34_PUBLISHED": "cept.validation.references",
    "IEEE123_ENERGY_REF": "cept.validation.references",
    "KUNDUR_SMIB_PUBLISHED": "cept.validation.references",
    "compare_emt_waveform": "cept.validation.waveform",
    "aggregate_error_index": "cept.verification.comparison.error_index",
    "align_series": "cept.verification.comparison.error_index",
    "categorical_error_item": "cept.verification.comparison.error_index",
    "scalar_error_item": "cept.verification.comparison.error_index",
    "write_error_index": "cept.verification.comparison.error_index",
}

#: Re-exported names the CEPT Advance tier owns. Core names the public symbol,
#: never a module path, so this table cannot go stale when Advance moves a file.
_ADVANCE_NAMES = frozenset(
    {
        "compare_runs",
        "compare_three_runs",
        "write_comparison",
        "write_three_way_comparison",
        "cross_engine_fault",
        "cross_engine_voltages",
        "run_cross_engine",
    }
)

__all__ = sorted(set(_NAME_SOURCES) | _ADVANCE_NAMES)


def __getattr__(name: str) -> Any:
    source = _NAME_SOURCES.get(name)
    if source is not None:
        return getattr(importlib.import_module(source), name)
    if name in _ADVANCE_NAMES:
        try:
            return capability(name)
        except AdvanceUnavailable as exc:
            raise ImportError(f"cept.validation.{name}: {exc}") from exc
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)