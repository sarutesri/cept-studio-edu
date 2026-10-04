"""The 16 parallel HTML report entry points, moved out of ``render.py``
(WP11 phase 3.6).  They stay in ONE module: they are a *shape*, not debt,
and must not become 16 files.  They consume the shared rendering shell
(Jinja environment, vendored JS, SLD/context/gallery helpers) from
``cept.reporting.render``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from cept import __version__ as CEPT_VERSION
from cept.reporting import figures as F
from cept.reporting.render import (
    _ENV,
    _case_context,
    _comparison_interactive_graph,
    _comparison_sld_gallery,
    _phase_str,
    _sld_caption,
    _sld_json,
    _sld_snapshot_views,
    _three_way_sld_gallery,
    branch_flow_figure,
    table_summary_policy,
)
from cept.schema.case import Case
from cept.schema.result import StudyResult, ValidationReport
from cept.schema.sld import SLDModel


def _require_matching_case(study: StudyResult, case: Case) -> None:
    """Fail closed when a report pairs a result with a foreign Case."""
    expected = case.fingerprint()
    if study.case_fingerprint != expected:
        raise ValueError(
            "report Case/result mismatch: StudyResult.case_fingerprint "
            f"{study.case_fingerprint!r} does not match Case fingerprint {expected!r}"
        )



def render_load_flow_report(study: StudyResult, case: Case, *, out_path: Optional[str | Path] = None) -> str:
    """Render a load-flow study report. The SLD is always included; if the
    study ran an experiment, before/after diagrams are shown side by side."""
    _require_matching_case(study, case)
    lf = study.load_flow
    assert lf is not None, "load-flow report requires a load_flow result"
    std = case.standards
    vbus = F.voltages_by_bus(lf)

    all_v = [bv.v_pu for bv in lf.bus_voltages]
    vmin, vmax = (min(all_v), max(all_v)) if all_v else (0.0, 0.0)

    bus_rows = []
    for bus in vbus:
        phases = vbus[bus]
        ok = all(std.v_min_pu <= v <= std.v_max_pu for v in phases.values())
        flag = ""
        if not ok:
            lo = any(v < std.v_min_pu for v in phases.values())
            hi = any(v > std.v_max_pu for v in phases.values())
            flag = "UNDER" if lo and not hi else "OVER" if hi and not lo else "OUT"
        bus_rows.append(
            {
                "bus": bus.upper(),
                "a": _phase_str(vbus, bus, 1),
                "b": _phase_str(vbus, bus, 2),
                "c": _phase_str(vbus, bus, 3),
                "ok": ok,
                "flag": flag,
            }
        )

    editable_parameters = []
    if getattr(case.network, "kind", None) == "inline" and case.network.inline is not None:
        net = case.network.inline
        editable_parameters.extend(
            {"label": f"Load {item.id} P (kW)", "path": f"network.loads.{item.id}.kw", "value": item.kw}
            for item in net.loads
        )
        editable_parameters.extend(
            {"label": f"Load {item.id} PF", "path": f"network.loads.{item.id}.pf", "value": item.pf}
            for item in net.loads
        )
        editable_parameters.extend(
            {
                "label": f"Generator {item.name} P (kW)",
                "path": f"network.generators.{item.name}.kw",
                "value": item.kw,
            }
            for item in net.generators
        )
        editable_parameters.extend(
            {
                "label": f"Generator {item.name} V (pu)",
                "path": f"network.generators.{item.name}.pu",
                "value": item.pu,
            }
            for item in net.generators
        )
        editable_parameters.extend(
            {
                "label": f"Transformer {item.name} uk (%)",
                "path": f"network.transformers.{item.name}.uk_pct",
                "value": item.uk_pct,
            }
            for item in net.transformers
        )

    # WP19 size-adaptive policy: above the threshold the section-4 bus table
    # shows the out-of-range/worst buses first and the section-5 branch table
    # is summarised with a loss chart of every branch instead of a wall of rows.
    bus_policy = table_summary_policy(
        bus_rows, sort_key=lambda r: (not r["ok"], r["bus"])
    )
    branches = study.sld.edges if study.sld else []
    branch_policy = table_summary_policy(
        branches, sort_key=lambda e: (e.losses_kw, e.id)
    )

    html = _ENV.get_template("load_flow.html").render(
        title=f"Load-Flow Study â€” {case.meta.name}",
        subtitle=case.meta.description or "Distribution power-flow analysis",
        cept_version=CEPT_VERSION,
        engine=study.engine,
        engine_version=study.engine_version,
        fingerprint=study.case_fingerprint,
        created_at=study.created_at,
        converged=lf.converged,
        iterations=lf.iterations,
        source_kw=f"{lf.source_p_kw:.1f}" if lf.source_p_kw is not None else "â€”",
        loss_kw=f"{lf.total_loss_kw:.1f}" if lf.total_loss_kw is not None else "â€”",
        vmin=vmin,
        vmax=vmax,
        v_min=std.v_min_pu,
        v_max=std.v_max_pu,
        voltage_method=std.voltage_method,
        study_type=study.study_type,
        network_desc=_network_desc(case),
        frequency_hz=case.network.frequency_hz,
        source_mode=case.network.source.kind,
        description=case.meta.description or "â€”",
        voltage_div=F.voltage_profile_figure(lf, std.v_min_pu, std.v_max_pu),
        bus_rows=bus_policy["rows"],
        bus_summary_note=bus_policy["note"],
        branches=branch_policy["rows"],
        branch_summary_note=branch_policy["note"],
        branch_summary_div=branch_flow_figure(branches) if branch_policy["folded"] else "",
        total_loss_kw=lf.total_loss_kw or 0.0,
        editable_parameters=editable_parameters,
        # SLD
        experiment_name=study.experiment_name,
        sld_json=_sld_json(study.sld, study.case_fingerprint),
        sld_caption=_sld_caption(study.sld),
        sld_after_json=_sld_json(study.sld_after, study.case_fingerprint),
        sld_after_caption=_sld_caption(study.sld_after),
        sld_snapshots=_sld_snapshot_views(study),
        **_case_context(case),
    )
    if out_path:
        Path(out_path).write_text(html, encoding="utf-8")
    return html


def render_validation_report(
    vr: ValidationReport,
    *,
    engine: str = "opendss",
    engine_version: str = "",
    fingerprint: str = "â€”",
    config_note: str = "",
    sld: Optional[SLDModel] = None,
    out_path: Optional[str | Path] = None,
) -> str:
    tol = vr.items[0].tol_abs if vr.items else 0.0
    html = _ENV.get_template("validation.html").render(
        title=f"Validation Report â€” {vr.reference_name}",
        subtitle=f"Case: {vr.case_name}",
        cept_version=CEPT_VERSION,
        engine=engine,
        engine_version=engine_version,
        fingerprint=fingerprint,
        created_at=vr.created_at,
        passed=vr.passed,
        n_pass=vr.n_pass,
        n_total=len(vr.items),
        max_err=vr.max_error_abs,
        tol=tol,
        reference_name=vr.reference_name,
        config_note=config_note or "As configured by the validation harness.",
        scatter_div=F.validation_scatter_figure(vr),
        error_div=F.validation_error_figure(vr),
        items=vr.items,
        sld_json=_sld_json(sld),
        sld_caption=_sld_caption(sld),
    )
    if out_path:
        Path(out_path).write_text(html, encoding="utf-8")
    return html


def render_pfd_pdf_validation_report(
    comparison: dict,
    *,
    out_path: Optional[str | Path] = None,
    title: str | None = None,
) -> str:
    """Render PFD-only or optional PFD/PDF comparison using the standard shell."""
    rows = list(comparison.get("items", []))
    summary = comparison.get("error_index") or comparison.get("summary") or {}
    pfd_only = not comparison.get("pdf_manifest")
    sld_gallery = _comparison_sld_gallery(
        comparison, interactive_graph=_comparison_interactive_graph(comparison)
    )
    html = _ENV.get_template("pfd_pdf_validation.html").render(
        title=title
        or ("PFD/CEPT model-fidelity validation report" if pfd_only else "PFD/PDF/CEPT validation report"),
        subtitle=(
            "Original PowerFactory PFD fresh rerun <-> CEPT fresh rerun"
            if pfd_only
            else "Original PowerFactory PFD fresh rerun <-> CEPT fresh rerun <-> published PDF"
        ),
        cept_version=CEPT_VERSION,
        engine="PowerFactory / CEPT",
        engine_version="see provenance",
        fingerprint="see comparison hashes",
        created_at=comparison.get("created_at", ""),
        summary=summary,
        rows=rows,
        error_index_div=F.error_index_bar_figure(summary, rows),
        heatmap_div=F.error_heatmap_figure(rows),
        scatter_div=F.reference_candidate_scatter_figure(rows),
        timeseries_figs=F.comparison_timeseries_figures(rows),
        residual_figs=F.comparison_residual_figures(rows),
        status_div=F.status_confusion_figure(rows),
        sld_gallery=sld_gallery,
        comparison=comparison,
    )
    if out_path:
        Path(out_path).write_text(html, encoding="utf-8")
    return html


def render_open_reference_report(
    study: StudyResult,
    manifest: dict,
    *,
    out_path: Optional[str | Path] = None,
) -> str:
    """Render an independent reference run using the same offline report shell."""
    dynamics = study.dynamics
    html = _ENV.get_template("independent_reference.html").render(
        title=f"Independent Reference â€” {study.case_name}",
        subtitle="Source-pinned solver run; not a substitute for an incomplete PFD controller",
        cept_version=CEPT_VERSION,
        engine=study.engine,
        engine_version=study.engine_version,
        fingerprint=study.case_fingerprint,
        created_at=study.created_at,
        manifest=manifest,
        dynamics=dynamics,
        load_flow=study.load_flow,
        angle_div=F.dynamics_angle_figure(dynamics) if dynamics else "",
        freq_div=F.dynamics_frequency_figure(dynamics) if dynamics else "",
        volt_div=F.dynamics_voltage_figure(dynamics) if dynamics else "",
        power_div=F.dynamics_power_figure(dynamics) if dynamics else "",
        channels=[
            {
                "name": c.name,
                "unit": c.unit,
                "samples": len(c.values),
                "first": c.values[0] if c.values else None,
                "last": c.values[-1] if c.values else None,
            }
            for trace in dynamics.monitors
            for c in trace.channels
        ]
        if dynamics
        else [],
    )
    if out_path:
        Path(out_path).write_text(html, encoding="utf-8")
    return html


def render_open_reference_comparison(
    comparison: dict,
    *,
    out_path: Optional[str | Path] = None,
    sld_data: Optional[dict] = None,
    native_sld_b64: Optional[str] = None,
) -> str:
    """Render an independent-reference comparison, reusing CEPT chart helpers.

    Parameters
    ----------
    sld_data:
        ECharts SLD graph payload with ``nodes`` (list) and ``links`` (list).
    native_sld_b64:
        Base64-encoded native PowerFactory SLD PNG image for inline embedding.
    """
    summary = comparison.get("error_index") or {}
    rows = list(comparison.get("items", []))
    chart_rows = [row for row in rows if (row.get("metrics") or {}).get("time")]
    if comparison.get("validation_basis") == "same-typed-case-cross-engine-diagnostic":
        # The cover report must show only channels that were actually paired.
        # Candidate-only OpenDSS monitor channels remain in the verdict table,
        # but putting them beside a PF trace would visually imply a mapping
        # that the reviewed registry did not establish.
        chart_rows = [row for row in chart_rows if row.get("presence") == "both"]
    preferred = [
        row
        for row in chart_rows
        if any(token in str(row.get("signal/quantity", "")).lower() for token in ("delta", "omega", "v bus"))
    ]
    chart_rows = (preferred + [row for row in chart_rows if row not in preferred])[:12]
    basis = str(comparison.get("validation_basis", "independent-reference-repeatability"))
    subtitle = {
        "published-reference-fixture": "Fresh CEPT solver output versus a frozen published reference fixture",
        "source-derived-reduced-model": "Fresh candidate output versus a fresh source-derived reduced reference; partial scope only",
        "independent-reference-repeatability": "Fresh solver run versus fresh solver run; observed repeatability only",
        "same-typed-case-cross-engine-diagnostic": (
            "PowerFactory and OpenDSS solver artifacts from one typed Case; "
            "cross-engine diagnostic evidence, not project validation"
        ),
    }.get(basis, "Fresh solver output comparison; observed evidence only")
    sld_gallery = _comparison_sld_gallery(
        comparison,
        interactive_graph=sld_data or _comparison_interactive_graph(comparison),
        supplied_native=native_sld_b64,
    )
    html = _ENV.get_template("independent_reference_comparison.html").render(
        title=comparison.get("title", "Independent Reference Comparison"),
        subtitle=subtitle,
        cept_version=CEPT_VERSION,
        engine=(
            f"{comparison.get('reference_engine', 'reference')} / "
            f"{comparison.get('candidate_engine', 'candidate')}"
        ),
        engine_version="see per-run provenance",
        fingerprint="see result hashes",
        created_at=comparison.get("created_at", ""),
        summary=summary,
        rows=rows,
        comparison=comparison,
        error_index_div=F.error_index_bar_figure(summary, rows),
        heatmap_div=F.error_heatmap_figure(rows),
        scatter_div=F.reference_candidate_scatter_figure(rows),
        timeseries_figs=F.comparison_timeseries_figures(
            chart_rows, events=comparison.get("events")
        ),
        residual_figs=F.comparison_residual_figures(chart_rows, events=comparison.get("events")),
        status_div=F.status_confusion_figure(rows),
        sld_data=sld_data,
        native_sld_b64=native_sld_b64,
        sld_gallery=sld_gallery,
    )
    if out_path:
        Path(out_path).write_text(html, encoding="utf-8")
    return html


def render_three_way_comparison_report(
    comparison: dict,
    *,
    out_path: Optional[str | Path] = None,
) -> str:
    """Render one report for source PFD, CEPT PFD, and CEPT OpenDSS."""
    html = _ENV.get_template("three_way_comparison.html").render(
        title=comparison.get("title", "Three-way solver comparison"),
        subtitle="a. source example.pfd  â†”  b. CEPT PowerFactory .pfd  â†”  c. CEPT OpenDSS",
        cept_version=CEPT_VERSION,
        engine="PowerFactory / OpenDSS",
        engine_version="see per-role provenance",
        fingerprint="see per-run hashes",
        created_at=comparison.get("created_at", ""),
        comparison=comparison,
        roles=list(comparison.get("roles", {}).values()),
        sld_gallery=_three_way_sld_gallery(comparison),
        error_index=comparison.get("error_index") or {},
        error_div=F.three_way_error_index_figure(comparison.get("items", [])),
        timeseries_figs=F.three_way_timeseries_figures(comparison.get("traces", [])),
        rows=comparison.get("items", []),
        analysis=comparison.get("analysis", []),
    )
    if out_path:
        Path(out_path).write_text(html, encoding="utf-8")
    return html


def render_ibr_report(study: StudyResult, case, *, out_path=None) -> str:
    _require_matching_case(study, case)
    lf = study.load_flow
    assert lf is not None
    std = case.standards
    vbus = F.voltages_by_bus(lf)
    all_v = [bv.v_pu for bv in lf.bus_voltages]
    vmin, vmax = (min(all_v), max(all_v)) if all_v else (0.0, 0.0)
    pv_ders = [d for d in lf.der_outputs if d.kind == "pv"]
    bus_rows = []
    for bus in vbus:
        phases = vbus[bus]
        ok = all(std.v_min_pu <= v <= std.v_max_pu for v in phases.values())
        lo = any(v < std.v_min_pu for v in phases.values())
        hi = any(v > std.v_max_pu for v in phases.values())
        flag = "UNDER" if lo and not hi else "OVER" if hi and not lo else "OUT"
        bus_rows.append(
            {
                "bus": bus.upper(),
                "a": _phase_str(vbus, bus, 1),
                "b": _phase_str(vbus, bus, 2),
                "c": _phase_str(vbus, bus, 3),
                "ok": ok,
                "flag": flag,
            }
        )
    bus_policy = table_summary_policy(bus_rows, sort_key=lambda r: (not r["ok"], r["bus"]))
    html = _ENV.get_template("ibr.html").render(
        title=f"IBR / Smart-Inverter Study â€” {case.meta.name}",
        subtitle=case.meta.description or "IEEE 1547 Volt-VAR / Volt-Watt inverter study",
        cept_version=CEPT_VERSION,
        engine=study.engine,
        engine_version=study.engine_version,
        fingerprint=study.case_fingerprint,
        created_at=study.created_at,
        converged=lf.converged,
        vmin=vmin,
        vmax=vmax,
        v_min=std.v_min_pu,
        v_max=std.v_max_pu,
        loss_kw=lf.total_loss_kw or 0.0,
        total_pv_kw=round(sum(d.p_kw for d in pv_ders), 1),
        total_pv_kvar=round(sum(d.q_kvar for d in pv_ders), 1),
        network_desc=_network_desc(case),
        ders=case.ders,
        der_outputs=lf.der_outputs,
        sld_json=_sld_json(study.sld, study.case_fingerprint),
        sld_caption=_sld_caption(study.sld),
        voltage_div=F.voltage_profile_figure(lf, std.v_min_pu, std.v_max_pu),
        bus_rows=bus_policy["rows"],
        bus_summary_note=bus_policy["note"],
        **_case_context(case),
    )
    if out_path:
        Path(out_path).write_text(html, encoding="utf-8")
    return html


def render_dynamics_report(study: StudyResult, case, *, out_path=None) -> str:
    _require_matching_case(study, case)
    d = study.dynamics
    assert d is not None, "dynamics report requires a dynamics result"
    initialization = d.initialization or {}
    initialization_rows = []
    for generator in initialization.get("generators", []) or []:
        for channel in generator.get("channels", []) or []:
            initialization_rows.append(
                {
                    "generator": generator.get("name", ""),
                    "channel": channel.get("name", ""),
                    "source_channel": channel.get("source_channel", ""),
                    "value": channel.get("value"),
                    "unit": channel.get("unit", ""),
                }
            )
    html = _ENV.get_template("dynamics.html").render(
        title=f"Dynamics Study â€” {case.meta.name}",
        subtitle=case.meta.description or "Electromechanical (RMS) transient simulation",
        cept_version=CEPT_VERSION,
        engine=study.engine,
        engine_version=study.engine_version,
        fingerprint=study.case_fingerprint,
        created_at=study.created_at,
        converged=d.converged,
        stable=d.stable,
        freq_nadir=d.freq_nadir_hz,
        freq_peak=d.freq_peak_hz,
        max_angle=d.max_rotor_angle_deg,
        duration=d.duration,
        stepsize=d.stepsize,
        generators=d.generators,
        events=d.events,
        dynamic_model_mapping=study.extra.get("dynamic_model_mapping"),
        initialization_status=initialization.get("status", "blocked"),
        initialization_source=initialization.get("source", ""),
        initialization_time=initialization.get("time_s"),
        initialization_rows=initialization_rows,
        sld_snapshots=_sld_snapshot_views(study),
        settling_note=d.settling_note or "â€”",
        network_desc=_network_desc(case),
        sld_json=_sld_json(study.sld, study.case_fingerprint),
        sld_caption=_sld_caption(study.sld),
        angle_div=F.dynamics_angle_figure(d),
        freq_div=F.dynamics_frequency_figure(d),
        volt_div=F.dynamics_voltage_figure(d),
        power_div=F.dynamics_power_figure(d),
        **_case_context(case),
    )
    if out_path:
        Path(out_path).write_text(html, encoding="utf-8")
    return html


def render_emt_report(study: StudyResult, case: Case, *, out_path: Optional[str | Path] = None) -> str:
    _require_matching_case(study, case)
    emt = study.emt
    assert emt is not None, "EMT report requires an emt result"
    channel_rows = [
        {
            "name": trace.name if len(trace.channels) == 1 else f"{trace.name}:{ch.name}",
            "samples": len(ch.values),
            "first": f"{ch.values[0]:.6g}" if ch.values else "â€”",
            "last": f"{ch.values[-1]:.6g}" if ch.values else "â€”",
        }
        for trace in emt.channels
        for ch in trace.channels
    ]
    html = _ENV.get_template("emt.html").render(
        title=f"EMT Study â€” {case.meta.name}",
        subtitle=case.meta.description or "Electromagnetic-transient waveform channels",
        cept_version=CEPT_VERSION,
        engine=study.engine,
        engine_version=study.engine_version,
        fingerprint=study.case_fingerprint,
        created_at=study.created_at,
        converged=emt.converged,
        samples=emt.samples,
        duration=emt.duration_s,
        requested_stepsize=case.emt.stepsize_s if case.emt else emt.stepsize_s,
        actual_stepsize=emt.stepsize_s,
        network_desc=_network_desc(case),
        channel_names=[trace.name for trace in emt.channels],
        channel_rows=channel_rows,
        waveform_div=F.emt_waveform_figure(emt),
        **_case_context(case),
    )
    if out_path:
        Path(out_path).write_text(html, encoding="utf-8")
    return html


def render_time_series_report(
    study: StudyResult, case: Case, *, out_path: Optional[str | Path] = None
) -> str:
    _require_matching_case(study, case)
    ts = study.time_series
    assert ts is not None, "time-series report requires a time_series result"
    html = _ENV.get_template("time_series.html").render(
        title=f"QSTS Study â€” {case.meta.name}",
        subtitle=case.meta.description or "Quasi-static time-series power flow",
        cept_version=CEPT_VERSION,
        engine=study.engine,
        engine_version=study.engine_version,
        fingerprint=study.case_fingerprint,
        created_at=study.created_at,
        converged=ts.converged,
        duration=ts.duration_s,
        stepsize=ts.stepsize_s,
        min_voltage=ts.min_voltage_pu,
        max_voltage=ts.max_voltage_pu,
        violation_count=ts.violation_count,
        snapshots=ts.snapshots,
        network_desc=_network_desc(case),
        sld_json=_sld_json(study.sld, study.case_fingerprint),
        sld_caption=_sld_caption(study.sld),
        **_case_context(case),
    )
    if out_path:
        Path(out_path).write_text(html, encoding="utf-8")
    return html


def render_harmonics_report(study: StudyResult, case: Case, *, out_path: Optional[str | Path] = None) -> str:
    _require_matching_case(study, case)
    h = study.harmonics
    assert h is not None, "harmonics report requires a harmonics result"
    html = _ENV.get_template("harmonics.html").render(
        title=f"Harmonic Study â€” {case.meta.name}",
        subtitle=case.meta.description or "OpenDSS harmonic frequency sweep",
        cept_version=CEPT_VERSION,
        engine=study.engine,
        engine_version=study.engine_version,
        fingerprint=study.case_fingerprint,
        created_at=study.created_at,
        converged=h.converged,
        fundamental_hz=h.fundamental_hz,
        snapshots=h.snapshots,
        thd=h.thd_v_pct_by_bus,
        network_desc=_network_desc(case),
        sld_json=_sld_json(study.sld, study.case_fingerprint),
        sld_caption=_sld_caption(study.sld),
        **_case_context(case),
    )
    if out_path:
        Path(out_path).write_text(html, encoding="utf-8")
    return html


def render_protection_report(study: StudyResult, case: Case, *, out_path: Optional[str | Path] = None) -> str:
    _require_matching_case(study, case)
    p = study.protection
    assert p is not None, "protection report requires a protection result"
    html = _ENV.get_template("protection.html").render(
        title=f"Protection Coordination â€” {case.meta.name}",
        subtitle="Fault current and inverse-time relay screening",
        cept_version=CEPT_VERSION,
        engine=study.engine,
        engine_version=study.engine_version,
        fingerprint=study.case_fingerprint,
        created_at=study.created_at,
        fault_bus=p.fault_bus,
        fault_type=p.fault_type,
        fault_current=p.fault_current_a,
        relays=p.relays,
        method=p.calculation_method,
        network_desc=_network_desc(case),
        sld_json=_sld_json(study.sld, study.case_fingerprint),
        sld_caption=_sld_caption(study.sld),
        **_case_context(case),
    )
    if out_path:
        Path(out_path).write_text(html, encoding="utf-8")
    return html


def render_gic_report(study: StudyResult, case, *, out_path=None) -> str:
    _require_matching_case(study, case)
    g = study.gic
    assert g is not None, "GIC report requires a gic result"
    elements_policy = table_summary_policy(g.elements, sort_key=lambda e: (e.gic_amps, e.name))
    html = _ENV.get_template("gic.html").render(
        title=f"GIC Study â€” {case.meta.name}",
        subtitle="Geomagnetically induced current analysis",
        cept_version=CEPT_VERSION,
        engine=study.engine,
        engine_version=study.engine_version,
        fingerprint=study.case_fingerprint,
        created_at=study.created_at,
        max_gic=g.max_gic_a,
        total_gic=g.total_gic_a,
        n_transformers=g.n_transformers,
        n_lines=g.n_lines,
        frequency=case.study.options.get("frequency", 0.1),
        e_field=g.e_field_v_per_km,
        network_desc=_network_desc(case),
        gic_div=F.gic_bar_figure(g),
        elements=elements_policy["rows"],
        elements_summary_note=elements_policy["note"],
        **_case_context(case),
    )
    if out_path:
        Path(out_path).write_text(html, encoding="utf-8")
    return html


_FAULT_LONG = {
    "3ph": "Three-phase bolted fault",
    "slg": "Single-line-to-ground fault",
    "ll": "Line-to-line fault",
    "llg": "Double-line-to-ground fault",
}


def render_fault_report(study: StudyResult, case: Case, *, out_path: Optional[str | Path] = None) -> str:
    _require_matching_case(study, case)
    f = study.fault
    assert f is not None, "fault report requires a fault result"
    html = _ENV.get_template("fault.html").render(
        title=f"Fault Analysis â€” {case.meta.name}",
        subtitle=f"{_FAULT_LONG.get(f.fault_type, f.fault_type)} at bus {f.bus}",
        cept_version=CEPT_VERSION,
        engine=study.engine,
        engine_version=study.engine_version,
        fingerprint=study.case_fingerprint,
        created_at=study.created_at,
        bus=f.bus.upper(),
        ftype=f.fault_type.upper(),
        ftype_long=_FAULT_LONG.get(f.fault_type, f.fault_type),
        rf=f.rf_ohm,
        phases=", ".join({1: "A", 2: "B", 3: "C"}[p] for p in f.phases),
        total_i=f.total_fault_current_a or 0.0,
        vmin=f.min_voltage_pu or 0.0,
        currents=f.currents,
        network_desc=_network_desc(case),
        sld_json=_sld_json(study.sld, study.case_fingerprint),
        sld_caption=_sld_caption(study.sld),
        sag_div=F.sag_profile_figure(f),
        study_rows=f.study_rows,
        faultcur_div=F.fault_current_figure(f) if f.study_rows else "",
        **_case_context(case),
    )
    if out_path:
        Path(out_path).write_text(html, encoding="utf-8")
    return html


def render_hosting_capacity_report(
    study: StudyResult, case: Case, *, out_path: Optional[str | Path] = None
) -> str:
    _require_matching_case(study, case)
    hc = study.hosting_capacity
    assert hc is not None, "HC report requires a hosting_capacity result"
    mn = hc.min_hc
    items_policy = table_summary_policy(
        sorted(hc.items, key=lambda i: i.hc_kw),
        sort_key=lambda i: (-i.hc_kw, i.bus),
    )
    html = _ENV.get_template("hosting_capacity.html").render(
        title=f"Hosting Capacity â€” {case.meta.name}",
        subtitle="Maximum violation-free PV injection per bus",
        cept_version=CEPT_VERSION,
        engine=study.engine,
        engine_version=study.engine_version,
        fingerprint=study.case_fingerprint,
        created_at=study.created_at,
        n_buses=len(hc.items),
        criterion=hc.criterion,
        v_max=hc.v_max_pu,
        baseline_vmax=hc.baseline_v_max_pu or 0.0,
        min_bus=(mn.bus.upper() if mn else "â€”"),
        min_hc=(mn.hc_kw if mn else 0.0),
        der_type=hc.der_type,
        max_kw=hc.max_search_kw,
        network_desc=_network_desc(case),
        sld_json=_sld_json(study.sld, study.case_fingerprint),
        sld_caption=_sld_caption(study.sld),
        hc_div=F.hosting_capacity_figure(hc),
        items=items_policy["rows"],
        items_summary_note=items_policy["note"],
        **_case_context(case),
    )
    if out_path:
        Path(out_path).write_text(html, encoding="utf-8")
    return html


def _network_desc(case: Case) -> str:
    n = case.network
    if n.kind == "builtin":
        return f"builtin: {n.name}"
    if n.kind == "dss_file":
        return f"dss_file: {n.path}"
    return n.kind


def render_trust_dashboard(summary_payload: dict[str, Any], *, out_path: Optional[str | Path] = None) -> str:
    """Render curated-tier master trust dashboard."""
    tmpl_path = Path(__file__).parent / "templates" / "trust_dashboard.html"
    template_text = tmpl_path.read_text(encoding="utf-8")

    cases = summary_payload.get("cases", [])
    total_cases = summary_payload.get("total_cases", len(cases))
    pfd_parity_passes = summary_payload.get("pfd_parity_passes", 0)

    headline = f"{pfd_parity_passes}/{total_cases} cases at PFD parity"

    rows_html = []
    for c in cases:
        c_id = c.get("id", "")
        title = c.get("title", c_id)
        study_type = c.get("study_type", "load_flow")
        eval_info = c.get("evaluation", {})

        pfd_verdict = eval_info.get("pfd_parity_verdict", "SKIPPED")
        dss_verdict = eval_info.get("cross_engine_verdict", "SKIPPED")
        rows_eval = eval_info.get("total_rows", 0)

        pfd_badge_class = (
            "badge-pass" if pfd_verdict == "PASS" else "badge-fail" if pfd_verdict == "FAIL" else "badge-obs"
        )
        dss_badge_class = (
            "badge-pass" if dss_verdict == "PASS" else "badge-fail" if dss_verdict == "FAIL" else "badge-obs"
        )

        report_dir = c.get("report_dir", "")
        report_link = f'<a href="{c_id}/comparison.html">comparison.html</a>' if report_dir else "â€”"

        rows_html.append(
            f"<tr>"
            f"<td><strong>{title}</strong><br><small style='color: var(--text-muted);'>{c_id}</small></td>"
            f"<td><code>{study_type}</code></td>"
            f"<td><span class='badge {pfd_badge_class}'>{pfd_verdict}</span></td>"
            f"<td><span class='badge {dss_badge_class}'>{dss_verdict}</span></td>"
            f"<td>{rows_eval}</td>"
            f"<td>{report_link}</td>"
            f"</tr>"
        )

    html = template_text.replace("__HEADLINE_SUMMARY__", headline)
    html = html.replace("__TOTAL_CASES__", str(total_cases))
    html = html.replace("__PFD_PARITY_PASSES__", str(pfd_parity_passes))
    html = html.replace("__TABLE_ROWS__", "\n".join(rows_html))

    if out_path:
        Path(out_path).write_text(html, encoding="utf-8")
    return html
