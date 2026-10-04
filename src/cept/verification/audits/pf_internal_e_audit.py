"""Audit PowerFactory Classical internal-emf and reference-frequency semantics."""

from __future__ import annotations

import cmath
import math
import statistics
from typing import Any


SCHEMA = "cept-pf-internal-e-audit-v1"
_INTERVALS = ("pre_fault", "fault_boundary", "fault", "clear_open_boundary", "post_fault")


def _event_time(results: dict[str, Any], kind: str) -> float | None:
    dynamics = results.get("dynamics")
    events = dynamics.get("events", []) if isinstance(dynamics, dict) else []
    for event in events or []:
        if not isinstance(event, dict) or str(event.get("kind", "")).lower() != kind:
            continue
        value = event.get("t")
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            return float(value)
    return None


def _find_trace(results: dict[str, Any], generator: str) -> dict[str, Any]:
    dynamics = results.get("dynamics")
    monitors = dynamics.get("monitors", []) if isinstance(dynamics, dict) else []
    traces = [trace for trace in monitors or [] if isinstance(trace, dict)]
    ordered = [trace for trace in traces if trace.get("name") == generator]
    ordered.extend(trace for trace in traces if trace not in ordered)
    for trace in ordered:
        if isinstance(trace.get("t"), list) and trace.get("t"):
            return trace
    raise ValueError(f"PowerFactory results have no monitor trace for {generator!r}")


def _find_channel(trace: dict[str, Any], names: tuple[str, ...]) -> tuple[list[float], str, str]:
    channels = trace.get("channels", []) or []
    for channel in channels:
        if not isinstance(channel, dict):
            continue
        if channel.get("name") not in names and channel.get("source_channel") not in names:
            continue
        values = channel.get("values")
        times = trace.get("t")
        if not isinstance(values, list) or not isinstance(times, list) or len(values) != len(times):
            continue
        converted: list[float] = []
        for value in values:
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError(f"PowerFactory channel {channel.get('source_channel')!r} contains a non-finite value")
            converted.append(float(value))
        return converted, str(channel.get("source_channel") or channel.get("name") or ""), str(
            channel.get("unit") or ""
        )
    raise ValueError(f"PowerFactory trace {trace.get('name')!r} is missing channels {names!r}")


def _optional_channel(trace: dict[str, Any], names: tuple[str, ...]) -> tuple[list[float] | None, str | None, str | None]:
    try:
        values, source, unit = _find_channel(trace, names)
    except ValueError:
        return None, None, None
    return values, source, unit


def _step(times: list[float]) -> float:
    deltas = sorted(right - left for left, right in zip(times, times[1:]) if right > left)
    return deltas[len(deltas) // 2] if deltas else 0.001


def _wrap_deg(value: float) -> float:
    return (float(value) + 180.0) % 360.0 - 180.0


def _derivative(times: list[float], values: list[float]) -> list[float]:
    result: list[float] = []
    for index in range(len(values)):
        if index == 0:
            left, right = index, min(index + 1, len(values) - 1)
        elif index == len(values) - 1:
            left, right = index - 1, index
        else:
            left, right = index - 1, index + 1
        result.append((values[right] - values[left]) / max(times[right] - times[left], 1.0e-12))
    return result


def _summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"status": "unavailable", "sample_count": 0}
    finite = [float(value) for value in values if math.isfinite(float(value))]
    if not finite:
        return {"status": "unavailable", "sample_count": 0}
    return {
        "status": "observed",
        "sample_count": len(finite),
        "minimum": min(finite),
        "maximum": max(finite),
        "span": max(finite) - min(finite),
        "mean": statistics.fmean(finite),
        "maximum_absolute": max(abs(value) for value in finite),
    }


def _interval(time: float, *, fault: float, clear: float, tolerance: float) -> str:
    if abs(time - fault) <= tolerance:
        return "fault_boundary"
    if abs(time - clear) <= tolerance:
        return "clear_open_boundary"
    if time < fault:
        return "pre_fault"
    if time < clear:
        return "fault"
    return "post_fault"


