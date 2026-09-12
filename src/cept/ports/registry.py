"""Adapter registry port: how the domain core obtains an engine adapter."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from cept.ports.engine import EngineAdapter


@runtime_checkable
class AdapterRegistry(Protocol):
    """Resolves an engine name to a ready-to-run adapter instance."""

    def adapter_for(self, engine: str) -> EngineAdapter: ...

    def names(self) -> list[str]: ...


__all__ = ["AdapterRegistry"]
