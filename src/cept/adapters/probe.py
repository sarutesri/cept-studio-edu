"""Engine discovery implementation behind the :class:`EngineProbe` port.

``cept.ports`` stays pure, so the concrete probe lives on the adapter side:
it answers "which engines are installed on this host" by importability /
installation scan, never by instantiating an adapter (instantiation is what
connects to the engine).
"""

from __future__ import annotations

import importlib

from typing import Any

from cept.ports.probe import EngineProbe

_ENGINE_ORDER = ("opendss", "powerfactory")


class InstalledEngineProbe:
    """Fail-safe discovery: never raises; returns None for anything unknown."""

    def available_engines(self) -> list[str]:
        return [engine for engine in _ENGINE_ORDER if self.engine_version(engine) is not None]

    def engine_version(self, engine: str) -> str | None:
        try:
            if engine == "opendss":
                import opendssdirect as dss

                return dss.Basic.Version()
            if engine == "powerfactory":
                provider = importlib.import_module("cept.adapters.powerfactory")
                versions = provider.list_installed_versions()
                return versions[0] if versions else None
        except Exception:
            return None
        return None

    def powerfactory_info(self) -> dict[str, Any] | None:
        """Installed versions + Python-API folder, or None on any failure.

        Keeps ``locate_pf_api`` / ``list_installed_versions`` behind the
        adapter boundary; consumers (research, doctor) go through the probe.
        """
        try:
            provider = importlib.import_module("cept.adapters.powerfactory")
            versions = provider.list_installed_versions()
            api = provider.locate_pf_api()
            return {
                "installed_versions": versions,
                "api_dir": str(api) if api is not None else None,
            }
        except Exception:
            return None

    def opender_info(self) -> dict[str, Any]:
        """OpenDER availability via the adapter's own probe (never raises)."""
        try:
            from cept.adapters.opender import availability

            return availability()
        except Exception as exc:
            return {"available": False, "reason": f"probe failure: {type(exc).__name__}: {exc}"}


default_probe: EngineProbe = InstalledEngineProbe()

__all__ = ["InstalledEngineProbe", "default_probe"]
