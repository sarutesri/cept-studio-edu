"""Result schema — engine-agnostic outputs.

A study returns these regardless of which engine produced them, so the
reporting and validation layers never depend on OpenDSS/pandapower/etc.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from cept.schema.sld import SLDModel


class BusVoltage(BaseModel):
    bus: str
    phase: int = Field(..., ge=1, le=3, description="Phase 1=A, 2=B, 3=C.")
    v_pu: float = Field(..., description="Per-unit voltage magnitude (line-to-neutral).")
    v_angle_deg: float
    v_kv_ln: Optional[float] = Field(None, description="Line-to-neutral kV magnitude.")


class DEROutput(BaseModel):
    """Actual P and Q output of a DER element (after inverter control)."""

    name: str
    kind: str
    bus: str
    p_kw: float
    q_kvar: float
    v_mean_pu: Optional[float] = None


class BranchFlow(BaseModel):
    name: str
    bus_from: str
    bus_to: str
    p_kw: float
    q_kvar: float
    losses_kw: Optional[float] = None
    loading_pct: Optional[float] = None
    # Optional receiving-end values.  Kept after the original fields so
    # positional construction of the legacy result contract remains valid.
    p_to_kw: Optional[float] = None
    q_to_kvar: Optional[float] = None


class LoadFlowResult(BaseModel):
    converged: bool
    iterations: Optional[int] = None
    bus_voltages: list[BusVoltage] = Field(default_factory=list)
    branch_flows: list[BranchFlow] = Field(default_factory=list)
    total_load_kw: Optional[float] = None
    total_load_kvar: Optional[float] = None
    total_loss_kw: Optional[float] = None
    total_loss_kvar: Optional[float] = None
    source_p_kw: Optional[float] = None
    source_q_kvar: Optional[float] = None
    der_outputs: list[DEROutput] = Field(default_factory=list)
    # Solver-returned generator/load outputs used by component validation.
    # Empty is valid for engines that expose only network quantities.
    device_outputs: list[DEROutput] = Field(default_factory=list)

    def voltage(self, bus: str, phase: int) -> Optional[float]:
        """Convenience lookup of v_pu for (bus, phase); None if absent."""
        b = bus.lower()
        for bv in self.bus_voltages:
            if bv.bus.lower() == b and bv.phase == phase:
                return bv.v_pu
        return None


# --------------------------------------------------------------------------- #
# Fault analysis
# --------------------------------------------------------------------------- #


class FaultCurrent(BaseModel):
    phase: int
    i_amp: float
    i_angle_deg: float


class FaultStudyRow(BaseModel):
    """Available fault current at a bus (OpenDSS FaultStudy mode)."""

    bus: str
    i_3ph_a: Optional[float] = None
    i_1ph_a: Optional[float] = None
    i_ll_a: Optional[float] = None


class FaultResult(BaseModel):
    bus: str
    fault_type: str  # 3ph | slg | ll | llg
    rf_ohm: float
    phases: list[int] = Field(default_factory=list)
    currents: list[FaultCurrent] = Field(default_factory=list)
    total_fault_current_a: Optional[float] = None
    bus_voltages_during: list[BusVoltage] = Field(default_factory=list)
    min_voltage_pu: Optional[float] = None
    study_rows: list[FaultStudyRow] = Field(default_factory=list)
    voltage_factor_applied: Optional[float] = Field(
        None,
        description=(
            "IEC 60909 voltage factor c applied to the pre-fault source voltage, "
            "when the source declared one. None means the engine faulted the "
            "network at its own pre-fault voltage."
        ),
    )


# --------------------------------------------------------------------------- #
# Hosting capacity
# --------------------------------------------------------------------------- #


class HostingCapacityItem(BaseModel):
    bus: str
    hc_kw: float
    limit: str  # overvoltage | thermal | maxed | none
    v_at_hc: Optional[float] = None


class HostingCapacityResult(BaseModel):
    der_type: str = "pv"
    criterion: str = "overvoltage"
    v_max_pu: float = 1.05
    max_search_kw: float = 5000.0
    baseline_v_max_pu: Optional[float] = None
    items: list[HostingCapacityItem] = Field(default_factory=list)

    @property
    def min_hc(self) -> Optional["HostingCapacityItem"]:
        return min(self.items, key=lambda i: i.hc_kw) if self.items else None


# --------------------------------------------------------------------------- #
# Dynamics (electromechanical transient)
# --------------------------------------------------------------------------- #


class ChannelSemanticDescriptor(BaseModel):
    """Typed semantic identity for one solver-returned result channel.

    The descriptor is additive evidence: legacy ``name``, ``unit`` and
    ``source_channel`` remain unchanged, while high-risk cross-engine axes are
    carried structurally instead of being reconstructed from display text.
    """

    canonical_quantity: str
    unit: str = ""
    raw_unit: str = ""
    phase: str = "unknown"
    sequence: str = ""
    terminal: str = ""
    per_unit_base: str = ""
    sign_convention: str = "unknown"
    reference_frame: str = "unknown"
    time_origin_kind: str = "unknown"
    review_status: Literal["reviewed", "unreviewed"] = "unreviewed"
    mapping_method: str = "unreviewed"


class MonitorChannel(BaseModel):
    name: str
    values: list[float] = Field(default_factory=list)
    unit: str = ""
    source_channel: str = ""
    semantics: Optional[ChannelSemanticDescriptor] = Field(
        None,
        exclude_if=lambda value: value is None,
        description="Typed channel semantics when bound at the engine/result boundary.",
    )


class MonitorTrace(BaseModel):
    name: str
    element: str
    mode: int = 0
    t: list[float] = Field(default_factory=list)
    channels: list[MonitorChannel] = Field(default_factory=list)

    def channel(self, name: str) -> Optional[MonitorChannel]:
        for c in self.channels:
            if c.name.lower() == name.lower():
                return c
        return None


class DynamicsResult(BaseModel):
    converged: bool
    duration: float
    stepsize: float
    generators: list[str] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    monitors: list[MonitorTrace] = Field(default_factory=list)
    unresolved_signals: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Requested solver channels that were unavailable or unbound.",
    )
    # Solver-produced initial conditions captured immediately after
    # PowerFactory/OpenDSS initialization and before the first simulation
    # step.  This is deliberately an opaque evidence object: CEPT does not
    # solve or invent controller states in Python.
    initialization: dict[str, Any] = Field(default_factory=dict)
    # headline metrics
    stable: Optional[bool] = None
    freq_nadir_hz: Optional[float] = None
    freq_peak_hz: Optional[float] = None
    max_rotor_angle_deg: Optional[float] = None
    max_angle_separation_deg: Optional[float] = None
    settling_note: str = ""


class EMTResult(BaseModel):
    """Solver-returned electromagnetic-transient waveform channels."""

    converged: bool
    duration_s: float
    stepsize_s: float
    samples: int = Field(..., ge=1)
    channels: list[MonitorTrace] = Field(default_factory=list)


class TimeSeriesSnapshot(BaseModel):
    t_s: float
    converged: bool
    min_voltage_pu: Optional[float] = None
    max_voltage_pu: Optional[float] = None
    total_load_kw: Optional[float] = None
    bus_voltages: list[BusVoltage] = Field(default_factory=list)


class TimeSeriesResult(BaseModel):
    converged: bool
    duration_s: float
    stepsize_s: float
    snapshots: list[TimeSeriesSnapshot] = Field(default_factory=list)
    min_voltage_pu: Optional[float] = None
    max_voltage_pu: Optional[float] = None
    violation_count: int = 0


class HarmonicBusVoltage(BaseModel):
    bus: str
    phase: int
    harmonic: int
    frequency_hz: float
    v_pu: float


class HarmonicSnapshot(BaseModel):
    harmonic: int
    frequency_hz: float
    converged: bool
    bus_voltages: list[HarmonicBusVoltage] = Field(default_factory=list)


class HarmonicsResult(BaseModel):
    converged: bool
    fundamental_hz: float
    snapshots: list[HarmonicSnapshot] = Field(default_factory=list)
    thd_v_pct_by_bus: dict[str, float] = Field(default_factory=dict)


class SLDSnapshot(BaseModel):
    """A value-bearing SLD captured at one solver time/state."""

    id: str
    t_s: float = 0.0
    label: str
    event_id: Optional[str] = None
    phase: Literal["initial", "after_event", "final", "timeline"] = "initial"
    status: Literal["available", "blocked"] = "available"
    solver_provenance: dict[str, Any] = Field(default_factory=dict)
    sld: Optional[SLDModel] = None


class ProtectionTrip(BaseModel):
    name: str
    pickup_a: float
    current_multiple: float
    trip_time_s: Optional[float] = None
    status: Literal["trip", "no-trip"]


class ProtectionResult(BaseModel):
    fault_bus: str
    fault_type: str
    fault_current_a: Optional[float] = None
    relays: list[ProtectionTrip] = Field(default_factory=list)
    calculation_method: str = "IEC inverse-time curve applied to solver-returned fault current"


# --------------------------------------------------------------------------- #
# GIC (geomagnetically induced current)
# --------------------------------------------------------------------------- #


class GICElementFlow(BaseModel):
    name: str
    kind: str  # "transformer" | "line"
    bus_h: str = ""
    gic_amps: float = 0.0
    gic_amps_per_phase: float = 0.0  # per-phase effective for transformers


class GICResult(BaseModel):
    elements: list[GICElementFlow] = Field(default_factory=list)
    max_gic_a: float = 0.0
    total_gic_a: float = 0.0
    e_field_v_per_km: Optional[float] = None
    n_transformers: int = 0
    n_lines: int = 0


class ModellingApproximation(BaseModel):
    """A deliberate engine-side departure from what the source declared.

    Recorded so a comparison can name the reason a candidate value differs
    instead of reporting the row as an unexplained block.  An approximation is
    a statement the adapter makes about its own output; it is never a licence
    to widen a tolerance.
    """

    asset: str
    quantity: str = Field(
        description="The canonical quantity the approximation perturbs.",
    )
    source_value: Optional[float] = Field(
        None, description="What the source declared, in the source's own unit."
    )
    applied_value: Optional[float] = Field(None, description="What the engine was given instead.")
    reason: str
    provenance: str = Field("", description="Why this value and not another.")


class StudyResult(BaseModel):
    """Wrapper carrying provenance alongside the typed payload."""

    study_type: str
    case_name: str
    case_fingerprint: str
    engine: str
    engine_version: str = ""
    calculation_method: str = ""
    # The nominal system frequency this run actually solved at.  Recorded so a
    # three-way comparison can BLOCK when source/candidate runs disagree on
    # frequency (which silently mis-sizes line charging B = 2*pi*f*C).
    nominal_frequency_hz: Optional[float] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    load_flow: Optional[LoadFlowResult] = None
    fault: Optional[FaultResult] = None
    hosting_capacity: Optional[HostingCapacityResult] = None
    dynamics: Optional[DynamicsResult] = None
    emt: Optional[EMTResult] = None
    time_series: Optional[TimeSeriesResult] = None
    harmonics: Optional[HarmonicsResult] = None
    protection: Optional[ProtectionResult] = None
    gic: Optional[GICResult] = None
    # SLD view(s). For an experiment, `sld` is the "before" state and
    # `sld_after` the "after" state; otherwise only `sld` is set.
    sld: Optional[SLDModel] = None
    sld_after: Optional[SLDModel] = None
    sld_snapshots: list[SLDSnapshot] = Field(default_factory=list)
    load_flow_after: Optional[LoadFlowResult] = None
    experiment_name: Optional[str] = None
    # Departures the adapter had to make from the Case, each naming the asset
    # and quantity it perturbs so a comparison can attribute the difference.
    modelling_approximations: list[ModellingApproximation] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


class ValidationItem(BaseModel):
    label: str
    computed: float
    reference: float
    tol_abs: float
    error_abs: float
    error_rel_pct: Optional[float] = None
    passed: bool


class ValidationReport(BaseModel):
    case_name: str
    reference_name: str
    quantity: Literal["voltage_pu", "power_kw", "current_a", "generic"] = "voltage_pu"
    items: list[ValidationItem] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def n_pass(self) -> int:
        return sum(1 for i in self.items if i.passed)

    @property
    def n_fail(self) -> int:
        return sum(1 for i in self.items if not i.passed)

    @property
    def max_error_abs(self) -> float:
        return max((i.error_abs for i in self.items), default=0.0)

    @property
    def passed(self) -> bool:
        return self.n_fail == 0 and len(self.items) > 0
