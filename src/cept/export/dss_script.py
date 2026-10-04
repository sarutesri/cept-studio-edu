"""Generate a self-contained OpenDSS script (.dss) from a Case schema.

The output is a single master .dss file (plus a tiny companion README) that
can be opened in OpenDSS / OpenDSS-G without modification.

Supported study types
---------------------
load_flow / unbalanced_load_flow
    Solves and writes explicit CSV Export commands. Interactive ``Show``
    commands are opt-in via ``include_show_commands=True``.
fault
    Adds a Fault element, solves, and writes FaultStudy + sag exports.
hosting_capacity
    Writes a loop over candidate buses using a PVSystem probe DER and
    comments explaining the binary search used by the Python harness.
    Because OpenDSS script does not have a native HC sweep, it exports
    a stand-alone snapshot *at the weakest-bus HC operating point*.
ibr (load_flow with Volt-VAR / Volt-Watt DERs)
    Uses PVSystem + InvControl — OpenDSS-G can step through the control
    iterations interactively.

Notes for OpenDSS-G users
--------------------------
- Open the exported *_master.dss file with File → Open Script.
- Click the toolbar Run button (or type "Solve" in the command window).
- The script uses ``Plot Circuit`` and ``Plot Profile`` which render in
  OpenDSS-G's built-in plot window.
- ``Show Voltages LN Nodes`` and ``Export Voltages`` write results to the
  default output directory (same folder as the script).
"""

from __future__ import annotations

import importlib
import math
import textwrap
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Optional

from cept.schema.case import Case, DER, ExperimentAction
from cept.adapters.opendss.translation import (
    BUILTIN_PATH as _BUILTIN_PATH,
    BusKvSource,
    LiveDssBusKvSource,
    _transformer_phase_refs,
    aggregate_parallel_generator,
    inline_load_model_options,
    matrix_line_commands,
    transformer_core_options,
    transformer_tap_option,
)

# IEEE 1547-2018 Category-B curve definitions (same as adapter)
_VV_CURVE = "New XYCurve.cept_vv1547 npts=4 Xarray=(0.92 0.98 1.02 1.08) Yarray=(1.0 0.0 0.0 -1.0)"
_VW_CURVE = "New XYCurve.cept_vw1547 npts=3 Xarray=(1.06 1.08 1.10) Yarray=(1.0 0.6 0.2)"


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def export_dss(
    case: Case,
    stem: str = "cept_study",
    out_dir: str | Path = "exports",
    *,
    bundle_feeder: bool = True,
    include_show_commands: bool = False,
    bus_kv_source: BusKvSource | None = None,
) -> Path:
    """Export *case* as a standalone OpenDSS script.

    Parameters
    ----------
    bundle_feeder
        If True (default), copy all feeder source files into a
        ``feeder/`` subfolder next to the master script so the package
        is fully self-contained and works on any machine.
    bus_kv_source
        Injected ``{bus: kV_LL}`` resolver for the DER overlay.  Defaults
        to :class:`LiveDssBusKvSource`; the exporter itself never opens a
        solver.

    Returns the path of the written master .dss file.
    """
    import shutil

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    kv_map = (bus_kv_source or LiveDssBusKvSource()).resolve(case)

    # --- bundle feeder files ----------------------------------------------- #
    feeder_rel: Optional[str] = None
    if bundle_feeder and case.network.kind in ("builtin", "dss_file"):
        src_master = (
            _BUILTIN_PATH.get(case.network.name or "")
            if case.network.kind == "builtin"
            else Path(case.network.path or "")
        )
        if src_master and Path(src_master).exists():
            feeder_dir = out / "feeder"
            feeder_dir.mkdir(exist_ok=True)
            src_dir = Path(src_master).parent
            for f in src_dir.iterdir():
                if f.is_file():
                    shutil.copy2(f, feeder_dir / f.name)
            feeder_rel = f"feeder/{Path(src_master).name}"

    script = _build_script(
        case,
        kv_map,
        feeder_rel=feeder_rel,
        include_show_commands=include_show_commands,
    )
    path = out / f"{stem}_master.dss"
    path.write_text(script, encoding="utf-8")
    readme = out / f"{stem}_README.txt"
    readme.write_text(_readme(case, path, bundled=bundle_feeder), encoding="utf-8")
    _write_package_manifest(out, case, path, readme)
    return path


