"""Neutral application operations shared by the CLI, recipes, and callers.

Each operation is the single implementation owner of one reusable capability:

- :mod:`~cept.application.operations.check` reports one typed Case's readiness.
- :mod:`~cept.application.operations.run` executes a typed Case to run artifacts.
- :mod:`~cept.application.operations.verify` checks an existing run's evidence.
- :mod:`~cept.application.operations.compare` relates two existing runs.
- :mod:`~cept.application.operations.report` renders HTML, PDF, and DOCX.
- :mod:`~cept.application.operations.notebook` assembles one run's notebook.
- :mod:`~cept.application.operations.serve` opens and serves the local report.

Command handlers are thin compatibility wrappers over these operations, and a
workflow recipe stage calls the same functions in process. There is one
execution owner and one report-output owner; nothing here duplicates them.

Submodules are imported on demand so a caller that only needs one operation does
not pay for the rest. Import them directly
(``from cept.application.operations.run import run_case_operation``) or use the
re-exports below.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime cost.
    from cept.application.operations.check import (
        CheckCaseOutcome,
        CheckCaseRequest,
        check_case_operation,
    )
    from cept.application.operations.compare import (
        CompareRunsOutcome,
        CompareRunsRequest,
        compare_runs_operation,
    )
    from cept.application.operations.notebook import (
        AssembleNotebookOutcome,
        AssembleNotebookRequest,
        assemble_notebook_operation,
    )
    from cept.application.operations.report import (
        RenderReportOutcome,
        RenderReportRequest,
        ReportFormat,
        render_report_operation,
    )
    from cept.application.operations.run import (
        RunCaseOutcome,
        RunCaseRequest,
        run_case_operation,
    )
    from cept.application.operations.serve import (
        OpenReportRequest,
        ServeReportOutcome,
        ServeReportRequest,
        open_report_operation,
        serve_report_operation,
    )
    from cept.application.operations.verify import (
        VerifyRunOutcome,
        VerifyRunRequest,
        verify_run_operation,
    )

_SUBMODULES = frozenset({"check", "compare", "notebook", "report", "run", "serve", "verify"})

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "AssembleNotebookOutcome": ("notebook", "AssembleNotebookOutcome"),
    "AssembleNotebookRequest": ("notebook", "AssembleNotebookRequest"),
    "CheckCaseOutcome": ("check", "CheckCaseOutcome"),
    "CheckCaseRequest": ("check", "CheckCaseRequest"),
    "CompareRunsOutcome": ("compare", "CompareRunsOutcome"),
    "RenderReportOutcome": ("report", "RenderReportOutcome"),
    "RenderReportRequest": ("report", "RenderReportRequest"),
    "ReportFormat": ("report", "ReportFormat"),
    "RunCaseOutcome": ("run", "RunCaseOutcome"),
    "RunCaseRequest": ("run", "RunCaseRequest"),
    "ServeReportOutcome": ("serve", "ServeReportOutcome"),
    "ServeReportRequest": ("serve", "ServeReportRequest"),
    "VerifyRunOutcome": ("verify", "VerifyRunOutcome"),
    "VerifyRunRequest": ("verify", "VerifyRunRequest"),
    "assemble_notebook_operation": ("notebook", "assemble_notebook_operation"),
    "check_case_operation": ("check", "check_case_operation"),
    "compare_runs_operation": ("compare", "compare_runs_operation"),
    "open_report_operation": ("serve", "open_report_operation"),
    "render_report_operation": ("report", "render_report_operation"),
    "run_case_operation": ("run", "run_case_operation"),
    "serve_report_operation": ("serve", "serve_report_operation"),
    "verify_run_operation": ("verify", "verify_run_operation"),
}


def __getattr__(name: str) -> Any:
    """Resolve one operation export from its owning submodule on first use."""

    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(f"{__name__}.{module_name}"), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(__all__)


__all__ = [
    "AssembleNotebookOutcome",
    "AssembleNotebookRequest",
    "CheckCaseOutcome",
    "CheckCaseRequest",
    "CompareRunsOutcome",
    "CompareRunsRequest",
    "OpenReportRequest",
    "RenderReportOutcome",
    "RenderReportRequest",
    "ReportFormat",
    "RunCaseOutcome",
    "RunCaseRequest",
    "ServeReportOutcome",
    "ServeReportRequest",
    "VerifyRunOutcome",
    "VerifyRunRequest",
    "assemble_notebook_operation",
    "check_case_operation",
    "compare_runs_operation",
    "open_report_operation",
    "render_report_operation",
    "run_case_operation",
    "serve_report_operation",
    "verify_run_operation",
]