def _runtime_value(results: dict[str, Any], generator: str, name: str) -> float | None:
    dynamics = results.get("dynamics")
    initialization = dynamics.get("initialization") if isinstance(dynamics, dict) else None
    readback = initialization.get("runtime_model_readback", []) if isinstance(initialization, dict) else []
    for row in readback or []:
        if not isinstance(row, dict) or row.get("name") != generator:
            continue
        for section_name in ("type_parameter_audit", "calculation_parameter_audit"):
            section = row.get(section_name)
            item = section.get(name) if isinstance(section, dict) else None
            value = item.get("readback") if isinstance(item, dict) else None
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                return float(value)
    return None


def _case_machine(case_payload: dict[str, Any], generator: str) -> dict[str, Any]:
    inline = ((case_payload.get("network") or {}).get("inline") or {})
    for row in inline.get("generators", []) or []:
        if isinstance(row, dict) and row.get("name") == generator:
            return row
    raise ValueError(f"case has no generator {generator!r}")


def _bus_frequency_pu(results: dict[str, Any], frequency_hz: float) -> list[float] | None:
    dynamics = results.get("dynamics")
    monitors = dynamics.get("monitors", []) if isinstance(dynamics, dict) else []
    for trace in monitors or []:
        if not isinstance(trace, dict) or str(trace.get("name", "")).lower() != "sourcebus":
            continue
        values, _source, _unit = _optional_channel(trace, ("bus_frequency", "m:fehz"))
        if values is not None:
            return [value / max(frequency_hz, 1.0e-12) for value in values]
    return None


def _sample_indices(times: list[float], speed: list[float], *, clear: float, step: float) -> list[dict[str, Any]]:
    requested = [("pre_fault", 0.9), ("fault", 1.02), ("clear_open", 1.061)]
    candidates: list[dict[str, Any]] = []
    for index in range(1, len(speed) - 1):
        if times[index] < clear + 0.02:
            continue
        deviation = speed[index] - 1.0
        if (deviation >= speed[index - 1] - 1.0 and deviation > speed[index + 1] - 1.0) or (
            deviation <= speed[index - 1] - 1.0 and deviation < speed[index + 1] - 1.0
        ):
            if not candidates or times[index] - float(candidates[-1]["time_s"]) >= max(0.08, 80.0 * step):
                candidates.append({"time_s": times[index], "index": index, "deviation_pu": deviation})
    for label, target in requested:
        index = min(range(len(times)), key=lambda item: abs(times[item] - target))
        candidates.append({"label": label, "time_s": times[index], "index": index, "deviation_pu": speed[index] - 1.0})
    for number, item in enumerate(candidates[:2], 1):
        item["label"] = f"post_fault_extremum_{number}"
    return candidates