def _write_package_manifest(out: Path, case: Case, master: Path, readme: Path) -> Path:
    """Record the portable package contents and hashes.

    The manifest is deliberately plain JSON so a researcher can audit or
    verify a package without importing CEPT.  Only files inside ``out`` are
    listed; this catches accidental absolute-path leakage in future changes.
    """
    files: list[dict[str, str | int]] = []
    for path in sorted(out.rglob("*")):
        if not path.is_file() or path.name == "package.json":
            continue
        rel = path.relative_to(out).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        files.append({"path": rel, "sha256": digest, "bytes": path.stat().st_size})
    payload = {
        "format": "cept-opendss-package-v1",
        "case_name": case.meta.name,
        "case_fingerprint": case.fingerprint(),
        "master": master.relative_to(out).as_posix(),
        "readme": readme.relative_to(out).as_posix(),
        "portable": True,
        "files": files,
    }
    target = out / "package.json"
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


# --------------------------------------------------------------------------- #
# Builder
# --------------------------------------------------------------------------- #


def _build_script(
    case: Case,
    kv_map: dict[str, float] | None = None,
    feeder_rel: Optional[str] = None,
    include_show_commands: bool = False,
) -> str:
    kv_map = kv_map or {}
    lines: list[str] = []
    _header(lines, case)
    _network(lines, case, feeder_rel=feeder_rel)
    _ders(lines, case, kv_map)
    _experiment_before(lines, case)
    _solve_block(lines, case)
    _experiment_after(lines, case)
    _outputs(lines, case, include_show_commands=include_show_commands)
    _plot_commands(lines, case)
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Section builders
# --------------------------------------------------------------------------- #


def _header(L, case: Case):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    L += [
        "! ============================================================",
        "! CEPT Power Studio — exported script",
        f"! Study  : {case.meta.name}",
        f"! Author : {case.meta.author}",
        f"! Created: {now}",
        f"! Fingerprint: {case.fingerprint()}",
        "!",
        f"! {case.meta.description}" if case.meta.description else "!",
        "!",
        "! HOW TO RUN (OpenDSS-G):",
        "!   File > Open Script > select this file > Run",
        "! HOW TO RUN (OpenDSS CLI):",
        f'!   redirect "{Path(__file__).name}"   ! or compile',
        "! ============================================================",
        "",
        "Clear",
        f"Set DefaultBaseFrequency={case.network.frequency_hz:.0f}",
        "",
    ]


def _network(L, case: Case, feeder_rel: Optional[str] = None):
    net = case.network
    if net.kind in ("builtin", "dss_file"):
        if feeder_rel:
            # Relative path — portable across machines
            redirect_path = feeder_rel
        else:
            master = _BUILTIN_PATH.get(net.name or "") if net.kind == "builtin" else Path(net.path or "")
            redirect_path = str(master) if master else None

        if redirect_path:
            L += [
                "! --- Base network ---",
                f'Redirect "{redirect_path}"',
                "",
            ]
    elif net.kind == "inline":
        _inline_network(L, case)


def _dss_quote(value: object) -> str:
    """Quote a DSS token so portable exports survive spaces and punctuation."""
    return '"' + str(value).replace('"', '""') + '"'


def _dss_object(class_name: str, name: object) -> str:
    return _dss_quote(f"{class_name}.{name}")


def _dss_transformer_conns_taps(tr) -> tuple[str, str]:
    vg = (getattr(tr, "vector_group", None) or "Ynyn0").strip()
    hv_conn = "delta" if vg.startswith("D") or vg.startswith("d") else "wye"
    lv_conn = "delta" if any(c in vg[1:] for c in ("D", "d")) else "wye"
    conns_str = f"conns=({hv_conn} {lv_conn})"
    # One mapping, shared with the live compiler: a hand-run export that
    # silently solves a different circuit than `cept run` is the same class of
    # defect this whole gate exists to stop.
    return conns_str, transformer_tap_option(tr)


