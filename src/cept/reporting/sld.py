"""Build an ECharts ``graph`` option from an :class:`~cept.schema.sld.SLDModel`.

Why ECharts (not Plotly) for the SLD: its graph series gives first-class
support for directed edges with arrowheads, custom node symbols, free roam/
zoom, and rich hover tooltips — exactly what a single-line diagram needs.

Display philosophy (per CEPT requirements):

* **Always visible, non-overlapping:** bus name + voltage, distinct symbols
  per element type, flow-direction arrows, and a compact length·loss tag on
  each branch. Device symbols (load/gen/shunt) hang off the bus on short
  stubs so they never sit on top of the bus label.
* **On hover:** full per-phase V∠θ, every connected load/DER/shunt, and per
  branch P, Q, loss, length, tap, status.
* **Toggle layers:** the page can switch node labels (voltage ↔ per-phase ↔
  off) and branch labels (length·loss ↔ P/Q ↔ off) to manage clutter.
* **Experiments:** affected elements carry an event marker, and the report
  shows the network before vs after side by side.

This module only produces JSON-able dicts; the rendering/JS lives in the
templates.
"""

from __future__ import annotations

import math
import re

from cept.schema.sld import SLDEdge, SLDModel, SLDNode

# --- distinct symbols (SVG silhouettes, scaled by ECharts to symbolSize) --- #
# Generators / DER by type.
_GEN_SYMBOL = {
    "pv": "path://M3,18 L9,5 L27,5 L21,18 Z M3,18 L21,18 M9,12 L25,12 M13,5 L9,18 M19,5 L15,18",
    "wind": "path://M15,16 L17,16 L16.5,30 L15.5,30 Z M16,15 m-1.6,0 a1.6,1.6 0 1,0 3.2,0 a1.6,1.6 0 1,0 -3.2,0 M16,13 L15,1 L18,2 Z M18,15 L30,14 L29,17 Z M14,16 L4,22 L3,19 Z",
    "hydro": "path://M16,2 C7,13 7,20 16,28 C25,20 25,13 16,2 Z",
    "syncgen": "path://M16,16 m-13,0 a13,13 0 1,0 26,0 a13,13 0 1,0 -26,0 M8,18 C11,9 13,9 16,16 C19,23 21,23 24,14",
    "battery": "path://M4,9 L24,9 L24,25 L4,25 Z M10,9 L10,5 L18,5 L18,9 M8,17 L14,17 M11,14 L11,20",
    "generator": "path://M16,16 m-13,0 a13,13 0 1,0 26,0 a13,13 0 1,0 -26,0 M10,16 L22,16 M16,10 L16,22",
    "indmach": "path://M16,16 m-13,0 a13,13 0 1,0 26,0 a13,13 0 1,0 -26,0 M9,16 L23,16 M10,12 L10,20 M22,12 L22,20",
    "grid": "path://M6,6 L26,6 L26,26 L6,26 Z M6,16 L26,16 M16,6 L16,26",
}
_GEN_COLOR = {
    "pv": "#f59e0b",
    "wind": "#38bdf8",
    "hydro": "#0ea5e9",
    "syncgen": "#a855f7",
    "battery": "#22c55e",
    "generator": "#a855f7",
    "indmach": "#e11d48",
    "grid": "#3b82f6",
}
_GEN_TAG = {
    "pv": "PV",
    "wind": "WIND",
    "hydro": "HYDRO",
    "syncgen": "SG",
    "battery": "BESS",
    "generator": "GEN",
    "indmach": "IM",
    "grid": "GRID",
}

# Load symbol: downward arrow (consumption).  The temporary public SLD keeps
# this conventional glyph; its orientation is the connection-side indicator,
# not a feeder-flow result.
_LOAD_SYMBOL = "path://M6,2 L18,2 L18,12 L24,12 L12,26 L0,12 L6,12 Z"
# A branch whose solved active power is effectively zero has no determined
# direction.  Do not draw an arbitrary arrow or label it as P->.
_FLOW_ZERO_TOLERANCE_KW = 1e-6
# Capacitor / shunt symbol: two parallel plates.
_CAP_SYMBOL = "path://M12,0 L12,8 M4,8 L20,8 M4,16 L20,16 M12,16 L12,24"

