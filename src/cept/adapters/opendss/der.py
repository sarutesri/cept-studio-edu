"""OpenDSS adapter — DER handling (PV, generators, induction machines)."""

from __future__ import annotations

import math

from cept.schema.case import Case, InductionMachine
from cept.schema.result import LoadFlowResult

from cept.adapters.opendss.dynamics_mapping import generator_dynamic_properties
from cept.adapters.opendss.utils import DER_KIND


def bus_kv(dss, bus: str, phases: int) -> tuple[str, float]:
    """Return (bus-with-nodes, element kV) for a DER at a bus."""
    dss.Circuit.SetActiveBus(bus)
    kv_ln = dss.Bus.kVBase()
    if phases >= 3:
        return f"{bus}.1.2.3", round(kv_ln * math.sqrt(3), 4)
    return f"{bus}.1", round(kv_ln, 4)


def ensure_inv_curves(dss, state: dict) -> None:
    """Define IEEE 1547-2018 (Category B) Volt-VAR and Volt-Watt curves once."""
    if state.get("_curves_defined", False):
        return
    dss.Text.Command("New XYCurve.cept_vv1547 npts=4 Xarray=(0.92 0.98 1.02 1.08) Yarray=(1.0 0.0 0.0 -1.0)")
    dss.Text.Command("New XYCurve.cept_vw1547 npts=3 Xarray=(1.06 1.08 1.10) Yarray=(1.0 0.6 0.2)")
    state["_curves_defined"] = True


def emit_pv(dss, name, bus, kw, phases, control="constant_pf", pf=1.0, kva=None, state=None) -> None:
    """Emit a PVSystem (+ InvControl for smart-inverter / GFM / GFL modes)."""
    from cept.dynamics_lib import GFL_PV_PROPS, dyneq_command

    spec, kv_val = bus_kv(dss, bus, phases)

    if control == "gfl_dynamic":
        kva = kva or round(kw * 1.2, 2)
        dss.Text.Command(dyneq_command("gfl", f"{name}_dyn"))
        dss.Text.Command(
            f"New PVSystem.{name} phases={phases} bus1={spec} kV={kv_val} "
            f"kVA={kva} Pmpp={kw} irradiance=1 {GFL_PV_PROPS} "
            f"DynamicEq={name}_dyn DynOut=[it]"
        )
        return

    smart = control not in ("constant_pf",)
    kva = kva or (round(kw * 1.2, 2) if smart else kw)
    dss.Text.Command(
        f"New PVSystem.{name} phases={phases} bus1={spec} kV={kv_val} "
        f"kVA={kva} Pmpp={kw} irradiance=1 %cutin=0.05 %cutout=0.05 "
        f"pf={pf} Vminpu=0.8 Vmaxpu=1.2 kvarMax={round(0.44 * kva, 2)} "
        f"kvarMaxAbs={round(0.44 * kva, 2)}"
    )
    if control == "constant_pf":
        return
    if control == "grid_forming":
        dss.Text.Command(f"edit PVSystem.{name} SafeVoltage=0")
        dss.Text.Command(f"New InvControl.{name}_ic mode=GFM DERList=[PVSystem.{name}]")
        return
    ensure_inv_curves(dss, state or {})
    if control == "volt_var":
        mode = "mode=VOLTVAR vvc_curve1=cept_vv1547"
    elif control == "volt_watt":
        mode = "mode=VOLTWATT voltwatt_curve=cept_vw1547"
    else:
        mode = "combimode=VV_VW vvc_curve1=cept_vv1547 voltwatt_curve=cept_vw1547"
    dss.Text.Command(f"New InvControl.{name}_ic {mode} voltage_curvex_ref=rated DERList=[PVSystem.{name}]")


def emit_generator(dss, name, bus, kw, phases, pf=1.0, machine=None) -> None:
    spec, kv_val = bus_kv(dss, bus, phases)
    cmd = (
        f"New Generator.{name} phases={phases} bus1={spec} "
        f"kV={kv_val} kW={kw} PF={pf} model=1 Vminpu=0.8 Vmaxpu=1.2"
    )
    if machine is not None:
        mva = machine.mva or round(max(kw, 1.0) * 1.2 / 1000.0, 4)
        cmd += f"{generator_dynamic_properties(machine)} MVA={mva}"
    dss.Text.Command(cmd)


def emit_storage(dss, name, bus, kw, phases, kwh=None, kva=None) -> None:
    """Emit native OpenDSS storage for an explicitly OpenDER-backed BESS."""
    spec, kv_val = bus_kv(dss, bus, phases)
    rated = kva or max(abs(kw), 1.0)
    energy = kwh or rated
    state = "DISCHARGING" if kw >= 0 else "CHARGING"
    dss.Text.Command(
        f"New Storage.{name} phases={phases} bus1={spec} kV={kv_val} "
        f"kWrated={rated} kW={abs(kw)} kWhrated={energy} kWhstored={energy} "
        f"%stored=100 %reserve=0 state={state}"
    )


