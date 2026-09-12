"""Study models: network spec, DERs, loads, dynamics, studies, experiments, standards.

Extracted from `cept.schema.case` (mechanical move, no behavior change);
re-exported by the package so the old import paths keep working.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

from cept.intent.schema import Intent
from cept.schema.case.network import GridSource, InlineNetwork


class NetworkSpec(BaseModel):
    """Where the network model comes from.

    ``kind='dss_file'`` compiles an existing OpenDSS model (OpenDSS only).
    ``kind='builtin'`` loads a bundled test feeder by name (OpenDSS only).
    ``kind='inline'`` is the engine-neutral structured builder: it works on
    both OpenDSS and PowerFactory, which is what a cross-engine study needs.
    """

    kind: Literal["dss_file", "builtin", "inline"] = "dss_file"
    path: Optional[str] = Field(None, description="Path to the OpenDSS master .dss file (kind='dss_file').")
    name: Optional[str] = Field(
        None, description="Name of a bundled test feeder (kind='builtin'), e.g. 'ieee13'."
    )
    inline: Optional[InlineNetwork] = Field(
        None, description="Structured, engine-neutral network (kind='inline')."
    )
    frequency_hz: float = Field(50.0, gt=0, description="System base frequency.")
    source: GridSource = Field(default_factory=GridSource)

    @model_validator(mode="after")
    def _check_source_pointer(self) -> "NetworkSpec":
        if self.kind == "dss_file" and not self.path:
            raise ValueError("network.kind='dss_file' requires network.path")
        if self.kind == "builtin" and not self.name:
            raise ValueError("network.kind='builtin' requires network.name")
        if self.kind == "inline" and not self.inline:
            raise ValueError("network.kind='inline' requires network.inline")
        return self


# --------------------------------------------------------------------------- #
# DERs / loads (used by inline networks and as overlays on a base model)
# --------------------------------------------------------------------------- #


class InverterControl(BaseModel):
    mode: Literal["grid_following", "grid_forming"] = "grid_following"
    pf: float = Field(1.0, ge=-1.0, le=1.0, description="Power factor (grid-following).")
    # IEEE 1547 smart-inverter + grid-forming/following functions.
    #   constant_pf / volt_var / volt_watt / volt_var_volt_watt : quasi-static
    #   grid_forming : OpenDSS InvControl mode=GFM (quasi-static voltage source)
    #   gfl_dynamic  : grid-following inverter with DynamicExp current control
    #                  (use in a dynamics study)
    control: Literal[
        "constant_pf",
        "volt_var",
        "volt_watt",
        "volt_var_volt_watt",
        "grid_forming",
        "gfl_dynamic",
    ] = Field("constant_pf", description="Inverter control mode.")
    p_droop: Optional[float] = Field(None, description="P-f droop (grid-forming), pu.")
    q_droop: Optional[float] = Field(None, description="Q-V droop (grid-forming), pu.")
    standard: Optional[str] = Field(None, description="e.g. 'IEEE1547-2018'.")


class OpenDERSpec(BaseModel):
    """Pinned, optional OpenDER model boundary for one PV/BESS DER.

    OpenDER is deliberately not a base dependency and this object does not
    replace the network solver.  A missing runtime therefore blocks a Case
    that explicitly opts in instead of silently falling back to another model.
    """

    model_config = ConfigDict(extra="forbid")

    model: Literal["pv", "bess"]
    mode: Literal["snapshot", "qsts", "dynamic"] = "snapshot"
    source_repo: str = "https://github.com/epri-dev/OpenDER"
    source_commit: str = Field(..., min_length=7)
    interface_repo: str = "https://github.com/epri-dev/OpenDER_interface"
    interface_commit: str | None = Field(None, min_length=7)
    model_version: str = Field(..., min_length=1)
    nominal_power_kw: float = Field(..., gt=0)
    timestep_s: float | None = Field(None, gt=0)
    as_file_path: str | None = Field(
        None, description="Pinned OpenDER applied-settings CSV, relative to the Case."
    )
    model_file_path: str | None = Field(
        None, description="Pinned OpenDER model-parameters CSV, relative to the Case."
    )

    @model_validator(mode="after")
    def _qsts_and_dynamic_need_step(self) -> "OpenDERSpec":
        if self.mode in {"qsts", "dynamic"} and self.timestep_s is None:
            raise ValueError(f"OpenDER {self.mode} mode requires timestep_s")
        return self


class MachineDynamics(BaseModel):
    """Electromechanical dynamic parameters for a synchronous machine
    (used in OpenDSS Dynamics mode). Defaults are typical for a small DG."""

    h: float = Field(5.0, gt=0, description="Inertia constant H (MW·s/MVA).")
    d: float = Field(1.0, ge=0, description="Damping constant.")
    xd: float = Field(1.0, gt=0, description="Synchronous reactance (pu).")
    xdp: float = Field(0.27, gt=0, description="Transient reactance Xd' (pu).")
    xdpp: float = Field(0.20, gt=0, description="Subtransient reactance Xd'' (pu).")
    mva: Optional[float] = Field(None, description="Machine MVA base (default 1.2*kW).")
    exciter: Optional[str] = Field(
        None,
        description="Named exciter model from the dynamics library "
        "(e.g. 'simple_avr'), or None for constant field.",
    )
    governor: Optional[str] = Field(None, description="Named governor model (e.g. 'simple_gov'), or None.")


class InductionMachine(BaseModel):
    """Induction machine (IndMach012) parameters. kW>0 = motor (load),
    kW<0 = induction generator."""

    h: float = Field(0.5, gt=0, description="Inertia constant (MW·s/MVA).")
    d: float = Field(1.0, ge=0, description="Damping.")
    conn: Literal["delta", "wye"] = "delta"
    slip: Optional[float] = Field(None, description="Initial slip; auto if None.")


class DER(BaseModel):
    id: str
    type: Literal["pv", "storage", "generator", "wind", "hydro", "syncgen", "indmach"]
    bus: str
    phases: int = Field(3, ge=1, le=3)
    kva: Optional[float] = Field(None, ge=0)
    kw: Optional[float] = Field(None, description="Real power; for indmach <0 = generator.")
    kwh: Optional[float] = Field(None, ge=0, description="Storage energy capacity.")
    profile: Optional[str] = Field(None, description="Named time profile (e.g. irradiance).")
    inverter: Optional[InverterControl] = None
    # Phase-2 dynamic models
    machine: Optional[MachineDynamics] = Field(
        None, description="Synchronous machine dynamics (syncgen/generator)."
    )
    indmach: Optional[InductionMachine] = Field(
        None, description="Induction machine params (type='indmach')."
    )
    opender: Optional[OpenDERSpec] = Field(
        None, description="Optional pinned OpenDER PV/BESS model; runtime is opt-in and fail-closed."
    )

    @model_validator(mode="after")
    def _opender_kind_matches_der(self) -> "DER":
        if self.opender is not None:
            expected = "storage" if self.opender.model == "bess" else "pv"
            if self.type != expected:
                raise ValueError(
                    f"OpenDER model '{self.opender.model}' must be attached to type='{expected}' DER"
                )
        return self


class Load(BaseModel):
    id: str
    bus: str
    phases: int = Field(3, ge=1, le=3)
    kw: float = Field(..., ge=0)
    pf: float = Field(0.95, gt=0, le=1.0)
    profile: Optional[str] = None


# --------------------------------------------------------------------------- #
# Study
# --------------------------------------------------------------------------- #

StudyType = Literal[
    "load_flow",
    "unbalanced_load_flow",
    "fault",
    "hosting_capacity",
    "dynamics",  # electromechanical (RMS) transient
    "dynamics_rms",  # alias of 'dynamics'
    "qsts",  # quasi-static time series
    "harmonics",  # frequency-domain harmonic sweep
    "protection",  # fault-current relay coordination
    "gic",  # geomagnetically induced current
    "emt",  # external EMT engine contract (not locally solved)
    "economic_dispatch",
    "unit_commitment",
]


# --------------------------------------------------------------------------- #
# Dynamics (electromechanical transient) study
# --------------------------------------------------------------------------- #

DynamicsEventKind = Literal[
    "fault",
    "clear_fault",
    "open",
    "close",
    "trip_gen",
    "load_step",
]


class DynamicsEvent(BaseModel):
    """A timed disturbance during a dynamics simulation."""

    t: float = Field(..., ge=0, description="Event time in seconds.")
    kind: DynamicsEventKind
    target: str = Field("", description="Element or bus the event acts on.")
    params: dict[str, Any] = Field(default_factory=dict)
    label: str = ""


class DynamicsMonitorSignal(BaseModel):
    """Explicit solver result binding for a dynamic/controller signal.

    ``element`` is a stable Case/model binding such as ``G1:TGOV1`` and
    ``channel`` is an explicit PowerFactory ``m:``, ``s:``, or ``c:`` result
    variable.  CEPT never
    guesses either value from a controller name; an unavailable binding is
    reported as blocked by the adapter.
    """

    name: str = Field(..., min_length=1)
    element: str = Field(..., min_length=1)
    channel: str = Field(..., min_length=2)
    unit: str = ""
    source: str = ""

    @model_validator(mode="after")
    def _check_channel(self) -> "DynamicsMonitorSignal":
        if not self.channel.startswith(("m:", "s:", "c:")):
            raise ValueError(
                "dynamic monitor signal channel must be an explicit PowerFactory m:/s:/c: variable"
            )
        return self


class DynamicsSpec(BaseModel):
    """Configuration for a dynamics (RMS transient) study."""

    start_time: float = Field(
        0.0,
        description="Initial-condition time (s); preserve a source ComInc.tstart when declared.",
    )
    stepsize: float = Field(0.001, gt=0, description="Integration step (s).")
    duration: float = Field(5.0, gt=0, description="Total sim time (s).")
    monitor_buses: list[str] = Field(default_factory=list, description="Extra buses to record V/I.")
    monitor_signals: list[DynamicsMonitorSignal] = Field(
        default_factory=list,
        description="Explicit generator/controller result bindings; no name inference.",
    )
    events: list[DynamicsEvent] = Field(default_factory=list)


class EMTSpec(BaseModel):
    """EMT contract; native PowerFactory is demonstrator-only, others need adapters."""

    model_config = ConfigDict(extra="forbid")

    engine: Literal["opendss", "powerfactory", "pscad", "emtp", "openmodelica", "external"]
    model_path: str = Field(..., min_length=1)
    stepsize_s: float = Field(..., gt=0)
    duration_s: float = Field(..., gt=0)
    channels: list[str] = Field(default_factory=list)
    channel_units: dict[str, str] = Field(default_factory=dict)
    channel_sources: dict[str, str] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)
    validation_sources: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_channel_metadata(self) -> "EMTSpec":
        known = set(self.channels)
        for label, mapping in (
            ("channel_units", self.channel_units),
            ("channel_sources", self.channel_sources),
        ):
            unknown = set(mapping) - known
            if unknown:
                raise ValueError(f"{label} contains channels not listed in channels: {sorted(unknown)}")
            if any(not key.strip() or not value.strip() for key, value in mapping.items()):
                raise ValueError(f"{label} keys and values must be non-empty")
        return self


class TimeSeriesProfile(BaseModel):
    """Explicit multiplier data for one or more OpenDSS elements."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    values: list[float] = Field(..., min_length=1)
    interval_s: float = Field(..., gt=0)
    targets: list[str] = Field(..., min_length=1)


