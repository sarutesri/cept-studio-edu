"""OpenDSS adapter — load flow execution and result extraction."""

from __future__ import annotations

import math
from typing import Optional

from cept.schema.case import Case, InlineNetwork
from cept.schema.result import (
    BranchFlow,
    BusVoltage,
    DEROutput,
    LoadFlowResult,
)


def solve_load_flow(dss, case: Case, extra_commands=None, solver: str = "native") -> LoadFlowResult:
    """Lightweight path used by the validation harness (no SLD/experiment)."""
    from cept.adapters.opendss.network import load_network
    from cept.adapters.opendss.der import apply_ders

    if solver == "ybus-nr":
        from cept.adapters.opendss.ybus_power_flow import solve_ybus_power_flow

        load_network(dss, case.network, "", regulate_slack_terminal=True)
        apply_ders(dss, case, {})
        for cmd in extra_commands or ():
            dss.Text.Command(cmd)
        result, _evidence = solve_ybus_power_flow(dss, case)
        return result
    if solver != "native":
        raise ValueError(f"Unknown solver lane '{solver}'.")
    load_network(dss, case.network, "", regulate_slack_terminal=True)
    apply_ders(dss, case, {})
    for cmd in extra_commands or ():
        dss.Text.Command(cmd)
    _solve(dss, refine=_snapshot_refine_ok(case))
    return extract_load_flow(dss, case.network.inline)


# Refined convergence tolerance for the snapshot power flow.  OpenDSS's default
# tolerance is coarse enough that Newton stops after ~2 iterations, leaving bus
# angles off by ~1e-4 deg; on short, low-impedance MV lines dP/ddelta is steep,
# so that angle residual amplifies into ~0.03% active-power errors versus a
# PowerFactory reference.  Tightening is a solver-accuracy setting, not an
# acceptance tolerance.  It is opt-in (``refine``) because some models — notably
# OpenDSS PV generators cycling their var limits — oscillate and never settle
# below a tight tolerance; for those we leave the robust default untouched.
_SOLVE_TOL_DEFAULT = 1e-4
_SOLVE_TOL_REFINE = 1e-8
_SOLVE_MAXITER = 100


def _snapshot_refine_ok(case) -> bool:
    """True when the network has no oscillation-prone voltage-controlled
    generators, so a tighter snapshot tolerance can be safely applied."""
    net = getattr(case.network, "inline", None)
    if net is None:
        return False
    gens = getattr(net, "generators", None) or []
    return not any((getattr(g, "bus_type", "") or "").lower() == "pv" for g in gens)


def _raw_solve(dss) -> None:
    try:
        dss.Solution.Solve()
    except Exception as exc:
        if "485" not in str(exc) and "Max Control" not in str(exc):
            raise


def _solve(dss, refine: bool = False) -> None:
    """Solve and swallow OpenDSS warning-level control exceptions (#485).

    With ``refine`` the snapshot is first converged at the robust default
    tolerance and then warm-started at a tighter one; if the tight pass fails to
    converge the default-tolerance solution is restored.  Solver settings are
    saved and restored so a refined solve never leaks tolerance/iteration state
    onto later solves that share the OpenDSS singleton.
    """
    if not refine:
        _raw_solve(dss)
        return
    prev_tol = dss.Solution.Convergence()
    prev_max = dss.Solution.MaxIterations()
    try:
        dss.Solution.MaxIterations(_SOLVE_MAXITER)
        dss.Solution.Convergence(_SOLVE_TOL_DEFAULT)
        _raw_solve(dss)
        if not dss.Solution.Converged():
            return  # honest failure surfaces downstream via converged=False
        dss.Solution.Convergence(_SOLVE_TOL_REFINE)
        _raw_solve(dss)
        if not dss.Solution.Converged():
            dss.Solution.Convergence(_SOLVE_TOL_DEFAULT)
            _raw_solve(dss)
    finally:
        dss.Solution.Convergence(prev_tol)
        dss.Solution.MaxIterations(prev_max)


def bus_phase_voltages(dss):
    names = dss.Circuit.AllNodeNames()
    vmag = dss.Circuit.AllBusMagPu()
    volts = dss.Circuit.AllBusVolts()
    vpu: dict[str, dict[int, float]] = {}
    ang: dict[str, dict[int, float]] = {}
    for i, name in enumerate(names):
        bus, _, ph_s = name.partition(".")
        try:
            ph = int(ph_s)
        except ValueError:
            continue
        if not 1 <= ph <= 3:
            continue
        re, im = volts[2 * i], volts[2 * i + 1]
        vpu.setdefault(bus, {})[ph] = round(vmag[i], 6)
        ang.setdefault(bus, {})[ph] = round(math.degrees(math.atan2(im, re)), 4)
    return vpu, ang


