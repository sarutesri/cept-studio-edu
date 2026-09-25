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

from cept.public import verify_study
from cept.domain.sld.layout_contract import solved_layout_plan
from cept.reporting.sld_runtime import build_sld_option_v2
from cept.reporting.sld_svg import render_native_sld_svg
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

_SLD_STYLE = """<style>
.cept-sld-wrap{border:1px solid #d9dee8;border-radius:10px;padding:8px;background:#fff;overflow:hidden}
.cept-sld-viewport{height:520px;min-height:340px;border:1px solid #e2e8f0;border-radius:8px;background:#fff;overflow:auto;scrollbar-gutter:stable}
.cept-sld-svg{display:block;width:100%;height:100%;background:#fff}
.cept-sld-svg .sld-busbar,.cept-sld-svg .sld-branch,.cept-sld-svg .sld-terminal-stem,.cept-sld-svg .sld-symbol-shape *{vector-effect:non-scaling-stroke}
.cept-sld-svg .sld-busbar:hover{fill:#0b5cad;cursor:pointer;filter:drop-shadow(0 0 2px rgba(11,92,173,.4))}
.cept-sld-svg .sld-branch:hover{stroke:#0b5cad;stroke-width:3.2;cursor:pointer}
.cept-sld-svg .sld-device:hover,.cept-sld-svg .sld-inline:hover{cursor:pointer;filter:drop-shadow(0 0 3px rgba(11,92,173,.6))}
.cept-sld-legend{display:flex;gap:14px;flex-wrap:wrap;padding:8px 4px 2px;color:#475569;font-size:12px}
.cept-sld-cap{padding:2px 4px 0;color:#64748b;font-size:11px}
.cept-table-scroll{overflow-x:auto;border:1px solid #d9dee8;border-radius:10px;margin-top:10px}
.cept-bus-table{border-collapse:collapse;width:100%;min-width:650px;font-size:.84rem}
.cept-bus-table th,.cept-bus-table td{padding:7px 9px;border-bottom:1px solid #e6e9ef;text-align:left;white-space:nowrap}
.cept-bus-table thead th{background:#f6f8fb;color:#435067;font-size:.76rem;text-transform:uppercase;letter-spacing:.04em}
.cept-status{display:inline-block;padding:2px 7px;border-radius:999px;font-size:.72rem;font-weight:800}
.cept-status.ok{background:#e8f5ec;color:#235d37}.cept-status.under{background:#fff3d9;color:#794d00}
.cept-status.over{background:#ffe7e1;color:#8b3020}.cept-status.out{background:#f7e7ff;color:#62327f}.cept-status.nodata{background:#eef1f5;color:#596579}
@media (max-width:600px){.cept-sld-viewport{height:420px;min-height:420px}.cept-sld-wrap--wide .cept-sld-svg{width:760px;max-width:none;height:420px}.cept-sld-wrap--dense .cept-sld-svg{width:1040px;max-width:none;height:520px}}
</style>"""



def _sld_svg(sld: SLDModel, *, dom_id: str) -> str:
    """Render the same canonical engineering SLD used by CEPT reports."""
    graph = dict(build_sld_option_v2(sld))
    # Public lesson diagrams show electrical topology only. Solver event
    # annotations remain in the persisted result and production report, but
    # must not float as unexplained symbols in a teaching SLD.
    graph["nodes"] = [
        node for node in graph.get("nodes") or []
        if not str(node.get("name") or "").startswith("__event_")
    ]
    nodes = graph["nodes"]
    if not nodes:
        return '<div class="cept-empty">No SLD geometry is available for this result.</div>'
    buses = sum(1 for node in nodes if node.get("category") == "bus")
    size_class = " cept-sld-wrap--dense" if buses >= 10 else " cept-sld-wrap--wide" if buses >= 4 else ""
    branches = sum(1 for link in graph.get("links") or [] if link.get("edge_id"))
    svg = render_native_sld_svg(
        graph,
        dom_id=dom_id,
        strict_connections=True,
    )
    return (
        f'<div class="cept-sld-wrap{size_class}">'
        '<div class="cept-sld-help">Hover a bus, branch, or device for its solver-backed identity.</div>'
        f'<div class="cept-sld-viewport" role="img" aria-label="{_esc(sld.title)}">{svg}</div>'
        '<div class="cept-sld-legend"><span>━ Busbar</span><span>⊞ External grid</span>'
        '<span>◎ Transformer</span><span>▼ Load</span></div>'
        f'<div class="cept-sld-cap">{buses} buses · {branches} branches · canonical CEPT geometry</div>'
        "</div>"
    )