def _inline_network(L: list[str], case: Case) -> None:
    """Write the complete inline network, not just a placeholder.

    The in-memory adapter and the handoff package must consume the same typed
    Case.  This exporter intentionally mirrors the adapter's positive-sequence
    representation; missing sequence data remains visible in the Case and is
    not silently promoted to validation evidence.
    """
    net = case.network
    inline = net.inline
    if inline is None:
        raise ValueError("inline network is missing")
    bus_kv = {bus.name: bus.kv for bus in inline.buses}
    eg = inline.external_grids[0] if inline.external_grids else None
    slack = next((gen for gen in inline.generators if gen.bus_type == "slack"), None)
    if eg is None and slack is None:
        raise ValueError("inline export requires an external_grid or slack generator")

    L += ["! --- Inline network definitions ---"]
    if eg is not None:
        sk1 = eg.sk1_mva if eg.sk1_mva is not None else eg.sk3_mva
        L.append(
            f"New {_dss_object('Circuit', f'inline_{eg.name}')} "
            f"basekv={bus_kv[eg.bus]} bus1={_dss_quote(eg.bus)} pu={eg.pu} "
            f"angle={eg.angle_deg} MVAsc3={eg.sk3_mva} MVAsc1={sk1} "
            f"x1r1={eg.x_r_ratio} x0r0={eg.x_r_ratio}"
        )
    else:
        # A slack-only Case has no Thevenin strength. Keep the same ideal
        # load-flow reference as the OpenDSS adapter; fault claims remain
        # blocked until explicit source strength is supplied.
        L.append(
            f"New {_dss_object('Circuit', f'inline_{slack.name}')} "
            f"basekv={bus_kv[slack.bus]} bus1={_dss_quote(slack.bus)} "
            f"pu={slack.pu} angle=0 MVAsc3=1e9 MVAsc1=1e9"
        )
    for extra in inline.external_grids[1:]:
        L.append(
            f"New {_dss_object('Vsource', extra.name)} bus1={_dss_quote(extra.bus)} "
            f"basekv={bus_kv[extra.bus]} pu={extra.pu} angle={extra.angle_deg} "
            f"MVAsc3={extra.sk3_mva} MVAsc1={extra.sk1_mva or extra.sk3_mva} "
            f"x1r1={extra.x_r_ratio} x0r0={extra.x_r_ratio}"
        )

    open_elements = set(getattr(inline, "open_elements", []))
    terminal_switches = {sw.element: sw for sw in getattr(inline, "terminal_switches", []) if not sw.closed}
    for line in inline.lines:
        if line.matrix is not None:
            L += matrix_line_commands(line, float(net.frequency_hz), set(open_elements))
            continue
        r0 = line.r0_for_adapter_ohm_per_km
        x0 = line.x0_for_adapter_ohm_per_km
        # Match the in-memory adapter's explicit demonstrator fallback. The
        # Case remains the source of truth; missing zero-sequence data must
        # still keep the study out of a validated claim.
        if r0 is None:
            r0 = 3.0 * line.r1_for_adapter_ohm_per_km
        if x0 is None:
            x0 = 3.0 * line.x1_for_adapter_ohm_per_km
        c1_nf = line.b1_for_adapter_us_per_km * 1000.0 / (2.0 * math.pi * net.frequency_hz)
        code = f"lc_{line.name}"
        L += [
            f"New {_dss_object('LineCode', code)} nphases=3 baseFreq={net.frequency_hz} "
            f"r1={line.r1_for_adapter_ohm_per_km} x1={line.x1_for_adapter_ohm_per_km} "
            f"r0={r0} x0={x0} c1={c1_nf} c0={c1_nf} units=km",
            f"New {_dss_object('Line', line.name)} bus1={_dss_quote(line.from_bus)} "
            f"bus2={_dss_quote(line.to_bus)} linecode={_dss_quote(code)} "
            f"length={line.adapter_length_km} units=km"
            + (f" normamps={line.normal_amps}" if line.normal_amps else "")
            + (" enabled=n" if line.name in open_elements else ""),
        ]
        terminal = terminal_switches.get(line.name)
        if terminal is not None and line.name in open_elements:
            if terminal.bus not in (line.from_bus, line.to_bus):
                raise ValueError(f"terminal switch '{terminal.name}' is not at line '{line.name}' endpoint")
            floating_bus = f"__open_terminal_{line.name}"
            if terminal.bus == line.from_bus:
                line_bus1, line_bus2 = floating_bus, line.to_bus
            else:
                line_bus1, line_bus2 = line.from_bus, floating_bus
            L += [
                f"Edit {_dss_object('Line', line.name)} bus1={_dss_quote(line_bus1)} "
                f"bus2={_dss_quote(line_bus2)} enabled=y",
                f"New {_dss_object('Line', 'sw_' + terminal.name)} "
                f"bus1={_dss_quote(terminal.bus)} bus2={_dss_quote(floating_bus)} "
                "switch=y r1=0.0001 x1=0.0001 enabled=n",
            ]

    transformer_bus_refs: dict[str, str] = {}
    for transformer in inline.transformers:
        r_pct, x_pct = transformer.series_r_x_pct
        conns_str, taps_str = _dss_transformer_conns_taps(transformer)
        hv_ref, lv_ref, leadlag = _transformer_phase_refs(transformer)
        transformer_bus_refs[transformer.lv_bus] = lv_ref
        phase_option = f" leadlag={leadlag}" if leadlag else ""
        core_option = transformer_core_options(transformer)
        L.append(
            f"New {_dss_object('Transformer', transformer.name)} phases=3 windings=2 "
            f"xhl={x_pct} %rs=({r_pct / 2.0} {r_pct / 2.0}) "
            f"buses=({_dss_quote(hv_ref)} {_dss_quote(lv_ref)}) "
            f"kvs=({transformer.hv_kv} {transformer.lv_kv}) "
            f"kvas=({transformer.mva * transformer.parallel_units * 1000} "
            f"{transformer.mva * transformer.parallel_units * 1000}) "
            f"{conns_str} {taps_str}{phase_option}{core_option}"
            + (" enabled=n" if transformer.name in open_elements else "")
        )

    for load in inline.loads:
        kvar = load.q_kvar
        if kvar is None:
            kvar = load.kw * math.sqrt(max(1.0 - load.pf**2, 0.0)) / load.pf
        model, load_options = inline_load_model_options(inline, load)
        L.append(
            f"New {_dss_object('Load', load.id)} bus1={_dss_quote(load.bus)} phases=3 "
            f"kV={bus_kv[load.bus]} kW={load.kw} kvar={kvar} model={model}"
            f"{load_options}" + (" enabled=n" if load.id in open_elements else "")
        )

    for generator in inline.generators:
        if generator.bus_type == "slack" and eg is None:
            continue
        # Parallel machine units are a PowerFactory concept (pgini/sgn are per
        # machine, dispatch is ngnum x pgini). OpenDSS has no equivalent, so
        # fold the count into the emitted rating -- see compile_inline for the
        # 39-bus measurement that exposed this.
        gen_kw, gen_mva = aggregate_parallel_generator(generator)
        if generator.bus_type == "slack":
            sk3 = gen_mva / max(generator.xdpp_pu, 1e-3)
            L.append(
                f"New {_dss_object('Vsource', generator.name)} bus1={_dss_quote(generator.bus)} "
                f"basekv={bus_kv[generator.bus]} pu={generator.pu} "
                f"MVAsc3={sk3} MVAsc1={sk3} x1r1=10 x0r0=10"
            )
            continue
        model = 3 if generator.bus_type == "pv" else 1
        pf = generator.pf if generator.bus_type == "pq" else 1.0
        q_limit = math.sqrt(max((gen_mva * 1000.0) ** 2 - gen_kw**2, 0.0))
        limits = (
            f" Maxkvar={q_limit:.12g} Minkvar={-q_limit:.12g} Pvfactor=0.1"
            if generator.bus_type == "pv"
            else ""
        )
        machine = generator.dynamics
        if machine is None:
            dynamic_props = f" Xdp={generator.xdpp_pu} Xdpp={generator.xdpp_pu}"
        else:
            # Dynamic, matching der.py/network.py: the dynamics adapter is a
            # feature-gated module the free wheel does not ship, and a static
            # import here would make the export's import closure require it even
            # for a load_flow export that never touches generator dynamics.
            dynamics_mapping = importlib.import_module(
                "cept.adapters.opendss.dynamics_mapping"
            )
            dynamic_props = dynamics_mapping.generator_dynamic_properties(machine)
        L.append(
            f"New {_dss_object('Generator', generator.name)} bus1={_dss_quote(transformer_bus_refs.get(generator.bus, generator.bus))} "
            f"phases=3 kV={generator.kv} kW={gen_kw} PF={pf} "
            f"kVA={gen_mva * 1000} model={model} Vpu={generator.pu}{limits}"
            f"{dynamic_props}"
        )
    L += [
        "Set VoltageBases=["
        + " ".join(sorted({str(bus.kv) for bus in inline.buses}, key=float, reverse=True))
        + "]",
        "CalcVoltageBases",
        f"Set Frequency={net.frequency_hz}",
        "",
    ]


