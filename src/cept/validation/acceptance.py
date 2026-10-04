"""Acceptance policy for CEPT power-systems validation.

Maps (claim_level, quantity_kind) -> (tolerance_abs, tolerance_rel, scale_floor).
Enforces principled, physics-grounded acceptance criteria for:
1. pfd_parity: Same solver (source PF vs CEPT-PFD) -- tight tolerances.
2. cross_engine: Cross engine (source PF vs CEPT-OpenDSS) -- physically justified looser tolerances.

Any quantity without a policy entry or physical basis returns None, remaining observed-only.
"""

from __future__ import annotations


POLICY_VERSION = "1.0"


ACCEPTANCE_POLICY: dict[str, dict[str, dict[str, float]]] = {
    "pfd_parity": {
        "voltage_magnitude": {
            "tolerance_abs": 1e-4,  # 0.0001 pu (~0.1 mV on 1 pu)
            "tolerance_rel": 1e-4,
            "scale_floor": 1e-3,
        },
        "voltage_angle": {
            "tolerance_abs": 0.01,  # 0.01 degrees
            "tolerance_rel": 0.0,
            "scale_floor": 1e-3,
        },
        "active_power": {
            "tolerance_abs": 0.001,  # 0.1% rel + ~1 kW zero-flow floor
            "tolerance_rel": 0.001,
            "scale_floor": 1.0,
        },
        "reactive_power": {
            "tolerance_abs": 0.001,  # 0.1% rel + ~1 kvar zero-flow floor
            "tolerance_rel": 0.001,
            "scale_floor": 1.0,
        },
        "loading": {
            "tolerance_abs": 0.01,  # 0.01% loading
            "tolerance_rel": 0.001,
            "scale_floor": 0.01,
        },
        "current": {
            "tolerance_abs": 0.001,
            "tolerance_rel": 0.001,
            "scale_floor": 1.0,
        },
        "speed": {
            "tolerance_abs": 0.001,
            "tolerance_rel": 0.0,
            "scale_floor": 1e-3,
        },
        # The reviewed registry keeps a machine's own rotor angle separate from
        # a rotor angle measured against a reference machine.  They are judged
        # alike but never compared to each other.
        "rotor_angle": {
            "tolerance_abs": 0.01,  # 0.01 degrees
            "tolerance_rel": 0.0,
            "scale_floor": 1e-3,
        },
        "relative_rotor_angle": {
            "tolerance_abs": 0.01,  # 0.01 degrees
            "tolerance_rel": 0.0,
            "scale_floor": 1e-3,
        },
        "frequency": {
            "tolerance_abs": 1e-4,  # 0.0001 Hz
            "tolerance_rel": 0.0,
            "scale_floor": 1e-3,
        },
        "turbine_power": {
            "tolerance_abs": 0.005,
            "tolerance_rel": 0.005,
            "scale_floor": 1e-3,
        },
        "timeseries_peak": {
            "tolerance_abs": 1e-3,
            "tolerance_rel": 1e-3,
            "scale_floor": 1.0,
        },
        "timeseries_final": {
            "tolerance_abs": 1e-3,
            "tolerance_rel": 1e-3,
            "scale_floor": 1.0,
        },
        "timeseries_nrmse": {
            "tolerance_abs": 0.01,  # 1% NRMSE
            "tolerance_rel": 0.0,
            "scale_floor": 1.0,
        },
        "timeseries_event_time": {
            "tolerance_abs": 1e-3,  # 1 ms
            "tolerance_rel": 0.0,
            "scale_floor": 1.0,
        },
    },
    "cross_engine": {
        "voltage_magnitude": {
            "tolerance_abs": 5e-3,  # 0.005 pu (balanced pos-seq vs 3-phase floor)
            "tolerance_rel": 1e-3,
            "scale_floor": 1e-3,
        },
        "voltage_angle": {
            "tolerance_abs": 0.1,  # 0.1 deg angle resolution
            "tolerance_rel": 0.0,
            "scale_floor": 1e-3,
        },
        "active_power": {
            "tolerance_abs": 0.01,  # 1.0% rel + ~1 kW zero-flow floor
            "tolerance_rel": 0.01,
            "scale_floor": 1.0,
        },
        "reactive_power": {
            "tolerance_abs": 0.02,  # 2.0% rel (charging / zero-sequence differences)
            "tolerance_rel": 0.02,
            "scale_floor": 1.0,
        },
        "loading": {
            "tolerance_abs": 0.05,  # 0.05% loading
            "tolerance_rel": 0.01,
            "scale_floor": 0.01,
        },
        "current": {
            "tolerance_abs": 0.01,
            "tolerance_rel": 0.01,
            "scale_floor": 1.0,
        },
        "speed": {
            "tolerance_abs": 0.005,
            "tolerance_rel": 0.0,
            "scale_floor": 1e-3,
        },
        # The reviewed registry keeps a machine's own rotor angle separate from
        # a rotor angle measured against a reference machine.  They are judged
        # alike but never compared to each other.
        "rotor_angle": {
            "tolerance_abs": 0.05,  # 0.05 degrees
            "tolerance_rel": 0.0,
            "scale_floor": 1e-3,
        },
        "relative_rotor_angle": {
            "tolerance_abs": 0.05,  # 0.05 degrees
            "tolerance_rel": 0.0,
            "scale_floor": 1e-3,
        },
        "frequency": {
            "tolerance_abs": 1e-3,  # 0.001 Hz
            "tolerance_rel": 0.0,
            "scale_floor": 1e-3,
        },
        "turbine_power": {
            "tolerance_abs": 0.01,
            "tolerance_rel": 0.01,
            "scale_floor": 1e-3,
        },
        "timeseries_peak": {
            "tolerance_abs": 1e-2,
            "tolerance_rel": 1e-2,
            "scale_floor": 1.0,
        },
        "timeseries_final": {
            "tolerance_abs": 1e-2,
            "tolerance_rel": 1e-2,
            "scale_floor": 1.0,
        },
        "timeseries_nrmse": {
            "tolerance_abs": 0.05,  # 5% NRMSE
            "tolerance_rel": 0.0,
            "scale_floor": 1.0,
        },
        "timeseries_event_time": {
            "tolerance_abs": 1e-2,  # 10 ms
            "tolerance_rel": 0.0,
            "scale_floor": 1.0,
        },
    },
}


