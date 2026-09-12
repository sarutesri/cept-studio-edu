"""Optional OpenDER runtime boundary.

The network remains solved by OpenDSS.  This module only attests availability
of the separately pinned model runtime and keeps the PV/BESS exchange explicit.
"""

from __future__ import annotations

import importlib
import math
from pathlib import Path
from typing import Any

from cept.schema.case import OpenDERSpec
from pydantic import BaseModel, ConfigDict, Field


class OpenDERUnavailable(RuntimeError):
    """The Case opted into OpenDER but the optional runtime is unavailable."""


class OpenDERStepMismatch(ValueError):
    """The caller clock time does not align with the pinned OpenDER timestep.

    A co-simulation exchange is explicit and timestep-coupled: asking OpenDER
    for a step off the pinned ``timestep_s`` clock would produce a value that
    is not the one the solver clock describes, so it fails closed.
    """


class OpenDEROuterSolveFailed(RuntimeError):
    """The outer OpenDSS network solve did not converge.

    No OpenDER exchange is valid without a converged outer solve: a record
    marked ``CO_SIMULATED`` means an explicit timestep-coupled exchange with a
    converged network solve, never a re-labelled native value.
    """


class OpenDERExchange(BaseModel):
    """One explicit terminal exchange between OpenDER and a network solver.

    Typed co-simulation exchange record — engine-specific (the OpenDSS network
    solve owns the outer clock; the pinned OpenDER runtime owns the inverter
    state), no generic co-sim framework.  It documents the six dimensions of
    the co-simulation contract:

    * **exchanged signals with units/sign** — ``v_pu, f_hz, p_kw, q_kvar``
      plus :attr:`signal_units` / :attr:`signal_sign`;
    * **engine-owned states** — :attr:`state_owner` maps each scalar to the
      engine that publishes it (``network`` = OpenDSS, ``runtime`` = OpenDER,
      ``clock`` = the co-simulation orchestrator);
    * **init protocol** — :attr:`init_protocol`;
    * **timestep/clock** — :attr:`t_s`, :attr:`clock_domain`,
      :attr:`solver_step_s`;
    * **failure propagation** — :attr:`failure` (``None`` on a clean exchange;
      a typed error otherwise — a failure aborts the outer solve, it is never
      downgraded to a green value);
    * **envelope reference** — :attr:`envelope`.

    ``CO_SIMULATED`` keeps its load-bearing meaning: a record is CO_SIMULATED
    only when produced by an explicit timestep-coupled exchange through
    :func:`run_model_step` at the solver clock, never by re-labelling a native
    OpenDSS value and never by a Python substitution of the model.
    """

    model_config = ConfigDict(extra="forbid")

    # --- exchanged signals ---
    t_s: float = Field(description="Clock time of the exchange, seconds (solver clock).")
    v_pu: float = Field(description="Network bus voltage magnitude in pu of nominal.")
    f_hz: float = Field(description="Network frequency in Hz.")
    p_kw: float = Field(description="DER real-power output in kW (sign in signal_sign).")
    q_kvar: float = Field(description="DER reactive-power output in kvar (sign in signal_sign).")
    model: str = Field(description="Runtime identity: 'pv' or 'bess'.")

    # --- exchanged signals: units + sign convention ---
    signal_units: dict[str, str] = Field(
        default_factory=lambda: {
            "t_s": "s",
            "v_pu": "pu of nominal",
            "f_hz": "Hz",
            "p_kw": "kW",
            "q_kvar": "kvar",
        },
        description="SI/electrical unit of each exchanged signal.",
    )
    signal_sign: dict[str, str] = Field(
        default_factory=lambda: {
            "v_pu": "positive magnitude",
            "f_hz": "positive frequency",
            "p_kw": "positive = DER exports real power to the network",
            "q_kvar": "positive = DER supplies inductive vars (OpenDER supply-positive convention)",
        },
        description="Sign convention of each exchanged signal.",
    )

    # --- engine-owned states / ownership split ---
    state_owner: dict[str, str] = Field(
        default_factory=lambda: {
            "t_s": "clock",
            "v_pu": "network",
            "f_hz": "network",
            "p_kw": "runtime",
            "q_kvar": "runtime",
        },
        description="Which engine owns each scalar: 'network' = OpenDSS, "
        "'runtime' = OpenDER, 'clock' = co-simulation orchestrator.",
    )

    # --- init protocol ---
    init_protocol: str = Field(
        default="solver-solved OpenDSS network; OpenDER state advanced once at t_s",
        description="How this exchange was initialized; never a Python substitution.",
    )

    # --- timestep / clock ---
    clock_domain: str = Field(
        default="network-solver-clock",
        description="Simulation clock that drives the exchange.",
    )
    solver_step_s: float | None = Field(
        default=None,
        description="Pinned OpenDER timestep_s from the spec (None = time-invariant snapshot).",
    )

    # --- failure propagation (fail-closed) ---
    failure: str | None = Field(
        default=None,
        description="Named failure reason; None = clean exchange. A failure aborts the "
        "outer solve and is never recorded as a green value.",
    )

    # --- envelope reference ---
    envelope: str = Field(
        default="snapshot",
        description="Simulation envelope of this exchange, e.g. 'snapshot' or 'qsts@t=60s'.",
    )


def _aligned_to_timestep(t_s: float, timestep_s: float | None, *, atol_s: float = 1e-6) -> bool:
    """True when ``t_s`` sits on the pinned timestep grid (or no grid is pinned)."""
    if timestep_s is None or timestep_s <= 0:
        return True
    if abs(t_s) <= 1e-9:
        return True
    remainder = abs(t_s % timestep_s)
    return remainder <= atol_s or abs(remainder - timestep_s) <= atol_s