class QSTSSpec(BaseModel):
    """Quasi-static time-series configuration.

    Profiles are solver inputs, not generated defaults. Each profile must
    provide one multiplier per simulation step and names existing OpenDSS
    elements (for example ``Load.l1`` or ``PVSystem.pv1``).
    """

    model_config = ConfigDict(extra="forbid")

    stepsize_s: float = Field(..., gt=0)
    duration_s: float = Field(..., gt=0)
    profiles: list[TimeSeriesProfile] = Field(..., min_length=1)
    monitor_buses: list[str] = Field(default_factory=list)


class HarmonicInjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(..., min_length=1, description="Generator/PVSystem element name.")
    magnitudes_pct: dict[int, float] = Field(
        ..., description="Harmonic order to magnitude as percent of fundamental."
    )
    angles_deg: dict[int, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_spectrum(self) -> "HarmonicInjection":
        if not self.magnitudes_pct or any(h < 1 or v < 0 for h, v in self.magnitudes_pct.items()):
            raise ValueError("harmonic magnitudes_pct must contain non-negative orders and values")
        if 1 not in self.magnitudes_pct:
            raise ValueError("harmonic spectrum must include fundamental order 1")
        return self


class HarmonicsSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fundamental_hz: float = Field(..., gt=0)
    injections: list[HarmonicInjection] = Field(..., min_length=1)


class ProtectionRelay(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1)
    pickup_a: float = Field(..., gt=0)
    time_dial: float = Field(1.0, gt=0)
    curve: Literal["standard_inverse", "very_inverse", "extremely_inverse"] = "standard_inverse"


class ProtectionOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bus: str = Field(..., min_length=1)
    type: Literal["3ph", "slg", "ll", "llg"] = "3ph"
    rf: float = Field(0.0, ge=0)
    phase: Optional[int] = Field(None, ge=1, le=3)
    phase2: Optional[int] = Field(None, ge=1, le=3)
    run_faultstudy: bool = True
    relays: list[ProtectionRelay] = Field(..., min_length=1)


class LoadFlowOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FaultOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bus: str = Field(..., min_length=1)
    type: Literal["3ph", "slg", "ll", "llg"] = "3ph"
    rf: float = Field(0.0, ge=0)
    # PowerFactory's documented ComShc calculation mode is source input, not
    # a guessed engine default.  OpenDSS ignores this field; retaining it in
    # the canonical Case keeps a PFD-derived fault reproducible on PF.
    powerfactory_mode: Optional[int] = Field(None, ge=0)
    # Source study-case label/provenance.  This is intentionally separate
    # from ``powerfactory_mode``: the public input inventory exposed Method C
    # in the case name, but did not expose a numeric voltage factor.
    powerfactory_method: Optional[Literal["method_a", "method_b", "method_c"]] = None
    powerfactory_voltage_factor: Optional[float] = Field(None, gt=0)
    powerfactory_xf: Optional[float] = Field(None, ge=0, description="Source ComShc Xf in ohm when exposed.")
    phase: Optional[int] = Field(None, ge=1, le=3)
    phase2: Optional[int] = Field(None, ge=1, le=3)
    run_faultstudy: bool = True


class HostingCapacityOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion: Literal["overvoltage", "thermal", "both"] = "overvoltage"
    v_max: float = Field(1.05, gt=0)
    max_kw: float = Field(5000.0, gt=0)
    phases: int = Field(3, ge=1, le=3)
    der_control: Literal["constant_pf", "volt_var", "volt_watt", "volt_var_volt_watt"] = "constant_pf"
    buses: Optional[list[str]] = None


class GicOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frequency: float = Field(0.1, gt=0)
    e_field_v_per_km: Optional[float] = Field(None, ge=0)
    ee: Optional[float] = None
    en: Optional[float] = None


class StudySpec(BaseModel):
    """What to compute, with study-specific option validation."""

    type: StudyType = "load_flow"
    options: dict[str, Any] = Field(default_factory=dict)
    intent: Optional[Intent] = Field(
        None,
        description="Typed research question (WP14): an acceptance predicate "
        "over solver results, evaluated against results.json via the shared "
        "grid-code criteria evaluation. Absent means the study carries no "
        "typed acceptance claim.",
    )

    @model_serializer(mode="wrap")
    def _omit_absent_intent(
        self, handler: SerializerFunctionWrapHandler, info: SerializationInfo
    ) -> Any:
        # Additive contract: a Case without an intent must serialize exactly
        # as before, so fingerprints and stored case.json files never move
        # for existing studies.
        payload = handler(self)
        if isinstance(payload, dict) and payload.get("intent") is None:
            # Defensive: under model_dump(exclude_none/exclude_unset/exclude)
            # the handler payload has no "intent" key at all, so a bare pop
            # would raise KeyError (latent crash surfaced while fixing T-008).
            payload.pop("intent", None)
        return payload

    @model_validator(mode="after")
    def _validate_options(self) -> "StudySpec":
        option_models = {
            "load_flow": LoadFlowOptions,
            "unbalanced_load_flow": LoadFlowOptions,
            "fault": FaultOptions,
            "hosting_capacity": HostingCapacityOptions,
            "gic": GicOptions,
            "qsts": LoadFlowOptions,
            "harmonics": LoadFlowOptions,
            "protection": ProtectionOptions,
        }
        model = option_models.get(self.type)
        if model is not None:
            self.options = model.model_validate(self.options).model_dump(mode="json")
        return self


class Scenario(BaseModel):
    """A named variation of the base case.

    ``overrides`` uses dotted paths into the case, e.g.
    ``{"ders.PV1.kva": 750}``. Applied by the orchestrator before solving.
    """

    name: str
    overrides: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Experiment (before/after disturbance studies)
# --------------------------------------------------------------------------- #

ActionKind = Literal[
    "fault",  # apply a shunt fault at a bus
    "open",  # open a line/switch terminal
    "close",  # close a line/switch
    "trip_gen",  # disable a generator/DER
    "shed_load",  # disable a load
    "set_tap",  # force a transformer tap
]


class ExperimentAction(BaseModel):
    """One disturbance applied between the 'before' and 'after' solves."""

    kind: ActionKind
    target: str = Field(..., description="Element or bus the action acts on.")
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Action-specific values, e.g. {'phases':3,'r':0.001} for "
        "a fault, or {'tap':1.05} for set_tap.",
    )
    label: str = Field("", description="Short caption shown on the SLD marker.")
    # Optional grouping for multi-event studies. Actions without a time stay
    # in one legacy before/after group.
    time_s: Optional[float] = Field(None, ge=0)
    event_id: Optional[str] = None