_EDGE_COLOR = {
    "line": "#64748b",
    "transformer": "#f59e0b",
    "regulator": "#f59e0b",
    "switch": "#10b981",
    # T-039: teal VSC stations; matches the runtime inline color below.
    "converter": "#0d9488",
}
_EVENT_STYLE = {
    "fault": {
        "color": "#ef4444",
        "symbol": "path://M16,1 L20,13 L31,13 L22,20 L26,31 L16,24 L6,31 L10,20 L1,13 L12,13 Z",
        "tag": "⚡ FAULT",
    },
    "open": {"color": "#ef4444", "symbol": "circle", "tag": "OPEN"},
    "close": {"color": "#10b981", "symbol": "circle", "tag": "CLOSED"},
    "trip_gen": {"color": "#ef4444", "symbol": "circle", "tag": "GEN TRIP"},
    "shed_load": {"color": "#f97316", "symbol": "circle", "tag": "LOAD SHED"},
    "set_tap": {"color": "#eab308", "symbol": "circle", "tag": "TAP"},
    "info": {"color": "#38bdf8", "symbol": "circle", "tag": "→"},
}


def _lane_offset(lane: int, spacing: float = 10.0) -> float:
    """Alternate parallel branch lanes so their elbows cannot cross."""
    if lane <= 0:
        return 0.0
    magnitude = ((lane + 1) // 2) * spacing
    return magnitude if lane % 2 == 0 else -magnitude


def _node_color(node: SLDNode, vmin: float, vmax: float) -> str:
    if node.kind == "substation":
        return "#3b82f6"
    values = node.v_pu.values()
    if (
        not values
        or any(phase not in node.v_pu for phase in node.phases)
        or any(not math.isfinite(value) for value in values)
        or not math.isfinite(vmin)
        or not math.isfinite(vmax)
        or vmin > vmax
    ):
        return "#94a3b8"
    # Status is a worst-phase result: checking only v_min misses an
    # overvoltage on another phase when the minimum remains in range.
    phase_min = min(values)
    phase_max = max(values)
    if phase_min < vmin:
        return "#ef4444"  # undervoltage
    if phase_max > vmax:
        return "#f59e0b"  # overvoltage
    return "#22c55e"  # in range


def _fmt_phase_label(node: SLDNode) -> str:
    names = {1: "A", 2: "B", 3: "C"}
    rows = []
    for phase in node.phases or sorted(node.v_pu):
        voltage = node.v_pu.get(phase)
        angle = node.angle_deg.get(phase)
        magnitude = f"{voltage:.3f}" if voltage is not None and math.isfinite(voltage) else "N/A"
        angle_text = f"{angle:.0f}°" if angle is not None and math.isfinite(angle) else "N/A"
        rows.append(f"{names.get(phase, str(phase))} {magnitude}∠{angle_text}")
    return "\n".join(rows)


def _fmt_voltage_label(node: SLDNode) -> str:
    v = node.v_mean
    # Keep the canvas legible for long equipment-terminal names.  The full
    # identifier remains in the hover tooltip; this compact token is only a
    # drawing label and is deterministic across engines.
    display_id = re.sub(r"^TERM\s+CONV\s+", "TC", node.id.upper())
    return f"{display_id}\n{v:.3f} pu" if v is not None else display_id


def _fmt_power(kw: float) -> str:
    """Keep equipment labels compact without changing the stored kW value."""
    if abs(kw) >= 1000:
        return f"{kw / 1000:,.0f} MW"
    return f"{kw:,.0f} kW"


# Wp19/Step-0: some engines hand back *real-world* bus coordinates (CIGRE-LV
# metres, PowerFactory project geometry) that can span millions of units but
# place neighbouring buses under a fraction of a unit apart — the whole
# network collapses to a single dot on screen.  CEPT's own deterministic
# layouts (inline/hierarchical) land in a ~0-400 drawing space, so anything
# spanning far beyond that with sub-unit nearest-neighbour gaps is raw
# real-world geometry that must be rebased onto the topology layout.  The
# renderer and the collision check share this rule so what you see is what
# the gate measures.
_REALWORLD_SPAN_CEIL = 2000.0  # beyond ~5x the 400-unit canonical drawing space
_REALWORLD_MIN_GAP_FLOOR = 2.0  # pair gap below this (raw units) is a blob


def _render_bus_positions(
    sld: SLDModel,
    *,
    _SX: float = 2.0,
    _SY: float = 2.6,
) -> dict[str, tuple[float, float]]:
    """Raw scaled bus positions, or a topology-derived readable layout.

    Engines that ship real bus coordinates (OpenDSS ``X()/Y()`` for feeders,
    PowerFactory project geometry) can produce SLDs whose nodes all collapse
    to one dot (CIGRE-LV is 907 buses span ~391000 x ~392000 but the closest
    pair is ~0.9 apart).  When the raw drawing space is not readable, this
    falls back to :func:`cept.geometry.sld_topology_layout`, keeping every
    SLD legible regardless of the source model.
    """

    def sx(n: SLDNode) -> float:
        return n.x * _SX

    def sy(n: SLDNode) -> float:
        return -n.y * _SY  # flip so the source sits on top

    raw = {n.id.lower(): (sx(n), sy(n)) for n in sld.nodes}
    if _is_readable_layout(raw):
        return raw

    from cept.domain.sld.engineering_layout import sld_topology_layout

    placed = sld_topology_layout(
        nodes=sld.nodes, edges=sld.edges, source_ids={n.id for n in sld.nodes if n.kind == "substation"}
    )
    if not placed:
        return raw
    return {name: placed[name] for name in raw if name in placed} or raw


def _is_readable_layout(pos: dict[str, tuple[float, float]]) -> bool:
    """True when the raw scaled positions can actually be read as an SLD.

    A reading hazard exists only when the drawing space is *intentional* but
    the nodes have collapsed.  Tight, hand-crafted fixtures and CEPT's own
    canonical layouts (0-400 space) are untouched; real-world coordinate
    blobs (huge span with sub-unit nearest-neighbour gaps) are rebased.
    """
    if len(pos) < 2:
        return True
    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    span = max(max(xs) - min(xs), max(ys) - min(ys))
    if span <= _REALWORLD_SPAN_CEIL:
        # Tight but intentional (tests, canonical layouts): preserve as-is.
        return True

    # Huge canvas (real-world coordinates): readable only if every pair is
    # separated enough that scaled bars/labels do not merge into one blob.
    names = sorted(pos)
    for i, name in enumerate(names):
        xi, yi = pos[name]
        for other in names[i + 1 :]:
            dx = pos[other][0] - xi
            dy = pos[other][1] - yi
            if dx * dx + dy * dy < _REALWORLD_MIN_GAP_FLOOR * _REALWORLD_MIN_GAP_FLOOR:
                return False
    return True


def build_sld_option(sld: SLDModel) -> dict:
    """Return a JSON-able dict consumed by the template's ECharts init."""
    vmin, vmax = sld.v_min_pu, sld.v_max_pu
    nodes: list[dict] = []
    links: list[dict] = []

    # ECharts graph (layout:'none') fits the node bounding box into the view,
    # so a uniform stretch only changes label-vs-symbol spacing. WP19 raised
    # the drawing-space scale to 2x: the IEEE-13 shipped coordinates place six
    # bus pairs exactly 50 units apart (its smallest grid step), and the
    # 96-unit load stub, 110-unit generator clearance and 24-unit label
    # distance all reach into a neighbour's bar there. Doubling the scale
    # (400x400 -> 800x800 drawing space, smallest step 100) clears every
    # measured overlap while keeping the 1.3 trunk-label stretch ratio; the
    # collision check mirrors _SX/_SY (src/cept/reporting/collisions.py).
    _SX, _SY = 2.0, 2.6

    pos = _render_bus_positions(sld, _SX=_SX, _SY=_SY)
    bus_pos = {n.id.lower(): pos[n.id.lower()] for n in sld.nodes}

    direction_score = {name: [0.0, 0.0] for name in bus_pos}
    for edge in sld.edges:
        src, dst = edge.src.lower(), edge.dst.lower()
        if src not in bus_pos or dst not in bus_pos:
            continue
        dx = abs(bus_pos[src][0] - bus_pos[dst][0])
        dy = abs(bus_pos[src][1] - bus_pos[dst][1])
        for bus in (src, dst):
            direction_score[bus][0] += dx
            direction_score[bus][1] += dy

    orientations: dict[str, str] = {}
    device_directions = _leaf_device_directions(sld, bus_pos)
    # Reserve a deterministic label side for crowded bus rows/columns.  The
    # old fixed right/bottom placement made adjacent buses, generators and
    # voltage text share the same pixels even though the electrical routes
    # were orthogonal.
    label_positions: dict[str, str] = {}
    for n in sld.nodes:
        name = n.id.lower()
        x, y = bus_pos[name]
        horizontal = False
        for other in sld.nodes:
            if other.id.lower() == name:
                continue
            ox, oy = bus_pos.get(other.id.lower(), (None, None))
            if ox is None:
                continue
            if abs(oy - y) <= 1.0 and abs(ox - x) < 900.0:
                horizontal = True
                break
        if horizontal:
            row = sorted(
                other.id.lower()
                for other in sld.nodes
                if abs(bus_pos[other.id.lower()][1] - y) <= 1.0
                and abs(bus_pos[other.id.lower()][0] - x) < 900.0
            )
            label_positions[name] = "top" if row.index(name) % 2 == 0 else "bottom"
        else:
            column = sorted(
                other.id.lower()
                for other in sld.nodes
                if abs(bus_pos[other.id.lower()][0] - x) <= 1.0
                and abs(bus_pos[other.id.lower()][1] - y) < 900.0
            )
            label_positions[name] = "left" if column.index(name) % 2 == 0 else "right" if column else "right"
    for n in sld.nodes:
        x, y = bus_pos[n.id.lower()]
        is_sub = n.kind == "substation"
        has_gen = bool(n.gens)
        color = _node_color(n, vmin, vmax)
        horizontal_branches, vertical_branches = direction_score[n.id.lower()]
        orientation = "vertical" if horizontal_branches > vertical_branches else "horizontal"
        orientations[n.id.lower()] = orientation
        if orientation == "vertical":
            size = [10, 44] if is_sub else [8, 38]
            label_position = label_positions.get(n.id.lower(), "bottom")
        else:
            size = [44, 10] if is_sub else [38, 8]
            label_position = label_positions.get(n.id.lower(), "right")
        phases = n.phases or sorted(n.v_pu)

        node = {
            "name": n.id,
            "category": "bus",
            "x": x,
            "y": y,
            "symbol": "rect" if is_sub else "rect",
            "symbolSize": size,
            "itemStyle": {
                "color": color,
                "borderColor": "#ec4899" if has_gen else "#ffffff",
                "borderWidth": 2.5 if has_gen else 1,
            },
            # No per-node formatter: the series-level formatter applies the
            # current toggle mode (voltage / per-phase / off) using label_v /
            # label_phase below. Device nodes keep their own formatter.
            "label": {
                "show": True,
                "position": label_position,
                "distance": 24,
                "color": "#e2e8f0",
                "fontSize": 9,
                "fontWeight": "bold",
                "textBorderColor": "#0f172a",
                "textBorderWidth": 3,
                "align": "left",
            },
            # custom payload for tooltip + label toggles
            "kind": n.kind,
            "kv": n.kv_base,
            "bus_orientation": orientation,
            "phases": phases,
            "v_pu": [n.v_pu.get(phase) for phase in phases],
            "ang": [n.angle_deg.get(phase) for phase in phases],
            "loads": [
                {"name": item.name, "kw": item.kw, "kvar": item.kvar, "shed": item.shed} for item in n.loads
            ],
            "gens": [
                {"name": g.name, "kind": g.kind, "kw": g.kw, "kvar": g.kvar, "tripped": g.tripped}
                for g in n.gens
            ],
            "shunts": [{"name": s.name, "kind": s.kind, "kvar": s.kvar} for s in n.shunts],
            "label_v": _fmt_voltage_label(n),
            "label_phase": _fmt_phase_label(n),
            "event": ({"kind": n.event.kind, "label": n.event.label} if n.event else None),
        }
        nodes.append(node)

        # --- device stubs around the bus (never overlap the bus label) ---
        _add_device_nodes(n, x, y, nodes, links, device_directions.get(n.id.lower(), (0.0, -1.0)))

        # --- event marker node (offset above-left) ---
        if n.event:
            _add_event_marker(n.event.kind, n.event.label, x - 22, y - 18, nodes)

    # --- branch edges ---
    lane_counts: dict[tuple[str, str], int] = {}
    for e in sld.edges:
        pair = tuple(sorted((e.src.lower(), e.dst.lower())))
        lane = lane_counts.get(pair, 0)
        lane_counts[pair] = lane + 1
        _add_edge(e, nodes, links, bus_pos, orientations, lane=lane)

    # center the view on the bounding box (ECharts graph 'none' anchors on the
    # origin otherwise, pushing the feeder to one side)
    xs = [n["x"] for n in nodes]
    ys = [n["y"] for n in nodes]
    center = [(min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2] if xs else [0, 0]

    return {
        "title": sld.title,
        "nodes": nodes,
        "links": links,
        "vmin": vmin,
        "vmax": vmax,
        "center": center,
        "total_loss_kw": sld.total_loss_kw,
        "legend": _legend(sld),
    }


def _leaf_device_directions(
    sld: SLDModel,
    bus_pos: dict[str, tuple[float, float]],
) -> dict[str, tuple[float, float]]:
    """Place a leaf generator outside its sole network connection."""
    neighbours: dict[str, list[str]] = {name: [] for name in bus_pos}
    for edge in sld.edges:
        src, dst = edge.src.lower(), edge.dst.lower()
        if src in neighbours and dst in neighbours:
            neighbours[src].append(dst)
            neighbours[dst].append(src)

    result: dict[str, tuple[float, float]] = {}
    for name, connected in neighbours.items():
        if len(connected) != 1:
            continue
        x, y = bus_pos[name]
        nx, ny = bus_pos[connected[0]]
        dx, dy = x - nx, y - ny
        if abs(dx) > abs(dy):
            result[name] = (1.0 if dx > 0 else -1.0, 0.0)
        elif abs(dy) > 0:
            result[name] = (0.0, 1.0 if dy > 0 else -1.0)
    return result


def _add_device_nodes(
    n: SLDNode,
    x: float,
    y: float,
    nodes: list[dict],
    links: list[dict],
    generator_direction: tuple[float, float],
) -> None:
    # loads: aggregate into ONE symbol per bus (breakdown stays in tooltip)
    if n.loads:
        total_kw = sum(item.kw for item in n.loads)
        all_shed = all(item.shed for item in n.loads)
        nm = f"__load_{n.id}"
        load_label = _fmt_power(total_kw) + (" (SHED)" if all_shed else "")
        nodes.append(
            {
                "name": nm,
                "x": x,
                "y": y + 96,
                "symbol": _LOAD_SYMBOL,
                "symbolSize": 14,
                "is_device": True,
                "itemStyle": {
                    "color": "#94a3b8" if all_shed else "#fbbf24",
                    "opacity": 0.35 if all_shed else 1.0,
                },
                "label": {
                    "show": True,
                    "position": "bottom",
                    "distance": 2,
                    "formatter": load_label,
                    "fontSize": 8.5,
                    "color": "#94a3b8" if all_shed else "#cbd5e1",
                    "textBorderColor": "#0f172a",
                    "textBorderWidth": 2,
                },
                "silent": True,
            }
        )
        links.append(
            {
                "source": n.id,
                "target": nm,
                "ignoreForceLayout": True,
                "symbol": ["none", "none"],
                "lineStyle": {"color": "#475569", "width": 1.2},
            }
        )
    # A generator at a leaf bus belongs outside the network, not always above
    # it.  This keeps G1 below the WSCC9 ring while G2/G3 remain left/right.
    gx, gy = generator_direction
    label_position = "right" if gx > 0 else "left" if gx < 0 else "bottom" if gy > 0 else "top"
    px, py = -gy, gx
    for i, g in enumerate(n.gens):
        offset = (i - (len(n.gens) - 1) / 2) * 22
        # Keep machine symbols and their labels outside the bus/branch
        # clearance envelope.  The extra clearance is intentional: the
        # report canvas is responsive and may compress a wide feeder.
        device_clearance = 110
        dx = x + gx * device_clearance + px * offset
        dy = y + gy * device_clearance + py * offset
        nm = f"__gen_{n.id}_{i}"
        gen_label = f"{_GEN_TAG.get(g.kind, 'GEN')} {_fmt_power(g.kw)}" + (" (TRIPPED)" if g.tripped else "")
        nodes.append(
            {
                "name": nm,
                "x": dx,
                "y": dy,
                "symbol": _GEN_SYMBOL.get(g.kind, _GEN_SYMBOL["generator"]),
                "symbolSize": 20,
                "is_device": True,
                "itemStyle": {
                    "color": "#94a3b8" if g.tripped else _GEN_COLOR.get(g.kind, "#a855f7"),
                    "opacity": 0.35 if g.tripped else 1.0,
                    "borderColor": "#ef4444" if g.tripped else "#0f172a",
                    "borderWidth": 2 if g.tripped else 1,
                },
                "label": {
                    "show": True,
                    "position": label_position,
                    "distance": 2,
                    "formatter": gen_label,
                    "fontSize": 8.5,
                    "fontWeight": "bold",
                    "color": "#94a3b8" if g.tripped else _GEN_COLOR.get(g.kind, "#a855f7"),
                    "textBorderColor": "#0f172a",
                    "textBorderWidth": 2,
                },
                "silent": True,
            }
        )
        links.append(
            {
                "source": nm,
                "target": n.id,
                "ignoreForceLayout": True,
                "symbol": ["none", "none"],
                "lineStyle": {"color": _GEN_COLOR.get(g.kind, "#a855f7"), "width": 1.4, "type": "solid"},
            }
        )
    # shunts to the left
    for i, s in enumerate(n.shunts):
        dx = x - 68
        dy = y + (i - (len(n.shunts) - 1) / 2) * 16
        nm = f"__shunt_{n.id}_{i}"
        nodes.append(
            {
                "name": nm,
                "x": dx,
                "y": dy,
                "symbol": _CAP_SYMBOL,
                "symbolSize": 14,
                "rotation": 90,
                "is_device": True,
                "itemStyle": {"color": "#2dd4bf"},
                "label": {
                    "show": True,
                    "position": "left",
                    "distance": 2,
                    "formatter": f"{s.kvar:.0f} kvar",
                    "fontSize": 8,
                    "color": "#5eead4",
                    "textBorderColor": "#0f172a",
                    "textBorderWidth": 2,
                },
                "silent": True,
            }
        )
        links.append(
            {
                "source": nm,
                "target": n.id,
                "ignoreForceLayout": True,
                "symbol": ["none", "none"],
                "lineStyle": {"color": "#2dd4bf", "width": 1.2},
            }
        )


def _add_event_marker(kind: str, label: str, x: float, y: float, nodes: list[dict]) -> None:
    st = _EVENT_STYLE.get(kind, _EVENT_STYLE["info"])
    nodes.append(
        {
            "name": f"__event_{kind}_{x}_{y}",
            "x": x,
            "y": y,
            "symbol": st["symbol"],
            "symbolSize": 22 if kind == "fault" else 14,
            "is_device": True,
            "itemStyle": {"color": st["color"], "borderColor": "#fff", "borderWidth": 1},
            "label": {
                "show": True,
                "position": "left",
                "distance": 3,
                "formatter": label or st["tag"],
                "fontSize": 9,
                "fontWeight": "bold",
                "color": st["color"],
                "textBorderColor": "#0f172a",
                "textBorderWidth": 3,
            },
            "silent": True,
            "z": 10,
        }
    )


def _add_edge(
    e: SLDEdge,
    nodes: list[dict],
    links: list[dict],
    bus_pos: dict[str, tuple[float, float]],
    orientations: dict[str, str],
    *,
    lane: int = 0,
) -> None:
    # orient the arrow along the actual power flow
    reverse = e.p_kw < 0
    source = e.dst if reverse else e.src
    target = e.src if reverse else e.dst
    p_mag = abs(e.p_kw)

    width = max(2.0, min(9.0, 2.0 + p_mag / 400.0))
    color = _EDGE_COLOR.get(e.kind, "#64748b")
    dashed = e.kind in ("transformer", "regulator")
    if e.status == "open":
        color = "#ef4444"
        dashed = True

    len_txt = (
        f"{e.length:.0f} {e.length_unit}".strip()
        if e.length is not None
        else ("SW" if e.kind == "switch" else "")
    )
    label_len = f"{len_txt} · {e.losses_kw:.1f} kW" if len_txt else f"{e.losses_kw:.1f} kW"
    sign = "P≈0" if abs(float(e.p_kw)) <= _FLOW_ZERO_TOLERANCE_KW else "P" + ("←" if reverse else "→")
    label_flow = f"{sign} {_fmt_power(p_mag)} · {e.q_kvar:+,.0f} kvar"

    link = {
        "source": source,
        "target": target,
        # The visible flow arrow is emitted as a separate marker on the
        # longest route segment below.  Endpoint arrows are easy to hide
        # behind a bus bar, transformer, or device symbol.
        "symbol": ["none", "none"],
        "symbolSize": [5, 11],
        "lineStyle": {
            "color": color,
            "width": width,
            "type": "dashed" if dashed else "solid",
            "opacity": 0.5 if e.status == "open" else 0.92,
            "curveness": 0,
        },
        "label_len": label_len,
        "label_flow": label_flow,
        # tooltip payload
        "edge_id": e.id,
        "el_kind": e.kind,
        "p_kw": e.p_kw,
        "q_kvar": e.q_kvar,
        "losses_kw": e.losses_kw,
        "length": e.length,
        "length_unit": e.length_unit,
        "tap": e.tap,
        "status": e.status,
        "src_bus": e.src,
        "dst_bus": e.dst,
        "event": ({"kind": e.event.kind, "label": e.event.label} if e.event else None),
    }

    source_pos = bus_pos.get(source.lower())
    target_pos = bus_pos.get(target.lower())
    if not source_pos or not target_pos:
        link["route_points"] = []
        links.append(link)
        return

    # Separate parallel branches into deterministic Manhattan lanes.
    if lane and (source_pos[0] == target_pos[0] or source_pos[1] == target_pos[1]):
        offset = _lane_offset(lane)
        if source_pos[1] == target_pos[1]:
            route = [
                source_pos,
                (source_pos[0], source_pos[1] + offset),
                (target_pos[0], target_pos[1] + offset),
                target_pos,
            ]
        else:
            route = [
                source_pos,
                (source_pos[0] + offset, source_pos[1]),
                (target_pos[0] + offset, target_pos[1]),
                target_pos,
            ]
        bend_a, bend_b = route[1], route[2]
        bend_a_name, bend_b_name = f"__bend_{e.id}_a", f"__bend_{e.id}_b"
        for name, point in ((bend_a_name, bend_a), (bend_b_name, bend_b)):
            nodes.append(
                {
                    "name": name,
                    "x": point[0],
                    "y": point[1],
                    "symbolSize": 1,
                    "is_device": True,
                    "is_bend": True,
                    "silent": True,
                    "itemStyle": {"opacity": 0},
                    "label": {"show": False},
                }
            )
        parts = [
            dict(link, source=source, target=bend_a_name, symbol=["none", "none"]),
            dict(link, source=bend_a_name, target=bend_b_name, symbol=["none", "none"]),
            dict(link, source=bend_b_name, target=target),
        ]
        for part, left, right in zip(parts, route, route[1:]):
            part["route_points"] = [left, right]
        parts[1]["label_len"], parts[1]["label_flow"] = "", ""
        links.extend(parts)
        _add_flow_arrow(e, nodes, route, color=color)
        return
    if source_pos[0] == target_pos[0] or source_pos[1] == target_pos[1]:
        link["route_points"] = [source_pos, target_pos]
        links.append(link)
        _add_flow_arrow(e, nodes, [source_pos, target_pos], color=color)
        return

    # One invisible elbow gives the engine-neutral view PowerFactory-like
    # orthogonal routing while keeping the canonical topology unchanged.
    if orientations.get(source.lower()) == "horizontal":
        bend_x, bend_y = source_pos[0], target_pos[1]
    else:
        bend_x, bend_y = target_pos[0], source_pos[1]
    if lane:
        offset = _lane_offset(lane)
        bend_x += offset if abs(source_pos[0] - target_pos[0]) > 1e-9 else 0.0
        bend_y += offset if abs(source_pos[1] - target_pos[1]) > 1e-9 else 0.0
    bend = f"__bend_{e.id}"
    nodes.append(
        {
            "name": bend,
            "x": bend_x,
            "y": bend_y,
            "symbolSize": 1,
            "is_device": True,
            "is_bend": True,
            "silent": True,
            "itemStyle": {"opacity": 0},
            "label": {"show": False},
        }
    )
    first = dict(link, source=source, target=bend, symbol=["none", "none"])
    second = dict(link, source=bend, target=target)
    first["route_points"] = [source_pos, (bend_x, bend_y)]
    second["route_points"] = [(bend_x, bend_y), target_pos]
    first_length = abs(source_pos[0] - bend_x) + abs(source_pos[1] - bend_y)
    second_length = abs(target_pos[0] - bend_x) + abs(target_pos[1] - bend_y)
    unlabeled = first if second_length >= first_length else second
    unlabeled["label_len"] = ""
    unlabeled["label_flow"] = ""
    links.extend((first, second))
    _add_flow_arrow(e, nodes, [source_pos, (bend_x, bend_y), target_pos], color=color)


def _add_flow_arrow(
    edge: SLDEdge,
    nodes: list[dict],
    route: list[tuple[float, float]],
    *,
    color: str,
) -> None:
    """Place a visible, direction-aware flow marker away from terminals.

    ECharts draws graph links underneath graph nodes.  An arrowhead attached
    to the target terminal can therefore disappear underneath a bus bar or a
    transformer symbol.  A standalone marker on the longest Manhattan
    segment keeps the direction visible without changing electrical topology.
    """
    if edge.status == "open" or abs(float(edge.p_kw)) <= _FLOW_ZERO_TOLERANCE_KW or len(route) < 2:
        return
    segments = [
        (left, right, abs(right[0] - left[0]) + abs(right[1] - left[1]))
        for left, right in zip(route, route[1:])
    ]
    left, right, length = max(segments, key=lambda item: item[2])
    if length <= 1e-9:
        return
    mid_x = (left[0] + right[0]) / 2.0
    mid_y = (left[1] + right[1]) / 2.0
    angle = math.degrees(math.atan2(right[1] - left[1], right[0] - left[0]))
    nodes.append(
        {
            "name": f"__flow_arrow_{edge.id}",
            "x": mid_x,
            "y": mid_y,
            "symbol": "arrow",
            "symbolRotate": angle,
            "symbolSize": 12 if length >= 16.0 else 8,
            "is_device": True,
            "is_flow_arrow": True,
            "silent": True,
            "z": 20,
            "itemStyle": {"color": color, "borderColor": "#0f172a", "borderWidth": 1},
            "label": {"show": False},
        }
    )


def _legend(sld: SLDModel) -> list[dict]:
    used_gen = sorted({g.kind for n in sld.nodes for g in n.gens})
    items = [
        {"label": "Substation", "color": "#3b82f6", "shape": "rect"},
        {"label": "Bus (in-range V)", "color": "#22c55e", "shape": "rect"},
        {"label": "Undervoltage", "color": "#ef4444", "shape": "rect"},
        {"label": "Overvoltage", "color": "#f59e0b", "shape": "rect"},
        {"label": "Load", "color": "#fbbf24", "shape": "load"},
        {"label": "Capacitor", "color": "#2dd4bf", "shape": "cap"},
    ]
    for k in used_gen:
        items.append({"label": _GEN_TAG.get(k, k), "color": _GEN_COLOR.get(k, "#a855f7"), "shape": "gen"})
    return items


def render_sld_png(sld: SLDModel, out_path, *, dpi: int = 110):
    """Rasterize an :class:`SLDModel` to a PNG with no GUI (matplotlib Agg).

    A headless alternative to PowerFactory's ``ComWr`` native raster, which
    needs an active GUI graphics frame: this draws the same node/edge geometry
    the interactive SLD uses, so an SLD image is always available in
    headless / CI / conduct sessions.  Node colour flags voltage against the
    ``[v_min_pu, v_max_pu]`` band; open branches are dashed.
    """
    from pathlib import Path
    import matplotlib

    matplotlib.use("Agg")  # headless: never opens a window
    import matplotlib.pyplot as plt

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    pos = {n.id.lower(): (float(n.x), -float(n.y)) for n in sld.nodes}  # flip y: source on top
    fig, ax = plt.subplots(figsize=(11, 8))
    for e in sld.edges:
        s, d = pos.get(e.src.lower()), pos.get(e.dst.lower())
        if s is None or d is None:
            continue
        if len(e.route_points) >= 2:
            xs = [float(p[0]) for p in e.route_points]
            ys = [-float(p[1]) for p in e.route_points]
        else:
            xs, ys = [s[0], d[0]], [s[1], d[1]]
        closed = e.status == "closed"
        ax.plot(
            xs,
            ys,
            color="#64748b" if closed else "#cbd5e1",
            linewidth=1.6 if closed else 1.1,
            linestyle="-" if closed else "--",
            zorder=1,
        )
    for n in sld.nodes:
        x, y = pos[n.id.lower()]
        vmean = n.v_mean
        color = (
            "#94a3b8"
            if vmean is None
            else "#ef4444"
            if (vmean < sld.v_min_pu or vmean > sld.v_max_pu)
            else "#16a34a"
        )
        marker = "s" if n.kind == "substation" else "o"
        ax.scatter([x], [y], s=90, c=color, marker=marker, edgecolors="#1e293b", linewidths=0.8, zorder=2)
        label = n.id if vmean is None else f"{n.id}\n{vmean:.3f} pu"
        ax.annotate(
            label, (x, y), textcoords="offset points", xytext=(6, 6), fontsize=7, color="#0f172a", zorder=3
        )
    ax.set_title(sld.title or "Single-Line Diagram", fontsize=11)
    ax.set_aspect("equal", adjustable="datalim")
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(out, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out
