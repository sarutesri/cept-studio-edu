"""OpenDSS adapter — quasi-static time series.

The QSTS profile contract is canonical-asset-first for inline Cases. A profile
that targets ``Load.<case-id>`` therefore drives every OpenDSS phase-split load
object emitted for that one Case load. This is essential for unbalanced feeders:
PowerFactory keeps one canonical load object while OpenDSS may need
``Load.<id>_1``/``_2``/``_3`` to preserve phase placement.

Native OpenDSS element names remain accepted for backward compatibility. Native
baselines are read only after an initial solve so terminal powers are not stale
or zero merely because the circuit has not been solved yet.
"""

from __future__ import annotations

from typing import Iterable, Optional

from cept.schema.case import Case
from cept.schema.result import (
    TimeSeriesResult,
    TimeSeriesSnapshot,
)

from cept.adapters.opendss.network import load_network
from cept.adapters.opendss.der import apply_ders, apply_opender_snapshot
from cept.adapters.opendss.load_flow import _solve, extract_load_flow
from cept.adapters.opendss.sld import build_sld
from cept.adapters.opendss.utils import dss_safe_label


def _load_kvar(load) -> float:
    return round(
        load.q_kvar if load.q_kvar is not None else load.kw * ((1 - load.pf**2) ** 0.5) / load.pf,
        4,
    )


def _canonical_load_parts(case: Case, target: str) -> list[tuple[str, float, float]]:
    """Return native OpenDSS load names/setpoints for one canonical Case load.

    The naming and P/Q split intentionally mirror ``network.load_commands``.
    Keeping this derivation source-declared avoids using pre-solve terminal power
    as an implicit model input and lets one canonical profile drive a split
    single-/two-phase OpenDSS representation.
    """
    inline = case.network.inline
    if inline is None:
        return []
    raw = target.split(".", 1)[1] if "." in target and target.split(".", 1)[0].casefold() == "load" else target
    load = next((item for item in inline.loads if item.id.casefold() == raw.casefold()), None)
    if load is None:
        return []

    kvar = _load_kvar(load)
    nodes = load.phase_nodes
    if not nodes or (len(nodes) == 3 and load.connection == "wye" and not load.kw_per_phase):
        return [(dss_safe_label(load.id), float(load.kw), float(kvar))]

    kw_split = load.kw_per_phase or [load.kw / len(nodes)] * len(nodes)
    kvar_split = load.kvar_per_phase or [kvar / len(nodes)] * len(nodes)
    if load.connection == "delta" and len(nodes) == 3:
        return [(dss_safe_label(load.id), float(sum(kw_split)), float(sum(kvar_split)))]

    parts: list[tuple[str, float, float]] = []
    for index, node in enumerate(nodes):
        p = float(kw_split[index])
        q = float(kvar_split[index])
        if p == 0.0 and q == 0.0:
            continue
        name = load.id if len(nodes) == 1 else f"{load.id}_{node}"
        parts.append((dss_safe_label(name), p, q))
    return parts


def _native_power_baseline(dss, full: str, cls: str) -> tuple[float, float]:
    dss.Circuit.SetActiveElement(full)
    powers = dss.CktElement.Powers()
    nc = dss.CktElement.NumConductors()
    raw_p = sum(powers[0 : 2 * nc : 2])
    raw_q = sum(powers[1 : 2 * nc : 2])
    if cls.casefold() == "load":
        return abs(float(raw_p)), abs(float(raw_q))
    return -float(raw_p), -float(raw_q)


def setup_qsts_profiles(dss, case: Case):
    spec = case.qsts
    assert spec is not None
    available = {name.lower() for name in (dss.Circuit.AllElementNames() or [])}
    bindings = []
    seen: set[str] = set()
    for profile in spec.profiles:
        for target in profile.targets:
            candidates = (
                [target]
                if "." in target
                else [
                    f"Load.{target}",
                    f"PVSystem.{target}",
                    f"Generator.{target}",
                    f"Storage.{target}",
                ]
            )
            full = next((c for c in candidates if c.lower() in available), None)
            if full is not None:
                cls, name = full.split(".", 1)
                key = full.casefold()
                if key in seen:
                    raise ValueError(
                        f"qsts target '{target}' is driven by more than one profile; "
                        "combine source multipliers before the run"
                    )
                seen.add(key)
                base_p, base_q = _native_power_baseline(dss, full, cls)
                bindings.append((profile, cls, name, base_p, base_q))
                continue

            # Inline unbalanced loads may be represented as several native
            # OpenDSS objects even though the engine-neutral Case has one load.
            # Resolve the canonical id to the exact emitted phase objects and
            # preserve the declared per-phase P/Q split.
            load_parts = _canonical_load_parts(case, target)
            if load_parts:
                missing = [name for name, _p, _q in load_parts if f"load.{name}".casefold() not in available]
                if missing:
                    raise ValueError(
                        f"qsts profile '{profile.name}' canonical load target '{target}' "
                        "does not match emitted OpenDSS phase object(s): " + ", ".join(missing)
                    )
                for name, base_p, base_q in load_parts:
                    key = f"load.{name}".casefold()
                    if key in seen:
                        raise ValueError(
                            f"qsts target '{target}' resolves to Load.{name}, which is driven by more than one profile; "
                            "combine source multipliers before the run"
                        )
                    seen.add(key)
                    bindings.append((profile, "Load", name, base_p, base_q))
                continue

            raise ValueError(
                f"qsts profile '{profile.name}' target '{target}' "
                "does not name an existing OpenDSS element or canonical inline load"
            )
    return bindings


