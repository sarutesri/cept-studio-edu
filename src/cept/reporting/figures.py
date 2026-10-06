"""Plotly figure builders. Each returns an HTML <div> (no plotly.js inline);
the page template (base.html) inlines the vendored plotly.js once instead."""

from __future__ import annotations

from collections import defaultdict
import math
from typing import Optional

from collections import defaultdict as _dd

import plotly.graph_objects as go

from cept.schema.result import (
    BusVoltage,
    DynamicsResult,
    EMTResult,
    FaultResult,
    GICResult,
    HostingCapacityResult,
    LoadFlowResult,
    ValidationReport,
)

_PHASE_COLOR = {1: "#1f77b4", 2: "#ff7f0e", 3: "#2ca02c"}
_PHASE_NAME = {1: "A", 2: "B", 3: "C"}


def _div(fig: go.Figure) -> str:
    return fig.to_html(full_html=False, include_plotlyjs=False, config={"responsive": True})


def voltages_by_bus(lf: LoadFlowResult) -> dict[str, dict[int, float]]:
    out: dict[str, dict[int, float]] = defaultdict(dict)
    for bv in lf.bus_voltages:
        out[bv.bus.lower()][bv.phase] = bv.v_pu
    return out


def _voltage_axis_range(lf: LoadFlowResult, v_min: float, v_max: float) -> tuple[float, float]:
    """Return a readable voltage range without hiding solver-returned values.

    Plotly bar charts default to a zero baseline, which makes normal feeder
    voltage variation around 1.0 pu visually disappear. Include both the
    observed values and diagnostic limits, then add a small rounded margin so
    the chart remains honest while making the useful range legible.
    """
    values = [bv.v_pu for bv in lf.bus_voltages if math.isfinite(bv.v_pu)]
    bounds = [v_min, v_max, *values]
    lower = min(bounds)
    upper = max(bounds)
    span = upper - lower
    if span <= 1e-12:
        span = max(abs(lower) * 0.02, 0.02)
    padding = max(span * 0.12, 0.005)
    lower = math.floor((lower - padding) * 100.0) / 100.0
    upper = math.ceil((upper + padding) * 100.0) / 100.0
    if upper <= lower:
        upper = lower + 0.01
    return lower, upper


def voltage_profile_figure(lf: LoadFlowResult, v_min: float, v_max: float) -> str:
    """Grouped per-phase voltage bars across buses."""
    vbus = voltages_by_bus(lf)
    buses = list(vbus.keys())
    bus_labels = [bus.upper() for bus in buses]
    fig = go.Figure()
    for ph in (1, 2, 3):
        ys = [vbus[bus].get(ph) for bus in buses]
        fig.add_trace(
            go.Bar(
                x=bus_labels,
                y=ys,
                name=f"Phase {_PHASE_NAME[ph]}",
                marker_color=_PHASE_COLOR[ph],
                hovertemplate=(
                    f"Bus %{{x}}<br>Phase {_PHASE_NAME[ph]}: %{{y:.4f}} pu"
                    "<extra></extra>"
                ),
            )
        )
    y_min, y_max = _voltage_axis_range(lf, v_min, v_max)
    fig.add_hline(y=v_max, line=dict(color="red", dash="dash"), annotation_text=f"max {v_max}")
    fig.add_hline(y=v_min, line=dict(color="red", dash="dash"), annotation_text=f"min {v_min}")
    fig.update_layout(
        title="Voltage profile (per-unit, line-to-neutral)",
        xaxis_title="Bus",
        yaxis_title="Voltage (pu)",
        template="plotly_white",
        height=460,
        barmode="group",
        bargap=0.2,
        bargroupgap=0.04,
        xaxis=dict(categoryorder="array", categoryarray=bus_labels),
        yaxis=dict(range=[y_min, y_max], tickformat=".2f"),
        legend=dict(orientation="h"),
    )
    return _div(fig)


