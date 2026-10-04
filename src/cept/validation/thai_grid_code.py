"""Traceable Thai grid-code checks.

The values below are the PEA Power Network System Interconnection Code B.E.
2559 (2016), section 8.1.1/8.2. The profile is deliberately versioned and
does not claim to replace a current utility review.
"""

from __future__ import annotations

from typing import Any

from cept.schema.case import Case
from cept.schema.result import StudyResult

PEA_2016_SOURCE = (
    "https://bimstecenergycentre.org/wp-content/uploads/2025/04/PEA-Interconnection-Code-2016-3.pdf"
)
PEA_2016_VOLTAGE_KV = {
    22.0: (20.9, 23.1),
    33.0: (31.3, 34.7),
    115.0: (109.2, 120.7),
}


def _nominal_kv(case: Case) -> dict[str, float]:
    if case.network.kind != "inline" or case.network.inline is None:
        return {}
    return {bus.name.lower(): bus.kv for bus in case.network.inline.buses}


def validate_pea_2016(case: Case, result: StudyResult) -> dict[str, Any]:
    """Return machine-readable voltage checks for the PEA 2016 profile."""
    kv_by_bus = _nominal_kv(case)
    checks = []
    voltages = []
    if result.load_flow:
        voltages.extend(result.load_flow.bus_voltages)
    if result.time_series:
        for snapshot in result.time_series.snapshots:
            voltages.extend(snapshot.bus_voltages)
    for value in voltages:
        kv = kv_by_bus.get(value.bus.lower())
        limits = PEA_2016_VOLTAGE_KV.get(kv or -1)
        if limits is None:
            checks.append(
                {
                    "bus": value.bus,
                    "phase": value.phase,
                    "passed": False,
                    "reason": f"No PEA-2016 voltage table for nominal kV={kv}",
                }
            )
            continue
        lo, hi = limits
        pu_lo, pu_hi = lo / kv, hi / kv
        checks.append(
            {
                "bus": value.bus,
                "phase": value.phase,
                "v_pu": value.v_pu,
                "lower_pu": pu_lo,
                "upper_pu": pu_hi,
                "passed": pu_lo <= value.v_pu <= pu_hi,
            }
        )
    return {
        "profile": "PEA-2016",
        "source": PEA_2016_SOURCE,
        "passed": bool(checks) and all(c["passed"] for c in checks),
        "checks": checks,
        "limitations": [
            "Frequency, harmonic, flicker, protection, and ride-through clauses require dedicated solver channels and are not inferred from voltage alone.",
            "Confirm the current utility-issued code and connection category before design sign-off.",
        ],
    }


__all__ = ["PEA_2016_SOURCE", "validate_pea_2016"]
