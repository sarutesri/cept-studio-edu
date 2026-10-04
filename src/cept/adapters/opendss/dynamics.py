"""OpenDSS adapter — dynamics (electromechanical / RMS transient)."""

from __future__ import annotations

from typing import Iterable, Optional

from cept.schema.case import Case
from cept.schema.result import (
    MonitorChannel,
    MonitorTrace,
    DynamicsResult,
    SLDSnapshot,
)

from cept.adapters.opendss.network import load_network
from cept.adapters.opendss.der import apply_ders, apply_opender_snapshot
from cept.adapters.opendss.load_flow import _snapshot_refine_ok, _solve, extract_load_flow
from cept.adapters.opendss.utils import dss_object, dss_quote
from cept.semantics.registry import result_channel_descriptor


def generator_names(dss) -> list[str]:
    return list(dss.Generators.AllNames() or [])


def setup_dynamic_monitors(dss, case: Case) -> list[tuple[str, str, int]]:
    """Create monitors for every generator (state, V/I, P/Q) and for any
    requested buses. Returns [(monitor_name, element, mode)]."""
    mons: list[tuple[str, str, int]] = []
    for g in generator_names(dss):
        # Mode 65 = power (mode 1) + positive-sequence/average adder (64).
        # Keep the phase-power monitor for audit/debug, and add the explicit
        # positive-sequence channel needed for source-PFD semantic mapping.
        for suffix, mode in (("st", 3), ("vi", 0), ("pq", 1), ("pqseq", 65), ("iseq", 112)):
            mname = f"cept_{g}_{suffix}"
            dss.Text.Command(
                f"New {dss_object('Monitor', mname)} "
                f"Element={dss_object('Generator', g)} Terminal=1 "
                f"Mode={mode} ppolar=no"
            )
            mons.append((mname, f"Generator.{g}", mode))
    # The reference machine is compiled as a Vsource (see opendss/network.py),
    # so the Generator loop above never sees it and the source's terminal
    # measurements for that machine had no counterpart at all.  A Vsource has
    # no rotor, so mode 3 is genuinely inapplicable -- but its terminal power,
    # voltage and current are real measurements OpenDSS will report.
    inline = case.network.inline if case.network.kind == "inline" else None
    if inline is not None:
        from cept.adapters.opendss.network import inline_asset_identities

        native_by_asset = {
            asset.canonical_id: asset.engine_native_name for asset in inline_asset_identities(case.network)
        }
        for gen in inline.generators:
            if gen.bus_type != "slack":
                continue
            native = native_by_asset.get(gen.name, "")
            if not native.startswith("Vsource."):
                continue
            for suffix, mode in (("pqseq", 65), ("iseq", 112)):
                mname = f"cept_{gen.name}_{suffix}"
                dss.Text.Command(
                    f"New {dss_object('Monitor', mname)} "
                    f"Element={dss_quote(native)} Terminal=1 "
                    f"Mode={mode} ppolar=no"
                )
                mons.append((mname, native, mode))
    for pv in dss.PVsystems.AllNames() or []:
        for suffix, mode in (("st", 3), ("vi", 0), ("pq", 1)):
            mname = f"cept_pv_{pv}_{suffix}"
            dss.Text.Command(
                f"New {dss_object('Monitor', mname)} "
                f"Element={dss_object('PVSystem', pv)} Terminal=1 "
                f"Mode={mode} ppolar=no"
            )
            mons.append((mname, f"PVSystem.{pv}", mode))
    if case.dynamics:
        for bus in case.dynamics.monitor_buses:
            elem = _element_at_bus(dss, bus)
            if elem:
                mname = f"cept_bus_{bus}"
                dss.Text.Command(
                    f"New {dss_object('Monitor', mname)} Element={dss_quote(elem[0])} "
                    f"Terminal={elem[1]} Mode=0 ppolar=no"
                )
                mons.append((mname, elem[0], 0))
    return mons


def _element_at_bus(dss, bus: str):
    """Find a Line terminating at bus -> (element_name, terminal)."""
    b = bus.lower()
    i = dss.Lines.First()
    while i:
        name = dss.Lines.Name()
        if dss.Lines.Bus2().split(".")[0].lower() == b:
            return (f"Line.{name}", 2)
        if dss.Lines.Bus1().split(".")[0].lower() == b:
            return (f"Line.{name}", 1)
        i = dss.Lines.Next()
    return None


