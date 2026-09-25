"""Shared pre-render contract for Native and Interactive SLDs.

The geometry kernel already gives both renderers the same bus/port/route
coordinates.  This module adds the metadata boundary around that geometry:
physical buses, branch identities/kinds, and terminal cardinality must be
resolved and validated before either renderer is allowed to draw.

Renderer-specific transforms, symbols, colours, and viewport settings are not
part of the contract identity.  They may change presentation, but they must
not change the physical topology or canonical route plan consumed by Native
PowerFactory and Interactive SLD.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import hashlib
import json
from typing import Any

from cept.domain.sld.geometry import CanonicalSLDGeometry, validate_canonical_geometry
from cept.domain.sld.geometry_receipt import geometry_fingerprint

SCHEMA = "cept-canonical-sld-render-contract-v1"
_HEX = frozenset("0123456789abcdefABCDEF")


def _ids(label: str, values: Iterable[object]) -> tuple[str, ...]:
    result = tuple(sorted(str(value).strip().lower() for value in values))
    if any(not value for value in result):
        raise ValueError(f"{label} contains an empty identifier")
    if len(set(result)) != len(result):
        raise ValueError(f"{label} contains duplicate identifiers")
    return result


def _sha256(value: object, *, label: str) -> str:
    text = str(value or "").strip().lower()
    if len(text) != 64 or any(char not in _HEX for char in text):
        raise ValueError(f"{label} must be an exact SHA-256")
    return text


def _branch_kinds(values: Iterable[tuple[object, object]]) -> tuple[tuple[str, str], ...]:
    result = tuple(
        sorted(
            (str(edge_id).strip().lower(), str(kind).strip().lower())
            for edge_id, kind in values
        )
    )
    if any(not edge_id or not kind for edge_id, kind in result):
        raise ValueError("branch metadata contains an empty edge id or kind")
    if len({edge_id for edge_id, _kind in result}) != len(result):
        raise ValueError("branch metadata contains duplicate edge identifiers")
    return result


def _terminal_counts(
    values: Mapping[str, int] | None,
    *,
    physical_bus_ids: tuple[str, ...],
) -> tuple[tuple[str, int], ...]:
    physical = set(physical_bus_ids)
    result: list[tuple[str, int]] = []
    for bus_id, count in (values or {}).items():
        key = str(bus_id).strip().lower()
        if not key:
            raise ValueError("terminal metadata contains an empty bus id")
        if key not in physical:
            raise ValueError(f"terminal metadata references non-physical bus {bus_id!r}")
        try:
            number = int(count)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"terminal metadata for {bus_id!r} is not an integer") from exc
        if number < 0:
            raise ValueError(f"terminal metadata for {bus_id!r} is negative")
        if number:
            result.append((key, number))
    if len({bus_id for bus_id, _count in result}) != len(result):
        raise ValueError("terminal metadata contains duplicate bus identifiers")
    return tuple(sorted(result))


@dataclass(frozen=True, slots=True)
class SLDRenderContract:
    """Immutable metadata + geometry contract shared by both renderers."""

    geometry: CanonicalSLDGeometry
    geometry_sha256: str
    layout_sha256: str
    physical_bus_ids: tuple[str, ...]
    branch_kinds: tuple[tuple[str, str], ...]
    terminal_counts: tuple[tuple[str, int], ...]
    schema: str = SCHEMA

    @property
    def contract_sha256(self) -> str:
        payload = {
            "schema": self.schema,
            "geometry_sha256": self.geometry_sha256,
            "layout_sha256": self.layout_sha256,
            "physical_bus_ids": list(self.physical_bus_ids),
            "branch_kinds": [list(item) for item in self.branch_kinds],
            "terminal_counts": [list(item) for item in self.terminal_counts],
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def receipt(self, *, consumer: str, renderer: str | None = None) -> dict[str, Any]:
        """Return the stable evidence payload for a renderer consumer."""
        return {
            "schema": self.schema,
            "consumer": consumer,
            "renderer": renderer,
            "geometry_version": self.geometry.version,
            "geometry_sha256": self.geometry_sha256,
            "layout_sha256": self.layout_sha256,
            "contract_sha256": self.contract_sha256,
            "physical_bus_ids": list(self.physical_bus_ids),
            "branch_kinds": [list(item) for item in self.branch_kinds],
            "terminal_counts": {
                bus_id: count for bus_id, count in self.terminal_counts
            },
        }


def build_sld_render_contract(
    geometry: CanonicalSLDGeometry,
    *,
    layout_sha256: str,
    physical_bus_ids: Iterable[object],
    branch_kinds: Iterable[tuple[object, object]],
    terminal_counts: Mapping[str, int] | None = None,
) -> SLDRenderContract:
    """Validate and freeze the metadata both SLD renderers must consume."""
    violations = validate_canonical_geometry(geometry)
    if violations:
        raise ValueError("invalid shared SLD geometry: " + ", ".join(violations))

    physical = _ids("physical bus metadata", physical_bus_ids)
    geometry_buses = {bus.bus_id.strip().lower() for bus in geometry.buses}
    if set(physical) != geometry_buses:
        raise ValueError(
            "physical bus metadata does not match canonical geometry: "
            f"metadata={sorted(physical)!r}, geometry={sorted(geometry_buses)!r}"
        )

    branches = _branch_kinds(branch_kinds)
    geometry_edges = {route.edge_id.strip().lower() for route in geometry.routes}
    branch_edges = {edge_id for edge_id, _kind in branches}
    if branch_edges != geometry_edges:
        raise ValueError(
            "branch metadata does not match canonical geometry: "
            f"metadata={sorted(branch_edges)!r}, geometry={sorted(geometry_edges)!r}"
        )

    counts = _terminal_counts(terminal_counts, physical_bus_ids=physical)
    geometry_sha = geometry_fingerprint(geometry)
    return SLDRenderContract(
        geometry=geometry,
        geometry_sha256=geometry_sha,
        layout_sha256=_sha256(layout_sha256, label="layout SHA-256"),
        physical_bus_ids=physical,
        branch_kinds=branches,
        terminal_counts=counts,
    )


__all__ = ["SCHEMA", "SLDRenderContract", "build_sld_render_contract"]
