"""OpenDSS adapter — GIC (geomagnetically induced current)."""

from __future__ import annotations

from cept.schema.case import Case
from cept.schema.result import (
    GICElementFlow,
    GICResult,
)

from cept.adapters.opendss.network import load_network
from cept.adapters.opendss.load_flow import _solve
from cept.adapters.opendss.sld import build_sld


def run_gic(dss, case: Case, state: dict):
    load_network(dss, case.network, "")
    opt = case.study.options
    ef = opt.get("e_field_v_per_km")
    if ef is not None:
        for name in dss.Circuit.AllElementNames():
            if name.lower().startswith("gicline"):
                n = name.split(".", 1)[1]
                dss.Text.Command(f"GICLine.{n}.EE={opt.get('ee', ef)} EN={opt.get('en', 0.0)}")
    freq = float(opt.get("frequency", 0.1))
    dss.Text.Command(f"Set frequency={freq}")
    _solve(dss)

    elements: list[GICElementFlow] = []
    max_g = 0.0
    total_g = 0.0
    n_tx = n_ln = 0
    for name in dss.Circuit.AllElementNames():
        low = name.lower()
        if low.startswith("gictransformer") or low.startswith("gicline"):
            dss.Circuit.SetActiveElement(name)
            current_mag = dss.CktElement.CurrentsMagAng()
            imag = current_mag[0] if current_mag else 0.0
            buses = dss.CktElement.BusNames()
            kind = "transformer" if "transformer" in low else "line"
            if kind == "transformer":
                n_tx += 1
            else:
                n_ln += 1
            elements.append(
                GICElementFlow(
                    name=name,
                    kind=kind,
                    bus_h=buses[0].split(".")[0] if buses else "",
                    gic_amps=round(imag, 3),
                    gic_amps_per_phase=round(imag / 3.0, 3),
                )
            )
            max_g = max(max_g, imag)
            total_g += imag
    elements.sort(key=lambda e: e.gic_amps, reverse=True)
    res = GICResult(
        elements=elements,
        max_gic_a=round(max_g, 3),
        total_gic_a=round(total_g, 3),
        e_field_v_per_km=ef,
        n_transformers=n_tx,
        n_lines=n_ln,
    )
    sld = build_sld(dss, case, der_kind=state.get("_der_kind"))
    return res, sld
