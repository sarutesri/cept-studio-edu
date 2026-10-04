"""Opt-in DSS-Python generator UserModels for OpenDSS RMS dynamics.

The normal CEPT OpenDSS path deliberately continues to use the built-in
``Generator`` model.  This module adds a small, explicit bridge for cases that
need a model equation CEPT cannot express with the built-in single-mass
generator.  The bridge is solver-side: OpenDSS calls the registered Python
model through the DSS-Python UserModel ABI during its power-flow and dynamic
iterations.  It is therefore not a Python post-processing approximation.

The first model is intentionally bounded.  It is a classical synchronous
machine with a constant internal positive-sequence emf behind ``Ra + jXd'``
and a two-state rotor swing equation.  It is useful for a paper/classical
SMIB lane and as a regression target for the UserModel plumbing, but it is not
claimed to be a GENROU/TypSym sixth-order implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from cmath import phase
from math import cos, isfinite, pi, sin, sqrt
from typing import Any

from cept.dynamics_contract import (
    CLASSICAL_USER_MODEL,
    CLASSICAL_USER_MODEL_CLASS,
    CLASSICAL_USER_MODEL_FAMILY,
    GENCLS_USERMODEL,
    USER_MODEL_INITIAL_STATE_V1,
    USER_MODEL_INITIAL_STATE_V2,
    USER_MODEL_INITIAL_STATE_V3,
    requested_dynamic_model,
)
from cept.schema.case import Case, GenrouDynamics, InlineGenerator

USER_MODEL_OPTION = "opendss_user_model"
USER_MODEL_INITIAL_STATE_OPTION = "opendss_usermodel_initial_state"

@dataclass(frozen=True)
class UserModelSpec:
    """Runtime identity for one supported DSS-Python UserModel lane."""

    name: str
    class_name: str
    model_family: str
    equations: tuple[str, ...]


CLASSICAL_SPEC = UserModelSpec(
    name=CLASSICAL_USER_MODEL,
    class_name=CLASSICAL_USER_MODEL_CLASS,
    model_family=CLASSICAL_USER_MODEL_FAMILY,
    equations=(
        "d(delta)/dt = omega",
        "tm = Pm / n",
        "d(omega)/dt = omega_s/(2H) * (tm - Pe - D*(n - 1))",
        "I1 = (V1 - E1)/(Ra + jXd')",
    ),
)


def _finite_float(value: Any, *, field: str, name: str) -> float:
    if not isinstance(value, (int, float)) or not isfinite(float(value)):
        raise ValueError(f"UserModel initial state for {name!r} requires finite {field}.")
    return float(value)


def _wrap_angle_rad(value: float) -> float:
    """Wrap an electrical angle to [-pi, pi)."""

    return (float(value) + pi) % (2.0 * pi) - pi


def _classical_mechanical_torque_pu(mechanical_power_pu: float, speed_pu: float) -> float:
    """Convert constant shaft power to the torque used by PF Classical.

    PowerFactory's Classical machine exposes ``s:pt`` as shaft power but its
    swing equation uses ``m:xmt = s:pt / n``.  Keeping that distinction in the
    solver-side UserModel is part of the cross-engine model contract; it is not
    a trajectory-fit parameter.
    """

    power = float(mechanical_power_pu)
    speed = float(speed_pu)
    if not isfinite(power) or not isfinite(speed) or speed <= 0.0:
        raise ValueError("Classical mechanical torque requires finite positive speed")
    return power / speed


def _network_frame_shift_rad(local_terminal_v1: complex, source_terminal_angle_deg: float) -> float:
    """Return the fixed source->local network coordinate rotation.

    The transform is derived once from the same-machine pre-event terminal
    positive-sequence phasor.  No transformer clock number or dynamic result
    trace participates in the calculation.
    """

    return _wrap_angle_rad(phase(local_terminal_v1) - float(source_terminal_angle_deg) * pi / 180.0)


def user_model_initial_states(case: Case) -> dict[str, dict[str, float]]:
    """Return the optional locked initial states carried by a parity Case.

    Version 1 preserves the original contract where the reader adds a declared
    rotor-angle offset. Version 2 is source-bound and solver-native: it carries
    the PowerFactory terminal positive-sequence phasor plus a
    reference-machine rotor-to-internal-emf offset. Version 3 carries the same
    terminal phasor but uses PowerFactory's network-referenced ``firot`` angle,
    so an SMIB does not need a synchronous reference machine. Both source-bound
    versions derive one constant local network-frame shift at ``Init`` and
    Mode-3 Theta needs no reader-side numerical translation.
    """

    raw = case.study.options.get(USER_MODEL_INITIAL_STATE_OPTION)
    if raw in (None, "", False):
        return {}
    if not isinstance(raw, dict):
        raise ValueError(
            f"{USER_MODEL_INITIAL_STATE_OPTION!r} must be an object with a 'generators' map."
        )
    schema = str(raw.get("schema") or USER_MODEL_INITIAL_STATE_V1)
    if schema not in {
        USER_MODEL_INITIAL_STATE_V1,
        USER_MODEL_INITIAL_STATE_V2,
        USER_MODEL_INITIAL_STATE_V3,
    }:
        raise ValueError(
            f"Unsupported UserModel initial-state schema {schema!r}; expected "
            f"{USER_MODEL_INITIAL_STATE_V1!r}, {USER_MODEL_INITIAL_STATE_V2!r}, "
            f"or {USER_MODEL_INITIAL_STATE_V3!r}."
        )
    generators = raw.get("generators")
    if not isinstance(generators, dict) or not generators:
        raise ValueError(
            f"{USER_MODEL_INITIAL_STATE_OPTION!r} requires a non-empty 'generators' map."
        )
    if schema == USER_MODEL_INITIAL_STATE_V2:
        source = raw.get("source")
        audit = source.get("reference_machine_audit") if isinstance(source, dict) else None
        if not isinstance(audit, dict) or audit.get("exactly_one_reference_machine") is not True:
            raise ValueError(
                f"{USER_MODEL_INITIAL_STATE_OPTION!r} v2 requires source.reference_machine_audit "
                "with exactly_one_reference_machine=true; m:firel has no demonstrated reference."
            )
    elif schema == USER_MODEL_INITIAL_STATE_V3:
        source = raw.get("source")
        reference = source.get("angle_reference") if isinstance(source, dict) else None
        if not isinstance(reference, dict) or reference.get("channel") != "m:firot":
            raise ValueError(
                f"{USER_MODEL_INITIAL_STATE_OPTION!r} v3 requires "
                "source.angle_reference.channel='m:firot'."
            )
    result: dict[str, dict[str, float]] = {}
    for name, state in generators.items():
        if not isinstance(state, dict):
            raise ValueError(f"UserModel initial state for {name!r} must be an object.")
        values = {
            field: _finite_float(state.get(field), field=field, name=str(name))
            for field in ("e1_mag_pu", "e1_angle_deg", "speed_pu")
        }
        if values["e1_mag_pu"] <= 0 or values["speed_pu"] <= 0:
            raise ValueError(
                f"UserModel initial state for {name!r} requires e1_mag_pu>0 and speed_pu>0."
            )
        if schema in {USER_MODEL_INITIAL_STATE_V2, USER_MODEL_INITIAL_STATE_V3}:
            values["frame_contract_v2"] = 1.0
            values["frame_contract_v3"] = 1.0 if schema == USER_MODEL_INITIAL_STATE_V3 else 0.0
            values["source_terminal_v1_mag_pu"] = _finite_float(
                state.get("source_terminal_v1_mag_pu"),
                field="source_terminal_v1_mag_pu",
                name=str(name),
            )
            values["source_terminal_v1_angle_deg"] = _finite_float(
                state.get("source_terminal_v1_angle_deg"),
                field="source_terminal_v1_angle_deg",
                name=str(name),
            )
            values["source_angle_reference_offset_deg"] = _finite_float(
                state.get("source_angle_reference_offset_deg"),
                field="source_angle_reference_offset_deg",
                name=str(name),
            )
            if schema == USER_MODEL_INITIAL_STATE_V3:
                values["source_reference_angle_deg"] = _finite_float(
                    state.get("source_reference_angle_deg"),
                    field="source_reference_angle_deg",
                    name=str(name),
                )
            if values["source_terminal_v1_mag_pu"] <= 0:
                raise ValueError(
                    f"UserModel initial state for {name!r} requires source_terminal_v1_mag_pu>0."
                )
            # Source-bound contracts keep the rotor state in the
            # source/canonical frame, so the monitor reader must not translate
            # the trace numerically.
            values["angle_reference_offset_deg"] = 0.0
        else:
            values["frame_contract_v2"] = 0.0
            values["frame_contract_v3"] = 0.0
            values["angle_reference_offset_deg"] = _finite_float(
                state.get("angle_reference_offset_deg", 0.0),
                field="angle_reference_offset_deg",
                name=str(name),
            )
        # Frozen-E is an explicit, diagnostic-only algebraic replay contract.
        # It is kept out of the normal v1/v2/v3 trajectory unless the caller
        # opts in with FrozenEMode=1, and the values are validated here so a
        # malformed replay cannot silently become a normal dynamic run.
        if float(state.get("frozen_e_mode", 0.0) or 0.0) > 0.5:
            values["frozen_e_mode"] = 1.0
            values["frozen_e_mag_pu"] = _finite_float(
                state.get("frozen_e_mag_pu"),
                field="frozen_e_mag_pu",
                name=str(name),
            )
            values["frozen_e_angle_deg"] = _finite_float(
                state.get("frozen_e_angle_deg"),
                field="frozen_e_angle_deg",
                name=str(name),
            )
            if values["frozen_e_mag_pu"] <= 0:
                raise ValueError(
                    f"UserModel frozen-E state for {name!r} requires frozen_e_mag_pu>0."
                )
        result[str(name)] = values
    return result


def requested_user_model(case: Case) -> UserModelSpec | None:
    """Resolve the explicit Case opt-in, failing closed on unknown values."""

    reduced_model = requested_dynamic_model(case)
    requested: Any = case.study.options.get(USER_MODEL_OPTION)
    if reduced_model == GENCLS_USERMODEL and requested in (None, "", False, "builtin", "none"):
        requested = CLASSICAL_USER_MODEL
    if requested in (None, "", False, "builtin", "none"):
        return None
    if isinstance(requested, dict):
        requested = requested.get("name")
    if requested != CLASSICAL_USER_MODEL:
        raise ValueError(
            f"Unsupported OpenDSS UserModel {requested!r}; expected "
            f"{CLASSICAL_USER_MODEL!r} or omit {USER_MODEL_OPTION!r} to use the built-in model."
        )
    if reduced_model == "gencls-classical":
        raise ValueError(
            "OpenDSS GENCLS benchmark and DSS-Python UserModel cannot be selected together."
        )
    if reduced_model == GENCLS_USERMODEL:
        raw_state = case.study.options.get(USER_MODEL_INITIAL_STATE_OPTION)
        if not isinstance(raw_state, dict) or not isinstance(raw_state.get("source"), dict):
            raise ValueError(
                f"{USER_MODEL_INITIAL_STATE_OPTION!r} requires a source-bound evidence object "
                "for the gencls-classical-usermodel parity lane."
            )
        states = user_model_initial_states(case)
        required = {
            generator.name
            for generator in (case.network.inline.generators if case.network.inline is not None else [])
            if generator.bus_type != "slack"
        }
        missing = sorted(required - set(states))
        if missing:
            raise ValueError(
                "The gencls-classical-usermodel parity lane requires locked initial state for: "
                + ", ".join(missing)
            )
    if case.study.type not in {"dynamics", "dynamics_rms"}:
        raise ValueError(
            f"{USER_MODEL_OPTION!r} is only valid for study.type='dynamics' or 'dynamics_rms'."
        )
    inline = case.network.inline
    if inline is None:
        raise ValueError("The DSS-Python UserModel lane currently requires an inline Case network.")
    if any(generator.wecc is not None for generator in inline.generators):
        raise ValueError("The classical DSS-Python UserModel lane cannot replace a WECC composite model.")
    missing = [
        generator.name
        for generator in inline.generators
        if generator.bus_type != "slack" and generator.dynamics is None
    ]
    if missing:
        raise ValueError(
            "The classical DSS-Python UserModel lane requires explicit generator dynamics for: "
            + ", ".join(missing)
        )
    return CLASSICAL_SPEC


def _dss_user_model_backend():
    """Import and return the DSS-Python callback registry, with a useful error."""

    try:
        from dss.UserModels import GenUserModel
    except Exception as exc:  # pragma: no cover - exercised on machines without DSS-Python
        raise RuntimeError(
            "The OpenDSS UserModel lane requires DSS-Python with dss.UserModels.GenUserModel. "
            "The ordinary OpenDSS built-in dynamics lane does not have this requirement."
        ) from exc
    if not getattr(GenUserModel, "dll_path", None):
        raise RuntimeError("DSS-Python exposed GenUserModel but no callback library path.")
    return GenUserModel


def user_model_provenance(spec: UserModelSpec) -> dict[str, Any]:
    """Return JSON-safe runtime identity for manifests and review reports."""

    registry = _dss_user_model_backend()
    return {
        "status": "selected",
        "name": spec.name,
        "class_name": spec.class_name,
        "model_family": spec.model_family,
        "backend": "DSS-Python GenUserModel",
        "callback_library": str(registry.dll_path),
        "equations": list(spec.equations),
        "solver_boundary": "OpenDSS calls Init/Calc/Integrate inside the solver iteration",
    }


def _machine_q_mvar(generator: InlineGenerator, active_kw: float) -> float:
    if generator.q_mvar is not None:
        return float(generator.q_mvar) * float(generator.parallel_units)
    pf = float(generator.pf)
    if abs(pf) < 1.0e-12:
        return 0.0
    return active_kw / 1000.0 * sqrt(max(1.0 - pf * pf, 0.0)) / abs(pf)


def user_model_generator_properties(
    generator: InlineGenerator,
    machine: GenrouDynamics,
    spec: UserModelSpec,
    *,
    active_kw: float,
    active_mva: float,
    initial_state: dict[str, float] | None = None,
) -> str:
    """Render the explicit Model=6/UserModel/UserData properties.

    ``active_kw`` and ``active_mva`` are already folded for parallel units by
    the OpenDSS compiler.  The UserModel receives a per-unit mechanical and
    reactive operating point on that same aggregate generator base.
    """

    registry = _dss_user_model_backend()
    q_mvar = _machine_q_mvar(generator, active_kw)
    p_pu = active_kw / (active_mva * 1000.0) if active_mva > 0 else 0.0
    q_pu = q_mvar / active_mva if active_mva > 0 else 0.0
    locked_state = ""
    if initial_state is not None:
        locked_state = (
            " InitLocked=1"
            f" E1MagPu={initial_state['e1_mag_pu']:.12g}"
            f" E1AngleDeg={initial_state['e1_angle_deg']:.12g}"
            f" InitialSpeedPu={initial_state['speed_pu']:.12g}"
        )
        if float(initial_state.get("frame_contract_v2", 0.0)) > 0.5:
            if float(initial_state.get("frame_contract_v3", 0.0)) > 0.5:
                locked_state += (
                    " FrameContractV3=1"
                    f" SourceTerminalV1MagPu={initial_state['source_terminal_v1_mag_pu']:.12g}"
                    f" SourceTerminalV1AngleDeg={initial_state['source_terminal_v1_angle_deg']:.12g}"
                    f" SourceReferenceAngleDeg={initial_state['source_reference_angle_deg']:.12g}"
                    f" SourceAngleReferenceOffsetDeg={initial_state['source_angle_reference_offset_deg']:.12g}"
                )
            else:
                locked_state += (
                    " FrameContractV2=1"
                    f" SourceTerminalV1MagPu={initial_state['source_terminal_v1_mag_pu']:.12g}"
                    f" SourceTerminalV1AngleDeg={initial_state['source_terminal_v1_angle_deg']:.12g}"
                    f" SourceAngleReferenceOffsetDeg={initial_state['source_angle_reference_offset_deg']:.12g}"
                )
        if float(initial_state.get("frozen_e_mode", 0.0)) > 0.5:
            locked_state += (
                " FrozenEMode=1"
                f" FrozenEMagPu={initial_state['frozen_e_mag_pu']:.12g}"
                f" FrozenEAngleDeg={initial_state['frozen_e_angle_deg']:.12g}"
            )
    return (
        f" model=6 usermodel=\"{str(registry.dll_path).replace(chr(34), chr(34) * 2)}\""
        f" userdata=(pymodel={spec.class_name}"
        f" H={machine.h:.12g} D={machine.d:.12g}"
        f" Xdp={machine.xdp:.12g} Ra={machine.ra:.12g}"
        f" PmPu={p_pu:.12g} QmPu={q_pu:.12g}{locked_state})"
    )


def _positive_sequence(values: list[complex]) -> tuple[complex, complex, complex]:
    """Return zero/positive/negative sequence values for A-B-C phase order."""

    a = complex(-0.5, sqrt(3.0) / 2.0)
    aa = a.conjugate()
    va, vb, vc = values[:3]
    return (
        (va + vb + vc) / 3.0,
        (va + a * vb + aa * vc) / 3.0,
        (va + aa * vb + a * vc) / 3.0,
    )


def _phase_values(sequence: tuple[complex, complex, complex]) -> tuple[complex, complex, complex]:
    """Reconstruct A-B-C phase values from zero/positive/negative sequence."""

    a = complex(-0.5, sqrt(3.0) / 2.0)
    aa = a.conjugate()
    v0, v1, v2 = sequence
    return (v0 + v1 + v2, v0 + aa * v1 + a * v2, v0 + a * v1 + aa * v2)


def _register_classical_model() -> None:
    """Register the model once when DSS-Python is present."""

    try:
        from dss.UserModels import GenUserModel
    except Exception:
        return

    if CLASSICAL_USER_MODEL_CLASS.lower() in getattr(GenUserModel, "model_classes", {}):
        return

    Base = GenUserModel.Base

    @GenUserModel.register
    class CEPTClassicalSynchronous(Base):  # type: ignore[misc, valid-type]
        """Two-state classical synchronous machine for OpenDSS Model=6."""

        def __init__(self, gen, dyn, callbacks):
            Base.__init__(self, gen, dyn, callbacks)
            self.add_inputs(
                ("H", 3.5),
                ("D", 0.0),
                ("Xdp", 0.3),
                ("Ra", 0.0),
                ("PmPu", 0.0),
                ("QmPu", 0.0),
                ("InitLocked", 0.0),
                ("E1MagPu", 1.0),
                ("E1AngleDeg", 0.0),
                ("InitialSpeedPu", 1.0),
                ("FrameContractV2", 0.0),
                ("FrameContractV3", 0.0),
                ("SourceTerminalV1MagPu", 1.0),
                ("SourceTerminalV1AngleDeg", 0.0),
                ("SourceReferenceAngleDeg", 0.0),
                ("SourceAngleReferenceOffsetDeg", 0.0),
                ("FrozenEMode", 0.0),
                ("FrozenEMagPu", 1.0),
                ("FrozenEAngleDeg", 0.0),
            )
            self.add_outputs(
                "UserModelSpeed",
                "UserModelTheta",
                "ElectricalPowerPU",
                "MechanicalPowerPU",
                "MechanicalTorquePU",
                "InternalVoltagePU",
                "TerminalVoltagePU",
                "PositiveSequenceCurrentPU",
                "NetworkFrameShiftDeg",
                "TerminalVoltageAngleDeg",
                "PositiveSequenceCurrentAngleDeg",
                "InternalVoltageAngleDeg",
                "LocalInternalAngleDeg",
                "CanonicalRotorAngleDeg",
                "ElectricalReactivePowerPU",
                "SpeedDerivativeRadPerS2",
                "AngleDerivativeRadPerS",
                "PowerBalanceResidualPU",
            )
            self.add_state_vars("delta", "omega")
            self.e_mag = 0.0
            self.e_complex = 0j
            self.electrical_power_pu = 0.0
            self.electrical_reactive_power_pu = 0.0
            self.mechanical_torque_pu = 0.0
            self.frame_contract_v2_active = False
            self.frame_contract_v3_active = False
            self.network_frame_shift_rad = 0.0
            self.source_angle_reference_offset_rad = 0.0
            self.frozen_e_active = False
            self.frozen_e_complex = 0j
            self.update()

        def update(self):
            gen = self.gen
            self.sbase_va = max(float(gen.kVArating) * 1000.0, 1.0)
            self.vbase_v = max(float(gen.kVGeneratorBase) * 1000.0 / sqrt(3.0), 1.0)
            zbase = 1000.0 * float(gen.kVGeneratorBase) ** 2 / max(float(gen.kVArating), 1.0)
            self.z = complex(float(self.Ra) * zbase, float(self.Xdp) * zbase)
            if abs(self.z) < 1.0e-12:
                self.z = complex(0.0, 1.0e-12)
            self.pm_w = float(self.PmPu) * self.sbase_va
            self.qm_var = float(self.QmPu) * self.sbase_va

        def _local_internal_angle(self) -> float:
            if self.frame_contract_v2_active or self.frame_contract_v3_active:
                return (
                    float(self.delta)
                    - self.source_angle_reference_offset_rad
                    + self.network_frame_shift_rad
                )
            return float(self.delta)

        def init_state_vars(self, V, currents):
            """Initialize emf and rotor states from the solved terminal snapshot."""

            v1 = _positive_sequence(V[:3])[1]
            i1 = _positive_sequence(currents[:3])[1]
            omega_s = max(float(self.gen.w0), 2.0 * pi * 50.0)
            self.frozen_e_active = float(self.FrozenEMode) > 0.5
            if float(self.InitLocked) > 0.5:
                # These are explicit solver-derived initial conditions, not a
                # Python-calculated replacement trajectory.  Contract v2 uses
                # the reference-machine rotor frame; v3 uses PF's
                # network-referenced firot frame. Both map E1 into the local
                # DSS network frame only in the electrical equation.
                self.e_mag = float(self.E1MagPu) * self.vbase_v
                self.frame_contract_v2_active = float(self.FrameContractV2) > 0.5
                self.frame_contract_v3_active = float(self.FrameContractV3) > 0.5
                if self.frame_contract_v3_active:
                    if float(self.SourceTerminalV1MagPu) <= 0:
                        raise ValueError("FrameContractV3 requires SourceTerminalV1MagPu>0")
                    self.network_frame_shift_rad = _network_frame_shift_rad(
                        v1, float(self.SourceTerminalV1AngleDeg)
                    )
                    self.source_angle_reference_offset_rad = (
                        float(self.SourceAngleReferenceOffsetDeg) * pi / 180.0
                    )
                    self.delta = float(self.SourceReferenceAngleDeg) * pi / 180.0
                elif self.frame_contract_v2_active:
                    if float(self.SourceTerminalV1MagPu) <= 0:
                        raise ValueError("FrameContractV2 requires SourceTerminalV1MagPu>0")
                    self.network_frame_shift_rad = _network_frame_shift_rad(
                        v1, float(self.SourceTerminalV1AngleDeg)
                    )
                    self.source_angle_reference_offset_rad = (
                        float(self.SourceAngleReferenceOffsetDeg) * pi / 180.0
                    )
                    self.delta = (
                        float(self.E1AngleDeg) * pi / 180.0
                        + self.source_angle_reference_offset_rad
                    )
                else:
                    self.network_frame_shift_rad = 0.0
                    self.source_angle_reference_offset_rad = 0.0
                    self.delta = float(self.E1AngleDeg) * pi / 180.0
                if self.frozen_e_active:
                    # FrozenEAngleDeg is deliberately in the local DSS
                    # terminal frame.  The replay driver derives it from the
                    # fixed source-terminal V1 frame shift; no dynamic trace
                    # or fitted reader offset enters this path.
                    self.frozen_e_complex = float(self.FrozenEMagPu) * self.vbase_v * complex(
                        cos(float(self.FrozenEAngleDeg) * pi / 180.0),
                        sin(float(self.FrozenEAngleDeg) * pi / 180.0),
                    )
                    self.e_mag = abs(self.frozen_e_complex)
                    self.e_complex = self.frozen_e_complex
                else:
                    local_e_angle = self._local_internal_angle()
                    self.e_complex = self.e_mag * complex(cos(local_e_angle), sin(local_e_angle))
                self.omega = (float(self.InitialSpeedPu) - 1.0) * omega_s
            else:
                self.frame_contract_v2_active = False
                self.frame_contract_v3_active = False
                self.network_frame_shift_rad = 0.0
                self.source_angle_reference_offset_rad = 0.0
                self.e_complex = v1 - i1 * self.z
                self.e_mag = abs(self.e_complex)
                if self.e_mag < 1.0e-9:
                    self.e_complex = v1
                    self.e_mag = abs(v1)
                self.delta = float(phase(self.e_complex))
                self.omega = 0.0
            self.gen.Speed = self.omega
            self.gen.dSpeed = 0.0
            self.gen.Theta = self.delta
            self.gen.dTheta = 0.0
            self.copy_state()

        def _dynamic_current(self, v1: complex) -> complex:
            if not self.frozen_e_active:
                local_e_angle = self._local_internal_angle()
                self.e_complex = self.e_mag * complex(cos(local_e_angle), sin(local_e_angle))
            return (v1 - self.e_complex) / self.z

        def _power_flow_current(self, v1: complex) -> complex:
            # The UserModel I vector is current into the Generator element.
            # PmPu/QmPu describe positive generation, hence the negative sign.
            s_phase = complex(self.PmPu, -self.QmPu) * self.sbase_va / 3.0
            if abs(v1) < 1.0e-12:
                return 0j
            return -s_phase / v1.conjugate()

        def calc(self, V, currents):
            """Return solver-side terminal current for power flow or dynamics."""

            if len(V) < 3 or len(currents) < 3:
                return
            v1 = _positive_sequence(list(V[:3]))[1]
            if self.dyn.SolutionMode == 14:  # DSS SOLUTION_DYNAMICMODE
                i1 = self._dynamic_current(v1)
            else:
                i1 = self._power_flow_current(v1)
            iabc = _phase_values((0j, i1, 0j))
            for index, value in enumerate(iabc):
                currents[index] = value
            for index in range(3, len(currents)):
                currents[index] = 0j

            # Electrical power is positive when the machine injects into the
            # network; the callback current itself is into the Generator.
            generated_power = -3.0 * v1 * i1.conjugate()
            self.electrical_power_pu = generated_power.real / self.sbase_va
            self.electrical_reactive_power_pu = generated_power.imag / self.sbase_va
            omega_s = max(float(self.gen.w0), 2.0 * pi * 50.0)
            speed_pu = 1.0 + float(self.omega) / omega_s
            self.mechanical_torque_pu = _classical_mechanical_torque_pu(
                float(self.PmPu), speed_pu
            )
            power_balance_pu = (
                self.mechanical_torque_pu
                - self.electrical_power_pu
                - float(self.D) * (speed_pu - 1.0)
            )
            self.gen.Pshaft = self.pm_w
            self.gen.Speed = float(self.omega)
            self.gen.Theta = float(self.delta)
            self.gen.dTheta = 0.0 if self.frozen_e_active else float(self.omega)
            domega_dt = (
                0.0
                if self.frozen_e_active
                else omega_s / (2.0 * max(float(self.H), 1.0e-9))
                * power_balance_pu
            )
            # OpenDSS exposes dSpeed through the shared Generator public
            # structure, but the host Generator may refresh that field with
            # its own Model=6 shaft-power derivative before the next callback
            # integration pass.  Keep the UserModel derivative in its own
            # state slot so the two-state contract cannot silently fall back
            # to the host's fixed-power equation.
            self.domega_dt = domega_dt
            self.gen.dSpeed = domega_dt
            self.ElectricalPowerPU = self.electrical_power_pu
            self.MechanicalPowerPU = float(self.PmPu)
            self.MechanicalTorquePU = self.mechanical_torque_pu
            self.UserModelSpeed = float(self.omega)
            self.UserModelTheta = float(self.delta)
            self.InternalVoltagePU = self.e_mag / self.vbase_v
            self.TerminalVoltagePU = abs(v1) / self.vbase_v
            self.PositiveSequenceCurrentPU = (
                abs(i1)
                * sqrt(3.0)
                * float(self.gen.kVGeneratorBase)
                * 1000.0
                / self.sbase_va
            )
            self.NetworkFrameShiftDeg = self.network_frame_shift_rad * 180.0 / pi
            self.TerminalVoltageAngleDeg = phase(v1) * 180.0 / pi
            self.PositiveSequenceCurrentAngleDeg = phase(i1) * 180.0 / pi
            self.InternalVoltageAngleDeg = phase(self.e_complex) * 180.0 / pi
            self.LocalInternalAngleDeg = self._local_internal_angle() * 180.0 / pi
            self.CanonicalRotorAngleDeg = float(self.delta) * 180.0 / pi
            self.ElectricalReactivePowerPU = self.electrical_reactive_power_pu
            self.SpeedDerivativeRadPerS2 = domega_dt
            self.AngleDerivativeRadPerS = float(self.omega)
            self.PowerBalanceResidualPU = power_balance_pu

        def integrate(self):
            """Use the DSS-Python trapezoidal state helper for the swing states."""

            if self.frozen_e_active:
                # Algebraic replay intentionally has no rotor integration.
                # Keep the callback alive for solver iterations while holding
                # the supplied E and both state derivatives exactly fixed.
                if self.dyn.IterationFlag == 0:
                    self.copy_state()
                self.ddelta_dt = 0.0
                self.domega_dt = 0.0
                return
            if self.dyn.IterationFlag == 0:
                self.copy_state()
            self.ddelta_dt = float(self.omega)
            Base.integrate(self)


_register_classical_model()


__all__ = [
    "CLASSICAL_SPEC",
    "CLASSICAL_USER_MODEL",
    "CLASSICAL_USER_MODEL_CLASS",
    "CLASSICAL_USER_MODEL_FAMILY",
    "USER_MODEL_OPTION",
    "USER_MODEL_INITIAL_STATE_OPTION",
    "USER_MODEL_INITIAL_STATE_V1",
    "USER_MODEL_INITIAL_STATE_V2",
    "USER_MODEL_INITIAL_STATE_V3",
    "UserModelSpec",
    "requested_user_model",
    "user_model_initial_states",
    "user_model_generator_properties",
    "user_model_provenance",
]
