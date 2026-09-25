"""Interactive report runtime bridge for canonical SLD contracts.

The legacy builder still owns solved-value payloads and tooltips.  This bridge
replaces geometry/presentation-critical metadata with the renderer-neutral
physical plan; browser code never chooses a second topology layout.
"""

from __future__ import annotations

import math
from typing import Any

from cept.domain.sld.engineering_layout import orient_finite_page_layout, sld_topology_layout
from cept.reporting.sld import (
    _FLOW_ZERO_TOLERANCE_KW,
    _render_bus_positions,
    build_sld_option as _legacy_build_sld_option,
)
from cept.reporting.sld_plan import (
    InteractiveSLDPlan,
    build_interactive_sld_plan,
    virtual_grid_attachments,
)
from cept.schema.sld import SLDEdge, SLDModel
from cept.domain.sld.geometry import CANONICAL_TERMINAL_CLEARANCE
from cept.domain.sld.geometry_receipt import geometry_receipt
from cept.domain.sld.layout_contract import canonical_sld_edge_id, solved_layout_plan

_INLINE_SYMBOL = {
    "transformer": "path://M4,16 A12,12 0 1,0 28,16 A12,12 0 1,0 4,16 M20,16 A12,12 0 1,0 44,16 A12,12 0 1,0 20,16",
    "regulator": "path://M4,16 A12,12 0 1,0 28,16 A12,12 0 1,0 4,16 M20,16 A12,12 0 1,0 44,16 A12,12 0 1,0 20,16",
    "switch": "path://M2,16 L12,16 M20,16 L30,16 M12,16 L20,8",
    # T-039: diamond with a poles divider for VSC stations; the ECharts
    # path parser supports M/L/Z like the symbols above.
    "converter": "path://M16,5 L27,16 L16,27 L5,16 Z M16,5 L16,27",
}
_INLINE_COLOR = {
    "transformer": "#b45309",
    "regulator": "#b45309",
    "switch": "#047857",
    "converter": "#0d9488",
}

_MIN_VISUAL_BUS_GAP = 72.0
_MAX_DENSE_ASPECT = 12.0
_DENSE_NETWORK_MIN_BUSES = 25
_LABEL_SOFT_LIMIT = 24
_DENSE_LABEL_SOFT_LIMIT = 14
_DENSE_LABEL_MIN_BUS_GAP = 16.0


def _bus_nodes(nodes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(node.get("name", "")).lower(): node for node in nodes if node.get("category") == "bus"}


def _uses_dense_label_policy(nodes: list[dict[str, Any]]) -> bool:
    """Select dense labels from page density, not bus count alone.

    A normal IEEE13-sized feeder has enough separation for every bus name. A
    compact switchboard can still have fewer than 25 buses, so extremely tight
    coordinates remain on the landmarks-plus-hover policy.
    """
    buses = list(_bus_nodes(nodes).values())
    if len(buses) >= _DENSE_NETWORK_MIN_BUSES:
        return True
    if len(buses) < 13:
        return False
    positions = [
        (float(node["x"]), float(node["y"]))
        for node in buses
        if isinstance(node.get("x"), (int, float)) and isinstance(node.get("y"), (int, float))
    ]
    if len(positions) < 2:
        return False
    minimum_gap = min(
        math.hypot(x - ox, y - oy)
        for index, (x, y) in enumerate(positions)
        for ox, oy in positions[index + 1 :]
    )
    return minimum_gap < _DENSE_LABEL_MIN_BUS_GAP


def _device_nodes(nodes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(node.get("name", "")): node
        for node in nodes
        if node.get("is_device") and not node.get("is_bend") and not node.get("is_flow_arrow")
    }


def _terminal_symbol_rotation(kind: str, direction: str) -> float:
    """Return the quarter-turn needed to align a terminal glyph to its stem."""
    if kind == "capacitor" and direction in {"left", "right"}:
        return 90.0
    if kind == "reactor" and direction in {"up", "down"}:
        return 90.0
    return 0.0


