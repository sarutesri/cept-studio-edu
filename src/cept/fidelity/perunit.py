"""Typed per-unit base consistency audit for a Case.

A gate, not a second solver: it checks that every declared per-unit / kV base
in the typed Case is internally consistent and finite.  It never re-solves,
never repairs, and never derives engineering values; an absent declaration is
simply skipped, never assumed.

Fail-closed rules (each reason names the offending element):

- buses: ``kv`` finite and > 0
- lines: any declared per-kilometre value and any ``total_parameters``
  entry finite and >= 0 (this schema gives lines no independent ``base_kv``;
  a line bridging two voltage levels is rejected by the network model itself)
- two-winding transformers: ``uk_pct`` finite and >= 0; ``hv_kv``/``lv_kv``
  finite, > 0, and within 0.5% of the connected bus ``kv`` when the bus exists
- three-winding transformers: per-winding ``*_uk_pct`` / ``*_r_pu`` finite and
  >= 0, and per-winding ``*_kv`` within 0.5% of the connected bus ``kv``
- generators: ``xdpp_pu`` finite and > 0, ``mva`` finite and > 0, and, when a
  dynamics block is declared, its ``xd``/``xq`` reactances finite and > 0
- loads: ``kw`` (and ``q_kvar`` when declared) finite
- shunts: ``q_mvar`` finite
- DERs: ``kva`` finite and > 0 when declared
"""

from __future__ import annotations

import math
from typing import Any

from cept.schema import Case

_SCHEMA = "cept-perunit-audit-v1"
# Relative tolerance for a winding voltage against the bus it terminates on.
_KV_TOLERANCE = 0.005  # 0.5% of the larger of the two values
_SCOPE = "declared inline per-unit/kV bases only; no re-solve and no derived engineering values"


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _mismatch(a: float, b: float) -> bool:
    """True when ``a`` and ``b`` differ by more than 0.5% of the larger."""
    larger = max(a, b)
    if larger <= 0:
        return False
    return abs(a - b) / larger > _KV_TOLERANCE


def _audit_buses(inline: Any, reasons: list[str]) -> None:
    for bus in inline.buses:
        if not _finite(bus.kv) or bus.kv <= 0:
            reasons.append(f"bus {bus.name}: kv={bus.kv} must be finite and > 0")


def _audit_lines(inline: Any, reasons: list[str]) -> None:
    for line in inline.lines:
        for field in ("r1_ohm_per_km", "x1_ohm_per_km", "r0_ohm_per_km", "x0_ohm_per_km", "b1_us_per_km"):
            value = getattr(line, field)
            if value is not None and (not _finite(value) or value < 0):
                reasons.append(f"line {line.name}: {field}={value} must be finite and >= 0")
        total = line.total_parameters
        if total is not None:
            for field in ("r1_ohm", "x1_ohm", "b1_us", "r0_ohm", "x0_ohm", "b0_us"):
                value = getattr(total, field)
                if value is not None and (not _finite(value) or value < 0):
                    reasons.append(f"line {line.name}: total_parameters.{field}={value} must be finite and >= 0")


def _audit_transformer_windings(
    label: str,
    name: str,
    kv_by_bus: dict[str, float],
    windings: list[tuple[str, str, float, Any]],
    reasons: list[str],
) -> None:
    """Check one winding-voltage base against its connected bus (and any pu pair)."""
    for winding, bus_name, winding_kv, pu_values in windings:
        if not _finite(winding_kv) or winding_kv <= 0:
            reasons.append(f"{label} {name}: {winding}_kv={winding_kv} must be finite and > 0")
        elif bus_name in kv_by_bus and _mismatch(winding_kv, kv_by_bus[bus_name]):
            reasons.append(
                f"{label} {name}: {winding}_kv={winding_kv} mismatches bus "
                f"{bus_name} kv={kv_by_bus[bus_name]} by more than 0.5%"
            )
        for field, value in pu_values:
            if not _finite(value) or value < 0:
                reasons.append(f"{label} {name}: {field}={value} must be finite and >= 0")


