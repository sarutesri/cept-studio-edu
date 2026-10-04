"""Neutral application operation for verifying an explicit set of runs.

:func:`verify_run_operation` is the single entry point that decides whether an
explicitly supplied set of run directories is complete, self-consistent, and
safe to claim.  ``cept study verify`` is a thin CLI wrapper over it, so a
recipe runner, a notebook, or a future package reaches the same verifier
instead of re-implementing it.

One verdict owner
-----------------
Every verdict comes from :mod:`cept.verification.run_set`, the canonical
implementation this module delegates to.  Nothing here re-derives a Case
fingerprint, re-reads a solver result, or interprets a validation report a
second way; the verdict is read back exactly as the verifier produced it.

Fail-closed
-----------
A run without an explicit ``passed=true`` validation report, a malformed
artifact, or a broken identity/hash binding is *incomplete*, not "probably
fine".  :func:`verify_run_operation` never coerces, defaults, or infers
``passed``: :attr:`VerifyRunOutcome.passed` is the verifier's literal verdict,
and a run set that verified nothing reports ``passed=False`` with the exit
code the verifier returned.

Claim boundary
--------------
This operation verifies artifacts.  It does not promote them.  A verifying run
carries exactly the claim its own manifest declares; benchmark, reference, and
project-validation claims stay separate statements and are never merged into
one verdict here.

This module is deliberately CLI-parser-free: it imports no command handler,
parser group, or route registry.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cept.verification.run_set import verify_run_set, write_verification_summary

#: Verdict artifacts the verifier consults, in the order it prefers them for a
#: run that claims no reference lane.
_VALIDATION_REPORT_ARTIFACTS = ("validation_report.json", "cross_engine_report.json")
#: Hash/identity-bound receipt the verifier consults for every lane.
_VALIDATION_RECORD_ARTIFACT = "validation-record.json"
#: Manifest claims whose lane is verified through the reference receipt rather
#: than through a validation report.
_REFERENCE_CLAIMS = frozenset({"powerfactory_reference", "independent-reference"})


@dataclass(frozen=True)
class VerifyRunRequest:
    """Explicit, typed inputs for one :func:`verify_run_operation` call.

    The fields mirror exactly what ``cept study verify`` accepts: the explicit
    run directories, an optional JSON summary destination, and the ``--refresh``
    rebuild flag.  No tolerance, engine, or claim flag is accepted here -- the
    verification policy is not a caller-supplied input.
    """

    run_dir: Path
    run_dirs: tuple[Path, ...] = ()
    out: Path | None = None
    refresh: bool = False

    def all_run_dirs(self) -> tuple[Path, ...]:
        """Return the explicit run set in argument order, resolved like the verifier."""
        return tuple(Path(raw).resolve() for raw in (self.run_dir, *self.run_dirs))


@dataclass(frozen=True)
class VerifyRunOutcome:
    """Stable result of one verification operation.

    ``verdict`` is the literal summary the verifier produced and ``passed`` is
    that verdict's own value, not a re-derived one.  The artifact fields are
    the exact files this operation read or wrote, so a caller can point at the
    evidence instead of guessing where it lives.
    """

    exit_code: int
    passed: bool
    verdict: dict[str, Any]
    verdict_summary: str
    verdict_path: Path | None
    run_dirs: tuple[Path, ...]
    validation_artifacts: tuple[Path, ...]
    refreshed_artifacts: tuple[Path, ...]
    conduct_mode: dict[str, Any] | None


def verify_run_operation(request: VerifyRunRequest) -> VerifyRunOutcome:
    """Verify the explicit run set in ``request`` and report the exact verdict.

    The console receipt and the summary artifact are byte-identical to what the
    CLI verify command emits, because this function is that command's only
    implementation.
    """
    run_dirs = request.all_run_dirs()

    refreshed: list[Path] = []
    if request.refresh:
        for run_dir in run_dirs:
            refreshed.extend(refresh_run_validation(run_dir))

    exit_code, summary = write_verification_summary(list(run_dirs), request.out)
    return VerifyRunOutcome(
        exit_code=exit_code,
        passed=summary["passed"],
        verdict=summary,
        verdict_summary=encode_verdict_summary(summary),
        verdict_path=Path(request.out).resolve() if request.out else None,
        run_dirs=run_dirs,
        validation_artifacts=read_validation_artifacts(run_dirs),
        refreshed_artifacts=tuple(refreshed),
        conduct_mode=summary.get("conduct_mode"),
    )


def verify_run_verdict(run_dirs: list[str | Path]) -> dict[str, Any]:
    """Return the canonical verification verdict for ``run_dirs`` without side effects.

    This is the read-only form of :func:`verify_run_operation`: it answers
    "does this run set verify?" without writing a summary artifact, emitting
    console output, or touching the exit-code contract.
    """
    return verify_run_set(run_dirs)


def refresh_run_validation(run_dir: Path) -> tuple[Path, ...]:
    """Rebuild a run's ``validation_report.*`` from its immutable Case/results.

    ``--refresh`` re-derives the validation report from the artifacts the run
    already wrote -- it never re-runs a solver. The run's experiment receipt is
    re-checked first so a stale or blocked experiment is still refused, and the
    rebuilt reports are returned as the exact paths written.
    """
    from cept.application.artifacts import _write_validation
    from cept.application.operations.report import check_run_export_eligibility
    from cept.schema import Case
    from cept.schema.result import StudyResult
    from cept.util import read_json

    run = Path(run_dir).resolve()
    check_run_export_eligibility(run)

    case_path = run / "case.json"
    result_path = run / "results.json"
    if not case_path.is_file() or not result_path.is_file():
        raise FileNotFoundError(f"cannot refresh validation without case.json and results.json: {run}")
    case = Case.model_validate(read_json(case_path))
    result = StudyResult.model_validate(read_json(result_path))
    _write_validation(run, case, result)
    return (run / "validation_report.json", run / "validation_report.md")


def encode_verdict_summary(summary: dict[str, Any]) -> str:
    """Encode a verdict exactly as the CLI summary artifact encodes it."""
    return json.dumps(summary, indent=2, ensure_ascii=False)


def read_validation_artifacts(run_dirs: tuple[Path, ...]) -> tuple[Path, ...]:
    """Return the validation/verdict artifacts the verifier reads for each run.

    A run claiming a reference lane is verified through its
    ``validation-record.json`` receipt; every other run is verified through its
    validation report. The path is returned only when the artifact exists, so
    the tuple never advertises evidence that is not on disk.
    """
    found: list[Path] = []
    for run_dir in run_dirs:
        run = Path(run_dir).resolve()
        names = (
            (_VALIDATION_RECORD_ARTIFACT,)
            if _reference_claim(run)
            else (*_VALIDATION_REPORT_ARTIFACTS, _VALIDATION_RECORD_ARTIFACT)
        )
        found.extend(
            path for name in names if (path := run / name).is_file()
        )
    return tuple(found)


def _reference_claim(run: Path) -> bool:
    """Return whether ``run``'s manifest declares a reference lane."""
    try:
        manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False
    return isinstance(manifest, dict) and manifest.get("claim") in _REFERENCE_CLAIMS


__all__ = [
    "VerifyRunOutcome",
    "VerifyRunRequest",
    "encode_verdict_summary",
    "read_validation_artifacts",
    "refresh_run_validation",
    "verify_run_operation",
    "verify_run_verdict",
]