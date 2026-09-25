"""OpenDSS adapter â€” SLD construction."""

from __future__ import annotations

from typing import Optional

from cept.schema.case import Case, Experiment, InlineNetwork
from cept.schema.sld import (
    SLDEdge,
    SLDEvent,
    SLDGen,
    SLDLoad,
    SLDModel,
    SLDNode,
    SLDShunt,
)

from cept.adapters.opendss.utils import LEN_UNITS
from cept.adapters.opendss.load_flow import bus_phase_voltages


# Vertical distance from a grid attachment bus to its dedicated source node
# (one CEPT layout cell, matching `geometry._SCALE`), so the source icon never
# overlaps the network it feeds.
_GRID_SPACING = 80.0


def _solver_source_buses(dss) -> set[str]:
    """Return buses backed by solver-owned OpenDSS Vsource elements.

    Built-in and file-backed circuits do not carry CEPT ``external_grids``
    metadata, but their solved source is still a real OpenDSS element. SLD
    fidelity therefore follows that element instead of relying on the
    IEEE13-specific ``sourcebus`` name or dropping ``Vsource.source``.
    """
    result: set[str] = set()
    for name in dss.Vsources.AllNames() or []:
        try:
            dss.Circuit.SetActiveElement(f"Vsource.{name}")
            buses = dss.CktElement.BusNames() or []
        except Exception:
            continue
        if not buses:
            continue
        bus = str(buses[0]).split(".", 1)[0].strip().lower()
        if bus:
            result.add(bus)
    return result


def _add_inline_grid_sources(
    dss, case: Case, nodes: dict[str, SLDNode]
) -> list[SLDEdge]:
    """Emit a visible utility connection for inline external grids.

    A grid-tied Case must *draw* its connection. Tagging the attachment bus as
    ``substation`` alone produced a solitary bus-bar with no line when the Case
    has no other branches (single-bus OpenDER cases): the SLD showed no
    connection at all yet still passed fidelity.  Instead, emit a dedicated
    source/substation node per external grid plus an explicit grid edge to the
    attachment bus (T-012 fuller fix).
    """
    inline = case.network.inline if case.network.kind == "inline" else None
    if inline is None or not inline.external_grids:
        return []
    edges: list[SLDEdge] = []
    for grid in inline.external_grids:
        attach = grid.bus.lower().split(".")[0]
        if attach not in nodes:
            continue
        src_id = grid.name or "ExternalGrid"
        if src_id.lower() in nodes:
            src_id = f"{grid.name}_source"
        ax, ay = nodes[attach].x, nodes[attach].y
        # Check horizontal vs vertical chain
        gx, gy = ax, ay
        other_nodes = [n for k, n in nodes.items() if k != attach and n.kind != "substation"]
        if other_nodes:
            avg_other_x = sum(n.x for n in other_nodes) / len(other_nodes)
            if avg_other_x > ax + 20:
                gx = ax - _GRID_SPACING
            elif avg_other_x < ax - 20:
                gx = ax + _GRID_SPACING
            else:
                gy = ay - _GRID_SPACING
        else:
            gy = ay - _GRID_SPACING
        # The attachment bus is a plain bus; the *source* is the substation.
        nodes[attach].kind = "bus"
        nodes[src_id] = SLDNode(
            id=src_id,
            x=round(gx, 2),
            y=round(gy, 2),
            kind="substation",
            kv_base=nodes[attach].kv_base,
            phases=list(nodes[attach].phases),
            v_pu=dict(nodes[attach].v_pu),
            angle_deg=dict(nodes[attach].angle_deg),
        )
        edges.append(
            SLDEdge(
                id=f"GridLink.{grid.name or 'grid'}",
                src=src_id,
                dst=attach,
                kind="line",
                status="closed",
            )
        )
    return edges


