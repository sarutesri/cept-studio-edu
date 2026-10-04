"""Reference-waveform comparison for solver-produced EMT traces."""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any

from cept.schema.result import StudyResult


def _read_long_csv(path: str | Path) -> dict[str, tuple[list[float], list[float], str, str]]:
    """Read a metadata-bearing long waveform CSV from a paper/dataset."""
    rows: dict[str, tuple[list[float], list[float], str, str]] = {}
    with Path(path).open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        required = {"time_s", "channel", "value", "unit", "source_channel"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(
                "reference waveform CSV must contain time_s, channel, value, unit, source_channel columns"
            )
        for row in reader:
            channel = (row.get("channel") or "").strip()
            if not channel:
                raise ValueError("reference waveform contains an empty channel")
            unit = (row.get("unit") or "").strip()
            source_channel = (row.get("source_channel") or "").strip()
            if not unit or not source_channel:
                raise ValueError(f"reference waveform metadata is incomplete for channel {channel}")
            if channel not in rows:
                rows[channel] = ([], [], unit, source_channel)
            t, values, known_unit, known_source = rows[channel]
            if (unit, source_channel) != (known_unit, known_source):
                raise ValueError(f"reference waveform metadata changes within channel {channel}")
            t.append(float(row["time_s"]))
            values.append(float(row["value"]))
    if not rows:
        raise ValueError("reference waveform CSV contains no data rows")
    return rows


def compare_emt_waveform(
    result: StudyResult,
    reference_csv: str | Path,
    *,
    source_id: str,
    source_url: str,
    tolerance: float,
    time_tolerance: float = 1e-9,
) -> dict[str, Any]:
    """Compare solver channels against a cited long-format reference CSV.

    Missing channels, unequal sample counts, or time-grid mismatches are
    ``blocked`` checks rather than silently interpolated values.
    """
    if result.emt is None:
        raise ValueError("paper waveform comparison requires an EMT StudyResult")
    if tolerance < 0:
        raise ValueError("tolerance must be non-negative")
    reference = _read_long_csv(reference_csv)
    actual = {
        trace.name: (
            trace.t,
            trace.channels[0].values,
            trace.channels[0].unit,
            trace.channels[0].source_channel,
        )
        for trace in result.emt.channels
        if trace.channels
    }
    checks: list[dict[str, Any]] = []
    for channel, (ref_t, ref_y, ref_unit, ref_source) in reference.items():
        if channel not in actual:
            checks.append({"channel": channel, "status": "blocked", "reason": "solver channel absent"})
            continue
        got_t, got_y, got_unit, got_source = actual[channel]
        if not got_unit or not got_source:
            checks.append(
                {"channel": channel, "status": "blocked", "reason": "solver channel metadata is incomplete"}
            )
            continue
        if got_unit != ref_unit:
            checks.append(
                {
                    "channel": channel,
                    "status": "blocked",
                    "reason": f"unit mismatch: solver={got_unit}, reference={ref_unit}",
                }
            )
            continue
        if len(ref_t) != len(got_t) or any(abs(a - b) > time_tolerance for a, b in zip(ref_t, got_t)):
            checks.append(
                {"channel": channel, "status": "blocked", "reason": "time grid or sample count mismatch"}
            )
            continue
        errors = [abs(a - b) for a, b in zip(got_y, ref_y)]
        rmse = math.sqrt(sum(error * error for error in errors) / len(errors)) if errors else math.inf
        checks.append(
            {
                "channel": channel,
                "status": "pass" if max(errors, default=math.inf) <= tolerance else "fail",
                "n": len(errors),
                "max_abs": max(errors, default=None),
                "rmse": rmse,
                "tolerance": tolerance,
                "unit": ref_unit,
                "reference_source_channel": ref_source,
                "solver_source_channel": got_source,
            }
        )
    for channel in sorted(set(actual) - set(reference)):
        checks.append(
            {
                "channel": channel,
                "status": "blocked",
                "reason": "reference CSV does not cover this solver channel",
            }
        )
    return {
        "source_id": source_id,
        "source_url": source_url,
        "case_name": result.case_name,
        "case_fingerprint": result.case_fingerprint,
        "engine": result.engine,
        "calculation_method": result.calculation_method,
        "metadata_required": True,
        "reference_csv": str(Path(reference_csv).resolve()),
        "passed": bool(checks) and all(check["status"] == "pass" for check in checks),
        "checks": checks,
    }


__all__ = ["compare_emt_waveform"]