def normalize_quantity_kind(quantity: str) -> str:
    """Map raw quantity strings to canonical policy quantity keys."""
    q = quantity.lower().strip()
    if "voltage_magnitude" in q or q in (
        "v",
        "u",
        "voltage",
        "voltage_pu",
        "v_pu",
        "terminal_voltage",
        "terminal voltage",
    ):
        return "voltage_magnitude"
    if "voltage_angle" in q or q in ("angle", "deg", "phi", "phi_deg"):
        return "voltage_angle"
    if "reactive_power" in q or q in ("q", "q_mvar", "q_kvar", "q_to", "q_from", "power_q"):
        return "reactive_power"
    if (
        "active_power" in q
        or "electrical power" in q
        or "electrical_power" in q
        or q in ("p", "p_mw", "p_kw", "p_to", "p_from", "power_p")
    ):
        return "active_power"
    if "loss" in q or "ploss" in q or "qloss" in q:
        return "loss"
    if "loading" in q or "loading_pct" in q:
        return "loading"
    if "current" in q or q in ("i", "i_ka", "i_a", "current_ka"):
        return "current"
    if "rotor_angle" in q or "rotor angle" in q:
        return "rotor_angle"
    if "turbine_power" in q or q in ("turbine power", "pt", "pshaft"):
        return "turbine_power"
    # The reviewed registry names this quantity ``rotor_speed``; the policy
    # table has always keyed it as ``speed``.
    if q in ("speed", "rotor_speed", "rotorspeed", "xspeed"):
        return "speed"
    if "peak" in q:
        return "timeseries_peak"
    if "final" in q:
        return "timeseries_final"
    if "nrmse" in q or "rmse" in q:
        return "timeseries_nrmse"
    if "event_time" in q:
        return "timeseries_event_time"
    return q


def get_acceptance_tolerances(
    claim_level: str,
    quantity: str,
) -> dict[str, float] | None:
    """Lookup tolerance dict (tolerance_abs, tolerance_rel, scale_floor) for (claim_level, quantity).

    Returns None if no acceptance criterion exists for that combination (observed-only).
    """
    level_dict = ACCEPTANCE_POLICY.get(claim_level)
    if not level_dict:
        return None

    kind = normalize_quantity_kind(quantity)
    entry = level_dict.get(kind)
    if not entry:
        return None

    return dict(entry)


def get_tolerances_for_pair(pair: str) -> dict[str, dict[str, float]]:
    """Return complete quantity -> tolerance_dict map for a comparison pair string.

    pair can be 'source ↔ cept-pfd', 'cept-pfd', 'source ↔ opendss', 'opendss', etc.
    """
    p = pair.lower().strip()
    if "opendss" in p or "cross_engine" in p:
        claim_level = "cross_engine"
    else:
        claim_level = "pfd_parity"

    level_dict = ACCEPTANCE_POLICY.get(claim_level, {})
    res: dict[str, dict[str, float]] = {}
    for q_kind, tol in level_dict.items():
        res[q_kind] = dict(tol)
        # Also alias common specific key prefixes for pfd_pdf matching
        if q_kind == "voltage_magnitude":
            res["voltage_magnitude_pu"] = dict(tol)
            res["voltage_magnitude"] = dict(tol)
        elif q_kind == "voltage_angle":
            res["voltage_angle_deg"] = dict(tol)
            res["voltage_angle"] = dict(tol)
        elif q_kind == "active_power":
            res["active_power_kw"] = dict(tol)
            res["active_power_mw"] = dict(tol)
            res["active_power"] = dict(tol)
        elif q_kind == "reactive_power":
            res["reactive_power_kvar"] = dict(tol)
            res["reactive_power_mvar"] = dict(tol)
            res["reactive_power"] = dict(tol)
        elif q_kind == "loading":
            res["loading_pct"] = dict(tol)
            res["loading"] = dict(tol)
    return res
