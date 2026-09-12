"""OpenDSS adapter — experiment actions."""

from __future__ import annotations

from typing import Iterable

from cept.schema.case import ExperimentAction


def apply_experiment_actions(dss, actions: Iterable[ExperimentAction]) -> None:
    for a in actions:
        if a.kind == "fault":
            ph = a.params.get("phases", 3)
            r = a.params.get("r", 0.001)
            bus = a.params.get("bus", a.target)
            conn = ".1.2.3" if ph >= 3 else f".{a.params.get('phase', 1)}"
            dss.Text.Command(f"New Fault.exp_{a.target} bus1={bus}{conn} phases={ph} r={r}")
        elif a.kind == "open":
            dss.Text.Command(f"Open Line.{a.target} term={a.params.get('term', 1)}")
        elif a.kind == "close":
            dss.Text.Command(f"Close Line.{a.target} term={a.params.get('term', 1)}")
        elif a.kind == "trip_gen":
            dss.Text.Command(f"Generator.{a.target}.enabled=no")
        elif a.kind == "shed_load":
            dss.Text.Command(f"Load.{a.target}.enabled=no")
        elif a.kind == "set_tap":
            tap = a.params.get("tap")
            dss.Text.Command(f"Transformer.{a.target}.wdg=2 Tap={tap}")
