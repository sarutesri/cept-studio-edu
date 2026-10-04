"""Artifact writers for a `cept run`: reports, SLD layout, manifest,
validation reports and receipts.

Extracted from `cept.cli.commands._engine` (mechanical move, no behavior
change). Everything here writes deterministic, hash-bound evidence files
beside `results.json` so every run folder is self-auditable.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

from cept import __version__
from cept.application.run_identity import artifact_set_digest, assessment_id
from cept.engine_passports import capability_for_case
from cept.domain.sld.geometry import route_axis_backtrack_count
from cept.domain.sld.layout_contract import canonical_sld_edge_id
from cept.reporting import (
    render_dynamics_report,
    render_emt_report,
    render_fault_report,
    render_gic_report,
    render_hosting_capacity_report,
    render_ibr_report,
    render_load_flow_report,
    render_time_series_report,
    render_harmonics_report,
    render_protection_report,
)
from cept.reporting.sld_plan import build_interactive_sld_plan
from cept.reporting.geometry import topology_signature, validate_geometry
from cept.schema import Case
from cept.schema.result import StudyResult
from cept.util import read_json, sha256_file, write_json as _write_json


def _is_ibr_case(case: Case) -> bool:
    for der in case.ders:
        if der.type == "pv" and der.inverter and der.inverter.control != "constant_pf":
            return True
    return False


def _render_report(result: StudyResult, case: Case, run_dir: Path) -> Path:
    """Write report.html for whichever study this result carries."""
    return _render_report_html(result, case, run_dir)[0]


def _render_report_html(result: StudyResult, case: Case, run_dir: Path) -> tuple[Path, str]:
    """Same as :func:`_render_report`, also returning the rendered HTML.

    Every renderer already returns the HTML it writes. Handing that string
    back lets `_run_case` seed `_ReportEdits` with it, so the injectors never
    read the ~7.5 MB file back off disk. The file is still written here rather
    than only at flush, so a report exists even if a later step (DSS export,
    PowerFactory package export) raises.
    """
    report_path = run_dir / "report.html"
    if result.fault is not None:
        html = render_fault_report(result, case, out_path=report_path)
    elif result.hosting_capacity is not None:
        html = render_hosting_capacity_report(result, case, out_path=report_path)
    elif result.dynamics is not None:
        html = render_dynamics_report(result, case, out_path=report_path)
    elif result.emt is not None:
        html = render_emt_report(result, case, out_path=report_path)
    elif result.time_series is not None:
        html = render_time_series_report(result, case, out_path=report_path)
    elif result.harmonics is not None:
        html = render_harmonics_report(result, case, out_path=report_path)
    elif result.protection is not None:
        html = render_protection_report(result, case, out_path=report_path)
    elif result.gic is not None:
        html = render_gic_report(result, case, out_path=report_path)
    elif _is_ibr_case(case):
        html = render_ibr_report(result, case, out_path=report_path)
    else:
        html = render_load_flow_report(result, case, out_path=report_path)
    return report_path, html


def _inject_opendss_sld_status(
    report_path: Path, master: Path, run_dir: Path, *, edits: "_ReportEdits | None" = None
) -> None:
    rel = master.resolve().relative_to(run_dir.resolve()).as_posix()
    target = edits or _ReportEdits(report_path)
    html = target.html()
    section = (
        '<section class="native-sld-reference">'
        "<h2>OpenDSS plot commands (optional)</h2>"
        '<p class="note">The portable master script contains OpenDSS '
        f"<code>Plot Circuit</code> commands. OpenDSS-G can render them from "
        f"<code>{rel}</code>; this headless run did not capture a native image. "
        "No OpenDSS-G native image is required or claimed; the interactive SLD "
        "is engine-neutral.</p>"
        "</section>"
    )
    target.set(_insert_before_closing_body(html, section))
    if edits is None:
        target.flush()


def _inject_powerfactory_sld(
    report_path: Path,
    image: Path | None,
    run_dir: Path,
    *,
    error: str | None = None,
    edits: "_ReportEdits | None" = None,
) -> None:
    """Embed source/native views while keeping the editable PFD separate.

    The report is deliberately self-contained, but the captions still expose
    the hashes used to audit each view. A missing source capture stays
    explicitly blocked; the native raster is never silently relabelled as the
    source reference.
    """
    encoded = base64.b64encode(image.read_bytes()).decode("ascii") if image and image.exists() else None
    source = run_dir / "powerfactory" / "source_sld.png"

    def _hash(path: Path | None) -> str:
        try:
            return sha256_file(path)[:12] if path and path.exists() else "missing"
        except OSError:
            return "unreadable"

    case_hash = _hash(run_dir / "case.json")
    native_hash = _hash(image)
    source_hash = _hash(source)
    result_meta = {}
    try:
        result_meta = read_json(run_dir / "results.json")
    except (OSError, ValueError):
        pass
    engine = escape(str(result_meta.get("engine", "powerfactory")))
    engine_version = escape(str(result_meta.get("engine_version", "unknown")))
    geometry_verdict = "not-run"
    try:
        layout = read_json(run_dir / "sld-layout.json")
        geometry_verdict = str(layout.get("geometry", {}).get("verdict", "not-run"))
    except (OSError, ValueError):
        pass
    meta = (
        '<div class="sld-evidence-meta">'
        f"<span>Case hash <code>{escape(case_hash)}</code></span>"
        f"<span>Engine <code>{engine} {engine_version}</code></span>"
        f"<span>Geometry <strong>{escape(geometry_verdict)}</strong></span>"
        "</div>"
    )

    def _image_tools(dom_id: str) -> str:
        return (
            f'<div class="sld-image-tools" data-pf-image-tools="{dom_id}">'
            '<button type="button" class="sld-btn" data-pf-image-action="fit">Fit</button>'
            '<button type="button" class="sld-btn" data-pf-image-action="zoom-in">Zoom +</button>'
            '<button type="button" class="sld-btn" data-pf-image-action="zoom-out">Zoom −</button>'
            '<button type="button" class="sld-btn" data-pf-image-action="reset">Reset</button>'
            "</div>"
        )

    source_block = (
        '<div class="sld-view source-sld-view"><h3>Source SLD</h3>'
        '<p class="note"><span class="badge bad">BLOCKED</span> '
        "No source SLD capture was supplied for this run; native output is "
        "not treated as the source image.</p>"
        f'<p class="note">Source hash: <code>{escape(source_hash)}</code></p></div>'
    )
    if source.exists():
        source_encoded = base64.b64encode(source.read_bytes()).decode("ascii")
        source_block = (
            '<div class="sld-view source-sld-view"><h3>Source SLD</h3>'
            '<p class="note">Baseline source capture (input-only); geometry is '
            "not re-authored by CEPT.</p>"
            f'<p class="note">Source hash: <code>{escape(source_hash)}</code></p>'
            f"{_image_tools('source')}"
            f'<img data-pf-image="source" alt="Source single-line diagram" '
            f'src="data:image/png;base64,{source_encoded}"></div>'
        )
    native_block = (
        '<div class="sld-view native-sld-view"><h3>Native PowerFactory SLD</h3>'
        '<p class="note"><span class="badge bad">BLOCKED</span> '
        + escape(error or "native PNG was not produced")
        + f'</p><p class="note">Native hash: <code>{escape(native_hash)}</code></p></div>'
        if encoded is None
        else '<div class="sld-view native-sld-view"><h3>Native PowerFactory SLD</h3>'
        '<p class="note">This is a raster capture of the native PowerFactory '
        "diagram. Edit the graphical SLD in the exported <code>.pfd</code> "
        "package; this image is report evidence only.</p>"
        f'<p class="note">Native hash: <code>{escape(native_hash)}</code></p>'
        f"{_image_tools('native')}"
        f'<img data-pf-image="native" alt="PowerFactory native single-line diagram" '
        f'src="data:image/png;base64,{encoded}"></div>'
    )
    # Keep the two raster views together for geometry comparison.  The
    # editable engine-neutral SLD is injected in the following full-width row
    # by the report template, so it remains large enough to use as an editor.
    section = f'{meta}<div class="pf-sld-reference-row">{source_block}{native_block}</div>'
    target = edits or _ReportEdits(report_path)
    html = target.html()
    marker = '<div id="powerfactory-sld-source-native"></div>'
    had_marker = marker in html
    html = html.replace(marker, section, 1)
    fallback = (
        '<section class="native-sld-reference sld-comparison">'
        "<h2>SLD comparison</h2>" + section + "</section>"
    )
    html = html if had_marker else _insert_before_closing_body(html, fallback)
    # Keep image controls local to this report; no external assets or server
    # are needed for a reviewer opening report.html from a copied run folder.
    controls = """
