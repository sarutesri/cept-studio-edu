"""Read back what OpenDSS actually holds, and compare it to the typed Case.

The source-to-Case receipt in :mod:`cept.fidelity.fidelity` proves the Case
describes the source.  It cannot prove the *engine* was given that Case: the
IEEE-39 tap-side defect lived entirely here, in the compiler, with a Case that
carried ``powerfactory_tap_side`` correctly the whole time.  The turns ratio
was inverted between the Case and the circuit OpenDSS solved, and nothing in
the run said so -- the study simply reported a collapsed transmission side.

So the second leg reads the compiled circuit back through the engine API and
checks the values a study depends on: declared load, line sequence impedance
and its frequency basis, transformer winding voltages, ratings, taps and the
winding the tap sits on, and generator dispatch and rating.  It runs after
``compile_inline`` and before ``Solve()``.
"""

from __future__ import annotations

import math
from typing import Any

_REL = 1e-4  # OpenDSS stores and returns floats it has already rounded.


def _close(a: Any, b: Any, rel: float = _REL) -> bool:
    try:
        x, y = float(a), float(b)
    except (TypeError, ValueError):
        return False
    if not (math.isfinite(x) and math.isfinite(y)):
        return False
    return abs(x - y) <= rel * max(1.0, abs(x), abs(y))


def _row(element, name, quantity, expected, actual, defect_class, reason="") -> dict[str, Any]:
    ok = _close(expected, actual) if isinstance(expected, (int, float)) else expected == actual
    return {
        "element": element,
        "source_locator": f"case:{element}[{name}]",
        "primitive_input": quantity,
        "unit": "",
        "basis_owner": None,
        "basis_value": None,
        "case_field": f"{element}[].{quantity}",
        "engine_attribute": f"OpenDSS {element}.{name}.{quantity}",
        "written_value": expected,
        "read_back_value": actual,
        "status": "pass" if ok else "fail",
        "defect_class": defect_class,
        "reason": "" if ok else reason,
    }


def _active(dss, cls: str, name: str) -> bool:
    return dss.Circuit.SetActiveElement(f"{cls}.{name}") is not None and str(
        dss.CktElement.Name()
    ).lower().endswith(name.lower())


def transformer_rows(dss, inline) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for tr in getattr(inline, "transformers", None) or []:
        try:
            dss.Transformers.Name(tr.name)
            if str(dss.Transformers.Name()).lower() != tr.name.lower():
                rows.append(
                    _row(
                        "transformer",
                        tr.name,
                        "present",
                        tr.name,
                        None,
                        "F4",
                        "the Case declares this transformer but the compiled circuit has no such object",
                    )
                )
                continue
            dss.Transformers.Wdg(1)
            hv_kv, hv_kva, hv_tap = (dss.Transformers.kV(), dss.Transformers.kVA(), dss.Transformers.Tap())
            dss.Transformers.Wdg(2)
            lv_kv, lv_tap = dss.Transformers.kV(), dss.Transformers.Tap()
        except Exception as exc:  # pragma: no cover - engine-shape guard
            rows.append(
                _row(
                    "transformer",
                    tr.name,
                    "readable",
                    True,
                    False,
                    "F4",
                    f"OpenDSS did not return this transformer: {exc}",
                )
            )
            continue
        rows.append(
            _row(
                "transformer",
                tr.name,
                "hv_kv",
                tr.hv_kv,
                hv_kv,
                "F3",
                "winding 1 must carry the HV rating; a swapped winding order inverts the ratio",
            )
        )
        rows.append(
            _row("transformer", tr.name, "lv_kv", tr.lv_kv, lv_kv, "F3", "winding 2 must carry the LV rating")
        )
        rows.append(
            _row(
                "transformer",
                tr.name,
                "kva",
                tr.mva * tr.parallel_units * 1000.0,
                hv_kva,
                "F4",
                "the compiled rating must include the parallel units the source declared",
            )
        )
        # F3: the tap must sit on the winding the source named, and the other
        # winding must be at unity.  Reading both is what separates "the tap is
        # right" from "the tap is right on the wrong winding".
        from cept.adapters.opendss.network import transformer_tap_winding

        on_hv = transformer_tap_winding(tr) == 1
        expected_hv_tap = tr.tap_pu if on_hv else 1.0
        expected_lv_tap = 1.0 if on_hv else tr.tap_pu
        rows.append(
            _row(
                "transformer",
                tr.name,
                "tap_winding_1",
                expected_hv_tap,
                hv_tap,
                "F3",
                f"the source declares tap_side={tr.powerfactory_tap_side}; "
                "winding 1 does not hold the ratio that implies",
            )
        )
        rows.append(
            _row(
                "transformer",
                tr.name,
                "tap_winding_2",
                expected_lv_tap,
                lv_tap,
                "F3",
                f"the source declares tap_side={tr.powerfactory_tap_side}; "
                "winding 2 does not hold the ratio that implies",
            )
        )
    return rows


