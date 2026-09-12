"""Source and intake provenance for reproducible CEPT Cases.

The schema deliberately records evidence status separately from the solver
Case.  A model may be usable as a demonstrator while still being unsuitable
for research because a topology figure or parameter is unresolved.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


EvidenceStatus = Literal[
    "VERIFIED_STRUCTURED",
    "VERIFIED_VISION",
    "NEEDS_HUMAN_REVIEW",
    "BLOCKED_SOURCE_FIGURE",
    "UNRESOLVED_INPUT",
]


class SourceFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uri: str = Field(..., min_length=1)
    sha256: str = Field(..., pattern=r"^[0-9a-fA-F]{64}$")
    locators: list[str] = Field(default_factory=list)
    # Format/engine are descriptive metadata, deliberately open-ended so the
    # same manifest supports PFD, ANDES, MATPOWER, OpenDSS, PSSE, raw, and
    # future source adapters without another schema migration.
    source_type: str = Field(default="other", pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    engine: str | None = Field(default=None, min_length=1)
    role: str | None = Field(default=None, min_length=1)


class TopologyEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: EvidenceStatus
    method: Literal[
        "structured",
        "pfd-object-graph",
        "dss-or-raw",
        "pdf-text-or-table",
        "vision",
        "human",
        "unavailable",
    ]
    source_ref: str = Field(..., min_length=1)
    excerpt: str | None = Field(default=None, min_length=8)
    notes: str | None = None


class SourceManifest(BaseModel):
    """Evidence manifest produced before CSV/template ingestion."""

    model_config = ConfigDict(extra="forbid")

    sources: list[SourceFile] = Field(..., min_length=1)
    topology: TopologyEvidence
    unresolved: list[str] = Field(default_factory=list)
    reviewer: str | None = None

    @model_validator(mode="after")
    def _check_evidence_status(self) -> "SourceManifest":
        if self.topology.status == "VERIFIED_VISION" and self.topology.method != "vision":
            raise ValueError("VERIFIED_VISION topology evidence must use method='vision'")
        if self.topology.status == "VERIFIED_STRUCTURED" and self.topology.method not in {
            "structured",
            "pfd-object-graph",
            "dss-or-raw",
            "pdf-text-or-table",
        }:
            raise ValueError("VERIFIED_STRUCTURED topology evidence requires structured/PFD/DSS method")
        if self.topology.status in {"VERIFIED_STRUCTURED", "VERIFIED_VISION"}:
            if not self.topology.excerpt:
                raise ValueError(
                    f"{self.topology.status} topology evidence requires a literal source "
                    "excerpt (a direct quote/OCR snippet) proving connectivity was actually "
                    "read, not a self-declared status label"
                )
            if not any(source.locators for source in self.sources):
                raise ValueError(
                    f"{self.topology.status} topology evidence requires at least one "
                    "source with a non-empty locators[] (page/table/figure reference)"
                )
        return self

    @property
    def research_ready(self) -> bool:
        return self.topology.status in {"VERIFIED_STRUCTURED", "VERIFIED_VISION"} and not self.unresolved


class PowerFactoryVariation(BaseModel):
    """A network variation in effect for the selected Study Case.

    A PowerFactory project can describe several networks at once: a variation's
    expansion stage overlays changes on the base model, and which one is active
    is a property of the Study Case, not of the objects.  The Nine-bus example
    swaps every machine's type this way, so two of its study cases describe two
    different sets of machines while every object keeps its name.

    Recording only the resolved numbers loses that: the Case would look
    complete while being silent about which of the project's networks it is.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    full_name: str = Field(..., min_length=1)
    expansion_stages: list[str] = Field(
        default_factory=list,
        description="Names of the stages the source reports for this variation.",
    )


class PowerFactoryStudyCase(BaseModel):
    """Hash-bound identity of the selected PowerFactory Study Case."""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(..., ge=0)
    name: str = Field(..., min_length=1)
    full_name: str = Field(..., min_length=1)
    available_studies: list[str] = Field(default_factory=list)
    # An empty list is a positive statement -- the base network was solved --
    # and is distinct from the field being absent on an older Case.
    active_variations: list[PowerFactoryVariation] = Field(default_factory=list)


class CaseProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_manifest: str = Field(..., min_length=1)
    source_manifest_sha256: str = Field(..., pattern=r"^[0-9a-fA-F]{64}$")
    ingest_receipt: str = Field(..., min_length=1)
    selected_study_case: PowerFactoryStudyCase | None = None
