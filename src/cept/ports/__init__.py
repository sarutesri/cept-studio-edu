"""Ports and adapters for the CEPT engine boundary (WP11 phase 1).

``cept.adapters`` is the lazy engine-wiring facade.  The historical
``cept.adapters.registry`` module delegates to that facade.  This package
declares abstractions the domain core depends on and imports nothing outside
typing/dataclasses/pathlib/``cept.schema``, keeping the dependency direction
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
