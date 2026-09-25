"""Pure SVG renderer for CEPT single-line diagrams.

The renderer consumes the already-planned canonical SLD graph and is deliberately
presentation-only: it never invents topology.  Device glyphs are scaled from the
world-to-screen ratio so a 78-bus overview still shows recognisable equipment,
while branch and symbol strokes use ``vector-effect=non-scaling-stroke`` so zoom
and finite-page fitting do not turn the drawing into hairlines.

A busbar's longitudinal extent is canonical geometry, not presentation. Viewport
scaling may enlarge glyphs, text, and busbar *thickness*, but it must never change
``canonical_half_length`` or the renderer can visually overlap adjacent buses
while the canonical/PF geometry remains collision-free.
"""

from __future__ import annotations

import html
import math
from typing import Any

_TARGET_WIDTH_PX = 1100.0
_TARGET_HEIGHT_PX = 700.0
_MAX_GLYPH_SCALE = 6.0
# Widest common inline glyph is the 50-unit transformer symbol. Reserve a
# small engineering clearance around repeated devices in the same row before
# viewport scaling. If canonical pitch is smaller than this envelope, glyphs
# stay at their 1.0 minimum and geometry—not presentation magnification—owns
# any remaining crowding decision.
_DENSE_ROW_GLYPH_ENVELOPE = 56.0
_VERY_DENSE_MIN_GLYPH_SCALE = 0.65
_TERMINAL_KINDS = {
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
}

