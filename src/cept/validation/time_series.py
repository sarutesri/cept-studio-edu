"""Exact-grid comparison helpers for CEPT quasi-static time-series evidence.

This module intentionally does not interpolate or resample. A parity report is
only meaningful when candidates represent the same declared solver clock.
QSTS is a sequence of steady-state solves and must not be labelled dynamic
validation.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Iterable


class TimeSeriesComparisonError(ValueError):
    pass


@dataclass(frozen=True)
class ErrorMetrics:
    n: int
    mae: float
    rmse: float
    max_abs: float
    max_rel_pct: float | None

    def as_dict(self) -> dict[str, float | int | None]:
        return {
            "n": self.n,
            "mae": self.mae,
            "rmse": self.rmse,
            "max_abs": self.max_abs,
            "max_rel_pct": self.max_rel_pct,
        }


def load_result(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _result_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Accept a raw StudyResult or common run-envelope shapes."""
    if isinstance(payload.get("time_series"), dict):
        return payload
    for key in ("result", "results", "study_result"):
        candidate = payload.get(key)
        if isinstance(candidate, dict) and isinstance(candidate.get("time_series"), dict):
            return candidate
    raise TimeSeriesComparisonError("result does not contain a time_series payload")


def snapshots(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = _result_payload(payload)
    rows = result["time_series"].get("snapshots")
    if not isinstance(rows, list) or not rows:
        raise TimeSeriesComparisonError("time_series.snapshots is empty")
    return rows


def time_grid(payload: dict[str, Any]) -> list[float]:
    grid: list[float] = []
    for row in snapshots(payload):
        value = row.get("t_s")
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise TimeSeriesComparisonError("every snapshot must carry a finite numeric t_s")
        grid.append(float(value))
    if any(b <= a for a, b in zip(grid, grid[1:])):
        raise TimeSeriesComparisonError("time grid must be strictly increasing")
    return grid


def require_same_time_grid(results: dict[str, dict[str, Any]], *, atol_s: float = 1e-9) -> list[float]:
    if len(results) < 2:
        raise TimeSeriesComparisonError("at least two result lanes are required")
    labels = list(results)
    reference = time_grid(results[labels[0]])
    for label in labels[1:]:
        candidate = time_grid(results[label])
        if len(candidate) != len(reference):
            raise TimeSeriesComparisonError(
                f"time grid length mismatch: {labels[0]}={len(reference)}, {label}={len(candidate)}"
            )
        for index, (a, b) in enumerate(zip(reference, candidate)):
            if abs(a - b) > atol_s:
                raise TimeSeriesComparisonError(
                    f"time grid mismatch at index {index}: {labels[0]}={a:g}s, {label}={b:g}s; "
                    "interpolation is deliberately disabled"
                )
    return reference


def _finite_optional(value: Any) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return None
    return float(value)


def scalar_channel(payload: dict[str, Any], name: str) -> list[float | None]:
    if name not in {"min_voltage_pu", "max_voltage_pu", "total_load_kw"}:
        raise TimeSeriesComparisonError(f"unsupported common scalar channel: {name}")
    return [_finite_optional(row.get(name)) for row in snapshots(payload)]


def bus_voltage_channel(payload: dict[str, Any], bus: str, phase: int) -> list[float | None]:
    key = bus.casefold()
    values: list[float | None] = []
    for row in snapshots(payload):
        match = None
        for item in row.get("bus_voltages") or []:
            if str(item.get("bus", "")).casefold() == key and int(item.get("phase", 0)) == phase:
                match = _finite_optional(item.get("v_pu"))
                break
        values.append(match)
    return values


def opender_channel(payload: dict[str, Any], der: str, signal: str) -> tuple[list[float], list[float]]:
    """Extract controller-only OpenDER telemetry from StudyResult.extra."""
    if signal not in {"v_pu", "f_hz", "p_kw", "q_kvar"}:
        raise TimeSeriesComparisonError(f"unsupported OpenDER signal: {signal}")
    result = _result_payload(payload)
    records = (result.get("extra") or {}).get("opender") or []
    record = next((r for r in records if str(r.get("der", "")).casefold() == der.casefold()), None)
    if record is None:
        raise TimeSeriesComparisonError(f"OpenDER telemetry not found for DER '{der}'")
    times: list[float] = []
    values: list[float] = []
    for item in record.get("exchanges") or []:
        exchange = item.get("exchange") or {}
        t_s = _finite_optional(item.get("t_s", exchange.get("t_s")))
        value = _finite_optional(exchange.get(signal))
        if t_s is not None and value is not None:
            times.append(t_s)
            values.append(value)
    if not times:
        raise TimeSeriesComparisonError(f"OpenDER signal '{signal}' for DER '{der}' is empty")
    return times, values


def metrics(
    reference: Iterable[float | None],
    candidate: Iterable[float | None],
    *,
    expected_n: int | None = None,
    channel: str = "channel",
    candidate_label: str = "candidate",
) -> ErrorMetrics:
    """Return complete exact-grid error metrics.

    Missing/non-finite aligned values are a blocking evidence defect.  If a
    caller does not provide ``expected_n`` explicitly, the declared channel
    length becomes the expected complete sample count.  This keeps every caller
    of the exact-grid validation module fail-closed, including readiness
    re-derivation, and prevents a comparison from improving by silently dropping
    difficult timestamps.
    """
    ref_values = list(reference)
    candidate_values = list(candidate)
    if len(ref_values) != len(candidate_values):
        raise TimeSeriesComparisonError("channel length mismatch")
    if expected_n is None:
        expected_n = len(ref_values)
    if len(ref_values) != expected_n:
        raise TimeSeriesComparisonError(
            f"BLOCKED_INCOMPLETE_CHANNEL: {channel} has {len(ref_values)} samples; expected {expected_n}"
        )
    pairs = [
        (float(a), float(b))
        for a, b in zip(ref_values, candidate_values)
        if a is not None and b is not None and math.isfinite(float(a)) and math.isfinite(float(b))
    ]
    if len(pairs) != expected_n:
        raise TimeSeriesComparisonError(
            f"BLOCKED_INCOMPLETE_CHANNEL: {channel} for {candidate_label} has "
            f"{len(pairs)}/{expected_n} finite aligned sample pairs"
        )
    if not pairs:
        raise TimeSeriesComparisonError("channel has no aligned finite sample pairs")
    errors = [b - a for a, b in pairs]
    abs_errors = [abs(value) for value in errors]
    rel = [100.0 * abs(b - a) / abs(a) for a, b in pairs if abs(a) > 1e-12]
    return ErrorMetrics(
        n=len(pairs),
        mae=sum(abs_errors) / len(abs_errors),
        rmse=math.sqrt(sum(value * value for value in errors) / len(errors)),
        max_abs=max(abs_errors),
        max_rel_pct=max(rel) if rel else None,
    )


def compare_common_channels(
    results: dict[str, dict[str, Any]],
    *,
    reference_label: str,
    buses: Iterable[tuple[str, int]] = (),
) -> dict[str, Any]:
    """Compare required QSTS channels on one exact, complete solver clock.

    This function produces diagnostic numerical agreement only.  It deliberately
    does not manufacture a PASS tolerance: acceptance must come from a named
    benchmark policy or an independent measured/published reference.
    """
    grid = require_same_time_grid(results)
    if reference_label not in results:
        raise TimeSeriesComparisonError(f"reference lane '{reference_label}' is missing")
    expected_n = len(grid)
    channels: dict[str, dict[str, Any]] = {}
    for name in ("min_voltage_pu", "max_voltage_pu", "total_load_kw"):
        ref = scalar_channel(results[reference_label], name)
        channels[name] = {}
        for label, payload in results.items():
            if label != reference_label:
                channels[name][label] = metrics(
                    ref,
                    scalar_channel(payload, name),
                    expected_n=expected_n,
                    channel=name,
                    candidate_label=label,
                ).as_dict()
    for bus, phase in buses:
        name = f"voltage_pu:{bus}:phase{phase}"
        ref = bus_voltage_channel(results[reference_label], bus, phase)
        channels[name] = {}
        for label, payload in results.items():
            if label != reference_label:
                channels[name][label] = metrics(
                    ref,
                    bus_voltage_channel(payload, bus, phase),
                    expected_n=expected_n,
                    channel=name,
                    candidate_label=label,
                ).as_dict()
    return {
        "schema": "cept-time-series-parity-v1",
        "evidence_class": "cross-engine-diagnostic",
        "acceptance_status": "observed-no-acceptance-criterion",
        "acceptance_criterion": None,
        "dynamic_validation": False,
        "interpolation_used": False,
        "complete_sample_coverage": True,
        "reference_lane": reference_label,
        "lanes": list(results),
        "samples": expected_n,
        "time_grid_s": grid,
        "channels": channels,
        "claim_boundary": (
            "Agreement demonstrates numerical parity diagnostics under the declared Case/time grid. "
            "No PASS is implied without a named acceptance criterion. It is not independent ground "
            "truth unless the reference lane itself is backed by independent measured/published results."
        ),
    }