def extract_load_flow(dss, net: Optional[InlineNetwork] = None) -> LoadFlowResult:
    vpu, ang = bus_phase_voltages(dss)
    if net is not None and net.buses:
        reported_buses = {bus.name.lower() for bus in net.buses}
        vpu = {name: values for name, values in vpu.items() if name.lower() in reported_buses}
        ang = {name: values for name, values in ang.items() if name.lower() in reported_buses}
    bus_voltages = [
        BusVoltage(bus=b, phase=p, v_pu=vpu[b][p], v_angle_deg=ang[b][p]) for b in vpu for p in sorted(vpu[b])
    ]
    tp = dss.Circuit.TotalPower()
    losses = dss.Circuit.Losses()
    total_load_kw = 0.0
    total_load_kvar = 0.0
    load_outputs: list[DEROutput] = []
    # An unbalanced load is emitted as one OpenDSS object per energised phase
    # ("L634-YcPQ_1", "_2", "_3").  The Case still has one load, and so does the
    # source result, so the phases are summed back under the canonical id --
    # otherwise every unbalanced load would arrive as several elements the
    # comparator has never heard of.
    case_load_ids = {load.id for load in net.loads} if net is not None else set()
    aggregated: dict[str, list[float]] = {}
    aggregated_bus: dict[str, str] = {}
    i_load = dss.Loads.First()
    while i_load:
        name = dss.Loads.Name()
        dss.Circuit.SetActiveElement(f"Load.{name}")
        buses = dss.CktElement.BusNames()
        powers = dss.CktElement.Powers()
        nc = dss.CktElement.NumConductors()
        p_kw = sum(powers[0 : 2 * nc : 2])
        q_kvar = sum(powers[1 : 2 * nc : 2])
        total_load_kw += p_kw
        total_load_kvar += q_kvar
        canonical = name
        if canonical not in case_load_ids and "_" in name:
            stem = name.rsplit("_", 1)[0]
            if stem in case_load_ids or any(item.lower() == stem.lower() for item in case_load_ids):
                canonical = next((item for item in case_load_ids if item.lower() == stem.lower()), stem)
        entry = aggregated.setdefault(canonical, [0.0, 0.0])
        entry[0] += p_kw
        entry[1] += q_kvar
        aggregated_bus.setdefault(canonical, buses[0].split(".")[0].lower())
        i_load = dss.Loads.Next()
    for canonical, (p_kw, q_kvar) in aggregated.items():
        load_outputs.append(
            DEROutput(
                name=canonical,
                kind="load",
                bus=aggregated_bus[canonical],
                p_kw=round(p_kw, 4),
                q_kvar=round(q_kvar, 4),
            )
        )
    if net is not None:
        represented_loads = {item.name.lower() for item in load_outputs}
        for load in net.loads:
            if load.id in net.open_elements and load.id.lower() not in represented_loads:
                load_outputs.append(
                    DEROutput(
                        name=load.id,
                        kind="load",
                        bus=load.bus.lower(),
                        p_kw=0.0,
                        q_kvar=0.0,
                    )
                )
    return LoadFlowResult(
        converged=bool(dss.Solution.Converged()),
        iterations=dss.Solution.Iterations(),
        bus_voltages=bus_voltages,
        branch_flows=extract_line_flows(dss, net) + extract_transformer_flows(dss),
        source_p_kw=round(-tp[0], 4),
        source_q_kvar=round(-tp[1], 4),
        total_load_kw=round(total_load_kw, 4),
        total_load_kvar=round(total_load_kvar, 4),
        total_loss_kw=round(losses[0] / 1000.0, 4),
        total_loss_kvar=round(losses[1] / 1000.0, 4),
        der_outputs=extract_der_outputs(dss, vpu, net),
        device_outputs=load_outputs,
    )


def extract_der_outputs(dss, vpu, net: Optional[InlineNetwork] = None) -> list[DEROutput]:
    """Extract actual P, Q from every solver-emitted DER element."""
    outs: list[DEROutput] = []
    for cls, default_kind in [("Generator", "generator"), ("PVSystem", "pv")]:
        first = dss.Generators.First if cls == "Generator" else dss.PVsystems.First
        nxt = dss.Generators.Next if cls == "Generator" else dss.PVsystems.Next
        name_fn = dss.Generators.Name if cls == "Generator" else dss.PVsystems.Name
        i = first()
        while i:
            outs.append(_der_output(dss, cls, name_fn(), default_kind, vpu))
            i = nxt()
    if hasattr(dss, "Storages"):
        i = dss.Storages.First()
        while i:
            outs.append(_der_output(dss, "Storage", dss.Storages.Name(), "battery", vpu))
            i = dss.Storages.Next()
    for full in dss.Circuit.AllElementNames():
        if full.lower().startswith("indmach012."):
            outs.append(_der_output(dss, "IndMach012", full.split(".", 1)[1], "indmach", vpu))
    i = dss.Vsources.First()
    while i:
        name = dss.Vsources.Name()
        if name.lower() != "source":
            outs.append(_der_output(dss, "Vsource", name, "generator", vpu))
        i = dss.Vsources.Next()
    if net is not None and not net.external_grids:
        slack = next((g for g in net.generators if g.bus_type == "slack"), None)
        if slack is not None and not any(o.name.lower() == slack.name.lower() for o in outs):
            p, q = dss.Circuit.TotalPower()
            bus = slack.bus.lower()
            v_phases = vpu.get(bus, {})
            vmean = round(sum(v_phases.values()) / len(v_phases), 4) if v_phases else None
            outs.append(
                DEROutput(
                    name=slack.name,
                    kind="generator",
                    bus=bus,
                    p_kw=round(-p, 2),
                    q_kvar=round(-q, 2),
                    v_mean_pu=vmean,
                )
            )
    return outs


