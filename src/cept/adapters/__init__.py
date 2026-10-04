"""Lazy engine-adapter facade.

The package boundary is intentionally import-safe for CEPT Public: importing
``cept.adapters`` or ``cept.adapters.opendss`` must not import the licensed
PowerFactory adapter, which now lives in the separate ``cept_advanced``
distribution.

Two facts are load-bearing here:

* ``default_probe`` is a **core** capability (``cept.adapters.probe``), so it is
  a plain import. It used to sit in the lazy Pro table, which made four core
  modules reach an engine-discovery helper through a licensed-capability path.
* The licensed symbols are listed here by **name only**. This module contains
  no module path into ``cept_advanced``; ``cept.ports.advanced.capability``
  resolves the name against the Advance package's own declaration and fails
  closed when the tier is absent. A renamed Advance module can therefore no
  longer leave a stale string here that reports a working capability as missing.
"""

from __future__ import annotations

import importlib
from typing import Any

from cept.adapters.advanced import (
    AdvanceUnavailable,
    available as advance_available,
    capability,
    declared_names as advance_declared_names,
    module_name,
)
from cept.adapters.probe import default_probe

#: Names this facade forwards to the CEPT Advance tier. Names, not module paths.
ADVANCE_CAPABILITIES = frozenset(
    {
        "PowerFactoryAdapter",
        "PowerFactoryNotAvailable",
        "PowerFactoryRunError",
        "audit_active_native_diagram",
        "build_native_pfd_plan",
        "export_verified_native_sld",
        "extract_load_flow",
        "powerfactory_inventory_worker",
        "run_load_flow",
    }
)


def adapter_for(engine: str) -> Any:
    """Create the requested adapter without importing unrelated engines."""

    if engine == "opendss":
        return __getattr__("OpenDSSAdapter")()
    if engine == "powerfactory":
        return __getattr__("PowerFactoryAdapter")()
    raise NotImplementedError(f"Unknown engine '{engine}'.")


def adapter_names() -> list[str]:
    """Return registered engine names without connecting to either engine."""

    return ["opendss", "powerfactory"]


def __getattr__(name: str) -> Any:
    if name == "OpenDSSAdapter":
        return importlib.import_module("cept.adapters.opendss").OpenDSSAdapter
    if name in ADVANCE_CAPABILITIES:
        return capability(name)
    raise AttributeError(name)


__all__ = [
    "AdvanceUnavailable",
    "OpenDSSAdapter",
    "PowerFactoryAdapter",
    "PowerFactoryNotAvailable",
    "PowerFactoryRunError",
    "adapter_for",
    "advance_available",
    "advance_declared_names",
    "capability",
    "module_name",
    "powerfactory_inventory_worker",
    "audit_active_native_diagram",
    "default_probe",
    "build_native_pfd_plan",
    "extract_load_flow",
    "export_verified_native_sld",
    "run_load_flow",
]