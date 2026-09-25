"""Renderer-neutral SLD layout contract.

This module owns the *position-selection* boundary that previously lived in
renderer-specific code. Native PowerFactory and Interactive rendering may apply
different finite-page/view transforms, but they must start from the same
physical engineering hierarchy and the same compact busbar sizing policy.

The solved SLD transport can contain legacy ``GridLink.*`` pseudo buses. They
are deliberately excluded from the physical layout contract: an external grid
is terminal/source semantics attached to its physical bus, not an extra
network bus/branch.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Iterable, Mapping

from cept.domain.sld.engineering_layout import (
    classify_inline_topology,
    hierarchical_layout,
    sld_topology_layout,
)
from cept.schema.case import InlineNetwork
from cept.schema.sld import SLDEdge, SLDModel

_LAYOUT_SCHEMA = "cept-canonical-sld-layout-v1"
_ENGINE_BRANCH_PREFIXES = frozenset({"line", "transformer", "switch", "regulator"})
# Branch ports need a wide engineering fan-out pitch. Terminal devices live on
# the opposite busbar side in the common source/load cases and therefore do not
# need to inherit that same 48-unit longitudinal pitch. The allocator uses
# offsets 0, -pitch, +pitch, ...; the pitch must leave a visible gap around
# the terminal glyph in both renderers.
# Terminal glyphs are about 18 world units wide in the Interactive renderer.
# Keep their centre pitch larger than the glyph envelope so multiple loads or
# DERs on one bus remain visually distinct in both renderers.
CANONICAL_TERMINAL_PORT_SPACING = 20.0
# These are presentation coordinates, not electrical distances.  They keep a
# topology-derived fallback compact while leaving enough room for canonical
# busbars, terminal glyphs, and the renderer's local route leads.
_ROUTE_SAFE_X_GAP = 192.0
_ROUTE_SAFE_Y_GAP = 96.0


@dataclass(frozen=True, slots=True)
class CanonicalSLDLayoutPlan:
    positions: dict[str, tuple[float, float]]
    physical_bus_ids: tuple[str, ...]
    physical_edge_ids: tuple[str, ...]
    source_bus_ids: tuple[str, ...]
    virtual_source_bus_ids: tuple[str, ...]
    family: str
    fingerprint: str
    schema: str = _LAYOUT_SCHEMA


def canonical_sld_edge_id(edge_id: object) -> str:
    """Return the engine-neutral branch identity used by both SLD renderers.

    Solved OpenDSS transport commonly carries identifiers such as
    ``Line.source_ht_1`` while the canonical Case/PF plan uses
    ``source_ht_1``.  The element class is adapter provenance, not a second
    engineering branch.  Only known branch-class prefixes are removed; a
    user/model identifier containing a dot remains unchanged.
    """
    value = str(edge_id).strip()
    head, separator, tail = value.partition(".")
    if separator and head.casefold() in _ENGINE_BRANCH_PREFIXES and tail:
        return tail
    return value


def _normalized_positions(
    positions: dict[str, tuple[float, float]],
    bus_ids: Iterable[str],
) -> dict[str, tuple[float, float]]:
    wanted = {str(bus_id).lower() for bus_id in bus_ids}
    return {
        str(name).lower(): (round(float(point[0]), 6), round(float(point[1]), 6))
        for name, point in positions.items()
        if str(name).lower() in wanted
    }


def _explicit_positions_use_reversed_y(
    explicit_positions: Mapping[str, tuple[float, float]],
    canonical_positions: Mapping[str, tuple[float, float]],
    edges: Iterable[SLDEdge],
    *,
    source_ids: Iterable[str] = (),
) -> bool:
    """Detect an engine layout whose downstream Y direction is inverted.

    OpenDSS can expose legacy screen coordinates with upstream buses at a
    larger Y value, while the CEPT layout contract uses +Y downstream.  The
    edge directions are not assumed to be source-to-load here; comparing the
    sign on both endpoints still tells us whether the entire coordinate
    system is vertically mirrored.  Horizontal edges do not contribute a
    vote.
    """

    edge_list = tuple(edges)
    aligned = 0
    reversed_ = 0
    topology_depth: dict[str, int] = {}
    adjacency: dict[str, set[str]] = {}
    for edge in edge_list:
        source_id = str(edge.src).lower()
        target_id = str(edge.dst).lower()
        adjacency.setdefault(source_id, set()).add(target_id)
        adjacency.setdefault(target_id, set()).add(source_id)
    roots = sorted({str(source_id).lower() for source_id in source_ids} & set(adjacency))
    frontier = list(roots)
    topology_depth.update({root: 0 for root in roots})
    while frontier:
        next_frontier: list[str] = []
        for node_id in frontier:
            for neighbour in sorted(adjacency.get(node_id, ())):
                if neighbour in topology_depth:
                    continue
                topology_depth[neighbour] = topology_depth[node_id] + 1
                next_frontier.append(neighbour)
        frontier = next_frontier

    for edge in edge_list:
        source = explicit_positions.get(str(edge.src).lower())
        target = explicit_positions.get(str(edge.dst).lower())
        canonical_source = canonical_positions.get(str(edge.src).lower())
        canonical_target = canonical_positions.get(str(edge.dst).lower())
        if source is None or target is None or canonical_source is None or canonical_target is None:
            continue

        explicit_dy = float(target[1]) - float(source[1])
        canonical_dy = float(canonical_target[1]) - float(canonical_source[1])
        if abs(canonical_dy) <= 1e-9:
            source_depth = topology_depth.get(str(edge.src).lower())
            target_depth = topology_depth.get(str(edge.dst).lower())
            if source_depth is not None and target_depth is not None:
                canonical_dy = float(target_depth - source_depth)
        if abs(explicit_dy) <= 1e-9 or abs(canonical_dy) <= 1e-9:
            continue
        if explicit_dy * canonical_dy > 0:
            aligned += 1
        else:
            reversed_ += 1

    return reversed_ > aligned


def layout_fingerprint(
    positions: dict[str, tuple[float, float]],
    *,
    edge_ids: Iterable[str] = (),
    source_bus_ids: Iterable[str] = (),
) -> str:
    payload = {
        "schema": _LAYOUT_SCHEMA,
        "positions": [
            [name, round(point[0], 6), round(point[1], 6)]
            for name, point in sorted(positions.items())
        ],
        "edge_ids": sorted(canonical_sld_edge_id(edge_id).lower() for edge_id in edge_ids),
        "source_bus_ids": sorted(str(bus_id).lower() for bus_id in source_bus_ids),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def inline_layout_plan(net: InlineNetwork) -> CanonicalSLDLayoutPlan:
    """Return the canonical automatic engineering layout for an InlineNetwork."""
    positions = hierarchical_layout(net)
    bus_ids = tuple(sorted((bus.name for bus in net.buses), key=str.lower))
    normalized = _normalized_positions(positions, bus_ids)
    edge_ids = tuple(
        sorted(
            [line.name for line in net.lines]
            + [item.name for item in net.transformers]
            + [item.name for item in net.switches]
        )
    )
    sources = tuple(sorted({grid.bus.lower() for grid in net.external_grids}))
    if not sources:
        sources = tuple(
            sorted(
                {
                    gen.bus.lower()
                    for gen in net.generators
                    if str(getattr(gen, "bus_type", "")).lower() == "slack"
                }
            )
        )
    intent = classify_inline_topology(net)
    return CanonicalSLDLayoutPlan(
        positions=normalized,
        physical_bus_ids=tuple(sorted(normalized)),
        physical_edge_ids=edge_ids,
        source_bus_ids=sources,
        virtual_source_bus_ids=(),
        family=intent.family,
        fingerprint=layout_fingerprint(normalized, edge_ids=edge_ids, source_bus_ids=sources),
    )


def _solved_source_partition(sld: SLDModel) -> tuple[set[str], set[str]]:
    nodes = {str(node.id).lower(): node for node in sld.nodes}
    grid_buses = {
        str(node.id)
        for node in sld.nodes
        if any(str(getattr(gen, "kind", "")).lower() == "grid" for gen in getattr(node, "gens", ()))
    }
    virtual: set[str] = set()
    for edge in sld.edges:
        if not str(edge.id).casefold().startswith("gridlink."):
            continue
        a, b = str(edge.src), str(edge.dst)
        a_key, b_key = a.lower(), b.lower()
        if a_key not in nodes or b_key not in nodes:
            continue
        explicit = {a_key, b_key} & {bus_id.lower() for bus_id in grid_buses}
        if explicit:
            # Older solved transports put a grid generator on the physical
            # attachment bus and connect it to a compatibility pseudo-node.
            physical_key = next(iter(explicit))
            virtual_key = b_key if physical_key == a_key else a_key
            virtual.add(nodes[virtual_key].id)
            continue

        # The OpenDSS inline adapter represents the utility as a dedicated
        # substation node linked to a plain physical attachment bus.  Infer
        # that virtual endpoint from the explicit substation marker; do not
        # make the virtual node a second physical bus.
        substation = [
            key
            for key in (a_key, b_key)
            if str(getattr(nodes[key], "kind", "")).lower() == "substation"
        ]
        if len(substation) == 1:
            virtual_key = substation[0]
            physical_key = b_key if virtual_key == a_key else a_key
            grid_buses.add(nodes[physical_key].id)
            virtual.add(nodes[virtual_key].id)
            continue

        # If both endpoints are marked substation, prefer the endpoint that
        # has no non-GridLink incident branch. This is deterministic and
        # keeps the physical source bus when the transport is still useful
        # but its adapter metadata is incomplete.
        non_grid_degree = {key: 0 for key in (a_key, b_key)}
        for candidate in sld.edges:
            if str(candidate.id).casefold().startswith("gridlink."):
                continue
            for endpoint in (str(candidate.src).lower(), str(candidate.dst).lower()):
                if endpoint in non_grid_degree:
                    non_grid_degree[endpoint] += 1
        zero_degree = [key for key, degree in non_grid_degree.items() if degree == 0]
        if len(zero_degree) == 1:
            virtual_key = zero_degree[0]
            physical_key = b_key if virtual_key == a_key else a_key
            grid_buses.add(nodes[physical_key].id)
            virtual.add(nodes[virtual_key].id)
    return grid_buses, virtual


def physical_sld_edges(sld: SLDModel, virtual_source_bus_ids: Iterable[str]) -> tuple[SLDEdge, ...]:
    virtual = {str(bus_id).lower() for bus_id in virtual_source_bus_ids}
    return tuple(
        edge.model_copy(update={"id": canonical_sld_edge_id(edge.id)})
        for edge in sld.edges
        if not str(edge.id).casefold().startswith("gridlink.")
        and str(edge.src).lower() not in virtual
        and str(edge.dst).lower() not in virtual
    )


def _simple_topology_is_forest(
    bus_ids: Iterable[str],
    edges: Iterable[tuple[str, str, str]],
) -> bool:
    """Return whether the physical topology is a forest after collapsing parallels.

    Parallel electrical elements are still separate drawing lanes, but they
    do not make a feeder topology meshed.  Collapsing only duplicate endpoint
    pairs lets the route-safe fallback apply to radial feeders such as IEEE13
    while leaving genuinely cyclic networks under their authored/layout
    policy.
    """
    parent = {str(bus_id).lower(): str(bus_id).lower() for bus_id in bus_ids}
    components = len(parent)
    pairs: set[tuple[str, str]] = set()
    for _edge_id, source, target in edges:
        left, right = str(source).lower(), str(target).lower()
        if left == right or left not in parent or right not in parent:
            return False
        pairs.add((left, right) if left < right else (right, left))

    def find(node: str) -> str:
        root = node
        while parent[root] != root:
            root = parent[root]
        while parent[node] != node:
            next_node = parent[node]
            parent[node] = root
            node = next_node
        return root

    for left, right in sorted(pairs):
        left_root, right_root = find(left), find(right)
        if left_root == right_root:
            return False
        parent[left_root] = right_root
        components -= 1
    return len(pairs) == len(parent) - components


def _compact_rank_layout(
    reference: Mapping[str, tuple[float, float]],
) -> dict[str, tuple[float, float]]:
    """Compress a deterministic topology layout without changing rank/order."""
    if not reference:
        return {}
    x_levels = sorted({round(float(point[0]), 6) for point in reference.values()})
    y_levels = sorted({round(float(point[1]), 6) for point in reference.values()})
    x_index = {value: index for index, value in enumerate(x_levels)}
    y_index = {value: index for index, value in enumerate(y_levels)}
    return {
        name: (
            float(x_index[round(float(point[0]), 6)]) * _ROUTE_SAFE_X_GAP,
            float(y_index[round(float(point[1]), 6)]) * _ROUTE_SAFE_Y_GAP,
        )
        for name, point in sorted(reference.items())
    }


def _layout_route_backtracks(
    positions: Mapping[str, tuple[float, float]],
    edges: Iterable[tuple[str, str, str]],
) -> int | None:
    """Measure canonical route reversals for a candidate bus placement."""
    from cept.domain.sld.geometry import (
        CANONICAL_RENDER_PORT_SPACING,
        canonical_geometry,
        route_axis_backtrack_count,
    )

    edge_list = tuple(edges)
    try:
        geometry = canonical_geometry(
            positions=dict(positions),
            edges=edge_list,
            port_spacing=CANONICAL_RENDER_PORT_SPACING,
        )
    except (KeyError, ValueError):
        return None
    return sum(route_axis_backtrack_count(route.points) for route in geometry.routes)


def route_safe_positions(
    explicit: dict[str, tuple[float, float]],
    reference: Mapping[str, tuple[float, float]],
    *,
    bus_ids: Iterable[str],
    edges: Iterable[tuple[str, str, str]],
) -> dict[str, tuple[float, float]]:
    """Replace a small radial layout only when it visibly backtracks.

    Solver coordinates remain authoritative when their canonical routes are
    already readable.  A compact topology placement is a view-only fallback
    for a forest whose source coordinates force U-turns; it cannot invent
    topology or silently rearrange a meshed network.
    """
    edge_list = tuple(edges)
    if not _simple_topology_is_forest(bus_ids, edge_list):
        return explicit
    explicit_score = _layout_route_backtracks(explicit, edge_list)
    if explicit_score is None or explicit_score <= 0:
        return explicit
    compact = _compact_rank_layout(reference)
    compact_score = _layout_route_backtracks(compact, edge_list)
    if compact_score is not None and compact_score < explicit_score:
        return compact
    return explicit


def solved_layout_plan(sld: SLDModel) -> CanonicalSLDLayoutPlan:
    """Return the same physical hierarchy from solved SLD transport data."""
    grid_buses, virtual = _solved_source_partition(sld)
    physical_nodes = [node for node in sld.nodes if str(node.id).lower() not in {item.lower() for item in virtual}]
    physical_ids = tuple(sorted(str(node.id).lower() for node in physical_nodes))
    edges = physical_sld_edges(sld, virtual)
    edge_ids = tuple(sorted(str(edge.id) for edge in edges))

    placed = sld_topology_layout(
        nodes=sld.nodes,
        edges=sld.edges,
        source_ids={bus_id for bus_id in grid_buses},
    )
    explicit = {
        str(node.id): (float(node.x), float(node.y))
        for node in physical_nodes
        if math.isfinite(float(node.x)) and math.isfinite(float(node.y))
    }
    # Small Cases may carry a reviewed source figure layout. Preserve those
    # coordinates when their canonical routes are safe so Interactive consumes
    # the same explicit geometry that the PowerFactory adapter replays.  The
    # route-safe fallback below may replace only a visibly backtracking radial
    # placement; dense networks retain the deterministic topology layout and
    # intentionally ignore raw solver coordinates.
    explicit_normalized = _normalized_positions(explicit, physical_ids)
    canonical_reference = _normalized_positions(placed, physical_ids)
    layout_source_ids = grid_buses or {
        str(node.id)
        for node in physical_nodes
        if str(getattr(node, "kind", "")).lower() == "substation"
    }
    if len(physical_ids) < 25 and set(explicit_normalized) == set(physical_ids):
        normalized = explicit_normalized
        if set(canonical_reference) == set(physical_ids) and _explicit_positions_use_reversed_y(
            explicit_normalized,
            canonical_reference,
            edges,
            source_ids=layout_source_ids,
        ):
            # Some solver transports use a page-space convention where
            # upstream is larger Y. Normalize that legacy view once at the
            # renderer-neutral boundary so every downstream renderer sees the
            # CEPT canonical +Y-downstream contract.
            normalized = {
                bus_id: (position[0], -position[1])
                for bus_id, position in explicit_normalized.items()
            }
    else:
        normalized = canonical_reference
    if (
        len(physical_ids) < 25
        and set(normalized) == set(physical_ids)
        and set(canonical_reference) == set(physical_ids)
    ):
        normalized = route_safe_positions(
            normalized,
            canonical_reference,
            bus_ids=physical_ids,
            edges=((edge.id, edge.src, edge.dst) for edge in edges),
        )
    if set(normalized) != set(physical_ids):
        missing = sorted(set(physical_ids) - set(normalized))
        raise ValueError(f"canonical solved SLD layout is missing physical buses: {missing}")

    sources = tuple(sorted(bus_id.lower() for bus_id in grid_buses if bus_id.lower() in normalized))
    return CanonicalSLDLayoutPlan(
        positions=normalized,
        physical_bus_ids=tuple(sorted(normalized)),
        physical_edge_ids=edge_ids,
        source_bus_ids=sources,
        virtual_source_bus_ids=tuple(sorted(bus_id.lower() for bus_id in virtual)),
        family="solved-physical-topology",
        fingerprint=layout_fingerprint(normalized, edge_ids=edge_ids, source_bus_ids=sources),
    )


def _side_half_length(
    count: int,
    *,
    spacing: float,
    minimum: float,
    edge_margin: float,
    center_first: bool,
) -> float:
    """Half-length needed by one longitudinal side of a busbar."""
    if count <= 1:
        return minimum
    if center_first:
        # Terminal placement grows from the centre as 0, -spacing, +spacing...
        extent = ((count - 1 + 1) // 2) * spacing
    else:
        # Electrical branch ports are centred across the available busbar side.
        extent = ((count - 1) * spacing) / 2.0
    return max(minimum, extent + edge_margin)


def compact_terminal_bus_half_lengths(
    bus_ids: Iterable[str],
    edges: Iterable[tuple[str, str]],
    terminal_counts: dict[str, int],
    *,
    minimum: float = 19.0,
    terminal_minimum: float = 64.0,
    port_spacing: float = 48.0,
    terminal_port_spacing: float = CANONICAL_TERMINAL_PORT_SPACING,
    edge_margin: float = 2.0,
) -> dict[str, float]:
    """Size bars by the busiest *side*, not branches+terminals added together.

    Branches and terminal devices commonly occupy opposite sides of a
    horizontal engineering busbar: upstream branch ``up``, load/shunt ``down``
    (and the analogous left/right pair for vertical bars). Summing those counts
    as if all ports had to share one longitudinal side made ordinary feeder
    leaves exactly as wide as, or wider than, their centre-to-centre pitch.

    This contract reserves centred branch capacity at the canonical branch
    pitch and centre-first terminal capacity at the smaller terminal-device
    pitch, then uses the larger requirement. High-degree branch buses retain
    the historical ``terminal_minimum`` floor.
    """
    if (
        minimum <= 0.0
        or terminal_minimum <= 0.0
        or port_spacing <= 0.0
        or terminal_port_spacing <= 0.0
        or edge_margin < 0.0
    ):
        raise ValueError("busbar sizing dimensions must be valid")
    terminals = {str(bus_id).lower(): max(0, int(count)) for bus_id, count in terminal_counts.items()}
    degree = {str(bus_id).lower(): 0 for bus_id in bus_ids}
    for source, target in edges:
        a, b = str(source).lower(), str(target).lower()
        degree.setdefault(a, 0)
        degree.setdefault(b, 0)
        degree[a] += 1
        degree[b] += 1

    result: dict[str, float] = {}
    for bus_id, branch_count in degree.items():
        terminal_count = terminals.get(bus_id, 0)
        branch_required = _side_half_length(
            branch_count,
            spacing=port_spacing,
            minimum=minimum,
            edge_margin=edge_margin,
            center_first=False,
        )
        terminal_required = _side_half_length(
            terminal_count,
            spacing=terminal_port_spacing,
            minimum=minimum,
            edge_margin=edge_margin,
            center_first=True,
        )
        required = max(branch_required, terminal_required)
        if branch_count >= 3:
            required = max(required, terminal_minimum)
        result[bus_id] = max(minimum, required)
    return result


__all__ = [
    "CANONICAL_TERMINAL_PORT_SPACING",
    "CanonicalSLDLayoutPlan",
    "canonical_sld_edge_id",
    "compact_terminal_bus_half_lengths",
    "inline_layout_plan",
    "layout_fingerprint",
    "physical_sld_edges",
    "route_safe_positions",
    "solved_layout_plan",
]