def _envelope_for(spec: OpenDERSpec, t_s: float) -> str:
    if spec.mode == "snapshot":
        return "snapshot"
    return f"{spec.mode}@t={t_s:g}s"


def _init_protocol(spec: OpenDERSpec) -> str:
    if spec.mode == "snapshot":
        return "solver-solved OpenDSS network; single time-invariant OpenDER step at t=0"
    return (
        "solver-solved OpenDSS network; OpenDER state advanced from the previous "
        f"converged step at the pinned timestep_s={spec.timestep_s:g}s clock"
    )


def run_model_step(
    spec: OpenDERSpec,
    *,
    settings_root: str | Path,
    v_pu: float,
    f_hz: float,
    t_s: float = 0.0,
    available_power_kw: float | None = None,
    demand_power_kw: float | None = None,
    runtime_state: dict | None = None,
    model_key: str | None = None,
    network_converged: bool = True,
) -> OpenDERExchange:
    """Run one pinned OpenDER step; never substitutes a CEPT/Python model.

    The network adapter owns the outer OpenDSS solve.  This helper only invokes
    the upstream model with the explicit voltage/frequency and returns P/Q.

    Fail-closed exchange contract:

    * ``network_converged=False`` — the outer OpenDSS solve did not converge,
      so no exchange is valid (:class:`OpenDEROuterSolveFailed`); an exchange
      is only ``CO_SIMULATED`` against a converged outer solve.
    * ``t_s`` not aligned to the pinned ``spec.timestep_s`` clock
      (:class:`OpenDERStepMismatch`).
    * missing runtime (:class:`OpenDERUnavailable`), missing pinned Common File
      Format CSVs (``FileNotFoundError``), or a non-finite terminal exchange —
      never a silent Python fallback.
    """
    if not network_converged:
        raise OpenDEROuterSolveFailed(
            "outer OpenDSS solve did not converge; refusing an OpenDER exchange — "
            "CO_SIMULATED requires a converged timestep-coupled solve"
        )
    require_runtime(spec)
    if not _aligned_to_timestep(t_s, spec.timestep_s):
        raise OpenDERStepMismatch(
            f"OpenDER clock t_s={t_s:g}s is not aligned to the pinned "
            f"timestep_s={spec.timestep_s:g}s co-simulation clock"
        )
    if spec.as_file_path is None or spec.model_file_path is None:
        raise ValueError("OpenDER step requires as_file_path and model_file_path")
    root = Path(settings_root).resolve()
    as_path = (root / spec.as_file_path).resolve()
    model_path = (root / spec.model_file_path).resolve()
    if not as_path.is_file() or not model_path.is_file():
        raise FileNotFoundError("OpenDER applied-settings/model CSV is missing")
    module = importlib.import_module("opender.der_pv" if spec.model == "pv" else "opender.der_bess")
    model_class = module.DER_PV if spec.model == "pv" else module.DER_BESS
    cache_key = model_key or spec.model
    models = (runtime_state or {}).setdefault("_opender_models", {})
    model = models.get(cache_key)
    if model is None:
        if spec.timestep_s is not None:
            model_class.t_s = spec.timestep_s
        model = model_class(as_file_path=as_path, model_file_path=model_path)
        if runtime_state is not None:
            models[cache_key] = model
    if spec.model == "pv":
        if available_power_kw is None:
            raise ValueError("PV OpenDER step requires available_power_kw")
        model.update_der_input(v_pu=v_pu, f=f_hz, p_dc_kw=available_power_kw)
    else:
        if demand_power_kw is None:
            raise ValueError("BESS OpenDER step requires demand_power_kw")
        model.update_der_input(v_pu=v_pu, f=f_hz, p_dem_kw=demand_power_kw)
    model.run()
    p_kw, q_kvar = model.get_der_output("PQ_kVA")
    values = (t_s, v_pu, f_hz, p_kw, q_kvar)
    if not all(isinstance(value, (int, float)) and math.isfinite(float(value)) for value in values):
        raise ValueError("OpenDER returned a non-finite terminal exchange")
    return OpenDERExchange(
        t_s=t_s,
        v_pu=v_pu,
        f_hz=f_hz,
        p_kw=p_kw,
        q_kvar=q_kvar,
        model=spec.model,
        solver_step_s=spec.timestep_s,
        envelope=_envelope_for(spec, t_s),
        init_protocol=_init_protocol(spec),
        clock_domain="network-solver-clock",
    )


def availability() -> dict[str, Any]:
    try:
        module = importlib.import_module("opender")
    except Exception as exc:
        return {"available": False, "reason": f"opender import failed: {type(exc).__name__}: {exc}"}
    return {
        "available": True,
        "version": getattr(module, "__version__", None),
        "module": getattr(module, "__file__", None),
        "source_commit_verified": False,
        "note": "Package availability is not a source-commit attestation; pinning remains required in the Case.",
    }


def validate_spec(spec: OpenDERSpec) -> list[str]:
    errors: list[str] = []
    if spec.model == "bess" and not spec.interface_commit:
        errors.append("OpenDER BESS requires an interface_commit pin")
    if not spec.source_repo.startswith("https://"):
        errors.append("OpenDER source_repo must be an HTTPS repository URL")
    return errors


def require_runtime(spec: OpenDERSpec) -> None:
    errors = validate_spec(spec)
    if errors:
        raise ValueError("invalid OpenDER spec: " + "; ".join(errors))
    state = availability()
    if not state["available"]:
        raise OpenDERUnavailable(state["reason"])


__all__ = [
    "OpenDERExchange",
    "OpenDEROuterSolveFailed",
    "OpenDERStepMismatch",
    "OpenDERUnavailable",
    "availability",
    "require_runtime",
    "run_model_step",
    "validate_spec",
]