def network_diagram_figure(
    coords: dict[str, tuple[float, float]],
    edges: list[tuple[str, str]],
    lf: LoadFlowResult,
    v_min: float,
    v_max: float,
) -> Optional[str]:
    """SLD-like node-edge diagram using real bus coordinates, nodes colored
    by their minimum phase voltage (worst phase). Returns None if no coords."""
    if not coords:
        return None
    vbus = voltages_by_bus(lf)

    fig = go.Figure()
    # edges
    ex, ey = [], []
    for a, b in edges:
        if a in coords and b in coords:
            ex += [coords[a][0], coords[b][0], None]
            ey += [coords[a][1], coords[b][1], None]
    fig.add_trace(
        go.Scatter(
            x=ex, y=ey, mode="lines", line=dict(color="#888", width=1.5), hoverinfo="none", showlegend=False
        )
    )
    # nodes
    nx, ny, ncolor, ntext = [], [], [], []
    for bus, (x, y) in coords.items():
        vmin_bus = min(vbus.get(bus, {1: 1.0}).values())
        nx.append(x)
        ny.append(y)
        ncolor.append(vmin_bus)
        phases = ", ".join(f"{_PHASE_NAME[p]}={vbus[bus][p]:.4f}" for p in sorted(vbus.get(bus, {})))
        ntext.append(f"<b>{bus.upper()}</b><br>{phases}")
    fig.add_trace(
        go.Scatter(
            x=nx,
            y=ny,
            mode="markers+text",
            text=[b.upper() for b in coords],
            textposition="top center",
            textfont=dict(size=9),
            marker=dict(
                size=16,
                color=ncolor,
                colorscale="RdYlGn",
                cmin=v_min,
                cmax=v_max,
                showscale=True,
                colorbar=dict(title="V (pu)"),
            ),
            hovertext=ntext,
            hoverinfo="text",
            showlegend=False,
        )
    )
    fig.update_layout(
        title="Network diagram (nodes colored by worst-phase voltage)",
        template="plotly_white",
        height=520,
        xaxis=dict(visible=False),
        yaxis=dict(visible=False, scaleanchor="x"),
    )
    return _div(fig)


def _vbus_from_list(bvs: list[BusVoltage]) -> dict[str, dict[int, float]]:
    out: dict[str, dict[int, float]] = _dd(dict)
    for bv in bvs:
        out[bv.bus.lower()][bv.phase] = bv.v_pu
    return out


def sag_profile_figure(fault: FaultResult, v_min: float = 0.9) -> str:
    """Per-phase voltage across all buses *during* the fault (voltage sag)."""
    vbus = _vbus_from_list(fault.bus_voltages_during)
    buses = list(vbus.keys())
    fig = go.Figure()
    for ph in (1, 2, 3):
        xs = [b.upper() for b in buses if ph in vbus[b]]
        ys = [vbus[b][ph] for b in buses if ph in vbus[b]]
        if xs:
            fig.add_trace(
                go.Scatter(
                    x=xs,
                    y=ys,
                    mode="markers+lines",
                    name=f"Phase {_PHASE_NAME[ph]}",
                    marker=dict(color=_PHASE_COLOR[ph], size=7),
                )
            )
    fig.add_hline(y=v_min, line=dict(color="red", dash="dash"), annotation_text=f"sag limit {v_min}")
    fig.update_layout(
        title=f"Voltage sag during fault at {fault.bus.upper()} ({fault.fault_type.upper()})",
        xaxis_title="Bus",
        yaxis_title="Voltage (pu)",
        template="plotly_white",
        height=440,
        legend=dict(orientation="h"),
    )
    return _div(fig)


def fault_current_figure(fault: FaultResult) -> str:
    """Bar of available 3-phase fault current at the top buses (FaultStudy)."""
    rows = [r for r in fault.study_rows if r.i_3ph_a]
    rows.sort(key=lambda r: r.i_3ph_a or 0, reverse=True)
    rows = rows[:15]
    fig = go.Figure()
    fig.add_trace(
        go.Bar(x=[r.bus for r in rows], y=[r.i_3ph_a for r in rows], name="3-phase", marker_color="#1f77b4")
    )
    fig.add_trace(
        go.Bar(
            x=[r.bus for r in rows], y=[r.i_1ph_a for r in rows], name="1-phase (SLG)", marker_color="#ff7f0e"
        )
    )
    fig.update_layout(
        title="Available fault current by bus (top 15)",
        xaxis_title="Bus",
        yaxis_title="Fault current (A)",
        barmode="group",
        template="plotly_white",
        height=420,
        legend=dict(orientation="h"),
    )
    return _div(fig)


