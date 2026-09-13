"""Lazy engine-adapter facade.

The package boundary is intentionally import-safe for CEPT Public: importing
``cept.adapters`` or ``cept.adapters.opendss`` must not import the licensed
PowerFactory adapter.  Pro callers keep the historical names through
``__getattr__``; the licensed modules are loaded only when a Pro-only symbol
is actually requested.
"""

from __future__ import annotations

import importlib
from typing import Any


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
    if name == "powerfactory_inventory_worker":
        try:
            return importlib.import_module("cept.adapters.pf.inventory")
        except ModuleNotFoundError as exc:
            raise ImportError(
                "PowerFactory inventory is a CEPT Pro capability and is not installed in this CEPT Public package."
            ) from exc
    if name == "OpenDSSAdapter":
        return importlib.import_module("cept.adapters.opendss").OpenDSSAdapter
    if name == "PowerFactoryAdapter":
        try:
            return importlib.import_module("cept.adapters.pf.phase_aware").PowerFactoryAdapter
        except ModuleNotFoundError as exc:
            raise ImportError(
                "PowerFactory is a CEPT Pro capability and is not installed in this CEPT Public package."
            ) from exc
    pro_exports = {
        "PowerFactoryNotAvailable": ("cept.adapters.pf.errors", "PowerFactoryNotAvailable"),
        "PowerFactoryRunError": ("cept.adapters.pf.errors", "PowerFactoryRunError"),
        "export_verified_native_sld": ("cept.adapters.pf.native_export", "export_verified_native_sld"),
        "build_native_pfd_plan": ("cept.adapters.pf.sld_plan", "build_native_pfd_plan"),
        "extract_load_flow": ("cept.adapters.pf.study", "extract_load_flow"),
        "run_load_flow": ("cept.adapters.pf.study", "run_load_flow"),
        "audit_active_native_diagram": ("cept.adapters.pf.sld_readback", "audit_active_native_diagram"),
        "default_probe": ("cept.adapters.probe", "default_probe"),
    }
    target = pro_exports.get(name)
    if target is not None:
        try:
            return getattr(importlib.import_module(target[0]), target[1])
        except ModuleNotFoundError as exc:
            raise ImportError(
                f"{name} is a CEPT Pro capability and is not installed in this CEPT Public package."
            ) from exc
    raise AttributeError(name)


__all__ = [
    "OpenDSSAdapter",
    "PowerFactoryAdapter",
    "PowerFactoryNotAvailable",
    "PowerFactoryRunError",
    "adapter_for",
    "powerfactory_inventory_worker",
    "audit_active_native_diagram",
    "default_probe",
    "build_native_pfd_plan",
    "extract_load_flow",
    "export_verified_native_sld",
    "run_load_flow",
]
