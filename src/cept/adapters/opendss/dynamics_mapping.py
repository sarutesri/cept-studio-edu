"""Reviewed model-family mappings for OpenDSS RMS dynamics."""

from __future__ import annotations

from typing import Any

from cept.dynamics_contract import (
    GENCLS_CLASSICAL,
    GENCLS_FAMILY,
    GENCLS_USERMODEL,
    GENCLS_USERMODEL_FAMILY,
    requested_dynamic_model,
)
from cept.schema.case import Case, GenrouDynamics


ENGINE_SOURCES = [
    "https://opendss.epri.com/GeneratorDynamicsModel.html",
    "https://opendss.epri.com/OpenDSSDynamicsMode.html",
    "https://opendss.epri.com/Properties9.html",
]
POWERFACTORY_DYNAMIC_SOURCES = [
    "https://www.digsilent.de/en/faq-reader-powerfactory/how-does-powerfactory-interpret-p-u-values-for-turbine-and-generator-models.html",
    "https://www.digsilent.me/dme/filedata/fetch?id=569",
]

CLASSICAL_SINGLE_MASS = "classical-single-mass-generator"


def _xr_dp(machine: GenrouDynamics) -> float:
    """Return OpenDSS's ``XRdp`` representation of the Case armature R.

    The built-in OpenDSS Generator model does not expose a direct ``Ra``
    property.  Its equivalent is the dimensionless ``Xdp/R`` ratio.  A zero
    Case resistance is represented by the large finite value used by the
    reviewed DSS-Extensions Kundur example; using a finite value keeps the
    generated DSS input portable across C-API builds.
    """

    # Older DER ``MachineDynamics`` cases predate the full synchronous-machine
    # container and have no explicit Ra field.  Preserve their historical
    # zero-resistance behavior while the richer GenrouDynamics maps verbatim.
    ra = float(getattr(machine, "ra", 0.0))
    if ra < 0:
        raise ValueError("OpenDSS generator armature resistance Ra must be non-negative")
    if ra == 0:
        return 1.0e12
    return machine.xdp / ra


def generator_dynamic_properties(machine: GenrouDynamics) -> str:
    """Render the OpenDSS properties supported by the mapped family."""
    return (
        f" H={machine.h} D={machine.d} Xd={machine.xd} Xdp={machine.xdp} "
        f"Xdpp={machine.xdpp} XRdp={_xr_dp(machine):.12g}"
    )


