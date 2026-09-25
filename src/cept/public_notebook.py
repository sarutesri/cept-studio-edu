"""Notebook-native CEPT result views for the public teaching surface.

The renderer is deliberately dependency-light: it consumes the persisted
:class:`StudyResult` view model and emits self-contained HTML/SVG.  It never
runs a solver, recomputes engineering quantities, or invents missing channels.

Interactive behaviour is browser-native (SVG hover titles and HTML details),
which keeps the output usable in Google Colab without an extra JavaScript or
plotting-library dependency.
"""

from __future__ import annotations

import html
import json
import math
from pathlib import Path
from typing import Callable, Iterable, Sequence

from cept.domain.sld.layout_contract import canonical_sld_edge_id
from cept.domain.sld.plan import CanonicalSLDPlan, build_canonical_sld_plan
from cept.public import verify_study
from cept.schema.result import DynamicsResult, StudyResult
from cept.schema.sld import SLDModel, SLDNode

def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _finite(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _fmt(value: object, digits: int = 4) -> str:
    number = _finite(value)
    if number is None:
        return "—"
    return f"{number:.{digits}f}"


def _status(node: SLDNode, sld: SLDModel) -> str:
    values = [value for value in node.v_pu.values() if _finite(value) is not None]
    if not values:
        return "NO DATA"
    low = any(value < sld.v_min_pu for value in values)
    high = any(value > sld.v_max_pu for value in values)
    if low and high:
        return "OUT"
    if low:
        return "UNDER"
    if high:
        return "OVER"
    return "OK"


def _status_class(status: str) -> str:
    return {
        "OK": "ok",
        "UNDER": "under",
        "OVER": "over",
        "OUT": "out",
        "NO DATA": "nodata",
    }.get(status, "nodata")


def _phase_text(node: SLDNode, phase: int) -> str:
    value = node.v_pu.get(phase)
    angle = node.angle_deg.get(phase)
    if value is None:
        return "—"
    if angle is None:
        return f"{value:.4f} pu"
    return f"{value:.4f} pu @ {angle:.2f}°"



def _terminal_symbol(
    kind: str,
    x: float,
    y: float,
    label: str,
    detail: str,
    bus_y: float | None = None,
) -> str:
    """Render one compact terminal symbol with a hover label."""
    normalized = str(kind or "generator").lower()
    symbol = {
        "grid": "external-grid",
        "indmach": "motor",
        "syncgen": "generator",
        "generator": "generator",
    }.get(normalized, normalized)
    glyph = {
        "external-grid": "G",
        "motor": "M",
        "pv": "PV",
        "wind": "W",
        "hydro": "H",
        "battery": "B",
        "generator": "G",
    }.get(symbol, "G")
    if symbol == "load":
        shape = '<polygon points="-10,-6 10,-6 0,10" fill="#f8fafc" stroke="#0f172a" stroke-width="2"/>'
        text = ""
    elif symbol == "external-grid":
        shape = '<circle cx="0" cy="0" r="10" fill="#f8fafc" stroke="#0f172a" stroke-width="2"/><path d="M-7,-7 L7,7 M7,-7 L-7,7" stroke="#0f172a" stroke-width="2"/>'
        text = '<text x="0" y="25" text-anchor="middle">G</text>'
    elif symbol == "motor":
        shape = '<circle cx="0" cy="0" r="10" fill="#f8fafc" stroke="#0f172a" stroke-width="2"/>'
        text = '<text x="0" y="4" text-anchor="middle">M</text>'
    elif symbol in {"capacitor", "reactor", "statcom", "svc"}:
        shape = '<path d="M-9,-5 H9 M-9,0 H9 M-9,5 H9" stroke="#0f172a" stroke-width="2"/>'
        text = ""
    else:
        shape = '<circle cx="0" cy="0" r="10" fill="#f8fafc" stroke="#0f172a" stroke-width="2"/>'
        text = f'<text x="0" y="4" text-anchor="middle">{glyph}</text>'
    lead = ""
    if bus_y is not None:
        lead = (
            f'<line class="cept-terminal-lead" x1="0" y1="0" x2="0" '
            f'y2="{bus_y - y:.1f}" stroke="#0f172a" stroke-width="2"/>'
        )
    tooltip = f"{label}: {detail}"
    return (
        f'<g class="cept-terminal-symbol" data-symbol="{_esc(symbol)}" '
        f'transform="translate({x:.1f} {y:.1f})">'
        f"<title>{_esc(tooltip)}</title>{lead}{shape}{text}</g>"
    )


def _terminal_symbol_parts(
    sld: SLDModel, positions: dict[str, tuple[float, float]]
) -> list[str]:
    parts: list[str] = []
    for node in sld.nodes:
        x, y = positions.get(node.id.lower(), (0.0, 0.0))
        for index, generator in enumerate(node.gens):
            parts.append(
                _terminal_symbol(
                    str(generator.kind).lower(),
                    x,
                    y - 42 - index * 26,
                    generator.name,
                    f"{generator.kw:.1f} kW",
                    bus_y=y,
                )
            )
        for index, load in enumerate(node.loads):
            parts.append(
                _terminal_symbol(
                    "load",
                    x,
                    y + 42 + index * 26,
                    load.name,
                    f"{load.kw:.1f} kW",
                    bus_y=y,
                )
            )
        for index, shunt in enumerate(node.shunts):
            parts.append(
                _terminal_symbol(
                    shunt.kind,
                    x,
                    y + 42 + (len(node.loads) + index) * 26,
                    shunt.name,
                    f"{shunt.kvar:.1f} kvar",
                    bus_y=y,
                )
            )
    return parts

def _bus_tooltip(node: SLDNode, sld: SLDModel) -> str:
    rows = [f"Bus {node.id}", f"Status: {_status(node, sld)}"]
    for phase, label in ((1, "A"), (2, "B"), (3, "C")):
        if phase in node.v_pu:
            rows.append(f"{label}: {_phase_text(node, phase)}")
    if node.loads:
        rows.append(
            "Loads: "
            + ", ".join(f"{item.name} {item.kw:.1f} kW" for item in node.loads)
        )
    if node.gens:
        rows.append(
            "Generation: "
            + ", ".join(f"{item.name} {item.kw:.1f} kW" for item in node.gens)
        )
    if node.event is not None:
        label = node.event.label or node.event.kind
        rows.append(f"Event: {label}")
    return "\n".join(rows)


def _canonical_plan_view(
    sld: SLDModel, *, width: float, height: float
) -> tuple[CanonicalSLDPlan, dict[str, tuple[float, float]], dict[str, list[tuple[float, float]]]]:
    """Project the canonical persisted-SLD plan into the notebook viewport."""

    plan = build_canonical_sld_plan(sld)
    points = [bus.center.as_tuple() for bus in plan.geometry.buses]
    points.extend(point.as_tuple() for route in plan.geometry.routes for point in route.points)
    if not points:
        return plan, {}, {}

    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    dx = max(x1 - x0, 1.0)
    dy = max(y1 - y0, 1.0)
    pad_x, pad_y = 70.0, 55.0
    usable_w = width - 2 * pad_x
    usable_h = height - 2 * pad_y

    def scale(point: tuple[float, float]) -> tuple[float, float]:
        return (
            pad_x + (point[0] - x0) / dx * usable_w,
            pad_y + (point[1] - y0) / dy * usable_h,
        )

    positions = {bus.bus_id.lower(): scale(bus.center.as_tuple()) for bus in plan.geometry.buses}
    routes = {
        route.edge_id.lower(): [scale(point.as_tuple()) for point in route.points]
        for route in plan.geometry.routes
    }
    return plan, positions, routes


def _sld_svg(sld: SLDModel) -> str:
    width, height = 1000.0, 560.0
    _plan, positions, routes = _canonical_plan_view(sld, width=width, height=height)
    if not positions:
        return '<div class="cept-empty">No SLD geometry is available for this result.</div>'

    edge_parts: list[str] = []
    for edge in sld.edges:
        points = routes.get(canonical_sld_edge_id(edge.id).lower(), [])
        if len(points) < 2:
            continue
        serialized = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        tooltip = (
            f"{edge.id}\n{edge.src} → {edge.dst}\n"
            f"P={edge.p_kw:.2f} kW, Q={edge.q_kvar:.2f} kvar\n"
            f"Loss={edge.losses_kw:.3f} kW\nStatus={edge.status}"
        )
        css = " edge-open" if edge.status == "open" else ""
        edge_parts.append(
            f'<polyline class="cept-edge{css}" points="{serialized}">'
            f"<title>{_esc(tooltip)}</title></polyline>"
        )

    node_parts: list[str] = []
    dense = len(sld.nodes) >= 13
    for node in sld.nodes:
        x, y = positions.get(node.id.lower(), (0.0, 0.0))
        status = _status(node, sld)
        css = _status_class(status)
        label = node.id if len(node.id) <= 14 else node.id[:13] + "…"
        kind = "substation" if node.kind == "substation" else "bus"
        event_mark = " ⚠" if node.event is not None else ""
        node_parts.append(
            f'<g class="cept-bus {css} {kind}" tabindex="0" '
            f'aria-label="Bus {_esc(node.id)}, status {_esc(status)}">'
            f"<title>{_esc(_bus_tooltip(node, sld))}</title>"
            f'<rect x="{x - 33:.1f}" y="{y - 12:.1f}" width="66" height="24" rx="5"/>'
            f'<text x="{x:.1f}" y="{y + 4:.1f}" text-anchor="middle">{_esc(label)}</text>'
            + (
                ""
                if dense and node.kind != "substation"
                else f'<text class="cept-bus-status-label" x="{x:.1f}" y="{y + 28:.1f}" text-anchor="middle">{_esc(status + event_mark)}</text>'
            )
            + "</g>"
        )

    return (
        '<div class="cept-sld-wrap">'
        '<div class="cept-sld-help">Hover or focus a bus to inspect solver-returned phase voltage and angle.</div>'
        f'<svg class="cept-sld" viewBox="0 0 {int(width)} {int(height)}" role="img" '
        f'aria-label="{_esc(sld.title)}">'
        + "".join(edge_parts)
        + "".join(_terminal_symbol_parts(sld, positions))
        + "".join(node_parts)
        + "</svg></div>"
    )


def _bus_table(sld: SLDModel) -> str:
    rows: list[str] = []
    for node in sorted(sld.nodes, key=lambda item: item.id.lower()):
        status = _status(node, sld)
        css = _status_class(status)
        rows.append(
            "<tr>"
            f"<th scope=\"row\">{_esc(node.id)}</th>"
            f"<td>{_esc(_phase_text(node, 1))}</td>"
            f"<td>{_esc(_phase_text(node, 2))}</td>"
            f"<td>{_esc(_phase_text(node, 3))}</td>"
            f'<td><span class="cept-status {css}">{_esc(status)}</span></td>'
            "</tr>"
        )
    if not rows:
        return '<div class="cept-empty">No bus values are available.</div>'
    return (
        '<div class="cept-table-scroll"><table class="cept-bus-table">'
        "<thead><tr><th>Bus</th><th>Phase A</th><th>Phase B</th><th>Phase C</th><th>Status</th></tr></thead>"
        "<tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
        f'<p class="cept-threshold-note">Voltage status uses the SLD limits '
        f'{sld.v_min_pu:.3f}–{sld.v_max_pu:.3f} pu carried by this solved result.</p>'
    )


def _downsample_xy(xs: Sequence[float], ys: Sequence[float], limit: int = 1200) -> tuple[list[float], list[float]]:
    n = min(len(xs), len(ys))
    if n <= limit:
        return list(xs[:n]), list(ys[:n])
    step = max(1, math.ceil(n / limit))
    indexes = list(range(0, n, step))
    if indexes[-1] != n - 1:
        indexes.append(n - 1)
    return [float(xs[index]) for index in indexes], [float(ys[index]) for index in indexes]


def _line_chart_svg(
    title: str,
    y_label: str,
    series: Sequence[tuple[str, Sequence[float], Sequence[float]]],
    *,
    events: Iterable[float] = (),
) -> str:
    clean: list[tuple[str, list[float], list[float]]] = []
    for label, raw_x, raw_y in series:
        pairs = [
            (float(x), float(y))
            for x, y in zip(raw_x, raw_y)
            if _finite(x) is not None and _finite(y) is not None
        ]
        if not pairs:
            continue
        xs, ys = zip(*pairs)
        sx, sy = _downsample_xy(xs, ys)
        clean.append((label, sx, sy))
    if not clean:
        return ""

    x_values = [value for _, xs, _ in clean for value in xs]
    y_values = [value for _, _, ys in clean for value in ys]
    xmin, xmax = min(x_values), max(x_values)
    ymin, ymax = min(y_values), max(y_values)
    if xmax <= xmin:
        xmax = xmin + 1.0
    if ymax <= ymin:
        delta = max(abs(ymin) * 0.02, 1.0)
        ymin -= delta
        ymax += delta
    ypad = max((ymax - ymin) * 0.08, 1e-9)
    ymin -= ypad
    ymax += ypad

    width, height = 900.0, 330.0
    left, right, top, bottom = 76.0, 22.0, 42.0, 54.0
    plot_w = width - left - right
    plot_h = height - top - bottom

    def sx(value: float) -> float:
        return left + (value - xmin) / (xmax - xmin) * plot_w

    def sy(value: float) -> float:
        return top + (ymax - value) / (ymax - ymin) * plot_h

    grid: list[str] = []
    for index in range(5):
        fraction = index / 4
        y_value = ymax - fraction * (ymax - ymin)
        y = top + fraction * plot_h
        grid.append(
            f'<line class="cept-grid" x1="{left:.1f}" x2="{width-right:.1f}" y1="{y:.1f}" y2="{y:.1f}"/>'
            f'<text class="cept-axis-label" x="{left-10:.1f}" y="{y+4:.1f}" text-anchor="end">{_esc(f"{y_value:.4g}")}</text>'
        )

    event_parts: list[str] = []
    for value in events:
        event = _finite(value)
        if event is None or event < xmin or event > xmax:
            continue
        x = sx(event)
        event_parts.append(
            f'<line class="cept-event-line" x1="{x:.1f}" x2="{x:.1f}" y1="{top:.1f}" y2="{height-bottom:.1f}">'
            f"<title>Event at t={event:.6g} s</title></line>"
        )

    paths: list[str] = []
    legend: list[str] = []
    for index, (label, xs, ys) in enumerate(clean):
        css = f"series-{index % 6}"
        points = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in zip(xs, ys))
        paths.append(
            f'<polyline class="cept-trace {css}" points="{points}">'
            f"<title>{_esc(label)} — min {_esc(f'{min(ys):.5g}')}, max {_esc(f'{max(ys):.5g}')}</title></polyline>"
        )
        sample_step = max(1, math.ceil(len(xs) / 80))
        for point_index in range(0, len(xs), sample_step):
            x, y = xs[point_index], ys[point_index]
            paths.append(
                f'<circle class="cept-sample {css}" cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="2.8">'
                f"<title>{_esc(label)}\nt={x:.6g} s\n{_esc(y_label)}={y:.6g}</title></circle>"
            )
        legend.append(
            f'<span class="cept-legend-item"><span class="cept-legend-swatch {css}"></span>{_esc(label)}</span>'
        )

    return (
        '<div class="cept-chart-card">'
        f"<h4>{_esc(title)}</h4>"
        f'<svg class="cept-chart" viewBox="0 0 {int(width)} {int(height)}" role="img" aria-label="{_esc(title)}">'
        + "".join(grid)
        + f'<line class="cept-axis" x1="{left:.1f}" x2="{width-right:.1f}" y1="{height-bottom:.1f}" y2="{height-bottom:.1f}"/>'
        + f'<line class="cept-axis" x1="{left:.1f}" x2="{left:.1f}" y1="{top:.1f}" y2="{height-bottom:.1f}"/>'
        + f'<text class="cept-axis-title" x="{(left+width-right)/2:.1f}" y="{height-14:.1f}" text-anchor="middle">Time (s)</text>'
        + f'<text class="cept-axis-title" transform="translate(18 {(top+height-bottom)/2:.1f}) rotate(-90)" text-anchor="middle">{_esc(y_label)}</text>'
        + f'<text class="cept-axis-label" x="{left:.1f}" y="{height-bottom+19:.1f}" text-anchor="middle">{xmin:.4g}</text>'
        + f'<text class="cept-axis-label" x="{width-right:.1f}" y="{height-bottom+19:.1f}" text-anchor="middle">{xmax:.4g}</text>'
        + "".join(event_parts)
        + "".join(paths)
        + "</svg>"
        + '<div class="cept-legend">'
        + "".join(legend)
        + "</div></div>"
    )