def _solve_initial_snapshot(dss, case: Case) -> dict[str, object]:
    """Solve only the pre-dynamics snapshot with the safe precision policy.

    A PQ-only snapshot can use the refined OpenDSS convergence pass without
    changing the tolerances of any subsequent dynamic step.  PV-controlled
    networks stay on the robust default because their control loop can
    oscillate under a tight tolerance.  Keeping this decision in one helper
    makes the initialization contract explicit and testable.
    """
    refine = _snapshot_refine_ok(case)
    _solve(dss, refine=refine)
    if not dss.Solution.Converged():
        dss.Text.Command("Solve mode=direct")
    return {
        "policy": "snapshot-refine-v1" if refine else "snapshot-default-v1",
        "refine": refine,
        "dynamic_steps_refined": False,
    }


def _line_endpoints(case: Case, target: str) -> tuple[str, str] | None:
    inline = case.network.inline if case.network.kind == "inline" else None
    if inline is None:
        return None
    line = next((item for item in inline.lines if item.name.lower() == target.lower()), None)
    return (line.from_bus, line.to_bus) if line is not None else None


def _disable_matching_element(dss, case: Case, target: str) -> bool:
    """Disable a Generator/PVSystem/Storage element by Case-level name.

    DER elements are emitted as ``PVSystem.<id>`` / ``Storage.<id>`` (der.py);
    the Generator path covers synchronous machines.  Returns True when a
    matching element was disabled, False when the target matches nothing
    (callers keep the historic Generator fallback).
    """
    lid = str(target).lower()
    inline = case.network.inline if case.network.kind == "inline" else None
    if inline is not None:
        for gen in inline.generators:
            if gen.name.lower() == lid:
                dss.Text.Command(f"Edit {dss_object('Generator', gen.name)} enabled=no")
                return True
    for der in case.ders:
        if der.id.lower() != lid:
            continue
        if der.type == "pv":
            cls = "PVSystem"
        elif der.type == "storage" and der.opender is not None and der.opender.model == "bess":
            cls = "Storage"
        else:
            cls = "Generator"
        dss.Text.Command(f"Edit {dss_object(cls, der.id)} enabled=no")
        return True
    return False


def apply_dynamic_event(dss, ev, case: Case) -> None:
    p = ev.params
    if ev.kind == "fault":
        ph = p.get("phases", 3)
        r = p.get("r", p.get("R_f", 0.001))
        conn = ".1.2.3" if ph >= 3 else f".{p.get('phase', 1)}"
        fault_name = dss_object("Fault", f"cept_dyn_{ev.target}")
        endpoints = _line_endpoints(case, ev.target)
        if endpoints is not None:
            bus1, bus2 = endpoints
            dss.Text.Command(
                f"New {fault_name} Bus1={dss_quote(bus1 + conn)} "
                f"Bus2={dss_quote(bus2 + conn)} Phases={ph} R={r}"
            )
        else:
            dss.Text.Command(f"New {fault_name} Bus1={dss_quote(ev.target + conn)} Phases={ph} R={r}")
    elif ev.kind == "clear_fault":
        dss.Text.Command(f"Disable {dss_object('Fault', f'cept_dyn_{ev.target}')}")
    elif ev.kind == "open":
        # PowerFactory EvtSwitch commonly clears a preceding line EvtShc at
        # the same target. Disable the corresponding temporary Fault first so
        # the opened line is not left shorted across its two buses.
        dss.Text.Command(f"Disable {dss_object('Fault', f'cept_dyn_{ev.target}')}")
        dss.Text.Command(f"Open {dss_object('Line', ev.target)} term={p.get('term', 1)}")
    elif ev.kind == "close":
        dss.Text.Command(f"Close {dss_object('Line', ev.target)} term={p.get('term', 1)}")
    elif ev.kind == "trip_gen":
        # The target may be a synchronous Generator or an OpenDER-coupled
        # PV/BESS DER (emitted as PVSystem.<id> / Storage.<id>). Disable the
        # matching element class so a tripped DER is removed from the solve
        # but still appears greyed on the SLD (T-013c).
        if not _disable_matching_element(dss, case, ev.target):
            dss.Text.Command(f"Edit {dss_object('Generator', ev.target)} enabled=no")
    elif ev.kind == "load_step":
        load = dss_object("Load", ev.target)
        kw = p.get("kw")
        if kw is None:
            # Scale the currently-applied kW by `mult` (a load-shed factor).
            dss.Circuit.SetActiveElement(load)
            kw = round(dss.Loads.kW() * p.get("mult", 1.0), 4)
        dss.Text.Command(f"Edit {load} kW={kw}")