def line_rows(dss, inline, frequency_hz: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in getattr(inline, "lines", None) or []:
        if getattr(line, "matrix", None):
            continue  # a composite matrix line has no scalar sequence inputs
        try:
            dss.Lines.Name(line.name)
            if str(dss.Lines.Name()).lower() != line.name.lower():
                continue
            r1, x1, c1 = dss.Lines.R1(), dss.Lines.X1(), dss.Lines.C1()
            length = dss.Lines.Length()
        except Exception:  # pragma: no cover - engine-shape guard
            continue
        # OpenDSS returns per-unit-length values in the units the line declared.
        rows.append(
            _row(
                "line",
                line.name,
                "r1_ohm_per_km",
                line.r1_ohm_per_km,
                r1,
                "F1",
                "series resistance differs from the Case",
            )
        )
        rows.append(
            _row(
                "line",
                line.name,
                "x1_ohm_per_km",
                line.x1_ohm_per_km,
                x1,
                "F1",
                "series reactance differs from the Case",
            )
        )
        rows.append(
            _row(
                "line",
                line.name,
                "length_km",
                line.length_km,
                length,
                "F4",
                "length scales the whole branch impedance",
            )
        )
        # F1: OpenDSS stores capacitance, so the susceptance the study sees is
        # C at the circuit's base frequency.  The Case states susceptance at the
        # line type's own frequency.  Comparing them is the only way to catch a
        # basis substitution that survived ingest.
        expected_c = (
            line.b1_us_per_km / (2.0 * math.pi * frequency_hz) if getattr(line, "b1_us_per_km", None) else 0.0
        )
        rows.append(
            _row(
                "line",
                line.name,
                "c1_nf_per_km",
                expected_c * 1000.0,
                c1,
                "F1",
                "shunt capacitance does not restate the Case susceptance "
                "at the circuit base frequency; a basis substitution "
                "shows up here and nowhere else",
            )
        )
    return rows


def generator_rows(dss, inline) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for gen in getattr(inline, "generators", None) or []:
        if gen.bus_type == "slack":
            continue  # compiled as the circuit source, not a Generator object
        try:
            dss.Generators.Name(gen.name)
            if str(dss.Generators.Name()).lower() != gen.name.lower():
                rows.append(
                    _row(
                        "generator",
                        gen.name,
                        "present",
                        gen.name,
                        None,
                        "F4",
                        "the Case declares this machine but the compiled circuit has no such object",
                    )
                )
                continue
            kw, kva = dss.Generators.kW(), dss.Generators.kVA()
        except Exception:  # pragma: no cover - engine-shape guard
            continue
        rows.append(
            _row(
                "generator", gen.name, "kw", gen.kw, kw, "F3", "dispatched active power differs from the Case"
            )
        )
        rows.append(
            _row(
                "generator",
                gen.name,
                "kva",
                gen.mva * getattr(gen, "parallel_units", 1) * 1000.0,
                kva,
                "F4",
                "the compiled rating must include the parallel machines the source declared",
            )
        )
    return rows


def total_power_rows(dss, inline) -> list[dict[str, Any]]:
    """Declared load must survive compilation in total, not just per element."""
    loads = getattr(inline, "loads", None) or []
    declared_kw = sum(float(load.kw or 0.0) for load in loads)
    # A load may state reactive power directly or imply it through a power
    # factor; OpenDSS is given whichever the Case actually carries, so the
    # expected total has to be built the same way rather than assuming the
    # explicit field is always present.
    declared_kvar = 0.0
    for load in loads:
        explicit = getattr(load, "q_kvar", None)
        if explicit is not None:
            declared_kvar += float(explicit)
            continue
        pf = float(getattr(load, "pf", 0.0) or 0.0)
        kw = float(load.kw or 0.0)
        if 0.0 < pf < 1.0:
            declared_kvar += kw * math.tan(math.acos(pf))
    total_kw = total_kvar = 0.0
    try:
        names = list(dss.Loads.AllNames() or [])
        for name in names:
            if str(name).lower() == "none":
                continue
            dss.Loads.Name(name)
            total_kw += float(dss.Loads.kW() or 0.0)
            total_kvar += float(dss.Loads.kvar() or 0.0)
    except Exception:  # pragma: no cover - engine-shape guard
        return []
    return [
        _row(
            "study",
            "total_load",
            "kw",
            declared_kw,
            total_kw,
            "F4",
            "the compiled circuit does not carry the load the Case declares",
        ),
        _row(
            "study",
            "total_load",
            "kvar",
            declared_kvar,
            total_kvar,
            "F4",
            "the compiled circuit does not carry the reactive load the Case declares",
        ),
    ]


def readback_rows(dss, net) -> list[dict[str, Any]]:
    """Every engine-side receipt row for a compiled inline network."""
    inline = getattr(net, "inline", None)
    if inline is None:
        return []
    frequency_hz = float(getattr(net, "frequency_hz", 60.0) or 60.0)
    rows: list[dict[str, Any]] = []
    rows.extend(transformer_rows(dss, inline))
    rows.extend(line_rows(dss, inline, frequency_hz))
    rows.extend(generator_rows(dss, inline))
    rows.extend(total_power_rows(dss, inline))
    return rows


__all__ = [
    "generator_rows",
    "line_rows",
    "readback_rows",
    "total_power_rows",
    "transformer_rows",
]