_DEFS_SVG = '''<defs>
  <g id="sym-tx-h" class="sld-symbol-shape">
    <rect x="-25" y="-16" width="50" height="32" rx="3" fill="#fff" stroke="none"/>
    <circle cx="-7" cy="0" r="11" fill="#fff" stroke="#92400e" vector-effect="non-scaling-stroke" stroke-width="2.6"/>
    <circle cx="7" cy="0" r="11" fill="none" stroke="#92400e" vector-effect="non-scaling-stroke" stroke-width="2.6"/>
  </g>
  <g id="sym-cb-closed" class="sld-symbol-shape">
    <rect x="-17" y="-12" width="34" height="24" rx="2" fill="#fff" stroke="none"/>
    <circle cx="-11" cy="0" r="3.4" fill="#fff" stroke="#047857" vector-effect="non-scaling-stroke" stroke-width="2.2"/>
    <circle cx="11" cy="0" r="3.4" fill="#fff" stroke="#047857" vector-effect="non-scaling-stroke" stroke-width="2.2"/>
    <line x1="-11" y1="0" x2="11" y2="0" stroke="#047857" vector-effect="non-scaling-stroke" stroke-width="3.4" stroke-linecap="round"/>
  </g>
  <g id="sym-cb-open" class="sld-symbol-shape">
    <rect x="-17" y="-17" width="34" height="34" rx="2" fill="#fff" stroke="none"/>
    <circle cx="-11" cy="0" r="3.4" fill="#fff" stroke="#dc2626" vector-effect="non-scaling-stroke" stroke-width="2.2"/>
    <circle cx="11" cy="0" r="3.4" fill="#fff" stroke="#dc2626" vector-effect="non-scaling-stroke" stroke-width="2.2"/>
    <line x1="-11" y1="0" x2="7" y2="-12" stroke="#dc2626" vector-effect="non-scaling-stroke" stroke-width="3.2" stroke-linecap="round"/>
  </g>
  <g id="sym-converter" class="sld-symbol-shape">
    <rect x="-25" y="-16" width="50" height="32" rx="3" fill="#fff" stroke="none"/>
    <polygon points="0,-11 11,0 0,11 -11,0" fill="#fff" stroke="#0d9488" vector-effect="non-scaling-stroke" stroke-width="2.6"/>
    <line x1="0" y1="-11" x2="0" y2="11" stroke="#0d9488" vector-effect="non-scaling-stroke" stroke-width="2.2" stroke-linecap="round"/>
  </g>
  <g id="sym-load-down" class="sld-symbol-shape"><polygon points="-9,-5 9,-5 0,13" fill="#334155" stroke="#1e293b" vector-effect="non-scaling-stroke" stroke-width="1.6"/></g>
  <g id="sym-load-up" class="sld-symbol-shape"><polygon points="-9,5 9,5 0,-13" fill="#334155" stroke="#1e293b" vector-effect="non-scaling-stroke" stroke-width="1.6"/></g>
  <g id="sym-load-left" class="sld-symbol-shape"><polygon points="5,-9 5,9 -13,0" fill="#334155" stroke="#1e293b" vector-effect="non-scaling-stroke" stroke-width="1.6"/></g>
  <g id="sym-load-right" class="sld-symbol-shape"><polygon points="-5,-9 -5,9 13,0" fill="#334155" stroke="#1e293b" vector-effect="non-scaling-stroke" stroke-width="1.6"/></g>
  <g id="sym-gen" class="sld-symbol-shape"><circle r="14" fill="#fff" stroke="#166534" vector-effect="non-scaling-stroke" stroke-width="2.6"/><text y="4.5" text-anchor="middle" font-family="system-ui,sans-serif" font-size="11" font-weight="700" fill="#166534">G</text></g>
  <g id="sym-hydro" class="sld-symbol-shape"><circle r="14" fill="#fff" stroke="#0369a1" vector-effect="non-scaling-stroke" stroke-width="2.6"/><text y="4.5" text-anchor="middle" font-family="system-ui,sans-serif" font-size="11" font-weight="700" fill="#0369a1">H</text></g>
  <g id="sym-indmach" class="sld-symbol-shape"><circle r="14" fill="#fff" stroke="#be123c" vector-effect="non-scaling-stroke" stroke-width="2.6"/><text y="4" text-anchor="middle" font-family="system-ui,sans-serif" font-size="8.5" font-weight="700" fill="#be123c">IM</text></g>
  <g id="sym-grid" class="sld-symbol-shape">
    <rect x="-16" y="-11" width="32" height="22" rx="1" fill="#fff" stroke="#1e293b" vector-effect="non-scaling-stroke" stroke-width="2.2"/>
    <line x1="-16" y1="0" x2="16" y2="0" stroke="#1e293b" vector-effect="non-scaling-stroke" stroke-width="1.6"/>
    <line x1="0" y1="-11" x2="0" y2="11" stroke="#1e293b" vector-effect="non-scaling-stroke" stroke-width="1.6"/>
    <line x1="-16" y1="-5.5" x2="0" y2="-5.5" stroke="#1e293b" vector-effect="non-scaling-stroke" stroke-width="1.2"/>
    <line x1="0" y1="5.5" x2="16" y2="5.5" stroke="#1e293b" vector-effect="non-scaling-stroke" stroke-width="1.2"/>
  </g>
  <g id="sym-pv" class="sld-symbol-shape"><rect x="-13" y="-13" width="26" height="26" rx="2.5" fill="#fff" stroke="#b45309" vector-effect="non-scaling-stroke" stroke-width="2.6"/><text y="4" text-anchor="middle" font-family="system-ui,sans-serif" font-size="9.5" font-weight="700" fill="#b45309">PV</text></g>
  <g id="sym-wind" class="sld-symbol-shape"><polygon points="0,-15 -13,11 13,11" fill="#fff" stroke="#0369a1" vector-effect="non-scaling-stroke" stroke-width="2.6"/><text y="7" text-anchor="middle" font-family="system-ui,sans-serif" font-size="10" font-weight="700" fill="#0369a1">W</text></g>
  <g id="sym-battery" class="sld-symbol-shape"><rect x="-13" y="-13" width="26" height="26" rx="2.5" fill="#fff" stroke="#15803d" vector-effect="non-scaling-stroke" stroke-width="2.6"/><line x1="-5" y1="-4" x2="5" y2="-4" stroke="#15803d" vector-effect="non-scaling-stroke" stroke-width="2"/><line x1="0" y1="-9" x2="0" y2="1" stroke="#15803d" vector-effect="non-scaling-stroke" stroke-width="2"/><line x1="-5" y1="6" x2="5" y2="6" stroke="#15803d" vector-effect="non-scaling-stroke" stroke-width="2"/></g>
  <g id="sym-capacitor" class="sld-symbol-shape"><line x1="0" y1="-16" x2="0" y2="-4" stroke="#6d28d9" vector-effect="non-scaling-stroke" stroke-width="2.2"/><line x1="-10" y1="-4" x2="10" y2="-4" stroke="#6d28d9" vector-effect="non-scaling-stroke" stroke-width="2.8"/><line x1="-10" y1="4" x2="10" y2="4" stroke="#6d28d9" vector-effect="non-scaling-stroke" stroke-width="2.8"/><line x1="0" y1="4" x2="0" y2="16" stroke="#6d28d9" vector-effect="non-scaling-stroke" stroke-width="2.2"/></g>
  <g id="sym-reactor" class="sld-symbol-shape"><path d="M-12,0 Q-9,-8 -6,0 Q-3,-8 0,0 Q3,-8 6,0 Q9,-8 12,0" fill="none" stroke="#6d28d9" vector-effect="non-scaling-stroke" stroke-width="2.6" stroke-linecap="round"/></g>
  <g id="sym-statcom" class="sld-symbol-shape"><polygon points="0,-14 14,0 0,14 -14,0" fill="#fff" stroke="#6d28d9" vector-effect="non-scaling-stroke" stroke-width="2.5"/><text y="3.5" text-anchor="middle" font-family="system-ui,sans-serif" font-size="8" font-weight="700" fill="#6d28d9">SC</text></g>
  <g id="sym-svc" class="sld-symbol-shape"><rect x="-13" y="-13" width="26" height="26" rx="2.5" fill="#fff" stroke="#6d28d9" vector-effect="non-scaling-stroke" stroke-width="2.5"/><text y="3.5" text-anchor="middle" font-family="system-ui,sans-serif" font-size="7.5" font-weight="700" fill="#6d28d9">SVC</text></g>
  <g id="sym-event" class="sld-symbol-shape"><circle r="12" fill="#fff" stroke="#dc2626" vector-effect="non-scaling-stroke" stroke-width="2.4"/><text y="4.5" text-anchor="middle" font-family="system-ui,sans-serif" font-size="13" font-weight="800" fill="#dc2626">!</text></g>
</defs>'''


