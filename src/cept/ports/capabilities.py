"""Capability contracts at the engine-neutral boundary.

CEPT has two intentionally separate capability authorities:

* :class:`ExecutionCapability` describes whether a study can execute for a
  study/engine/representation combination and what the execution gate requires.
* :class:`EvidenceCapability` describes the installed engine's qualification,
  runtime metadata, research status, and claim ceiling.

Concrete resolvers live outside ``cept.ports``.  ``Capability`` and
``CapabilityResolver`` remain as compatibility contracts for older callers;
new code should use the explicit authority-specific names.
"""

from __future__ import annotations

from typing import Any, Protocol, TypedDict, runtime_checkable

from cept.schema.case import Case


class Capability(TypedDict, total=False):
    """Backward-compatible common capability facts.

    Historically one shape carried both execution-feasibility and engine
    evidence fields.  Keep the common keys stable while new code uses the
    narrower authority-specific contracts below.
    """

    passport_schema_version: int
    engine: str
    study_type: str
    fidelity: str
    claim_cap: str
    runtime: Any
    license: Any
    experimental_features: list[str]
    supported_model_families: list[str]
    prohibited_claims: list[str]


class ExecutionCapability(Capability, total=False):
    """Facts owned by the execution-feasibility capability matrix."""

    matrix_schema_version: int
    representation_family: str
    status: str
    reason: str
    gate: str
    supported_quantities: list[str]
    unsupported_quantities: list[str]
    required_inputs: list[str]


class EvidenceCapability(Capability, total=False):
    """Facts owned by the engine evidence/qualification passport."""

    current_claim: str
    maximum_claim_if_external_evidence_satisfied: str
    missing_evidence_for_higher_claim: list[str]
    trust_summary: dict[str, Any]
    research_status: str
    research_benchmark: Any
    validated_runtime_version: Any
    validated_platforms: list[str]
    last_verified_release: Any


class ResolvedStudyCapability(TypedDict):
    """Aggregate view that preserves both capability authorities."""

    engine: str
    study_type: str
    execution: ExecutionCapability
    evidence: EvidenceCapability


@runtime_checkable
class CapabilityResolver(Protocol):
    """Compatibility resolver protocol retained for existing integrations."""

    def for_case(self, case: Case) -> Capability: ...


@runtime_checkable
class ExecutionCapabilityResolver(Protocol):
    def for_case(self, case: Case) -> ExecutionCapability: ...


@runtime_checkable
class EvidenceCapabilityResolver(Protocol):
    def for_case(self, case: Case) -> EvidenceCapability: ...


__all__ = [
    "Capability",
    "CapabilityResolver",
    "EvidenceCapability",
    "EvidenceCapabilityResolver",
    "ExecutionCapability",
    "ExecutionCapabilityResolver",
    "ResolvedStudyCapability",
]