def _event_times(dynamics: DynamicsResult) -> list[float]:
    values: list[float] = []
    for event in dynamics.events:
        for key in ("time_s", "t_s", "time"):
            value = _finite(event.get(key))
            if value is not None:
                values.append(value)
                break
    return values


def _monitor_channel_series(
    dynamics: DynamicsResult,
    predicate: Callable[[str], bool],
) -> list[tuple[str, Sequence[float], Sequence[float]]]:
    series: list[tuple[str, Sequence[float], Sequence[float]]] = []
    for monitor in dynamics.monitors:
        for channel in monitor.channels:
            if not predicate(channel.name.lower()):
                continue
            n = min(len(monitor.t), len(channel.values))
            if n:
                label = monitor.element.split(".")[-1] if monitor.element else monitor.name
                series.append((f"{label} — {channel.name}", monitor.t[:n], channel.values[:n]))
    return series


def _power_series(dynamics: DynamicsResult) -> list[tuple[str, Sequence[float], Sequence[float]]]:
    series: list[tuple[str, Sequence[float], Sequence[float]]] = []
    for monitor in dynamics.monitors:
        channels = []
        for phase in (1, 2, 3):
            found = monitor.channel(f"P{phase} (kW)")
            if found is not None and found.values:
                channels.append(found)
        if not channels or not monitor.t:
            continue
        n = min([len(monitor.t)] + [len(channel.values) for channel in channels])
        values = [sum(channel.values[index] for channel in channels) for index in range(n)]
        label = monitor.element.split(".")[-1] if monitor.element else monitor.name
        series.append((f"{label} — P", monitor.t[:n], values))
    return series


