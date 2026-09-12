"""OpenDSS adapter — protection studies."""

from __future__ import annotations

from cept.schema.case import Case
from cept.schema.result import (
    ProtectionResult,
    ProtectionTrip,
)

from cept.adapters.opendss.fault import run_fault


def run_protection(dss, case: Case, work_dir: str, state: dict):
    fault, sld = run_fault(dss, case, work_dir, state)
    opt = case.study.options
    current = fault.total_fault_current_a
    trips = []
    constants = {
        "standard_inverse": (0.14, 0.02),
        "very_inverse": (13.5, 1.0),
        "extremely_inverse": (80.0, 2.0),
    }
    for relay in opt.get("relays", []):
        pickup = float(relay["pickup_a"])
        multiple = (current / pickup) if current is not None else 0.0
        if multiple <= 1.0:
            trips.append(
                ProtectionTrip(
                    name=relay["name"],
                    pickup_a=pickup,
                    current_multiple=round(multiple, 6),
                    status="no-trip",
                )
            )
            continue
        a, exponent = constants[relay["curve"]]
        time_s = a * float(relay["time_dial"]) / (multiple**exponent - 1.0)
        trips.append(
            ProtectionTrip(
                name=relay["name"],
                pickup_a=pickup,
                current_multiple=round(multiple, 6),
                trip_time_s=round(time_s, 6),
                status="trip",
            )
        )
    result = ProtectionResult(
        fault_bus=opt["bus"],
        fault_type=opt.get("type", "3ph"),
        fault_current_a=current,
        relays=trips,
    )
    return result, sld
