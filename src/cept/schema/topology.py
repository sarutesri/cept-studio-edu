"""Deterministic validation for topology/layout evidence extracted from reports.

PDF figures are an evidence source, not a solver input.  A human or a vision
capable harness writes this small manifest; CEPT then reconciles it with the
structured CSV network before a Case is written.  This keeps agents from
silently guessing transformer endpoints or SLD placement.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cept.schema.case import InlineNetwork


class TopologyEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    kind: Literal["line", "transformer"]
    from_bus: str = Field(..., alias="from")
    to_bus: str = Field(..., alias="to")
    evidence: str | None = Field(default=None, min_length=8)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class TopologyManifest(BaseModel):
    """Topology and optional explicit SLD layout extracted from a source figure."""

    model_config = ConfigDict(extra="forbid")

    source_ref: str = Field(..., min_length=1)
    locator: str | None = None
    status: Literal[
        "VERIFIED_STRUCTURED",
        "VERIFIED_VISION",
        "NEEDS_HUMAN_REVIEW",
        "BLOCKED_SOURCE_FIGURE",
        "UNRESOLVED_INPUT",
    ]
    method: Literal["structured", "vision", "human", "pdf-text-or-table"]
    edges: list[TopologyEdge] = Field(..., min_length=1)
    layout: dict[str, tuple[float, float]] | None = None
    unresolved: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _status_method(self) -> "TopologyManifest":
        if self.status == "VERIFIED_VISION" and self.method != "vision":
            raise ValueError("VERIFIED_VISION topology manifest requires method='vision'")
        if self.status == "VERIFIED_STRUCTURED" and self.method not in {"structured", "pdf-text-or-table"}:
            raise ValueError(
                "VERIFIED_STRUCTURED topology manifest requires structured/pdf-text-or-table method"
            )
        if self.status in {"VERIFIED_STRUCTURED", "VERIFIED_VISION"}:
            missing_evidence = [edge.id for edge in self.edges if not edge.evidence]
            if missing_evidence:
                raise ValueError(
                    f"{self.status} topology manifest requires a literal evidence citation "
                    f"(quote/OCR snippet naming both endpoints) for every edge; missing for: "
                    f"{', '.join(missing_evidence)}"
                )
        ids = [edge.id for edge in self.edges]
        if len(ids) != len(set(ids)):
            raise ValueError("topology manifest edge ids must be unique")
        for name, xy in (self.layout or {}).items():
            if not name or not all(math.isfinite(float(v)) for v in xy):
                raise ValueError(f"invalid SLD layout position for bus '{name}'")
        return self

    @property
    def research_ready(self) -> bool:
        return self.status in {"VERIFIED_STRUCTURED", "VERIFIED_VISION"} and not self.unresolved


def load_topology_manifest(path: Path) -> tuple[TopologyManifest, str]:
    if not path.is_file():
        raise FileNotFoundError(f"Topology manifest not found: {path}")
    raw = path.read_bytes()
    return TopologyManifest.model_validate(json.loads(raw)), hashlib.sha256(raw).hexdigest()


def _pair(a: str, b: str) -> frozenset[str]:
    return frozenset((a, b))


def validate_topology_manifest(manifest: TopologyManifest, network: InlineNetwork, *, research: bool) -> None:
    """Reconcile every manifest edge against the structured network."""
    if research and not manifest.research_ready:
        raise ValueError(
            "research intake requires a verified topology manifest with no unresolved inputs; "
            f"got status={manifest.status}, unresolved={manifest.unresolved}"
        )
    buses = {bus.name for bus in network.buses}
    for edge in manifest.edges:
        if edge.from_bus not in buses or edge.to_bus not in buses:
            raise ValueError(
                f"Topology manifest edge {edge.id} references unknown bus: {edge.from_bus}-{edge.to_bus}"
            )

    actual: dict[tuple[str, str], tuple[str, frozenset[str]]] = {}
    for line in network.lines:
        actual[("line", line.name)] = (line.name, _pair(line.from_bus, line.to_bus))
    for tr in network.transformers:
        actual[("transformer", tr.name)] = (tr.name, _pair(tr.hv_bus, tr.lv_bus))
    expected = {(edge.kind, edge.id): (edge.id, _pair(edge.from_bus, edge.to_bus)) for edge in manifest.edges}
    missing = sorted(set(actual) - set(expected))
    extra = sorted(set(expected) - set(actual))
    if missing or extra:
        detail = []
        if missing:
            detail.append("missing from figure: " + ", ".join(f"{k}:{n}" for k, n in missing))
        if extra:
            detail.append("not in CSV network: " + ", ".join(f"{k}:{n}" for k, n in extra))
        raise ValueError("Topology manifest does not cover the CSV network (" + "; ".join(detail) + ")")
    for key, (_, actual_pair) in actual.items():
        expected_pair = expected[key][1]
        if actual_pair != expected_pair:
            kind, name = key
            a, b = sorted(actual_pair)
            c, d = sorted(expected_pair)
            raise ValueError(
                f"Topology manifest mismatch for {kind} '{name}': "
                f"CSV has {a}-{b}; figure manifest has {c}-{d}"
            )
    if manifest.layout:
        unknown = sorted(set(manifest.layout) - buses)
        if unknown:
            raise ValueError("SLD layout references unknown bus(es): " + ", ".join(unknown))


def apply_layout(network: InlineNetwork, manifest: TopologyManifest) -> InlineNetwork:
    if not manifest.layout:
        return network
    payload = network.model_dump(mode="json")
    payload["sld_layout"] = manifest.layout
    return InlineNetwork.model_validate(payload)
