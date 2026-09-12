"""Pre-solver Case admission policy for every CEPT front door.

A front desk may explain why a Case is blocked, but it must not maintain a
second topology or branch-count policy.  ``prepare_case`` is the canonical
pre-solver admission boundary; this module translates its typed outcome into a
small, JSON-safe decision for CLI and guided callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from cept.studies.planning import PreparedCase, prepare_case
from cept.schema.case import Case


ReadinessStatus = Literal["PASS", "BLOCKED"]


@dataclass(frozen=True)
class CaseReadiness:
    """Immutable front-door view of one pre-solver Case admission decision."""

    status: ReadinessStatus
    case_fingerprint: str
    engine: str | None
    study_type: str | None
    representation_family: str | None
    execution_status: str | None
    execution_reason: str | None
    research_status: str | None
    claim_cap: str | None
    gaps: tuple[dict[str, Any], ...]
    prepared: PreparedCase | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a stable JSON-safe view without exposing mutable internals."""
        return {
            "schema": "cept-case-readiness-v1",
            "status": self.status,
            "case_fingerprint": self.case_fingerprint,
            "engine": self.engine,
            "study_type": self.study_type,
            "representation_family": self.representation_family,
            "execution_status": self.execution_status,
            "execution_reason": self.execution_reason,
            "research_status": self.research_status,
            "claim_cap": self.claim_cap,
            "execution_disposition": "admitted" if self.status == "PASS" else "blocked",
            "gaps": [dict(gap) for gap in self.gaps],
        }


def _fingerprint(case: Case) -> str:
    try:
        return case.fingerprint()
    except Exception:
        return "unavailable"


def _blocked(
    case: Case,
    *,
    field: str,
    reason: str,
    prepared: PreparedCase | None = None,
) -> CaseReadiness:
    return CaseReadiness(
        status="BLOCKED",
        case_fingerprint=_fingerprint(case),
        engine=prepared.engine if prepared else getattr(case, "engine", None),
        study_type=prepared.study_type if prepared else getattr(case.study, "type", None),
        representation_family=prepared.representation_family if prepared else None,
        execution_status=prepared.execution_status if prepared else "blocked",
        execution_reason=prepared.execution_reason if prepared else reason,
        research_status=prepared.evidence_research_status if prepared else None,
        claim_cap=prepared.evidence_claim_cap if prepared else None,
        gaps=(
            {
                "field": field,
                "blocking": True,
                "reason": reason,
            },
        ),
        prepared=prepared,
    )


def readiness_for_case(case: Case) -> CaseReadiness:
    """Resolve the canonical pre-solver policy for ``case``.

    No adapter is constructed and no solver is contacted.  Capability or
    identity failures become an explicit blocking gap for guided callers;
    they are never replaced by a local topology heuristic.
    """
    try:
        prepared = prepare_case(case)
    except Exception as exc:
        return _blocked(
            case,
            field="case.admission",
            reason=f"Case admission failed before solver execution: {exc}",
        )

    if prepared.execution_status == "blocked":
        return _blocked(
            case,
            field="case.execution_capability",
            reason=prepared.execution_reason or "The capability policy blocks this Case.",
            prepared=prepared,
        )

    return CaseReadiness(
        status="PASS",
        case_fingerprint=prepared.case_fingerprint,
        engine=prepared.engine,
        study_type=prepared.study_type,
        representation_family=prepared.representation_family,
        execution_status=prepared.execution_status,
        execution_reason=prepared.execution_reason,
        research_status=prepared.evidence_research_status,
        claim_cap=prepared.evidence_claim_cap,
        gaps=(),
        prepared=prepared,
    )


__all__ = ["CaseReadiness", "ReadinessStatus", "readiness_for_case"]
