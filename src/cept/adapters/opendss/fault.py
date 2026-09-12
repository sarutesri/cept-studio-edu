"""OpenDSS adapter — fault analysis."""

from __future__ import annotations

import math
from pathlib import Path

from cept.schema.case import Case
from cept.schema.result import (
    BusVoltage,
    FaultCurrent,
    FaultResult,
    FaultStudyRow,
)

from cept.adapters.opendss.load_flow import bus_phase_voltages, _solve
from cept.adapters.opendss.network import load_network
from cept.adapters.opendss.der import apply_ders


def apply_voltage_factor(dss, case: Case) -> float | None:
    """Raise the pre-fault source voltage to c*Un for an IEC 60909 study.

    IEC 60909's equivalent voltage source method energises the faulted network
    at ``c * Un / sqrt(3)``, not at the operating voltage.  PowerFactory applies
    the factor itself (``ComShc.cfac``); OpenDSS has no notion of it and faults
    the network at its pre-fault voltage, which understated Ik'' by the whole
    factor -- 4% on IEC_60909_Example_1.  Scaling the source is the same
    equivalent-voltage-source device, applied where OpenDSS can see it.

    Returns the factor applied, or None when the source declares none (a
    hand-built Case keeps OpenDSS's own pre-fault voltage).
    """
    factor = case.study.options.get("powerfactory_voltage_factor")
    if not isinstance(factor, (int, float)) or isinstance(factor, bool) or float(factor) <= 0:
        return None
    factor = float(factor)
    for name in dss.Vsources.AllNames():
        dss.Circuit.SetActiveElement(f"Vsource.{name}")
        try:
            pu = float(dss.Properties.Value("pu"))
        except (TypeError, ValueError):
            continue
        dss.Text.Command(f"Edit Vsource.{name} pu={pu * factor}")
    return factor


def fault_command(bus, ftype, rf, phase, phase2):
    p1 = phase or 1
    p2 = phase2 or (2 if p1 == 1 else 1)
    if ftype == "3ph":
        return f"New Fault.cept_flt bus1={bus}.1.2.3 phases=3 r={rf}", [1, 2, 3]
    if ftype == "slg":
        return f"New Fault.cept_flt bus1={bus}.{p1} phases=1 r={rf}", [p1]
    if ftype == "ll":
        return (f"New Fault.cept_flt bus1={bus}.{p1} bus2={bus}.{p2} phases=1 r={rf}", [p1, p2])
    if ftype == "llg":
        return f"New Fault.cept_flt bus1={bus}.{p1}.{p2} phases=2 r={rf}", [p1, p2]
    raise ValueError(f"Unknown fault type '{ftype}' (use 3ph|slg|ll|llg).")


def fault_study_rows(dss, work_dir: str) -> list[FaultStudyRow]:
    """Available fault current at every bus via OpenDSS FaultStudy mode."""
    dss.Text.Command("Solve Mode=FaultStudy")
    path = Path(work_dir) / "faultstudy.csv"
    dss.Text.Command(f'Export FaultStudy "{path}"')
    rows: list[FaultStudyRow] = []
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return rows
    for line in lines[1:]:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4 or not parts[0]:
            continue

        def _f(x):
            try:
                return round(float(x), 1)
            except ValueError:
                return None

        rows.append(
            FaultStudyRow(bus=parts[0], i_3ph_a=_f(parts[1]), i_1ph_a=_f(parts[2]), i_ll_a=_f(parts[3]))
        )
    return rows


def run_fault(dss, case: Case, work_dir: str, state: dict):
    opt = case.study.options
    bus = opt.get("bus")
    if not bus:
        raise ValueError("fault study requires study.options.bus")
    ftype = opt.get("type", "3ph")
    rf = float(opt.get("rf", 0.001))
    phase = opt.get("phase")
    phase2 = opt.get("phase2")

    study_rows = []
    if opt.get("run_faultstudy", True):
        load_network(dss, case.network, work_dir)
        apply_ders(dss, case, state)
        apply_voltage_factor(dss, case)
        study_rows = fault_study_rows(dss, work_dir)

    load_network(dss, case.network, work_dir)
    apply_ders(dss, case, state)
    applied_factor = apply_voltage_factor(dss, case)
    cmd, phases = fault_command(bus, ftype, rf, phase, phase2)
    dss.Text.Command(cmd)
    _solve(dss)

    dss.Circuit.SetActiveElement("Fault.cept_flt")
    cur = dss.CktElement.Currents()
    nc = dss.CktElement.NumConductors()
    currents = []
    for k, ph in enumerate(phases[:nc]):
        re, im = cur[2 * k], cur[2 * k + 1]
        currents.append(
            FaultCurrent(
                phase=ph,
                i_amp=round(abs(complex(re, im)), 2),
                i_angle_deg=round(math.degrees(math.atan2(im, re)), 2),
            )
        )
    if ftype == "ll" and len(currents) == 1 and len(phases) == 2:
        c0 = currents[0]
        currents.append(
            FaultCurrent(
                phase=phases[1], i_amp=c0.i_amp, i_angle_deg=round((c0.i_angle_deg + 180) % 360 - 180, 2)
            )
        )

    vpu, ang = bus_phase_voltages(dss)
    sag = [
        BusVoltage(bus=b, phase=p, v_pu=vpu[b][p], v_angle_deg=ang[b][p]) for b in vpu for p in sorted(vpu[b])
    ]
    fr = FaultResult(
        bus=bus,
        fault_type=ftype,
        rf_ohm=rf,
        phases=phases,
        currents=currents,
        total_fault_current_a=round(max((c.i_amp for c in currents), default=0.0), 2),
        bus_voltages_during=sag,
        min_voltage_pu=min((bv.v_pu for bv in sag), default=None),
        study_rows=study_rows,
        voltage_factor_applied=applied_factor,
    )

    from cept.adapters.opendss.sld import build_sld, SLDEvent

    sld = build_sld(dss, case, der_kind=state.get("_der_kind"))
    for n in sld.nodes:
        if n.id.lower() == bus.lower():
            n.event = SLDEvent(kind="fault", label=f"{ftype.upper()} fault")
    return fr, sld
