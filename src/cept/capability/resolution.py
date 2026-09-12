"""Aggregate capability resolution without collapsing authority boundaries."""

from __future__ import annotations

from cept.capability.matrix import execution_capability_for_case
from cept.engine_passports import evidence_capability_for_case
from cept.ports.capabilities import ResolvedStudyCapability
from cept.schema.case import Case


class CapabilityConsistencyError(RuntimeError):
    """Execution and evidence authorities resolved incompatible identities."""


def resolve_study_capability(case: Case) -> ResolvedStudyCapability:
    """Resolve execution feasibility and engine evidence for one Case.

    The two authorities remain visible in the returned object.  A disagreement
    about the engine or study identity fails closed before adapter dispatch.
    """

    execution = execution_capability_for_case(case)
    evidence = evidence_capability_for_case(case)
    execution_engine = execution["engine"]
    evidence_engine = evidence["engine"]
    if execution_engine != evidence_engine:
        raise CapabilityConsistencyError(
            "capability authorities resolved different engines: "
            f"execution={execution_engine!r}, evidence={evidence_engine!r}"
        )
    if execution["study_type"] != evidence["study_type"]:
        raise CapabilityConsistencyError(
            "capability authorities resolved different study identities: "
            f"execution={execution['study_type']!r}, evidence={evidence['study_type']!r}"
        )
    return {
        "engine": evidence_engine,
        "study_type": evidence["study_type"],
        "execution": execution,
        "evidence": evidence,
    }


__all__ = ["CapabilityConsistencyError", "resolve_study_capability"]
