"""Measured SLD symbol-overlap (collision) check — WP19.

``SLDModel`` nodes carry no symbol geometry, so each node's footprint is
derived from the layout constants the renderer actually uses
(``src/cept/reporting/sld.py`` and the shared SLD template) rather than from
invented values.  The layout-rules doc ``docs/cept/SLD_LAYOUT_RULES.md``
defines the geometry contract (Manhattan routing, no unannounced overlap);
this module turns that contract into a measurement.

Model (documented assumptions)
------------------------------
* Coordinate convention: ``SLDModel`` x/y layout units mapped through the
  renderer's spacing scale before the graph canvas fits the bounding box
  (``build_sld_option`` in ``reporting/sld.py`` multiplies x by ``_SX`` and
  y by ``_SY``).  This check mirrors that mapping — node positions scale,
  symbol/label extents do not, exactly as in the renderer.  The scale was
  introduced at 2x in WP19 so the IEEE-13 drawing space becomes 800x800 and
  its smallest 50-unit grid step becomes 100 units, which is what separates
  the bar/label/device footprints that used to collide.
* Footprint = bounding box of the bus-bar rectangle **and** its label box
  (union).  Label sizes are pixel estimates at the renderer's declared font
  sizes (9 px bus labels, 8.5 px device labels, ``distance`` 24 from the bar
  edge) — estimated glyph extents, not measured text metrics.
* Label side follows the renderer's default: a vertical bus bar labels
  ``bottom``, a horizontal one labels ``right``; the renderer's
  crowding-based side selection is not modelled (the check is deliberately
  conservative on the default side).
* Device symbols are modelled where the layout rules place them
  deterministically: the aggregate load symbol 96 units below its bus, the
  capacitor symbol 68 units to the left, and leaf-bus generators 110 units
  away from the network (replicating ``sld.py``).  Non-leaf generators are
  not modelled.
* Severity: overlap area >= ``SEVERITY_MAJOR_FRACTION`` of the smaller
  footprint is *major* (symbols/labels corrupt each other); anything below
  is *minor* (crowding).  Verdicts are fail-closed: any major overlap
  blocks, minor-only is a warning.

The check never alters the layout: it reports so a report/SLD pass can act.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from cept.schema.sld import SLDModel

# --- layout constants mirrored from src/cept/reporting/sld.py ---------------
# Renderer drawing-space scale: build_sld_option maps node x/y through
# _SX/_SY before the canvas fits the bounding box.  Keep in sync with
# reporting/sld.py — the check measures the renderer's actual drawing space.
RENDER_SCALE: tuple[float, float] = (2.0, 2.6)  # (x, y) — WP19 2x spacing fix
BUS_BAR_SIZE: dict[str, tuple[float, float]] = {
    "bus": (38.0, 8.0),  # (width, height) of a horizontal bus bar
    "substation": (44.0, 10.0),
}
LABEL_DISTANCE = 24.0  # ECharts/Plotly label distance from the bar edge
LABEL_FONT_SIZE = 9.0
LABEL_CHAR_WIDTH = 0.6 * LABEL_FONT_SIZE  # ~0.6 em per glyph (estimate)
LABEL_LINE_HEIGHT = 1.3 * LABEL_FONT_SIZE
LABEL_BORDER = 3.0  # textBorderWidth
DEVICE_LABEL_DISTANCE = 2.0
DEVICE_FONT_SIZE = 8.5
DEVICE_SYMBOL_SIZE: dict[str, float] = {"load": 14.0, "cap": 14.0, "gen": 20.0}
LOAD_STUB_Y = 96.0  # aggregate load symbol hangs 96 units below the bus
SHUNT_OFFSET_X = 68.0  # shunt symbol sits 68 units left of the bus
GEN_CLEARANCE = 110.0  # leaf generator clearance (device_clearance in sld.py)
SEVERITY_MAJOR_FRACTION = 0.25


@dataclass(frozen=True)
class Box:
    """Axis-aligned bounding box in layout units."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def area(self) -> float:
        return max(0.0, self.x1 - self.x0) * max(0.0, self.y1 - self.y0)

    def overlap(self, other: "Box") -> float:
        x = min(self.x1, other.x1) - max(self.x0, other.x0)
        y = min(self.y1, other.y1) - max(self.y0, other.y0)
        return max(0.0, x) * max(0.0, y)


