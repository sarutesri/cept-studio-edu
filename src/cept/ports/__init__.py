"""Ports and adapters for the CEPT engine boundary (WP11 phase 1).

``cept.ports`` declares the abstractions the domain core depends on; the
concrete engine adapters live under ``cept.adapters`` and are reachable only
through :mod:`cept.adapters.registry`.  This package imports nothing outside
typing/dataclasses/pathlib/``cept.schema`` so the dependency direction stays
one-way: ports -> schema, adapters -> ports.
"""

from cept.ports.capabilities import (
    Capability,
    CapabilityResolver,
    EvidenceCapability,
    EvidenceCapabilityResolver,
    ExecutionCapability,
    ExecutionCapabilityResolver,
    ResolvedStudyCapability,
)
from cept.ports.engine import (
    EngineAdapter,
    SupportsArtifactExport,
    SupportsIdentity,
    SupportsLifecycle,
    SupportsYbusLane,
)
from cept.ports.options import RunOptions
from cept.ports.probe import EngineProbe
from cept.ports.registry import AdapterRegistry

__all__ = [
    "AdapterRegistry",
    "Capability",
    "CapabilityResolver",
    "EngineAdapter",
    "EngineProbe",
    "EvidenceCapability",
    "EvidenceCapabilityResolver",
    "ExecutionCapability",
    "ExecutionCapabilityResolver",
    "ResolvedStudyCapability",
    "RunOptions",
    "SupportsArtifactExport",
    "SupportsIdentity",
    "SupportsLifecycle",
    "SupportsYbusLane",
]
