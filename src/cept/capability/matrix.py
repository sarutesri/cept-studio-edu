"""Execution-feasibility capability matrix reader (L4).

``capability-matrix.json`` is the authority for whether CEPT may execute a
``(study_type, engine, representation_family)`` combination, including its
supported/unsupported quantities, required inputs, and named blocking gate.
It is deliberately *not* the authority for engine qualification or evidence
claims; those belong to :mod:`cept.engine_passports`.

The concrete execution resolver intentionally lives in ``cept.capability``
and not in ``cept.ports`` so the ports package remains an engine-neutral
interface boundary.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any

from cept.ports.capabilities import ExecutionCapability
from cept.schema.case import Case

_WILDCARD = "*"
_DEFAULT_ENGINE = "opendss"


class CapabilityBlocked(NotImplementedError):
    """A requested study/engine/representation is not a CEPT capability.

    Carries the *named* reason from the matrix (never a silent conversion).
    """

    def __init__(
        self,
        study_type: str,
        engine: str | None,
        representation: str,
        reason: str,
    ) -> None:
        self.study_type = study_type
        self.engine = engine
        self.representation = representation
        self.reason = reason
        super().__init__(
            f"study_type={study_type!r} engine={engine!r} representation={representation!r} "
            f"is not a supported CEPT capability: {reason}"
        )


@lru_cache(maxsize=1)
def load_capability_matrix() -> dict[str, Any]:
    """Load and validate the packaged capability matrix."""
    path = resources.files("cept").joinpath("capability").joinpath("capability-matrix.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("invalid packaged capability-matrix.json: bad schema_version")
    if not isinstance(payload.get("study_types"), dict):
        raise ValueError("invalid packaged capability-matrix.json: missing study_types")
    if not isinstance(payload.get("representations"), list):
        raise ValueError("invalid packaged capability-matrix.json: missing representations")
    return payload


def _representation_for_case(case: Case) -> str:
    kind = case.network.kind
    if kind not in ("inline", "dss_file", "builtin"):
        raise CapabilityBlocked(
            case.study.type,
            case.engine,
            str(kind),
            f"unrecognized network.kind={kind!r}; no representation family declared in the capability matrix.",
        )
    return kind


def _engine_for_case(case: Case) -> str | None:
    """Resolve the engine the matrix row applies to.

    Mirrors ``engine_passports.capability_for_case``: EMT takes its engine
    from the EMTSpec, everything else from ``case.engine``; when unset the
    default engine is used by the caller's wildcard lookup.
    """
    if case.study.type == "emt":
        return case.emt.engine if case.emt is not None else None
    return case.engine


def _cell(
    study_type: str,
    engine: str | None,
    representation: str,
) -> dict[str, Any]:
    """Look up a matrix cell with ``*`` wildcard fallback for engine and representation."""
    matrix = load_capability_matrix()

    reserved = matrix.get("reserved_study_types", {})
    if study_type in reserved:
        entry = reserved[study_type]
        return {
            "status": entry.get("status", "blocked"),
            "reason": entry.get("reason", "reserved study type"),
            "gate": entry.get("gate", "capability gate blocks reserved study type"),
            "quantities": {"supported": [], "unsupported": []},
            "required_inputs": [],
        }

    study = matrix["study_types"].get(study_type)
    if study is None:
        return {
            "status": "blocked",
            "reason": f"no capability-matrix row for study_type={study_type!r}; a study type cannot be added without a matrix row plus adapter plus audit plus benchmark",
            "gate": "capability matrix has no study_types entry",
            "quantities": {"supported": [], "unsupported": []},
            "required_inputs": [],
        }

    engines = study.get("engines", {})
    if engine is not None and engine not in engines:
        # Explicitly requested engine has no declared row -> name that engine,
        # never silently fall back to the default engine's cell/message.
        return {
            "status": "blocked",
            "reason": f"engine={engine!r} is not declared for study_type={study_type!r} in the capability matrix",
            "gate": "capability matrix engine lookup",
            "quantities": {"supported": [], "unsupported": []},
            "required_inputs": [],
        }
    engine_keys = [engine] if engine in engines else ([_DEFAULT_ENGINE] if _DEFAULT_ENGINE in engines else [])
    for engine_key in engine_keys:
        entry = engines[engine_key]
        if representation in entry:
            return dict(entry[representation])
        if _WILDCARD in entry:
            return dict(entry[_WILDCARD])

    # Engine exists for the study but not this representation (or no default).
    if engine in engines:
        return {
            "status": "blocked",
            "reason": f"representation_family={representation!r} is not declared for engine={engine!r} and study_type={study_type!r}",
            "gate": "capability matrix representation lookup",
            "quantities": {"supported": [], "unsupported": []},
            "required_inputs": [],
        }

    return {
        "status": "blocked",
        "reason": f"engine={engine!r} is not declared for study_type={study_type!r} in the capability matrix",
        "gate": "capability matrix engine lookup",
        "quantities": {"supported": [], "unsupported": []},
        "required_inputs": [],
    }


def cell_for_case(case: Case) -> dict[str, Any]:
    """Resolve the matrix cell for a Case without raising on blocked cells."""
    representation = _representation_for_case(case)
    return _cell(case.study.type, _engine_for_case(case), representation)


def gate_case(case: Case) -> dict[str, Any]:
    """Validate a Case against the capability matrix (the L4 gate).

    Raises :class:`CapabilityBlocked` with the matrix's named reason when the
    requested study/engine/representation is blocked or undeclared — before any
    solver connection.  Returns the resolved cell when the Case is admitted.
    """
    cell = cell_for_case(case)
    status = cell.get("status")
    if status == "blocked":
        raise CapabilityBlocked(
            case.study.type,
            _engine_for_case(case),
            _representation_for_case(case),
            str(cell.get("reason", "blocked by capability matrix")),
        )
    # T-001: the per-unit/kV base consistency gate is fail-closed *at intake*.
    # A Case whose declared bases are internally inconsistent (winding kV vs
    # bus kV, generator xd/mva, non-finite line/load/shunt/DER quantities) must
    # not reach a solver — the run blocks with a named reason, never silently
    # coerced.  Non-inline cases pass vacuously.
    from cept.fidelity.perunit import audit_perunit

    perunit = audit_perunit(case)
    if not perunit["passed"]:
        raise CapabilityBlocked(
            case.study.type,
            _engine_for_case(case),
            _representation_for_case(case),
            "per-unit/kV base inconsistency at intake: " + "; ".join(perunit["reasons"]),
        )
    return cell


class MatrixCapabilityResolver:
    """Resolve execution feasibility from the machine-readable matrix.

    Reserved study types and blocked cells raise :class:`CapabilityBlocked`
    with a named reason instead of being silently converted.
    """

    def for_case(self, case: Case) -> ExecutionCapability:
        cell = gate_case(case)
        engine = _engine_for_case(case) or _DEFAULT_ENGINE
        quantities = cell.get("quantities", {})
        supported = list(quantities.get("supported", []))
        unsupported = list(quantities.get("unsupported", []))
        matrix_schema_version = int(load_capability_matrix()["schema_version"])
        return {
            "matrix_schema_version": matrix_schema_version,
            # Backward-compatible key retained for callers that consumed the
            # old generic Capability shape from this namespace.
            "passport_schema_version": matrix_schema_version,
            "engine": engine,
            "study_type": case.study.type,
            "representation_family": _representation_for_case(case),
            "status": str(cell.get("status", "blocked")),
            "fidelity": str(cell.get("fidelity", "steady_state")),
            "reason": str(cell.get("reason", "")),
            "gate": str(cell.get("gate", "")),
            "supported_quantities": supported,
            "unsupported_quantities": unsupported,
            "required_inputs": list(cell.get("required_inputs", [])),
            # Compatibility fields: matrix status is an execution status, not
            # an evidence claim ceiling. New code should use ``status``.
            "claim_cap": str(cell.get("status", "blocked")),
            "experimental_features": [],
            "supported_model_families": supported,
            "prohibited_claims": unsupported,
        }


_default_resolver = MatrixCapabilityResolver()


def execution_capability_for_case(case: Case) -> ExecutionCapability:
    """Return execution-feasibility facts for ``case`` and run its intake gate."""
    return _default_resolver.for_case(case)


def capability_for_case(case: Case) -> ExecutionCapability:
    """Backward-compatible alias for :func:`execution_capability_for_case`."""
    return execution_capability_for_case(case)


__all__ = [
    "CapabilityBlocked",
    "MatrixCapabilityResolver",
    "capability_for_case",
    "cell_for_case",
    "execution_capability_for_case",
    "gate_case",
    "load_capability_matrix",
]