def build_sld(
    dss,
    case: Case,
    experiment: Optional[Experiment] = None,
    der_kind: Optional[dict] = None,
    inline_layout: Optional[dict] = None,
) -> SLDModel:
    vpu, ang = bus_phase_voltages(dss)

    # Inline Cases have no OpenDSS-native drawing coordinates. Reuse the
    # deterministic CEPT layout instead of leaving every bus at (0, 0).
    if not inline_layout and case.network.kind == "inline" and case.network.inline is not None:
        from cept.domain.sld.engineering_layout import inline_layout as deterministic_layout

        inline_layout = deterministic_layout(case.network.inline)
    inline_layout = inline_layout or {}
    # The circuit source comes from the inline external grid (Vsource). Tag the
    # point-of-attachment bus as the substation so a grid-tied case still draws
    # its utility connection; the historical "sourcebus"/"650" names are a
    # specific-feeder convention and miss inline cases where the grid sits
    # directly on the bus.
    source_buses: set[str] = set()
    if case.network.kind == "inline" and case.network.inline is not None:
        source_buses = {eg.bus.lower().split(".")[0] for eg in (case.network.inline.external_grids or [])}
    solver_source_buses = _solver_source_buses(dss) if case.network.kind != "inline" else set()
    substation_buses = {"sourcebus", "650", *source_buses, *solver_source_buses}
    nodes: dict[str, SLDNode] = {}
    for bus in dss.Circuit.AllBusNames():
        dss.Circuit.SetActiveBus(bus)
        b = bus.lower()
        if b in inline_layout:
            x, y = inline_layout[b]
        else:
            x, y = dss.Bus.X(), dss.Bus.Y()
        nodes[b] = SLDNode(
            id=bus,
            x=x,
            y=y,
            kind="substation" if b in substation_buses else "bus",
            kv_base=round(dss.Bus.kVBase(), 4),
            phases=sorted(vpu.get(bus, {})),
            v_pu=vpu.get(bus, {}),
            angle_deg=ang.get(bus, {}),
        )

    grid_edges = _add_inline_grid_sources(dss, case, nodes)

    attach_loads(dss, nodes)
    attach_capacitors(dss, nodes)
    if case.network.kind != "inline":
        attach_solver_sources(dss, nodes)
    attach_generators(dss, nodes, case.network.inline, der_kind)

    edges = [*grid_edges, *extract_edges(dss)]

    if experiment:
        mark_after_events(dss, nodes, edges, experiment)

    losses = dss.Circuit.Losses()
    return SLDModel(
        title="Single-Line Diagram",
        nodes=list(nodes.values()),
        edges=edges,
        total_loss_kw=round(losses[0] / 1000.0, 4),
        v_min_pu=case.standards.v_min_pu,
        v_max_pu=case.standards.v_max_pu,
    )


def attach_loads(dss, nodes: dict[str, SLDNode]) -> None:
    i = dss.Loads.First()
    while i:
        name = dss.Loads.Name()
        dss.Circuit.SetActiveElement(f"Load.{name}")
        enabled = dss.CktElement.Enabled()
        busspec = dss.CktElement.BusNames()[0]
        bus = busspec.split(".")[0].lower()
        phases = [int(p) for p in busspec.split(".")[1:] if p.isdigit()] or [1, 2, 3]
        if bus in nodes:
            nodes[bus].loads.append(
                SLDLoad(
                    name=name,
                    kw=round(dss.Loads.kW(), 2),
                    kvar=round(dss.Loads.kvar(), 2),
                    phases=phases,
                    shed=not enabled,
                )
            )
        i = dss.Loads.Next()


def attach_capacitors(dss, nodes: dict[str, SLDNode]) -> None:
    i = dss.Capacitors.First()
    while i:
        name = dss.Capacitors.Name()
        dss.Circuit.SetActiveElement(f"Capacitor.{name}")
        busspec = dss.CktElement.BusNames()[0]
        bus = busspec.split(".")[0].lower()
        phases = [int(p) for p in busspec.split(".")[1:] if p.isdigit()] or [1, 2, 3]
        if bus in nodes:
            nodes[bus].shunts.append(
                SLDShunt(
                    name=name,
                    kind="capacitor",
                    kvar=round(dss.Capacitors.kvar(), 2),
                    phases=phases,
                )
            )
        i = dss.Capacitors.Next()


