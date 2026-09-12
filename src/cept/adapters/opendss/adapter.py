"""OpenDSS adapter implementation delegating to dedicated study modules."""

from __future__ import annotations

import tempfile
from typing import Any, Iterable, Optional, cast

from cept.ports.options import RunOptions
from cept.schema.case import (
    Case,
)
from cept.schema.result import (
    SLDSnapshot,
    StudyResult,
)


class OpenDSSAdapter:
    """Stateless-per-call wrapper around a single OpenDSSDirect context."""

    engine = "opendss"

    def __init__(self) -> None:
        import opendssdirect as dss

        self.dss = dss
        self._work_dir = tempfile.TemporaryDirectory(prefix="cept-opendss-", ignore_cleanup_errors=True)
        import os

        orig = os.getcwd()
        try:
            self.dss.Basic.DataPath(self._work_dir.name)
        finally:
            os.chdir(orig)
            self.dss.Basic.DataPath(orig)
        self._der_kind: dict[str, str] = {}
        self._state: dict = {}

    @property
    def version(self) -> str:
        return self.dss.Basic.Version()

    def run(self, case: Case, options: RunOptions = RunOptions()) -> StudyResult:
        import os

        orig = os.getcwd()
        try:
            self.dss.Basic.DataPath(self._work_dir.name)
            # DSS C-API may change the process working directory as a side effect
            # of DataPath. Keep CEPT's caller cwd stable so relative dss_file
            # Case paths resolve where the user launched the run.
            os.chdir(orig)
            return self._run(case, options.extra_commands, solver=options.solver)
        finally:
            os.chdir(orig)
            self.dss.Basic.DataPath(orig)

    def _run(
        self,
        case: Case,
        extra_commands: Optional[Iterable[str]] = None,
        *,
        solver: str = "native",
    ) -> StudyResult:
        from cept.adapters.opendss.network import load_network
        from cept.adapters.opendss.der import apply_ders, apply_opender_snapshot
        from cept.adapters.opendss.load_flow import _solve, extract_load_flow
        from cept.adapters.opendss.sld import build_sld, mark_before_events
        from cept.adapters.opendss.experiment import apply_experiment_actions
        from cept.adapters.opendss.fault import run_fault
        from cept.adapters.opendss.hosting_capacity import run_hosting_capacity
        from cept.adapters.opendss.dynamics import run_dynamics
        from cept.adapters.opendss.qsts import run_qsts
        from cept.adapters.opendss.harmonics import run_harmonics
        from cept.adapters.opendss.protection import run_protection
        from cept.adapters.opendss.gic import run_gic

        st = case.study.type
        if solver not in {"native", "ybus-nr"}:
            raise ValueError(f"Unknown solver lane '{solver}'.")
        if solver != "native" and st not in {"load_flow", "unbalanced_load_flow"}:
            raise ValueError(f"solver='{solver}' is only available for load_flow, not study.type='{st}'.")

        result = StudyResult(
            study_type=st,
            case_name=case.meta.name,
            case_fingerprint=case.fingerprint(),
            engine=self.engine,
            engine_version=self.version,
            nominal_frequency_hz=getattr(case.network, "frequency_hz", None),
            calculation_method=(
                "direct bolted-fault calculation (OpenDSS)"
                if st == "fault"
                else "OpenDSS quasi-static time-series power-flow calculation"
                if st == "qsts"
                else "OpenDSS harmonic frequency-domain calculation"
                if st == "harmonics"
                else "OpenDSS fault calculation plus IEC inverse-time relay coordination"
                if st == "protection"
                else "OpenDSS Dynamics mode (DSS C-API; Generator Model=1 or reviewed Model=6 UserModel)"
                if st in ("dynamics", "dynamics_rms")
                else "CEPT polar Newton-Raphson over OpenDSS-extracted SystemY"
                if solver == "ybus-nr"
                else "OpenDSS phasor-domain power-system calculation"
            ),
        )

        inline_net = getattr(case.network, "inline", None)
        if inline_net is not None:
            from cept.adapters.opendss.network import ideal_ratio_changer_approximations
            from cept.schema.result import ModellingApproximation

            result.modelling_approximations = [
                ModellingApproximation(
                    asset=cast(str, item["asset"]),
                    quantity=cast(str, item["quantity"]),
                    source_value=cast(Optional[float], item.get("source_value")),
                    applied_value=cast(Optional[float], item.get("applied_value")),
                    reason=cast(str, item["reason"]),
                    provenance=cast(str, item.get("provenance", "")),
                )
                for item in ideal_ratio_changer_approximations(inline_net)
            ]

        if st in ("fault", "protection"):
            net = case.network.inline
            if net:
                egs = getattr(net, "external_grids", []) or []
                gens = getattr(net, "generators", []) or []
                eg = egs[0] if egs else None
                slack = next((gen for gen in gens if gen.bus_type == "slack"), None)
                if eg is None and slack is not None:
                    raise ValueError(
                        f"Study type '{st}' is blocked for generator-only slack configurations "
                        "without an external grid because short-circuit strength data is not available."
                    )

        if st in ("load_flow", "unbalanced_load_flow"):
            from cept.adapters.opendss.load_flow import _snapshot_refine_ok

            refine = _snapshot_refine_ok(case)
            load_network(
                self.dss,
                case.network,
                self._work_dir.name,
                regulate_slack_terminal=True,
            )
            apply_ders(self.dss, case, self._state)
            for cmd in extra_commands or ():
                self.dss.Text.Command(cmd)
            if solver == "ybus-nr":
                from cept.adapters.opendss.ybus_power_flow import solve_ybus_power_flow

                load_flow, evidence = solve_ybus_power_flow(self.dss, case)
                result.load_flow = load_flow
                result.extra["ybus_nr"] = evidence
                return result
            _solve(self.dss, refine=refine)
            result.load_flow = extract_load_flow(self.dss, case.network.inline)
            opender_exchanges = apply_opender_snapshot(
                self.dss, case, result.load_flow, self._work_dir.name, self._state
            )
            if opender_exchanges:
                _solve(self.dss, refine=refine)
                result.load_flow = extract_load_flow(self.dss, case.network.inline)
            if self._state.get("_opender_boundary"):
                records = list(self._state["_opender_boundary"])
                snapshot_by_der: dict[Any, dict[str, Any]] = {
                    exchange.get("der"): exchange for exchange in opender_exchanges
                }
                for record in records:
                    exchange = snapshot_by_der.get(record.get("der"))
                    if exchange is not None:
                        record.update({"status": "CO_SIMULATED", "exchange": exchange})
                result.extra["opender"] = records
            sld_before = build_sld(self.dss, case, der_kind=self._state.get("_der_kind"))
            result.sld = sld_before
            result.sld_snapshots.append(
                SLDSnapshot(
                    id="initial",
                    t_s=0.0,
                    label="Initial",
                    phase="initial",
                    solver_provenance={
                        "engine": self.engine,
                        "engine_version": self.version,
                        "solver_stage": "solve_load_flow",
                    },
                    sld=sld_before,
                )
            )
            if case.experiment and case.experiment.actions:
                groups: dict[float, list] = {}
                for index, action in enumerate(case.experiment.actions):
                    # Untimed legacy actions intentionally share one group.
                    key = float(action.time_s) if action.time_s is not None else 0.0
                    groups.setdefault(key, []).append(action)
                from cept.schema.case import Experiment

                cumulative_actions = []
                for event_index, (time_s, actions) in enumerate(sorted(groups.items()), 1):
                    apply_experiment_actions(self.dss, actions)
                    _solve(self.dss, refine=refine)
                    result.load_flow_after = extract_load_flow(self.dss, case.network.inline)
                    cumulative_actions.extend(actions)
                    # Event annotations describe the cumulative state, so the
                    # final tab still shows a line opened by an earlier event.
                    event_experiment = Experiment(name=case.experiment.name, actions=list(cumulative_actions))
                    result.sld_after = build_sld(
                        self.dss, case, experiment=event_experiment, der_kind=self._state.get("_der_kind")
                    )
                    result.sld_snapshots.append(
                        SLDSnapshot(
                            id=f"after-event-{event_index}",
                            t_s=time_s,
                            label=f"After Event {event_index}",
                            phase="after_event",
                            event_id=next((a.event_id for a in actions if a.event_id), None)
                            or f"event-{event_index}",
                            solver_provenance={
                                "engine": self.engine,
                                "engine_version": self.version,
                                "solver_stage": "solve_load_flow",
                                "actions": len(actions),
                            },
                            sld=result.sld_after,
                        )
                    )
                result.experiment_name = case.experiment.name
                mark_before_events(sld_before, case.experiment)
            return result

        if st == "fault":
            result.fault, result.sld = run_fault(self.dss, case, self._work_dir.name, self._state)
            return result

        if st == "hosting_capacity":
            result.hosting_capacity, result.sld = run_hosting_capacity(self.dss, case, self._state)
            return result

        if st in ("dynamics", "dynamics_rms"):
            result.dynamics, result.sld, result.sld_snapshots = run_dynamics(
                self.dss, case, self._work_dir.name, self._state, extra_commands=extra_commands
            )
            # The pre-disturbance snapshot the simulation started from; the
            # source's RMS run reports the same thing, so publishing it makes
            # the network state comparable instead of source-only.
            initial_load_flow = self._state.pop("_dynamics_initial_load_flow", None)
            if initial_load_flow is not None:
                result.load_flow = initial_load_flow
            initial_solver = self._state.pop("_dynamics_initial_load_flow_solver", None)
            if initial_solver is not None:
                result.extra["dynamics_initial_load_flow_solver"] = initial_solver
            from cept.adapters.opendss.dynamics_mapping import dynamic_model_mapping

            result.extra["dynamic_model_mapping"] = dynamic_model_mapping(case)
            if self._state.get("_opendss_user_model"):
                result.extra["dynamic_model_mapping"]["runtime"] = self._state["_opendss_user_model"]
            if self._state.get("_opender_boundary"):
                records = list(self._state["_opender_boundary"])
                exchanges = list(self._state.get("_opender_dynamic_exchanges", []))
                dynamic_by_der: dict[str, list[dict[str, Any]]] = {}
                for step in exchanges:
                    for exchange in step.get("exchanges", []):
                        dynamic_by_der.setdefault(str(exchange.get("der")), []).append(
                            {
                                "t_s": step.get("t_s"),
                                "exchange": exchange,
                            }
                        )
                for record in records:
                    matched: list[dict[str, Any]] = dynamic_by_der.get(str(record.get("der")), [])
                    if matched:
                        record.update({"status": "CO_SIMULATED", "exchanges": matched})
                result.extra["opender"] = records
            return result

        if st == "qsts":
            result.time_series, result.sld = run_qsts(
                self.dss, case, self._work_dir.name, self._state, extra_commands=extra_commands
            )
            if self._state.get("_opender_boundary"):
                records = list(self._state["_opender_boundary"])
                exchanges = list(self._state.get("_opender_qsts_exchanges", []))
                qsts_by_der: dict[str, list[dict[str, Any]]] = {}
                for step in exchanges:
                    for exchange in step.get("exchanges", []):
                        qsts_by_der.setdefault(str(exchange.get("der")), []).append(
                            {
                                "t_s": step.get("t_s"),
                                "exchange": exchange,
                            }
                        )
                for record in records:
                    qsts_matched: list[dict[str, Any]] = qsts_by_der.get(str(record.get("der")), [])
                    if qsts_matched:
                        record.update({"status": "CO_SIMULATED", "exchanges": qsts_matched})
                result.extra["opender"] = records
            return result

        if st == "harmonics":
            result.harmonics, result.sld = run_harmonics(self.dss, case, self._state)
            return result

        if st == "protection":
            result.protection, result.sld = run_protection(self.dss, case, self._work_dir.name, self._state)
            return result

        if st == "gic":
            result.gic, result.sld = run_gic(self.dss, case, self._state)
            return result

        raise NotImplementedError(f"study.type='{st}' not implemented yet.")

    def solve_load_flow(
        self,
        case: Case,
        extra_commands: Optional[Iterable[str]] = None,
        solver: str = "native",
    ):
        from cept.adapters.opendss.load_flow import solve_load_flow
        import os

        orig = os.getcwd()
        try:
            self.dss.Basic.DataPath(self._work_dir.name)
            return solve_load_flow(self.dss, case, extra_commands, solver=solver)
        finally:
            os.chdir(orig)
            self.dss.Basic.DataPath(orig)

    def asset_identities(self, case: Case) -> list:
        """Implement :class:`~cept.ports.engine.SupportsIdentity`.

        The module that emits ``New Line.<name>`` is the only one entitled to
        say what the object is called, so this delegates to the same
        ``inline_asset_identities`` the comparator used to reach by a direct
        import from ``semantics.identity`` (boundary leak, closed WP11-followup).
        """
        from cept.adapters.opendss.network import inline_asset_identities

        if getattr(case.network, "kind", "") != "inline":
            return []
        return inline_asset_identities(case.network)

    def ybus_system(self, case: Case) -> Any:
        """Implement :class:`~cept.ports.engine.SupportsYbusLane`.

        Compiles the passive-Ybus snapshot the explainable ybus-nr lane
        drives.  All engine state changes (DataPath, cwd) are confined to the
        adapter's own try/finally seam, so a caller reuses the circuit without
        leaking a temporary cwd or DataPath into the rest of the process.
        """
        from cept.adapters.opendss.der import apply_ders
        from cept.adapters.opendss.network import load_network
        from cept.adapters.opendss.ybus_power_flow import extract_passive_ybus
        import os

        inline = case.network.inline
        if inline is None:
            raise ValueError("Ybus lane requires an inline network.")
        orig = os.getcwd()
        try:
            self.dss.Basic.DataPath(self._work_dir.name)
            load_network(self.dss, case.network, self._work_dir.name, regulate_slack_terminal=True)
            apply_ders(self.dss, case, {})
            return extract_passive_ybus(self.dss, inline)
        finally:
            os.chdir(orig)
            self.dss.Basic.DataPath(orig)

    def _compile_inline(self, net):
        """Backward-compatible method for inline network compilation."""
        from cept.adapters.opendss.network import compile_inline

        result = compile_inline(self.dss, net)
        if result is not None:
            self._inline_layout = result
        return result