def _annotate_event_sld(sld, ev) -> None:
    """Stamp an SLD with the presentation marker for one dynamics event.

    The solver owns topology/values; this adds event semantics only: a fault on
    a bus/node, a tripped generator (lowered output), a shed load, an opened or
    re-closed branch.  Device lookup is by the Case-level target name so the
    marker survives the OpenDSS element renaming.
    """
    from cept.adapters.opendss.sld import SLDEvent

    lid = (ev.target or "").lower()
    if not lid:
        return
    if ev.kind == "fault":
        for n in sld.nodes:
            if n.id.lower() == lid:
                n.event = SLDEvent(kind="fault", label=ev.label or "fault")
    elif ev.kind == "trip_gen":
        for n in sld.nodes:
            for g in n.gens:
                if g.name.lower() == lid:
                    g.tripped = True
                    g.kw = 0.0
                    g.kvar = 0.0
    elif ev.kind == "load_step":
        for n in sld.nodes:
            for ld in n.loads:
                if ld.name.lower() == lid:
                    ld.shed = True
    elif ev.kind == "open":
        for e in sld.edges:
            if e.id.lower() == lid or e.id.lower().split(".", 1)[-1] == lid:
                e.status = "open"
    elif ev.kind == "close":
        for e in sld.edges:
            if e.id.lower() == lid or e.id.lower().split(".", 1)[-1] == lid:
                e.status = "closed"


