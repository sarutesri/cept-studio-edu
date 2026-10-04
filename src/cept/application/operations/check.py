"""Neutral application operation for checking one typed Case's readiness.

``cept case check`` and the ``case.check`` recipe stage both call
:func:`check_case_operation`, so the Case load, the hash-bound build-receipt
gate, the readiness policy, and the reported facts have exactly one
implementation.

The operation returns the console lines instead of printing them: the CLI prints
them verbatim, and a recipe stage records them as a stage note. Neither surface
can therefore describe a Case differently from the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cept.application.readiness import CaseReadiness, readiness_for_case
from cept.application.exit_codes import EXIT_OK, EXIT_POLICY
from cept.schema.intake_provenance import verify_case_provenance
from cept.schema import Case
from cept.util import read_json


@dataclass(frozen=True)
class CheckCaseRequest:
    """Explicit, typed inputs for one :func:`check_case_operation` call.

    ``receipt_path`` names the build receipt to check beside the Case. When it
    is omitted the receipt is looked up beside the Case file under the declared
    ``<case-stem>.build-receipt.json`` name; an absent receipt is not a failure,
    because a Case may legitimately have been written without one.
    """

    case_path: Path
    receipt_path: Path | None = None


@dataclass(frozen=True)
class CheckCaseOutcome:
    """Stable result of one Case-readiness check.

    ``exit_code`` is :data:`cept.application.exit_codes.EXIT_POLICY` when readiness
    reported a blocking gap and :data:`cept.application.exit_codes.EXIT_OK` otherwise.
    A refused build receipt is raised, not reported: a receipt whose fingerprint
    does not match the Case is a broken evidence chain, not a readiness gap.
    """

    exit_code: int
    case_path: Path
    fingerprint: str
    readiness: CaseReadiness
    build_receipt: Path | None
    lines: tuple[str, ...]


def default_build_receipt(case_path: Path) -> Path:
    """Return the build receipt declared beside ``case_path`` by name."""
    resolved = Path(case_path).resolve()
    return resolved.with_name(f"{resolved.stem}.build-receipt.json")


def load_typed_case(case_path: Path) -> Case:
    """Load one typed Case and verify its intake provenance.

    This is the single Case loader the readiness check and the run operation
    share, so a Case that is refused here is refused identically there.
    """
    source = Path(case_path)
    if not source.is_file():
        raise FileNotFoundError(f"Case is missing: {source.resolve()}")
    case = Case.model_validate(read_json(source))
    verify_case_provenance(case, source.resolve())
    return case


def _checked_build_receipt(case: Case, receipt: Path) -> bool:
    """Fail closed unless the bound build receipt matches this Case."""
    if not receipt.is_file():
        return False
    payload = read_json(receipt)
    if payload.get("case_fingerprint") != case.fingerprint():
        raise ValueError(f"build receipt fingerprint does not match {receipt.name}")
    if payload.get("status") == "BLOCKED":
        raise ValueError(f"build receipt is BLOCKED: {receipt}")
    return True


def check_case_operation(request: CheckCaseRequest) -> CheckCaseOutcome:
    """Check one Case's build receipt and readiness, and report the facts.

    No adapter is constructed and no solver is contacted: this is the
    pre-solver admission decision only.
    """
    case = load_typed_case(request.case_path)
    receipt = (
        Path(request.receipt_path).resolve()
        if request.receipt_path is not None
        else default_build_receipt(request.case_path)
    )
    checked = _checked_build_receipt(case, receipt) or None

    readiness = readiness_for_case(case)
    lines = [
        f"Case OK: {request.case_path}",
        f"Fingerprint: {case.fingerprint()}",
        f"Readiness: {readiness.status}",
        f"Engine: {readiness.engine or 'unresolved'}",
        f"Study: {readiness.study_type or 'unresolved'}",
    ]
    if readiness.research_status:
        lines.append(f"Research support: {readiness.research_status}")
    if readiness.claim_cap:
        lines.append(f"Claim ceiling: {readiness.claim_cap}")

    if readiness.status == "BLOCKED":
        for gap in readiness.gaps:
            lines.append(
                f"Blocked: {gap.get('field', 'case')} — "
                f"{gap.get('reason', 'readiness requirement not met')}"
            )
        lines.append(
            "Next: resolve the blocking input or capability, then run `cept check` again."
        )
        return CheckCaseOutcome(
            exit_code=EXIT_POLICY,
            case_path=request.case_path,
            fingerprint=case.fingerprint(),
            readiness=readiness,
            build_receipt=checked,
            lines=tuple(lines),
        )

    lines.append("Next: run `cept run` with this Case, then verify the exact run directory.")
    return CheckCaseOutcome(
        exit_code=EXIT_OK,
        case_path=request.case_path,
        fingerprint=case.fingerprint(),
        readiness=readiness,
        build_receipt=checked,
        lines=tuple(lines),
    )


__all__ = [
    "CheckCaseOutcome",
    "CheckCaseRequest",
    "check_case_operation",
    "default_build_receipt",
    "load_typed_case",
]