def _device_kind(node: dict[str, Any]) -> str:
    kind = str(node.get("sld_device_kind") or "")
    name = str(node.get("name") or "")
    if name.startswith("__load_"):
        return "load"
    if name.startswith("__event_"):
        return "event"
    return kind or "generator"


def _is_terminal_device(node: dict[str, Any]) -> bool:
    return bool(
        node.get("is_device")
        and not node.get("is_inline_device")
        and not node.get("is_flow_arrow")
        and not node.get("is_bend")
        and _device_kind(node) in _TERMINAL_KINDS
    )


def _raw_bounds(nodes: list[dict[str, Any]], links: list[dict[str, Any]]) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for node in nodes:
        if node.get("is_bend") or node.get("is_flow_arrow"):
            continue
        if node.get("is_device") and _device_kind(node) == "event":
            continue
        try:
            xs.append(float(node["x"]))
            ys.append(float(node["y"]))
        except (KeyError, TypeError, ValueError):
            continue
    for link in links:
        for point in link.get("route_points") or []:
            try:
                xs.append(float(point[0]))
                ys.append(float(point[1]))
            except (IndexError, TypeError, ValueError):
                continue
    if not xs or not ys:
        return (0.0, 0.0, 800.0, 500.0)
    return (min(xs), min(ys), max(xs), max(ys))


def _display_scales(
    bounds: tuple[float, float, float, float],
    nodes: list[dict[str, Any]] | None = None,
) -> tuple[float, float, float]:
    x0, y0, x1, y1 = bounds
    span_x = max(x1 - x0, 120.0)
    span_y = max(y1 - y0, 80.0)
    world_per_px = max(span_x / _TARGET_WIDTH_PX, span_y / _TARGET_HEIGHT_PX)
    glyph = min(_MAX_GLYPH_SCALE, max(1.0, world_per_px * 1.05))
    if nodes:
        xs_by_y: dict[float, list[float]] = {}
        for n in nodes:
            if not n.get("is_device") or n.get("is_flow_arrow") or n.get("is_bend"):
                continue
            if _device_kind(n) == "event":
                continue
            try:
                y_key = round(float(n.get("y", 0.0)), 1)
                xs_by_y.setdefault(y_key, []).append(float(n.get("x", 0.0)))
            except (TypeError, ValueError):
                pass
        min_dx = float("inf")
        for xs in xs_by_y.values():
            if len(xs) > 1:
                s_xs = sorted(xs)
                diffs = [b - a for a, b in zip(s_xs, s_xs[1:]) if b - a > 1.0]
                if diffs:
                    min_dx = min(min_dx, min(diffs))
        if math.isfinite(min_dx):
            # A 78-bus industrial overview can have a 48-unit electrical
            # port pitch while the transformer/switch glyph is 50 units wide.
            # Keeping the historical 1.0 floor makes the glyph overlap its
            # neighbouring route.  Only very dense graphs may use a smaller
            # presentation scale; canonical coordinates and topology remain
            # unchanged.
            minimum_glyph = _VERY_DENSE_MIN_GLYPH_SCALE if len(nodes) >= 60 else 1.0
            max_allowed_glyph = max(minimum_glyph, min_dx / _DENSE_ROW_GLYPH_ENVELOPE)
            glyph = min(glyph, max_allowed_glyph)
    text = min(4.5, max(1.0, world_per_px * 0.98))
    bus = min(3.0, max(1.0, 0.55 + glyph * 0.55))
    return glyph, text, bus