def _bus_orientation(
    sld: SLDModel, *, bus_pos: dict[str, tuple[float, float]] | None = None
) -> dict[str, str]:
    """Same rule as ``sld.py``: the bar runs perpendicular to the dominant
    branch spread (vertical bar when branches spread horizontally).  The
    renderer computes the spread on the scaled drawing space (``_SX``/``_SY``),
    so this mirrors the same scaled positions."""
    if bus_pos is None:
        bus_pos = {
            n.id.lower(): (float(n.x) * RENDER_SCALE[0], float(n.y) * RENDER_SCALE[1]) for n in sld.nodes
        }
    pos = {name: tuple(v) for name, v in bus_pos.items()}
    score = {name: [0.0, 0.0] for name in pos}
    for edge in sld.edges:
        src, dst = edge.src.lower(), edge.dst.lower()
        if src not in pos or dst not in pos:
            continue
        dx = abs(pos[src][0] - pos[dst][0])
        dy = abs(pos[src][1] - pos[dst][1])
        for bus in (src, dst):
            score[bus][0] += dx
            score[bus][1] += dy
    return {name: "vertical" if score[name][0] > score[name][1] else "horizontal" for name in pos}


def _neighbours(sld: SLDModel) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for edge in sld.edges:
        src, dst = edge.src.lower(), edge.dst.lower()
        result.setdefault(src, []).append(dst)
        result.setdefault(dst, []).append(src)
    return result


def _label_size(node_id: str, v_mean: float | None, *, v_pu: bool = False) -> tuple[float, float]:
    """Estimated label box (width, height) in layout units for a bus label.

    Mirrors ``sld.py``'s ``_fmt_voltage_label``: ``ID`` or ``ID\\nV pu``.
    """
    display_id = node_id.upper()
    lines = [display_id]
    if v_mean is not None:
        lines.append(f"{v_mean:.3f} pu")
    width = max(len(line) for line in lines) * LABEL_CHAR_WIDTH + 2 * LABEL_BORDER
    height = len(lines) * LABEL_LINE_HEIGHT + 2 * LABEL_BORDER
    return width, height


def _leaf_gen_direction(
    sld: SLDModel, bus_pos: dict[str, tuple[float, float]]
) -> dict[str, tuple[float, float]]:
    """Replicate ``sld.py._leaf_device_directions``: a leaf generator is
    placed outside its sole network connection."""
    neighbours = _neighbours(sld)
    result: dict[str, tuple[float, float]] = {}
    for name, connected in neighbours.items():
        if len(connected) != 1:
            continue
        x, y = bus_pos[name]
        nx, ny = bus_pos.get(connected[0], (x, y))
        dx, dy = x - nx, y - ny
        if abs(dx) > abs(dy):
            result[name] = (1.0 if dx > 0 else -1.0, 0.0)
        elif abs(dy) > 0:
            result[name] = (0.0, 1.0 if dy > 0 else -1.0)
    return result