def _ders(L, case: Case, kv_map: dict[str, float]):
    if not case.ders:
        return
    L += [
        "! --- DER overlays (injected on top of the base network) ---",
        "",
    ]
    need_vv = any(
        d.inverter and d.inverter.control in ("volt_var", "volt_var_volt_watt")
        for d in case.ders
        if d.type == "pv"
    )
    need_vw = any(
        d.inverter and d.inverter.control in ("volt_watt", "volt_var_volt_watt")
        for d in case.ders
        if d.type == "pv"
    )
    if need_vv:
        L.append(_VV_CURVE)
    if need_vw:
        L.append(_VW_CURVE)
    if need_vv or need_vw:
        L.append("")

    for der in case.ders:
        L += _der_lines(der, kv_map)
    L.append("")


def _der_lines(der: DER, kv_map: dict[str, float]) -> list[str]:
    lines: list[str] = [f"! DER: {der.id}  ({der.type})"]
    inv = der.inverter
    pf = inv.pf if inv else 1.0
    kw = der.kw if der.kw is not None else (der.kva or 0.0)
    ph = der.phases
    bus_nodes = f"{der.bus}.1.2.3" if ph >= 3 else f"{der.bus}.1"

    # Use resolved kV; fall back to calculated estimate from LN→LL if unavailable.
    kv_ll = kv_map.get(der.bus.lower())
    if kv_ll is None:
        kv_ll_str = f"! UNRESOLVED — set manually for bus {der.bus}"
    else:
        kv_ll_str = str(kv_ll)

    if der.type == "pv":
        ctrl = inv.control if inv else "constant_pf"
        kva = der.kva or (round(kw * 1.2, 2) if ctrl != "constant_pf" else kw)
        kvar_max = round(0.44 * kva, 2)
        lines.append(
            f"New PVSystem.{der.id} phases={ph} bus1={bus_nodes} "
            f"kV={kv_ll_str} kVA={kva} Pmpp={kw} irradiance=1 "
            f"%cutin=0.05 %cutout=0.05 pf={pf} "
            f"Vminpu=0.8 Vmaxpu=1.2 kvarMax={kvar_max} kvarMaxAbs={kvar_max}"
        )
        if ctrl == "volt_var":
            lines.append(
                f"New InvControl.{der.id}_ic mode=VOLTVAR "
                f"vvc_curve1=cept_vv1547 voltage_curvex_ref=rated "
                f"DERList=[PVSystem.{der.id}]"
            )
        elif ctrl == "volt_watt":
            lines.append(
                f"New InvControl.{der.id}_ic mode=VOLTWATT "
                f"voltwatt_curve=cept_vw1547 voltage_curvex_ref=rated "
                f"DERList=[PVSystem.{der.id}]"
            )
        elif ctrl == "volt_var_volt_watt":
            lines.append(
                f"New InvControl.{der.id}_ic combimode=VV_VW "
                f"vvc_curve1=cept_vv1547 voltwatt_curve=cept_vw1547 "
                f"voltage_curvex_ref=rated DERList=[PVSystem.{der.id}]"
            )
    elif der.type == "storage":
        lines.append(
            f"New Storage.{der.id} phases={ph} bus1={bus_nodes} "
            f"kV={kv_ll_str} kWhrated={der.kwh or kw} kWrated={kw} "
            f"State=IDLING %stored=100"
        )
    else:
        lines.append(
            f"New Generator.{der.id} phases={ph} bus1={bus_nodes} "
            f"kV={kv_ll_str} kW={kw} PF={pf} model=1 Vminpu=0.8 Vmaxpu=1.2"
        )
    return lines + [""]


