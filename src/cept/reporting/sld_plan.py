"""Canonical renderer plan for the Interactive SLD.

The solved-value transport may contain renderer/engine compatibility nodes, but
the visible engineering plan is built from the physical canonical layout. The
renderer replays this immutable plan plus solver values/tooltips; it does not
invent positions, bus orientations, branch bends, or synthetic source buses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from cept.schema.sld import SLDEdge, SLDModel
from cept.domain.sld.geometry import (
    CANONICAL_RENDER_PORT_SPACING,
    CANONICAL_TERMINAL_CLEARANCE,
    CANONICAL_TERMINAL_CLEARANCE_MAX,
    CanonicalSLDGeometry,
    Point,
    canonical_geometry,
    inline_device_placement,
    place_terminal_devices,
)
from cept.domain.sld.layout_contract import (
    CANONICAL_TERMINAL_PORT_SPACING,
    compact_terminal_bus_half_lengths,
    layout_fingerprint,
    physical_sld_edges,
    route_safe_positions,
    solved_layout_plan,
)
from cept.domain.sld.render_contract import SLDRenderContract, build_sld_render_contract

PRIMARY_FONT_PX = 13
DEVICE_FONT_PX = 12
SECONDARY_FONT_PX = 10
_DENSE_CANONICAL_MIN_BUSES = 25

InteractiveDeviceKind = Literal[
    "load",
    "pv",
    "wind",
    "hydro",
    "syncgen",
    "battery",
    "generator",
    "indmach",
    "grid",
    "capacitor",
    "reactor",
    "statcom",
    "svc",
]


@dataclass(frozen=True, slots=True)
class InteractiveBusPlan:
    bus_id: str
    center: Point
    orientation: Literal["h", "v"]
    half_length: float


@dataclass(frozen=True, slots=True)
class InteractiveBranchPlan:
    edge_id: str
    src: str
    dst: str
    kind: str
    points: tuple[Point, ...]
    status: str
    inline_center: Point | None = None
    inline_axis: Literal["h", "v"] | None = None


@dataclass(frozen=True, slots=True)
class InteractiveDevicePlan:
    device_id: str
    kind: InteractiveDeviceKind
    bus_id: str
    center: Point
    bus_port: Point
    direction: Literal["up", "down", "left", "right"]


@dataclass(frozen=True, slots=True)
class InteractiveUXPolicy:
    """Presentation defaults that do not alter canonical geometry."""

    primary_font_px: int = PRIMARY_FONT_PX
    device_font_px: int = DEVICE_FONT_PX
    secondary_font_px: int = SECONDARY_FONT_PX
    branch_data_default: Literal["hidden"] = "hidden"
    branch_data_interaction: Literal["hover-click"] = "hover-click"
    viewport_transform_only: bool = True
    uniform_xy_scale: bool = True
    status_color_semantics: bool = True


@dataclass(frozen=True, slots=True)
class InteractiveSLDPlan:
    geometry: CanonicalSLDGeometry
    geometry_sha256: str
    layout_sha256: str
    render_contract: SLDRenderContract
    physical_bus_ids: tuple[str, ...]
    virtual_source_bus_ids: tuple[str, ...]
    buses: tuple[InteractiveBusPlan, ...]
    branches: tuple[InteractiveBranchPlan, ...]
    devices: tuple[InteractiveDevicePlan, ...]
    ux: InteractiveUXPolicy = InteractiveUXPolicy()
    schema: str = "cept-interactive-sld-plan-v2"

    def branch(self, edge_id: str) -> InteractiveBranchPlan:
        for branch in self.branches:
            if branch.edge_id == edge_id:
                return branch
        raise KeyError(edge_id)


def _device_inputs(
    sld: SLDModel,
    *,
    allowed_bus_ids: set[str],
    virtual_source_bus_ids: tuple[str, ...],
) -> dict[str, tuple[str, list[tuple[str, str]]]]:
    """Return visual terminal nodes grouped only by physical bus.

    Loads stay aggregated to one glyph per bus for presentation. Busbar sizing
    is handled separately from these visual nodes so hidden per-load model
    cardinality can still match Native PowerFactory geometry.
    """
    result: dict[str, tuple[str, list[tuple[str, str]]]] = {}
    for node in sld.nodes:
        key = str(node.id).lower()
        if key not in allowed_bus_ids:
            continue
        devices: list[tuple[str, str]] = []
        if node.loads:
            devices.append((f"__load_{node.id}", "load"))
        devices.extend((f"__gen_{node.id}_{index}", item.kind) for index, item in enumerate(node.gens))
        devices.extend((f"__shunt_{node.id}_{index}", item.kind) for index, item in enumerate(node.shunts))
        if devices:
            result[key] = (node.id, sorted(devices))
    for device_id, bus_id, _source_id in virtual_grid_attachments(
        sld,
        allowed_bus_ids=allowed_bus_ids,
        virtual_source_bus_ids=virtual_source_bus_ids,
    ):
        key = bus_id.lower()
        original, entries = result.setdefault(key, (bus_id, []))
        if device_id not in {item[0] for item in entries}:
            entries.append((device_id, "grid"))
            entries.sort()
        result[key] = (original, entries)
    return result


def virtual_grid_attachments(
    sld: SLDModel,
    *,
    allowed_bus_ids: set[str],
    virtual_source_bus_ids: tuple[str, ...],
) -> tuple[tuple[str, str, str], ...]:
    """Return virtual-grid links as physical terminal attachments.

    OpenDSS transports an inline external grid as ``GridLink.<name>`` between
    the physical Case bus and a compatibility source node. The source node is
    intentionally excluded from physical geometry, but the grid still counts
    as one terminal on the physical bus just like the native PF plan. Older
    transports already carry a ``kind=grid`` generator on that bus; those are
    not counted twice.
    """
    physical = {str(bus_id).lower() for bus_id in allowed_bus_ids}
    virtual = {str(bus_id).lower() for bus_id in virtual_source_bus_ids}
    nodes = {str(node.id).lower(): node for node in sld.nodes}
    result: list[tuple[str, str, str]] = []
    for edge in sld.edges:
        if not str(edge.id).casefold().startswith("gridlink."):
            continue
        endpoints = (str(edge.src), str(edge.dst))
        virtual_endpoints = [item for item in endpoints if item.lower() in virtual]
        physical_endpoints = [item for item in endpoints if item.lower() in physical]
        if len(virtual_endpoints) != 1 or len(physical_endpoints) != 1:
            continue
        bus_id = physical_endpoints[0]
        physical_node = nodes.get(bus_id.lower())
        if physical_node is None or any(
            str(getattr(gen, "kind", "")).lower() == "grid"
            for gen in getattr(physical_node, "gens", ())
        ):
            continue
        source_id = virtual_endpoints[0]
        result.append((f"grid:{source_id}", bus_id, source_id))
    return tuple(sorted(set(result), key=lambda item: (item[1].lower(), item[0].lower())))


def _terminal_capacity_counts(
    sld: SLDModel,
    *,
    allowed_bus_ids: set[str],
    virtual_source_bus_ids: tuple[str, ...],
) -> dict[str, int]:
    """Reserve geometry slots from physical model cardinality, not glyph count."""
    result: dict[str, int] = {}
    for node in sld.nodes:
        key = str(node.id).lower()
        if key not in allowed_bus_ids:
            continue
        count = len(node.loads) + len(node.gens) + len(node.shunts)
        if count:
            result[key] = count
    for _device_id, bus_id, _source_id in virtual_grid_attachments(
        sld,
        allowed_bus_ids=allowed_bus_ids,
        virtual_source_bus_ids=virtual_source_bus_ids,
    ):
        key = bus_id.lower()
        result[key] = result.get(key, 0) + 1
    return result


def _selected_positions(
    sld: SLDModel,
    positions: dict[str, tuple[float, float]] | None,
) -> tuple[
    dict[str, tuple[float, float]],
    tuple[str, ...],
    tuple[str, ...],
    tuple[SLDEdge, ...],
    str,
]:
    layout = solved_layout_plan(sld)
    physical = set(layout.physical_bus_ids)
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
                bus_ids=physical,
                edges=((edge.id, edge.src, edge.dst) for edge in edges),
            )

    edge_ids = tuple(sorted(str(edge.id) for edge in edges))
    layout_sha = layout_fingerprint(
        selected,
        edge_ids=edge_ids,
        source_bus_ids=layout.source_bus_ids,
    )
    return (
        selected,
        tuple(sorted(physical)),
        tuple(sorted(layout.virtual_source_bus_ids)),
        edges,
        layout_sha,
    )


def build_interactive_sld_plan(
    sld: SLDModel,
    *,
    positions: dict[str, tuple[float, float]] | None = None,
    terminal_clearance: float = CANONICAL_TERMINAL_CLEARANCE,
) -> InteractiveSLDPlan:
    """Build renderer-neutral Interactive SLD plan from solved view data."""
    selected, physical_bus_ids, virtual_bus_ids, edges, layout_sha = _selected_positions(sld, positions)
    physical = set(physical_bus_ids)
    device_inputs = _device_inputs(
        sld,
        allowed_bus_ids=physical,
        virtual_source_bus_ids=virtual_bus_ids,
    )
    terminal_counts = _terminal_capacity_counts(
        sld,
        allowed_bus_ids=physical,
        virtual_source_bus_ids=virtual_bus_ids,
    )
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
    edge_by_id: dict[str, SLDEdge] = {edge.id: edge for edge in edges}
    render_contract = build_sld_render_contract(
        geometry,
        layout_sha256=layout_sha,
        physical_bus_ids=physical_bus_ids,
        branch_kinds=((edge.id, edge.kind) for edge in edges),
        terminal_counts=terminal_counts,
    )

    buses = tuple(
        InteractiveBusPlan(
            bus_id=bus.bus_id,
            center=bus.center,
            orientation=bus.orientation,
            half_length=bus.half_length,
        )
        for bus in geometry.buses
    )

    branches: list[InteractiveBranchPlan] = []
    for route in geometry.routes:
        edge = edge_by_id[route.edge_id]
        inline_center: Point | None = None
        inline_axis: Literal["h", "v"] | None = None
        if edge.kind in {"transformer", "regulator", "switch", "converter"}:
            inline_placement = inline_device_placement(route)
            inline_center = inline_placement.center
            inline_axis = inline_placement.axis
        branches.append(
            InteractiveBranchPlan(
                edge_id=edge.id,
                src=edge.src,
                dst=edge.dst,
                kind=edge.kind,
                points=route.points,
                status=edge.status,
                inline_center=inline_center,
                inline_axis=inline_axis,
            )
        )

    devices: list[InteractiveDevicePlan] = []
    effective_terminal_clearance = min(float(terminal_clearance), CANONICAL_TERMINAL_CLEARANCE_MAX)
    for bus_key, (_original, entries) in sorted(device_inputs.items()):
        kinds = {device_id: kind for device_id, kind in entries}
        placements = place_terminal_devices(
            geometry,
            bus_id=bus_key,
            device_ids=[device_id for device_id, _kind in entries],
            device_kinds=kinds,
            clearance=effective_terminal_clearance,
            port_spacing=CANONICAL_TERMINAL_PORT_SPACING,
        )
        if len(placements) != len(entries):
            placed = {item.device_id for item in placements}
            missing = sorted(device_id for device_id, _kind in entries if device_id not in placed)
            raise ValueError(
                f"canonical Interactive SLD could not place every terminal on {bus_key}: {missing}"
            )
        for placement in placements:
            kind = kinds[placement.device_id]
            devices.append(
                InteractiveDevicePlan(
                    device_id=placement.device_id,
                    kind=kind,  # type: ignore[arg-type]
                    bus_id=placement.bus_id,
                    center=placement.center,
                    bus_port=placement.bus_port,
                    direction=placement.direction,
                )
            )

    return InteractiveSLDPlan(
        geometry=geometry,
        geometry_sha256=render_contract.geometry_sha256,
        layout_sha256=render_contract.layout_sha256,
        render_contract=render_contract,
        physical_bus_ids=physical_bus_ids,
        virtual_source_bus_ids=virtual_bus_ids,
        buses=buses,
        branches=tuple(branches),
        devices=tuple(devices),
    )


__all__ = [
    "DEVICE_FONT_PX",
    "PRIMARY_FONT_PX",
    "SECONDARY_FONT_PX",
    "InteractiveBranchPlan",
    "InteractiveBusPlan",
    "InteractiveDeviceKind",
    "InteractiveDevicePlan",
    "InteractiveSLDPlan",
    "InteractiveUXPolicy",
    "build_interactive_sld_plan",
    "virtual_grid_attachments",
]