def node_footprints(
    sld: SLDModel,
    *,
    symbol_extent: float | None = None,
    positions: dict[str, tuple[float, float]] | None = None,
) -> list[tuple[str, Box]]:
    """Return ``(owner, box)`` footprints for every node of the SLD.

    ``symbol_extent`` overrides the modelled bus-bar extent (a square of that
    side, labels/devices omitted) so tests can exercise the check with
    explicit symbol sizes; ``None`` uses the documented layout constants.
    The ``symbol_extent`` path keeps the raw model coordinates (it exists to
    probe overlap geometry directly, independent of the renderer's scale).

    ``positions`` optionally provides the renderer's *final* scaled layout
    (documented scale + any readability rebase from
    :func:`cept.reporting.sld._render_bus_positions`).  When supplied, the
    check measures exactly what the renderer draws; otherwise it applies the
    documented ``RENDER_SCALE`` itself.
    """
    if symbol_extent is not None:
        half = symbol_extent / 2.0
        return [
            (n.id, Box(float(n.x) - half, float(n.y) - half, float(n.x) + half, float(n.y) + half))
            for n in sld.nodes
        ]

    if positions is not None:
        bus_pos = {
            n.id.lower(): positions.get(
                n.id.lower(), (float(n.x) * RENDER_SCALE[0], float(n.y) * RENDER_SCALE[1])
            )
            for n in sld.nodes
        }
    else:
        bus_pos = {
            n.id.lower(): (float(n.x) * RENDER_SCALE[0], float(n.y) * RENDER_SCALE[1]) for n in sld.nodes
        }
    orientation = _bus_orientation(sld, bus_pos=bus_pos)
    gen_direction = _leaf_gen_direction(sld, bus_pos)
    neighbours = _neighbours(sld)
    dense_network = len(sld.nodes) > 24
    ordered_names = sorted(
        (n.id.lower() for n in sld.nodes),
        key=lambda key: (round(bus_pos[key][1], 6), round(bus_pos[key][0], 6), key),
    )
    label_index = {name: index for index, name in enumerate(ordered_names)}
    footprints: list[tuple[str, Box]] = []

    def add(owner: str, box: Box) -> None:
        footprints.append((owner, box))

    for n in sld.nodes:
        x, y = bus_pos[n.id.lower()]
        w, h = BUS_BAR_SIZE.get(n.kind, BUS_BAR_SIZE["bus"])
        if orientation.get(n.id.lower()) == "vertical":
            w, h = h, w
        bar = Box(x - w / 2, y - h / 2, x + w / 2, y + h / 2)
        add(f"{n.id} (bar)", bar)

        degree = len(neighbours.get(n.id.lower(), ()))
        important = n.kind == "substation" or degree > 2 or bool(n.gens)
        label_visible = not dense_network or important or (not n.loads and label_index[n.id.lower()] % 5 == 0)
        if label_visible:
            label_w, label_h = _label_size(n.id, n.v_mean)
            label_side = "bottom" if orientation.get(n.id.lower()) == "vertical" else "right"
            if label_side == "bottom":
                add(
                    f"{n.id} (label)",
                    Box(
                        x - label_w / 2,
                        y - h / 2 - LABEL_DISTANCE - label_h,
                        x + label_w / 2,
                        y - h / 2 - LABEL_DISTANCE,
                    ),
                )
            else:
                add(
                    f"{n.id} (label)",
                    Box(
                        x + w / 2 + LABEL_DISTANCE,
                        y - label_h / 2,
                        x + w / 2 + LABEL_DISTANCE + label_w,
                        y + label_h / 2,
                    ),
                )

        # Aggregate load symbol hangs 96 units below the bus (sld.py).
        if n.loads:
            s = DEVICE_SYMBOL_SIZE["load"]
            lx, ly = x, y - LOAD_STUB_Y
            add(f"{n.id} (load)", Box(lx - s / 2, ly - s / 2, lx + s / 2, ly + s / 2))

        # Capacitor/shunt symbol 68 units left of the bus (sld.py).
        for i, shunt in enumerate(n.shunts):
            s = DEVICE_SYMBOL_SIZE["cap"]
            dx = x - SHUNT_OFFSET_X
            dy = y + (i - (len(n.shunts) - 1) / 2.0) * 16.0
            add(f"{n.id} (shunt:{shunt.name})", Box(dx - s / 2, dy - s / 2, dx + s / 2, dy + s / 2))

        # Leaf generators sit 110 units outside the network (sld.py).
        gx, gy = gen_direction.get(n.id.lower(), (0.0, 1.0))
        s = DEVICE_SYMBOL_SIZE["gen"]
        for i, gen in enumerate(n.gens):
            offset = (i - (len(n.gens) - 1) / 2.0) * 22.0
            px, py = -gy, gx
            dx = x + gx * GEN_CLEARANCE + px * offset
            dy = y + gy * GEN_CLEARANCE + py * offset
            add(f"{n.id} (gen:{gen.name})", Box(dx - s / 2, dy - s / 2, dx + s / 2, dy + s / 2))

    return footprints