def _compact_identifier(value: str, limit: int = _LABEL_SOFT_LIMIT) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    left = max(7, (limit - 1) // 2)
    right = max(5, limit - left - 1)
    return f"{text[:left]}…{text[-right:]}"


def _resolve_bus_label_collisions(
    nodes: list[dict[str, Any]],
    links: list[dict[str, Any]] | None = None,
    *,
    hide_blocked: bool,
) -> None:
    """Place bus labels away from bars/routes, hiding only dense leftovers.

    Medium feeders should keep all labels and move a blocked label to another
    side of its bus. Dense overviews keep landmark labels and full hover
    identity, but may hide a lower-priority label when no side is available.
    Deconflicting presentation metadata here keeps every renderer consistent.
    """
    buses = [
        node
        for node in nodes
        if node.get("category") == "bus"
        and node.get("label_default_visible") is not False
        and str(node.get("label_v") or "")
    ]
    if len(buses) < 2:
        return
    drawable = [
        node
        for node in nodes
        if not node.get("is_bend")
        and not node.get("is_flow_arrow")
        and isinstance(node.get("x"), (int, float))
        and isinstance(node.get("y"), (int, float))
    ]
    xs = [float(node.get("x", 0.0)) for node in drawable]
    ys = [float(node.get("y", 0.0)) for node in drawable]
    world_per_px = max(
        max(max(xs) - min(xs), 120.0) / 1100.0,
        max(max(ys) - min(ys), 80.0) / 700.0,
    )
    text_scale = min(4.5, max(1.0, world_per_px * 0.98))
    glyph_scale = min(6.0, max(1.0, world_per_px * 1.05))
    xs_by_y: dict[float, list[float]] = {}
    for node in nodes:
        if not node.get("is_device") or node.get("is_flow_arrow") or node.get("is_bend"):
            continue
        if not isinstance(node.get("x"), (int, float)) or not isinstance(node.get("y"), (int, float)):
            continue
        xs_by_y.setdefault(round(float(node["y"]), 1), []).append(float(node["x"]))
    min_dx = min(
        (right - left for values in xs_by_y.values() for left, right in zip(sorted(values), sorted(values)[1:]) if right - left > 1.0),
        default=float("inf"),
    )
    if math.isfinite(min_dx):
        minimum_glyph = 0.65 if len(nodes) >= 60 else 1.0
        glyph_scale = min(glyph_scale, max(minimum_glyph, min_dx / 56.0))
    bus_scale = min(3.0, max(1.0, 0.55 + glyph_scale * 0.55))

    def overlaps(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> bool:
        return left[0] < right[2] and right[0] < left[2] and left[1] < right[3] and right[1] < left[3]

    def route_hits_box(route: Any, box: tuple[float, float, float, float]) -> bool:
        points = list(route or [])
        for left, right in zip(points, points[1:]):
            try:
                x1, y1 = float(left[0]), float(left[1])
                x2, y2 = float(right[0]), float(right[1])
            except (IndexError, TypeError, ValueError):
                continue
            if abs(x1 - x2) <= 1e-9:
                if box[0] <= x1 <= box[2] and max(min(y1, y2), box[1]) < min(max(y1, y2), box[3]):
                    return True
            elif abs(y1 - y2) <= 1e-9:
                if box[1] <= y1 <= box[3] and max(min(x1, x2), box[0]) < min(max(x1, x2), box[2]):
                    return True
        return False

    def label_box(
        node: dict[str, Any],
        position: str,
        offset_x: float = 0.0,
        offset_y: float = 0.0,
    ) -> tuple[float, float, float, float]:
        x = float(node.get("x", 0.0)) + offset_x
        y = float(node.get("y", 0.0)) + offset_y
        half = float(node.get("canonical_half_length") or 22.0) * bus_scale
        text = str(node.get("label_v") or "")
        lines = text.splitlines() or [text]
        font = 11.5 * text_scale
        line_height = font * 1.15
        label_width = max(len(line) for line in lines) * font * 0.62
        label_height = len(lines) * line_height
        if position == "left":
            return (
                x - half - 8.0 * text_scale - label_width,
                y - label_height / 2.0,
                x - half - 8.0 * text_scale,
                y + label_height / 2.0,
            )
        if position == "right":
            return (
                x + half + 8.0 * text_scale,
                y - label_height / 2.0,
                x + half + 8.0 * text_scale + label_width,
                y + label_height / 2.0,
            )
        if position == "bottom":
            return (
                x - label_width / 2.0,
                y + half + 6.0 * text_scale,
                x + label_width / 2.0,
                y + half + 6.0 * text_scale + label_height,
            )
        return (
            x - label_width / 2.0,
            y - half - 6.0 * text_scale - label_height,
            x + label_width / 2.0,
            y - half - 6.0 * text_scale,
        )

    bars: dict[str, tuple[float, float, float, float]] = {}
    labels: list[tuple[int, str, dict[str, Any], tuple[float, float, float, float]]] = []
    for node in buses:
        name = str(node.get("name") or "")
        x, y = float(node.get("x", 0.0)), float(node.get("y", 0.0))
        half = float(node.get("canonical_half_length") or 22.0) * bus_scale
        vertical = node.get("bus_orientation") == "vertical"
        thick = (6.5 if node.get("kind") == "substation" else 4.5) * min(bus_scale, 2.0)
        bars[name.lower()] = (
            x - thick / 2.0,
            y - half,
            x + thick / 2.0,
            y + half,
        ) if vertical else (
            x - half,
            y - thick / 2.0,
            x + half,
            y + thick / 2.0,
        )
        position = str((node.get("label") or {}).get("position") or "top")
        box = label_box(node, position)
        labels.append((int(node.get("label_priority") or 0), name.lower(), node, box))

    accepted: list[tuple[float, float, float, float]] = []
    for _priority, name, node, original_box in sorted(labels, key=lambda item: (-item[0], item[1])):
        current = str((node.get("label") or {}).get("position") or "top")
        candidates: list[tuple[str, float, float]] = [
            (current, 0.0, 0.0),
            *[(position, 0.0, 0.0) for position in ("top", "right", "bottom", "left") if position != current],
        ]
        for position in ("top", "right", "bottom", "left"):
            offsets = (
                ((-48.0, 0.0), (48.0, 0.0), (-72.0, 0.0), (72.0, 0.0))
                if position in {"top", "bottom"}
                else ((0.0, -32.0), (0.0, 32.0), (0.0, -48.0), (0.0, 48.0))
            )
            candidates.extend(
                (position, offset_x, offset_y)
                for offset_x, offset_y in offsets
            )
        selected_box: tuple[float, float, float, float] | None = None
        selected_position: str | None = None
        selected_offset = (0.0, 0.0)
        for position, offset_x, offset_y in candidates:
            box = label_box(node, position, offset_x, offset_y)
            blocked = any(overlaps(box, other) for other in accepted)
            if not blocked:
                blocked = any(overlaps(box, other_box) for other_box in bars.values())
            if not blocked and links:
                blocked = any(route_hits_box(link.get("route_points"), box) for link in links)
            if not blocked:
                selected_box, selected_position = box, position
                selected_offset = (offset_x, offset_y)
                break
        if selected_box is None:
            if hide_blocked:
                node["label_default_visible"] = False
                node.setdefault("label", {})["show"] = False
            else:
                # Keep the name visible on a medium feeder even if every side
                # is occupied; this is still more useful than an unlabeled bus
                # and remains fully available in the hover payload.
                selected_box, selected_position = original_box, current
        if selected_box is not None:
            label = node.setdefault("label", {})
            label["position"] = selected_position
            if selected_offset != (0.0, 0.0):
                label["offset"] = {"x": selected_offset[0], "y": selected_offset[1]}
            else:
                label.pop("offset", None)
            accepted.append(selected_box)


def _minimum_gap(positions: dict[str, tuple[float, float]]) -> float:
    values = list(positions.values())
    if len(values) < 2:
        return float("inf")
    best = float("inf")
    for index, (x, y) in enumerate(values):
        for ox, oy in values[index + 1 :]:
            best = min(best, math.hypot(ox - x, oy - y))
    return best


def _visual_positions(sld: SLDModel) -> dict[str, tuple[float, float]]:
    """Compatibility helper; dense production positions are canonical."""
    if len(sld.nodes) >= _DENSE_NETWORK_MIN_BUSES:
        return dict(solved_layout_plan(sld).positions)

    raw = _render_bus_positions(sld, _SX=2.0, _SY=2.6)
    if len(raw) < 2:
        return raw
    xs = [point[0] for point in raw.values()]
    ys = [point[1] for point in raw.values()]
    span_x = max(xs) - min(xs)
    span_y = max(ys) - min(ys)
    short_span = max(min(span_x, span_y), 1.0)
    aspect = max(span_x, span_y) / short_span
    dense = _minimum_gap(raw) < _MIN_VISUAL_BUS_GAP
    strip = len(raw) > 20 and aspect > _MAX_DENSE_ASPECT
    if not dense and not strip:
        return raw
    placed = sld_topology_layout(
        nodes=sld.nodes,
        edges=sld.edges,
        source_ids={node.id for node in sld.nodes if node.kind == "substation"},
    )
    selected = {name: placed[name] for name in raw if name in placed} or placed
    return orient_finite_page_layout(selected, minimum_nodes=_DENSE_NETWORK_MIN_BUSES, tall_aspect=1.25)


def _line_style_color(link: dict[str, Any]) -> str:
    line_style = link.get("lineStyle")
    if isinstance(line_style, dict):
        color = line_style.get("color")
        if isinstance(color, str) and color:
            return color
    return "#64748b"


def _primary_edge_link(links: list[dict[str, Any]], edge_id: str) -> dict[str, Any] | None:
    canonical_id = canonical_sld_edge_id(edge_id)
    matches = [
        link
        for link in links
        if link.get("edge_id") and canonical_sld_edge_id(link.get("edge_id")) == canonical_id
    ]
    if not matches:
        return None
    return max(
        matches,
        key=lambda link: (bool(link.get("label_len")), bool(link.get("label_flow")), len(link.get("route_points") or [])),
    )


def _flow_route(edge: SLDEdge, points: tuple[Any, ...]) -> list[tuple[float, float]]:
    route = [(float(point.x), float(point.y)) for point in points]
    if edge.p_kw < 0:
        route.reverse()
    return route


def _add_inline_symbol(
    *, edge: SLDEdge, plan: InteractiveSLDPlan, nodes: list[dict[str, Any]]
) -> tuple[float, float] | None:
    branch = plan.branch(edge.id)
    if branch.inline_center is None or branch.inline_axis is None:
        return None
    color = "#dc2626" if edge.status == "open" else _INLINE_COLOR.get(edge.kind, "#475569")
    nodes.append(
        {
            "name": f"__inline_{edge.id}",
            "x": branch.inline_center.x,
            "y": branch.inline_center.y,
            "symbol": _INLINE_SYMBOL.get(edge.kind, _INLINE_SYMBOL["switch"]),
            "symbolSize": 18 if edge.kind in {"transformer", "regulator"} else 15,
            "rotation": 90 if branch.inline_axis == "v" else 0,
            "is_device": True,
            "is_inline_device": True,
            "sld_device_kind": edge.kind,
            "canonical_edge_id": edge.id,
            "itemStyle": {
                "color": color,
                "opacity": 0.45 if edge.status == "open" else 1.0,
                "borderColor": "#334155",
                "borderWidth": 1,
            },
            "label": {"show": False},
            "silent": True,
            "z": 12,
        }
    )
    return (branch.inline_center.x, branch.inline_center.y)


def _candidate_arrow_points(route: list[tuple[float, float]]) -> list[tuple[float, float, float, float, float]]:
    candidates: list[tuple[float, float, float, float, float]] = []
    for left, right in zip(route, route[1:]):
        length = abs(right[0] - left[0]) + abs(right[1] - left[1])
        if length <= 1e-9:
            continue
        angle = math.degrees(math.atan2(right[1] - left[1], right[0] - left[0]))
        for fraction in (0.5, 0.25, 0.75):
            x = left[0] + (right[0] - left[0]) * fraction
            y = left[1] + (right[1] - left[1]) * fraction
            candidates.append((length, x, y, angle, length))
    return candidates


def _add_safe_flow_arrow(
    edge: SLDEdge,
    nodes: list[dict[str, Any]],
    route: list[tuple[float, float]],
    *,
    color: str,
    inline_center: tuple[float, float] | None,
) -> None:
    if edge.status == "open" or abs(float(edge.p_kw)) <= _FLOW_ZERO_TOLERANCE_KW or len(route) < 2:
        return
    candidates = _candidate_arrow_points(route)
    if not candidates:
        return

    def score(candidate: tuple[float, float, float, float, float]) -> tuple[float, float, float, float]:
        segment_length, x, y, _angle, _raw_length = candidate
        clearance = float("inf") if inline_center is None else abs(x - inline_center[0]) + abs(y - inline_center[1])
        return (1.0 if clearance >= 22.0 else 0.0, min(clearance, 10_000.0), segment_length, -(abs(x) + abs(y)))

    _segment_length, x, y, angle, raw_length = max(candidates, key=score)
    if inline_center is not None and abs(x - inline_center[0]) + abs(y - inline_center[1]) < 14.0:
        return
    nodes.append(
        {
            "name": f"__flow_arrow_{edge.id}",
            "x": x,
            "y": y,
            "symbol": "arrow",
            "symbolRotate": angle,
            "symbolSize": 11 if raw_length >= 16.0 else 8,
            "is_device": True,
            "is_flow_arrow": True,
            "silent": True,
            "z": 20,
            "itemStyle": {"color": color, "borderColor": "#ffffff", "borderWidth": 1},
            "label": {"show": False},
        }
    )


def _point_to_segment_distance(px: float, py: float, x1: float, y1: float, x2: float, y2: float) -> float:
    dx, dy = x2 - x1, y2 - y1
    l2 = dx * dx + dy * dy
    if l2 <= 1e-9:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / l2))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def _segment_intersects_box(
    x1: float, y1: float, x2: float, y2: float, bx0: float, by0: float, bx1: float, by1: float, margin: float = 6.0
) -> bool:
    min_bx, max_bx = min(bx0, bx1) - margin, max(bx0, bx1) + margin
    min_by, max_by = min(by0, by1) - margin, max(by0, by1) + margin
    if min_bx <= x1 <= max_bx and min_by <= y1 <= max_by:
        return True
    if min_bx <= x2 <= max_bx and min_by <= y2 <= max_by:
        return True
    if max(x1, x2) < min_bx or min(x1, x2) > max_bx or max(y1, y2) < min_by or min(y1, y2) > max_by:
        return False
    cx, cy = (min_bx + max_bx) / 2.0, (min_by + max_by) / 2.0
    return _point_to_segment_distance(cx, cy, x1, y1, x2, y2) < math.hypot(max_bx - min_bx, max_by - min_by) * 0.425