def hosting_capacity_figure(hc: HostingCapacityResult) -> str:
    items = sorted(hc.items, key=lambda i: i.hc_kw)
    colors = {
        "overvoltage": "#d62728",
        "thermal": "#9467bd",
        "maxed": "#2ca02c",
        "none": "#7f7f7f",
    }
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            y=[i.bus.upper() for i in items],
            x=[i.hc_kw for i in items],
            orientation="h",
            marker_color=[colors.get(i.limit, "#1f77b4") for i in items],
            text=[f"{i.hc_kw:.0f} kW" for i in items],
            textposition="outside",
        )
    )
    fig.update_layout(
        title=f"PV hosting capacity by bus (limit: {hc.criterion}, Vmax={hc.v_max_pu})",
        xaxis_title="Hosting capacity (kW)",
        yaxis_title="Bus",
        template="plotly_white",
        height=max(360, 26 * len(items) + 120),
    )
    return _div(fig)


def _event_shapes(events, y0, y1):
    """Vertical dashed lines marking disturbance events on a time axis."""
    shapes, annos = [], []
    labels_by_time: dict[float, list[str]] = {}
    drawn_times: set[float] = set()
    for ev in events:
        t = ev.get("t")
        if not isinstance(t, (int, float)):
            continue
        t = float(t)
        if t not in drawn_times:
            shapes.append(
                dict(
                    type="line",
                    x0=t,
                    x1=t,
                    y0=y0,
                    y1=y1,
                    line=dict(color="#ef4444", width=1.2, dash="dot"),
                )
            )
            drawn_times.add(t)
        label = str(ev.get("label") or ev.get("kind") or "")
        if label and label not in labels_by_time.setdefault(t, []):
            labels_by_time[t].append(label)
    for t, labels in labels_by_time.items():
        annos.append(
            dict(
                x=t,
                y=y1,
                text="<br>".join(labels),
                showarrow=False,
                font=dict(size=9, color="#ef4444"),
                textangle=-90,
                xanchor="left",
                yanchor="top",
            )
        )
    return shapes, annos


def _channel_figure(dyn: DynamicsResult, want, title, ytitle, scale=1.0):
    """Plot a named channel from every monitor that has it."""
    fig = go.Figure()
    traced = False
    ymin, ymax = 1e9, -1e9
    for m in dyn.monitors:
        ch = m.channel(want)
        if not ch or not ch.values or not m.t:
            continue
        n = min(len(m.t), len(ch.values))
        ys = [v * scale for v in ch.values[:n]]
        label = m.element.split(".")[-1] if m.element else m.name
        fig.add_trace(go.Scatter(x=m.t[:n], y=ys, mode="lines", name=f"{label}", line=dict(width=1.6)))
        traced = True
        ymin, ymax = min(ymin, min(ys)), max(ymax, max(ys))
    if not traced:
        return None
    shapes, annos = _event_shapes(dyn.events, ymin, ymax)
    fig.update_layout(
        title=title,
        xaxis_title="Time (s)",
        yaxis_title=ytitle,
        template="plotly_white",
        height=360,
        legend=dict(orientation="h"),
        shapes=shapes,
        annotations=annos,
    )
    return _div(fig)


def dynamics_frequency_figure(dyn: DynamicsResult):
    return _channel_figure(dyn, "Frequency", "Generator frequency", "Frequency (Hz)")


def dynamics_angle_figure(dyn: DynamicsResult):
    fig = _channel_figure(dyn, "Theta (Deg)", "Rotor angle", "Angle (deg)")
    return fig or _channel_figure(dyn, "theta", "Rotor angle", "Angle (deg)")