def _legacy_gridlink_pseudo_nodes(sld: SLDModel) -> set[str]:
    """Return compatibility source buses removed by the browser view."""
    physical = {
        node.id.lower()
        for node in sld.nodes
        if any(getattr(gen, "kind", None) == "grid" for gen in node.gens)
    }
    pseudo: set[str] = set()
    for edge in sld.edges:
        if not str(edge.id).startswith("GridLink."):
            continue
        a, b = edge.src.lower(), edge.dst.lower()
        if a in physical and b not in physical:
            pseudo.add(b)
        elif b in physical and a not in physical:
            pseudo.add(a)
    return pseudo


def _rendered_footprints(sld: SLDModel) -> list[tuple[str, Box]]:
    """Measure the actual v2 renderer nodes, labels and glyph footprints."""
    from cept.reporting.sld_runtime import build_sld_option_v2

    option = build_sld_option_v2(sld)
    pseudo_nodes = _legacy_gridlink_pseudo_nodes(sld)
    footprints: list[tuple[str, Box]] = []

    def size(node: dict[str, Any]) -> tuple[float, float]:
        raw = node.get("symbolSize", 0.0)
        if isinstance(raw, (list, tuple)) and len(raw) >= 2:
            return float(raw[0]), float(raw[1])
        value = float(raw or 0.0)
        return value, value

    def add(owner: str, x: float, y: float, width: float, height: float) -> None:
        footprints.append((owner, Box(x - width / 2, y - height / 2, x + width / 2, y + height / 2)))

    for node in option.get("nodes", []):
        if node.get("is_flow_arrow"):
            continue
        if node.get("category") == "bus" and str(node.get("name", "")).lower() in pseudo_nodes:
            continue
        try:
            x, y = float(node["x"]), float(node["y"])
        except (KeyError, TypeError, ValueError):
            continue
        width, height = size(node)
        name = str(node.get("name", "node"))
        if width > 0.0 and height > 0.0:
            role = "bar" if node.get("category") == "bus" else "device"
            add(f"{name} ({role})", x, y, width, height)

        label = node.get("label") if isinstance(node.get("label"), dict) else {}
        visible = bool(label.get("show", False))
        if node.get("category") == "bus":
            visible = visible and bool(node.get("label_default_visible", True))
        text = str(node.get("label_v") or label.get("formatter") or "").strip()
        if not visible or not text:
            continue
        font = float(label.get("fontSize", LABEL_FONT_SIZE) or LABEL_FONT_SIZE)
        lines = text.splitlines() or [text]
        label_w = max(len(line) for line in lines) * 0.6 * font + 2 * LABEL_BORDER
        label_h = len(lines) * 1.3 * font + 2 * LABEL_BORDER
        distance = float(label.get("distance", 0.0) or 0.0)
        position = str(label.get("position", "right"))
        if position == "left":
            lx = x - width / 2 - distance - label_w / 2
            ly = y
        elif position == "top":
            lx = x
            ly = y - height / 2 - distance - label_h / 2
        elif position == "bottom":
            lx = x
            ly = y + height / 2 + distance + label_h / 2
        else:
            lx = x + width / 2 + distance + label_w / 2
            ly = y
        add(f"{name} (label)", lx, ly, label_w, label_h)
    return footprints