def _audit_transformers(inline: Any, reasons: list[str]) -> None:
    kv_by_bus = {bus.name: bus.kv for bus in inline.buses}
    for transformer in inline.transformers:
        if not _finite(transformer.uk_pct) or transformer.uk_pct < 0:
            reasons.append(
                f"transformer {transformer.name}: uk_pct={transformer.uk_pct} must be finite and >= 0"
            )
        _audit_transformer_windings(
            "transformer",
            transformer.name,
            kv_by_bus,
            [
                ("hv", transformer.hv_bus, transformer.hv_kv, []),
                ("lv", transformer.lv_bus, transformer.lv_kv, []),
            ],
            reasons,
        )
    for transformer in inline.three_winding_transformers:
        windings = []
        for winding in ("hv", "mv", "lv"):
            windings.append(
                (
                    winding,
                    getattr(transformer, f"{winding}_bus"),
                    getattr(transformer, f"{winding}_kv"),
                    [
                        (f"{winding}_uk_pct", getattr(transformer, f"{winding}_uk_pct")),
                        (f"{winding}_r_pu", getattr(transformer, f"{winding}_r_pu")),
                    ],
                )
            )
        _audit_transformer_windings("three-winding transformer", transformer.name, kv_by_bus, windings, reasons)


def _audit_generators(inline: Any, reasons: list[str]) -> None:
    for generator in inline.generators:
        if not _finite(generator.xdpp_pu) or generator.xdpp_pu <= 0:
            reasons.append(
                f"generator {generator.name}: xdpp_pu={generator.xdpp_pu} must be finite and > 0"
            )
        if not _finite(generator.mva) or generator.mva <= 0:
            reasons.append(f"generator {generator.name}: mva={generator.mva} must be finite and > 0")
        dynamics = generator.dynamics
        if dynamics is not None:
            for field in ("xd", "xq"):
                value = getattr(dynamics, field)
                if not _finite(value) or value <= 0:
                    reasons.append(
                        f"generator {generator.name}: dynamics.{field}={value} must be finite and > 0"
                    )


def _audit_loads(inline: Any, case: Case, reasons: list[str]) -> None:
    for load in inline.loads:
        if not _finite(load.kw):
            reasons.append(f"load {load.id}: kw={load.kw} must be finite")
        if load.q_kvar is not None and not _finite(load.q_kvar):
            reasons.append(f"load {load.id}: q_kvar={load.q_kvar} must be finite")
    for load in case.loads:
        if not _finite(load.kw):
            reasons.append(f"load overlay {load.id}: kw={load.kw} must be finite")


def _audit_shunts(inline: Any, reasons: list[str]) -> None:
    for shunt in inline.shunts:
        if not _finite(shunt.q_mvar):
            reasons.append(f"shunt {shunt.name}: q_mvar={shunt.q_mvar} must be finite")


def _audit_ders(case: Case, reasons: list[str]) -> None:
    for der in case.ders:
        if der.kva is not None and (not _finite(der.kva) or der.kva <= 0):
            reasons.append(f"DER {der.id}: kva={der.kva} must be finite and > 0 when declared")


def audit_perunit(case: Case) -> dict[str, Any]:
    """Audit the Case's declared per-unit/kV bases; PASS when none conflict."""
    reasons: list[str] = []
    inline = case.network.inline if case.network.kind == "inline" else None
    if inline is None:
        scope = (
            f"network.kind='{case.network.kind}' declares no inline per-unit "
            "bases to audit"
        )
        return _record(reasons, scope)

    _audit_buses(inline, reasons)
    _audit_lines(inline, reasons)
    _audit_transformers(inline, reasons)
    _audit_generators(inline, reasons)
    _audit_loads(inline, case, reasons)
    _audit_shunts(inline, reasons)
    _audit_ders(case, reasons)
    return _record(reasons, _SCOPE)


def _record(reasons: list[str], scope: str) -> dict[str, Any]:
    return {
        "schema": _SCHEMA,
        "status": "PASS" if not reasons else "BLOCKED",
        "passed": not reasons,
        "reasons": reasons,
        "scope": scope,
    }


__all__ = ["audit_perunit"]