def dynamics_voltage_figure(dyn: DynamicsResult):
    """Per-monitor phase-1 voltage magnitude over time."""
    fig = go.Figure()
    traced = False
    ymin, ymax = 1e9, -1e9
    for m in dyn.monitors:
        ch = m.channel("V1")
        if not ch or not ch.values or not m.t:
            continue
        n = min(len(m.t), len(ch.values))
        # convert to kV (values are in volts)
        ys = [v / 1000.0 for v in ch.values[:n]]
        label = m.element.split(".")[-1] if m.element else m.name
        fig.add_trace(go.Scatter(x=m.t[:n], y=ys, mode="lines", name=f"{label} V1", line=dict(width=1.4)))
        traced = True
        ymin, ymax = min(ymin, min(ys)), max(ymax, max(ys))
    if not traced:
        return None
    shapes, annos = _event_shapes(dyn.events, ymin, ymax)
    fig.update_layout(
        title="Bus / terminal voltage (phase A)",
        xaxis_title="Time (s)",
        yaxis_title="Voltage (kV)",
        template="plotly_white",
        height=360,
        legend=dict(orientation="h"),
        shapes=shapes,
        annotations=annos,
    )
    return _div(fig)


def dynamics_power_figure(dyn: DynamicsResult):
    """Total real power per generator over time (sum of phase channels)."""
    fig = go.Figure()
    traced = False
    ymin, ymax = 1e9, -1e9
    for m in dyn.monitors:
        pchs = [m.channel(f"P{p} (kW)") for p in (1, 2, 3)]
        pchs = [c for c in pchs if c and c.values]
        if not pchs or not m.t:
            continue
        n = min([len(m.t)] + [len(c.values) for c in pchs])
        ys = [sum(c.values[k] for c in pchs) for k in range(n)]
        label = m.element.split(".")[-1] if m.element else m.name
        fig.add_trace(go.Scatter(x=m.t[:n], y=ys, mode="lines", name=f"{label} P", line=dict(width=1.6)))
        traced = True
        ymin, ymax = min(ymin, min(ys)), max(ymax, max(ys))
    if not traced:
        return None
    shapes, annos = _event_shapes(dyn.events, ymin, ymax)
    fig.update_layout(
        title="Generator real-power output",
        xaxis_title="Time (s)",
        yaxis_title="P (kW)",
        template="plotly_white",
        height=360,
        legend=dict(orientation="h"),
        shapes=shapes,
        annotations=annos,
    )
    return _div(fig)


def emt_waveform_figure(emt: EMTResult):
    """Plot every recorded EMT channel against the solver's own time grid."""
    fig = go.Figure()
    traced = False
    for trace in emt.channels:
        for ch in trace.channels:
            if not ch.values or not trace.t:
                continue
            n = min(len(trace.t), len(ch.values))
            label = trace.name if len(trace.channels) == 1 else f"{trace.name}:{ch.name}"
            unit = f" ({ch.unit})" if ch.unit else ""
            fig.add_trace(
                go.Scatter(
                    x=trace.t[:n], y=ch.values[:n], mode="lines", name=f"{label}{unit}", line=dict(width=1.2)
                )
            )
            traced = True
    if not traced:
        return None
    fig.update_layout(
        title="EMT waveform channels",
        xaxis_title="Time (s)",
        yaxis_title="Value",
        template="plotly_white",
        height=420,
        legend=dict(orientation="h"),
    )
    return _div(fig)


def gic_bar_figure(gic: GICResult):
    items = sorted(gic.elements, key=lambda e: e.gic_amps, reverse=True)[:20]
    colors = ["#b91c1c" if e.kind == "transformer" else "#2563eb" for e in items]
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=[e.name.split(".")[-1] for e in items],
            y=[e.gic_amps for e in items],
            marker_color=colors,
            text=[f"{e.gic_amps:.0f}" for e in items],
            textposition="outside",
        )
    )
    fig.update_layout(
        title="GIC per element (red = transformer, blue = line) — top 20",
        xaxis_title="Element",
        yaxis_title="GIC (A)",
        template="plotly_white",
        height=440,
    )
    return _div(fig)