def _collision_record(
    sld: SLDModel,
    boxes: list[tuple[str, Box]],
    *,
    model: str,
) -> dict[str, Any]:
    per_node: dict[str, list[Box]] = {}
    for owner, box in boxes:
        per_node.setdefault(owner, []).append(box)

    overlaps: list[dict[str, Any]] = []
    for index, (owner_a, boxes_a) in enumerate(per_node.items()):
        for owner_b, boxes_b in list(per_node.items())[index + 1 :]:
            node_a = owner_a.rsplit(" (", 1)[0]
            node_b = owner_b.rsplit(" (", 1)[0]
            if node_a == node_b:
                continue
            best = 0.0
            for ba in boxes_a:
                for bb in boxes_b:
                    best = max(best, ba.overlap(bb))
            if best <= 0.0:
                continue
            area_a = min(b.area for b in boxes_a)
            area_b = min(b.area for b in boxes_b)
            severity = "major" if best >= SEVERITY_MAJOR_FRACTION * min(area_a, area_b) else "minor"
            overlaps.append(
                {
                    "pair": [owner_a, owner_b],
                    "area": round(best, 1),
                    "severity": severity,
                }
            )

    major = sum(1 for o in overlaps if o["severity"] == "major")
    minor = sum(1 for o in overlaps if o["severity"] == "minor")
    return {
        "verdict": "pass" if not overlaps else "blocked" if major else "warning",
        "overlap_count": len(overlaps),
        "major_count": major,
        "minor_count": minor,
        "overlaps": overlaps,
        "model": model,
        "n_nodes": len(sld.nodes),
        "n_edges": len(sld.edges),
    }


def collision_check(
    sld: SLDModel,
    *,
    symbol_extent: float | None = None,
    positions: dict[str, tuple[float, float]] | None = None,
) -> dict[str, Any]:
    """Measure low-level symbol/label overlap using the documented box model."""
    boxes = node_footprints(sld, symbol_extent=symbol_extent, positions=positions)
    return _collision_record(sld, boxes, model="unit-boxes-v2")


def rendered_collision_check(sld: SLDModel) -> dict[str, Any]:
    """Measure the effective renderer geometry without weakening fail-closed checks.

    Small diagrams keep the direct layout collision contract so malformed or
    coincident source coordinates cannot be hidden by an automatic re-layout.
    Dense diagrams use the final v2 renderer footprints because its deliberate
    landmark/hover policy suppresses most leaf labels at overview scale.
    """
    if len(sld.nodes) <= 24:
        return collision_check(sld, positions=_render_positions(sld))
    return _collision_record(
        sld,
        _rendered_footprints(sld),
        model="renderer-v2-footprints",
    )


def collision_report_over_results(result_paths: Sequence[Path | str]) -> list[dict[str, Any]]:
    """Run :func:`collision_check` over a set of solver ``results.json`` files.

    A report/SLD pass can feed the benchmark run directories' ``results.json``
    here to get a per-case collision verdict without touching the layout.
    """
    import json

    from cept.schema.result import StudyResult

    report: list[dict[str, Any]] = []
    for path in result_paths:
        p = Path(path)
        try:
            result = StudyResult.model_validate(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError) as exc:
            report.append({"path": str(p), "verdict": "blocked", "error": str(exc)})
            continue
        entry: dict[str, Any] = {"path": str(p), "case": result.case_name}
        if result.sld is None or not result.sld.nodes:
            entry.update({"verdict": "pass", "overlap_count": 0, "major_count": 0, "minor_count": 0})
        else:
            entry.update(rendered_collision_check(result.sld))
        report.append(entry)
    return report


def _render_positions(sld: SLDModel) -> dict[str, tuple[float, float]]:
    """The renderer's final scaled layout for an SLD (same as the HTML)."""
    from cept.reporting.sld import _render_bus_positions

    return _render_bus_positions(sld)


__all__ = [
    "Box",
    "collision_check",
    "collision_report_over_results",
    "node_footprints",
    "rendered_collision_check",
]
