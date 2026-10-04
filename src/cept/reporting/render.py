"""Render HTML reports from results + case context."""

from __future__ import annotations

import base64
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from cept.schema.case import Case
from cept.schema.result import StudyResult
from cept.schema.sld import SLDEdge, SLDGen, SLDLoad, SLDModel, SLDNode

_ENV = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
    autoescape=select_autoescape(["html"]),
)

# --- WP19: one charting library, declared size budget -----------------------
# Measured 2026-08-04 on `cept run examples/cases/ieee13_load_flow.json
# --engine opendss`: report.html was 7,531,025 bytes because the bundle
# shipped TWO charting libraries as base64 — plotly.js full build (6,078,417
# b64 chars) plus ECharts (1,372,425 b64 chars) — for a 13-bus feeder whose
# content is ~36 KB.  WP19 ships ONE library: the plotly.js *cartesian*
# partial bundle, v2.35.2 (the exact version previously vendored in full, so
# python-side figure divs are unchanged).  That bundle is 1,358,851 bytes
# (1,811,802 b64 chars); the measured report is ~1.9 MB.  Budget = 2.5 MB
# (~30% headroom over the measurement).
REPORT_SIZE_BUDGET_BYTES = 2_500_000

# --- WP19: size-adaptive table/chart policy ----------------------------------
# Any table with more than TABLE_SUMMARY_THRESHOLD rows is summarised to the
# top TABLE_TOP_N rows (worst-first), an explicit note names exactly what was
# folded, and a chart of the full data is rendered instead of a wall of rows.
TABLE_SUMMARY_THRESHOLD = 25
TABLE_TOP_N = 12


@lru_cache(maxsize=None)
def _vendor_js_b64(filename: str) -> str:
    """Read the vendored plotly library for inlining into report.html.

    Reports get written to arbitrary run directories (not always alongside the
    installed package), so a relative ``<script src>`` would break the moment a
    report is moved or shared; inlining keeps report.html fully self-contained
    and viewable with no network access.

    It is base64, not raw script text: large minified libraries contain
    HTML-parser sentinels such as ``<![CDATA[`` inside string literals, and a
    browser can enter the script escaped state and truncate the DOM even though
    the file is valid JavaScript.  Base64 is parser-safe.

    Encoding the file's bytes directly avoids materialising a second 5.6 MB
    copy as ``str``; the vendored files are UTF-8, so the output is identical
    to the decode/encode round trip this replaced.
    """
    path = Path(__file__).parent / "static" / filename
    return base64.b64encode(path.read_bytes()).decode("ascii")


class _LazyVendorJs:
    """Defer reading the ~1.4 MB vendored plotly bundle until a report render.

    This was a module-level global, so merely importing this module read the
    file, base64-encoded it, and held the payload for the life of the process.
    `cept.cli.main` imports the reporting stack transitively, so `cept doctor`,
    `cept case show` and every conduct subprocess paid it too.

    Jinja renders `{{ plotly_js_b64 }}` through `escape()`, which honours
    `__html__`; base64 contains no HTML-special characters, so the emitted
    bytes are unchanged.
    """

    __slots__ = ("_filename",)

    def __init__(self, filename: str) -> None:
        self._filename = filename

    def __html__(self) -> str:
        return _vendor_js_b64(self._filename)

    def __str__(self) -> str:
        return _vendor_js_b64(self._filename)


# Vendor payload is decoded/evaluated by base.html in the browser.  Do not
# thread it through each render_*_report() call.  WP19: exactly ONE charting
# library ships in the bundle (plotly.js cartesian build); the second
# (ECharts, previously the interactive-SLD renderer) was removed and the SLD
# now renders on plotly as well.
_ENV.globals["plotly_js_b64"] = _LazyVendorJs("plotly.min.js")