def emit_indmach(dss, name, bus, kw, phases, im) -> None:
    spec, kv_val = bus_kv(dss, bus, phases)
    cmd = (
        f"New IndMach012.{name} phases={phases} bus1={spec} "
        f"kV={kv_val} kW={kw} conn={im.conn} H={im.h} D={im.d}"
    )
    if im.slip is not None:
        cmd += f" slip={im.slip}"
    dss.Text.Command(cmd)


def apply_ders(dss, case: Case, state: dict) -> None:
    """Inject declared DERs."""
    state["_der_kind"] = {}
    state["_curves_defined"] = False
    state["_opender_boundary"] = []
    for der in case.ders:
        if der.opender is not None:
            from cept.adapters.opender import require_runtime

            require_runtime(der.opender)
            state["_opender_boundary"].append(
                {
                    "der": der.id,
                    "model": der.opender.model,
                    "mode": der.opender.mode,
                    "source_repo": der.opender.source_repo,
                    "source_commit": der.opender.source_commit,
                    "interface_repo": der.opender.interface_repo,
                    "interface_commit": der.opender.interface_commit,
                    "model_version": der.opender.model_version,
                    "status": "BOUNDARY_ONLY",
                    "note": "native OpenDSS network element; status records whether pinned OpenDER P/Q was coupled",
                }
            )
        kind = DER_KIND.get(der.type, "generator")
        state["_der_kind"][der.id.lower()] = kind
        kw = der.kw if der.kw is not None else (der.kva or 0.0)
        inv = der.inverter
        pf = inv.pf if inv else 1.0
        if der.type == "pv":
            emit_pv(
                dss,
                der.id,
                der.bus,
                kw,
                der.phases,
                control=(inv.control if inv else "constant_pf"),
                pf=pf,
                kva=der.kva,
                state=state,
            )
        elif der.type == "indmach":
            emit_indmach(dss, der.id, der.bus, kw, der.phases, der.indmach or InductionMachine())
        elif der.type == "storage" and der.opender is not None and der.opender.model == "bess":
            emit_storage(dss, der.id, der.bus, kw, der.phases, der.kwh, der.kva)
            state["_der_kind"][der.id.lower()] = "battery"
        else:
            emit_generator(dss, der.id, der.bus, kw, der.phases, pf=pf, machine=der.machine)


def apply_opender_snapshot(
    dss,
    case: Case,
    load_flow: LoadFlowResult,
    settings_root: str,
    state: dict,
    power_overrides: dict[str, float] | None = None,
    t_s: float = 0.0,
) -> list[dict]:
    """Couple explicitly configured snapshot OpenDER models to OpenDSS once.

    The outer network remains the OpenDSS solve.  This function only exchanges
    the solver-returned terminal voltage with an upstream OpenDER model and
    applies its returned P/Q before a second OpenDSS solve.  Missing Common File
    Format files stay ``BOUNDARY_ONLY`` and never masquerade as model output.
    """
    from cept.adapters.opender import run_model_step

    exchanges: list[dict] = []
    for der in case.ders:
        spec = der.opender
        if spec is None:
            continue
        if spec.as_file_path is None or spec.model_file_path is None:
            continue
        voltage = load_flow.voltage(der.bus, 1)
        if voltage is None:
            raise ValueError(f"OpenDER DER '{der.id}' has no solver-returned phase-1 voltage")
        power = (power_overrides or {}).get(
            der.id,
            der.kw if der.kw is not None else (der.kva or 0.0),
        )
        exchange = run_model_step(
            spec,
            settings_root=settings_root,
            v_pu=voltage,
            f_hz=case.network.frequency_hz,
            t_s=t_s,
            runtime_state=state,
            model_key=der.id,
            available_power_kw=power if spec.model == "pv" else None,
            demand_power_kw=power if spec.model == "bess" else None,
        )
        if spec.model == "pv":
            dss.Text.Command(
                # OpenDSS PVSystem exposes Pmpp (not a writable kW property);
                # irradiance remains at the Case operating point.
                f"Edit PVSystem.{der.id} Pmpp={abs(exchange.p_kw):.12g} kvar={exchange.q_kvar:.12g}"
            )
        else:
            storage_state = "DISCHARGING" if exchange.p_kw >= 0 else "CHARGING"
            dss.Text.Command(
                f"Edit Storage.{der.id} kW={abs(exchange.p_kw):.12g} "
                f"kvar={exchange.q_kvar:.12g} state={storage_state}"
            )
        payload = exchange.model_dump(mode="json")
        payload["der"] = der.id
        exchanges.append(payload)
    state["_opender_exchanges"] = exchanges
    return exchanges
