"""Neutral application operation for assembling one run's notebook output.

``cept report notebook`` and the ``report.notebook`` recipe stage both call
:func:`assemble_notebook_operation`, which is the only caller of the reporting
owner's entry point. Every value in the notebook comes from artifacts already
stored in the run directory: this operation never re-runs a solver, re-derives a
Case fingerprint, or re-derives a validation verdict.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AssembleNotebookRequest:
    """Explicit, typed inputs for one :func:`assemble_notebook_operation` call."""

    run_dir: Path
    output_path: Path
    target: str = "plain"


@dataclass(frozen=True)
class AssembleNotebookOutcome:
    """Stable result of one notebook assembly.

    ``cells_emitted`` is ``0`` on a fail-closed attempt: stored evidence was
    missing, malformed, self-contradictory, or from a licensed host this output
    refuses to represent, so no notebook exists and ``output_path`` is only
    where it would have gone.
    """

    exit_code: int
    output_path: Path
    cells_emitted: int
    verdict: str
    claim: str
    warnings: tuple[str, ...]
    limitations: tuple[str, ...]


def assemble_notebook_operation(request: AssembleNotebookRequest) -> AssembleNotebookOutcome:
    """Assemble one notebook from the artifacts stored in ``request.run_dir``.

    A declared target with no implemented and tested runtime is refused, never
    silently downgraded to the plain notebook.
    """
    # Imported here, not at module scope: this module must stay importable in a
    # public install that ships the recipe runtime without the reporting extras.
    from cept.reporting.notebook import (
        AssembleNotebookRequest as OwnerRequest,
        NotebookTargetUnsupported,
        assemble_run_notebook,
    )

    try:
        outcome = assemble_run_notebook(
            OwnerRequest(
                run_dir=Path(request.run_dir),
                output_path=Path(request.output_path),
                target=request.target,
            )
        )
    except NotebookTargetUnsupported as exc:
        # A target with no implemented and tested runtime is never silently
        # downgraded to the plain notebook. Report it and write nothing.
        raise ValueError(str(exc)) from exc

    return AssembleNotebookOutcome(
        exit_code=outcome.exit_code,
        output_path=outcome.output_path,
        cells_emitted=outcome.cells_emitted,
        verdict=outcome.verdict,
        claim=outcome.claim,
        warnings=tuple(outcome.warnings),
        limitations=tuple(outcome.limitations),
    )


__all__ = [
    "AssembleNotebookOutcome",
    "AssembleNotebookRequest",
    "assemble_notebook_operation",
]