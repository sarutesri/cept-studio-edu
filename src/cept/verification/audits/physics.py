"""Small, solver-artifact-only physics/representation audit.

This is intentionally a gate, not a second solver.  It checks identities and
finite solver-returned quantities that can be proven from one run directory.
It never repairs or derives engineering values.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from cept.schema import Case
from cept.schema.result import StudyResult
from cept.util import sha256_file


def _read(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def audit_run(run_dir: str | Path) -> dict[str, Any]:
    """Audit a completed run and return an artifact-derived PASS/BLOCKED record."""
    run = Path(run_dir).resolve()
    reasons: list[str] = []
    manifest = _read(run / "manifest.json")
    result_payload = _read(run / "results.json")
    validation = _read(run / "validation_report.json")
    case_payload = _read(run / "case.json")
    if manifest is None:
        reasons.append("missing or invalid manifest.json")
    if result_payload is None:
        reasons.append("missing or invalid results.json")
    if case_payload is None:
        reasons.append("missing or invalid case.json")
    if validation is None:
        reasons.append("missing or invalid validation_report.json")
    if reasons:
        return _record(run, reasons, manifest, result_payload)

    try:
        result = StudyResult.model_validate(result_payload)
        case = Case.model_validate(case_payload)
    except Exception as exc:  # pydantic's exact error is useful to the owner
        reasons.append(f"typed artifact validation failed: {type(exc).__name__}: {exc}")
        return _record(run, reasons, manifest, result_payload)

    expected = case.fingerprint()
    identities = {
        "case.json": expected,
        "results.json": result.case_fingerprint,
        "manifest.json": manifest.get("case_fingerprint"),
    }
    if len(set(identities.values())) != 1:
        reasons.append("case fingerprint mismatch across case/results/manifest")
    if validation.get("passed") is not True:
        reasons.append("validation_report.json does not explicitly report passed=true")
    inline = case.network.inline if case.network.kind == "inline" else None
    if inline is not None:
        buses = {bus.name.lower() for bus in inline.buses}
        references: list[tuple[str, str]] = []
        references.extend(
            (f"line {line.name}", bus) for line in inline.lines for bus in (line.from_bus, line.to_bus)
        )
        references.extend(
            (f"transformer {transformer.name}", bus)
            for transformer in inline.transformers
            for bus in (transformer.hv_bus, transformer.lv_bus)
        )
        references.extend((f"load {load.id}", load.bus) for load in inline.loads)
        references.extend((f"generator {generator.name}", generator.bus) for generator in inline.generators)
        references.extend((f"external grid {grid.name}", grid.bus) for grid in inline.external_grids)
        references.extend((f"DER {der.id}", der.bus) for der in case.ders)
        references.extend((f"load overlay {load.id}", load.bus) for load in case.loads)
        if result.fault is not None:
            references.append(("fault result at", result.fault.bus))
        if result.protection is not None:
            references.append(("protection result at", result.protection.fault_bus))
        unknown = [
            f"{label} references unknown bus '{bus}'" for label, bus in references if bus.lower() not in buses
        ]
        reasons.extend(unknown)
        if len(buses) != len(inline.buses):
            reasons.append("inline network contains duplicate bus names")
    if result.load_flow:
        values = [v.v_pu for v in result.load_flow.bus_voltages]
        values += [v.v_angle_deg for v in result.load_flow.bus_voltages]
        values += [f.p_kw for f in result.load_flow.branch_flows]
        values += [f.q_kvar for f in result.load_flow.branch_flows]
        if not values:
            reasons.append("load-flow result has no solver-returned voltage or branch quantities")
        elif not all(_finite(value) for value in values):
            reasons.append("load-flow contains non-finite solver-returned quantities")
        if result.load_flow.converged is not True:
            reasons.append("load-flow solver did not converge")
    if result.fault:
        fault = result.fault
        if not _finite(fault.total_fault_current_a) or fault.total_fault_current_a <= 0:
            reasons.append("fault result has a non-positive or non-finite total fault current")
        if fault.fault_type not in {"3ph", "slg", "ll", "llg"}:
            reasons.append(f"fault result has an unrecognized fault_type '{fault.fault_type}'")
        if fault.currents and not all(_finite(c.i_amp) and _finite(c.i_angle_deg) for c in fault.currents):
            reasons.append("fault result contains non-finite phase currents")
        if fault.bus_voltages_during and not all(_finite(v.v_pu) for v in fault.bus_voltages_during):
            reasons.append("fault result contains non-finite during-fault bus voltages")
        if fault.voltage_factor_applied is not None and not _finite(fault.voltage_factor_applied):
            reasons.append("fault result declares a non-finite IEC 60909 voltage factor")
    if result.harmonics:
        harmonics = result.harmonics
        if harmonics.converged is not True:
            reasons.append("harmonics solver did not converge")
        for bus, thd in harmonics.thd_v_pct_by_bus.items():
            if not _finite(thd) or not 0.0 <= thd <= 100.0:
                reasons.append(f"harmonics THD for bus {bus} is outside [0, 100]%")
        for snapshot in harmonics.snapshots:
            if not all(_finite(v.v_pu) and _finite(v.frequency_hz) for v in snapshot.bus_voltages):
                reasons.append("harmonics snapshot contains non-finite bus voltage/frequency")
    if result.protection:
        protection = result.protection
        if protection.fault_current_a is not None and (
            not _finite(protection.fault_current_a) or protection.fault_current_a <= 0
        ):
            reasons.append("protection result has a non-positive or non-finite fault current")
        for relay in protection.relays:
            if not _finite(relay.current_multiple) or relay.pickup_a <= 0:
                reasons.append(f"relay {relay.name} has non-finite multiple or non-positive pickup")
            if relay.current_multiple <= 1 and relay.status != "no-trip":
                reasons.append(f"relay {relay.name} reports trip below pickup (multiple<=1)")
            if relay.current_multiple > 1 and relay.status != "trip":
                reasons.append(f"relay {relay.name} reports no-trip above pickup (multiple>1)")
            if relay.status == "trip" and (
                relay.trip_time_s is None or not _finite(relay.trip_time_s) or relay.trip_time_s < 0
            ):
                reasons.append(f"relay {relay.name} trips with missing/negative/non-finite time")
    if result.hosting_capacity:
        hosting = result.hosting_capacity
        for item in hosting.items:
            if not _finite(item.hc_kw) or item.hc_kw < 0:
                reasons.append(f"hosting capacity item at bus {item.bus} is non-finite or negative")
            if item.limit not in {"overvoltage", "thermal", "maxed", "none"}:
                reasons.append(
                    f"hosting capacity item at bus {item.bus} has unrecognized limit '{item.limit}'"
                )
    if result.gic:
        gic = result.gic
        if gic.elements and not all(_finite(e.gic_amps) for e in gic.elements):
            reasons.append("GIC result contains non-finite element currents")
        if not _finite(gic.max_gic_a) or not _finite(gic.total_gic_a):
            reasons.append("GIC result has non-finite max/total current")
    if result.emt:
        if result.emt.converged is not True:
            reasons.append("EMT solver did not converge")
        for trace in result.emt.channels:
            if not all(_finite(value) for value in trace.t):
                reasons.append(f"EMT trace {trace.name} has non-finite time values")
            for channel in trace.channels:
                if not all(_finite(value) for value in channel.values):
                    reasons.append(f"EMT channel {channel.name} has non-finite values")
    if result.dynamics:
        if result.dynamics.converged is not True:
            reasons.append("dynamics solver did not converge")
        for trace in result.dynamics.monitors:
            if not all(_finite(value) for value in trace.t):
                reasons.append(f"dynamics trace {trace.name} has non-finite time values")
            for channel in trace.channels:
                if not all(_finite(value) for value in channel.values):
                    reasons.append(f"dynamics channel {channel.name} has non-finite values")
    if result.time_series:
        if result.time_series.converged is not True or not result.time_series.snapshots:
            reasons.append("QSTS solver did not produce converged snapshots")
        for snapshot in result.time_series.snapshots:
            if not all(_finite(v.v_pu) for v in snapshot.bus_voltages):
                reasons.append("QSTS contains non-finite bus voltage")

    opender_records = (result.extra or {}).get("opender") or []
    if opender_records and any(
        not isinstance(item, dict) or item.get("status") != "CO_SIMULATED" for item in opender_records
    ):
        reasons.append("one or more OpenDER records are not CO_SIMULATED")

    if result.sld is not None and result.sld.nodes:
        from cept.reporting.collisions import rendered_collision_check

        collision = rendered_collision_check(result.sld)
        if collision["verdict"] == "blocked":
            reasons.append(
                f"SLD collision gate: {collision['major_count']} major overlap(s) "
                f"({collision['overlap_count']} total) - symbols/labels cover each other "
                "in the rendered single-line diagram"
            )
        from cept.validation.sld_fidelity import sld_fidelity

        fidelity = sld_fidelity(case, result)
        if not fidelity["passed"]:
            details = "; ".join(fidelity["reasons"]) or "FAIL"
            reasons.append(f"SLD fidelity gate: {details}")

    return _record(run, reasons, manifest, result_payload, case_fingerprint=expected)


def _record(
    run: Path,
    reasons: list[str],
    manifest: dict[str, Any] | None,
    result: dict[str, Any] | None,
    *,
    case_fingerprint: str | None = None,
) -> dict[str, Any]:
    paths = {
        name: str(run / name)
        for name in ("case.json", "results.json", "manifest.json", "validation_report.json")
    }
    hashes = {name: sha256_file(run / name) for name in paths if (run / name).is_file()}
    return {
        "schema": "cept-physics-audit-v1",
        "run_dir": str(run),
        "status": "PASS" if not reasons else "BLOCKED",
        "passed": not reasons,
        "case_fingerprint": case_fingerprint or (result or {}).get("case_fingerprint"),
        "study_type": (result or {}).get("study_type"),
        "artifacts": paths,
        "artifact_sha256": hashes,
        "reasons": reasons,
        "scope": "solver-returned quantities and representation identity only; no re-solve or analytic substitution",
    }
