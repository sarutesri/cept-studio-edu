"""Execution-feasibility capability authority.

The machine-readable matrix owns whether CEPT may execute a
``(study_type, engine, representation_family)`` combination. Engine
qualification, research status, and evidence claim ceilings remain owned by
:mod:`cept.engine_passports`. ``resolve_study_capability`` exposes both views
without collapsing their semantics.
"""

from cept.capability.matrix import (
    CapabilityBlocked,
    MatrixCapabilityResolver,
    capability_for_case,
    cell_for_case,
    execution_capability_for_case,
    gate_case,
    load_capability_matrix,
)
from cept.capability.resolution import CapabilityConsistencyError, resolve_study_capability

__all__ = [
    "CapabilityBlocked",
    "CapabilityConsistencyError",
    "MatrixCapabilityResolver",
    "capability_for_case",
    "cell_for_case",
    "execution_capability_for_case",
    "gate_case",
    "load_capability_matrix",
    "resolve_study_capability",
]