def short_engine_version(raw: str) -> str:
    """Collapse a full solver banner into a one-line engine identifier.

    WP19 footer fix: OpenDSS's ``dss.Basic.Version()`` returns a ~270-char
    banner; the footer used to dump it inline.  This keeps only the library
    name + version token (``DSS C-API Library version 0.14.5 revision ...``
    -> ``DSS C-API Library 0.14.5``).  Short strings pass through untouched.
    """
    if not raw:
        return raw
    text = " ".join(raw.split())
    match = re.search(r"([A-Za-z][A-Za-z0-9 .\-_/]{0,48}?)\s+version\s+(\d+(?:\.\d+){1,3})", text)
    if match:
        return f"{match.group(1).strip()} {match.group(2)}"
    if len(text) <= 80:
        return text
    return text[:80].rstrip() + "…"


_ENV.filters["cept_engine_version"] = short_engine_version


def table_summary_policy(
    rows: list[Any],
    *,
    threshold: int = TABLE_SUMMARY_THRESHOLD,
    top_n: int = TABLE_TOP_N,
    sort_key: Callable[[Any], Any] | None = None,
) -> dict[str, Any]:
    """WP19 size-adaptive table policy.

    Below ``threshold`` rows the table is shown whole.  Above it, keep the
    ``top_n`` worst rows (``sort_key`` descending) and fold the rest, with a
    note that names exactly what was folded.  Returns ``rows`` (the rows to
    render), ``folded`` (count hidden), ``note`` (None when nothing folded),
    and ``total``.
    """
    total = len(rows)
    if total <= threshold:
        return {"rows": rows, "folded": 0, "note": None, "total": total}
    ordered = sorted(rows, key=sort_key, reverse=True) if sort_key else rows
    kept = ordered[:top_n]
    folded = ordered[top_n:]

    def _id(item: Any) -> str:
        if isinstance(item, dict):
            return str(item.get("id") or item.get("bus") or item.get("name") or "")
        return str(getattr(item, "id", None) or getattr(item, "name", None) or "")

    listing = ", ".join(value for value in (_id(item) for item in folded) if value)
    if len(listing) > 400:
        listing = listing[:400] + "…"
    note = f"{total - top_n} of {total} rows folded (threshold {threshold}, top {top_n} shown)"
    if listing:
        note += f"; folded: {listing}"
    return {"rows": kept, "folded": total - top_n, "note": note, "total": total}


def branch_flow_figure(branches: list[SLDEdge], *, top_n: int = 15) -> str:
    """Bar chart of every branch's active loss (worst first) — the chart the
    WP19 policy renders when the section-5 branch table is summarised."""
    import plotly.graph_objects as go

    rows = sorted(branches, key=lambda e: e.losses_kw, reverse=True)[:top_n]
    fig = go.Figure(
        go.Bar(
            x=[e.id for e in rows],
            y=[e.losses_kw for e in rows],
            marker_color="#f59e0b",
            text=[f"{e.losses_kw:.1f} kW" for e in rows],
            textposition="outside",
        )
    )
    fig.update_layout(
        title=f"Branch active losses (top {len(rows)} of {len(branches)})",
        xaxis_title="Branch",
        yaxis_title="Loss (kW)",
        template="plotly_white",
        height=380,
    )
    return fig.to_html(full_html=False, include_plotlyjs=False, config={"responsive": True})


def _sld_json(sld: Optional[SLDModel], case_fingerprint: str | None = None) -> str:
    if sld is None:
        return "null"
    from cept.reporting.sld_runtime import build_sld_option_v2
    option = build_sld_option_v2(sld)
    if case_fingerprint:
        option["case_fingerprint"] = case_fingerprint
    return json.dumps(option)


def _phase_str(vbus: dict[str, dict[int, float]], bus: str, ph: int) -> str:
    v = vbus.get(bus, {}).get(ph)
    return f"{v:.4f}" if v is not None else "—"


def _sld_caption(sld: Optional[SLDModel]) -> str:
    if sld is None:
        return ""
    loss = f"{sld.total_loss_kw:.1f} kW" if sld.total_loss_kw is not None else "—"
    return f"{len(sld.nodes)} buses · {len(sld.edges)} branches · total active loss {loss}"