def _dynamic_charts(study: StudyResult) -> str:
    dynamics = study.dynamics
    if dynamics is None:
        return ""
    events = _event_times(dynamics)
    charts = [
        _line_chart_svg(
            "Generator frequency",
            "Frequency (Hz)",
            _monitor_channel_series(dynamics, lambda name: "frequency" in name),
            events=events,
        ),
        _line_chart_svg(
            "Rotor angle",
            "Angle (deg)",
            _monitor_channel_series(dynamics, lambda name: "theta" in name or "angle" in name),
            events=events,
        ),
        _line_chart_svg(
            "Bus / terminal voltage",
            "Voltage",
            _monitor_channel_series(dynamics, lambda name: name == "v1" or name.startswith("v1 ")),
            events=events,
        ),
        _line_chart_svg(
            "Generator real-power output",
            "P (kW)",
            _power_series(dynamics),
            events=events,
        ),
    ]
    visible = [chart for chart in charts if chart]
    if not visible:
        return '<div class="cept-empty">No resolved dynamic monitor channels are available to plot.</div>'
    return '<div class="cept-charts">' + "".join(visible) + "</div>"


def _time_series_chart(study: StudyResult) -> str:
    result = study.time_series
    if result is None or not result.snapshots:
        return ""
    times = [item.t_s for item in result.snapshots]
    low = [item.min_voltage_pu for item in result.snapshots]
    high = [item.max_voltage_pu for item in result.snapshots]
    series = []
    if all(value is not None for value in low):
        series.append(("Minimum voltage", times, [float(value) for value in low if value is not None]))
    if all(value is not None for value in high):
        series.append(("Maximum voltage", times, [float(value) for value in high if value is not None]))
    return _line_chart_svg("Voltage envelope", "Voltage (pu)", series)


