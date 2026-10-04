"""Neutral application operation for running one typed Case to artifacts.

:func:`run_case_operation` is the reusable entry for executing a Case. It
resolves the Case (an in-memory object or a JSON file path) and the run
directory, then delegates every solver, artifact, report, and validation step
to the one shared execution service
:func:`cept.application.execution.execute_study_to_artifacts`. There is exactly
one study runner: CLI handlers, workflow recipes, and Python callers all reach
the solver through this operation, so recipe and CLI behavior stay identical.

This module is deliberately CLI-free: it must not import command handlers,
parser groups, or the route registry. It depends on
:mod:`cept.schema.intake_provenance` for Case intake evidence and, through
it, on :mod:`cept.application.exit_codes`; those are core modules with no
command logic, so sharing them keeps one Case-loading implementation instead
of two.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from cept.application.execution import (
    StudyExecutionRequest,
    _slug,
    execute_study_to_artifacts,
)
from cept.artifacts import portable_run_dir
from cept.schema.intake_provenance import verify_case_provenance
from cept.schema import Case
from cept.util import read_json


@dataclass(frozen=True)
class RunCaseRequest:
    """Explicit, typed inputs for one :func:`run_case_operation` call.

    Exactly one of ``case`` and ``case_path`` must be supplied. An explicit
    ``run_dir`` wins over ``out``; when neither is given the operation derives a
    timestamped directory below the portable artifact root, exactly as the CLI
    does today.
    """

    case: Case | None = None
    case_path: Path | None = None
    out: str | Path | None = None
    run_dir: Path | None = None
    export: bool = False
    include_show_commands: bool = False
    force: bool = False
    strict: bool = False
    argv: list[str] = field(default_factory=list)
    extra_commands: list[str] | None = None
    solver: str = "native"
    experiment_context: dict[str, Any] | None = None


@dataclass(frozen=True)
class RunCaseOutcome:
    """Stable result of one run-case operation."""

    exit_code: int
    run_dir: Path
    report_path: Path | None
    claim: str | None
    execution_key: str | None


def load_run_case(path: Path) -> Case:
    """Load one typed Case from JSON and verify its intake provenance."""
    if path.suffix.lower() not in {".json"}:
        # Wording is preserved verbatim so existing CLI error output is unchanged.
        raise ValueError("only JSON Case files are supported by the CLI")
    case = Case.model_validate(read_json(path))
    verify_case_provenance(case, path.resolve())
    return case


def default_run_dir(case: Case) -> Path:
    """Derive a timestamped run directory below the portable artifact root."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return portable_run_dir(portable_run_dir(None) / f"{_slug(case.meta.name)}_{stamp}")


def resolve_run_dir(case: Case, out: str | Path | None) -> Path:
    """Resolve the run directory for ``case`` under an optional ``out`` target."""
    if out:
        return portable_run_dir(out)
    return default_run_dir(case)


def build_demo_run_case(study: str, network: str) -> Case:
    """Build the built-in demonstration Case for a study/network pair."""
    if study == "fault":
        spec: dict[str, Any] = {
            "type": "fault",
            "options": {"bus": "671", "type": "3ph", "rf": 0.001, "run_faultstudy": True},
        }
    elif study == "hosting-capacity":
        spec = {"type": "hosting_capacity", "options": {"v_max": 1.05, "max_kw": 5000}}
    elif study == "ibr":
        spec = {"type": "unbalanced_load_flow"}
    elif study == "dynamics":
        spec = {"type": "dynamics"}
    elif study == "gic":
        spec = {"type": "gic", "options": {"frequency": 0.1}}
        network = "gic"
    else:
        spec = {"type": "unbalanced_load_flow"}

    payload: dict[str, Any] = {
        "meta": {
            "name": f"{network}_{study}",
            "description": f"Built-in {study} demonstration on {network}.",
            "mode": "demonstrator",
        },
        "network": {"kind": "builtin", "name": network, "frequency_hz": 60},
        "study": spec,
    }
    if study == "ibr":
        payload["ders"] = [
            {
                "id": "pv1",
                "type": "pv",
                "bus": "675",
                "phases": 3,
                "kw": 1500,
                "kva": 1800,
                "inverter": {"control": "volt_var"},
            }
        ]
    if study == "dynamics":
        payload["ders"] = [
            {
                "id": "dg1",
                "type": "syncgen",
                "bus": "680",
                "phases": 3,
                "kw": 1000,
                "machine": {"h": 4.0, "d": 2.0, "xdp": 0.27},
            }
        ]
        payload["dynamics"] = {
            "stepsize": 0.001,
            "duration": 1.0,
            "monitor_buses": ["675"],
            "events": [
                {
                    "t": 0.2,
                    "kind": "fault",
                    "target": "671",
                    "params": {"phases": 3, "r": 0.001},
                    "label": "3-ph fault",
                },
                {"t": 0.3, "kind": "clear_fault", "target": "671", "label": "clear"},
            ],
        }
    return Case.model_validate(payload)


def _resolve_case(request: RunCaseRequest) -> Case:
    if (request.case is None) == (request.case_path is None):
        raise ValueError("run_case_operation requires exactly one of case= or case_path=")
    if request.case is not None:
        return request.case
    assert request.case_path is not None  # narrowed by the exclusive-or check above
    return load_run_case(Path(request.case_path))


def _resolve_run_dir(request: RunCaseRequest, case: Case) -> Path:
    if request.run_dir is not None:
        return Path(request.run_dir)
    return resolve_run_dir(case, request.out)


def run_case_operation(request: RunCaseRequest) -> RunCaseOutcome:
    """Execute one Case to solver artifacts and report the exact run outcome.

    Execution, artifacts, report rendering, and validation all belong to
    :func:`cept.application.execution.execute_study_to_artifacts`; this function
    only resolves inputs and translates the service result into
    :class:`RunCaseOutcome`. Console output matches the CLI run receipt because
    both entries call the same service with console output enabled.
    """
    case = _resolve_case(request)
    outcome = execute_study_to_artifacts(
        StudyExecutionRequest(
            case=case,
            run_dir=_resolve_run_dir(request, case),
            export=request.export,
            include_show_commands=request.include_show_commands,
            force=request.force,
            strict=request.strict,
            argv=list(request.argv),
            extra_commands=request.extra_commands,
            solver=request.solver,
            experiment_context=request.experiment_context,
            console_output=True,
        )
    )
    return RunCaseOutcome(
        exit_code=outcome.exit_code,
        run_dir=outcome.run_dir,
        report_path=outcome.report_path,
        claim=outcome.claim,
        execution_key=outcome.execution_key,
    )


__all__ = [
    "RunCaseOutcome",
    "RunCaseRequest",
    "build_demo_run_case",
    "default_run_dir",
    "load_run_case",
    "resolve_run_dir",
    "run_case_operation",
]