"""Canonical single-line-diagram geometry v2.

This module is intentionally renderer-neutral.  Interactive HTML and native
PowerFactory graphics must consume the same immutable bus/port/route plan
instead of independently inventing bends, terminal directions, or zoom-time
layout changes.

The contract enforced here is deliberately small and mechanical:

* busbars are horizontal (``h``) or vertical (``v``) only;
* every branch enters each busbar perpendicular to the bar;
* routes contain horizontal/vertical segments only and use 90 degree bends;
* a route does not reverse direction on the same axis just to reach a bus;
* parallel branches receive deterministic, distinct bus ports;
* inline devices are placed on a route segment and inherit its axis;
* terminal devices choose topology-aware free space rather than a device-kind
  hard-coded side;
* viewport zoom/pan is a uniform transform over an immutable geometry plan.

The functions are pure and do not import browser or PowerFactory runtimes, so
this contract can be qualified before the final local rendering/licensed gate.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from typing import Any, Iterable, Literal, Sequence

from cept.schema.case import InlineNetwork
from cept.schema.sld import SLDModel

Axis = Literal["h", "v"]
Direction = Literal["up", "down", "left", "right"]

_EPS = 1e-9
# The renderer-neutral plan uses a wider visible port pitch than the small
# geometry-unit default below.  Keeping this in the shared geometry module
# prevents Interactive and native PowerFactory from choosing different fan
# spacing for the same high-degree bus.
CANONICAL_RENDER_PORT_SPACING = 48.0
# Dense native PF pages compress world coordinates substantially. A short
# route lead then rasterizes too close to a busbar for the full transformer or
# switch glyph envelope, making a valid perpendicular branch look overlaid.
# Keep the value in the shared geometry contract so Native and Interactive use
# the same visible clearance. It is an upper bound: the router scales it down
# for compact source layouts instead of allowing a 128-unit lead to overshoot
# a 50-unit bus-to-bus gap.
CANONICAL_RENDER_LEAD_LENGTH = 128.0
# Short OpenDSS coordinate grids (for example IEEE13) use 50--100 world-unit
# rank spacing. A 16-unit floor leaves room for a real inline glyph while the
# distance fraction keeps the lead below half of a short rank gap.
CANONICAL_RENDER_MIN_LEAD_LENGTH = 16.0
CANONICAL_RENDER_LEAD_FRACTION = 0.32
CANONICAL_TERMINAL_CLEARANCE = 64.0
# Terminal distance is part of the shared topology geometry. Keep it fixed so
# a renderer's overview scale cannot push an attachment into a neighbouring
# branch corridor and make Native/Interactive disagree about free space.
CANONICAL_TERMINAL_CLEARANCE_MAX = CANONICAL_TERMINAL_CLEARANCE
_CARDINAL: dict[Direction, tuple[float, float]] = {
    "up": (0.0, -1.0),
    "down": (0.0, 1.0),
    "left": (-1.0, 0.0),
    "right": (1.0, 0.0),
}


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float

    def as_tuple(self) -> tuple[float, float]:
        return (self.x, self.y)


@dataclass(frozen=True, slots=True)
class BusPort:
    bus_id: str
    edge_id: str
    point: Point
    direction: Direction

    @property
    def normal(self) -> tuple[float, float]:
        return _CARDINAL[self.direction]


@dataclass(frozen=True, slots=True)
class BusGeometry:
    bus_id: str
    center: Point
    orientation: Axis
    half_length: float
    ports: tuple[BusPort, ...] = ()


@dataclass(frozen=True, slots=True)
class RouteGeometry:
    edge_id: str
    src: str
    dst: str
    points: tuple[Point, ...]
    src_port: BusPort
    dst_port: BusPort
    lane: int = 0

    @property
    def manhattan_length(self) -> float:
        return sum(
            abs(right.x - left.x) + abs(right.y - left.y) for left, right in zip(self.points, self.points[1:])
        )


@dataclass(frozen=True, slots=True)
class InlineDevicePlacement:
    edge_id: str
    center: Point
    axis: Axis
    segment_index: int


@dataclass(frozen=True, slots=True)
class TerminalPlacement:
    device_id: str
    bus_id: str
    bus_port: Point
    center: Point
    direction: Direction
    route: tuple[Point, Point]


@dataclass(frozen=True, slots=True)
class CanonicalSLDGeometry:
    """Immutable renderer-neutral SLD plan."""

    buses: tuple[BusGeometry, ...]
    routes: tuple[RouteGeometry, ...]
    version: str = "cept-canonical-sld-geometry-v2"

    def bus(self, bus_id: str) -> BusGeometry:
        key = bus_id.lower()
        for bus in self.buses:
            if bus.bus_id.lower() == key:
                return bus
        raise KeyError(bus_id)

    def route(self, edge_id: str) -> RouteGeometry:
        for route in self.routes:
            if route.edge_id == edge_id:
                return route
        raise KeyError(edge_id)

    def route_points(self) -> dict[str, tuple[tuple[float, float], ...]]:
        return {route.edge_id: tuple(point.as_tuple() for point in route.points) for route in self.routes}


@dataclass(frozen=True, slots=True)
class ViewportTransform:
    """Uniform viewport-only transform.

    A single ``zoom`` scalar intentionally prevents unequal X/Y scaling.  The
    canonical plan is never mutated; callers receive transformed copies of
    points for display only.
    """

    zoom: float = 1.0
    pan_x: float = 0.0
    pan_y: float = 0.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.zoom) or self.zoom <= 0.0:
            raise ValueError("viewport zoom must be finite and > 0")
        if not math.isfinite(self.pan_x) or not math.isfinite(self.pan_y):
            raise ValueError("viewport pan must be finite")

    def apply(self, point: Point) -> Point:
        return Point(point.x * self.zoom + self.pan_x, point.y * self.zoom + self.pan_y)

    def apply_route(self, route: RouteGeometry) -> tuple[Point, ...]:
        return tuple(self.apply(point) for point in route.points)


@dataclass(frozen=True, slots=True)
class _EdgeInput:
    edge_id: str
    src: str
    dst: str


@dataclass(frozen=True, slots=True)
class _Endpoint:
    edge: _EdgeInput
    bus: str
    other: str
    is_src: bool


def _stable_bit(*parts: str) -> int:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).digest()
    return digest[0] & 1


def _axis_aligned(left: Point, right: Point) -> bool:
    return abs(left.x - right.x) <= _EPS or abs(left.y - right.y) <= _EPS


def route_axis_backtrack_count(points: Sequence[Point]) -> int:
    """Count horizontal/vertical direction reversals in one route.

    A Manhattan polyline can satisfy every endpoint and crossing check while
    still drawing a visible U-turn: for example ``down, up`` on the same
    vertical axis.  Keep this as a route-quality metric rather than treating
    every possible reversal as an invalid topology; a meshed network may
    need a detour around another branch.  Tree/rank layouts use it as a hard
    preference and must reach zero before they are selected.
    """
    signs: dict[str, list[int]] = {"x": [], "y": []}
    for left, right in zip(points, points[1:]):
        dx = right.x - left.x
        dy = right.y - left.y
        if abs(dx) > _EPS:
            signs["x"].append(1 if dx > 0.0 else -1)
        if abs(dy) > _EPS:
            signs["y"].append(1 if dy > 0.0 else -1)
    return sum(
        sum(previous != current for previous, current in zip(axis_signs, axis_signs[1:]))
        for axis_signs in signs.values()
    )


def _simplify(points: Sequence[Point]) -> tuple[Point, ...]:
    result: list[Point] = []
    for point in points:
        if result and abs(point.x - result[-1].x) <= _EPS and abs(point.y - result[-1].y) <= _EPS:
            continue
        result.append(point)
    index = 1
    while index < len(result) - 1:
        left, middle, right = result[index - 1 : index + 2]
        collinear_x = abs(left.x - middle.x) <= _EPS and abs(middle.x - right.x) <= _EPS
        collinear_y = abs(left.y - middle.y) <= _EPS and abs(middle.y - right.y) <= _EPS
        if collinear_x or collinear_y:
            result.pop(index)
        else:
            index += 1
    return tuple(result)


def _bus_orientation(
    bus_id: str,
    positions: dict[str, Point],
    incident: Sequence[_Endpoint],
) -> Axis:
    center = positions[bus_id.lower()]
    targets = [positions[ep.other.lower()] for ep in incident if ep.other.lower() in positions]
    if not targets:
        return "h"
    # A downstream degree-one bus below its sole upstream neighbour is a
    # top-down feeder end, not a horizontal-through bus. Keep its bar
    # horizontal so the canonical branch arrives from above instead of making
    # a side-entry stub.
    if len(targets) == 1 and targets[0].y < center.y - _EPS:
        return "h"
    # If all connected branches are strictly horizontal (same Y), vertical bar accepts them perpendicularly.
    if all(abs(t.y - center.y) <= _EPS for t in targets):
        return "v"
    # If branches connect at different X columns and travel vertically, a horizontal busbar distributes them.
    distinct_xs = {round(t.x, 2) for t in targets}
    if len(distinct_xs) >= 2 and any(abs(t.y - center.y) > _EPS for t in targets):
        return "h"
    horizontal_score = sum(abs(t.x - center.x) for t in targets)
    vertical_score = sum(abs(t.y - center.y) for t in targets)
    if horizontal_score > vertical_score:
        return "v"
    if vertical_score > horizontal_score:
        return "h"
    return "h" if _stable_bit(bus_id.lower(), "orientation") == 0 else "v"


def _endpoint_direction(
    *,
    bus_id: str,
    edge_id: str,
    center: Point,
    target: Point,
    orientation: Axis,
) -> Direction:
    dx = target.x - center.x
    dy = target.y - center.y
    if orientation == "h":
        if abs(dy) > _EPS:
            return "down" if dy > 0 else "up"
        return "down" if _stable_bit(bus_id.lower(), edge_id, "normal") else "up"
    if abs(dx) > _EPS:
        return "right" if dx > 0 else "left"
    return "right" if _stable_bit(bus_id.lower(), edge_id, "normal") else "left"


def _slot_offsets(count: int, spacing: float, half_length: float) -> list[float]:
    if count <= 0:
        return []
    usable = max(0.0, half_length - min(2.0, half_length * 0.1))
    if count == 1:
        return [0.0]
    effective = min(spacing, (2.0 * usable) / (count - 1)) if usable else 0.0
    start = -0.5 * effective * (count - 1)
    return [start + index * effective for index in range(count)]


def _build_ports(
    *,
    bus_id: str,
    orientation: Axis,
    center: Point,
    half_length: float,
    incident: Sequence[_Endpoint],
    positions: dict[str, Point],
    spacing: float,
) -> dict[str, BusPort]:
    grouped: dict[Direction, list[_Endpoint]] = {direction: [] for direction in _CARDINAL}
    for endpoint in incident:
        target = positions[endpoint.other.lower()]
        direction = _endpoint_direction(
            bus_id=bus_id,
            edge_id=endpoint.edge.edge_id,
            center=center,
            target=target,
            orientation=orientation,
        )
        grouped[direction].append(endpoint)

    ports: dict[str, BusPort] = {}
    for direction, endpoints in grouped.items():
        if orientation == "h" and direction not in {"up", "down"}:
            continue
        if orientation == "v" and direction not in {"left", "right"}:
            continue
        endpoints = sorted(
            endpoints,
            key=lambda endpoint: (
                positions[endpoint.other.lower()].x
                if orientation == "h"
                else positions[endpoint.other.lower()].y,
                endpoint.edge.edge_id,
                endpoint.is_src,
            ),
        )
        for endpoint, offset in zip(endpoints, _slot_offsets(len(endpoints), spacing, half_length)):
            point = (
                Point(center.x + offset, center.y)
                if orientation == "h"
                else Point(center.x, center.y + offset)
            )
            ports[endpoint.edge.edge_id] = BusPort(
                bus_id=bus_id,
                edge_id=endpoint.edge.edge_id,
                point=point,
                direction=direction,
            )
    return ports


def _lead(port: BusPort, distance: float) -> Point:
    nx, ny = port.normal
    return Point(port.point.x + nx * distance, port.point.y + ny * distance)


def _local_lead_length(
    source: Point,
    target: Point,
    requested: float,
    *,
    source_orientation: Axis,
) -> float:
    """Return a compact lead for one edge while preserving the caller cap.

    ``requested`` remains the maximum visible clearance for large, sparse
    layouts. Small layouts need a local value: using the same lead for every
    edge can put both endpoint leads past the opposite bus and force a U-turn.
    The floor is intentionally modest because the route is still scaled as a
    whole by each renderer.
    """
    axis_distance = (
        abs(target.y - source.y)
        if source_orientation == "h"
        else abs(target.x - source.x)
    )
    if axis_distance <= _EPS:
        # Same-rank branches still need a visible lead, but the lateral gap
        # is the only local scale available.  Keeping this value small avoids
        # manufacturing a large loop merely because the buses are far apart
        # on the other coordinate.
        axis_distance = (
            abs(target.x - source.x)
            if source_orientation == "h"
            else abs(target.y - source.y)
        )
    if axis_distance <= _EPS:
        return requested
    return min(
        requested,
        max(CANONICAL_RENDER_MIN_LEAD_LENGTH, axis_distance * CANONICAL_RENDER_LEAD_FRACTION),
    )


def _lane_index(
    edges: Sequence[_EdgeInput],
    *,
    positions: dict[str, Point],
    orientations: dict[str, Axis],
) -> dict[str, int]:
    """Assign deterministic lanes to branches sharing a drawing corridor.

    Parallel electrical elements are not the only routes that can overlap. A
    dense radial feeder often has many distinct branches joining the same two
    bus ranks; using only the endpoint pair as the lane key makes all of those
    branches reuse the same horizontal trunk. Grouping by the source-axis and
    the two rank coordinates gives each corridor its own stable lane sequence
    while retaining the historical ``0, 1, 2, ...`` ordering for parallel
    elements.
    """
    grouped: dict[tuple[str, float, float], list[_EdgeInput]] = {}
    for edge in edges:
        source = positions[edge.src.lower()]
        target = positions[edge.dst.lower()]
        axis = orientations[edge.src.lower()]
        coordinate = source.y if axis == "h" else source.x
        target_coordinate = target.y if axis == "h" else target.x
        low, high = sorted((round(coordinate, 6), round(target_coordinate, 6)))
        key = (axis, low, high)
        grouped.setdefault(key, []).append(edge)
    result: dict[str, int] = {}
    for group in grouped.values():
        for lane, edge in enumerate(sorted(group, key=lambda item: item.edge_id)):
            result[edge.edge_id] = lane
    return result


def _lane_offset(lane: int, spacing: float) -> float:
    if lane <= 0:
        return 0.0
    magnitude = ((lane + 1) // 2) * spacing
    return magnitude if lane % 2 == 0 else -magnitude


def _corridor_offsets(
    edges: Sequence[_EdgeInput],
    *,
    positions: dict[str, Point],
    orientations: dict[str, Axis],
    spacing: float,
) -> dict[str, float]:
    grouped: dict[tuple[str, Axis, int, int], list[_EdgeInput]] = {}
    for edge in edges:
        source = positions[edge.src.lower()]
        target = positions[edge.dst.lower()]
        axis = orientations[edge.src.lower()]
        if axis == "h":
            travel = target.y - source.y
            travel_dir = 1 if travel >= 0.0 else -1
            lateral = -1 if target.x < source.x else 1
        else:
            travel = target.x - source.x
            travel_dir = 1 if travel >= 0.0 else -1
            lateral = -1 if target.y < source.y else 1
        grouped.setdefault((edge.src.lower(), axis, travel_dir, lateral), []).append(edge)

    offsets: dict[str, float] = {}
    for (src, axis, travel_dir, lateral), group in grouped.items():
        if not group:
            continue
        source = positions[src]
        if axis == "h":
            ordered = sorted(
                group,
                key=lambda edge: (abs(positions[edge.dst.lower()].x - source.x), edge.edge_id),
                reverse=True,
            )
        else:
            ordered = sorted(
                group,
                key=lambda edge: (abs(positions[edge.dst.lower()].y - source.y), edge.edge_id),
                reverse=True,
            )
        for index, edge in enumerate(ordered):
            travel_sign = float(travel_dir)
            magnitude = float(index) * spacing
            offsets[edge.edge_id] = travel_sign * magnitude
    return offsets


def _route_collision_count(
    points: Sequence[Point],
    existing: Sequence[Sequence[Point]],
    *,
    overlap_weight: int = 1,
) -> int:
    """Score interior route crossings/overlaps for deterministic retry.

    Touching at a route endpoint is a legitimate bus connection. Interior
    crossings and positive-length overlaps are not, so they are used as a
    presentation penalty while selecting alternate corridor lanes. A caller
    may make positive-length overlap dominate a mere crossing; this keeps a
    dense feeder from selecting a shared cable trunk merely because a longer
    detour would cross another route once.
    """
    if overlap_weight < 1:
        raise ValueError("route overlap weight must be >= 1")
    count = 0
    current_segments = list(zip(points, points[1:]))
    for left, right in current_segments:
        left_vertical = abs(left.x - right.x) <= _EPS
        for route in existing:
            for other_left, other_right in zip(route, route[1:]):
                other_vertical = abs(other_left.x - other_right.x) <= _EPS
                if left_vertical and other_vertical:
                    if abs(left.x - other_left.x) > _EPS:
                        continue
                    overlap = min(max(left.y, right.y), max(other_left.y, other_right.y)) - max(
                        min(left.y, right.y), min(other_left.y, other_right.y)
                    )
                    if overlap > _EPS:
                        count += overlap_weight
                    continue
                if not left_vertical and not other_vertical:
                    if abs(left.y - other_left.y) > _EPS:
                        continue
                    overlap = min(max(left.x, right.x), max(other_left.x, other_right.x)) - max(
                        min(left.x, right.x), min(other_left.x, other_right.x)
                    )
                    if overlap > _EPS:
                        count += overlap_weight
                    continue
                vertical_left, vertical_right = (left, right) if left_vertical else (other_left, other_right)
                horizontal_left, horizontal_right = (
                    (other_left, other_right) if left_vertical else (left, right)
                )
                x = vertical_left.x
                y = horizontal_left.y
                interior = (
                    min(horizontal_left.x, horizontal_right.x) + _EPS
                    < x
                    < max(horizontal_left.x, horizontal_right.x) - _EPS
                    and min(vertical_left.y, vertical_right.y) + _EPS
                    < y
                    < max(vertical_left.y, vertical_right.y) - _EPS
                )
                if interior:
                    count += 1
    return count


def _route_is_valid_for_axes(
    points: Sequence[Point],
    *,
    source_orientation: Axis,
    target_orientation: Axis,
) -> bool:
    if len(points) < 2:
        return False
    if any(not _axis_aligned(left, right) for left, right in zip(points, points[1:])):
        return False
    if any(left == right for left, right in zip(points, points[1:])):
        return False
    first, second = points[0], points[1]
    penultimate, last = points[-2], points[-1]
    if source_orientation == "h" and abs(first.x - second.x) > _EPS:
        return False
    if source_orientation == "v" and abs(first.y - second.y) > _EPS:
        return False
    if target_orientation == "h" and abs(penultimate.x - last.x) > _EPS:
        return False
    if target_orientation == "v" and abs(penultimate.y - last.y) > _EPS:
        return False
    return True


def _route_between(
    *,
    edge: _EdgeInput,
    src_port: BusPort,
    dst_port: BusPort,
    src_orientation: Axis,
    lane: int,
    lead_length: float,
    lane_spacing: float,
    corridor_offset: float | None = None,
) -> tuple[Point, ...]:
    start = src_port.point
    end = dst_port.point
    lead_a = _lead(src_port, lead_length)
    lead_b = _lead(dst_port, lead_length)
    offset = _lane_offset(lane, lane_spacing) if corridor_offset is None else corridor_offset

    # A corridor track must not land exactly on the destination bus centre.
    # Otherwise the route makes a short U-turn at the target and simplification
    # can erase the final perpendicular leg, which is both misleading in the
    # drawing and invalid under the endpoint contract.
    if src_orientation == "h" and abs(lead_a.y + offset - end.y) <= _EPS:
        travel = 1.0 if end.y >= start.y else -1.0
        offset += travel * lane_spacing
    elif src_orientation == "v" and abs(lead_a.x + offset - end.x) <= _EPS:
        travel = 1.0 if end.x >= start.x else -1.0
        offset += travel * lane_spacing

    # Do not let a parallel-lane offset carry the corridor beyond the target
    # lead.  The resulting path would travel past the destination and then
    # reverse on the same axis, which is the visual U-turn this contract is
    # designed to prevent.  The offset is still allowed to separate routes
    # within the source-to-target lead window.
    if src_orientation == "h":
        travel = end.y - start.y
        corridor = lead_a.y + offset
        target_lead = lead_b.y
        if abs(travel) > _EPS and (corridor - target_lead) * travel > _EPS:
            offset = target_lead - lead_a.y
    else:
        travel = end.x - start.x
        corridor = lead_a.x + offset
        target_lead = lead_b.x
        if abs(travel) > _EPS and (corridor - target_lead) * travel > _EPS:
            offset = target_lead - lead_a.x

    points: list[Point] = [start, lead_a]
    if abs(lead_a.x - lead_b.x) <= _EPS or abs(lead_a.y - lead_b.y) <= _EPS:
        if offset == 0.0:
            points.append(lead_b)
        elif abs(lead_a.y - lead_b.y) <= _EPS:
            y = lead_a.y + offset
            points.extend((Point(lead_a.x, y), Point(lead_b.x, y), lead_b))
        else:
            x = lead_a.x + offset
            points.extend((Point(x, lead_a.y), Point(x, lead_b.y), lead_b))
    elif src_orientation == "h":
        # Keep the first perpendicular lead as the corridor anchor.  Using
        # the midpoint here lets a negative lane offset fold the trunk back
        # through the source port, which simplifies away the required
        # perpendicular connection on short/diagonal branches.
        trunk_y = lead_a.y + offset
        points.extend((Point(lead_a.x, trunk_y), Point(lead_b.x, trunk_y), lead_b))
    else:
        trunk_x = lead_a.x + offset
        points.extend((Point(trunk_x, lead_a.y), Point(trunk_x, lead_b.y), lead_b))
    points.append(end)
    return _simplify(points)


def validate_route_geometry(
    route: RouteGeometry,
    src_bus: BusGeometry,
    dst_bus: BusGeometry,
) -> tuple[str, ...]:
    """Return machine-readable contract violations for one branch route."""
    violations: list[str] = []
    points = route.points
    if len(points) < 2:
        violations.append("route.has_fewer_than_two_points")
        return tuple(violations)
    if points[0] != route.src_port.point:
        violations.append("route.source_port_mismatch")
    if points[-1] != route.dst_port.point:
        violations.append("route.target_port_mismatch")
    for index, (left, right) in enumerate(zip(points, points[1:])):
        if not _axis_aligned(left, right):
            violations.append(f"route.segment[{index}].diagonal")
        if abs(left.x - right.x) <= _EPS and abs(left.y - right.y) <= _EPS:
            violations.append(f"route.segment[{index}].zero_length")

    first, second = points[0], points[1]
    penultimate, last = points[-2], points[-1]
    if src_bus.orientation == "h" and abs(first.x - second.x) > _EPS:
        violations.append("route.source_not_perpendicular")
    if src_bus.orientation == "v" and abs(first.y - second.y) > _EPS:
        violations.append("route.source_not_perpendicular")
    if dst_bus.orientation == "h" and abs(penultimate.x - last.x) > _EPS:
        violations.append("route.target_not_perpendicular")
    if dst_bus.orientation == "v" and abs(penultimate.y - last.y) > _EPS:
        violations.append("route.target_not_perpendicular")
    return tuple(violations)


def validate_canonical_geometry(geometry: CanonicalSLDGeometry) -> tuple[str, ...]:
    violations: list[str] = []
    for route in geometry.routes:
        for violation in validate_route_geometry(route, geometry.bus(route.src), geometry.bus(route.dst)):
            violations.append(f"{route.edge_id}:{violation}")
    return tuple(violations)


def canonical_geometry(
    *,
    positions: dict[str, tuple[float, float]],
    edges: Iterable[tuple[str, str, str]],
    bus_half_length: float = 19.0,
    port_spacing: float = 8.0,
    lead_length: float = CANONICAL_RENDER_LEAD_LENGTH,
    lane_spacing: float = 12.0,
    bus_half_lengths: dict[str, float] | None = None,
    bus_orientation_overrides: dict[str, Axis] | None = None,
) -> CanonicalSLDGeometry:
    """Build the immutable canonical plan from bus positions and branch tuples.

    ``edges`` entries are ``(edge_id, src_bus, dst_bus)``.  Input ordering does
    not affect the resulting route/port assignment.
    """
    if bus_half_length <= 0 or port_spacing <= 0 or lead_length <= 0 or lane_spacing <= 0:
        raise ValueError("canonical SLD geometry dimensions must be > 0")

    pos = {name.lower(): Point(float(value[0]), float(value[1])) for name, value in positions.items()}
    half_lengths = {name.lower(): float(value) for name, value in (bus_half_lengths or {}).items()}
    if any(value <= 0.0 or not math.isfinite(value) for value in half_lengths.values()):
        raise ValueError("canonical SLD bus half lengths must be finite and > 0")

    def half_length_for(bus_id: str) -> float:
        return half_lengths.get(bus_id.lower(), bus_half_length)

    edge_inputs = sorted(
        (_EdgeInput(str(edge_id), str(src), str(dst)) for edge_id, src, dst in edges),
        key=lambda item: (item.edge_id, item.src.lower(), item.dst.lower()),
    )
    if len({edge.edge_id for edge in edge_inputs}) != len(edge_inputs):
        raise ValueError("canonical SLD edge ids must be unique")
    for edge in edge_inputs:
        if edge.src.lower() not in pos or edge.dst.lower() not in pos:
            raise KeyError(f"edge {edge.edge_id!r} references a bus without a position")
        if edge.src.lower() == edge.dst.lower():
            raise ValueError(f"edge {edge.edge_id!r} is a self-loop")

    incident: dict[str, list[_Endpoint]] = {name: [] for name in pos}
    for edge in edge_inputs:
        incident[edge.src.lower()].append(_Endpoint(edge, edge.src, edge.dst, True))
        incident[edge.dst.lower()].append(_Endpoint(edge, edge.dst, edge.src, False))

    orientations = {name: _bus_orientation(name, pos, incident[name]) for name in sorted(pos)}

    # Engineering switchboards are semantic busbars, not arbitrary graph
    # vertices. In a top-down one-line a high-fanout board and its direct
    # feeder terminals must stay horizontal even when the children span a
    # wide X range. The distance-only orientation heuristic can otherwise
    # turn the board (and edge leaves) vertical, forcing the router to draw a
    # dense comb of dog-legs. This rule is geometry-local so Native and
    # Interactive plans receive the same orientation without renderer hints.
    high_fanout = {name for name, endpoints in incident.items() if len(endpoints) >= 8}
    for name in high_fanout:
        orientations[name] = "h"
        center = pos[name]
        vertical_targets = [
            endpoint for endpoint in incident[name] if abs(pos[endpoint.other.lower()].y - center.y) > _EPS
        ]
        if not vertical_targets:
            continue
        signs = [1 if pos[item.other.lower()].y > center.y else -1 for item in vertical_targets]
        dominant_sign = 1 if signs.count(1) >= signs.count(-1) else -1
        for endpoint in vertical_targets:
            other = endpoint.other.lower()
            dy = pos[other].y - center.y
            if (1 if dy > 0 else -1) != dominant_sign:
                continue
            # Direct outgoing feeder terminals are rank-aligned horizontal
            # buses. Limit propagation to low-degree neighbours so a dense
            # downstream board still decides its own policy above.
            if len(incident.get(other, ())) <= 2:
                orientations[other] = "h"

    # Load-bearing buses stay horizontal so their conventional terminal
    # glyphs can use a single downward stem. This is a shared geometry policy,
    # not a renderer-specific orientation hint.
    for bus_id, orientation in (bus_orientation_overrides or {}).items():
        key = str(bus_id).lower()
        if key not in orientations:
            raise KeyError(f"bus orientation override references unknown bus {bus_id!r}")
        if orientation not in {"h", "v"}:
            raise ValueError(f"invalid bus orientation override for {bus_id!r}: {orientation!r}")
        orientations[key] = orientation
    # A dense bus needs visible terminal ports, not merely mathematically
    # distinct coordinates. Keep a modest floor for the bus-side spacing;
    # the half-length map supplied by the SLD builders reserves enough bar
    # length for this same contract. Small caller-supplied bars still cap the
    # spacing in ``_slot_offsets`` rather than escaping the busbar.
    effective_port_spacing = max(port_spacing, 24.0)
    port_maps: dict[str, dict[str, BusPort]] = {}
    for name in sorted(pos):
        port_maps[name] = _build_ports(
            bus_id=name,
            orientation=orientations[name],
            center=pos[name],
            half_length=half_length_for(name),
            incident=incident[name],
            positions=pos,
            spacing=effective_port_spacing,
        )

    lanes = _lane_index(edge_inputs, positions=pos, orientations=orientations)
    # Twelve world units is enough for parallel line metadata but too tight
    # when a dense rank contains several unrelated branches. Give every
    # corridor a visible separation; callers can still request a larger value.
    # 72 world units survives the finite-page fit as a real visual lane rather
    # than a one-pixel hairline, while remaining small relative to the 400-unit
    # topology grid used by the deterministic layout.
    effective_lane_spacing = max(lane_spacing, 72.0)
    corridor_offsets = _corridor_offsets(
        edge_inputs,
        positions=pos,
        orientations=orientations,
        spacing=effective_lane_spacing,
    )
    routes: list[RouteGeometry] = []
    prior_routes: list[tuple[Point, ...]] = []
    for edge in edge_inputs:
        src_key, dst_key = edge.src.lower(), edge.dst.lower()
        src_port = port_maps[src_key][edge.edge_id]
        dst_port = port_maps[dst_key][edge.edge_id]
        lane = lanes[edge.edge_id]
        preferred_offset = corridor_offsets[edge.edge_id]
        source_point = pos[src_key]
        target_point = pos[dst_key]
        local_lead_length = _local_lead_length(
            source_point,
            target_point,
            lead_length,
            source_orientation=orientations[src_key],
        )
        travel = (
            target_point.y - source_point.y
            if orientations[src_key] == "h"
            else target_point.x - source_point.x
        )
        # A retry must not send a feeder back through the bus it is leaving.
        # That looked like a crossing/overlap in dense radial networks: when
        # the first positive corridor was occupied, the old +/- retry could
        # choose a negative offset and route a downward feeder above its
        # source bus. Keep retries on the travel side of the source bus; the
        # retry range still provides deterministic extra tracks when the
        # preferred lane is occupied.
        travel_sign = 1.0 if travel >= 0.0 else -1.0
        candidates: list[tuple[tuple[int, int, float, float, float], tuple[Point, ...]]] = []

        # Dedicated ports that are already aligned on their perpendicular
        # axis need no routing corridor at all.  Dense switchboards can have
        # more feeders than the corridor retry window; without this candidate
        # the middle feeders were forced into artificial dog-legs even though
        # their source and destination ports formed a collision-free straight
        # drop.  Keep the generic corridor search below for every non-aligned
        # or obstructed branch.
        direct = _simplify((src_port.point, dst_port.point))
        direct_is_valid = len(direct) >= 2 and _route_is_valid_for_axes(
            direct,
            source_orientation=orientations[src_key],
            target_orientation=orientations[dst_key],
        )
        semantic_switch_drop = (
            src_key in high_fanout
            and orientations[src_key] == "h"
            and orientations[dst_key] == "h"
            and abs(src_port.point.x - dst_port.point.x) <= _EPS
        )
        if direct_is_valid and semantic_switch_drop:
            points = direct
            prior_routes.append(points)
            routes.append(
                RouteGeometry(
                    edge_id=edge.edge_id,
                    src=edge.src,
                    dst=edge.dst,
                    points=points,
                    src_port=src_port,
                    dst_port=dst_port,
                    lane=lane,
                )
            )
            continue
        if direct_is_valid:
            direct_collisions = _route_collision_count(
                direct,
                prior_routes,
                overlap_weight=1000,
            )
            direct_length = sum(
                abs(right.x - left.x) + abs(right.y - left.y) for left, right in zip(direct, direct[1:])
            )
            candidates.append(
                (
                    (
                        direct_collisions,
                        route_axis_backtrack_count(direct),
                        direct_length,
                        0.0,
                        0.0,
                    ),
                    direct,
                )
            )

        for attempt in range(13):
            offsets = (
                (preferred_offset,)
                if attempt == 0
                else (
                    preferred_offset + attempt * effective_lane_spacing,
                    preferred_offset - attempt * effective_lane_spacing,
                )
            )
            for offset in offsets:
                if abs(travel) > _EPS and offset * travel_sign < -_EPS:
                    continue
                candidate = _route_between(
                    edge=edge,
                    src_port=src_port,
                    dst_port=dst_port,
                    src_orientation=orientations[src_key],
                    lane=lane,
                    lead_length=local_lead_length,
                    lane_spacing=effective_lane_spacing,
                    corridor_offset=offset,
                )
                if not _route_is_valid_for_axes(
                    candidate,
                    source_orientation=orientations[src_key],
                    target_orientation=orientations[dst_key],
                ):
                    continue
                collisions = _route_collision_count(
                    candidate,
                    prior_routes,
                    # A visible shared-length cable is worse than a single
                    # orthogonal crossing. Prefer a detour that separates
                    # parallel routes, then let the length tie-breaker keep
                    # the result compact.
                    overlap_weight=1000,
                )
                length = sum(
                    abs(right.x - left.x) + abs(right.y - left.y)
                    for left, right in zip(candidate, candidate[1:])
                )
                candidates.append(
                    (
                        (
                            collisions,
                            route_axis_backtrack_count(candidate),
                            length,
                            abs(offset - preferred_offset),
                            offset,
                        ),
                        candidate,
                    )
                )
        if not candidates:
            raise ValueError(f"unable to route canonical SLD edge {edge.edge_id!r}")
        _score, points = min(candidates, key=lambda item: item[0])
        prior_routes.append(points)
        routes.append(
            RouteGeometry(
                edge_id=edge.edge_id,
                src=edge.src,
                dst=edge.dst,
                points=points,
                src_port=src_port,
                dst_port=dst_port,
                lane=lane,
            )
        )

    buses = tuple(
        BusGeometry(
            bus_id=name,
            center=pos[name],
            orientation=orientations[name],
            half_length=half_length_for(name),
            ports=tuple(sorted(port_maps[name].values(), key=lambda port: port.edge_id)),
        )
        for name in sorted(pos)
    )
    geometry = CanonicalSLDGeometry(buses=buses, routes=tuple(routes))
    violations = validate_canonical_geometry(geometry)
    if violations:
        raise ValueError("invalid canonical SLD geometry: " + ", ".join(violations))
    return geometry


def canonical_sld_geometry(
    sld: SLDModel,
    *,
    positions: dict[str, tuple[float, float]] | None = None,
    **kwargs: Any,
) -> CanonicalSLDGeometry:
    """Build geometry for the interactive/reporting SLD view model."""
    selected = positions or {node.id: (node.x, node.y) for node in sld.nodes}
    orientation_overrides: dict[str, Axis] = {
        str(node.id).lower(): "h"
        for node in sld.nodes
        if node.loads
    }
    orientation_overrides.update(kwargs.pop("bus_orientation_overrides", {}) or {})
    return canonical_geometry(
        positions=selected,
        edges=((edge.id, edge.src, edge.dst) for edge in sld.edges),
        bus_orientation_overrides=orientation_overrides,
        **kwargs,
    )


def canonical_inline_geometry(
    net: InlineNetwork,
    *,
    positions: dict[str, tuple[float, float]] | None = None,
    **kwargs: Any,
) -> CanonicalSLDGeometry:
    """Build the same geometry contract directly from an inline Case network.

    Lines, two-winding transformers, and bus-to-bus switches are all branch
    topology.  Omitting switches here lets the supposedly shared native path
    silently diverge from the electrical Case, so all three are canonical.
    """
    if positions is None:
        from cept.domain.sld.engineering_layout import hierarchical_layout

        positions = hierarchical_layout(net)
    orientation_overrides: dict[str, Axis] = {
        str(load.bus).lower(): "h"
        for load in net.loads
    }
    orientation_overrides.update(kwargs.pop("bus_orientation_overrides", {}) or {})
    edges = (
        [(line.name, line.from_bus, line.to_bus) for line in net.lines]
        + [(transformer.name, transformer.hv_bus, transformer.lv_bus) for transformer in net.transformers]
        + [(switch.name, switch.bus1, switch.bus2) for switch in net.switches]
    )
    return canonical_geometry(
        positions=positions,
        edges=edges,
        bus_orientation_overrides=orientation_overrides,
        **kwargs,
    )


def inline_device_placement(route: RouteGeometry) -> InlineDevicePlacement:
    """Place transformer/regulator/switch symbols inline on the longest segment."""
    if len(route.points) < 2:
        raise ValueError("cannot place an inline device on an empty route")
    segments = list(zip(route.points, route.points[1:]))
    index = max(
        range(len(segments)),
        key=lambda item: (
            abs(segments[item][1].x - segments[item][0].x) + abs(segments[item][1].y - segments[item][0].y),
            -item,
        ),
    )
    left, right = segments[index]
    axis: Axis = "h" if abs(left.y - right.y) <= _EPS else "v"
    return InlineDevicePlacement(
        edge_id=route.edge_id,
        center=Point((left.x + right.x) / 2.0, (left.y + right.y) / 2.0),
        axis=axis,
        segment_index=index,
    )


def _network_centroid(geometry: CanonicalSLDGeometry) -> Point:
    if not geometry.buses:
        return Point(0.0, 0.0)
    return Point(
        sum(bus.center.x for bus in geometry.buses) / len(geometry.buses),
        sum(bus.center.y for bus in geometry.buses) / len(geometry.buses),
    )


def _stem_collides_with_route(
    stem_start: Point,
    stem_end: Point,
    route_start: Point,
    route_end: Point,
    *,
    min_sep: float = 8.0,
) -> bool:
    s_vert = abs(stem_start.x - stem_end.x) <= _EPS
    r_vert = abs(route_start.x - route_end.x) <= _EPS
    s_horiz = abs(stem_start.y - stem_end.y) <= _EPS
    r_horiz = abs(route_start.y - route_end.y) <= _EPS
    if s_vert and r_vert:
        if abs(stem_start.x - route_start.x) < min_sep:
            sy_min, sy_max = min(stem_start.y, stem_end.y), max(stem_start.y, stem_end.y)
            ry_min, ry_max = min(route_start.y, route_end.y), max(route_start.y, route_end.y)
            if min(sy_max, ry_max) - max(sy_min, ry_min) > _EPS:
                return True
    elif s_horiz and r_horiz:
        if abs(stem_start.y - route_start.y) < min_sep:
            sx_min, sx_max = min(stem_start.x, stem_end.x), max(stem_start.x, stem_end.x)
            rx_min, rx_max = min(route_start.x, route_end.x), max(route_start.x, route_end.x)
            if min(sx_max, rx_max) - max(sx_min, rx_min) > _EPS:
                return True
    elif s_vert and r_horiz:
        sx = stem_start.x
        sy_min, sy_max = min(stem_start.y, stem_end.y), max(stem_start.y, stem_end.y)
        ry = route_start.y
        rx_min, rx_max = min(route_start.x, route_end.x), max(route_start.x, route_end.x)
        if rx_min + _EPS <= sx <= rx_max - _EPS and sy_min + _EPS <= ry <= sy_max - _EPS:
            return True
    elif s_horiz and r_vert:
        sy = stem_start.y
        sx_min, sx_max = min(stem_start.x, stem_end.x), max(stem_start.x, stem_end.x)
        rx = route_start.x
        ry_min, ry_max = min(route_start.y, route_end.y), max(route_start.y, route_end.y)
        if ry_min + _EPS <= sy <= ry_max - _EPS and sx_min + _EPS <= rx <= sx_max - _EPS:
            return True
    return False


def place_terminal_devices(
    geometry: CanonicalSLDGeometry,
    *,
    bus_id: str,
    device_ids: Sequence[str],
    device_kinds: dict[str, str] | None = None,
    clearance: float = 64.0,
    port_spacing: float = 8.0,
) -> tuple[TerminalPlacement, ...]:
    if clearance <= 0 or port_spacing <= 0:
        raise ValueError("terminal placement dimensions must be > 0")
    bus = geometry.bus(bus_id)
    primary_dirs: tuple[Direction, ...] = ("up", "down") if bus.orientation == "h" else ("left", "right")
    counts = {d: 0 for d in ("up", "down", "left", "right")}
    for port in bus.ports:
        counts[port.direction] += 1

    centroid = _network_centroid(geometry)
    away_x: Direction = "left" if bus.center.x <= centroid.x else "right"
    away_y: Direction = "up" if bus.center.y <= centroid.y else "down"

    used_offsets: dict[Direction, set[float]] = {d: set() for d in ("up", "down", "left", "right")}
    for port in bus.ports:
        offset = (port.point.x - bus.center.x) if bus.orientation == "h" else (port.point.y - bus.center.y)
        used_offsets[port.direction].add(offset)

    placements: list[TerminalPlacement] = []
    # Keep a visible terminal/branch gap without forcing every small busbar to
    # grow to page-sized dimensions.  The stem and glyph themselves are
    # already separated by ``clearance``; this is only the route-collision
    # envelope used while choosing the bus-side slot.
    min_sep = max(12.0, min(24.0, clearance * 0.22))
    device_kinds = device_kinds or {}
    for device_id in sorted(device_ids):
        best = None
        semantic_direction: Direction | None = None
        if bus.orientation == "h":
            kind = str(device_kinds.get(device_id, "")).lower()
            if kind == "grid":
                semantic_direction = "up"
            elif kind in {"load", "shunt"}:
                semantic_direction = "down"
        # A terminal connection must always enter its busbar perpendicularly.
        # The old secondary-axis fallback made a crowded horizontal bar place a
        # load at its end with a parallel stem, which looked like the load had
        # come out of a branch and failed the SVG/native connection audit.
        direction_groups: tuple[tuple[Direction, ...], ...]
        if semantic_direction is None:
            direction_groups = (primary_dirs,)
        else:
            fallback = tuple(direction for direction in primary_dirs if direction != semantic_direction)
            direction_groups = ((semantic_direction,), fallback)
        for dir_group in direction_groups:
            if not dir_group:
                continue
            candidate_slots = []
            for direction in dir_group:
                nx, ny = _CARDINAL[direction]
                away = away_y if direction in ("up", "down") else away_x
                max_offset = max(0.0, bus.half_length - min(2.0, port_spacing * 0.25))
                offsets: list[float] = []
                k = 0
                while k < 32:
                    sign = -1.0 if k % 2 == 1 else 1.0
                    mag = ((k + 1) // 2) * port_spacing
                    offset = sign * mag if k > 0 else 0.0
                    k += 1
                    # Keep the bus-side anchor on the actual busbar. If a
                    # dense bus needs more room, the caller must enlarge the
                    # canonical bar rather than silently detaching a device.
                    if abs(offset) > max_offset + _EPS:
                        continue
                    if any(abs(offset - u) < port_spacing * 0.5 for u in used_offsets[direction]):
                        continue
                    offsets.append(offset)

                # A short leaf bus may have its centre slot occupied by an
                # electrical branch.  Keep the fallback terminal on the
                # visible bar by offering its two edge slots even when a full
                # terminal pitch does not fit; this is preferable to placing
                # a stem through an unrelated route or reusing the branch
                # port.
                for edge_offset in (-max_offset, max_offset):
                    if any(abs(edge_offset - offset) <= _EPS for offset in offsets):
                        continue
                    if any(abs(edge_offset - u) < port_spacing * 0.5 for u in used_offsets[direction]):
                        continue
                    offsets.append(edge_offset)

                for offset in offsets:

                    if bus.orientation == "h":
                        if direction in ("left", "right"):
                            bus_port = Point(
                                bus.center.x + nx * bus.half_length * 0.85, bus.center.y + offset
                            )
                        else:
                            bus_port = Point(bus.center.x + offset, bus.center.y)
                    else:
                        if direction in ("up", "down"):
                            bus_port = Point(
                                bus.center.x + offset, bus.center.y + ny * bus.half_length * 0.85
                            )
                        else:
                            bus_port = Point(bus.center.x, bus.center.y + offset)

                    center = Point(bus_port.x + nx * clearance, bus_port.y + ny * clearance)
                    hits = 0
                    for route in geometry.routes:
                        for p1, p2 in zip(route.points, route.points[1:]):
                            if _stem_collides_with_route(bus_port, center, p1, p2, min_sep=min_sep):
                                hits += 1

                    score = (
                        hits,
                        counts[direction],
                        0 if direction == away else 1,
                        _stable_bit(bus.bus_id.lower(), device_id, direction),
                        -abs(offset),
                    )
                    candidate_slots.append((score, direction, offset, bus_port, center))

            clean_slots = [s for s in candidate_slots if s[0][0] == 0]
            if clean_slots:
                # Prefer a collision-free perpendicular side even when the
                # semantic default (for example, a load below a horizontal
                # bus) is unavailable.  Keeping a colliding default here
                # prevented the fallback side from ever being considered and
                # let unrelated branch routes pass through terminal stems.
                best = min(clean_slots, key=lambda item: item[0])
                break
            if candidate_slots:
                candidate_best = min(candidate_slots, key=lambda item: item[0])
                if best is None or candidate_best[0] < best[0]:
                    best = candidate_best

        if best is None:
            continue
        _score, direction, offset, bus_port, center = best
        used_offsets[direction].add(offset)
        counts[direction] += 1
        placements.append(
            TerminalPlacement(
                device_id=device_id,
                bus_id=bus.bus_id,
                bus_port=bus_port,
                center=center,
                direction=direction,
                route=(bus_port, center),
            )
        )
    return tuple(placements)


def terminal_bus_half_lengths(
    bus_ids: Iterable[str],
    edges: Iterable[tuple[str, str]],
    terminal_counts: dict[str, int],
    *,
    minimum: float = 19.0,
    terminal_minimum: float = 64.0,
    port_spacing: float = 24.0,
    edge_margin: float = 2.0,
) -> dict[str, float]:
    """Size busbars so every declared branch/terminal has a legal port slot.

    Busbars used to have one fixed short length. On a high-degree bus the
    terminal fallback then escaped the bar and visually attached a load to a
    nearby branch. The renderer-neutral plan can safely lengthen only the
    affected bars; topology and branch routes remain unchanged.
    """
    if minimum <= 0.0 or terminal_minimum <= 0.0 or port_spacing <= 0.0 or edge_margin < 0.0:
        raise ValueError("busbar sizing dimensions must be valid")
    terminal_counts_normalized = {
        str(bus_id).lower(): max(0, int(count)) for bus_id, count in terminal_counts.items()
    }
    degree = {str(bus_id).lower(): 0 for bus_id in bus_ids}
    for source, target in edges:
        degree.setdefault(str(source).lower(), 0)
        degree.setdefault(str(target).lower(), 0)
        degree[str(source).lower()] += 1
        degree[str(target).lower()] += 1
    result: dict[str, float] = {}
    for bus_id, count in degree.items():
        terminals = terminal_counts_normalized.get(bus_id, 0)
        slots = count + terminals
        # Reserve one deterministic port pitch across the bar without making
        # every connected leaf look like a main switchboard.  A one-branch,
        # one-terminal leaf needs two ports and therefore exactly half a pitch
        # on either side of its centre; at the canonical 48-unit feeder pitch
        # that yields a 48-unit bar which touches, but does not overlap, the
        # next leaf busbar.  High-degree buses still grow with their true slot
        # count and retain a small edge margin.
        if slots <= 1:
            # A connected bus still reserves half a canonical pitch so a
            # renderer that knows about one terminal and one that does not
            # produce identical branch geometry for the same topology.
            required = max(minimum, port_spacing / 2.0) if count else minimum
        else:
            required = ((slots - 1) * port_spacing) / 2.0
            if slots >= 3:
                required += edge_margin
        # The historical terminal minimum is useful only when several terminal
        # devices share a bus and need a visibly distinct attachment zone.  It
        # must not enlarge ordinary one-load feeder leaves.
        if count >= 3 or terminals >= 3:
            required = max(required, terminal_minimum)
        result[bus_id] = max(minimum, required)
    return result


__all__ = [
    "Axis",
    "BusGeometry",
    "BusPort",
    "CANONICAL_RENDER_PORT_SPACING",
    "CANONICAL_TERMINAL_CLEARANCE",
    "CANONICAL_TERMINAL_CLEARANCE_MAX",
    "CanonicalSLDGeometry",
    "Direction",
    "InlineDevicePlacement",
    "Point",
    "RouteGeometry",
    "TerminalPlacement",
    "ViewportTransform",
    "canonical_geometry",
    "canonical_inline_geometry",
    "canonical_sld_geometry",
    "inline_device_placement",
    "place_terminal_devices",
    "route_axis_backtrack_count",
    "terminal_bus_half_lengths",
    "validate_canonical_geometry",
    "validate_route_geometry",
]
