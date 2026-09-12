"""OpenDSS adapter — harmonic frequency sweep."""

from __future__ import annotations

from cept.schema.case import Case
from cept.schema.result import (
    HarmonicBusVoltage,
    HarmonicSnapshot,
    HarmonicsResult,
)

from cept.adapters.opendss.network import load_network
from cept.adapters.opendss.der import apply_ders
from cept.adapters.opendss.load_flow import _solve, bus_phase_voltages
from cept.adapters.opendss.sld import build_sld


def run_harmonics(dss, case: Case, state: dict):
    spec = case.harmonics
    assert spec is not None
    load_network(dss, case.network, "")
    apply_ders(dss, case, state)
    available = {name.lower() for name in (dss.Circuit.AllElementNames() or [])}
    orders = set()
    for idx, injection in enumerate(spec.injections):
        target = injection.target
        candidates = [target] if "." in target else [f"Generator.{target}", f"PVSystem.{target}"]
        full = next((c for c in candidates if c.lower() in available), None)
        if full is None:
            raise ValueError(
                f"harmonic injection target '{target}' does not name an existing Generator or PVSystem"
            )
        hs = sorted(injection.magnitudes_pct)
        orders.update(hs)
        mags = " ".join(f"{injection.magnitudes_pct[h]:.12g}" for h in hs)
        angles = " ".join(f"{injection.angles_deg.get(h, 0.0):.12g}" for h in hs)
        dss.Text.Command(
            f"New Spectrum.cept_h_{idx} "
            f"Harmonic=({' '.join(str(h) for h in hs)}) "
            f"%Mag=({mags}) Angle=({angles})"
        )
        dss.Text.Command(f"Edit {full} spectrum=cept_h_{idx}")
    dss.Text.Command("Set mode=snapshot")
    _solve(dss)
    snapshots: list[HarmonicSnapshot] = []
    for harmonic in sorted(orders):
        freq = spec.fundamental_hz * harmonic
        dss.Text.Command(f"Set mode=harmonics frequency={freq:.12g}")
        _solve(dss)
        vpu, _angles = bus_phase_voltages(dss)
        points = [
            HarmonicBusVoltage(
                bus=bus, phase=phase, harmonic=harmonic, frequency_hz=freq, v_pu=round(value, 8)
            )
            for bus, phases in vpu.items()
            for phase, value in phases.items()
        ]
        snapshots.append(
            HarmonicSnapshot(
                harmonic=harmonic,
                frequency_hz=freq,
                converged=bool(dss.Solution.Converged()),
                bus_voltages=points,
            )
        )
    by_bus: dict[str, list[float]] = {}
    fundamental = next((s for s in snapshots if s.harmonic == 1), None)
    if fundamental:
        base = {p.bus.lower(): p.v_pu for p in fundamental.bus_voltages}
        for snap in snapshots:
            if snap.harmonic == 1:
                continue
            for p in snap.bus_voltages:
                by_bus.setdefault(p.bus.lower(), []).append(p.v_pu**2)
        thd = {
            bus: round((sum(values) ** 0.5) / base[bus] * 100.0, 6)
            for bus, values in by_bus.items()
            if bus in base and base[bus] > 0
        }
    else:
        thd = {}
    result = HarmonicsResult(
        converged=all(s.converged for s in snapshots),
        fundamental_hz=spec.fundamental_hz,
        snapshots=snapshots,
        thd_v_pct_by_bus=thd,
    )
    return result, build_sld(dss, case, der_kind=state.get("_der_kind"))