def attach_solver_sources(dss, nodes: dict[str, SLDNode]) -> None:
    """Attach each non-inline OpenDSS Vsource as a visible grid terminal.

    The source symbol is presentation metadata, but its existence, bus,
    phase set, and solved P/Q are read directly from the active solver
    element. Inline CEPT external grids use the explicit ``GridLink`` path and
    are intentionally handled separately to avoid a duplicate source glyph.
    """
    for name in dss.Vsources.AllNames() or []:
        try:
            dss.Circuit.SetActiveElement(f"Vsource.{name}")
            buses = dss.CktElement.BusNames() or []
            if not buses:
                continue
            busspec = str(buses[0])
            bus = busspec.split(".", 1)[0].strip().lower()
            if bus not in nodes:
                continue
            phases = [int(p) for p in busspec.split(".")[1:] if p.isdigit()]
            if not phases:
                phases = list(nodes[bus].phases) or [1, 2, 3]
            enabled = bool(dss.CktElement.Enabled())
            powers = dss.CktElement.Powers() if enabled else []
            nc = int(dss.CktElement.NumConductors()) if enabled else 0
            kw = round(-sum(powers[0 : 2 * nc : 2]), 2) if nc else 0.0
            kvar = round(-sum(powers[1 : 2 * nc : 2]), 2) if nc else 0.0
        except Exception:
            continue
        if any(g.kind == "grid" and g.name.lower() == str(name).lower() for g in nodes[bus].gens):
            continue
        nodes[bus].gens.append(
            SLDGen(
                name=str(name),
                kind="grid",
                kw=kw,
                kvar=kvar,
                phases=phases,
                tripped=not enabled,
            )
        )


def attach_generators(
    dss, nodes: dict[str, SLDNode], net: Optional[InlineNetwork] = None, der_kind: Optional[dict] = None
) -> None:
    # Iterate by AllNames (not First/Next): a tripped/disabled generator is
    # still part of the solved Case and must be *shown* on the SLD (greyed,
    # zero output), not silently dropped — otherwise the SLD would no longer
    # faithfully depict the Case and device-parity would fail.
    for name in dss.Generators.AllNames() or []:
        attach_one_gen(
            dss, nodes, "Generator", name, default_kind="generator", der_kind=der_kind
        )
    for name in dss.PVsystems.AllNames() or []:
        attach_one_gen(dss, nodes, "PVSystem", name, default_kind="pv", der_kind=der_kind)
    for name in dss.Storages.AllNames() or []:
        # OpenDER/BESS DERs are native OpenDSS Storage elements; they are still
        # part of the solved Case and must appear (as a battery) or the SLD
        # device-parity gate would report the DER missing.
        attach_one_gen(dss, nodes, "Storage", name, default_kind="battery", der_kind=der_kind)
    for full in dss.Circuit.AllElementNames():
        if full.lower().startswith("indmach012."):
            attach_one_gen(
                dss, nodes, "IndMach012", full.split(".", 1)[1], default_kind="indmach", der_kind=der_kind
            )
    i = dss.Vsources.First()
    while i:
        name = dss.Vsources.Name()
        if name.lower() != "source":
            attach_one_gen(dss, nodes, "Vsource", name, default_kind="generator", der_kind=der_kind)
        i = dss.Vsources.Next()
    if net is not None and not net.external_grids:
        slack = next((g for g in net.generators if g.bus_type == "slack"), None)
        if slack is not None:
            bus = slack.bus.lower()
            if bus in nodes and not any(g.name.lower() == slack.name.lower() for g in nodes[bus].gens):
                p, q = dss.Circuit.TotalPower()
                nodes[bus].gens.append(
                    SLDGen(
                        name=slack.name,
                        kind="generator",
                        kw=round(-p, 2),
                        kvar=round(-q, 2),
                        phases=[1, 2, 3],
                        tripped=False,
                    )
                )


def attach_one_gen(dss, nodes, cls, name, default_kind, der_kind=None) -> None:
    dss.Circuit.SetActiveElement(f"{cls}.{name}")
    enabled = dss.CktElement.Enabled()
    busspec = dss.CktElement.BusNames()[0]
    bus = busspec.split(".")[0].lower()
    phases = [int(p) for p in busspec.split(".")[1:] if p.isdigit()] or [1, 2, 3]
    powers = dss.CktElement.Powers() if enabled else [0, 0]
    nc = dss.CktElement.NumConductors() if enabled else 1
    if bus in nodes:
        nodes[bus].gens.append(
            SLDGen(
                name=name,
                kind=(der_kind or {}).get(name.lower(), default_kind),
                kw=round(-sum(powers[0 : 2 * nc : 2]), 2),
                kvar=round(-sum(powers[1 : 2 * nc : 2]), 2),
                phases=phases,
                tripped=not enabled,
            )
        )