def read_monitor(
    dss,
    name: str,
    *,
    nominal_frequency_hz: float = 60.0,
    current_base_a: float | None = None,
    voltage_base_v: float | None = None,
    power_base_w: float | None = None,
    angle_reference_offset_deg: float = 0.0,
    preserve_raw_theta: bool = False,
    frame_contract_v3: bool = False,
) -> Optional[MonitorTrace]:
    dss.Monitors.Name(name)
    if dss.Monitors.Name().lower() != name.lower():
        return None
    header = dss.Monitors.Header()
    if not header:
        return None
    t = [round(h * 3600.0, 6) for h in dss.Monitors.dblHour()]
    element = dss.Monitors.Element()
    mode = dss.Monitors.Mode()
    channels = []
    for i, h in enumerate(header):
        vals = [float(x) for x in dss.Monitors.Channel(i + 1)]
        channel_name = h
        unit = ""
        source_channel = h
        semantic_per_unit_base: str | None = None
        if mode == 3 and h.lower().startswith("frequency"):
            # OpenDSS Mode 3 exposes generator speed relative to synchronous
            # frequency. PowerFactory s:xspeed is the same state in p.u.;
            # retain the raw header and use the Case frequency as conversion.
            # Retain the raw Hz channel for the headline frequency metrics;
            # emit the reviewed p.u. alias separately for source parity.
            channels.append(
                MonitorChannel(
                    name=h,
                    values=[round(v, 5) for v in vals],
                    unit="Hz",
                    source_channel=h,
                    semantics=result_channel_descriptor(
                        "opendss", h, h, unit="Hz"
                    ),
                )
            )
            channel_name, unit = "Speed", "p.u."
            if nominal_frequency_hz <= 0:
                raise ValueError("nominal_frequency_hz must be positive for Mode-3 speed mapping")
            vals = [v / nominal_frequency_hz for v in vals]
            source_channel = f"opendss:mode3:{h}"
        elif mode == 3 and h.lower().startswith("pshaft"):
            # OpenDSS exposes the generator shaft-power state in watts.  The
            # reviewed PowerFactory `s:pt` channel is synchronous-machine
            # p.u.; convert only with the source-disclosed generator active
            # rating (MVA * nominal cosn), never by matching a result value.
            channels.append(
                MonitorChannel(
                    name=h,
                    values=[round(v, 5) for v in vals],
                    unit="W",
                    source_channel=h,
                    semantics=result_channel_descriptor(
                        "opendss", h, h, unit="W"
                    ),
                )
            )
            if power_base_w is not None and power_base_w > 0:
                channel_name, unit = "Turbine Power", "p.u."
                vals = [v / power_base_w for v in vals]
                source_channel = f"opendss:mode3:{h}"
                semantic_per_unit_base = "machine"
            else:
                continue
        elif mode == 3 and h.lower().startswith("theta"):
            # Generator Mode=3 reports the solver-native rotor state.  v1
            # keeps the legacy reader offset; v2 preserves the raw state in
            # the reviewed reference-machine frame; v3 preserves the raw
            # state in the PF network-referenced firot frame.  The latter gets
            # a distinct semantic label so a v3 report cannot accidentally
            # compare firot to PF firel.
            channel_name, unit = (
                ("Network rotor angle", "deg") if frame_contract_v3 else ("Relative rotor angle", "deg")
            )
            if frame_contract_v3:
                if abs(float(angle_reference_offset_deg)) > 1.0e-12:
                    raise ValueError(
                        "source-bound frame contract v3 cannot apply a reader-side Theta offset"
                    )
                source_channel = f"opendss:mode3:{h}; frame_contract=v3; raw=true"
            elif preserve_raw_theta:
                if abs(float(angle_reference_offset_deg)) > 1.0e-12:
                    raise ValueError(
                        "source-bound frame contract v2 cannot apply a reader-side Theta offset"
                    )
                source_channel = f"opendss:mode3:{h}; frame_contract=v2; raw=true"
            else:
                vals = [v + float(angle_reference_offset_deg) for v in vals]
                source_channel = (
                    f"opendss:mode3:{h}"
                    f"; frame_offset_deg={float(angle_reference_offset_deg):.12g}"
                )
        elif mode == 3 and h in {
            "UserModelTheta",
            "ElectricalPowerPU",
            "MechanicalPowerPU",
            "MechanicalTorquePU",
            "InternalVoltagePU",
            "TerminalVoltagePU",
            "PositiveSequenceCurrentPU",
        }:
            # UserModel callback outputs remain visible as solver-side audit
            # channels.  They intentionally stay unreviewed here unless a
            # PowerFactory counterpart and unit/sign contract is declared;
            # the paired parity lane uses the reviewed Mode=3/65/112
            # observables above instead of double-counting callback outputs.
            channel_name, unit = h, "solver-native"
            source_channel = f"opendss:mode3:{h}"
        elif mode == 3 and h in {
            "ElectricalReactivePowerPU",
            "PositiveSequenceCurrentAngleDeg",
            "InternalVoltageAngleDeg",
            "SpeedDerivativeRadPerS2",
            "AngleDerivativeRadPerS",
            "PowerBalanceResidualPU",
        }:
            channel_name, unit = h, "solver-native"
            source_channel = f"opendss:mode3:{h}"
        elif mode == 65:
            # EPRI documents Mode=1 as per-phase kW/kvar and the +64 adder as
            # positive-sequence/average. OpenDSS reports generator terminal
            # power into the monitored element, so the reviewed mapping
            # reverses sign and converts kW/kvar to MW/Mvar. Keep the raw
            # header in source_channel for auditability.
            lower = h.lower()
            if lower.startswith("p1"):
                channel_name, unit = "Positive-sequence, active power", "MW"
            elif lower.startswith("q1"):
                channel_name, unit = "Positive-sequence, reactive power", "Mvar"
            if unit:
                vals = [-v / 1000.0 for v in vals]
                source_channel = f"opendss:mode65:{h}"
                if lower.startswith("p1") and power_base_w is not None and power_base_w > 0:
                    channels.append(
                        MonitorChannel(
                            name="Electrical Power",
                            values=[round(v * 1_000_000.0 / power_base_w, 5) for v in vals],
                            unit="p.u.",
                            source_channel=source_channel,
                            semantics=result_channel_descriptor(
                                "opendss",
                                source_channel,
                                "Electrical Power",
                                unit="p.u.",
                                per_unit_base="machine",
                            ),
                        )
                    )
        elif mode == 112 and h.lower().startswith("v"):
            if voltage_base_v is not None and voltage_base_v > 0:
                channel_name, unit = "Terminal voltage", "p.u."
                vals = [v / voltage_base_v for v in vals]
                source_channel = f"opendss:mode112:{h}"
                semantic_per_unit_base = "machine"
            else:
                unit = "V"
        elif mode == 112 and h.lower().startswith("i"):
            # Mode 112 = positive-sequence current magnitude.  Convert the
            # solver's amperes to p.u. only with the reviewed machine base;
            # never scale to force parity.
            if current_base_a is not None and current_base_a > 0:
                channel_name, unit = "Positive-sequence current, magnitude", "p.u."
                vals = [v / current_base_a for v in vals]
                source_channel = f"opendss:mode112:{h}"
                semantic_per_unit_base = "machine"
            else:
                unit = "A"
        channels.append(
            MonitorChannel(
                name=channel_name,
                values=[round(v, 5) for v in vals],
                unit=unit,
                source_channel=source_channel,
                semantics=result_channel_descriptor(
                    "opendss",
                    source_channel,
                    channel_name,
                    unit=unit,
                    per_unit_base=semantic_per_unit_base,
                ),
            )
        )
    return MonitorTrace(name=name, element=element or "", mode=mode, t=t, channels=channels)