def _experiment_before(L, case: Case):
    exp = case.experiment
    if not exp:
        return
    L += [
        f"! --- Experiment (before): {exp.name} ---",
        f"! Description: {exp.description}" if exp.description else "!",
        "! Solve baseline first, then apply the disturbances below.",
        "Solve",
        "! --- Baseline solved. Now apply disturbances: ---",
        "",
    ]
    for action in exp.actions:
        L += _action_lines(action)


def _action_lines(a: ExperimentAction) -> list[str]:
    lbl = a.label or a.kind
    lines = [f"! Action: {lbl}"]
    if a.kind == "fault":
        bus = a.params.get("bus", a.target)
        phases = a.params.get("phases", 3)
        r_f = a.params.get("r", 0.001)
        ph_s = a.params.get("phase", 1)
        if phases == 3:
            conn = ".1.2.3"
        elif phases == 2:
            p2 = a.params.get("phase2", 2)
            conn = f".{ph_s}.{p2}"
        else:
            conn = f".{ph_s}"
        lines.append(f"New Fault.cept_{a.target} bus1={bus}{conn} phases={phases} r={r_f}")
    elif a.kind == "open":
        term = a.params.get("term", 1)
        lines.append(f"Open Line.{a.target} term={term}")
    elif a.kind == "close":
        term = a.params.get("term", 1)
        lines.append(f"Close Line.{a.target} term={term}")
    elif a.kind == "trip_gen":
        lines.append(f"Generator.{a.target}.enabled=no")
    elif a.kind == "shed_load":
        lines.append(f"Load.{a.target}.enabled=no")
    elif a.kind == "set_tap":
        tap = a.params.get("tap")
        wdg = a.params.get("wdg", 2)
        lines.append(f"Transformer.{a.target}.wdg={wdg} Tap={tap}")
    return lines + [""]