def validation_scatter_figure(vr: ValidationReport) -> str:
    comp = [i.computed for i in vr.items if i.computed == i.computed]  # drop NaN
    ref = [i.reference for i in vr.items if i.computed == i.computed]
    lo = min(ref + comp) - 0.01 if ref else 0.9
    hi = max(ref + comp) + 0.01 if ref else 1.1
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=[lo, hi], y=[lo, hi], mode="lines", line=dict(color="#bbb", dash="dash"), name="ideal (y=x)"
        )
    )
    fig.add_trace(
        go.Scatter(
            x=ref,
            y=comp,
            mode="markers",
            name="nodes",
            marker=dict(size=9, color="#1f77b4"),
            text=[i.label for i in vr.items if i.computed == i.computed],
            hovertemplate="%{text}<br>ref=%{x:.4f}<br>computed=%{y:.4f}<extra></extra>",
        )
    )
    fig.update_layout(
        title="Computed vs published voltage (pu)",
        xaxis_title="Published (pu)",
        yaxis_title="Computed (pu)",
        template="plotly_white",
        height=460,
    )
    return _div(fig)


def validation_error_figure(vr: ValidationReport) -> str:
    items = [i for i in vr.items if i.error_abs != float("inf")]
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=[i.label for i in items],
            y=[i.error_abs for i in items],
            marker_color=["#2ca02c" if i.passed else "#d62728" for i in items],
        )
    )
    tol = items[0].tol_abs if items else 0.005
    fig.add_hline(y=tol, line=dict(color="red", dash="dash"), annotation_text=f"tolerance {tol}")
    fig.update_layout(
        title="Absolute voltage error per node (pu)",
        xaxis_title="Node",
        yaxis_title="|error| (pu)",
        template="plotly_white",
        height=420,
    )
    return _div(fig)


def error_index_bar_figure(summary: dict, rows: list[dict]) -> str:
    """Comparison index bars.  Missing/blocked values remain visible."""
    scopes = ["input_fidelity", "snapshot", "timeseries", "pdf_crosscheck", "status"]
    indexes = summary.get("scope_indexes", {}) if isinstance(summary, dict) else {}
    vals = [indexes.get(s) for s in scopes]
    colors = ["#9ca3af" if v is None else "#2ca02c" if v <= 1 else "#d62728" for v in vals]
    fig = go.Figure(
        go.Bar(
            x=scopes,
            y=[v if v is not None else 0 for v in vals],
            marker_color=colors,
            customdata=[["blocked"] if v is None else [f"index={v:.4g}"] for v in vals],
            hovertemplate="%{x}<br>%{customdata[0]}<extra></extra>",
        )
    )
    fig.add_hline(y=1.0, line=dict(color="#b91c1c", dash="dash"), annotation_text="threshold 1.0")
    fig.update_layout(
        title="Normalized error index by scope",
        yaxis_title="Index (≤1 pass)",
        template="plotly_white",
        height=380,
    )
    return _div(fig)


def error_heatmap_figure(rows: list[dict]) -> str:
    assets = sorted({str(r.get("asset", "")) for r in rows})
    quantities = sorted({str(r.get("signal/quantity", "")) for r in rows})
    lookup = {
        (str(r.get("asset", "")), str(r.get("signal/quantity", ""))): r.get("normalized_error_index")
        for r in rows
    }
    z = [[lookup.get((a, q)) if lookup.get((a, q)) is not None else None for q in quantities] for a in assets]
    fig = go.Figure(
        go.Heatmap(
            x=quantities,
            y=assets,
            z=z,
            zmin=0,
            zmax=max(1.0, max((v or 0 for row in z for v in row), default=1.0)),
            colorscale=[[0, "#2ca02c"], [0.5, "#facc15"], [1, "#d62728"]],
            colorbar=dict(title="index"),
            hoverongaps=False,
        )
    )
    fig.update_layout(
        title="Asset × quantity error heatmap",
        template="plotly_white",
        height=max(360, 28 * len(assets) + 120),
    )
    return _div(fig)