def _capture_initialization(traces: list[MonitorTrace], case: Case) -> dict:
    """Capture reviewed first pre-event monitor samples as solver evidence."""
    inline = case.network.inline if case.network.kind == "inline" else None
    known = {gen.name.lower(): gen.name for gen in (inline.generators if inline else [])}
    # The reference machine is a Vsource, so its element name is not
    # ``Generator.<case name>`` and the name after the dot may be ``source``.
    # The adapter's own identity declaration is what maps it back, the same way
    # the comparator does it -- otherwise this machine's first sample is
    # silently dropped while its trajectory is compared.
    native_to_asset: dict[str, str] = {}
    if inline is not None:
        from cept.adapters.opendss.network import inline_asset_identities

        native_to_asset = {
            asset.engine_native_name.lower(): asset.canonical_id
            for asset in inline_asset_identities(case.network)
            if asset.engine_native_name
        }
    allowed = {
        "Speed",
        "Turbine Power",
        "Electrical Power",
        "Positive-sequence, active power",
        "Positive-sequence, reactive power",
        "Positive-sequence current, magnitude",
        "Terminal voltage",
        "ElectricalReactivePowerPU",
        "MechanicalTorquePU",
        "PositiveSequenceCurrentAngleDeg",
        "InternalVoltageAngleDeg",
        "SpeedDerivativeRadPerS2",
        "AngleDerivativeRadPerS",
        "PowerBalanceResidualPU",
    }
    grouped: dict[str, dict] = {}
    sample_times: list[float] = []
    for trace in traces:
        element = str(trace.element or "")
        if "." not in element or not trace.t:
            continue
        lowered = element.lower()
        if not lowered.startswith(("generator.", "vsource.")):
            continue
        raw_name = element.split(".", 1)[1]
        asset = native_to_asset.get(lowered) or known.get(raw_name.lower(), raw_name)
        channels = grouped.setdefault(asset, [])
        sample_times.append(float(trace.t[0]))
        for channel in trace.channels:
            if channel.name not in allowed or not channel.values:
                continue
            channels.append(
                {
                    "name": channel.name,
                    "source_channel": channel.source_channel or channel.name,
                    "unit": channel.unit,
                    "value": channel.values[0],
                    "semantics": (
                        channel.semantics.model_dump() if channel.semantics is not None else None
                    ),
                }
            )
    generators = [{"name": name, "channels": channels} for name, channels in grouped.items() if channels]
    if not generators:
        return {}
    return {
        "status": "captured",
        "method": "first_pre_event_monitor_sample",
        "source": "OpenDSS monitor first sample before the first scheduled event",
        "time_s": min(sample_times),
        "generators": generators,
    }


