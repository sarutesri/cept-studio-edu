"""Named seam: Case -> OpenDSS command translation for the DSS exporter.

The exporter (:mod:`cept.export.dss_script`) must not reach into adapter
internals (several helpers here are underscore-prefixed); it consumes this
seam instead, so the handoff package cannot drift away from the circuit
``cept run`` actually solved.  This module is the adapter side of that
boundary and the only place the translation functions are collected.

``BusKvSource`` inverts the historical live-engine lookup inside the
exporter: the exporter takes a source, never opens a solver itself.
"""

from __future__ import annotations

import math
from typing import Protocol, runtime_checkable

from cept.adapters.opendss.dynamics_mapping import generator_dynamic_properties
from cept.adapters.opendss.network import (
    _transformer_phase_refs,
    matrix_line_commands,
    transformer_tap_option,
)
from cept.adapters.opendss.utils import (
    aggregate_parallel_generator,
    inline_load_model_options,
    transformer_core_options,
)
from cept.schema.case import Case

# Builtin feeder master paths (absolute, so the exported script always works
# regardless of working directory) — shared with the exporter.
from importlib.resources import files
from pathlib import Path

TESTSYSTEMS = Path(str(files("cept").joinpath("testsystems")))
BUILTIN_PATH = {
    "ieee13": TESTSYSTEMS / "ieee13" / "IEEE13Nodeckt.dss",
    "ieee34": TESTSYSTEMS / "ieee34" / "master.dss",
    "ieee123": TESTSYSTEMS / "ieee123" / "master.dss",
    "cigre_lv": TESTSYSTEMS / "cigre_lv" / "master_snapshot.dss",
    "kundur": TESTSYSTEMS / "kundur" / "kundur_run.dss",
    "gic": TESTSYSTEMS / "gic" / "GIC_Example.dss",
}


@runtime_checkable
class BusKvSource(Protocol):
    """Resolves ``{bus_lower: kV_LL}`` for a Case's base network."""

    def resolve(self, case: Case) -> dict[str, float]: ...


class LiveDssBusKvSource:
    """Default implementation: load the base network in a throw-away OpenDSS
    instance and return {bus_lower: kV_LL} so every DER gets a real voltage.

    Kept out of the exporter so the export package never opens a solver
    itself; callers may inject any deterministic source instead.
    """

    def resolve(self, case: Case) -> dict[str, float]:
        try:
            import opendssdirect as dss

            net = case.network
            master = BUILTIN_PATH.get(net.name or "") if net.kind == "builtin" else Path(net.path or "")
            if not master or not Path(master).exists():
                return {}
            dss.Text.Command("Clear")
            dss.Text.Command(f'Redirect "{master}"')
            dss.Text.Command("Solve")
            kv_map: dict[str, float] = {}
            for bus in dss.Circuit.AllBusNames():
                dss.Circuit.SetActiveBus(bus)
                kv_ln = dss.Bus.kVBase()
                # Always return LL for 3-phase, LN for single-phase
                kv_map[bus.lower()] = round(kv_ln * math.sqrt(3), 4)
            return kv_map
        except Exception:
            return {}


__all__ = [
    "BUILTIN_PATH",
    "BusKvSource",
    "LiveDssBusKvSource",
    "_transformer_phase_refs",
    "aggregate_parallel_generator",
    "generator_dynamic_properties",
    "inline_load_model_options",
    "matrix_line_commands",
    "transformer_core_options",
    "transformer_tap_option",
]