def set_qsts_profile_step(dss, bindings, step: int) -> None:
    for profile, cls, name, base_p, base_q in bindings:
        mult = float(profile.values[step])
        if cls.lower() == "load":
            dss.Text.Command(f"Edit Load.{name} kW={base_p * mult:.12g} kvar={base_q * mult:.12g}")
        elif cls.lower() == "generator":
            dss.Text.Command(f"Edit Generator.{name} kW={base_p * mult:.12g}")
        elif cls.lower() == "pvsystem":
            dss.Text.Command(f"Edit PVSystem.{name} irradiance={mult:.12g}")
        elif cls.lower() == "storage":
            dss.Text.Command(f"Edit Storage.{name} kW={base_p * mult:.12g}")
        else:
            raise ValueError(
                f"qsts target class '{cls}' is not supported; use Load, Generator, PVSystem, or Storage"
            )


def run_qsts(dss, case: Case, work_dir: str, state: dict, extra_commands: Optional[Iterable[str]] = None):
    spec = case.qsts
    assert spec is not None
    load_network(dss, case.network, work_dir)
    apply_ders(dss, case, state)
    for cmd in extra_commands or ():
        dss.Text.Command(cmd)

    # Establish a real native operating point before reading terminal powers
    # for backward-compatible native-element targets. Canonical split loads use
    # the source-declared Case P/Q values above and do not depend on this solve.
    _solve(dss)
    bindings = setup_qsts_profiles(dss, case)
    dss.Text.Command(f"Set mode=daily stepsize={spec.stepsize_s / 3600.0:.12g} number=1 controlmode=time")
    snapshots: list[TimeSeriesSnapshot] = []
    state["_opender_qsts_exchanges"] = []
    for idx in range(round(spec.duration_s / spec.stepsize_s)):
        set_qsts_profile_step(dss, bindings, idx)
        _solve(dss)
        lf = extract_load_flow(dss, case.network.inline)
        overrides: dict[str, float] = {}
        for profile, cls, name, base_p, _base_q in bindings:
            if cls.lower() not in {"pvsystem", "storage"}:
                continue
            for der in case.ders:
                if der.id.lower() != name.lower() or der.opender is None:
                    continue
                declared = der.kw if der.kw is not None else der.opender.nominal_power_kw
                overrides[der.id] = (base_p or declared) * float(profile.values[idx])
        exchanges = apply_opender_snapshot(
            dss,
            case,
            lf,
            work_dir,
            state,
            power_overrides=overrides,
            t_s=round((idx + 1) * spec.stepsize_s, 9),
        )
        if exchanges:
            _solve(dss)
            lf = extract_load_flow(dss, case.network.inline)
        if exchanges or any(der.opender and der.opender.mode == "qsts" for der in case.ders):
            state["_opender_qsts_exchanges"].append(
                {
                    "t_s": round((idx + 1) * spec.stepsize_s, 9),
                    "exchanges": exchanges,
                }
            )
        values = [v.v_pu for v in lf.bus_voltages]
        snapshots.append(
            TimeSeriesSnapshot(
                t_s=round((idx + 1) * spec.stepsize_s, 9),
                converged=lf.converged,
                min_voltage_pu=round(min(values), 6) if values else None,
                max_voltage_pu=round(max(values), 6) if values else None,
                total_load_kw=lf.total_load_kw,
                bus_voltages=lf.bus_voltages,
            )
        )
    all_min = [s.min_voltage_pu for s in snapshots if s.min_voltage_pu is not None]
    all_max = [s.max_voltage_pu for s in snapshots if s.max_voltage_pu is not None]
    violations = sum(
        1
        for s in snapshots
        for v in s.bus_voltages
        if v.v_pu < case.standards.v_min_pu or v.v_pu > case.standards.v_max_pu
    )
    result = TimeSeriesResult(
        converged=all(s.converged for s in snapshots),
        duration_s=spec.duration_s,
        stepsize_s=spec.stepsize_s,
        snapshots=snapshots,
        min_voltage_pu=min(all_min) if all_min else None,
        max_voltage_pu=max(all_max) if all_max else None,
        violation_count=violations,
    )
    return result, build_sld(dss, case, der_kind=state.get("_der_kind"))