def run_dynamics(dss, case: Case, work_dir: str, state: dict, extra_commands: Optional[Iterable[str]] = None):
    from cept.schema.case import DynamicsSpec
    from cept.adapters.opendss.user_models import (
        requested_user_model,
        user_model_initial_states,
        user_model_provenance,
    )

    spec = case.dynamics
    if spec is None:
        spec = DynamicsSpec()
    user_model = requested_user_model(case)
    locked_initial_states = user_model_initial_states(case) if user_model is not None else {}
    frame_contract_v2 = any(
        float(values.get("frame_contract_v2", 0.0)) > 0.5
        for values in locked_initial_states.values()
    )
    frame_contract_v3 = any(
        float(values.get("frame_contract_v3", 0.0)) > 0.5
        for values in locked_initial_states.values()
    )
    source_bound_frame_contract = frame_contract_v2 or frame_contract_v3
    angle_reference_offsets = {
        f"cept_{name}_st".lower(): (
            0.0
            if source_bound_frame_contract
            else float(values.get("angle_reference_offset_deg", 0.0))
        )
        for name, values in locked_initial_states.items()
    }
    if user_model is not None:
        state["_opendss_user_model"] = user_model_provenance(user_model)
        if locked_initial_states:
            state["_opendss_user_model"]["initialization"] = "locked Case state contract"

    inline = case.network.inline
    if case.network.kind == "inline" and inline is not None:
        native_composites = [gen.name for gen in inline.generators if gen.wecc is not None]
        if native_composites:
            raise ValueError(
                "OpenDSS dynamics cannot execute PowerFactory WECC composite "
                "models without a reviewed REGC_A/REEC_A/REPC_A mapping: " + ", ".join(native_composites)
            )
        missing = [gen.name for gen in inline.generators if gen.bus_type != "slack" and gen.dynamics is None]
        if missing:
            raise ValueError(
                "OpenDSS inline dynamics requires explicit source-backed "
                "generator dynamics parameters for: " + ", ".join(missing)
            )

    load_network(
        dss,
        case.network,
        work_dir,
        user_model=user_model,
        user_model_initial_states=locked_initial_states,
    )
    apply_ders(dss, case, state)
    for cmd in extra_commands or ():
        dss.Text.Command(cmd)
    gens = generator_names(dss)
    mons = setup_dynamic_monitors(dss, case)
    current_bases = {}
    voltage_bases = {}
    if inline is not None:
        import math

        current_bases = {
            f"cept_{gen.name}_iseq".lower(): gen.mva * gen.parallel_units * 1000.0 / (math.sqrt(3.0) * gen.kv)
            for gen in inline.generators
            if gen.mva and gen.kv
        }
        voltage_bases = {
            f"cept_{gen.name}_iseq".lower(): gen.kv * 1000.0 / math.sqrt(3.0)
            for gen in inline.generators
            if gen.kv
        }
        power_bases = {
            f"cept_{gen.name}_{suffix}".lower(): gen.mva
            * gen.parallel_units
            * gen.rated_power_factor
            * 1_000_000.0
            for gen in inline.generators
            if gen.mva and gen.rated_power_factor
            for suffix in ("st", "pqseq")
        }
    else:
        power_bases = {}

    from cept.adapters.opendss.sld import build_sld

    state["_dynamics_initial_load_flow_solver"] = _solve_initial_snapshot(dss, case)
    # The pre-disturbance load flow that initialises the simulation is a real
    # solved snapshot, and the source's own RMS run reports one.  Capturing it
    # here is what lets bus voltages, branch flows and losses be compared at
    # all: without it, 66 of 128 cross-engine rows were source-only.
    state["_dynamics_initial_load_flow"] = extract_load_flow(dss, case.network.inline)
    initial_sld = build_sld(dss, case, der_kind=state.get("_der_kind"))
    snapshots = [
        SLDSnapshot(
            id="initial",
            t_s=0.0,
            label="Initial",
            phase="initial",
            solver_provenance={"engine": "opendss", "solver_stage": "solve_load_flow"},
            sld=initial_sld,
        )
    ]

    h = spec.stepsize
    start_time = spec.start_time
    dss.Text.Command(f"Set mode=dynamics h={h} number=1")
    # OpenDSS uses a non-negative simulation clock.  Shift the complete source
    # timeline by -tstart so a source that initializes at -0.1 s and faults at
    # 0 s still has the same 0.1 s pre-event interval; only the comparator's
    # explicit time-origin normalization removes this bookkeeping offset.
    # Monitor samples are emitted after the first integration step, whereas
    # the PowerFactory source contract includes the ComInc sample at tstart.
    # Do not add an extra h here: the event loop already advances one step
    # after applying each event.  Adding h delayed the solver-side fault and
    # clearing responses by one additional sample while the echoed event time
    # remained canonical, creating a real phase residual in cross-engine plots.
    time_offset = -start_time
    dss.Text.Command("Set ControlMode=TIME")

    events = sorted(spec.events, key=lambda e: e.t)
    t_now = 0.0
    ev_echo = []
    dynamic_opender = any(der.opender is not None and der.opender.mode == "dynamic" for der in case.ders)
    if dynamic_opender:
        # OpenDER's public model contract is one input/output exchange per
        # timestep.  Advance OpenDSS one step at a time so the solver remains
        # the source of voltage/state truth and the cached OpenDER instance
        # carries its own model state (not a Python reimplementation).
        state["_opender_dynamic_exchanges"] = []
        event_index = 0
        boundary: list = []
        for ev in events:
            if ev.t + time_offset <= t_now + h * 0.5:
                apply_dynamic_event(dss, ev, case)
                boundary.append(ev)
                ev_echo.append(
                    {"t": ev.t, "kind": ev.kind, "target": ev.target, "label": ev.label or ev.kind}
                )
                event_index += 1
            else:
                break

        def _capture_boundary(applied: list) -> None:
            """Advance one post-event exchange+Solve step, then capture a
            *value-bearing* SLD snapshot annotated with the events just applied.

            The plain OpenDSS branch does the same (solve one post-event step,
            then build the SLD); the extra OpenDER exchange before the solve
            lets the DER react to the post-event network, so a trip or
            ride-through is visible on the SLD instead of a hollow blocked
            placeholder (T-013c).
            """
            if not applied:
                return
            nonlocal t_now
            lf = extract_load_flow(dss, case.network.inline)
            exchanges = apply_opender_snapshot(
                dss, case, lf, work_dir, state, t_s=round(t_now, 9)
            )
            if exchanges:
                state["_opender_dynamic_exchanges"].append(
                    {
                        "t_s": round(t_now, 9),
                        "exchanges": exchanges,
                    }
                )
            dss.Text.Command("Solve number=1")
            t_now += h
            snap = build_sld(dss, case, der_kind=state.get("_der_kind"))
            for ev in applied:
                _annotate_event_sld(snap, ev)
            # One value-bearing snapshot per event id (events applied at the
            # same boundary share the single post-event solved state, so the
            # markers annotate cumulatively on the shared diagram).
            base = len(ev_echo) - len(applied)
            for i, ev in enumerate(applied, 1):
                index_raw = base + i
                snapshots.append(
                    SLDSnapshot(
                        id=f"after-event-{index_raw}",
                        t_s=round(float(ev.t), 6),
                        label=f"After Event {index_raw}: {ev.label or ev.kind}",
                        event_id=f"event-{index_raw}",
                        phase="after_event",
                        status="available",
                        solver_provenance={
                            "engine": "opendss",
                            "solver_stage": "dynamic-event",
                            "event_kind": ev.kind,
                            "target": ev.target,
                            "events_at_boundary": [e.kind for e in applied],
                        },
                        sld=snap,
                    )
                )

        _capture_boundary(boundary)
        boundary = []
        # OpenDER's public exchange contract includes the endpoint sample at
        # t=duration.  Keep that endpoint without shifting ordinary solver
        # event timing by an extra h.
        solver_end_time = spec.duration + h + time_offset
        n_steps = max(0, round((solver_end_time - t_now) / h))
        for _ in range(n_steps):
            lf = extract_load_flow(dss, case.network.inline)
            exchanges = apply_opender_snapshot(dss, case, lf, work_dir, state, t_s=round(t_now, 9))
            if exchanges:
                state["_opender_dynamic_exchanges"].append(
                    {
                        "t_s": round(t_now, 9),
                        "exchanges": exchanges,
                    }
                )
            dss.Text.Command("Solve number=1")
            t_now += h
            while event_index < len(events):
                ev = events[event_index]
                if ev.t + time_offset > t_now + h * 0.5:
                    break
                apply_dynamic_event(dss, ev, case)
                boundary.append(ev)
                ev_echo.append(
                    {"t": ev.t, "kind": ev.kind, "target": ev.target, "label": ev.label or ev.kind}
                )
                event_index += 1
            _capture_boundary(boundary)
            boundary = []
        # Events whose time falls outside the co-sim horizon were not applied
        # and cannot have a value-bearing post-event solve; keep an explicit
        # transparent snapshot for them instead of a silent gap.
        for index_raw in range(event_index + 1, len(events) + 1):
            ev = events[index_raw - 1]
            snapshots.append(
                SLDSnapshot(
                    id=f"after-event-{index_raw}",
                    t_s=round(float(ev.t), 6),
                    label=f"After Event {index_raw}: {ev.label or ev.kind}",
                    event_id=f"event-{index_raw}",
                    phase="after_event",
                    status="blocked",
                    solver_provenance={
                        "engine": "opendss",
                        "solver_stage": "dynamic-event",
                        "reason": "event time beyond the OpenDER co-sim horizon; no post-event solve was performed",
                    },
                )
            )
    else:
        event_number = 0
        event_index = 0
        while event_index < len(events):
            ev = events[event_index]
            solver_event_time = ev.t + time_offset
            n_steps = max(0, round((solver_event_time - t_now) / h))
            if n_steps > 0:
                dss.Text.Command(f"Solve number={n_steps}")
                t_now += n_steps * h
            # Apply all simultaneous IntEvt-equivalent operations before one
            # post-event solve, matching PowerFactory's event boundary.
            boundary: list = []
            while event_index < len(events) and abs(events[event_index].t - ev.t) <= 1.0e-9:
                boundary_ev = events[event_index]
                apply_dynamic_event(dss, boundary_ev, case)
                ev_echo.append(
                    {
                        "t": boundary_ev.t,
                        "kind": boundary_ev.kind,
                        "target": boundary_ev.target,
                        "label": boundary_ev.label or boundary_ev.kind,
                    }
                )
                boundary.append(boundary_ev)
                event_index += 1
            # Advance one post-event step so the solver is re-solved at the
            # disturbance, then capture a *value-bearing* SLD snapshot annotated
            # with that event's marker (fault / tripped gen / shed load / opened
            # or re-closed branch).  Without this, after-event snapshots used to
            # be hollow ``status=blocked`` placeholders with no sld at all.
            dss.Text.Command("Solve number=1")
            t_now += h
            snap = build_sld(dss, case, der_kind=state.get("_der_kind"))
            for boundary_ev in boundary:
                event_number += 1
                _annotate_event_sld(snap, boundary_ev)
                snapshots.append(
                    SLDSnapshot(
                        id=f"after-event-{event_number}",
                        t_s=round(float(boundary_ev.t), 6),
                        label=f"After Event {event_number}: {boundary_ev.label or boundary_ev.kind}",
                        event_id=f"event-{event_number}",
                        phase="after_event",
                        status="available",
                        solver_provenance={
                            "engine": "opendss",
                            "solver_stage": "dynamic-event",
                            "event_kind": boundary_ev.kind,
                            "target": boundary_ev.target,
                            "events_at_boundary": [item.kind for item in boundary],
                        },
                        sld=snap,
                    )
                )
        solver_end_time = spec.duration + time_offset
        n_rest = max(0, round((solver_end_time - t_now) / h))
        if n_rest > 0:
            dss.Text.Command(f"Solve number={n_rest}")

    converged = bool(dss.Solution.Converged())

    traces = []
    for mname, _elem, _mode in mons:
        tr = read_monitor(
            dss,
            mname,
            nominal_frequency_hz=case.network.frequency_hz,
            current_base_a=current_bases.get(mname.lower()),
            voltage_base_v=voltage_bases.get(mname.lower()),
            power_base_w=power_bases.get(mname.lower()),
            angle_reference_offset_deg=angle_reference_offsets.get(mname.lower(), 0.0),
            preserve_raw_theta=source_bound_frame_contract,
            frame_contract_v3=frame_contract_v3,
        )
        if tr is not None and tr.t:
            traces.append(tr)

    metrics = dynamics_metrics(traces, gens)

    result = DynamicsResult(
        converged=converged,
        duration=spec.duration,
        stepsize=h,
        generators=gens,
        events=ev_echo,
        monitors=traces,
        initialization=_capture_initialization(traces, case),
        **metrics,
    )

    # Rebuild the final (post-all-events) SLD and annotate every event marker so
    # the report's headline diagram reflects trips/sheds/opens, not just faults.
    sld = build_sld(dss, case, der_kind=state.get("_der_kind"))
    for ev in events:
        _annotate_event_sld(sld, ev)
    snapshots.append(
        SLDSnapshot(
            id="final",
            t_s=round(float(spec.duration), 6),
            label="Final",
            phase="final",
            solver_provenance={"engine": "opendss", "solver_stage": "solve_dynamics"},
            sld=sld,
        )
    )
    return result, sld, snapshots