def _summary_items(study: StudyResult, verification: dict | None) -> list[tuple[str, str]]:
    items = [
        ("Study", study.study_type.replace("_", " ").title()),
        ("Engine", study.engine or "—"),
        ("Case", study.case_name or "—"),
        ("Fingerprint", (study.case_fingerprint or "—")[:12]),
    ]
    if verification is not None:
        items.append(("Verification", "PASSED" if verification.get("passed") is True else "NEEDS ATTENTION"))
    if study.load_flow is not None:
        items.append(("Converged", "Yes" if study.load_flow.converged else "No"))
    if study.fault is not None and study.fault.total_fault_current_a is not None:
        items.append(("Fault current", f"{study.fault.total_fault_current_a:.1f} A"))
    if study.hosting_capacity is not None and study.hosting_capacity.min_hc is not None:
        item = study.hosting_capacity.min_hc
        items.append(("Minimum hosting capacity", f"{item.hc_kw:.1f} kW @ {item.bus}"))
    if study.dynamics is not None:
        items.append(("Dynamic solve", "Converged" if study.dynamics.converged else "Not converged"))
        if study.dynamics.stable is not None:
            items.append(("Stability", "Stable" if study.dynamics.stable else "Needs review"))
        if study.dynamics.freq_nadir_hz is not None:
            items.append(("Frequency nadir", f"{study.dynamics.freq_nadir_hz:.3f} Hz"))
    return items


