"""Same-sample PF/OpenDSS torque and power balance diagnostics."""

from __future__ import annotations

import math
import statistics
from typing import Any


SCHEMA = "cept-torque-power-audit-v1"
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


def _find_channel(
    results: dict[str, Any], *, trace_names: tuple[str, ...], channel_name: str
) -> tuple[list[float], list[float], str]:
    dynamics = results.get("dynamics")
    monitors = dynamics.get("monitors", []) if isinstance(dynamics, dict) else []
    traces = [trace for trace in monitors or [] if isinstance(trace, dict)]
    ordered = [trace for name in trace_names for trace in traces if trace.get("name") == name]
    ordered.extend(trace for trace in traces if trace not in ordered)
    for trace in ordered:
        times = trace.get("t")
        channel = next(
            (item for item in trace.get("channels", []) or [] if item.get("name") == channel_name),
            None,
        )
        values = channel.get("values") if isinstance(channel, dict) else None
        if not isinstance(times, list) or not isinstance(values, list) or len(times) != len(values):
            continue
        finite_t: list[float] = []
        finite_y: list[float] = []
        for time, value in zip(times, values):
            if not isinstance(time, (int, float)) or not isinstance(value, (int, float)):
                continue
            if math.isfinite(float(time)) and math.isfinite(float(value)):
                finite_t.append(float(time))
                finite_y.append(float(value))
        if len(finite_t) >= 3:
            return finite_t, finite_y, str(trace.get("name") or "")
    raise ValueError(f"no finite {channel_name!r} channel found")


