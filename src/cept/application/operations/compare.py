"""Relate two existing runs under a declared contract.

``compare`` reads the stored artifacts of two *completed* run directories and
relates them under the declared contract. It never runs a solver, never
re-derives a Case fingerprint, and never re-derives a verdict: every number in
the outcome comes either from the delegated lane implementation or from the
files that lane wrote.

The lane implementations own all comparison semantics. This module owns only
lane selection, the contract-bound argument vector, and reading back the exact
artifact paths and verdict fields, so that a recipe runner, a notebook, or a
future package can drive a comparison without going through the CLI.

Claim boundary
--------------
Cross-engine agreement is *diagnostic* evidence. It is not independent
engineering validation, and it is not project validation. Benchmark or
published-reference agreement is likewise not project evidence. This module
preserves the wording owned by the lane implementations exactly as those
implementations emit it and adds no claim of its own.

Fail-closed
-----------
A missing or malformed run artifact in either input is a failure, never a
default or an empty comparison. That refusal is owned by the delegated lane
implementation; this module deliberately adds no pre-flight gate of its own,
because pre-empting it would change the exit code the CLI already returns.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cept.application.exit_codes import EXIT_POLICY
from cept.adapters import AdvanceUnavailable, module_name as advanced_module_name

NATIVE_LANE = "native"
USERMODEL_LANE = "usermodel"

#: Declared parity lanes and the Advance capability that implements each one.
#: The mapping is the single source of truth; the CLI does not own it. The
#: values are capability *names*, never module paths: the installed Advance
#: package owns the mapping from a name to its worker module, so moving a
#: worker cannot leave a stale lane entry behind in shipped core.
LANE_CAPABILITIES: dict[str, str] = {
    NATIVE_LANE: "workflow_dynamic_benchmark_report",
    USERMODEL_LANE: "workflow_dynamic_usermodel_parity_report",
}

#: Retained under its historical name. The values are capability names.
LANE_WORKERS = LANE_CAPABILITIES

#: Launches a lane worker. The CLI supplies its own packaged-worker launcher so
#: the public command keeps its installed-product process boundary.
WorkerRunner = Callable[[str, Sequence[str]], int]

_COMPARISON_DIR = "comparison"
_COMPARISON_ARTIFACT = Path(_COMPARISON_DIR) / "comparison.json"
_REPORT_ARTIFACT = "validation_report.json"
_BENCHMARK_ARTIFACT = "benchmark_comparison.json"
_ERROR_INDEX_ARTIFACTS = ("error-index.json", "error-index.csv")
_HTML_ARTIFACTS = (
    "validation_report.html",
    "dynamic-cross-engine.html",
    "dynamic-diagnostics.html",
    "static-cross-engine.html",
)


@dataclass(frozen=True)
class CompareRunsRequest:
    """File-bound inputs for one comparison of two completed runs.

    ``left_run_dir`` is the PowerFactory/reference solver leaf and
    ``right_run_dir`` is the OpenDSS/candidate solver leaf, matching the
    argument names the declared lane contract has always used.

    ``case_path`` is the declared contract reference: the canonical typed Case
    that binds the model family, event definition, and provenance. Tolerances
    are not overridable on this route; they stay owned by the reviewed semantic
    registry and the lane implementation, so no tolerance flag is accepted here.
    """

    left_run_dir: Path
    right_run_dir: Path
    case_path: Path
    out_dir: Path
    lane: str = NATIVE_LANE


@dataclass(frozen=True)
class CompareRunsOutcome:
    """Structured result of one comparison, with exact artifact locations.

    ``exit_code`` is the value the lane worker returned, unchanged. The verdict
    fields are read back from the report the lane wrote; they are ``None`` when
    the lane produced no report, which is never a passing default.
    """

    exit_code: int
    lane: str
    worker: str
    out_dir: Path
    comparison_artifact: Path | None
    error_index_artifacts: tuple[Path, ...]
    report_artifact: Path | None
    benchmark_artifact: Path | None
    html_artifacts: tuple[Path, ...]
    passed: bool | None
    status: str | None
    claim: str | None
    claim_boundary: tuple[str, ...]


def compare_runs_arguments(request: CompareRunsRequest) -> tuple[str, ...]:
    """Return the declared argument vector handed to the lane worker.

    Paths are rendered POSIX-style so the worker's argument text does not
    change with the host separator; the lane resolves them back to the same
    locations.
    """
    return (
        "--out",
        request.out_dir.as_posix(),
        "--case",
        request.case_path.as_posix(),
        "--powerfactory",
        request.left_run_dir.as_posix(),
        "--opendss",
        request.right_run_dir.as_posix(),
    )


def compare_runs_operation(
    request: CompareRunsRequest,
    *,
    run_worker: WorkerRunner | None = None,
) -> CompareRunsOutcome:
    """Relate two existing runs under the declared contract in ``request``.

    Reads only stored artifacts. Raises :class:`ValueError` for an undeclared
    lane, and returns the worker's exit code verbatim otherwise.
    """
    declared = LANE_CAPABILITIES.get(request.lane)
    if declared is None:
        raise ValueError(f"unsupported dynamic comparison lane: {request.lane}")
    try:
        worker = advanced_module_name(declared)
    except AdvanceUnavailable as exc:
        # Fail closed with the same policy exit the launcher already returned when
        # a lane worker was not part of this install: a blocked lane, never a
        # fabricated comparison and never an agreement claim.
        print(f"blocked: {exc}", file=sys.stderr)
        return _read_outcome(request, worker=declared, exit_code=EXIT_POLICY)
    runner = _default_worker_runner if run_worker is None else run_worker
    exit_code = int(runner(worker, compare_runs_arguments(request)))
    return _read_outcome(request, worker=worker, exit_code=exit_code)


def _default_worker_runner(module: str, arguments: Sequence[str]) -> int:
    """Launch a packaged lane worker with the active Python environment."""
    from cept.application.worker import run_packaged_worker

    return run_packaged_worker(module, arguments)


def _read_outcome(
    request: CompareRunsRequest, *, worker: str, exit_code: int
) -> CompareRunsOutcome:
    """Read the artifacts the lane wrote; never invent a verdict."""
    out_dir = Path(request.out_dir)
    report_path = out_dir / _REPORT_ARTIFACT
    report = _read_json(report_path)
    return CompareRunsOutcome(
        exit_code=exit_code,
        lane=request.lane,
        worker=worker,
        out_dir=out_dir,
        comparison_artifact=_existing_file(out_dir / _COMPARISON_ARTIFACT),
        error_index_artifacts=_existing_files(out_dir / _COMPARISON_DIR, _ERROR_INDEX_ARTIFACTS),
        report_artifact=report_path if report else None,
        benchmark_artifact=_existing_file(out_dir / _BENCHMARK_ARTIFACT),
        html_artifacts=_existing_files(out_dir, _HTML_ARTIFACTS),
        passed=_literal_bool(report.get("passed")),
        status=report.get("status") if isinstance(report.get("status"), str) else None,
        claim=report.get("claim") if isinstance(report.get("claim"), str) else None,
        claim_boundary=_string_tuple(report.get("claim_boundary")),
    )


def _read_json(path: Path) -> dict[str, Any]:
    """Read a written report, treating an unreadable one as absent."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _existing_file(path: Path) -> Path | None:
    return path if path.is_file() else None


def _existing_files(root: Path, names: Sequence[str]) -> tuple[Path, ...]:
    return tuple(filter(None, (_existing_file(root / name) for name in names)))


def _literal_bool(value: Any) -> bool | None:
    return value if type(value) is bool else None


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(entry for entry in value if isinstance(entry, str))


__all__ = [
    "CompareRunsOutcome",
    "CompareRunsRequest",
    "LANE_CAPABILITIES",
    "NATIVE_LANE",
    "USERMODEL_LANE",
    "WorkerRunner",
    "compare_runs_arguments",
    "compare_runs_operation",
]