def dynamic_model_mapping(case: Case) -> dict[str, Any]:
    """Return auditable family/equation/init/source evidence for a Case."""
    from cept.adapters.opendss.user_models import requested_user_model

    inline = case.network.inline
    generators = [
        gen
        for gen in (inline.generators if inline is not None else [])
        if gen.bus_type != "slack" and gen.dynamics is not None
    ]
    provenance = case.provenance
    user_model = requested_user_model(case)
    reduced_model = requested_dynamic_model(case)
    if reduced_model == GENCLS_CLASSICAL:
        if user_model is not None:
            raise ValueError("GENCLS benchmark and DSS-Python UserModel cannot be selected together.")
        return _gencls_mapping(generators, provenance)
    if user_model is not None:
        return _user_model_mapping(case, generators, provenance, user_model)
    return {
        "engine": "opendss",
        "status": "supported",
        "families": [
            {
                "model_family": CLASSICAL_SINGLE_MASS,
                "instances": [gen.name for gen in generators],
                "mapping_status": "reviewed",
                "equations": [
                    "d(delta)/dt = omega_s * (omega - 1)",
                    "2H * d(omega)/dt = Pm - Pe - D * (omega - 1)",
                ],
                "initialization": {
                    "status": "load_flow_preconditioned",
                    "required": "Solve the pre-disturbance load flow before Set mode=dynamics.",
                    "evidence": "OpenDSS load-flow solve immediately precedes dynamic mode in the adapter.",
                    "state_capture": "not_claimed",
                },
                "mapped_parameters": {
                    "H": {"case": "dynamics.h", "opendss": "H", "unit": "s"},
                    "D": {"case": "dynamics.d", "opendss": "D", "unit": "pu torque/pu speed"},
                    "Xd": {"case": "dynamics.xd", "opendss": "Xd", "unit": "pu"},
                    "Xdp": {"case": "dynamics.xdp", "opendss": "Xdp", "unit": "pu"},
                    "Xdpp": {"case": "dynamics.xdpp", "opendss": "Xdpp", "unit": "pu"},
                },
                "channel_mappings": [
                    {
                        "source_quantity": "Speed",
                        "source_channels": ["s:xspeed"],
                        "candidate_channels": ["Frequency"],
                        "candidate_monitor_mode": 3,
                        "unit_conversion": "Hz -> p.u. using Case network.frequency_hz",
                        "sign_conversion": "none",
                        "mapping_status": "reviewed",
                        "source_evidence": ENGINE_SOURCES,
                    },
                    {
                        "source_quantity": "Turbine Power",
                        "source_channels": ["s:pt"],
                        "candidate_channels": ["PShaft"],
                        "candidate_monitor_mode": 3,
                        "unit_conversion": "W -> p.u. using source rated generator active power (MVA * cosn)",
                        "sign_conversion": "none",
                        "mapping_status": "reviewed",
                        "source_evidence": ENGINE_SOURCES + POWERFACTORY_DYNAMIC_SOURCES,
                    },
                    {
                        "source_quantity": "Positive-sequence, active power",
                        "source_channels": ["s:P1"],
                        "candidate_channels": ["P1 (kW)"],
                        "candidate_monitor_mode": 65,
                        "unit_conversion": "kW -> MW",
                        "sign_conversion": "negate monitored terminal power into Generator",
                        "mapping_status": "reviewed",
                        "source_evidence": ENGINE_SOURCES,
                    },
                    {
                        "source_quantity": "Electrical Power",
                        "source_channels": ["s:pgt"],
                        "candidate_channels": ["P1 (kW)"],
                        "candidate_monitor_mode": 65,
                        "unit_conversion": "kW -> p.u. using source rated generator active power (MVA * cosn)",
                        "sign_conversion": "negate monitored terminal power into Generator",
                        "mapping_status": "reviewed",
                        "source_evidence": ENGINE_SOURCES + POWERFACTORY_DYNAMIC_SOURCES,
                    },
                    {
                        "source_quantity": "Positive-sequence, reactive power",
                        "source_channels": ["s:Q1"],
                        "candidate_channels": ["Q1 (kvar)"],
                        "candidate_monitor_mode": 65,
                        "unit_conversion": "kvar -> Mvar",
                        "sign_conversion": "negate monitored terminal power into Generator",
                        "mapping_status": "reviewed",
                        "source_evidence": ENGINE_SOURCES,
                    },
                    {
                        "source_quantity": "Positive-sequence current, magnitude",
                        "source_channels": ["s:cur1"],
                        "candidate_channels": ["I", "I1 (A)"],
                        "candidate_monitor_mode": 112,
                        "unit_conversion": "A -> p.u. using generator MVA/kV base",
                        "sign_conversion": "magnitude (non-negative)",
                        "mapping_status": "reviewed",
                        "source_evidence": ENGINE_SOURCES,
                    },
                    {
                        "source_quantity": "Terminal voltage",
                        "source_channels": ["s:ut"],
                        "candidate_channels": ["V"],
                        "candidate_monitor_mode": 112,
                        "unit_conversion": "V -> p.u. using generator kV base",
                        "sign_conversion": "magnitude (non-negative)",
                        "mapping_status": "reviewed",
                        "source_evidence": ENGINE_SOURCES,
                    },
                ],
                "unsupported_case_fields": [
                    "xq",
                    "xqp",
                    "xqpp",
                    "xl",
                    "td0p",
                    "tq0p",
                    "ra",
                ],
                "source_evidence": {
                    "engine_semantics": ENGINE_SOURCES,
                    "case_source": (
                        {
                            "status": "linked",
                            "source_manifest": provenance.source_manifest,
                            "source_manifest_sha256": provenance.source_manifest_sha256,
                        }
                        if provenance is not None
                        else {
                            "status": "missing",
                            "required": "Attach Case provenance before a research claim.",
                        }
                    ),
                },
            }
        ],
        "blocked_families": [
            {
                "model_family": "powerfactory-wecc-composite",
                "mapping_status": "blocked",
                "reason": "Requires reviewed REGC_A/REEC_A/REPC_A equations and initialization.",
            },
        ],
    }