def _bus_label_position(plan: InteractiveSLDPlan, bus_id: str) -> str:
    bus = plan.geometry.bus(bus_id)
    thick, half, cx, cy = 6.0, bus.half_length, bus.center.x, bus.center.y
    if bus.orientation == "h":
        b_left, b_right, b_top, b_bottom = cx - half, cx + half, cy - thick / 2, cy + thick / 2
    else:
        b_left, b_right, b_top, b_bottom = cx - thick / 2, cx + thick / 2, cy - half, cy + half
    boxes = {
        "top": (cx - 35, b_top - 28, cx + 35, b_top),
        "bottom": (cx - 35, b_bottom, cx + 35, b_bottom + 28),
        "left": (b_left - 55, cy - 14, b_left, cy + 14),
        "right": (b_right, cy - 14, b_right + 55, cy + 14),
    }
    scores = {direction: 0.0 for direction in boxes}
    for port in bus.ports:
        direction = {"up": "top", "down": "bottom", "left": "left", "right": "right"}.get(port.direction)
        if direction:
            scores[direction] += 25.0
    for device in plan.devices:
        for direction, box in boxes.items():
            if math.hypot(device.center.x - (box[0] + box[2]) / 2, device.center.y - (box[1] + box[3]) / 2) < 36.0:
                scores[direction] += 60.0
    for branch in plan.branches:
        for p1, p2 in zip(branch.points, branch.points[1:]):
            for direction, box in boxes.items():
                if _segment_intersects_box(p1.x, p1.y, p2.x, p2.y, *box, margin=6.0):
                    scores[direction] += 30.0
    for other in plan.buses:
        if other.bus_id.lower() == bus.bus_id.lower():
            continue
        for direction, box in boxes.items():
            if math.hypot(other.center.x - (box[0] + box[2]) / 2, other.center.y - (box[1] + box[3]) / 2) < 32.0:
                scores[direction] += 50.0
    primary = ("top", "bottom") if bus.orientation == "h" else ("left", "right")
    for direction in primary:
        scores[direction] -= 2.0
    center_x = sum(item.center.x for item in plan.buses) / max(len(plan.buses), 1)
    center_y = sum(item.center.y for item in plan.buses) / max(len(plan.buses), 1)
    away = ("top" if bus.center.y <= center_y else "bottom") if bus.orientation == "h" else ("left" if bus.center.x <= center_x else "right")
    scores[away] -= 1.0
    return min(scores, key=lambda direction: (scores[direction], 0 if direction in primary else 1, 0 if direction == away else 1, direction))


