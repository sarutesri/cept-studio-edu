"""Canonical dynamic-model selections shared by engine adapters.

The normal RMS path carries the full generator parameter container to each
engine.  A benchmark that is intended to compare trajectories needs one more
piece of information: which reduced model contract is being requested.  This
module keeps that selection small and fail-closed so an adapter cannot silently
turn a full TypSym case into a classical-machine comparison. The selected
GENCLS lane is implemented by each engine's actual reduced classical model,
not by perturbing a full-order machine until it looks classical.
"""

from __future__ import annotations

from typing import Any

from cept.schema.case import Case, GenrouDynamics


DYNAMIC_MODEL_OPTION = "dynamic_model"
GENCLS_CLASSICAL = "gencls-classical"
GENCLS_USERMODEL = "gencls-classical-usermodel"
GENCLS_FAMILY = "gencls-classical-single-mass"
GENCLS_USERMODEL_FAMILY = "gencls-classical-locked-state"

# Shared identity/schema values for the reviewed Classical UserModel lane.
# These are contract identifiers, not imports from the DSS implementation, so
# validators and the two engine adapters can inspect the same names without
# violating the domain import boundary.
USER_MODEL_INITIAL_STATE_V1 = "cept-usermodel-initial-state-v1"
USER_MODEL_INITIAL_STATE_V2 = "cept-usermodel-initial-state-v2"
USER_MODEL_INITIAL_STATE_V3 = "cept-usermodel-initial-state-v3"
CLASSICAL_USER_MODEL = "cept-classical-synchronous"
CLASSICAL_USER_MODEL_CLASS = "CEPTClassicalSynchronous"
CLASSICAL_USER_MODEL_FAMILY = "dss-python-classical-synchronous-user-model"


def is_gencls_model(model: str | None) -> bool:
    """Return whether ``model`` selects one of CEPT's two-state GENCLS lanes."""

    return model in {GENCLS_CLASSICAL, GENCLS_USERMODEL}


def requested_dynamic_model(case: Case) -> str | None:
    """Return the explicit reduced dynamic-model contract, if selected.

    The option is intentionally opt-in.  Existing Cases therefore preserve
    their ordinary PowerFactory TypSym/OpenDSS built-in behavior.  Unknown
    values are rejected before either solver is called.
    """

    requested: Any = case.study.options.get(DYNAMIC_MODEL_OPTION)
    if requested in (None, "", False, "builtin", "none"):
        return None
    if isinstance(requested, dict):
        requested = requested.get("name")
    if requested not in {GENCLS_CLASSICAL, GENCLS_USERMODEL}:
        raise ValueError(
            f"Unsupported dynamic model {requested!r}; expected "
            f"{GENCLS_CLASSICAL!r}, {GENCLS_USERMODEL!r}, or omit "
            f"{DYNAMIC_MODEL_OPTION!r}."
        )
    if case.study.type not in {"dynamics", "dynamics_rms"}:
        raise ValueError(
            f"{DYNAMIC_MODEL_OPTION!r}={requested!r} is only valid for "
            "study.type='dynamics' or 'dynamics_rms'."
        )
    inline = case.network.inline
    if inline is None:
        raise ValueError("The GENCLS benchmark contract requires an inline Case network.")
    for generator in inline.generators:
        if generator.bus_type == "slack":
            continue
        if generator.dynamics is None:
            raise ValueError(
                "The GENCLS benchmark contract requires explicit generator dynamics for "
                f"'{generator.name}'."
            )
        validate_gencls_parameters(generator.dynamics, generator.name)
    return str(requested)


def validate_gencls_parameters(machine: GenrouDynamics, generator_name: str = "generator") -> None:
    """Validate the quantities that actually define the reduced GENCLS lane.

    PowerFactory Classical and OpenDSS Generator Model=1 both use the
    single-mass swing contract. PowerFactory's Classical model takes the
    internal-emf reactance from ``xstr``; OpenDSS takes ``Xd'``. Requiring
    these to agree prevents a hidden rotor-angle reference mismatch. The
    remaining ``GenrouDynamics`` fields are retained for the canonical Case
    container but are deliberately not used by this selected reduced lane.
    """

    for label, value in (("H", machine.h), ("D", machine.d), ("Xd'", machine.xdp)):
        if not isinstance(value, (int, float)) or float(value) != float(value):
            raise ValueError(
                f"GENCLS benchmark generator '{generator_name}' requires finite {label}."
            )
    if machine.h <= 0 or machine.d < 0 or machine.xdp <= 0:
        raise ValueError(
            f"GENCLS benchmark generator '{generator_name}' requires H>0, D>=0, and Xd'>0."
        )
    if machine.powerfactory_xstr is None or abs(float(machine.powerfactory_xstr) - float(machine.xdp)) > 5.0e-5:
        raise ValueError(
            f"GENCLS benchmark generator '{generator_name}' requires PowerFactory xstr "
            "to match OpenDSS Xd' so the internal-emf reference is the same reduced-machine point."
        )


__all__ = [
    "DYNAMIC_MODEL_OPTION",
    "GENCLS_CLASSICAL",
    "GENCLS_USERMODEL",
    "GENCLS_FAMILY",
    "GENCLS_USERMODEL_FAMILY",
    "USER_MODEL_INITIAL_STATE_V1",
    "USER_MODEL_INITIAL_STATE_V2",
    "USER_MODEL_INITIAL_STATE_V3",
    "CLASSICAL_USER_MODEL",
    "CLASSICAL_USER_MODEL_CLASS",
    "CLASSICAL_USER_MODEL_FAMILY",
    "is_gencls_model",
    "requested_dynamic_model",
    "validate_gencls_parameters",
]
