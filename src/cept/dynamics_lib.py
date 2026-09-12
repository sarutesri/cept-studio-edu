"""Library of OpenDSS ``DynamicExp`` models for the dynamics study.

OpenDSS Dynamics mode integrates state variables for each Power-Conversion
element. The built-in synchronous Generator already integrates the single-mass
swing equation (rotor speed + angle) using its ``H`` and ``D`` properties, so
basic electromechanical transients need **no** DynamicExp.

``DynamicExp`` (ported into DSS-Extensions in 2023) lets us attach *custom*
differential equations — exciter/AVR, governor, or inverter (GFL/GFM) control —
to a Generator via its ``DynamicEq`` property. The expression uses **Reverse
Polish Notation (RPN)**; statements ``var dt = <rpn>`` are separated by ``;``.

Reference syntax (Kundur Ex. 13.1, EPRI test suite)::

    New DynamicExp.myEq nvariables=6 varnames=[Speed Mass PShaft Pterm Damp theta]
    ~ expression=[Speed dt = -1 Mass / ( Pterm Damp Speed * + Pshaft - ) *; theta dt = Speed]

The Generator then sets ``DynamicEq=myEq``, initialises the state variables
inline, and selects monitor outputs with ``DynOut=[Speed theta]``.

This module exposes validated templates. Each entry is a callable that returns
the full ``New DynamicExp.<name> ...`` command plus the per-generator
initialisation/link snippet.
"""

from __future__ import annotations

from typing import Callable, Optional

# --------------------------------------------------------------------------- #
# Swing equation (classical, validated against Kundur Ex. 13.1)
# --------------------------------------------------------------------------- #
# State: Speed (pu rotor speed deviation × ω0), theta (rotor angle).
# Mass = 2H·Sbase/ω0 ; this is the same model OpenDSS uses internally, exposed
# here so it can be combined with custom exciter/governor terms.

_SWING_DEF = (
    "New DynamicExp.{name} nvariables=6 "
    "varnames=[Speed Mass PShaft Pterm Damp theta] "
    "expression=[Speed dt = -1 Mass / ( Pterm Damp Speed * + Pshaft - ) *; "
    "theta dt = Speed]"
)


def _swing(name: str) -> str:
    return _SWING_DEF.format(name=name)


# --------------------------------------------------------------------------- #
# Grid-following (GFL) inverter current control
# --------------------------------------------------------------------------- #
# From EPRI's IBRDynamics_Cases (GFL_IEEE123_DynExp). State: inverter current
# (it), DC voltage (vdc), modulation (modul), AC voltage (vac). The ODE is the
# inner current-control loop. This runs in OpenDSS Dynamics mode without the
# engine instability that affects the grid-forming (GFM) dynamic path.

_GFL_DEF = (
    "New DynamicExp.{name} nvariables=4 varnames=[it vdc modul vac] "
    "expression=[it dt = 1 0.61059E-3 / "
    "( -0.230187 it * modul vdc * + vac - ) *]"
)


def _gfl(name: str) -> str:
    return _GFL_DEF.format(name=name)


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
# name -> builder(name) returning the "New DynamicExp..." command.
DYNEQ_LIBRARY: dict[str, Callable[[str], str]] = {
    "swing": _swing,
    "gfl": _gfl,
}

# Inverter PVSystem/Storage extra properties required by the GFL DynamicExp.
GFL_PV_PROPS = "%R=50 %X=50 kP=0.01 KVDC=0.700 PITol=0.1 SafeVoltage=0"


def dyneq_command(model: str, name: str) -> Optional[str]:
    """Return the DSS command defining DynamicExp ``name`` for ``model``,
    or None if the model is unknown (caller falls back to built-in dynamics)."""
    builder = DYNEQ_LIBRARY.get(model.lower())
    return builder(name) if builder else None


def swing_init_snippet(mass: float, damp: float) -> str:
    """Generator init for the 'swing' DynamicExp: bind PShaft/Pterm/Speed/theta
    and provide Mass/Damp, then expose Speed+theta to monitors."""
    return f"Damp={damp} PShaft=P0 Pterm=P Speed=0 theta=Edp Mass={mass} DynOut=[Speed theta]"