def _step(times: list[float]) -> float:
    deltas = sorted(right - left for left, right in zip(times, times[1:]) if right > left)
    return deltas[len(deltas) // 2] if deltas else 0.001


def _same_sample_pairs(
    pf_times: list[float], dss_times: list[float], *, tolerance: float
) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    left = right = 0
    while left < len(pf_times) and right < len(dss_times):
        delta = pf_times[left] - dss_times[right]
        if abs(delta) <= tolerance:
            pairs.append((left, right))
            left += 1
            right += 1
        elif delta < 0:
            left += 1
        else:
            right += 1
    return pairs


def _derivative(times: list[float], values: list[float], *, frequency_hz: float) -> list[float]:
    result: list[float] = []
    for index in range(len(values)):
        if index == 0:
            right = 1
            delta_t = times[right] - times[index]
            delta_y = values[right] - values[index]
        elif index == len(values) - 1:
            left = index - 1
            delta_t = times[index] - times[left]
            delta_y = values[index] - values[left]
        else:
            left = index - 1
            right = index + 1
            delta_t = times[right] - times[left]
            delta_y = values[right] - values[left]
        result.append(2.0 * math.pi * frequency_hz * delta_y / max(delta_t, 1e-12))
    return result


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


def _metric(pf_values: list[float], dss_values: list[float]) -> dict[str, Any]:
    residual = [left - right for left, right in zip(pf_values, dss_values)]
    scale = max(max((abs(value) for value in dss_values), default=0.0), 1e-12)
    reference_rms = math.sqrt(sum(value * value for value in dss_values) / max(len(dss_values), 1))
    residual_rms = math.sqrt(sum(value * value for value in residual) / max(len(residual), 1))
    return {
        "sample_count": len(residual),
        "max_absolute_error": max((abs(value) for value in residual), default=0.0),
        "mean_error": statistics.fmean(residual) if residual else None,
        "nrmse": residual_rms / max(reference_rms, scale * 1e-6),
    }


def audit_torque_power(
    powerfactory_results: dict[str, Any],
    opendss_results: dict[str, Any],
    case_payload: dict[str, Any],
    *,
    generator: str = "G1",
) -> dict[str, Any]:
    """Compare solver terms at matched source timestamps, without gating."""

    try:
        fault_time = _event_time(powerfactory_results, "fault")
        clear_time = _event_time(powerfactory_results, "clear_fault")
        dss_fault_time = _event_time(opendss_results, "fault")
        dss_clear_time = _event_time(opendss_results, "clear_fault")
        if fault_time is None or clear_time is None or dss_fault_time is None or dss_clear_time is None:
            raise ValueError("both engines must expose fault and clear_fault event times")
        pf_trace_names = (generator,)
        dss_trace_names = (f"cept_{generator.lower()}_st", generator)
        pf_series = {
            name: _find_channel(
                powerfactory_results,
                trace_names=pf_trace_names,
                channel_name=channel,
            )
            for name, channel in {
                "electrical_torque": "electrical_torque",
                "mechanical_torque": "mechanical_torque",
                "electrical_power": "electrical_power_pu",
                "mechanical_input": "mechanical_power_input",
                "speed": "speed",
            }.items()
        }
        dss_series = {
            name: _find_channel(
                opendss_results,
                trace_names=dss_trace_names,
                channel_name=channel,
            )
            for name, channel in {
                "electrical_power": "ElectricalPowerPU",
                "mechanical_input": "MechanicalPowerPU",
                "speed": "Speed",
                "speed_derivative": "SpeedDerivativeRadPerS2",
            }.items()
        }
        try:
            dss_series["mechanical_torque"] = _find_channel(
                opendss_results,
                trace_names=dss_trace_names,
                channel_name="MechanicalTorquePU",
            )
            dss_mechanical_torque_source = "MechanicalTorquePU"
        except ValueError:
            # Results created before the explicit torque output was added can
            # still be audited, but their MechanicalPowerPU channel is not a
            # torque readback once speed differs from one.
            dss_series["mechanical_torque"] = dss_series["mechanical_input"]
            dss_mechanical_torque_source = "MechanicalPowerPU (legacy fallback)"
        pf_times = pf_series["speed"][0]
        dss_times = dss_series["speed"][0]
        tolerance = max(1e-4, 0.25 * max(_step(pf_times), _step(dss_times)))
        pairs = _same_sample_pairs(pf_times, dss_times, tolerance=tolerance)
        if not pairs:
            raise ValueError("no same-sample PF/OpenDSS timestamps within the alignment tolerance")

        inline = ((case_payload.get("network") or {}).get("inline") or {})
        generator_row = next(
            (row for row in inline.get("generators", []) or [] if row.get("name") == generator),
            None,
        )
        if not isinstance(generator_row, dict):
            raise ValueError(f"case has no generator {generator!r}")
        cosn = abs(float(generator_row.get("rated_power_factor") or generator_row.get("pf") or 1.0))
        cosn = cosn if cosn > 0 else 1.0
        frequency_hz = float((case_payload.get("network") or {}).get("frequency_hz") or 50.0)
        pf_speed_derivative = _derivative(
            pf_series["speed"][0], pf_series["speed"][1], frequency_hz=frequency_hz
        )
        dss_speed_derivative = dss_series["speed_derivative"][1]

        definitions = {
            "electrical_power": {
                "pf": "electrical_power",
                "dss": "electrical_power",
                "equation": "PF m:pgt * cosn (Pgn -> Sgn) versus DSS ElectricalPowerPU (Sgn)",
            },
            "mechanical_input": {
                "pf": "mechanical_input",
                "dss": "mechanical_input",
                "equation": "PF s:pt * cosn (Pgn -> Sgn) versus DSS MechanicalPowerPU (Sgn)",
            },
            "electrical_torque_vs_power": {
                "pf": "electrical_torque",
                "dss": "electrical_power",
                "equation": "PF m:xme * cosn (active-base torque) versus DSS ElectricalPowerPU",
            },
            "mechanical_torque_vs_power": {
                "pf": "mechanical_torque",
                "dss": "mechanical_torque",
                "equation": "PF m:xmt * cosn (active-base torque) versus DSS MechanicalTorquePU",
            },
            "torque_balance": {
                "pf": "torque_balance",
                "dss": "power_balance",
                "equation": "(PF m:xmt - m:xme) * cosn versus DSS (MechanicalTorquePU - ElectricalPowerPU)",
            },
            "speed_derivative": {
                "pf": "speed_derivative",
                "dss": "speed_derivative",
                "equation": "2*pi*f*d(speed_pu)/dt versus DSS SpeedDerivativeRadPerS2",
            },
        }
        interval_rows: dict[str, list[dict[str, Any]]] = {name: [] for name in _INTERVALS}
        sample_examples: dict[str, list[dict[str, Any]]] = {name: [] for name in _INTERVALS}
        for pf_index, dss_index in pairs:
            time_pf = pf_times[pf_index]
            time_dss = dss_times[dss_index]
            interval = _interval(
                (time_pf + time_dss) / 2.0,
                fault=(fault_time + dss_fault_time) / 2.0,
                clear=(clear_time + dss_clear_time) / 2.0,
                tolerance=tolerance,
            )
            row = {
                "time_powerfactory_s": time_pf,
                "time_opendss_s": time_dss,
                "time_delta_s": time_pf - time_dss,
                "values": {
                    "electrical_power": {
                        "pf_raw": pf_series["electrical_power"][1][pf_index],
                        "pf_sgn": pf_series["electrical_power"][1][pf_index] * cosn,
                        "dss": dss_series["electrical_power"][1][dss_index],
                    },
                    "mechanical_input": {
                        "pf_raw": pf_series["mechanical_input"][1][pf_index],
                        "pf_sgn": pf_series["mechanical_input"][1][pf_index] * cosn,
                        "dss": dss_series["mechanical_input"][1][dss_index],
                    },
                    "electrical_torque_vs_power": {
                        "pf_raw": pf_series["electrical_torque"][1][pf_index],
                        "pf_sgn": pf_series["electrical_torque"][1][pf_index] * cosn,
                        "dss": dss_series["electrical_power"][1][dss_index],
                    },
                    "mechanical_torque_vs_power": {
                        "pf_raw": pf_series["mechanical_torque"][1][pf_index],
                        "pf_sgn": pf_series["mechanical_torque"][1][pf_index] * cosn,
                        "dss": dss_series["mechanical_torque"][1][dss_index],
                    },
                    "torque_balance": {
                        "pf": (pf_series["mechanical_torque"][1][pf_index] - pf_series["electrical_torque"][1][pf_index]) * cosn,
                        "dss": dss_series["mechanical_torque"][1][dss_index] - dss_series["electrical_power"][1][dss_index],
                    },
                    "speed_derivative": {
                        "pf": pf_speed_derivative[pf_index],
                        "dss": dss_speed_derivative[dss_index],
                    },
                },
            }
            interval_rows[interval].append(row)
            if len(sample_examples[interval]) < 3:
                sample_examples[interval].append(row)

        metrics_by_interval: dict[str, dict[str, Any]] = {}
        for interval, rows in interval_rows.items():
            metric_rows: dict[str, Any] = {"sample_count": len(rows)}
            for quantity, definition in definitions.items():
                if quantity in {"electrical_power", "mechanical_input", "electrical_torque_vs_power", "mechanical_torque_vs_power"}:
                    pf_values = [float(row["values"][quantity]["pf_sgn"]) for row in rows]
                    dss_values = [float(row["values"][quantity]["dss"]) for row in rows]
                elif quantity == "torque_balance":
                    pf_values = [float(row["values"][quantity]["pf"]) for row in rows]
                    dss_values = [float(row["values"][quantity]["dss"]) for row in rows]
                else:
                    pf_values = [float(row["values"][quantity]["pf"]) for row in rows]
                    dss_values = [float(row["values"][quantity]["dss"]) for row in rows]
                metric_rows[quantity] = {
                    **_metric(pf_values, dss_values),
                    "equation": definition["equation"],
                }
            metrics_by_interval[interval] = metric_rows

        initial_pf_mechanical = pf_series["mechanical_input"][1][0] * cosn
        active_kw = float(generator_row.get("kw") or 0.0)
        rated_mva = float(generator_row.get("mva") or 0.0)
        case_pm_sgn = active_kw / (rated_mva * 1000.0) if rated_mva else None
        return {
            "schema": SCHEMA,
            "status": "observed",
            "diagnostic_only": True,
            "generator": generator,
            "same_sample_alignment": {
                "sample_count": len(pairs),
                "tolerance_s": tolerance,
                "powerfactory_step_s": _step(pf_times),
                "opendss_step_s": _step(dss_times),
                "event_times": {
                    "powerfactory_fault_s": fault_time,
                    "opendss_fault_s": dss_fault_time,
                    "powerfactory_clear_s": clear_time,
                    "opendss_clear_s": dss_clear_time,
                },
            },
            "base_conversion": {
                "powerfactory_to_opendss_factor": cosn,
                "equation": "PF active-power/Pgn base -> DSS Sgn base: value_Sgn = value_Pgn * cosn",
                "case_active_power_on_sgn": case_pm_sgn,
                "pf_initial_mechanical_after_conversion": initial_pf_mechanical,
                "initial_base_residual": (
                    initial_pf_mechanical - case_pm_sgn if case_pm_sgn is not None else None
                ),
            },
            "channels": {
                "powerfactory": {
                    "electrical_torque": "m:xme",
                    "mechanical_torque": "m:xmt",
                    "electrical_power": "m:pgt",
                    "mechanical_input": "s:pt",
                },
                "opendss": {
                    "electrical_power": "ElectricalPowerPU",
                    "mechanical_input": "MechanicalPowerPU",
                    "mechanical_torque": dss_mechanical_torque_source,
                    "speed_derivative": "SpeedDerivativeRadPerS2",
                },
            },
            "interval_metrics": metrics_by_interval,
            "sample_examples": sample_examples,
            "interpretation": (
                "Diagnostic only. The PF constant-power semantics are explicit: s:pt is the "
                "mechanical-power readback while m:xmt=s:pt/n is the torque used by the swing "
                "equation. PF m:xme/m:xmt/m:pgt/s:pt are compared after the explicit Pgn-to-Sgn "
                "base conversion; residuals are retained by interval so event kick and post-clear "
                "electrical torque can be separated."
            ),
        }
    except (TypeError, ValueError) as exc:
        return {
            "schema": SCHEMA,
            "status": "blocked",
            "diagnostic_only": True,
            "generator": generator,
            "reason": str(exc),
        }


__all__ = ["SCHEMA", "audit_torque_power"]