def _label_offset(node: dict[str, Any]) -> tuple[float, float]:
    offset = (node.get("label") or {}).get("offset")
    if not isinstance(offset, dict):
        return 0.0, 0.0
    try:
        return float(offset.get("x", 0.0) or 0.0), float(offset.get("y", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0, 0.0


def _expand_bounds_for_bus_labels(
    bounds: tuple[float, float, float, float],
    nodes: list[dict[str, Any]],
    *,
    text_scale: float,
) -> tuple[float, float, float, float]:
    """Include rendered bus-label extents in the SVG viewBox.

    The old bounds considered only topology points.  A left-aligned label on
    the first bus or a bottom label on a source bus could therefore be clipped
    even though the canonical geometry itself was valid.  This estimates the
    same monospace-independent envelope used by the SVG text element and keeps
    the crop deterministic without changing world geometry.
    """
    min_x, min_y, max_x, max_y = bounds
    for node in nodes:
        if node.get("category") != "bus":
            continue
        if not node.get("label_default_visible", True):
            continue
        label = str(node.get("label_v") or "")
        if not label:
            continue
        x = float(node.get("x", 0.0))
        y = float(node.get("y", 0.0))
        offset_x, offset_y = _label_offset(node)
        x += offset_x
        y += offset_y
        position = str((node.get("label") or {}).get("position") or "top")
        half = float(node.get("canonical_half_length") or (26.0 if node.get("kind") == "substation" else 22.0))
        font_size = 11.5 * text_scale
        line_height = font_size * 1.1
        lines = label.split("\n")
        text_width = max(len(line) for line in lines) * font_size * 0.62
        text_height = font_size + max(0, len(lines) - 1) * line_height
        if position == "bottom":
            lx, ly = x, y + half + 15.0 * text_scale
            left, right = lx - text_width / 2.0, lx + text_width / 2.0
            top, bottom = ly - font_size, ly + text_height * 0.25
        elif position == "left":
            lx, ly = x - half - 8.0 * text_scale, y + 4.0 * text_scale
            left, right = lx - text_width, lx
            top, bottom = ly - font_size, ly + text_height * 0.25
        elif position == "right":
            lx, ly = x + half + 8.0 * text_scale, y + 4.0 * text_scale
            left, right = lx, lx + text_width
            top, bottom = ly - font_size, ly + text_height * 0.25
        else:
            lx, ly = x, y - half - 8.0 * text_scale
            left, right = lx - text_width / 2.0, lx + text_width / 2.0
            top, bottom = ly - font_size, ly + text_height * 0.25
        min_x, max_x = min(min_x, left), max(max_x, right)
        min_y, max_y = min(min_y, top), max(max_y, bottom)
    return min_x, min_y, max_x, max_y


def _path(points: list[tuple[float, float]]) -> str:
    if not points:
        return ""
    return " ".join(
        [f"M {points[0][0]:.2f} {points[0][1]:.2f}"]
        + [f"L {x:.2f} {y:.2f}" for x, y in points[1:]]
    )


def _route_points(link: dict[str, Any]) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    for point in link.get("route_points") or []:
        try:
            result.append((float(point[0]), float(point[1])))
        except (IndexError, TypeError, ValueError):
            continue
    return result


def _terminal_link(node: dict[str, Any], links: list[dict[str, Any]]) -> dict[str, Any] | None:
    name = str(node.get("name") or "")
    matches = [
        link
        for link in links
        if not link.get("edge_id") and name in {str(link.get("source") or ""), str(link.get("target") or "")}
    ]
    return matches[0] if len(matches) == 1 else None


def _node_index(nodes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(node.get("name") or "").lower(): node for node in nodes if node.get("name")}


def _point_on_busbar(
    bus: dict[str, Any], point: tuple[float, float], *, tolerance: float
) -> bool:
    bx, by = float(bus.get("x", 0.0)), float(bus.get("y", 0.0))
    px, py = point
    half = float(bus.get("canonical_half_length") or 32.0)
    if bus.get("bus_orientation") == "vertical":
        return abs(px - bx) <= tolerance and abs(py - by) <= half
    return abs(py - by) <= tolerance and abs(px - bx) <= half


def _point_on_route(
    point: tuple[float, float], route: list[tuple[float, float]], *, tolerance: float
) -> bool:
    px, py = point
    for left, right in zip(route, route[1:]):
        lx, ly = left
        rx, ry = right
        dx, dy = rx - lx, ry - ly
        length = math.hypot(dx, dy)
        if length <= tolerance:
            continue
        cross = abs((px - lx) * dy - (py - ly) * dx) / length
        if cross > tolerance:
            continue
        dot = (px - lx) * dx + (py - ly) * dy
        if -tolerance <= dot <= dx * dx + dy * dy + tolerance:
            return True
    return False


def audit_terminal_connections(graph: dict[str, Any], *, tolerance: float = 1e-4) -> list[str]:
    """Return connection errors for terminal, branch and inline SLD geometry.

    The name is retained for API compatibility, but this is deliberately a
    full presentation-connection audit.  A review screenshot must not be
    accepted when a terminal stem misses its bus, a branch misses an endpoint
    busbar, or an inline transformer/switch is not actually on its route.
    """
    nodes: list[dict[str, Any]] = list(graph.get("nodes") or [])
    links: list[dict[str, Any]] = list(graph.get("links") or [])
    by_name = _node_index(nodes)
    errors: list[str] = []

    for node in nodes:
        if not _is_terminal_device(node):
            continue
        name = str(node.get("name") or "")
        link = _terminal_link(node, links)
        if link is None:
            errors.append(f"{name}: expected exactly one canonical terminal link")
            continue
        route = _route_points(link)
        if len(route) < 2:
            errors.append(f"{name}: terminal link has no canonical route")
            continue
        cx, cy = float(node.get("x", 0.0)), float(node.get("y", 0.0))
        first_distance = math.hypot(route[0][0] - cx, route[0][1] - cy)
        last_distance = math.hypot(route[-1][0] - cx, route[-1][1] - cy)
        if min(first_distance, last_distance) > tolerance:
            errors.append(f"{name}: canonical route does not terminate at the device center")
            continue
        device_at_start = first_distance <= last_distance
        bus_point = route[-1] if device_at_start else route[0]
        source = str(link.get("source") or "")
        target = str(link.get("target") or "")
        bus_id = target if source == name else source
        bus = by_name.get(bus_id.lower())
        if bus is None or bus.get("category") != "bus":
            errors.append(f"{name}: terminal counterpart {bus_id!r} is not a bus")
            continue
        if not _point_on_busbar(bus, bus_point, tolerance=tolerance):
            errors.append(f"{name}: canonical terminal route misses busbar {bus_id}")
        direction = str(node.get("canonical_direction") or "")
        if direction in {"up", "down"} and abs(bus_point[0] - cx) > tolerance:
            errors.append(f"{name}: {direction} terminal connection is not perpendicular to its busbar")
        if direction in {"left", "right"} and abs(bus_point[1] - cy) > tolerance:
            errors.append(f"{name}: {direction} terminal connection is not perpendicular to its busbar")

    for link in links:
        if not link.get("edge_id"):
            continue
        edge_id = str(link.get("edge_id") or "")
        route = _route_points(link)
        if len(route) < 2:
            errors.append(f"{edge_id}: branch has no canonical route")
            continue
        for endpoint, node_id in ((route[0], str(link.get("source") or "")), (route[-1], str(link.get("target") or ""))):
            bus = by_name.get(node_id.lower())
            if bus is None or bus.get("category") != "bus":
                errors.append(f"{edge_id}: branch endpoint {node_id!r} is not a bus")
            elif not _point_on_busbar(bus, endpoint, tolerance=tolerance):
                errors.append(f"{edge_id}: branch route misses busbar {node_id}")

    edge_links = {str(link.get("edge_id") or ""): link for link in links if link.get("edge_id")}
    for node in nodes:
        if not node.get("is_inline_device"):
            continue
        edge_id = str(node.get("canonical_edge_id") or "")
        link = edge_links.get(edge_id)
        if link is None:
            errors.append(f"{node.get('name') or edge_id}: inline device has no canonical branch")
            continue
        route = _route_points(link)
        point = (float(node.get("x", 0.0)), float(node.get("y", 0.0)))
        if not _point_on_route(point, route, tolerance=tolerance):
            errors.append(f"{node.get('name') or edge_id}: inline device center is not on its branch route")
    return errors


def _trim_terminal_route(
    node: dict[str, Any], link: dict[str, Any], glyph_scale: float
) -> list[tuple[float, float]]:
    route = _route_points(link)
    if len(route) < 2:
        return route
    cx, cy = float(node.get("x", 0.0)), float(node.get("y", 0.0))
    first_distance = math.hypot(route[0][0] - cx, route[0][1] - cy)
    last_distance = math.hypot(route[-1][0] - cx, route[-1][1] - cy)
    center_at_start = first_distance <= last_distance
    adjacent = route[1] if center_at_start else route[-2]
    dx, dy = adjacent[0] - cx, adjacent[1] - cy
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        return route
    kind = _device_kind(node)
    base_radius = {
        "load": 11.0,
        "capacitor": 10.0,
        "reactor": 11.0,
        "statcom": 14.0,
        "svc": 14.0,
    }.get(kind, 15.0)
    offset = min(length * 0.45, base_radius * glyph_scale)
    anchor = (cx + dx / length * offset, cy + dy / length * offset)
    trimmed = list(route)
    if center_at_start:
        trimmed[0] = anchor
    else:
        trimmed[-1] = anchor
    return trimmed


def _symbol_href(kind: str, direction: str) -> str:
    """Return the renderer symbol for a terminal device.

    Load glyphs use one conventional downward orientation.  Their connector
    stem remains topology-aware through ``canonical_direction``; it is not a
    solved real-power-flow marker.
    """
    return {
        "load": "#sym-load-down",
        "pv": "#sym-pv",
        "wind": "#sym-wind",
        "hydro": "#sym-hydro",
        "battery": "#sym-battery",
        "generator": "#sym-gen",
        "syncgen": "#sym-gen",
        "indmach": "#sym-indmach",
        "grid": "#sym-grid",
        "capacitor": "#sym-capacitor",
        "reactor": "#sym-reactor",
        "statcom": "#sym-statcom",
        "svc": "#sym-svc",
    }.get(kind, "#sym-gen")


def _flow_arrow_markup(
    nodes: list[dict[str, Any]],
    links: list[dict[str, Any]],
    glyph_scale: float,
) -> list[str]:
    """Render solver-backed P-direction markers separately from load glyphs.

    Load terminals can be placed on either side of a busbar to avoid route
    collisions.  Their symbol must therefore never be read as feeder-flow
    direction.  Flow markers are emitted only for non-zero solved ``p_kw`` and
    use the route-oriented ``symbolRotate`` already produced by the canonical
    SLD runtime.
    """
    links_by_id = {str(link.get("edge_id") or ""): link for link in links if link.get("edge_id")}
    arrows: list[str] = []
    for node in nodes:
        if not node.get("is_flow_arrow"):
            continue
        name = str(node.get("name") or "")
        edge_id = name.removeprefix("__flow_arrow_")
        link = links_by_id.get(edge_id)
        if link is not None:
            try:
                p_kw = float(link.get("p_kw", 0.0))
            except (TypeError, ValueError):
                p_kw = 0.0
            if not math.isfinite(p_kw) or abs(p_kw) <= 1e-6:
                continue
        try:
            x, y = float(node["x"]), float(node["y"])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(x) or not math.isfinite(y):
            continue
        try:
            rotation = float(node.get("symbolRotate", 0.0) or 0.0)
        except (TypeError, ValueError):
            rotation = 0.0
        if not math.isfinite(rotation):
            rotation = 0.0
        try:
            symbol_size = float(node.get("symbolSize", 11.0) or 11.0)
        except (TypeError, ValueError):
            symbol_size = 11.0
        scale = glyph_scale * max(0.7, min(1.4, symbol_size / 11.0))
        style = node.get("itemStyle")
        color = str(style.get("color") or "#475569") if isinstance(style, dict) else "#475569"
        if link is not None:
            source = str(link.get("src_bus") or link.get("source") or "")
            target = str(link.get("dst_bus") or link.get("target") or "")
            if p_kw < 0.0:
                source, target = target, source
            title = f"Solved real-power flow: {source} → {target}"
        else:
            title = "Solved real-power flow"
        arrows.append(
            f'<g class="sld-flow-arrow sld-symbol" transform="translate({x:.2f} {y:.2f}) rotate({rotation:.1f}) scale({scale:.4f})" data-flow-edge-id="{html.escape(edge_id)}">'
            f'<polygon points="-7,-4 7,0 -7,4" fill="{html.escape(color)}" stroke="#ffffff" vector-effect="non-scaling-stroke" stroke-width="1.2"/>'
            f'<title>{html.escape(title)}</title></g>'
        )
    return arrows


def render_native_sld_svg(
    graph: dict[str, Any],
    *,
    dom_id: str = "sld_canvas",
    padding: float = 24.0,
    strict_connections: bool = False,
) -> str:
    """Render a canonical graph as responsive, review-friendly SVG."""
    nodes: list[dict[str, Any]] = list(graph.get("nodes") or [])
    links: list[dict[str, Any]] = list(graph.get("links") or [])
    raw = _raw_bounds(nodes, links)
    glyph_scale, text_scale, bus_scale = _display_scales(raw, nodes)
    raw = _expand_bounds_for_bus_labels(
        raw,
        nodes,
        text_scale=text_scale,
    )
    # Recompute once after adding text extents so a wider label does not cause
    # the glyph sizing and the final fitted frame to disagree.
    glyph_scale, text_scale, bus_scale = _display_scales(raw, nodes)
    raw = _expand_bounds_for_bus_labels(
        raw,
        nodes,
        text_scale=text_scale,
    )
    dynamic_padding = max(float(padding), 28.0 * glyph_scale, 110.0 * text_scale)
    min_x, min_y = raw[0] - dynamic_padding, raw[1] - dynamic_padding
    max_x, max_y = raw[2] + dynamic_padding, raw[3] + dynamic_padding
    width = max(max_x - min_x, 120.0)
    height = max(max_y - min_y, 80.0)

    errors = audit_terminal_connections(graph)
    if strict_connections and errors:
        raise ValueError("; ".join(errors))

    branches: list[str] = []
    for link in links:
        if not link.get("edge_id"):
            continue
        route = _route_points(link)
        if len(route) < 2:
            continue
        is_open = link.get("status") == "open"
        kind = str(link.get("el_kind") or "")
        stroke = "#dc2626" if is_open else "#92400e" if kind in {"transformer", "regulator"} else "#047857" if kind == "switch" else "#0d9488" if kind == "converter" else "#334155"
        dash = ' stroke-dasharray="7,5"' if is_open else ""
        edge_id = html.escape(str(link.get("edge_id") or ""))
        tooltip = html.escape(str(link.get("label_len") or link.get("label_flow") or edge_id))
        branches.append(
            f'<path class="sld-branch" d="{_path(route)}" stroke="{stroke}" vector-effect="non-scaling-stroke" stroke-width="2.2" fill="none"{dash} data-edge-id="{edge_id}"><title>{tooltip}</title></path>'
        )
    flow_arrows = _flow_arrow_markup(nodes, links, glyph_scale)

    terminal_stems: list[str] = []
    for node in nodes:
        if not _is_terminal_device(node):
            continue
        term_link = _terminal_link(node, links)
        if term_link is None:
            continue
        route = _trim_terminal_route(node, term_link, glyph_scale)
        if len(route) < 2:
            continue
        terminal_stems.append(
            f'<path class="sld-terminal-stem" d="{_path(route)}" stroke="#475569" vector-effect="non-scaling-stroke" stroke-width="2" fill="none" data-device-id="{html.escape(str(node.get("name") or ""))}"/>'
        )

    inlines: list[str] = []
    for node in nodes:
        if not node.get("is_inline_device"):
            continue
        kind = str(node.get("sld_device_kind") or "")
        x, y = float(node.get("x", 0.0)), float(node.get("y", 0.0))
        rotation = 90.0 if float(node.get("rotation", 0.0)) % 180 else 0.0
        if kind in {"transformer", "regulator"}:
            href = "#sym-tx-h"
        elif kind == "converter":
            href = "#sym-converter"
        elif kind == "switch":
            edge_id = str(node.get("canonical_edge_id") or "")
            matching_edge: dict[str, Any] = next((item for item in links if str(item.get("edge_id") or "") == edge_id), {})
            href = "#sym-cb-open" if matching_edge.get("status") == "open" else "#sym-cb-closed"
        else:
            continue
        name = html.escape(str(node.get("name") or node.get("canonical_edge_id") or kind))
        inlines.append(
            f'<g class="sld-inline sld-symbol" transform="translate({x:.2f} {y:.2f}) rotate({rotation:.1f}) scale({glyph_scale:.4f})" data-kind="{kind}" data-edge-id="{html.escape(str(node.get("canonical_edge_id") or ""))}"><use href="{href}"/><title>{name}</title></g>'
        )

    busbars: list[str] = []
    labels: list[str] = []
    for node in nodes:
        if node.get("category") != "bus":
            continue
        x, y = float(node.get("x", 0.0)), float(node.get("y", 0.0))
        substation = node.get("kind") == "substation"
        vertical = node.get("bus_orientation") == "vertical"
        # Longitudinal busbar extent is canonical geometry. Only thickness is
        # presentation-scaled; otherwise dense buses can visually overlap even
        # though the shared canonical/PF geometry has positive clearance.
        half = float(node.get("canonical_half_length") or (26.0 if substation else 22.0))
        thick = (6.5 if substation else 4.5) * min(bus_scale, 2.0)
        style = node.get("itemStyle")
        candidate = style.get("color") if isinstance(style, dict) else None
        if isinstance(candidate, str) and candidate.strip():
            color = html.escape(candidate.strip())
        else:
            color = "#0b5cad" if substation else "#172433"
        if vertical:
            bx, by, bw, bh = x - thick / 2, y - half, thick, half * 2
        else:
            bx, by, bw, bh = x - half, y - thick / 2, half * 2, thick
        bus_id = html.escape(str(node.get("name") or ""))
        raw_title = str(node.get("label_v") or f"Bus: {bus_id}")
        bus_title = html.escape(" - ".join(raw_title.splitlines()))
        busbars.append(
            f'<rect class="sld-busbar" x="{bx:.2f}" y="{by:.2f}" width="{bw:.2f}" height="{bh:.2f}" rx="{max(1.5, thick * .18):.2f}" fill="{color}" data-bus-id="{bus_id}" data-canonical-half-length="{half:.2f}"><title>{bus_title}</title></rect>'
        )
        text = str(node.get("label_v") or "")
        if text and node.get("label_default_visible", True):
            position = str((node.get("label") or {}).get("position") or "top")
            label_style = node.get("label") if isinstance(node.get("label"), dict) else {}
            label_color = html.escape(str(label_style.get("color") or "#172433"))
            font_weight = html.escape(str(label_style.get("fontWeight") or 600))
            offset_x, offset_y = _label_offset(node)
            x += offset_x
            y += offset_y
            font_size = 11.5 * text_scale
            line_height = font_size * 1.15
            lines_list = html.escape(text).split("\n")
            num_lines = len(lines_list)
            top_edge = y - half if vertical else y - thick / 2
            bottom_edge = y + half if vertical else y + thick / 2
            left_edge = x - thick / 2 if vertical else x - half
            right_edge = x + thick / 2 if vertical else x + half
            if position == "bottom":
                lx, ly, anchor = x, bottom_edge + 6 * text_scale + 0.85 * font_size, "middle"
            elif position == "left":
                lx, ly, anchor = left_edge - 8 * text_scale, y - ((num_lines - 1) * line_height) / 2 + 0.35 * font_size, "end"
            elif position == "right":
                lx, ly, anchor = right_edge + 8 * text_scale, y - ((num_lines - 1) * line_height) / 2 + 0.35 * font_size, "start"
            else:
                lx, ly, anchor = x, top_edge - 6 * text_scale - (num_lines - 1) * line_height, "middle"
            tspans = "".join(
                f'<tspan x="{lx:.2f}" dy="{0 if index == 0 else line_height:.2f}">{line}</tspan>'
                for index, line in enumerate(lines_list)
            )
            labels.append(
                f'<text class="sld-bus-label" x="{lx:.2f}" y="{ly:.2f}" text-anchor="{anchor}" font-family="system-ui,sans-serif" font-size="{font_size:.2f}" font-weight="{font_weight}" fill="{label_color}">{tspans}</text>'
            )

    junctions: list[str] = []
    seen_junctions: set[tuple[float, float]] = set()
    bus_by_name = {
        str(node.get("name") or "").lower(): node
        for node in nodes
        if node.get("category") == "bus"
    }
    junction_radius = max(2.8, 3.0 * bus_scale)
    for link in links:
        route = _route_points(link)
        if len(route) < 2:
            continue
        for endpoint, node_id in (
            (route[0], str(link.get("source") or "")),
            (route[-1], str(link.get("target") or "")),
        ):
            bus = bus_by_name.get(node_id.lower())
            if bus is None or not _point_on_busbar(bus, endpoint, tolerance=1e-6):
                continue
            key = (round(endpoint[0], 6), round(endpoint[1], 6))
            if key in seen_junctions:
                continue
            seen_junctions.add(key)
            junctions.append(
                f'<circle class="sld-junction" cx="{endpoint[0]:.2f}" cy="{endpoint[1]:.2f}" r="{junction_radius:.2f}" fill="#0b5cad" stroke="#ffffff" stroke-width="1.5" data-bus-id="{html.escape(node_id)}"/>'
            )

    devices: list[str] = []
    for node in nodes:
        if not _is_terminal_device(node):
            continue
        kind = _device_kind(node)
        x, y = float(node.get("x", 0.0)), float(node.get("y", 0.0))
        direction = str(node.get("canonical_direction") or "down")
        href = _symbol_href(kind, direction)
        rotation = float(node.get("rotation", 0.0) or 0.0)
        title = html.escape(str(node.get("sld_hover_label") or node.get("display_name") or node.get("name") or kind))
        devices.append(
            f'<g class="sld-device sld-symbol" transform="translate({x:.2f} {y:.2f}) rotate({rotation:.1f}) scale({glyph_scale:.4f})" data-kind="{html.escape(kind)}" data-device-id="{html.escape(str(node.get("name") or node.get("display_name") or ""))}"><use href="{href}"/><title>{title}</title></g>'
        )

    events: list[str] = []
    for node in nodes:
        if not node.get("is_device") or _device_kind(node) != "event":
            continue
        bus = bus_by_name.get(str(node.get("event_bus_id") or "").lower())
        if bus is not None:
            bx, by = float(bus.get("x", 0.0)), float(bus.get("y", 0.0))
            x, y = bx + 18.0, by - 18.0
        else:
            x, y = float(node.get("x", 0.0)), float(node.get("y", 0.0))
        title = html.escape(str(node.get("sld_hover_label") or node.get("display_name") or "Event"))
        events.append(
            f'<g class="sld-event sld-symbol" transform="translate({x:.2f} {y:.2f}) scale({glyph_scale:.4f})" data-kind="event" data-event-bus="{html.escape(str(node.get("event_bus_id") or ""))}"><use href="#sym-event"/><title>{title}</title></g>'
        )

    style = '''<style>
      .cept-sld-svg{background:#fff;user-select:none}
      .cept-sld-svg .sld-branch,.cept-sld-svg .sld-terminal-stem,.cept-sld-svg .sld-symbol-shape *{vector-effect:non-scaling-stroke}
      .cept-sld-svg .sld-branch,.cept-sld-svg .sld-terminal-stem{stroke-linecap:round;stroke-linejoin:round}
    </style>'''
    return f'''<svg id="{html.escape(dom_id)}_svg" class="cept-sld-svg" viewBox="{min_x:.2f} {min_y:.2f} {width:.2f} {height:.2f}" preserveAspectRatio="xMidYMid meet" width="100%" height="100%" xmlns="http://www.w3.org/2000/svg" data-glyph-scale="{glyph_scale:.4f}" data-content-aspect="{width / height:.6f}" data-connection-errors="{len(errors)}">
{style}
{_DEFS_SVG}
<g class="sld-layer sld-branches">{''.join(branches)}</g>
<g class="sld-layer sld-flow-arrows">{''.join(flow_arrows)}</g>
<g class="sld-layer sld-terminal-stems">{''.join(terminal_stems)}</g>
<g class="sld-layer sld-inlines">{''.join(inlines)}</g>
<g class="sld-layer sld-busbars">{''.join(busbars)}</g>
<g class="sld-layer sld-junctions">{''.join(junctions)}</g>
<g class="sld-layer sld-events">{''.join(events)}</g>
<g class="sld-layer sld-devices">{''.join(devices)}</g>
<g class="sld-layer sld-labels">{''.join(labels)}</g>
</svg>'''


__all__ = ["audit_terminal_connections", "render_native_sld_svg"]