def _gencls_mapping(generators: list, provenance) -> dict[str, Any]:
    """Return the shared classical benchmark contract for OpenDSS."""

    return {
        "engine": "opendss",
        "status": "benchmark",
        "families": [
            {
                "model_family": GENCLS_FAMILY,
                "instances": [gen.name for gen in generators],
                "mapping_status": "reviewed",
                "model_object": "OpenDSS built-in Generator single-mass electromechanical model",
                "equations": [
                    "d(delta)/dt = omega_s * (omega - 1)",
                    "2H * d(omega)/dt = Pm - Pe - D * (omega - 1)",
                    "I1 = (V1 - E1) / (Ra + jXd')",
                ],
                "initialization": {
                    "status": "load_flow_preconditioned",
                    "required": "Solve the pre-disturbance load flow before Set mode=dynamics.",
                    "evidence": "OpenDSS load-flow solve immediately precedes dynamic mode in the adapter.",
                    "state_capture": "solver monitor first sample",
                },
                "mapped_parameters": {
                    "H": {"case": "dynamics.h", "opendss": "H", "unit": "s"},
                    "D": {"case": "dynamics.d", "opendss": "D", "unit": "pu torque/pu speed"},
                    "Xdp": {"case": "dynamics.xdp", "opendss": "Xdp", "unit": "pu"},
                    "Ra": {
                        "case": "dynamics.ra",
                        "opendss": "XRdp",
                        "unit": "pu represented through Xdp/XRdp",
                        "conversion": "XRdp = Xdp / Ra; Ra=0 is emitted as XRdp=1e12",
                    },
                },
                "channel_mappings": [
                    {
                        "source_quantity": "Speed",
                        "source_channels": ["s:xspeed"],
                        "candidate_channels": ["Frequency"],
                        "candidate_monitor_mode": 3,
                        "unit_conversion": "Hz -> p.u. using Case network.frequency_hz",
                        "sign_conversion": "none",
                        "mapping_status": "reviewed",
                        "source_evidence": ENGINE_SOURCES,
                    },
                    {
                        "source_quantity": "Turbine Power",
                        "source_channels": ["s:pt"],
                        "candidate_channels": ["PShaft"],
                        "candidate_monitor_mode": 3,
                        "unit_conversion": "W -> p.u. using source rated generator active power",
                        "sign_conversion": "none",
                        "mapping_status": "reviewed",
                        "source_evidence": ENGINE_SOURCES + POWERFACTORY_DYNAMIC_SOURCES,
                    },
                    {
                        "source_quantity": "Electrical Power",
                        "source_channels": ["m:P:bus1"],
                        "candidate_channels": ["P1 (kW)"],
                        "candidate_monitor_mode": 65,
                        "unit_conversion": "kW -> p.u. using source rated generator active power",
                        "sign_conversion": "negate monitored terminal power into Generator",
                        "mapping_status": "reviewed",
                        "source_evidence": ENGINE_SOURCES + POWERFACTORY_DYNAMIC_SOURCES,
                    },
                    {
                        "source_quantity": "Positive-sequence, reactive power",
                        "source_channels": ["m:Q:bus1"],
                        "candidate_channels": ["Q1 (kvar)"],
                        "candidate_monitor_mode": 65,
                        "unit_conversion": "kvar -> Mvar",
                        "sign_conversion": "negate monitored terminal power into Generator",
                        "mapping_status": "reviewed",
                        "source_evidence": ENGINE_SOURCES,
                    },
                    {
                        "source_quantity": "Positive-sequence current, magnitude",
                        "source_channels": ["s:cur1"],
                        "candidate_channels": ["I", "I1 (A)"],
                        "candidate_monitor_mode": 112,
                        "unit_conversion": "A -> p.u. using generator MVA/kV base",
                        "sign_conversion": "magnitude (non-negative)",
                        "mapping_status": "reviewed",
                        "source_evidence": ENGINE_SOURCES,
                    },
                    {
                        "source_quantity": "Terminal voltage",
                        "source_channels": ["s:ut"],
                        "candidate_channels": ["V"],
                        "candidate_monitor_mode": 112,
                        "unit_conversion": "V -> p.u. using generator kV base",
                        "sign_conversion": "magnitude (non-negative)",
                        "mapping_status": "reviewed",
                        "source_evidence": ENGINE_SOURCES,
                    },
                ],
                "unsupported_case_fields": [
                    "xq",
                    "xqp",
                    "xqpp",
                    "xdpp",
                    "xl",
                    "td0p",
                    "tq0p",
                    "td0pp",
                    "tq0pp",
                    "avr/governor controls",
                ],
                "reduction": {
                    "status": "reviewed",
                    "method": "same GENCLS contract; PowerFactory uses its Classical model",
                    "note": (
                        "PowerFactory receives TypSym.i_extModel=1 and xstr; OpenDSS receives "
                        "native Generator Model=1 with H/D/Xd/Xdp/Xdpp. OpenDSS has no direct Ra "
                        "property for this built-in model; CEPT emits "
                        "XRdp=Xdp/Ra and uses XRdp=1e12 when Ra=0."
                    ),
                },
                "source_evidence": {
                    "engine_semantics": ENGINE_SOURCES,
                    "case_source": (
                        {
                            "status": "linked",
                            "source_manifest": provenance.source_manifest,
                            "source_manifest_sha256": provenance.source_manifest_sha256,
                        }
                        if provenance is not None
                        else {
                            "status": "missing",
                            "required": "Attach Case provenance before a research claim.",
                        }
                    ),
                },
            }
        ],
        "blocked_families": [
            {
                "model_family": "powerfactory-typ_sym-intrinsic",
                "mapping_status": "blocked",
                "reason": "The full-order TypSym lane is not this GENCLS benchmark contract.",
            }
        ],
    }