def dynamics_metrics(traces, gens) -> dict:
    freqs, angles = [], []
    for tr in traces:
        fc = tr.channel("Frequency")
        if fc and fc.values:
            freqs.extend(fc.values)
        tc = (
            tr.channel("theta")
            or tr.channel("Theta (Deg)")
            or tr.channel("Relative rotor angle")
        )
        if tc and tc.values:
            angles.append(tc.values)
    out: dict = {}
    if freqs:
        out["freq_nadir_hz"] = round(min(freqs), 4)
        out["freq_peak_hz"] = round(max(freqs), 4)
    if angles:
        flat = [v for a in angles for v in a]
        out["max_rotor_angle_deg"] = round(max(abs(v) for v in flat), 3)
        if len(angles) > 1:
            n = min(len(a) for a in angles)
            sep = max(abs(max(a[i] for a in angles) - min(a[i] for a in angles)) for i in range(n))
            out["max_angle_separation_deg"] = round(sep, 3)
        ang_max = out.get("max_rotor_angle_deg", 0)
        stable = ang_max < 180.0
        out["stable"] = stable
        out["settling_note"] = (
            "Rotor angle bounded (<180 deg) — transiently stable."
            if stable
            else "Rotor angle exceeded 180 deg — loss of synchronism (unstable)."
        )
    return out