def reference_candidate_scatter_figure(rows: list[dict]) -> str:
    valid = [
        r
        for r in rows
        if isinstance(r.get("reference_value"), (int, float))
        and isinstance(r.get("candidate_value"), (int, float))
    ]
    values = [float(v) for r in valid for v in (r["reference_value"], r["candidate_value"])]
    lo, hi = (min(values), max(values)) if values else (0.0, 1.0)
    pad = (hi - lo) * 0.05 or 0.05
    lo, hi = lo - pad, hi + pad
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=[lo, hi], y=[lo, hi], mode="lines", name="identity y=x", line=dict(color="#999", dash="dash")
        )
    )
    fig.add_trace(
        go.Scatter(
            x=[r["reference_value"] for r in valid],
            y=[r["candidate_value"] for r in valid],
            mode="markers",
            text=[f"{r.get('asset')} / {r.get('signal/quantity')}" for r in valid],
            marker=dict(color=["#2ca02c" if r.get("status") == "pass" else "#d62728" for r in valid], size=8),
            hovertemplate="%{text}<br>reference=%{x}<br>candidate=%{y}<extra></extra>",
            name="values",
        )
    )
    fig.update_layout(
        title="Reference versus candidate",
        xaxis_title="Reference",
        yaxis_title="Candidate",
        template="plotly_white",
        height=440,
    )
    return _div(fig)


def comparison_timeseries_figures(
    rows: list[dict], *, events: list[dict] | None = None
) -> list[tuple[str, str]]:
    """One small chart per signal (reference + candidate = 2 series), not one
    combined chart with every signal overlaid. Cramming e.g. angle (deg),
    speed (pu), voltage (pu), and power (MW) onto one shared Y-axis made the
    traces visually meaningless -- degrees and per-unit values don't share a
    scale, so nothing actually overlaid correctly regardless of how well the
    engines agreed. Returns ``[(title, div_html), ...]`` for a small-multiples
    grid instead."""
    figs: list[tuple[str, str]] = []
    for row in rows:
        metrics = row.get("metrics") or {}
        if not metrics.get("time"):
            continue
        label = f"{row.get('asset')} / {row.get('signal/quantity')}"
        unit = row.get("unit") or ""
        metric_note = _trace_metric_note(metrics)
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=metrics["time"], y=metrics["reference"], mode="lines", name="reference"))
        fig.add_trace(go.Scatter(x=metrics["time"], y=metrics["candidate"], mode="lines", name="candidate"))
        values = [
            float(value)
            for series in (metrics.get("reference", []), metrics.get("candidate", []))
            for value in series
            if isinstance(value, (int, float))
        ]
        shapes, annotations = _event_shapes(events or [], min(values, default=0.0), max(values, default=1.0))
        fig.update_layout(
            title=f"{label}<br><sup>{metric_note}</sup>" if metric_note else label,
            xaxis_title="Time (s)",
            yaxis_title=unit or "Value",
            template="plotly_white",
            height=300,
            # Keep the per-trace metric note and the series legend in separate
            # regions.  The previous top legend overlapped the two-line title
            # on narrow report cards and made a readable mismatch look like a
            # rendering defect.
            margin=dict(t=58, b=72, l=50, r=20),
            legend=dict(orientation="h", yanchor="top", y=-0.24, xanchor="left", x=0),
            shapes=shapes,
            annotations=annotations,
        )
        figs.append((label, _div(fig)))
    return figs


def comparison_residual_figures(
    rows: list[dict], *, events: list[dict] | None = None
) -> list[tuple[str, str]]:
    """One small residual chart per signal, matching
    ``comparison_timeseries_figures`` -- same reasoning: different signals
    have different units/scales and must not share one axis."""
    figs: list[tuple[str, str]] = []
    for row in rows:
        metrics = row.get("metrics") or {}
        if not metrics.get("time"):
            continue
        label = f"{row.get('asset')} / {row.get('signal/quantity')}"
        unit = row.get("unit") or ""
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(x=metrics["time"], y=metrics.get("residual", []), mode="lines", name="residual")
        )
        fig.add_hline(y=0, line=dict(color="#999", dash="dash"))
        residuals = [float(value) for value in metrics.get("residual", []) if isinstance(value, (int, float))]
        shapes, annotations = _event_shapes(events or [], min(residuals, default=-1.0), max(residuals, default=1.0))
        fig.update_layout(
            title=label,
            xaxis_title="Time (s)",
            yaxis_title=f"Residual ({unit})" if unit else "Residual",
            template="plotly_white",
            height=240,
            margin=dict(t=40, b=30, l=50, r=20),
            shapes=shapes,
            annotations=annotations,
        )
        figs.append((label, _div(fig)))
    return figs