def _bus_table(sld: SLDModel) -> str:
    rows: list[str] = []
    physical_bus_ids = set(solved_layout_plan(sld).physical_bus_ids)
    for node in sorted(sld.nodes, key=lambda item: item.id.lower()):
        if str(node.id).lower() not in physical_bus_ids:
            continue
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
        body = _sld_svg(sld, dom_id=f"cept-sld-{index}") + _bus_table(sld)
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
.cept-sld-viewport{height:520px;min-height:340px;border:1px solid #e2e8f0;border-radius:8px;background:#fff;overflow:auto;scrollbar-gutter:stable}
.cept-sld-svg{display:block;width:100%;height:100%;background:#fff}
.cept-sld-svg .sld-busbar,.cept-sld-svg .sld-branch,.cept-sld-svg .sld-terminal-stem,.cept-sld-svg .sld-symbol-shape *{vector-effect:non-scaling-stroke}
.cept-sld-svg .sld-busbar:hover{fill:#0b5cad;cursor:pointer;filter:drop-shadow(0 0 2px rgba(11,92,173,.4))}
.cept-sld-svg .sld-branch:hover{stroke:#0b5cad;stroke-width:3.2;cursor:pointer}
.cept-sld-svg .sld-device:hover,.cept-sld-svg .sld-inline:hover{cursor:pointer;filter:drop-shadow(0 0 3px rgba(11,92,173,.6))}
.cept-sld-legend{display:flex;gap:14px;flex-wrap:wrap;padding:8px 4px 2px;color:#475569;font-size:12px}
.cept-sld-cap{padding:2px 4px 0;color:#64748b;font-size:11px}
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
@media (max-width:600px){.cept-nb{font-size:15px}.cept-summary{grid-template-columns:repeat(2,minmax(0,1fr))}.cept-sld-viewport{height:420px;min-height:420px}.cept-sld-wrap--wide .cept-sld-svg{width:760px;max-width:none;height:420px}.cept-sld-wrap--dense .cept-sld-svg{width:1040px;max-width:none;height:520px}.cept-charts{grid-template-columns:1fr}}
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


def _study_from_run(run_dir: str | Path) -> StudyResult:
    """Read and validate one persisted solver result."""
    root = Path(run_dir)
    result_path = root / "results.json"
    if not result_path.is_file():
        raise FileNotFoundError(f"results.json not found in {root}")
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    return StudyResult.model_validate(payload)


def render_run_html(run_dir: str | Path) -> str:
    """Read a persisted run and return its notebook-native HTML view."""
    root = Path(run_dir)
    study = _study_from_run(root)
    verification = verify_study(root)
    return render_study_html(study, verification=verification)


def render_sld_html(study: StudyResult, *, dom_id: str = "cept-sld") -> str:
    """Render the canonical SLD and persisted bus table only."""
    views = _sld_views(study)
    if not views:
        return '<div class="cept-empty">This result does not carry an SLD.</div>'
    _label, sld = views[0]
    return _SLD_STYLE + _sld_svg(sld, dom_id=dom_id) + _bus_table(sld)


def display_sld(run_dir: str | Path) -> None:
    """Display the canonical engineering SLD for a persisted run."""
    from IPython.display import HTML, display

    display(HTML(render_sld_html(_study_from_run(run_dir))))


def display_run(run_dir: str | Path) -> None:
    """Display a persisted CEPT run inline in Jupyter/Google Colab."""

    from IPython.display import HTML, display

    display(HTML(render_run_html(run_dir)))


__all__ = ["display_run", "display_sld", "render_run_html", "render_sld_html", "render_study_html"]