def _der_output(dss, cls, name, default_kind, vpu, der_kind=None) -> DEROutput:
    dss.Circuit.SetActiveElement(f"{cls}.{name}")
    busspec = dss.CktElement.BusNames()[0]
    bus = busspec.split(".")[0].lower()
    nc = dss.CktElement.NumConductors()
    pw = dss.CktElement.Powers()
    p = round(-sum(pw[0 : 2 * nc : 2]), 2)
    q = round(-sum(pw[1 : 2 * nc : 2]), 2)
    kind = (der_kind or {}).get(name.lower(), default_kind)
    v_phases = vpu.get(bus, {})
    vmean = round(sum(v_phases.values()) / len(v_phases), 4) if v_phases else None
    return DEROutput(name=name, kind=kind, bus=bus, p_kw=p, q_kvar=q, v_mean_pu=vmean)


def extract_line_flows(dss, net: Optional[InlineNetwork] = None) -> list[BranchFlow]:
    flows: list[BranchFlow] = []
    i = dss.Lines.First()
    while i:
        name = dss.Lines.Name()
        if name.startswith("sw_"):
            i = dss.Lines.Next()
            continue
        powers = dss.CktElement.Powers()
        nc = dss.CktElement.NumConductors()
        p_to = sum(powers[2 * nc : 4 * nc : 2]) if len(powers) >= 4 * nc else None
        q_to = sum(powers[2 * nc + 1 : 4 * nc : 2]) if len(powers) >= 4 * nc else None
        flows.append(
            BranchFlow(
                name=f"Line.{name}",
                bus_from=dss.Lines.Bus1().split(".")[0],
                bus_to=dss.Lines.Bus2().split(".")[0],
                p_kw=round(sum(powers[0 : 2 * nc : 2]), 4),
                q_kvar=round(sum(powers[1 : 2 * nc : 2]), 4),
                p_to_kw=round(p_to, 4) if p_to is not None else None,
                q_to_kvar=round(q_to, 4) if q_to is not None else None,
                losses_kw=round(sum(powers[0 : 2 * nc : 2]) + p_to, 4) if p_to is not None else None,
            )
        )
        i = dss.Lines.Next()
    represented = {flow.name.lower() for flow in flows}
    if net is not None:
        for line in net.lines:
            if line.name in net.open_elements and f"line.{line.name}".lower() not in represented:
                flows.append(
                    BranchFlow(
                        name=f"Line.{line.name}",
                        bus_from=line.from_bus,
                        bus_to=line.to_bus,
                        p_kw=0.0,
                        q_kvar=0.0,
                        p_to_kw=0.0,
                        q_to_kvar=0.0,
                        losses_kw=0.0,
                        loading_pct=0.0,
                    )
                )
    return flows


def extract_transformer_flows(dss) -> list[BranchFlow]:
    """Extract solver-returned two-winding transformer terminal powers."""
    flows: list[BranchFlow] = []
    i = dss.Transformers.First()
    while i:
        name = dss.Transformers.Name()
        dss.Circuit.SetActiveElement(f"Transformer.{name}")
        buses = dss.CktElement.BusNames()
        powers = dss.CktElement.Powers()
        nc = dss.CktElement.NumConductors()
        p_to = sum(powers[2 * nc : 4 * nc : 2]) if len(powers) >= 4 * nc else None
        q_to = sum(powers[2 * nc + 1 : 4 * nc : 2]) if len(powers) >= 4 * nc else None
        flows.append(
            BranchFlow(
                name=f"Transformer.{name}",
                bus_from=buses[0].split(".")[0],
                bus_to=buses[1].split(".")[0],
                p_kw=round(sum(powers[0 : 2 * nc : 2]), 4),
                q_kvar=round(sum(powers[1 : 2 * nc : 2]), 4),
                p_to_kw=round(p_to, 4) if p_to is not None else None,
                q_to_kvar=round(q_to, 4) if q_to is not None else None,
                losses_kw=round(sum(powers[0 : 2 * nc : 2]) + p_to, 4) if p_to is not None else None,
            )
        )
        i = dss.Transformers.Next()
    return flows