def _experiment_after(L, case: Case):
    """Nothing extra needed after the post-disturbance Solve (done in _solve_block)."""
    pass


def _solve_block(L, case: Case):
    st = case.study.type
    L.append("! --- Solve ---")
    L.append("Set MaxControlIter=100")

    if st == "fault":
        bus = case.study.options.get("bus", "?")
        ftype = case.study.options.get("type", "3ph")
        rf = case.study.options.get("rf", 0.001)
        phase = case.study.options.get("phase", 1)
        phase2 = case.study.options.get("phase2", 2)
        if ftype == "3ph":
            conn = ".1.2.3"
            phases = 3
        elif ftype == "slg":
            conn = f".{phase}"
            phases = 1
        elif ftype == "ll":
            conn = f".{phase}.{phase2}"
            phases = 1
        else:
            conn = f".{phase}.{phase2}"
            phases = 2
        L += [
            f"! {ftype.upper()} fault at bus {bus} (Rf = {rf} Ω)",
            f"New Fault.cept_fault bus1={bus}{conn} phases={phases} r={rf}",
            "Solve",
            "! --- Run FaultStudy for available fault current at every bus ---",
            "Solve Mode=FaultStudy",
            "",
        ]
    else:
        L += ["Solve", ""]


def _outputs(L, case: Case, *, include_show_commands: bool = False):
    st = case.study.type
    L += [
        "! ============================================================",
        "! Results",
        "! ============================================================",
        "",
        "! --- CSV exports ---",
        'Export Voltages "voltages.csv"',
        'Export Losses   "losses.csv"',
        'Export Powers   "powers.csv"',
        'Export Summary  "summary.csv"',
    ]
    if st == "fault":
        L.append('Export FaultStudy "faultstudy.csv"')
    if case.ders:
        L.append('Export Generators "generators.csv"')
    if include_show_commands:
        L += [
            "",
            "! --- Interactive OpenDSS-G views (opt-in) ---",
            "Show Voltages LN Nodes",
            "Show Powers kVA Elem",
            "Show Losses",
            "Show Taps",
        ]
        if st == "fault":
            L.append("Show Faults")
    L.append("")