def _encode_image(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    try:
        return base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return None


def _comparison_sld_gallery(
    comparison: dict,
    *,
    interactive_graph: dict | None = None,
    supplied_native: str | None = None,
) -> dict:
    """Build a provenance-labelled source/native/interactive SLD gallery."""
    ref = Path(str(comparison.get("reference_run", ""))) if comparison.get("reference_run") else None
    cand = Path(str(comparison.get("candidate_run", ""))) if comparison.get("candidate_run") else None
    views: list[dict] = []
    source = _encode_image(ref / "powerfactory" / "source_sld.png") if ref else None
    views.append(
        {
            "id": "source-sld",
            "label": "Source SLD",
            "kind": "image",
            "image": source,
            "caption": "Original source capture; topology reference only.",
            "status": "available" if source else "blocked",
        }
    )
    native = supplied_native
    if native is None and ref:
        native = _encode_image(ref / "powerfactory" / "native_sld.png")
    views.append(
        {
            "id": "native-sld",
            "label": "Native PowerFactory SLD",
            "kind": "image",
            "image": native,
            "caption": "Native PowerFactory raster; editable project remains the .pfd.",
            "status": "available" if native else "blocked",
        }
    )
    candidate_native = _encode_image(cand / "powerfactory" / "native_sld.png") if cand else None
    if candidate_native and candidate_native != native:
        views.append(
            {
                "id": "candidate-native-sld",
                "label": "Candidate Native SLD",
                "kind": "image",
                "image": candidate_native,
                "caption": "Candidate engine native capture, when supplied.",
                "status": "available",
            }
        )
    views.append(
        {
            "id": "interactive-sld",
            "label": "Interactive CEPT SLD",
            "kind": "interactive",
            "graph": interactive_graph,
            "caption": "Solver-backed CEPT view; hover and roam are available.",
            "status": "available" if interactive_graph else "blocked",
        }
    )
    return {"views": views}


def _comparison_interactive_graph(comparison: dict) -> dict | None:
    """Load the candidate run's solver-backed SLD for comparison reports."""
    return _interactive_graph_from_run(comparison.get("candidate_run"))


def _interactive_graph_from_run(path: str | None) -> dict | None:
    if not path:
        return None
    result_path = Path(path) / "results.json"
    if not result_path.is_file():
        return None
    try:
        result = StudyResult.model_validate(json.loads(result_path.read_text(encoding="utf-8")))
        return (
            json.loads(_sld_json(result.sld, result.case_fingerprint))
            if result.sld and result.sld.nodes
            else None
        )
    except (OSError, ValueError, TypeError):
        return None


def _source_interactive_graph(case_path: str | None, run_path: str | None) -> dict | None:
    """Build a source SLD from a *completed solver payload* and a canonical Case.

    The source reference runner intentionally does not return a CEPT ``StudyResult``
    (it is an adapter-neutral PowerFactory evidence packet).  When the caller
    supplies the Case used to construct the candidate, we can still make a
    value-bearing interactive view without reading live PowerFactory objects:
    topology/geometry comes from the Case and every displayed value comes from
    the immutable ``results.json`` solver snapshot.  The source voltage payload
    is positive-sequence, so it is shown explicitly on phase A only rather than
    inventing three phase values.
    """
    if not case_path or not run_path:
        return None
    try:
        case = Case.model_validate_json(Path(case_path).read_text(encoding="utf-8"))
        payload = json.loads((Path(run_path) / "results.json").read_text(encoding="utf-8"))
        net = case.network.inline
        if case.network.kind != "inline" or net is None:
            return None
        # T-039: this builder only knows AC lines/transformers. A converter
        # island drawn without its DC buses/edges would be the same
        # silent-drop the fidelity gate now forbids, so decline explicitly
        # (callers render a "no source SLD" placeholder) instead of drawing
        # half a station.
        if net.converters or net.dc_buses or net.dc_sources or net.dc_loads:
            return None
        from cept.domain.sld.engineering_layout import inline_layout

        layout = inline_layout(net)
        result = payload.get("results") or {}
        voltages = {str(v.get("name", "")).lower(): v for v in result.get("bus_voltages", [])}
        loads = {str(v.get("name", "")).lower(): v for v in result.get("loads", [])}
        generators = {str(v.get("name", "")).lower(): v for v in result.get("generators", [])}
        flows = {str(v.get("name", "")).lower(): v for v in result.get("branch_flows", [])}
        nodes: list[SLDNode] = []
        for bus in net.buses:
            value = voltages.get(bus.name.lower(), {})
            v = value.get("v_pu")
            angle = value.get("angle_deg")
            node = SLDNode(
                id=bus.name,
                x=float(layout.get(bus.name.lower(), (0.0, 0.0))[0]),
                y=float(layout.get(bus.name.lower(), (0.0, 0.0))[1]),
                kind="substation"
                if bus.name.lower() in {g.bus.lower() for g in net.external_grids}
                else "bus",
                kv_base=float(bus.kv),
                phases=[1] if v is not None else [],
                v_pu={1: float(v)} if v is not None else {},
                angle_deg={1: float(angle)} if angle is not None else {},
            )
            for load in net.loads:
                if load.bus.lower() != bus.name.lower():
                    continue
                value = loads.get(load.id.lower(), {})
                node.loads.append(
                    SLDLoad(
                        name=load.id,
                        kw=float(value.get("p_mw", load.kw / 1000.0)) * 1000.0,
                        kvar=float(value.get("q_mvar", (load.q_kvar or 0.0) / 1000.0)) * 1000.0,
                        phases=[1, 2, 3],
                        shed=bool(value.get("status", 0)),
                    )
                )
            for gen in net.generators:
                if gen.bus.lower() != bus.name.lower():
                    continue
                value = generators.get(gen.name.lower(), {})
                node.gens.append(
                    SLDGen(
                        name=gen.name,
                        kind="grid" if gen.bus_type == "slack" else "syncgen",
                        kw=float(value.get("p_mw", gen.kw / 1000.0)) * 1000.0,
                        kvar=float(value.get("q_mvar", 0.0)) * 1000.0,
                        phases=[1, 2, 3],
                        tripped=bool(value.get("status", 0)),
                    )
                )
            for grid in net.external_grids:
                if grid.bus.lower() == bus.name.lower() and not any(g.name == grid.name for g in node.gens):
                    node.gens.append(SLDGen(name=grid.name, kind="grid", phases=[1, 2, 3]))
            nodes.append(node)

        edges: list[SLDEdge] = []
        for line in net.lines:
            value = flows.get(line.name.lower(), {})
            p_from = float(value.get("p_from_mw", 0.0))
            q_from = float(value.get("q_from_mvar", 0.0))
            p_to = float(value.get("p_to_mw", 0.0))
            edges.append(
                SLDEdge(
                    id=line.name,
                    src=line.from_bus,
                    dst=line.to_bus,
                    kind="line",
                    phases=[1, 2, 3],
                    length=line.display_length_km,
                    length_unit="km",
                    p_kw=p_from * 1000.0,
                    q_kvar=q_from * 1000.0,
                    losses_kw=(p_from + p_to) * 1000.0,
                )
            )
        for tr in net.transformers:
            value = flows.get(tr.name.lower(), {})
            p_from = float(value.get("p_from_mw", 0.0))
            q_from = float(value.get("q_from_mvar", 0.0))
            p_to = float(value.get("p_to_mw", 0.0))
            edges.append(
                SLDEdge(
                    id=tr.name,
                    src=tr.hv_bus,
                    dst=tr.lv_bus,
                    kind="transformer",
                    phases=[1, 2, 3],
                    p_kw=p_from * 1000.0,
                    q_kvar=q_from * 1000.0,
                    losses_kw=(p_from + p_to) * 1000.0,
                )
            )
        sld = SLDModel(
            title="Source PowerFactory solver snapshot (positive sequence)",
            nodes=nodes,
            edges=edges,
            total_loss_kw=sum(edge.losses_kw for edge in edges),
            v_min_pu=case.standards.v_min_pu,
            v_max_pu=case.standards.v_max_pu,
        )
        return json.loads(_sld_json(sld, payload.get("case_fingerprint")))
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _three_way_sld_gallery(comparison: dict) -> dict:
    views: list[dict] = []
    roles = comparison.get("roles", {})
    for role, label in (
        ("source", "a. Source example.pfd"),
        ("cept-pfd", "b. CEPT-made PowerFactory .pfd"),
        ("opendss", "c. CEPT-made OpenDSS"),
    ):
        spec = roles.get(role, {})
        root = Path(spec["path"]) if spec.get("path") else None
        native = None
        for candidate in (
            root / "powerfactory" / ("source_sld.png" if role == "source" else "native_sld.png")
            if root
            else None,
            root / "native_sld.png" if root else None,
        ):
            native = _encode_image(candidate)
            if native:
                break
        artifact = None
        if root:
            candidates = (
                [root / "native-reference.pfd"]
                if role == "source"
                else list((root / "powerfactory").glob("*.pfd"))
                if role == "cept-pfd" and (root / "powerfactory").is_dir()
                else []
            )
            artifact = next((item for item in candidates if item.is_file()), None)
        graph = _interactive_graph_from_run(spec.get("path"))
        source_from_snapshot = False
        if role == "source" and graph is None:
            graph = _source_interactive_graph(comparison.get("case_path"), spec.get("path"))
            source_from_snapshot = graph is not None
        views.append(
            {
                "id": f"{role}-native",
                "label": f"Native SLD — {label}",
                "kind": "image",
                "image": native,
                "image_href": next(
                    (
                        candidate.resolve().as_uri()
                        for candidate in (
                            root
                            / "powerfactory"
                            / ("source_sld.png" if role == "source" else "native_sld.png")
                            if root
                            else None,
                            root / "native_sld.png" if root else None,
                        )
                        if candidate and candidate.is_file()
                    ),
                    None,
                ),
                "caption": (
                    "Native PowerFactory capture; source/editable project evidence only."
                    if role != "opendss"
                    else "OpenDSS has no native editable SLD raster."
                ),
                "artifact": str(artifact) if artifact else None,
                "artifact_href": artifact.resolve().as_uri() if artifact else None,
                "status": "available"
                if native
                else "artifact-only"
                if artifact
                else "not-applicable"
                if role == "opendss"
                else "blocked",
            }
        )
        views.append(
            {
                "id": f"{role}-interactive",
                "label": f"Interactive SLD — {label}",
                "kind": "interactive",
                "graph": graph,
                "caption": (
                    "Source solver snapshot + canonical Case geometry; positive-sequence values are shown on phase A only."
                    if source_from_snapshot
                    else "Solver-backed CEPT graph; hover and zoom are available."
                    if graph
                    else "No value-bearing CEPT SLD snapshot was supplied."
                ),
                "status": "available" if graph else "blocked",
            }
        )
    return {"views": views}


def _sld_snapshot_views(study: StudyResult) -> list[dict]:
    """Prepare independent, value-bearing SLD tabs for the report."""
    views = []
    for snapshot in study.sld_snapshots:
        views.append(
            {
                "id": snapshot.id,
                "label": snapshot.label,
                "t_s": snapshot.t_s,
                "phase": snapshot.phase,
                "status": snapshot.status,
                "graph_json": _sld_json(snapshot.sld, study.case_fingerprint)
                if snapshot.status == "available"
                else "null",
                "caption": _sld_caption(snapshot.sld),
            }
        )
    return views


def _case_context(case: Case) -> dict:
    machine_base_generators: list[str] = []
    if case.network.kind == "inline" and case.network.inline is not None:
        machine_base_generators = [
            f"{generator.name}: {generator.mva:g} MVA source machine base (not a nameplate rating)"
            for generator in case.network.inline.generators
            if generator.mva_basis == "machine_base"
        ]
    return {
        "case_mode": case.meta.mode,
        "assumptions": [item.model_dump(mode="json") for item in case.assumptions],
        "machine_base_generators": machine_base_generators,
    }


# WP11 phase 3.6: render.py is now the shared shell only; the 16
# render_* entry points moved to ``reports.py`` (one file, not sixteen).
# PEP 562 keeps every existing ``from cept.reporting.render import
# render_load_flow_report``-style import working with no import cycle in
# any order: the shell never imports reports at module level, and
# reports.py imports this module fully initialized.
def __getattr__(name: str):
    import cept.reporting.reports as _reports

    value = getattr(_reports, name)
    globals()[name] = value
    return value
