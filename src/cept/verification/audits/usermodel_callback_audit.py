"""Cross-check OpenDSS UserModel callback channels against mode monitors."""

from __future__ import annotations

import math
from typing import Any


SCHEMA = "cept-usermodel-callback-audit-v1"


def _monitor(dynamics: dict[str, Any], mode: int, element: str | None = None) -> dict[str, Any] | None:
    for item in dynamics.get("monitors", []) or []:
        if not isinstance(item, dict) or int(item.get("mode", -1)) != mode:
            continue
        if element is None or str(item.get("element", "")).lower() == element.lower():
            return item
    return None


def _channel(monitor: dict[str, Any], *needles: str) -> dict[str, Any] | None:
    lowered = tuple(needle.lower() for needle in needles)
    for channel in monitor.get("channels", []) or []:
        if not isinstance(channel, dict):
            continue
        haystack = " ".join(
            str(channel.get(key, "")) for key in ("name", "source_channel", "unit")
        ).lower()
        if all(needle in haystack for needle in lowered):
            return channel
    return None


def _numeric(values: Any) -> list[float] | None:
    if not isinstance(values, list):
        return None
    output: list[float] = []
    for value in values:
        try:
            converted = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(converted):
            return None
        output.append(converted)
    return output


def _base_mva(case: dict[str, Any], element: str) -> tuple[str, float] | None:
    generator_name = element.split(".", 1)[-1]
    generators = (
        case.get("network", {}).get("inline", {}).get("generators", [])
        if isinstance(case.get("network"), dict)
        else []
    )
    for generator in generators or []:
        if not isinstance(generator, dict):
            continue
        if str(generator.get("name", "")).lower() != generator_name.lower():
            continue
        mva_value = generator.get("mva")
        units_value = generator.get("parallel_units", 1) or 1
        try:
            mva = float(mva_value) if mva_value is not None else math.nan
            units = float(units_value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(mva) or not math.isfinite(units) or mva <= 0 or units <= 0:
            return None
        return str(generator.get("name")), mva * units
    return None


def _same_time_grid(left: dict[str, Any], right: dict[str, Any]) -> tuple[list[float], str | None]:
    left_times = _numeric(left.get("t"))
    right_times = _numeric(right.get("t"))
    if left_times is None or right_times is None or not left_times or not right_times:
        return [], "monitor time grids are missing or non-numeric"
    if len(left_times) != len(right_times):
        return [], "callback and mode monitor sample counts differ"
    if any(abs(left - right) > 1e-9 for left, right in zip(left_times, right_times)):
        return [], "callback and mode monitor time grids differ"
    return left_times, None


def _comparison(
    name: str,
    callback: dict[str, Any],
    mode: dict[str, Any],
    times: list[float],
    callback_scale: float,
    unit: str,
    tolerance: float,
) -> dict[str, Any]:
    callback_values = _numeric(callback.get("values")) or []
    mode_values = _numeric(mode.get("values")) or []
    count = min(len(callback_values), len(mode_values), len(times))
    residual = [callback_values[index] * callback_scale - mode_values[index] for index in range(count)]
    mode_rms = math.sqrt(
        sum(mode_values[index] * mode_values[index] for index in range(count)) / count
    ) if count else 0.0
    residual_rms = math.sqrt(sum(value * value for value in residual) / count) if count else 0.0
    return {
        "quantity": name,
        "unit": unit,
        "sample_count": count,
        "callback_scale": callback_scale,
        "max_absolute_error": max((abs(value) for value in residual), default=None),
        "nrmse": residual_rms / max(mode_rms, 1e-12),
        "first_error": residual[0] if residual else None,
        "last_error": residual[-1] if residual else None,
        "tolerance": tolerance,
        "passed": bool(count) and max((abs(value) for value in residual), default=math.inf) <= tolerance,
        "callback_channel": callback.get("source_channel") or callback.get("name"),
        "mode_channel": mode.get("source_channel") or mode.get("name"),
    }


def audit_usermodel_callback(results: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    """Compare callback P/Q/V/I with the same-sample OpenDSS mode monitors.

    This is a solver-internal extraction audit only.  It is intentionally not a
    cross-engine acceptance gate: matching callback and mode channels cannot
    prove PowerFactory/OpenDSS parity.
    """
    dynamics = results.get("dynamics") or {}
    monitors = [item for item in dynamics.get("monitors", []) or [] if isinstance(item, dict)]
    callback_monitor = next(
        (
            item
            for item in monitors
            if int(item.get("mode", -1)) == 3
            and _channel(item, "electricalpowerpu")
            and _channel(item, "electricalreactivepowerpu")
            and _channel(item, "terminalvoltagepu")
            and _channel(item, "positivesequencecurrentpu")
        ),
        None,
    )
    if callback_monitor is None:
        return {
            "schema": SCHEMA,
            "status": "blocked",
            "passed": False,
            "diagnostic_only": True,
            "reason": "mode-3 UserModel callback P/Q/V/I channels are required",
        }

    element = str(callback_monitor.get("element", ""))
    pq_monitor = _monitor(dynamics, 65, element)
    vi_monitor = _monitor(dynamics, 112, element)
    if pq_monitor is None or vi_monitor is None:
        return {
            "schema": SCHEMA,
            "status": "blocked",
            "passed": False,
            "diagnostic_only": True,
            "reason": "mode-65 and mode-112 monitors for the callback element are required",
            "callback_monitor": callback_monitor.get("name") or element,
        }

    base = _base_mva(case, element)
    if base is None:
        return {
            "schema": SCHEMA,
            "status": "blocked",
            "passed": False,
            "diagnostic_only": True,
            "reason": "generator MVA base for the callback element is required",
            "callback_monitor": callback_monitor.get("name") or element,
            "element": element,
        }
    generator_name, mva_base = base

    callback_times, time_error = _same_time_grid(callback_monitor, pq_monitor)
    vi_times, vi_time_error = _same_time_grid(callback_monitor, vi_monitor)
    if time_error or vi_time_error:
        return {
            "schema": SCHEMA,
            "status": "blocked",
            "passed": False,
            "diagnostic_only": True,
            "reason": time_error or vi_time_error,
            "callback_monitor": callback_monitor.get("name") or element,
            "pq_monitor": pq_monitor.get("name"),
            "vi_monitor": vi_monitor.get("name"),
        }

    callback_channels = {
        "active_power": _channel(callback_monitor, "electricalpowerpu"),
        "reactive_power": _channel(callback_monitor, "electricalreactivepowerpu"),
        "voltage_magnitude": _channel(callback_monitor, "terminalvoltagepu"),
        "current_magnitude": _channel(callback_monitor, "positivesequencecurrentpu"),
    }
    mode_channels = {
        "active_power": _channel(pq_monitor, "mode65", "p1"),
        "reactive_power": _channel(pq_monitor, "mode65", "q1"),
        "voltage_magnitude": _channel(vi_monitor, "mode112", ":v"),
        "current_magnitude": _channel(vi_monitor, "mode112", ":i"),
    }
    if any(channel is None for channel in (*callback_channels.values(), *mode_channels.values())):
        return {
            "schema": SCHEMA,
            "status": "blocked",
            "passed": False,
            "diagnostic_only": True,
            "reason": "required callback/mode channel mapping is incomplete",
            "callback_monitor": callback_monitor.get("name") or element,
            "pq_monitor": pq_monitor.get("name"),
            "vi_monitor": vi_monitor.get("name"),
        }
    callback_active = callback_channels["active_power"]
    callback_reactive = callback_channels["reactive_power"]
    callback_voltage = callback_channels["voltage_magnitude"]
    callback_current = callback_channels["current_magnitude"]
    mode_active = mode_channels["active_power"]
    mode_reactive = mode_channels["reactive_power"]
    mode_voltage = mode_channels["voltage_magnitude"]
    mode_current = mode_channels["current_magnitude"]
    assert callback_active is not None
    assert callback_reactive is not None
    assert callback_voltage is not None
    assert callback_current is not None
    assert mode_active is not None
    assert mode_reactive is not None
    assert mode_voltage is not None
    assert mode_current is not None

    comparisons = [
        _comparison(
            "active_power",
            callback_active,
            mode_active,
            callback_times,
            mva_base,
            "MW",
            0.1,
        ),
        _comparison(
            "reactive_power",
            callback_reactive,
            mode_reactive,
            callback_times,
            mva_base,
            "Mvar",
            0.1,
        ),
        _comparison(
            "voltage_magnitude",
            callback_voltage,
            mode_voltage,
            vi_times,
            1.0,
            "p.u.",
            2e-5,
        ),
        _comparison(
            "current_magnitude",
            callback_current,
            mode_current,
            vi_times,
            1.0,
            "p.u.",
            2e-5,
        ),
    ]
    passed = all(item["passed"] for item in comparisons)
    return {
        "schema": SCHEMA,
        "status": "consistent" if passed else "mismatch",
        "passed": passed,
        "diagnostic_only": True,
        "claim_cap": ["callback_vs_mode_mapping", "not_cross_engine_parity"],
        "element": element,
        "generator": {"name": generator_name, "mva_base": mva_base},
        "monitors": {
            "callback": {"name": callback_monitor.get("name"), "mode": 3},
            "power": {"name": pq_monitor.get("name"), "mode": 65},
            "voltage_current": {"name": vi_monitor.get("name"), "mode": 112},
        },
        "time_grid": {
            "sample_count": len(callback_times),
            "t_start_s": callback_times[0] if callback_times else None,
            "t_end_s": callback_times[-1] if callback_times else None,
        },
        "comparisons": comparisons,
        "tolerances_are": "diagnostic extraction tolerances, not parity acceptance tolerances",
    }