class Experiment(BaseModel):
    """A disturbance study: solve baseline, apply actions, solve again.

    The report shows the network *before* and *after* side by side with the
    affected elements marked.
    """

    name: str
    description: str = ""
    actions: list[ExperimentAction] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Standards / acceptance criteria
# --------------------------------------------------------------------------- #


class StandardsSpec(BaseModel):
    """Acceptance limits used to flag pass/fail in the report."""

    voltage_method: str = Field("range", description="'range' | 'EN50160' | custom.")
    v_min_pu: float = Field(0.95, gt=0)
    v_max_pu: float = Field(1.05, gt=0)
    inverter_standard: Optional[str] = "IEEE1547-2018"
    grid_code: Optional[str] = Field(
        None, description="Named acceptance profile, e.g. PEA-2016; None means generic limits."
    )


class SweepSpec(BaseModel):
    """One deterministic, one-dimensional parameter sweep."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1)
    values: list[float] | None = None
    start: float | None = None
    stop: float | None = None
    step: float | None = None

    @model_validator(mode="after")
    def _validate_shape(self) -> "SweepSpec":
        has_values = self.values is not None
        has_range = any(value is not None for value in (self.start, self.stop, self.step))
        if has_values == has_range:
            raise ValueError("sweep requires exactly one of values or start/stop/step")
        if has_values:
            if not self.values:
                raise ValueError("sweep.values must not be empty")
            return self
        if self.start is None or self.stop is None or self.step is None:
            raise ValueError("sweep range requires start, stop, and step")
        if self.step == 0:
            raise ValueError("sweep.step must not be zero")
        if self.stop > self.start and self.step < 0:
            raise ValueError("sweep.step must be positive when stop is greater than start")
        if self.stop < self.start and self.step > 0:
            raise ValueError("sweep.step must be negative when stop is less than start")
        return self


class CaseStudy(BaseModel):
    """One explicit runnable study in a physical Case.

    ``scenarios``/``experiments`` remain accepted as legacy input.  New Cases
    use one study per named operating point and an optional one-dimensional
    sweep, so the CLI never needs to expose a Cartesian-product ``matrix``.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., pattern=r"^[A-Za-z0-9_.-]+$")
    study: StudySpec = Field(default_factory=StudySpec)
    engines: list[Literal["opendss", "powerfactory"]] = Field(default_factory=list)
    set: dict[str, Any] = Field(default_factory=dict)
    sweep: SweepSpec | None = None
    model_packages: list[str] = Field(default_factory=list)
    actions: list[ExperimentAction] = Field(default_factory=list)
    scenarios: list[Scenario] = Field(default_factory=list)
    acceptance: StandardsSpec | None = None

    @field_validator("scenarios")
    @classmethod
    def _unique_scenarios(cls, value: list[Scenario]) -> list[Scenario]:
        names = [scenario.name for scenario in value]
        if len(names) != len(set(names)):
            raise ValueError("experiment scenario names must be unique")
        if "base" in names:
            raise ValueError("scenario name 'base' is reserved for the implicit base scenario")
        return value