def _apply_bus_geometry(plan: InteractiveSLDPlan, nodes: list[dict[str, Any]]) -> None:
    by_name = _bus_nodes(nodes)
    for bus in plan.buses:
        node = by_name.get(bus.bus_id.lower())
        if node is None:
            continue
        node["x"], node["y"] = bus.center.x, bus.center.y
        horizontal = bus.orientation == "h"
        node["bus_orientation"] = "horizontal" if horizontal else "vertical"
        node["canonical_half_length"] = bus.half_length
        node["sld_visual_role"] = "busbar"
        node["sld_device_kind"] = "bus"
        is_substation = node.get("kind") == "substation"
        if horizontal:
            node["symbolSize"] = [max(46 if is_substation else 40, bus.half_length * 2.0), 8 if is_substation else 6]
        else:
            node["symbolSize"] = [8 if is_substation else 6, max(46 if is_substation else 40, bus.half_length * 2.0)]
        label = node.setdefault("label", {})
        label.update({"position": _bus_label_position(plan, bus.bus_id), "distance": 12, "fontSize": 12, "color": "#172433", "textBorderWidth": 0})
        node["canonical_geometry_v2"] = True


def _add_virtual_grid_terminal_nodes(
    sld: SLDModel,
    plan: InteractiveSLDPlan,
    nodes: list[dict[str, Any]],
    links: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Turn a transport-only GridLink source into a physical grid terminal.

    The OpenDSS adapter retains a source node so the solved SLD remains
    connected and auditable. That node is not a second physical bus in the
    shared geometry. For rendering, replay it as one explicit grid terminal
    attached to the physical bus; this matches the native PF attachment model.
    """
    attachments = virtual_grid_attachments(
        sld,
        allowed_bus_ids=set(plan.physical_bus_ids),
        virtual_source_bus_ids=plan.virtual_source_bus_ids,
    )
    if not attachments:
        return []
    by_name = {str(node.get("name") or "").lower(): node for node in nodes}
    result: list[dict[str, Any]] = []
    for device_id, _bus_id, source_id in attachments:
        source = by_name.get(source_id.lower())
        if source is None:
            raise ValueError(f"canonical Interactive SLD is missing GridLink source node: {source_id}")
        rendered = dict(source)
        rendered.pop("category", None)
        rendered.update(
            {
                "name": device_id,
                "is_device": True,
                "is_inline_device": False,
                "sld_device_kind": "grid",
                "display_name": f"GRID {source_id}",
                "sld_hover_label": f"GRID {source_id}",
                "symbol": "path://M6,6 L26,6 L26,26 L6,26 Z",
                "symbolSize": 20,
                "label": {"show": False, "formatter": "GRID"},
                "silent": True,
                "z": 11,
            }
        )
        result.append(rendered)
        for link in links:
            edge_id = str(link.get("edge_id") or "")
            if not edge_id.casefold().startswith("gridlink."):
                continue
            changed = False
            if str(link.get("source") or "").lower() == source_id.lower():
                link["source"] = device_id
                link["src_bus"] = device_id
                changed = True
            if str(link.get("target") or "").lower() == source_id.lower():
                link["target"] = device_id
                link["dst_bus"] = device_id
                changed = True
            if changed:
                # GridLink is a terminal stem, not a physical branch in the
                # canonical contract. Preserve its provenance separately so
                # the renderer's terminal audit can enforce one connection.
                link["grid_link_id"] = edge_id
                link["edge_id"] = None
                break
    return result


def _apply_terminal_geometry(plan: InteractiveSLDPlan, nodes: list[dict[str, Any]], links: list[dict[str, Any]]) -> None:
    devices = _device_nodes(nodes)
    bus_names = {bus.bus_id.lower(): bus.bus_id for bus in plan.buses}
    label_position = {"up": "top", "down": "bottom", "left": "left", "right": "right"}
    for placement in plan.devices:
        node = devices.get(placement.device_id)
        if node is None:
            continue
        node["x"], node["y"] = placement.center.x, placement.center.y
        node["canonical_direction"] = placement.direction
        node["canonical_geometry_v2"] = True
        node["sld_device_kind"] = placement.kind
        node["rotation"] = _terminal_symbol_rotation(placement.kind, placement.direction)
        label = node.setdefault("label", {})
        label.update({"position": label_position[placement.direction], "distance": 6, "fontSize": 11, "color": "#334155", "textBorderWidth": 0, "show": False})
        bus_id = bus_names.get(placement.bus_id.lower(), placement.bus_id)
        for link in links:
            source, target = str(link.get("source", "")), str(link.get("target", ""))
            if placement.device_id not in {source, target} or bus_id.lower() not in {source.lower(), target.lower()}:
                continue
            link["route_points"] = (
                [placement.center.as_tuple(), placement.bus_port.as_tuple()]
                if source == placement.device_id
                else [placement.bus_port.as_tuple(), placement.center.as_tuple()]
            )
            link["canonical_geometry_v2"] = True
            break


def _rebuild_branch_geometry(
    sld: SLDModel, plan: InteractiveSLDPlan, nodes: list[dict[str, Any]], links: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    rebuilt = [
        link
        for link in links
        if not link.get("edge_id")
        or str(link.get("edge_id") or "").casefold().startswith("gridlink.")
    ]
    for edge in sld.edges:
        canonical_edge = edge.model_copy(update={"id": canonical_sld_edge_id(edge.id)})
        try:
            branch = plan.branch(canonical_edge.id)
        except KeyError:
            # Legacy GridLink.* and other non-physical transport edges are not
            # renderer geometry.
            continue
        template = _primary_edge_link(links, edge.id)
        if template is None:
            continue
        route = _flow_route(edge, branch.points)
        link = dict(template)
        link["edge_id"] = canonical_edge.id
        link["source"] = edge.dst if edge.p_kw < 0 else edge.src
        link["target"] = edge.src if edge.p_kw < 0 else edge.dst
        link["route_points"] = route
        link["canonical_geometry_v2"] = True
        rebuilt.append(link)
        inline_center = _add_inline_symbol(edge=canonical_edge, plan=plan, nodes=nodes)
        _add_safe_flow_arrow(canonical_edge, nodes, route, color=_line_style_color(link), inline_center=inline_center)
    return rebuilt


def _apply_label_policy(sld: SLDModel, nodes: list[dict[str, Any]]) -> None:
    degree = {node.id.lower(): 0 for node in sld.nodes}
    for edge in sld.edges:
        if str(edge.id).startswith("GridLink."):
            continue
        if edge.src.lower() in degree:
            degree[edge.src.lower()] += 1
        if edge.dst.lower() in degree:
            degree[edge.dst.lower()] += 1
    # Keep the landmarks-plus-hover policy for genuinely dense overviews. A
    # 13--24 bus feeder still has enough page area to show every bus label;
    # hiding those labels makes an IEEE13-style SLD look disconnected even
    # when the canonical routes themselves are correct.
    dense_network = _uses_dense_label_policy(nodes)
    by_name = {node.id.lower(): node for node in sld.nodes}
    visual_nodes = _bus_nodes(nodes)
    for key in sorted(visual_nodes, key=lambda name: (round(float(visual_nodes[name].get("y", 0.0)), 6), round(float(visual_nodes[name].get("x", 0.0)), 6), name)):
        visual = visual_nodes[key]
        source = by_name.get(key)
        if source is None:
            continue
        compact = _compact_identifier(source.id, limit=_DENSE_LABEL_SOFT_LIMIT if dense_network else _LABEL_SOFT_LIMIT)
        values = [float(value) for value in visual.get("v_pu", []) if isinstance(value, (int, float))]
        if not values and getattr(source, "v_pu", None):
            values = [float(v) for v in (source.v_pu.values() if isinstance(source.v_pu, dict) else source.v_pu) if isinstance(v, (int, float))]
        voltage = sum(values) / len(values) if values else None
        visual["display_name"] = compact
        visual["label_v"] = f"{compact}\n{voltage:.3f} pu" if voltage is not None else compact
        has_grid = any(str(getattr(gen, "kind", "")).lower() == "grid" for gen in getattr(source, "gens", ()))
        important = has_grid or source.kind == "substation" or degree.get(key, 0) > 2 or bool(source.gens)
        visual["label_default_visible"] = not dense_network or important
        visual.setdefault("label", {})["show"] = not dense_network or important
        visual["label_priority"] = 100 if has_grid or source.kind == "substation" else 80 if important else 20
    for node in nodes:
        if not node.get("is_device") or node.get("is_flow_arrow"):
            continue
        label = node.get("label")
        if not isinstance(label, dict):
            continue
        original = str(label.get("formatter", "") or "").strip()
        if original:
            node["sld_hover_label"] = original
        label["fontSize"] = max(11, int(float(label.get("fontSize", 11))))
        label["textBorderWidth"] = 0
        if str(label.get("color", "")).lower() in {"#cbd5e1", "#e2e8f0", "#5eead4"}:
            label["color"] = "#334155"
        if dense_network and node.get("sld_device_kind") not in {"event"}:
            label["formatter"] = ""
            label["show"] = False


def _canonical_center(nodes: list[dict[str, Any]]) -> list[float]:
    drawable = [node for node in nodes if not node.get("is_bend") and not node.get("is_flow_arrow") and isinstance(node.get("x"), (int, float)) and isinstance(node.get("y"), (int, float))]
    if not drawable:
        return [0.0, 0.0]
    xs, ys = [float(node["x"]) for node in drawable], [float(node["y"]) for node in drawable]
    return [(min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0]


def build_sld_option_v2(sld: SLDModel) -> dict[str, Any]:
    """Build the production report graph from the physical canonical plan."""
    option = _legacy_build_sld_option(sld)

    # Build canonical topology with a fixed shared terminal clearance. The
    # viewport may scale glyphs, but it must not move terminals into a route
    # corridor or make Native and Interactive choose different free space.
    plan = build_interactive_sld_plan(sld, terminal_clearance=CANONICAL_TERMINAL_CLEARANCE)

    physical = set(plan.physical_bus_ids)
    links = list(option.get("links", []))
    virtual_terminal_nodes = _add_virtual_grid_terminal_nodes(sld, plan, list(option.get("nodes", [])), links)
    nodes = [*virtual_terminal_nodes]
    nodes.extend(
        node
        for node in option.get("nodes", [])
        if not node.get("is_bend")
        and not node.get("is_flow_arrow")
        and not node.get("is_inline_device")
        and (node.get("category") != "bus" or str(node.get("name", "")).lower() in physical)
    )
    _apply_bus_geometry(plan, nodes)
    _apply_terminal_geometry(plan, nodes, links)
    links = _rebuild_branch_geometry(sld, plan, nodes, links)
    _apply_label_policy(sld, nodes)
    if len(_bus_nodes(nodes)) >= 13:
        _resolve_bus_label_collisions(
            nodes,
            links,
            hide_blocked=_uses_dense_label_policy(nodes),
        )

    option["nodes"] = nodes
    option["links"] = links
    option["center"] = _canonical_center(nodes)
    option["visual_policy"] = {
        "theme": "engineering-light",
        "device_glyphs": "semantic-plotly-overlay",
        "dense_label_policy": "landmarks-plus-hover",
        "min_visual_bus_gap": _MIN_VISUAL_BUS_GAP,
        "physical_sources": "terminal-not-pseudo-bus",
    }
    option["layout_receipt"] = {
        "schema": "cept-canonical-sld-layout-v1",
        "layout_sha256": plan.layout_sha256,
        "physical_bus_ids": list(plan.physical_bus_ids),
        "virtual_source_bus_ids": list(plan.virtual_source_bus_ids),
    }
    option["geometry_receipt"] = geometry_receipt(plan.geometry, consumer="interactive-sld", renderer="plotly")
    option["render_contract_receipt"] = plan.render_contract.receipt(
        consumer="interactive-sld",
        renderer="plotly",
    )
    option["ux_policy"] = {
        "primary_font_px": plan.ux.primary_font_px,
        "device_font_px": plan.ux.device_font_px,
        "secondary_font_px": plan.ux.secondary_font_px,
        "branch_data_default": plan.ux.branch_data_default,
        "branch_data_interaction": plan.ux.branch_data_interaction,
        "viewport_transform_only": plan.ux.viewport_transform_only,
        "uniform_xy_scale": plan.ux.uniform_xy_scale,
        "status_color_semantics": plan.ux.status_color_semantics,
    }
    option["geometry_schema"] = plan.schema
    return option


__all__ = ["build_sld_option_v2"]