def _sld_views(study: StudyResult) -> list[tuple[str, SLDModel]]:
    views: list[tuple[str, SLDModel]] = []
    for snapshot in study.sld_snapshots:
        if snapshot.status == "available" and snapshot.sld is not None:
            label = snapshot.label
            if snapshot.t_s:
                label = f"{label} — t={snapshot.t_s:.4g} s"
            views.append((label, snapshot.sld))
    if views:
        return views
    if study.sld is not None:
        views.append((study.sld.title or "Solved SLD", study.sld))
    if study.sld_after is not None:
        views.append(("After event", study.sld_after))
    return views


def render_study_html(study: StudyResult, *, verification: dict | None = None) -> str:
    """Render one solver-backed study result as Colab-friendly HTML/SVG."""

    summary = "".join(
        f'<div class="cept-summary-item"><span>{_esc(label)}</span><strong>{_esc(value)}</strong></div>'
        for label, value in _summary_items(study, verification)
    )

    sld_sections: list[str] = []
    views = _sld_views(study)
    for index, (label, sld) in enumerate(views):
        body = _sld_svg(sld) + _bus_table(sld)
        if len(views) == 1:
            sld_sections.append(body)
        else:
            open_attr = " open" if index == 0 else ""
            sld_sections.append(
                f'<details class="cept-snapshot"{open_attr}><summary>{_esc(label)}</summary>{body}</details>'
            )

    charts = _dynamic_charts(study) or _time_series_chart(study)
    chart_section = (
        '<section class="cept-section"><h3>Solver traces</h3>'
        '<p class="cept-section-note">Plots use persisted solver samples. Presentation downsampling may reduce SVG points; stored results are unchanged.</p>'
        + charts
        + "</section>"
        if charts
        else ""
    )

    sld_section = (
        '<section class="cept-section"><h3>Interactive CEPT SLD</h3>'
        '<p class="cept-section-note">Topology, values, and status come from the solved SLD carried in this StudyResult.</p>'
        + "".join(sld_sections)
        + "</section>"
        if sld_sections
        else '<section class="cept-section"><h3>Interactive CEPT SLD</h3><div class="cept-empty">This result does not carry an SLD.</div></section>'
    )

    style = """
<style>
.cept-nb{font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#172033;line-height:1.45;max-width:1180px;margin:0 auto}
.cept-nb *{box-sizing:border-box}
.cept-nb-header{display:flex;gap:12px;align-items:flex-start;justify-content:space-between;flex-wrap:wrap;margin:8px 0 14px}
.cept-nb-header h2{font-size:1.25rem;margin:0}
.cept-nb-kicker{font-size:.78rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#526079}
.cept-summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:8px;margin:0 0 16px}
.cept-summary-item{border:1px solid #d9dee8;border-radius:9px;padding:9px 11px;background:#fff}
.cept-summary-item span{display:block;font-size:.75rem;color:#68758a;margin-bottom:3px}
.cept-summary-item strong{display:block;font-size:.92rem;overflow-wrap:anywhere}
.cept-section{margin:18px 0 24px}
.cept-section h3{font-size:1.05rem;margin:0 0 3px}
.cept-section-note,.cept-threshold-note,.cept-sld-help{font-size:.82rem;color:#657187;margin:0 0 9px}
.cept-sld-wrap{border:1px solid #d9dee8;border-radius:10px;padding:8px;background:#fff;overflow:hidden}
.cept-sld{display:block;width:100%;height:auto;min-height:260px}
.cept-edge{fill:none;stroke:#7a879a;stroke-width:3;vector-effect:non-scaling-stroke}
.cept-edge.edge-open{stroke-dasharray:8 7}
.cept-bus rect{stroke-width:2;vector-effect:non-scaling-stroke;transition:stroke-width .12s ease}
.cept-bus text{font-size:13px;font-weight:700;pointer-events:none}
.cept-bus-status-label{font-size:10px!important;font-weight:600!important;fill:#596579}
.cept-bus.ok rect{fill:#e8f5ec;stroke:#2f7d4a}.cept-bus.under rect{fill:#fff3d9;stroke:#a46700}
.cept-bus.over rect{fill:#ffe7e1;stroke:#b8432e}.cept-bus.out rect{fill:#f7e7ff;stroke:#8147a6}.cept-bus.nodata rect{fill:#eef1f5;stroke:#7b8796}
.cept-bus.substation rect{stroke-width:3}
.cept-bus:hover rect,.cept-bus:focus rect{stroke-width:5;outline:none}
.cept-table-scroll{overflow-x:auto;border:1px solid #d9dee8;border-radius:10px;margin-top:10px}
.cept-bus-table{border-collapse:collapse;width:100%;min-width:650px;font-size:.84rem}
.cept-bus-table th,.cept-bus-table td{padding:7px 9px;border-bottom:1px solid #e6e9ef;text-align:left;white-space:nowrap}
.cept-bus-table thead th{background:#f6f8fb;color:#435067;font-size:.76rem;text-transform:uppercase;letter-spacing:.04em}
.cept-status{display:inline-block;padding:2px 7px;border-radius:999px;font-size:.72rem;font-weight:800}
.cept-status.ok{background:#e8f5ec;color:#235d37}.cept-status.under{background:#fff3d9;color:#794d00}
.cept-status.over{background:#ffe7e1;color:#8b3020}.cept-status.out{background:#f7e7ff;color:#62327f}.cept-status.nodata{background:#eef1f5;color:#596579}
.cept-snapshot{border:1px solid #d9dee8;border-radius:10px;margin:10px 0;background:#fbfcfe}
.cept-snapshot>summary{cursor:pointer;padding:10px 12px;font-weight:750}.cept-snapshot[open]>summary{border-bottom:1px solid #d9dee8}
.cept-snapshot>.cept-sld-wrap,.cept-snapshot>.cept-table-scroll,.cept-snapshot>.cept-threshold-note{margin-left:10px;margin-right:10px}.cept-snapshot>.cept-threshold-note{margin-bottom:12px}
.cept-charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,430px),1fr));gap:12px}
.cept-chart-card{border:1px solid #d9dee8;border-radius:10px;padding:8px 9px;background:#fff;min-width:0}
.cept-chart-card h4{font-size:.9rem;margin:2px 0 5px}
.cept-chart{display:block;width:100%;height:auto}
.cept-grid{stroke:#e7ebf1;stroke-width:1}.cept-axis{stroke:#7a879a;stroke-width:1.4}.cept-axis-label{font-size:11px;fill:#667287}.cept-axis-title{font-size:12px;fill:#4f5c71;font-weight:650}
.cept-trace{fill:none;stroke-width:2.2;vector-effect:non-scaling-stroke}.cept-sample{stroke:none;opacity:.06}.cept-sample:hover{opacity:1}
.cept-event-line{stroke:#b14b3b;stroke-width:1.4;stroke-dasharray:5 4}
.series-0{stroke:#2f6fbb;fill:#2f6fbb}.series-1{stroke:#2b8a66;fill:#2b8a66}.series-2{stroke:#a86d16;fill:#a86d16}
.series-3{stroke:#7b5bb5;fill:#7b5bb5}.series-4{stroke:#b34d65;fill:#b34d65}.series-5{stroke:#55737f;fill:#55737f}
.cept-legend{display:flex;gap:9px 14px;flex-wrap:wrap;font-size:.74rem;color:#566277;padding:2px 4px 3px}.cept-legend-item{display:inline-flex;align-items:center;gap:5px}.cept-legend-swatch{width:14px;height:3px;border-radius:3px}
.cept-empty{border:1px dashed #cbd2dd;border-radius:9px;padding:12px;color:#657187;background:#fafbfd}
@media (max-width:600px){.cept-nb{font-size:15px}.cept-summary{grid-template-columns:repeat(2,minmax(0,1fr))}.cept-sld{min-height:220px}.cept-charts{grid-template-columns:1fr}}
</style>
"""

    return (
        '<div class="cept-nb">'
        + style
        + '<div class="cept-nb-header"><div><div class="cept-nb-kicker">Solver-backed notebook view</div>'
        + f"<h2>{_esc(study.case_name or 'CEPT study result')}</h2></div></div>"
        + '<div class="cept-summary">'
        + summary
        + "</div>"
        + sld_section
        + chart_section
        + "</div>"
    )


def render_run_html(run_dir: str | Path) -> str:
    """Read a persisted run and return its notebook-native HTML view."""

    root = Path(run_dir)
    result_path = root / "results.json"
    if not result_path.is_file():
        raise FileNotFoundError(f"results.json not found in {root}")
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    study = StudyResult.model_validate(payload)

    verification = verify_study(root)
    return render_study_html(study, verification=verification)


def display_run(run_dir: str | Path) -> None:
    """Display a persisted CEPT run inline in Jupyter/Google Colab."""

    from IPython.display import HTML, display

    display(HTML(render_run_html(run_dir)))


__all__ = ["display_run", "render_run_html", "render_study_html"]
