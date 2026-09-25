"""Renderer-neutral SLD plan assembled from a persisted solved view.

This boundary is intentionally free of browser, report, vendor-runtime, and
value-replay policy.  It converts the persisted ``SLDModel`` topology and
coordinates into the same canonical geometry contract consumed by native and
interactive renderers.  A renderer may add styles and solver-value overlays,
but it must not choose a second bus/port/route layout.
"""

from __future__ import annotations

from dataclasses import dataclass

from cept.domain.sld.geometry import (
    CANONICAL_RENDER_PORT_SPACING,
    CanonicalSLDGeometry,
    canonical_geometry,
)
from cept.domain.sld.layout_contract import (
    solved_layout_plan,
    CANONICAL_TERMINAL_PORT_SPACING,
    compact_terminal_bus_half_lengths,
    physical_sld_edges,
    route_safe_positions,
)
from cept.domain.sld.render_contract import SLDRenderContract, build_sld_render_contract
from cept.schema.sld import SLDModel

_DENSE_CANONICAL_MIN_BUSES = 25


@dataclass(frozen=True, slots=True)
class CanonicalSLDPlan:
    """Immutable geometry and physical-topology metadata for any renderer."""

    geometry: CanonicalSLDGeometry
    render_contract: SLDRenderContract
    physical_bus_ids: tuple[str, ...]
    virtual_source_bus_ids: tuple[str, ...]
    layout_sha256: str
    schema: str = "cept-canonical-sld-plan-v1"

    @property
    def geometry_sha256(self) -> str:
        return self.render_contract.geometry_sha256


def _terminal_counts(sld: SLDModel, physical_bus_ids: set[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for node in sld.nodes:
        key = str(node.id).lower()
        if key not in physical_bus_ids:
            continue
        count = len(node.loads) + len(node.gens) + len(node.shunts)
        if count:
            counts[key] = count
    return counts


def build_canonical_sld_plan(
    sld: SLDModel,
    *,
    positions: dict[str, tuple[float, float]] | None = None,
) -> CanonicalSLDPlan:
    """Build the canonical plan from persisted solved-SLD transport data."""
    layout = solved_layout_plan(sld)
    physical_bus_ids = tuple(sorted(layout.physical_bus_ids))
    physical = set(physical_bus_ids)
    edges = physical_sld_edges(sld, layout.virtual_source_bus_ids)
    selected = dict(layout.positions)
    if positions is not None and len(physical) < _DENSE_CANONICAL_MIN_BUSES:
        candidate = {
            str(name).lower(): (float(point[0]), float(point[1]))
            for name, point in positions.items()
            if str(name).lower() in physical
        }
        if set(candidate) == physical:
            selected = route_safe_positions(
                candidate,
                layout.positions,
                bus_ids=physical_bus_ids,
                edges=((edge.id, edge.src, edge.dst) for edge in edges),
            )

    terminal_counts = _terminal_counts(sld, physical)
    half_lengths = compact_terminal_bus_half_lengths(
        physical_bus_ids,
        ((edge.src, edge.dst) for edge in edges),
        terminal_counts,
        port_spacing=CANONICAL_RENDER_PORT_SPACING,
        terminal_port_spacing=CANONICAL_TERMINAL_PORT_SPACING,
    )
    geometry = canonical_geometry(
        positions=selected,
        edges=((edge.id, edge.src, edge.dst) for edge in edges),
        bus_half_lengths=half_lengths,
        port_spacing=CANONICAL_RENDER_PORT_SPACING,
    )
    contract = build_sld_render_contract(
        geometry,
        layout_sha256=layout.fingerprint,
        physical_bus_ids=physical_bus_ids,
        branch_kinds=((edge.id, edge.kind) for edge in edges),
        terminal_counts=terminal_counts,
    )
    return CanonicalSLDPlan(
        geometry=geometry,
        render_contract=contract,
        physical_bus_ids=physical_bus_ids,
        virtual_source_bus_ids=tuple(sorted(layout.virtual_source_bus_ids)),
        layout_sha256=layout.fingerprint,
    )


__all__ = ["CanonicalSLDPlan", "build_canonical_sld_plan"]