def extract_edges(dss) -> list[SLDEdge]:
    edges: list[SLDEdge] = []
    i = dss.Lines.First()
    while i:
        name = dss.Lines.Name()
        dss.Circuit.SetActiveElement(f"Line.{name}")
        powers = dss.CktElement.Powers()
        nc = dss.CktElement.NumConductors()
        elosses = dss.CktElement.Losses()
        b1 = dss.Lines.Bus1().split(".")[0].lower()
        b2 = dss.Lines.Bus2().split(".")[0].lower()
        length = dss.Lines.Length()
        is_switch = length <= 0.0011 and dss.Lines.Units() == 0
        edges.append(
            SLDEdge(
                id=f"Line.{name}",
                src=b1,
                dst=b2,
                kind="switch" if is_switch else "line",
                p_kw=round(sum(powers[0 : 2 * nc : 2]), 3),
                q_kvar=round(sum(powers[1 : 2 * nc : 2]), 3),
                losses_kw=round(elosses[0] / 1000.0, 4),
                length=None if is_switch else round(length, 2),
                length_unit=LEN_UNITS.get(dss.Lines.Units(), ""),
                status="open" if not dss.CktElement.Enabled() else "closed",
            )
        )
        i = dss.Lines.Next()
    i = dss.Transformers.First()
    while i:
        name = dss.Transformers.Name()
        dss.Circuit.SetActiveElement(f"Transformer.{name}")
        buses = dss.CktElement.BusNames()
        b1 = buses[0].split(".")[0].lower()
        b2 = buses[1].split(".")[0].lower()
        powers = dss.CktElement.Powers()
        nc = dss.CktElement.NumConductors()
        elosses = dss.CktElement.Losses()
        is_reg = name.lower().startswith("reg")
        edges.append(
            SLDEdge(
                id=f"Transformer.{name}",
                src=b1,
                dst=b2,
                kind="regulator" if is_reg else "transformer",
                p_kw=round(sum(powers[0 : 2 * nc : 2]), 3),
                q_kvar=round(sum(powers[1 : 2 * nc : 2]), 3),
                losses_kw=round(elosses[0] / 1000.0, 4),
                tap=round(dss.Transformers.Tap(), 4),
            )
        )
        i = dss.Transformers.Next()
    return edges


def mark_after_events(dss, nodes, edges, experiment: Experiment) -> None:
    edge_by_id = {e.id.lower(): e for e in edges}
    for a in experiment.actions:
        lbl = a.label or a.kind.replace("_", " ")
        if a.kind == "fault":
            bus = (a.params.get("bus", a.target)).lower()
            if bus in nodes:
                nodes[bus].event = SLDEvent(kind="fault", label=lbl or "FAULT")
        elif a.kind in ("open", "close"):
            key = f"line.{a.target}".lower()
            if key in edge_by_id:
                edge_by_id[key].event = SLDEvent(kind=a.kind, label=lbl)
                edge_by_id[key].status = "open" if a.kind == "open" else "closed"
        elif a.kind == "trip_gen":
            bus = _element_bus(dss, f"Generator.{a.target}")
            if bus and bus in nodes:
                nodes[bus].event = SLDEvent(kind="trip_gen", label=lbl or "GEN TRIP")
                for g in nodes[bus].gens:
                    if g.name.lower() == a.target.lower():
                        g.tripped = True
        elif a.kind == "shed_load":
            bus = _element_bus(dss, f"Load.{a.target}")
            if bus and bus in nodes:
                nodes[bus].event = SLDEvent(kind="shed_load", label=lbl or "LOAD SHED")
        elif a.kind == "set_tap":
            key = f"transformer.{a.target}".lower()
            if key in edge_by_id:
                edge_by_id[key].event = SLDEvent(kind="set_tap", label=lbl or f"tap={a.params.get('tap')}")


def mark_before_events(sld_before: SLDModel, experiment: Experiment) -> None:
    node_by = {n.id.lower(): n for n in sld_before.nodes}
    edge_by = {e.id.lower(): e for e in sld_before.edges}
    for a in experiment.actions:
        tag = SLDEvent(kind="info", label=f"will {a.kind.replace('_', ' ')}")
        if a.kind == "fault":
            bus = (a.params.get("bus", a.target)).lower()
            if bus in node_by:
                node_by[bus].event = tag
        elif a.kind in ("open", "close", "set_tap"):
            pre = "transformer." if a.kind == "set_tap" else "line."
            key = f"{pre}{a.target}".lower()
            if key in edge_by:
                edge_by[key].event = tag


def _element_bus(dss, full_name: str) -> Optional[str]:
    try:
        dss.Circuit.SetActiveElement(full_name)
        names = dss.CktElement.BusNames()
        return names[0].split(".")[0].lower() if names else None
    except Exception:
        return None
