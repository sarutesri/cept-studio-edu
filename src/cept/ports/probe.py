"""Engine discovery behind a port.

Phase 1 declares the port; phase 2 (d) moves the real discovery
(``cept system doctor``) behind an implementation of it.  ``cept.ports`` stays
pure — it may not import anything outside typing/dataclasses/pathlib/
``cept.schema`` — so the implementation will live in ``cept.adapters``.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EngineProbe(Protocol):
    """Discovers which engines are installed and licensed on this host.

    ``powerfactory_info`` and ``opender_info`` exist so that
    ``research/provenance.py``, ``research/diagnostics.py`` and
    ``cept system doctor`` never import an adapter module directly: the concrete
    discovery lives on the adapter side behind this port.
    """

    def available_engines(self) -> list[str]: ...

    def engine_version(self, engine: str) -> str | None: ...

    def powerfactory_info(self) -> dict[str, Any] | None:
        """Installed versions plus the Python-API folder, or None on failure."""

    def opender_info(self) -> dict[str, Any]:
        """OpenDER availability probe (``available``, optional ``version``/``module``)."""


__all__ = ["EngineProbe"]
