"""Evidence and qualification authority for installed CEPT engines.

Engine passports describe runtime identity, research status, benchmark
provenance, and claim ceilings. They do not decide whether a particular
study/engine/representation is executable; that belongs to
:mod:`cept.capability.matrix`.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any, cast

from cept.ports.capabilities import EvidenceCapability
from cept.schema import Case


_ALLOWED_RESEARCH_STATUS = {"VALIDATED", "QUALIFIED", "EXPERIMENTAL", "DEMONSTRATOR"}


@lru_cache(maxsize=1)
def load_engine_passports() -> dict[str, Any]:
    path = resources.files("cept").joinpath("engine_passports.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("engines"), dict):
        raise ValueError("invalid packaged engine_passports.json")
    vocabulary = payload.get("status_vocabulary")
    if set(vocabulary or []) != _ALLOWED_RESEARCH_STATUS:
        raise ValueError("engine passport research-status vocabulary drifted")
    for engine, passport in payload["engines"].items():
        support = passport.get("research_support")
        if not isinstance(support, dict):
            raise ValueError(f"engine '{engine}' has no research_support map")
        supported = set(passport.get("supported_studies", []))
        if set(support) != supported:
            raise ValueError(f"engine '{engine}' research_support does not match supported_studies")
        for study, entry in support.items():
            if not isinstance(entry, dict) or entry.get("status") not in _ALLOWED_RESEARCH_STATUS:
                raise ValueError(f"engine '{engine}' study '{study}' has invalid research status")
    return payload


def _fidelity(study_type: str) -> str:
    if study_type in {"dynamics", "dynamics_rms"}:
        return "rms"
    if study_type == "emt":
        return "emt"
    if study_type in {"qsts", "harmonics"}:
        return "quasi_static_or_frequency_domain"
    return "steady_state"


def _claim_summary(research: dict[str, Any], maximum_claim: str) -> dict[str, Any]:
    """Explain what CEPT may say *now* versus what evidence could support later.

    ``claim_caps`` in the packaged passport is retained as a machine contract,
    but it is a ceiling rather than a statement that the current run has
    achieved that claim.  Keeping the two concepts separate prevents a
    qualified engine path from being presented as project validation.
    """
    status = str(research.get("status", "DEMONSTRATOR"))
    current = {
        "VALIDATED": "validated software capability",
        "QUALIFIED": "qualified software capability",
        "EXPERIMENTAL": "experimental capability",
        "DEMONSTRATOR": "demonstrator capability",
    }[status]
    missing: list[str] = []
    if maximum_claim == "project-validated":
        missing.extend(
            [
                "independent project/reference evidence",
                "reviewed project-specific acceptance criteria",
                "human engineering review binding the evidence to the claim",
            ]
        )
    elif maximum_claim not in {"demonstrator", "blocked"}:
        missing.append("external evidence required by the requested higher claim")
    return {
        "current_claim": current,
        "maximum_claim_if_external_evidence_satisfied": maximum_claim,
        "missing_evidence_for_higher_claim": missing,
    }


def evidence_capability_for_case(case: Case) -> EvidenceCapability:
    """Return engine evidence/qualification facts or fail before connection."""
    study = case.study.type
    if study == "emt":
        if case.engine == "opendss":
            raise NotImplementedError(
                "OpenDSS is an averaged/phasor-domain tool and cannot run switching-level EMT; "
                "use the existing PowerFactory EMT demonstrator or keep the study blocked."
            )
        engine = case.emt.engine if case.emt is not None else None
        if engine != "powerfactory":
            raise NotImplementedError(
                "EMT Case validated, but no reviewed external EMT adapter is installed in CEPT. "
                "Do not substitute RMS results."
            )
    else:
        engine = case.engine

    payload = load_engine_passports()
    engines = payload["engines"]
    if engine is None:
        candidates = [
            (entry.get("priority", 999), name)
            for name, entry in engines.items()
            if study in entry.get("supported_studies", [])
        ]
        if not candidates:
            raise NotImplementedError(f"No engine passport supports study.type='{study}'.")
        engine = min(candidates)[1]
    if engine not in engines:
        raise NotImplementedError(f"No CEPT engine passport exists for engine='{engine}'.")
    passport = engines[engine]
    if study not in passport.get("supported_studies", []):
        raise NotImplementedError(
            f"study.type='{study}' does not support engine='{engine}' according to the engine passport."
        )
    research = passport["research_support"][study]
    maximum_claim = passport.get("claim_caps", {}).get(study, "demonstrator")
    trust_summary = _claim_summary(research, maximum_claim)
    return cast(EvidenceCapability, {
        "passport_schema_version": payload["schema_version"],
        "engine": engine,
        "study_type": study,
        "fidelity": _fidelity(study),
        # Backward-compatible machine field. This is a ceiling, not the current claim.
        "claim_cap": maximum_claim,
        **trust_summary,
        "trust_summary": {
            **trust_summary,
            "research_status": research["status"],
            "benchmark": research.get("benchmark"),
        },
        "research_status": research["status"],
        "research_benchmark": research.get("benchmark"),
        "validated_runtime_version": research.get("validated_runtime_version"),
        "validated_platforms": research.get("platforms", []),
        "last_verified_release": research.get("last_verified_release"),
        "runtime": passport.get("runtime"),
        "license": passport.get("license"),
        "experimental_features": passport.get("experimental_features", []),
        "supported_model_families": passport.get("supported_model_families", []),
        "prohibited_claims": passport.get("prohibited_claims", []),
    })


def capability_for_case(case: Case) -> EvidenceCapability:
    """Backward-compatible alias for :func:`evidence_capability_for_case`."""
    return evidence_capability_for_case(case)


__all__ = [
    "capability_for_case",
    "evidence_capability_for_case",
    "load_engine_passports",
]