# Compatibility name used by the first Case-program slice.
CaseExperiment = CaseStudy


class Assumption(BaseModel):
    """A non-canonical input, missing value, or solver-aligned operating point."""

    path: str
    value: Any = None
    source: Literal["demonstrator-default", "missing", "solver-snapshot"] = "demonstrator-default"


def _collect_inline_assumptions(network: NetworkSpec) -> list[Assumption]:
    if network.kind != "inline" or network.inline is None:
        return []

    found: list[Assumption] = []

    def add(path: str, value: Any, *, missing: bool = False) -> None:
        found.append(
            Assumption(
                path=path,
                value=value,
                source="missing" if missing else "demonstrator-default",
            )
        )

    if "frequency_hz" not in network.model_fields_set:
        add("network.frequency_hz", network.frequency_hz)

    net = network.inline
    for i, line in enumerate(net.lines):
        base = f"network.inline.lines[{i}]"
        if line.uses_total_parameters:
            # Total branch data has no defensible zero-sequence default and
            # its total B is required by the nested contract.  Do not invent
            # either merely to satisfy a balanced study.
            if "normal_amps" not in line.model_fields_set:
                add(f"{base}.normal_amps", None, missing=True)
            continue
        if line.matrix is not None:
            # An untransposed line declares its full conductor matrices, so
            # there is no sequence default to record.  Assuming a zero-sequence
            # value here would be assuming one it explicitly does not use.
            if "normal_amps" not in line.model_fields_set:
                add(f"{base}.normal_amps", None, missing=True)
            continue
        if "r0_ohm_per_km" not in line.model_fields_set:
            add(f"{base}.r0_ohm_per_km", 3 * float(line.r1_ohm_per_km))
        if "x0_ohm_per_km" not in line.model_fields_set:
            add(f"{base}.x0_ohm_per_km", 3 * float(line.x1_ohm_per_km))
        if "b1_us_per_km" not in line.model_fields_set:
            add(f"{base}.b1_us_per_km", line.b1_us_per_km)
        if "normal_amps" not in line.model_fields_set:
            add(f"{base}.normal_amps", None, missing=True)

    for i, transformer in enumerate(net.transformers):
        if not transformer.zero_resistance and "x_r_ratio" not in transformer.model_fields_set:
            add(
                f"network.inline.transformers[{i}].x_r_ratio",
                transformer.x_r_ratio,
            )

    for i, load in enumerate(net.loads):
        if "pf" not in load.model_fields_set:
            add(f"network.inline.loads[{i}].pf", load.pf)

    for i, grid in enumerate(net.external_grids):
        base = f"network.inline.external_grids[{i}]"
        for field in ("pu", "angle_deg", "x_r_ratio"):
            if field not in grid.model_fields_set:
                add(f"{base}.{field}", getattr(grid, field))
        if "sk1_mva" not in grid.model_fields_set:
            add(f"{base}.sk1_mva", grid.sk3_mva)

    for i, generator in enumerate(net.generators):
        base = f"network.inline.generators[{i}]"
        for field in ("bus_type", "pu", "kw", "pf", "xdpp_pu"):
            if field not in generator.model_fields_set:
                add(f"{base}.{field}", getattr(generator, field))

    return found