def _user_model_mapping(case: Case, generators: list, provenance, user_model) -> dict[str, Any]:
    """Describe the opt-in UserModel lane without promoting it to GENROU."""

    parity_lane = requested_dynamic_model(case) == GENCLS_USERMODEL
    from cept.adapters.opendss.user_models import USER_MODEL_INITIAL_STATE_OPTION

    return {
        "engine": "opendss",
        "status": "experimental-parity-lane" if parity_lane else "experimental",
        "families": [
            {
                "model_family": GENCLS_USERMODEL_FAMILY if parity_lane else user_model.model_family,
                "instances": [generator.name for generator in generators],
                "mapping_status": "runtime-selected-locked-state" if parity_lane else "runtime-selected",
                "model_object": "OpenDSS Generator Model=6 + DSS-Python GenUserModel",
                "equations": list(user_model.equations),
                "initialization": {
                    "status": "locked-case-contract" if parity_lane else "load_flow_preconditioned",
                    "evidence": (
                        "UserModel receives the source-bound internal-emf/speed contract derived from "
                        "PowerFactory solver initialization."
                        if parity_lane
                        else "UserModel Init receives the pre-disturbance solver terminal V/I snapshot."
                    ),
                    "option": USER_MODEL_INITIAL_STATE_OPTION if parity_lane else None,
                },
                "mapped_parameters": {
                    "H": {"case": "dynamics.h", "usermodel": "H", "unit": "s"},
                    "D": {"case": "dynamics.d", "usermodel": "D", "unit": "pu torque/pu speed"},
                    "Xdp": {"case": "dynamics.xdp", "usermodel": "Xdp", "unit": "pu"},
                    "Ra": {"case": "dynamics.ra", "usermodel": "Ra", "unit": "pu"},
                    "PmPu": {
                        "case": "generator.kw / (generator.mva * 1000)",
                        "usermodel": "PmPu",
                        "unit": "pu on aggregate generator kVA base",
                    },
                    "QmPu": {
                        "case": "generator.q_mvar or generator.pf",
                        "usermodel": "QmPu",
                        "unit": "pu on aggregate generator kVA base",
                    },
                },
                "unsupported_case_fields": [
                    "xq",
                    "xqp",
                    "xqpp",
                    "xd",
                    "xdpp",
                    "xl",
                    "td0p",
                    "tq0p",
                    "td0pp",
                    "tq0pp",
                    "avr/governor controls",
                ],
                "source_evidence": {
                    "engine_semantics": ENGINE_SOURCES
                    + ["https://dss-extensions.org/DSS-Python/examples/UserModels/PyIndMach012/README.html"],
                    "case_source": (
                        {
                            "status": "linked",
                            "source_manifest": provenance.source_manifest,
                            "source_manifest_sha256": provenance.source_manifest_sha256,
                        }
                        if provenance is not None
                        else {
                            "status": "missing",
                            "required": "Attach Case provenance before a research claim.",
                        }
                    ),
                },
            }
        ],
        "blocked_families": [
            {
                "model_family": "powerfactory-typ_sym-intrinsic",
                "mapping_status": "not-equivalent",
                "reason": "The opt-in UserModel is a bounded classical transient model, not a TypSym sixth-order implementation.",
            },
            {
                "model_family": "powerfactory-wecc-composite",
                "mapping_status": "blocked",
                "reason": "Requires reviewed REGC_A/REEC_A/REPC_A equations and initialization.",
            },
        ],
    }
