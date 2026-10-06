"""OpenDSS adapter — hosting capacity analysis."""

from __future__ import annotations

from typing import Optional

from cept.schema.case import Case
from cept.schema.result import (
    HostingCapacityItem,
    HostingCapacityResult,
)

from cept.adapters.opendss.network import load_network
from cept.adapters.opendss.der import apply_ders, emit_pv
from cept.adapters.opendss.load_flow import _solve


def regulated_head_buses(dss) -> set[str]:
    """Source + substation/regulator transformer buses."""
    heads = {"sourcebus"}
    i = dss.Transformers.First()
    while i:
        name = dss.Transformers.Name().lower()
        if name.startswith("reg") or name.startswith("sub"):
            dss.Circuit.SetActiveElement(f"Transformer.{dss.Transformers.Name()}")
            for b in dss.CktElement.BusNames():
                heads.add(b.split(".")[0].lower())
        i = dss.Transformers.Next()
    return heads


def highest_node(dss, exclude: Optional[set[str]] = None) -> tuple[float, str]:
    """The highest per-unit node voltage and the node (``bus.phase``) carrying it."""
    exclude = exclude or set()
    names = dss.Circuit.AllNodeNames()
    vmag = dss.Circuit.AllBusMagPu()
    return max(
        (v, n) for n, v in zip(names, vmag) if n.split(".")[0].lower() not in exclude
    )


def overloaded_line(dss) -> Optional[str]:
    """The first line whose current exceeds its NormAmps, or None."""
    i = dss.Lines.First()
    while i:
        na = dss.Lines.NormAmps()
        name = dss.Lines.Name()
        dss.Circuit.SetActiveElement(f"Line.{name}")
        mags = dss.CktElement.CurrentsMagAng()
        nc = dss.CktElement.NumConductors()
        imax = max(mags[0 : 2 * nc : 2]) if mags else 0.0
        if na and imax > na:
            return name
        i = dss.Lines.Next()
    return None


def candidate_buses(dss, phases: int) -> list[str]:
    out = []
    for bus in dss.Circuit.AllBusNames():
        if bus.lower() in ("sourcebus", "650", "rg60"):
            continue
        dss.Circuit.SetActiveBus(bus)
        nodes = [n for n in dss.Bus.Nodes() if 1 <= n <= 3]
        if len(nodes) >= phases and dss.Bus.kVBase() > 0.4:
            out.append(bus)
    return out


def add_test_pv(dss, bus: str, kw: float, phases: int, control: str = "constant_pf", state=None) -> None:
    emit_pv(dss, "cept_hc", bus, kw, phases, control=control, state=state or {})


def freeze_regulators_caps(dss) -> None:
    """Disable only regulator and capacitor controls."""
    for rc in dss.RegControls.AllNames():
        if rc and rc.lower() != "none":
            dss.Text.Command(f"RegControl.{rc}.enabled=no")
    for cc in dss.CapControls.AllNames():
        if cc and cc.lower() != "none":
            dss.Text.Command(f"CapControl.{cc}.enabled=no")


def hc_for_bus(dss, case, bus, vmax, maxkw, criterion, phases, exclude, control="constant_pf", state=None):
    def feasible(kw):
        load_network(dss, case.network, "")
        apply_ders(dss, case, state or {})
        _solve(dss)
        freeze_regulators_caps(dss)
        if kw > 0:
            add_test_pv(dss, bus, kw, phases, control=control, state=state)
        _solve(dss)
        if not dss.Solution.Converged():
            return False, None, None
        vm, node = highest_node(dss, exclude)
        # Which limit is violated, and by which element. Overvoltage is checked
        # first, so under criterion "both" a step that breaks both is attributed
        # to the voltage ceiling; the element is recorded either way.
        if criterion in ("overvoltage", "both") and vm > vmax:
            return False, vm, ("overvoltage", f"node {node}")
        if criterion in ("thermal", "both"):
            line = overloaded_line(dss)
            if line is not None:
                return False, vm, ("thermal", f"line {line}")
        return True, vm, None

    ok_max, vm_max, _ = feasible(maxkw)
    if ok_max:
        return maxkw, "maxed", vm_max, None
    ok0, vm0, binding0 = feasible(0.0)
    if not ok0:
        # Already beyond the limit with no PV: the capacity is zero and the
        # element that is out of limit is the reason.
        limit, element = binding0 or ("none", None)
        return 0.0, limit, vm0, element
    lo, hi = 0.0, maxkw
    for _ in range(14):
        mid = (lo + hi) / 2
        ok, _vm, _binding = feasible(mid)
        if ok:
            lo = mid
        else:
            hi = mid
    _ok, vm, _ = feasible(lo)
    # The limit is named by the first infeasible step above the answer: that is
    # the element a little more PV would push out of bounds.
    _bad, _vm_hi, binding = feasible(hi)
    limit, element = binding or ("none", None)
    return lo, limit, vm, element


def run_hosting_capacity(dss, case: Case, state: dict):
    from cept.adapters.opendss.sld import build_sld, SLDEvent

    opt = case.study.options
    vmax = float(opt.get("v_max", case.standards.v_max_pu))
    maxkw = float(opt.get("max_kw", 5000.0))
    criterion = opt.get("criterion", "overvoltage")
    phases = int(opt.get("phases", 3))
    control = opt.get("der_control", "constant_pf")

    load_network(dss, case.network, "")
    apply_ders(dss, case, state)
    _solve(dss)
    exclude = regulated_head_buses(dss)
    base_vmax = highest_node(dss, exclude)[0]

    buses = opt.get("buses") or candidate_buses(dss, phases)
    items = []
    for bus in buses:
        hc, limit, vhc, element = hc_for_bus(
            dss, case, bus, vmax, maxkw, criterion, phases, exclude, control, state
        )
        items.append(
            HostingCapacityItem(
                bus=bus,
                hc_kw=round(hc, 1),
                limit=limit,
                v_at_hc=round(vhc, 4) if vhc else None,
                limited_by=element,
            )
        )

    res = HostingCapacityResult(
        criterion=criterion,
        v_max_pu=vmax,
        max_search_kw=maxkw,
        baseline_v_max_pu=round(base_vmax, 4),
        items=items,
    )

    load_network(dss, case.network, "")
    apply_ders(dss, case, state)
    _solve(dss)
    sld = build_sld(dss, case, der_kind=state.get("_der_kind"))
    hc_by = {i.bus.lower(): i for i in items}
    for n in sld.nodes:
        it = hc_by.get(n.id.lower())
        if it:
            n.event = SLDEvent(kind="info", label=f"HC {it.hc_kw:.0f} kW")
    return res, sld