def _plot_commands(L, case: Case):
    """Plot commands that render in OpenDSS-G's built-in plot window."""
    L += [
        "! ============================================================",
        "! Plots  (render in OpenDSS-G plot window)",
        "! ============================================================",
        "",
        "! Circuit diagram with power flow (MW, colour = loading)",
        "Set MarkTransformers=Yes  MarkCapacitors=Yes",
        "Plot Circuit Power Max=2000 dots=y labels=y subs=y   C1=$00FF0000 C2=$0000FF00 C3=$000000FF",
        "",
        "! Voltage profile (all phases)",
        "Plot Profile phases=all",
        "",
    ]
    if case.study.type in ("load_flow", "unbalanced_load_flow") and case.ders:
        L += [
            "! Voltage profile with DER injection visible",
            "Plot Profile phases=all",
            "",
        ]
    if case.study.type == "fault":
        bus = case.study.options.get("bus", "?")
        L += [
            f"! Voltage sag after fault at {bus}",
            "Plot Profile phases=all",
            "",
        ]


# --------------------------------------------------------------------------- #
# Companion README
# --------------------------------------------------------------------------- #


def _readme(case: Case, path: Path, bundled: bool = True) -> str:
    bundle_note = (
        "Feeder files are copied to the feeder/ subfolder — "
        "this package is self-contained and works on any machine."
        if bundled
        else "Feeder uses an absolute path — only works on the original machine."
    )
    return textwrap.dedent(f"""\
    CEPT Power Studio — Exported OpenDSS Script
    ================================================
    Study   : {case.meta.name}
    Script  : {path.name}
    Feeder  : {case.network.kind} / {case.network.name or case.network.path}
    Study   : {case.study.type}
    DERs    : {len(case.ders)} declared
    Expt.   : {case.experiment.name if case.experiment else "none"}
    Portable: {bundle_note}

    How to run in OpenDSS-G (GUI)
    -----------------------------
    1. Download OpenDSS-G from:
       https://sourceforge.net/projects/electricdss/
       or the newer OpenDSS project at:
       https://github.com/dss-extensions/OpenDSS
    2. File > Open Script > select "{path.name}"
    3. Click the Run (▶) toolbar button.
    4. Results appear in the text window; plots in the plot window.
       CSV files are written to this folder.

    How to run in OpenDSS command-line
    -----------------------------------
        opendsscmd "{path.name}"

    Notes on __KV__ placeholders
    -----------------------------
    DERs that were injected by the CEPT core use "__KV__" as a placeholder
    for the line-to-line kV at the bus. Replace with the correct value
    before running, or let OpenDSS use CalcVoltageBases.

    Voltage base lookup (IEEE 13-node example)
    -------------------------------------------
    Bus 675 = 4.16 kV (LL), 675.1.2.3 3-phase
    Bus 634 = 0.48 kV (LL), secondary transformer
    """)