<script>(function(){
  document.querySelectorAll('[data-pf-image-tools]').forEach(function(tools){
    var id=tools.getAttribute('data-pf-image-tools'), img=document.querySelector('[data-pf-image="'+id+'"]');
    if(!img) return; var scale=1;
    function apply(){ img.style.transform='scale('+scale+')'; img.style.transformOrigin='center center'; }
    tools.querySelectorAll('[data-pf-image-action]').forEach(function(btn){ btn.addEventListener('click',function(){
      var a=btn.getAttribute('data-pf-image-action');
      if(a==='zoom-in') scale=Math.min(4,scale*1.25);
      else if(a==='zoom-out') scale=Math.max(.25,scale/1.25);
      else scale=1;
      apply();
    }); });
  });
})();</script>"""
    target.set(_insert_before_closing_body(html, controls))
    if edits is None:
        target.flush()


def _inject_report_context(report_path: Path, run_dir: Path, *, edits: "_ReportEdits | None" = None) -> None:
    attempt = read_json(run_dir / "attempt.json") if (run_dir / "attempt.json").is_file() else {}
    token = secrets.token_urlsafe(32)
    (run_dir / ".cept-launch-token").write_text(token + "\n", encoding="utf-8")
    context = {
        "run_dir": str(run_dir),
        "launcher_token": token,
        "attempt_id": attempt.get("attempt_id"),
        "execution_key": attempt.get("execution_key"),
        "case_fingerprint": attempt.get("case_fingerprint"),
    }
    payload = json.dumps(context, ensure_ascii=False)
    section = f"<script>window.__ceptReportContext={payload};</script>"
    _write_json(
        run_dir / ".cept-launch-binding.json",
        {
            "schema": "cept-launch-binding-v1",
            "token": token,
            "attempt_id": attempt.get("attempt_id"),
            "execution_key": attempt.get("execution_key"),
            "case_fingerprint": attempt.get("case_fingerprint"),
        },
    )
    target = edits or _ReportEdits(report_path)
    target.set(_insert_before_closing_body(target.html(), section))
    if edits is None:
        target.flush()


def _insert_before_closing_body(html: str, section: str) -> str:
    head, marker, tail = html.rpartition("</body>")
    return head + section + marker + tail if marker else html + section


class _ReportEdits:
    """Hold report.html in memory so a run rewrites it once, not per injector.

    A report is ~7.5 MB (most of it the inlined vendor JS), and each injector
    used to read and rewrite the whole file for the sake of one `<section>`:
    render + context + SLD status meant ~37 MB of I/O per run to add a few
    hundred bytes.

    Passing one instance through the injectors keeps their order -- and so the
    resulting bytes -- exactly as before. Each `_inject_*` still works
    standalone with no editor, which is how `scripts/build_blind_validation_
    report.py` and the SLD tests call them.
    """

    __slots__ = ("path", "_html")

    def __init__(self, path: Path) -> None:
        self.path = path
        self._html: str | None = None

    def html(self) -> str:
        if self._html is None:
            self._html = self.path.read_text(encoding="utf-8")
        return self._html

    def set(self, html: str) -> None:
        self._html = html

    def flush(self) -> None:
        if self._html is not None:
            self.path.write_text(self._html, encoding="utf-8")
            self._html = None


def _write_identity_map(run_dir: Path, case: Case, adapter: Any) -> None:
    """Publish which engine object each Case asset became."""
    from cept.semantics.identity import publish_identity_map

    publish_identity_map(run_dir, case, adapter)


def _sld_collision_record(result: StudyResult) -> dict[str, Any]:
    """Compute the renderer-consistent SLD collision verdict for a result."""
    if result.sld is None or not result.sld.nodes:
        return {
            "verdict": "pass",
            "overlap_count": 0,
            "major_count": 0,
            "minor_count": 0,
            "overlaps": [],
            "n_nodes": 0,
            "n_edges": 0,
        }
    from cept.reporting.collisions import rendered_collision_check

    return rendered_collision_check(result.sld)


def _write_sld_collision(run_dir: Path, result: StudyResult) -> None:
    """Persist the SLD collision verdict beside results.json.

    The rendered single-line diagram must not let symbols or labels cover
    each other.  The verdict is measured on the *renderer's* final layout
    (including any real-world-coordinate rebase), so this artifact matches
    what a viewer sees in report.html.
    """
    record = _sld_collision_record(result)
    _write_json(run_dir / "sld_collision.json", record)


def _write_sld_fidelity(run_dir: Path, case: Case, result: StudyResult) -> None:
    """Persist the SLD structural-fidelity gate beside results.json.

    Proves the diagram faithfully depicts the solved Case (buses, devices,
    grid connection, branch endpoints, collision, fingerprint) —
    deterministic and engine-agnostic, so correctness is re-derivable rather
    than a drawn claim.  See `src/cept/validation/sld_fidelity.py`.
    """
    from cept.validation.sld_fidelity import sld_fidelity

    _write_json(run_dir / "sld-fidelity.json", sld_fidelity(case, result))


def _sld_fidelity_record(case: Case, result: StudyResult) -> dict[str, Any]:
    from cept.validation.sld_fidelity import sld_fidelity

    return sld_fidelity(case, result)


def _inject_sld_fidelity(
    report_path: Path,
    case: Case,
    result: StudyResult,
    *,
    edits: "_ReportEdits | None" = None,
) -> None:
    """Embed the SLD fidelity verdict into the report as a visible section."""
    target = edits or _ReportEdits(report_path)
    html = target.html()
    fidelity = _sld_fidelity_record(case, result)
    if fidelity["passed"]:
        badge = '<span class="badge ok">Pass</span>'
        detail = (
            "The single-line diagram matches the solved Case (buses, devices, "
            "grid connection, branch endpoints)."
        )
    else:
        badge = '<span class="badge bad">Fail</span>'
        detail = (
            "The single-line diagram does not fully match the solved Case: "
            + escape("; ".join(fidelity.get("reasons", [])) or "see sld-fidelity.json")
            + "."
        )
    section = (
        '<section class="sld-fidelity">'
        "<h2>SLD fidelity check</h2>"
        f"<p>{badge} {detail} "
        "<code>sld-fidelity.json</code> holds the machine-readable record.</p>"
        "</section>"
    )
    target.set(_insert_before_closing_body(html, section))
    if edits is None:
        target.flush()


def _verified_report_payload(
    case: Case,
    result: StudyResult,
    report_path: Path | None = None,
) -> dict[str, Any]:
    """The machine-readable slice of the report that `cept verify` re-checks.

    The report is only *correct* if it embeds the same fingerprint and SLD
    verdicts that the artifacts already record.  New run artifacts also carry
    their attempt and execution identity here, so a report cannot silently
    move between attempts.
    """
    fidelity = _sld_fidelity_record(case, result)
    collision = _sld_collision_record(result)
    payload = {
        "schema": "cept-verified-report-v1",
        "case_fingerprint": result.case_fingerprint,
        "sld_fidelity": {
            "verdict": fidelity.get("verdict"),
            "passed": fidelity.get("passed"),
        },
        "sld_collision": {"verdict": collision.get("verdict")},
    }
    if report_path is not None:
        attempt_path = report_path.parent / "attempt.json"
        if attempt_path.is_file():
            attempt = read_json(attempt_path)
            payload.update(
                {
                    "attempt_id": attempt.get("attempt_id"),
                    "execution_key": attempt.get("execution_key"),
                }
            )
    return payload


def _inject_verified_report_payload(
    report_path: Path,
    case: Case,
    result: StudyResult,
    *,
    edits: "_ReportEdits | None" = None,
) -> None:
    """Embed the verified-report payload so `cept verify` can bind the report
    back to the artifacts that produced it (Pillar C, report-correctness gate)."""
    payload = json.dumps(_verified_report_payload(case, result, report_path), ensure_ascii=False)
    marker = f"<script>window.__ceptVerifiedReport={payload};</script>"
    target = edits or _ReportEdits(report_path)
    target.set(_insert_before_closing_body(target.html(), marker))
    if edits is None:
        target.flush()


def _inject_sld_collision(
    report_path: Path,
    result: StudyResult,
    *,
    edits: "_ReportEdits | None" = None,
) -> None:
    """Embed the SLD collision verdict into the report as a visible section."""
    target = edits or _ReportEdits(report_path)
    html = target.html()
    collision = _sld_collision_record(result)
    if collision["verdict"] == "pass":
        badge = '<span class="badge ok">Clear</span>'
        detail = "No symbol or label overlaps in the rendered single-line diagram."
    elif collision["verdict"] == "warning":
        badge = '<span class="badge warn">Crowded</span>'
        detail = (
            f"{collision['minor_count']} minor overlap(s) - labels/symbols crowd but do not "
            "fully cover each other."
        )
    else:
        badge = '<span class="badge bad">Blocked</span>'
        detail = (
            f"{collision['major_count']} major overlap(s) out of {collision['overlap_count']} total - "
            "symbols/labels cover each other in the rendered diagram."
        )
    section = (
        '<section class="sld-collision">'
        "<h2>SLD collision check</h2>"
        f"<p>{badge} {detail} "
        f"<code>sld_collision.json</code> holds the machine-readable record.</p>"
        "</section>"
    )
    target.set(_insert_before_closing_body(html, section))
    if edits is None:
        target.flush()


def _write_sld_layout(run_dir: Path, case: Case, result: StudyResult) -> None:
    """Persist the engine-neutral layout as a hash-bound editable artifact."""
    sld = result.sld
    topology = [{"id": e.id, "src": e.src, "dst": e.dst, "kind": e.kind} for e in (sld.edges if sld else [])]
    topology_hash = hashlib.sha256(
        json.dumps(topology, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    nodes: dict[str, dict[str, Any]] = {}
    geometry: dict[str, Any]
    if sld:
        try:
            plan = build_interactive_sld_plan(sld)
            buses = {bus.bus_id.lower(): bus for bus in plan.buses}
            for node in sld.nodes:
                payload: dict[str, Any] = {
                    "x": float(node.x),
                    "y": float(node.y),
                    "rotation": 0,
                    "bus_orientation": None,
                }
                bus = buses.get(str(node.id).lower())
                if bus is not None:
                    payload.update(
                        {
                            "x": bus.center.x,
                            "y": bus.center.y,
                            "bus_orientation": "horizontal" if bus.orientation == "h" else "vertical",
                            "canonical_half_length": bus.half_length,
                            "canonical_geometry_v2": True,
                        }
                    )
                nodes[node.id] = payload

            canonical_routes = {
                edge_id: [list(point) for point in points]
                for edge_id, points in plan.geometry.route_points().items()
            }
            legacy_routes = validate_geometry(sld).get("route_points", {})
            routes: dict[str, Any] = {}
            for edge in sld.edges:
                canonical_id = canonical_sld_edge_id(edge.id)
                if canonical_id in canonical_routes:
                    routes[canonical_id] = canonical_routes[canonical_id]
                elif edge.id in legacy_routes:
                    # GridLink compatibility leaves are terminal stems rather
                    # than physical canonical branches; retain their exact
                    # transport route in the artifact.
                    routes[edge.id] = legacy_routes[edge.id]

            replay_nodes = [
                node.model_copy(
                    update={
                        "x": nodes[node.id]["x"],
                        "y": nodes[node.id]["y"],
                    }
                )
                for node in sld.nodes
            ]
            replay_edges = [
                edge.model_copy(
                    update={
                        "route_points": routes.get(canonical_sld_edge_id(edge.id), routes.get(edge.id, [])),
                    }
                )
                for edge in sld.edges
            ]
            replay = sld.model_copy(update={"nodes": replay_nodes, "edges": replay_edges})
            geometry = validate_geometry(replay)
            geometry["axis_backtrack_count"] = sum(
                route_axis_backtrack_count(route.points) for route in plan.geometry.routes
            )
            geometry["canonical_geometry_v2"] = True
            geometry["route_safety_policy"] = "zero-for-radial-forest"
            geometry["route_points"] = routes
        except (KeyError, ValueError) as exc:
            nodes = {
                node.id: {"x": node.x, "y": node.y, "rotation": 0, "bus_orientation": None}
                for node in sld.nodes
            }
            geometry = {
                "verdict": "blocked",
                "diagonal_segment_count": 0,
                "overlap_count": 0,
                "crossing_count": 0,
                "node_overlap_count": 0,
                "route_points": {},
                "canonical_geometry_v2": False,
                "reason": f"canonical SLD layout unavailable: {exc}",
            }
    else:
        geometry = {
            "verdict": "blocked",
            "diagonal_segment_count": 0,
            "overlap_count": 0,
            "crossing_count": 0,
            "route_points": {},
        }
    layout_hash = hashlib.sha256(
        json.dumps(
            {"nodes": nodes, "routes": geometry.get("route_points", {})},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    _write_json(
        run_dir / "sld-layout.json",
        {
            "case_fingerprint": case.fingerprint(),
            "topology_hash": topology_hash,
            "layout_hash": layout_hash,
            "layout_policy_version": "orthogonal-v2",
            "route_points": geometry.get("route_points", {}),
            "coordinate_system": "cept-sld-v1",
            "source_transform": "y_reflection" if case.network.kind == "inline" else None,
            "editor_version": "1",
            "geometry": {
                **geometry,
                "topology_signature": topology_signature(sld) if sld else None,
                "orientation_policy": "deterministic-orthogonal",
            },
            "nodes": nodes,
            "edges": topology,
        },
    )


def _pro_error_type(name: str) -> type[BaseException] | None:
    """Resolve a Pro-only exception type, or ``None`` when Pro is not installed.

    Describing a failure must never *require* a licensed symbol. On a public
    install ``cept.adapters`` raises ``ImportError`` for any Pro name, and
    letting that propagate replaced the real reason for a failed run with
    "… is a CEPT Pro capability and is not installed in this CEPT Public
    package" — the learner was told about the wrong problem. Without the type
    the extra PowerFactory fields are simply omitted, which loses detail rather
    than replacing the cause.
    """

    from cept import adapters

    try:
        resolved = getattr(adapters, name)
    except ImportError:
        return None
    return resolved if isinstance(resolved, type) and issubclass(resolved, BaseException) else None


def _write_failure(run_dir: Path, exc: Exception) -> None:
    pf_error = _pro_error_type("PowerFactoryRunError")

    payload: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "error_type": type(exc).__name__,
        "error": str(exc),
    }
    if pf_error is not None and isinstance(exc, pf_error):
        payload.update(
            {
                "stage": exc.stage,
                "project_name": exc.project_name,
                "preserved": exc.preserved,
                "cleanup": (
                    "preserved-for-inspection"
                    if exc.preserved and exc.cleanup_status == "not-required"
                    else {
                        "status": exc.cleanup_status,
                        "error": exc.cleanup_error,
                    }
                ),
            }
        )
    _write_json(run_dir / "failure.json", payload)


def _write_manifest(
    run_dir: Path,
    case: Case,
    result: StudyResult,
    report_path: Path,
    export_path: str | Path | None,
    *,
    status: str,
    validation: dict[str, Any],
    solver_package: str | Path | None = None,
    solver_package_error: str | None = None,
    native_sld: str | Path | None = None,
    native_sld_error: str | None = None,
    capability: dict[str, Any] | None = None,
    adapter_cleanup: dict[str, Any] | None = None,
    experiment_context: dict[str, Any] | None = None,
    attempt_id: str | None = None,
    execution_key: str | None = None,
    model_revision: dict[str, Any] | None = None,
    execution_plan: dict[str, Any] | None = None,
) -> None:
    capability = dict(capability or capability_for_case(case))

    def portable_path(path: str | Path | None) -> str | None:
        if path is None:
            return None
        resolved = Path(path).resolve()
        try:
            return resolved.relative_to(run_dir.resolve()).as_posix()
        except ValueError as exc:
            raise ValueError(f"run artifact must stay inside the run directory: {path}") from exc

    manifest = {
        "case": case.meta.name,
        "case_fingerprint": case.fingerprint(),
        "attempt_id": attempt_id,
        "execution_key": execution_key,
        "canonical_case_fingerprint": (experiment_context or {}).get("canonical_case_fingerprint"),
        "canonical_case_path": (experiment_context or {}).get("canonical_case_path"),
        "run_dir": ".",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "package": "cept-power-studio",
        "package_version": __version__,
        "status": status,
        "backend": result.engine,
        "engine_version": result.engine_version,
        "calculation_method": result.calculation_method,
        "study_type": result.study_type,
        "fidelity": capability["fidelity"],
        "claim": (experiment_context or {}).get("claim", "demonstrator"),
        "claim_cap": capability["claim_cap"],
        "engine_passport_version": capability["passport_schema_version"],
        "mode": case.meta.mode,
        "assumptions": [item.model_dump(mode="json") for item in case.assumptions],
        "provenance": case.provenance.model_dump(mode="json") if case.provenance else None,
        "report": portable_path(report_path),
        "sld_layout": "sld-layout.json",
        "dss_export": portable_path(export_path),
        "solver_package": portable_path(solver_package),
        "adapter_cleanup": dict(adapter_cleanup or {"status": "not-required", "error": None}),
        "solver_package_error": solver_package_error,
        "native_sld": portable_path(native_sld),
        "native_sld_error": native_sld_error,
        "source_sld": (
            "powerfactory/source_sld.png"
            if (run_dir / "powerfactory" / "source_sld.png").exists()
            else None
        ),
        "validation": validation,
        "experiment": experiment_context,
        "model_revision": model_revision,
        "execution_plan": execution_plan,
        "model_package_hashes": (experiment_context or {}).get("model_package_hashes", {}),
        "model_package_artifacts": (experiment_context or {}).get("model_package_artifacts", []),
        "dynamic_model_mapping": result.extra.get("dynamic_model_mapping"),
    }
    _write_json(run_dir / "manifest.json", manifest)


def _write_validation(
    run_dir: Path,
    case: Case,
    result: StudyResult,
    *,
    summary: dict[str, Any] | None = None,
    claim: str = "demonstrator",
    capability: dict[str, Any] | None = None,
    attempt_id: str | None = None,
    execution_key: str | None = None,
    model_revision: dict[str, Any] | None = None,
) -> str | None:
    summary = summary or _validation_summary(case, result)
    resolved_capability = dict(capability or capability_for_case(case))
    payload = {
        **summary,
        "model_revision": model_revision,
        "case_fingerprint": case.fingerprint(),
        "claim": claim,
        "claim_cap": resolved_capability["claim_cap"],
        "fidelity": resolved_capability["fidelity"],
    }
    assessment = None
    if attempt_id and execution_key:
        assessment = assessment_id(
            attempt_id,
            execution_key,
            sha256_file(run_dir / "results.json"),
            payload,
        )
        payload.update(
            {
                "attempt_id": attempt_id,
                "execution_key": execution_key,
                "assessment_id": assessment,
            }
        )
    _write_json(run_dir / "validation_report.json", payload)
    lines = [
        f"# Validation Report: {case.meta.name}",
        "",
        f"- Solver decision: {'PASS' if summary['solver_valid'] else 'BLOCKED'}",
        f"- Engineering compliance: {'PASS' if summary['engineering_compliance'] else 'FINDINGS'}",
        f"- Study type: {result.study_type}",
        f"- Backend: {result.engine}",
        f"- Engine version: {result.engine_version or 'unknown'}",
        f"- Case fingerprint: {case.fingerprint()}",
        f"- Fidelity: {resolved_capability['fidelity']}",
        f"- Claim: {claim} (cap: {resolved_capability['claim_cap']})",
        "",
        "## Checks",
        "",
    ]
    for check in summary["checks"]:
        status = (
            "PASS" if check["passed"] else ("FINDING" if check.get("category") == "compliance" else "BLOCKED")
        )
        lines.append(f"- {status}: {check['label']} - {check['value']}")
    lines.extend(["", "## Limitations", ""])
    lines.append(
        "- This validation is an acceptance gate for the generated run, not a full engineering review."
    )
    lines.append("- Do not treat missing project-specific topology or controller data as validated.")
    (run_dir / "validation_report.md").write_text("\n".join(lines), encoding="utf-8")
    return assessment


def _write_validation_record(
    run_dir: Path,
    case: Case,
    result: StudyResult,
    *,
    validation: dict[str, Any],
    claim: str,
    model_package_hashes: dict[str, str] | None = None,
    capability: dict[str, Any] | None = None,
    attempt_id: str | None = None,
    execution_key: str | None = None,
    assessment: str | None = None,
    model_revision: dict[str, Any] | None = None,
    execution_plan: dict[str, Any] | None = None,
) -> None:
    """Write the run-level, hash-bound evidence receipt.

    Experiment-level aggregation is still handled by
    ``research.write_validation_record``.  This smaller receipt makes every
    standalone run portable and auditable without inventing a second
    provenance system.
    """
    case_path = run_dir / "case.json"
    result_path = run_dir / "results.json"
    report_path = run_dir / "report.html"
    manifest_path = run_dir / "manifest.json"
    resolved_capability = dict(capability or capability_for_case(case))
    source_hashes = {}
    if case.provenance:
        source_hashes = {source.uri: source.sha256 for source in getattr(case.provenance, "sources", [])}
        if not source_hashes:
            source_hashes = {"source_manifest": case.provenance.source_manifest_sha256}
    artifact_hashes = {
        "case.json": sha256_file(case_path),
        "results.json": sha256_file(result_path),
        "report.html": sha256_file(report_path),
        "sld-layout.json": sha256_file(run_dir / "sld-layout.json"),
    }
    revision_path = run_dir / "model-revision.json"
    if model_revision is not None and revision_path.is_file():
        artifact_hashes["model-revision.json"] = sha256_file(revision_path)
    plan_path = run_dir / "execution-plan.json"
    if execution_plan is not None and plan_path.is_file():
        artifact_hashes["execution-plan.json"] = sha256_file(plan_path)
    record = {
        "schema_version": 1,
        "scope": "engine_path",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "case_fingerprint": case.fingerprint(),
        "attempt_id": attempt_id,
        "execution_key": execution_key,
        "assessment_id": assessment,
        "model_revision": model_revision,
        "execution_plan": execution_plan,
        "case_hash": artifact_hashes["case.json"],
        "model_package_hashes": model_package_hashes or {},
        "source_hashes": source_hashes,
        "engine": result.engine,
        "engine_version": result.engine_version,
        "adapter_source_version": __version__,
        "solver_options": case.study.options,
        "scenario_inputs": case.study.model_dump(mode="json"),
        "result_hash": artifact_hashes["results.json"],
        "artifact_hashes": artifact_hashes,
        "artifact_set_digest": artifact_set_digest(artifact_hashes),
        "validation_scope": "engine_path",
        "verdict": "pass" if validation.get("passed") else "warning",
        "claim": claim,
        "claim_cap": resolved_capability["claim_cap"],
        "reviewer": None,
        "limitations": [
            "A standalone run receipt does not provide project-system validation.",
            "Cross-engine and measurement evidence require an experiment-level record.",
        ],
    }
    # The manifest is deliberately not included in its own artifact hash set:
    # the manifest records this receipt, so hashing both would be circular.
    if manifest_path.exists():
        record["manifest_hash"] = sha256_file(manifest_path)
    (run_dir / "validation-record.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _validation_summary(case: Case, result: StudyResult) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    if result.load_flow is not None:
        lf = result.load_flow
        checks.append({"label": "power flow converged", "value": lf.converged, "passed": lf.converged})
        violations = [
            f"{v.bus}.{v.phase}={v.v_pu:.4f}"
            for v in lf.bus_voltages
            if v.v_pu < case.standards.v_min_pu or v.v_pu > case.standards.v_max_pu
        ]
        checks.append(
            {
                "label": "voltage within configured limits",
                "value": f"{len(violations)} violation(s)",
                "passed": len(violations) == 0,
                "category": "compliance",
                "violations": violations[:50],
            }
        )
    if result.fault is not None:
        f = result.fault
        checks.append(
            {
                "label": "fault current calculated",
                "value": f.total_fault_current_a,
                "passed": bool(f.currents),
            }
        )
    if result.hosting_capacity is not None:
        hc = result.hosting_capacity
        checks.append(
            {
                "label": "hosting-capacity buses evaluated",
                "value": len(hc.items),
                "passed": len(hc.items) > 0,
            }
        )
    if result.dynamics is not None:
        d = result.dynamics
        checks.append({"label": "dynamics completed", "value": d.converged, "passed": d.converged})
        if d.stable is not None:
            # An unstable transient is a legitimate engineering finding, not
            # an execution failure — surface it as a WARNING so the run
            # can't silently read as PASS (settling_note carries the reason).
            checks.append(
                {
                    "label": "system remained stable",
                    "value": d.stable if d.stable else d.settling_note,
                    "passed": bool(d.stable),
                    "category": "compliance",
                }
            )
    if result.emt is not None:
        e = result.emt
        checks.append(
            {
                "label": "EMT simulation completed",
                "value": f"{e.samples} samples / {len(e.channels)} channels",
                "passed": e.converged and e.samples > 0 and bool(e.channels),
            }
        )
    if result.time_series is not None:
        ts = result.time_series
        checks.append(
            {
                "label": "time-series steps converged",
                "value": f"{len(ts.snapshots)} snapshots",
                "passed": ts.converged and bool(ts.snapshots),
            }
        )
        checks.append(
            {
                "label": "time-series voltage within configured limits",
                "value": ts.violation_count,
                "passed": ts.violation_count == 0,
                "category": "compliance",
            }
        )
    if result.harmonics is not None:
        h = result.harmonics
        checks.append(
            {
                "label": "harmonic sweep converged",
                "value": f"{len(h.snapshots)} frequencies",
                "passed": h.converged and bool(h.snapshots),
            }
        )
    if result.protection is not None:
        p = result.protection
        checks.append(
            {
                "label": "protection fault current available",
                "value": p.fault_current_a,
                "passed": p.fault_current_a is not None,
            }
        )
        checks.append(
            {
                "label": "protection relays evaluated",
                "value": len(p.relays),
                "passed": bool(p.relays),
            }
        )
    if case.standards.grid_code == "PEA-2016":
        from cept.validation.thai_grid_code import validate_pea_2016

        grid = validate_pea_2016(case, result)
        checks.append(
            {
                "label": "PEA-2016 voltage profile",
                "value": f"{sum(1 for c in grid['checks'] if c['passed'])}/{len(grid['checks'])}",
                "passed": grid["passed"],
                "category": "compliance",
                "source": grid["source"],
            }
        )
    if result.gic is not None:
        g = result.gic
        checks.append(
            {"label": "GIC elements evaluated", "value": len(g.elements), "passed": len(g.elements) > 0}
        )

    if not checks:
        checks.append({"label": "result payload present", "value": True, "passed": True})
    compliance_checks = [c for c in checks if c.get("category") == "compliance"]
    solver_checks = [c for c in checks if c.get("category") != "compliance"]
    solver_valid = all(c["passed"] for c in solver_checks)
    engineering_compliance = all(c["passed"] for c in compliance_checks)
    return {
        # `passed` is the execution/solver acceptance gate.  A study may be a
        # valid, converged case study whose operating point intentionally
        # violates a limit; that finding belongs in engineering_compliance.
        "passed": solver_valid,
        "solver_valid": solver_valid,
        "engineering_compliance": engineering_compliance,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
    }


def _write_command_log(run_dir: Path, argv: list[str]) -> None:
    payload = [
        f"created_at={datetime.now(timezone.utc).isoformat()}",
        f"cwd={Path.cwd()}",
        "argv=" + " ".join(argv),
    ]
    (run_dir / "command_log.txt").write_text("\n".join(payload), encoding="utf-8")


__all__ = [
    "_render_report",
    "_render_report_html",
    "_is_ibr_case",
    "_inject_opendss_sld_status",
    "_inject_powerfactory_sld",
    "_inject_report_context",
    "_insert_before_closing_body",
    "_ReportEdits",
    "_write_identity_map",
    "_write_sld_layout",
    "_write_failure",
    "_write_manifest",
    "_write_validation",
    "_write_validation_record",
    "_validation_summary",
    "_write_command_log",
]