def audit_powerfactory_internal_emf(
    powerfactory_results: dict[str, Any],
    case_payload: dict[str, Any],
    *,
    generator: str = "G1",
) -> dict[str, Any]:
    """Reconstruct PF Classical E from raw solver channels without modifying a trace."""

    try:
        trace = _find_trace(powerfactory_results, generator)
        times = [float(value) for value in trace["t"]]
        if len(times) < 3 or any(not math.isfinite(value) for value in times):
            raise ValueError("PowerFactory generator trace has fewer than three finite timestamps")
        terminal_real, terminal_real_source, terminal_real_unit = _find_channel(
            trace, ("terminal_voltage_real", "m:utr")
        )
        terminal_imag, terminal_imag_source, terminal_imag_unit = _find_channel(
            trace, ("terminal_voltage_imag", "m:uti")
        )
        current_real, current_real_source, current_real_unit = _find_channel(
            trace, ("positive_sequence_current_real", "m:cur1r")
        )
        current_imag, current_imag_source, current_imag_unit = _find_channel(
            trace, ("positive_sequence_current_imag", "m:cur1i")
        )
        speed, speed_source, speed_unit = _find_channel(trace, ("speed", "m:xspeed"))
        # Prefer the explicitly converted radian channel.  The raw ``m:phi``
        # channel is also present in the PF receipt, but it is in degrees;
        # matching by source first would silently treat those degrees as
        # radians and invalidate every phi/fref diagnostic.
        phi_values, phi_source, phi_unit = _optional_channel(trace, ("angle_network_rad",))
        if phi_values is None:
            phi_deg, phi_source, phi_unit = _find_channel(trace, ("angle_network", "m:phi"))
            phi_values = [math.radians(value) for value in phi_deg]
            phi_unit = "rad-derived-from-deg"
        xphi_values, xphi_source, xphi_unit = _optional_channel(trace, ("angle_network_xphi_rad",))
        firot_values, firot_source, firot_unit = _optional_channel(trace, ("angle_network_firot",))
        sve_values, sve_source, sve_unit = _optional_channel(trace, ("excitation_voltage", "s:ve"))
        mve_values, mve_source, mve_unit = _optional_channel(trace, ("pf_internal_voltage", "m:ve"))
        fref_values, fref_source, fref_unit = _optional_channel(trace, ("reference_frequency", "s:fref"))
        freflocal_values, freflocal_source, freflocal_unit = _optional_channel(
            trace, ("local_reference_frequency", "s:freflocal")
        )
        if not all(len(values) == len(times) for values in (terminal_real, terminal_imag, current_real, current_imag, speed, phi_values)):
            raise ValueError("PowerFactory internal-E channels do not share one sample clock")

        machine = _case_machine(case_payload, generator)
        dynamics = machine.get("dynamics") or {}
        ra = _runtime_value(powerfactory_results, generator, "rstr")
        xstr = _runtime_value(powerfactory_results, generator, "xstr")
        if ra is None:
            ra = float(dynamics.get("ra") or 0.0)
        if xstr is None:
            xstr = float(dynamics.get("reference_xstr") or dynamics.get("xdp") or 0.0)
        if xstr <= 0:
            raise ValueError("PowerFactory internal-E audit requires positive rstr/xstr")
        e_values = [
            complex(vr, vi) + complex(ra, xstr) * complex(ir, ii)
            for vr, vi, ir, ii in zip(terminal_real, terminal_imag, current_real, current_imag)
        ]
        e_magnitude = [abs(value) for value in e_values]
        e_angle_deg = [math.degrees(cmath.phase(value)) for value in e_values]
        phi_deg = [math.degrees(value) for value in phi_values]
        xphi_deg = [math.degrees(value) for value in xphi_values] if xphi_values is not None else None
        firot_deg = [float(value) for value in firot_values] if firot_values is not None else None
        e_minus_phi = [_wrap_deg(e - p) for e, p in zip(e_angle_deg, phi_deg)]
        e_minus_xphi = [_wrap_deg(e - p) for e, p in zip(e_angle_deg, xphi_deg)] if xphi_deg is not None else None
        e_minus_firot = [_wrap_deg(e - p) for e, p in zip(e_angle_deg, firot_deg)] if firot_deg is not None else None
        firot_minus_phi = [_wrap_deg(f - p) for f, p in zip(firot_deg, phi_deg)] if firot_deg is not None else None
        frequency_hz = float((case_payload.get("network") or {}).get("frequency_hz") or 50.0)
        phi_derivative = _derivative(times, phi_values)
        fref_inferred = [speed_value - derivative / (2.0 * math.pi * frequency_hz) for speed_value, derivative in zip(speed, phi_derivative)]
        source_bus_frequency = _bus_frequency_pu(powerfactory_results, frequency_hz)
        fref_minus_inferred = (
            [value - inferred for value, inferred in zip(fref_values, fref_inferred)]
            if fref_values is not None
            else None
        )
        fref_minus_bus = (
            [value - bus_value for value, bus_value in zip(fref_values, source_bus_frequency)]
            if fref_values is not None and source_bus_frequency is not None
            else None
        )
        fault_time = _event_time(powerfactory_results, "fault")
        clear_time = _event_time(powerfactory_results, "clear_fault")
        if fault_time is None or clear_time is None:
            raise ValueError("PowerFactory results must expose fault and clear_fault events")
        tolerance = max(0.0005, 0.51 * _step(times))
        metric_series: dict[str, list[float] | None] = {
            "e_magnitude_pu": e_magnitude,
            "e_angle_deg": e_angle_deg,
            "sve_pu": sve_values,
            "mve_pu": mve_values,
            "e_magnitude_minus_sve_pu": (
                [e - sve for e, sve in zip(e_magnitude, sve_values)] if sve_values is not None else None
            ),
            "e_magnitude_minus_mve_pu": (
                [e - mve for e, mve in zip(e_magnitude, mve_values)] if mve_values is not None else None
            ),
            "e_angle_minus_phi_deg": e_minus_phi,
            "e_angle_minus_xphi_deg": e_minus_xphi,
            "e_angle_minus_firot_deg": e_minus_firot,
            "firot_minus_phi_deg": firot_minus_phi,
            "fref_inferred_pu": fref_inferred,
            "fref_pu": fref_values,
            "freflocal_pu": freflocal_values,
            "fref_minus_inferred_pu": fref_minus_inferred,
            "fref_minus_source_bus_pu": fref_minus_bus,
        }
        interval_indices: dict[str, list[int]] = {name: [] for name in _INTERVALS}
        for index, time in enumerate(times):
            interval_indices[_interval(time, fault=fault_time, clear=clear_time, tolerance=tolerance)].append(index)
        interval_metrics: dict[str, Any] = {}
        for interval, indices in interval_indices.items():
            interval_metrics[interval] = {
                "sample_count": len(indices),
                "time_s": _summary([times[index] for index in indices]),
                "metrics": {
                    name: _summary([series[index] for index in indices]) if series is not None else {"status": "unavailable", "sample_count": 0}
                    for name, series in metric_series.items()
                },
            }
        samples = []
        for item in _sample_indices(times, speed, clear=clear_time, step=_step(times)):
            index = int(item["index"])
            sample = dict(item)
            sample.update(
                {
                    "interval": _interval(times[index], fault=fault_time, clear=clear_time, tolerance=tolerance),
                    "e_magnitude_pu": e_magnitude[index],
                    "e_angle_deg": e_angle_deg[index],
                    "e_minus_phi_deg": e_minus_phi[index],
                    "e_minus_firot_deg": e_minus_firot[index] if e_minus_firot is not None else None,
                    "sve_pu": sve_values[index] if sve_values is not None else None,
                    "fref_inferred_pu": fref_inferred[index],
                }
            )
            samples.append(sample)
        return {
            "schema": SCHEMA,
            "status": "observed",
            "diagnostic_only": True,
            "generator": generator,
            "raw_solver_channels": {
                "terminal_voltage_real": {"source": terminal_real_source, "unit": terminal_real_unit},
                "terminal_voltage_imag": {"source": terminal_imag_source, "unit": terminal_imag_unit},
                "current_real": {"source": current_real_source, "unit": current_real_unit},
                "current_imag": {"source": current_imag_source, "unit": current_imag_unit},
                "phi": {"source": phi_source, "unit": phi_unit},
                "xphi": {"source": xphi_source, "unit": xphi_unit},
                "firot": {"source": firot_source, "unit": firot_unit},
                "speed": {"source": speed_source, "unit": speed_unit},
                "sve": {"source": sve_source, "unit": sve_unit},
                "fref": {"source": fref_source, "unit": fref_unit},
                "freflocal": {"source": freflocal_source, "unit": freflocal_unit},
            },
            "model_parameters": {
                "ra_pu": ra,
                "xstr_pu": xstr,
                "equation": "E_reconstructed = (m:utr + j*m:uti) + (rstr + j*xstr)*(m:cur1r + j*m:cur1i)",
                "fref_inferred_equation": "fref = n - (dphi/dt)/(2*pi*f_nominal)",
            },
            "events": {
                "fault_time_s": fault_time,
                "clear_fault_time_s": clear_time,
                "sample_tolerance_s": tolerance,
            },
            "interval_metrics": interval_metrics,
            "samples": samples,
            "global_metrics": {name: _summary(series) if series is not None else {"status": "unavailable", "sample_count": 0} for name, series in metric_series.items()},
            "interpretation": (
                "Diagnostic only. E is reconstructed from raw PowerFactory terminal voltage/current; "
                "no fitted offset or trace is fed into OpenDSS. s:ve is reported verbatim and is not "
                "declared equivalent to the reconstructed stator internal emf unless its residual proves that identity."
            ),
        }
    except (TypeError, ValueError, KeyError) as exc:
        return {
            "schema": SCHEMA,
            "status": "blocked",
            "passed": False,
            "diagnostic_only": True,
            "generator": generator,
            "reason": str(exc),
        }


__all__ = ["SCHEMA", "audit_powerfactory_internal_emf"]