def three_way_timeseries_figures(traces: list[dict]) -> list[tuple[str, str]]:
    """One key parameter per chart, with every available engine overlaid."""
    figs: list[tuple[str, str]] = []
    colors = {"source": "#2563eb", "cept-pfd": "#dc2626", "opendss": "#059669"}
    for trace in traces:
        series = trace.get("series") or {}
        if not series:
            continue
        label = f"{trace.get('asset')} / {trace.get('quantity')}"
        fig = go.Figure()
        for role in ("source", "cept-pfd", "opendss"):
            values = series.get(role)
            if not values:
                continue
            fig.add_trace(
                go.Scatter(
                    x=values.get("time", []),
                    y=values.get("values", []),
                    mode="lines",
                    name=role,
                    line=dict(color=colors[role]),
                )
            )
        fig.update_layout(
            title=label,
            xaxis_title="Time (s)",
            yaxis_title=trace.get("unit") or "Value",
            template="plotly_white",
            height=300,
            margin=dict(t=40, b=30, l=50, r=20),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        figs.append((label, _div(fig)))
    return figs


def three_way_error_index_figure(rows: list[dict]) -> str:
    """Compare both candidates against the source on aligned parameters."""
    valid = [row for row in rows if isinstance(row.get("normalized_error_index"), (int, float))]
    if not valid:
        return "<p class='note'>No numerical error index available.</p>"
    by_key: dict[tuple, dict[str, dict]] = {}
    for row in valid:
        key = (row.get("scope"), row.get("asset"), row.get("signal/quantity"), row.get("unit"))
        by_key.setdefault(key, {})[str(row.get("pair"))] = row
    keys = sorted(
        by_key,
        key=lambda key: max(float(row["normalized_error_index"]) for row in by_key[key].values()),
        reverse=True,
    )[:24]
    labels = [f"{key[1]} / {key[2]}" for key in keys]
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=labels,
            y=[0.0] * len(keys),
            mode="lines+markers",
            name="source (reference)",
            line=dict(color="#2563eb"),
        )
    )
    for pair, color in (("cept-pfd", "#dc2626"), ("opendss", "#059669")):
        fig.add_trace(
            go.Bar(
                x=labels,
                y=[by_key[key].get(pair, {}).get("normalized_error_index") for key in keys],
                name=pair,
                marker_color=color,
            )
        )
    fig.update_layout(
        title=f"Top {len(keys)} parameter errors vs source (source baseline = 0)",
        yaxis_title="Normalized error index",
        template="plotly_white",
        height=420,
        barmode="group",
        xaxis=dict(tickangle=-55),
        margin=dict(t=50, b=150, l=60, r=20),
    )
    return _div(fig)


def _trace_metric_note(metrics: dict) -> str:
    """Compact, honest per-trace metrics for the chart heading."""
    labels = (
        ("init", metrics.get("initial_value_error")),
        ("max", metrics.get("maximum_absolute_error")),
        ("RMSE", metrics.get("rmse")),
        ("NRMSE", metrics.get("nrmse")),
        ("peak", metrics.get("peak_error")),
        ("nadir", metrics.get("nadir_error")),
        ("final", metrics.get("final_value_error")),
    )
    return " · ".join(
        f"{name}={float(value):.4g}" for name, value in labels if isinstance(value, (int, float))
    )


def status_confusion_figure(rows: list[dict]) -> str:
    labels = ["match", "mismatch", "observed", "blocked"]
    counts = [
        sum(r.get("status") == "pass" for r in rows),
        sum(r.get("status") == "fail" for r in rows),
        sum(r.get("status") == "observed-no-acceptance-criterion" for r in rows),
        sum(r.get("status") == "blocked" for r in rows),
    ]
    fig = go.Figure(go.Bar(x=labels, y=counts, marker_color=["#2ca02c", "#d62728", "#f59e0b", "#9ca3af"]))
    fig.update_layout(title="Status comparison", yaxis_title="Items", template="plotly_white", height=340)
    return _div(fig)
