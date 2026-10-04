"""Neutral application operation for rendering one run's report outputs.

:func:`render_report_operation` is the single entry point that produces report
artifacts from an *existing* run directory: the run's HTML report, a PDF of
that same report, and a native DOCX document. ``cept report export`` (and its
``cept export`` compatibility surface) is a thin CLI wrapper over this
operation, so scripts, notebooks, and future workflow recipes reach the same
renderer instead of re-implementing it.

One artifact source, three formats
----------------------------------

Every format is derived from the run's stored ``case.json`` and
``results.json``; the verdict identity reported back is read from the stored
``validation_report.json`` rather than recomputed. No format may re-run the
solver, re-interpret the Case, or invent a value the stored run does not
carry. The two format-specific strategies already implemented in
:mod:`cept.reporting.export` are preserved exactly:

* **PDF** prints the actual ``report.html`` through headless Chromium, so it
  is a faithful static snapshot of the interactive report (SLD and charts
  included) rather than a second rendering of the study.
* **DOCX** is built natively from the typed ``Case``/``StudyResult`` plus the
  stored validation summary. It is never scraped from the HTML, and it is never
  given a verdict the run does not have.

The HTML report itself is owned by the run
(:func:`cept.application.execution.execute_study_to_artifacts` renders it and
then injects the trust summary, SLD evidence, and review context). Rendering
it again here would silently drop those injected sections, so the HTML format
resolves the run's canonical ``report.html`` and only materializes it from the
stored artifacts when a run never wrote one. Either way the artifact is the
run's report, not a second interpretation of it.

This module is deliberately CLI-free: it must not import command handlers,
parser groups, or the route registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from cept.application.experiment_diagnostics import (
    ExperimentRuntimeUnavailable,
    experiment_diagnostics as resolve_experiment_diagnostics,
)
from cept.schema.case import Case
from cept.schema.result import StudyResult
from cept.util import read_json

CASE_FILENAME = "case.json"
RESULT_FILENAME = "results.json"
VALIDATION_FILENAME = "validation_report.json"
VALIDATION_RECORD_FILENAME = "validation-record.json"
REPORT_FILENAME = "report.html"


class ReportFormat(str, Enum):
    """Output formats ``cept report export`` can produce for one run.

    ``HTML`` is the run's own interactive report, ``PDF`` is that report
    printed headlessly, and ``DOCX`` is the native Word rendering built from
    the same stored Case and result. No format is added here that the CLI
    cannot already reach.
    """

    HTML = "html"
    PDF = "pdf"
    DOCX = "docx"


@dataclass(frozen=True)
class RenderReportRequest:
    """Explicit, typed inputs for one :func:`render_report_operation` call.

    The fields mirror the flags ``cept report export`` actually accepts:
    ``--public`` is the only one that decides whether the run may be exported
    at all, so that boundary belongs to the operation rather than to the CLI
    wrapper around it.
    """

    run_dir: Path
    format: ReportFormat
    public_export: bool = False


@dataclass(frozen=True)
class RenderReportOutcome:
    """Stable result of one report-rendering operation.

    ``output_path`` is the exact artifact that was written (or resolved for
    ``HTML``). The verdict fields are read back from the run's stored
    evidence; they are never recomputed here, and they never describe a claim
    the run does not carry.
    """

    exit_code: int
    output_path: Path
    run_dir: Path
    format: ReportFormat
    case_fingerprint: str | None
    claim: str | None
    validation_passed: bool | None
    verdict: str | None


EXPERIMENT_BINDING_KEYS = (
    "classification",
    "manifest_ref",
    "manifest_sha256",
    "claim",
)
"""Run-context keys that only ``cept experiment run`` ever writes.

``cept experiment run`` builds one context dict and hands it to every run in
the experiment (``cli/commands/run/experiment.py``): ``experiment_id``,
``classification``, ``manifest_sha256``, ``claim``, and per-run ``manifest_ref``
and ``model_packages``. No plain ``cept study run`` writes any of these.

``experiment_id`` is deliberately NOT here. ``cli/commands/run/study.py``
already reuses that key for an unrelated study-program node id, so it is a
label rather than a binding, and keying on it both refuses ordinary matrix
runs and lets a receipt be unbound by deleting one key. The four keys above
are the ones that make the manifest locatable, hash-checkable, and
classifiable — i.e. what the gate can actually enforce.
"""


def _declares_experiment_identity(context: Mapping[str, Any]) -> bool:
    """Whether a run's recorded context carries an experiment binding.

    A plain ``cept study run`` ALWAYS records an ``experiment`` block, but that
    block holds only derived bookkeeping: the canonical case pointer plus the
    empty model-package sets that ``execute_study_to_artifacts`` adds. Only
    ``cept experiment run`` records the manifest binding itself.

    Keying on block *presence* made every ordinary run look experiment-bound
    and demanded a ``manifest_ref`` no ordinary run can carry, which is why
    PDF/DOCX export failed for every normal run. Keying on ``experiment_id``
    alone fixed that but inverted the failure into a bypass: deleting that one
    key from a real receipt disabled the whole manifest check, so a missing,
    tampered, or still-``planned`` manifest passed. So the trigger is the set
    of keys that are the binding itself, and each refusal below stays exactly
    as strict as it was when block presence was the trigger.
    """

    return any(key in context for key in EXPERIMENT_BINDING_KEYS)


def check_run_export_eligibility(run_dir: Path, *, public_export: bool = False) -> None:
    """Re-check the experiment receipt before validating or exporting a run.

    A run that records an experiment identity must still point at a portable,
    hash-matching experiment manifest in an exportable state. This is the
    existing refusal policy; it is kept here so every caller of the report
    operation enforces the same boundary instead of the CLI keeping its own
    copy.
    """
    receipt_path = run_dir / "manifest.json"
    if not receipt_path.is_file():
        if public_export:
            raise ValueError("public export requires a classified experiment receipt")
        return
    receipt = read_json(receipt_path)
    context = receipt.get("experiment")
    if "experiment" in receipt and not isinstance(context, Mapping):
        raise ValueError("experiment receipt context must be an object")
    if context is None or not _declares_experiment_identity(context):
        if public_export:
            raise ValueError("public export requires classification='public_benchmark'")
        return
    manifest_ref = context.get("manifest_ref")
    if not isinstance(manifest_ref, str) or not manifest_ref.strip():
        raise ValueError("experiment receipt lacks a portable manifest_ref")
    if Path(manifest_ref).is_absolute():
        raise ValueError("experiment receipt requires a portable manifest_ref")
    for key in EXPERIMENT_BINDING_KEYS:
        if key in context and (
            not isinstance(context[key], str) or not context[key].strip()
        ):
            raise ValueError(f"experiment receipt has invalid {key}")
    manifest_hash = context.get("manifest_sha256")
    if (
        not isinstance(manifest_hash, str)
        or len(manifest_hash) != 64
        or any(char not in "0123456789abcdef" for char in manifest_hash)
    ):
        raise ValueError("experiment receipt has invalid manifest_sha256")
    manifest_path = (run_dir / manifest_ref).resolve()
    if not manifest_path.is_file():
        raise FileNotFoundError(f"experiment manifest is missing: {manifest_path}")

    from cept.util import sha256_file

    if sha256_file(manifest_path) != context.get("manifest_sha256"):
        raise ValueError("experiment manifest hash no longer matches the run receipt")
    # The state machine behind ``state``/``classification`` is a capability core
    # does not ship. With the research runtime installed this is the original
    # check, unchanged; without it the slot refuses by name instead of us
    # inventing a verdict.
    try:
        diagnostics = resolve_experiment_diagnostics(manifest_path)
    except ExperimentRuntimeUnavailable as unavailable:
        raise ValueError(str(unavailable)) from unavailable
    if diagnostics["state"] in {"planned", "blocked", "invalidated"}:
        raise ValueError(f"experiment state is {diagnostics['state']}; validation/export is refused")
    if public_export and diagnostics.get("classification") != "public_benchmark":
        raise ValueError("public export is allow-listed only for classification='public_benchmark'")


def render_report_operation(request: RenderReportRequest) -> RenderReportOutcome:
    """Render one report artifact for ``request.run_dir`` and report the outcome.

    The console receipt matches the CLI export receipt because this function is
    the CLI handler's only implementation.
    """
    run_dir = Path(request.run_dir).resolve()
    report_format = ReportFormat(request.format)
    check_run_export_eligibility(run_dir, public_export=request.public_export)

    if report_format is not ReportFormat.HTML:
        _require_export_extra()

    case_path, result_path = run_dir / CASE_FILENAME, run_dir / RESULT_FILENAME
    if not case_path.exists() or not result_path.exists():
        raise FileNotFoundError("run directory must contain case.json and results.json")

    if report_format is ReportFormat.HTML:
        output_path = _html_report_path(run_dir, case_path, result_path)
    elif report_format is ReportFormat.PDF:
        # The PDF prints the run's own report; it never re-reads the Case.
        output_path = _render_pdf(run_dir)
    else:
        case = Case.model_validate(read_json(case_path))
        result = StudyResult.model_validate(read_json(result_path))
        output_path = _render_docx(case, result, run_dir, _stored_validation(run_dir))

    return RenderReportOutcome(
        exit_code=0,
        output_path=output_path,
        run_dir=run_dir,
        format=report_format,
        case_fingerprint=_stored_field(run_dir, "case_fingerprint"),
        claim=_stored_field(run_dir, "claim"),
        validation_passed=_stored_field(run_dir, "passed"),
        verdict=_stored_verdict(run_dir),
    )


def _require_export_extra() -> None:
    """Fail with the documented optional-extra guidance before rendering."""
    try:
        from cept.reporting.export import export_docx, export_pdf  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "Report export needs the optional 'export' extra: "
            'pip install -e ".[export]" (and `playwright install chromium` for PDF).'
        ) from exc


def _html_report_path(run_dir: Path, case_path: Path, result_path: Path) -> Path:
    """Return the run's report, rendering it from stored artifacts if absent.

    The run owner injects review and evidence sections into ``report.html``
    after the base render. Re-rendering here would remove them, so an existing
    report is the artifact; only a run that never wrote one gets one, built
    from the very same ``case.json``/``results.json`` the DOCX format uses.
    """
    report_path = run_dir / REPORT_FILENAME
    if report_path.is_file():
        return report_path
    from cept.application.artifacts import _render_report_html

    case = Case.model_validate(read_json(case_path))
    result = StudyResult.model_validate(read_json(result_path))
    rendered, _html = _render_report_html(result, case, run_dir)
    print(f"HTML written: {rendered}")
    return rendered


def _render_pdf(run_dir: Path) -> Path:
    """Print the run's actual HTML report headlessly."""
    from cept.reporting.export import export_pdf

    html_path = run_dir / REPORT_FILENAME
    if not html_path.exists():
        raise FileNotFoundError(f"{html_path} not found.")
    out = export_pdf(html_path, run_dir / "report.pdf")
    print(f"PDF written: {out}")
    return out


def _render_docx(
    case: Case, result: StudyResult, run_dir: Path, validation: dict[str, Any] | None
) -> Path:
    """Build the native Word report from the same Case/result data the HTML uses."""
    from cept.reporting.export import export_docx

    out = export_docx(result, case, run_dir / "report.docx", validation_summary=validation)
    print(f"DOCX written: {out}")
    return out


def _stored_validation(run_dir: Path) -> dict[str, Any] | None:
    """Read the run's stored validation summary, if the run wrote one."""
    validation_path = run_dir / VALIDATION_FILENAME
    return read_json(validation_path) if validation_path.exists() else None


def _stored_field(run_dir: Path, field: str) -> Any:
    validation = _stored_validation(run_dir)
    return None if validation is None else validation.get(field)


def _stored_verdict(run_dir: Path) -> str | None:
    record_path = run_dir / VALIDATION_RECORD_FILENAME
    if not record_path.is_file():
        return None
    record = read_json(record_path)
    verdict = record.get("verdict")
    return None if verdict is None else str(verdict)


__all__ = [
    "CASE_FILENAME",
    "EXPERIMENT_BINDING_KEYS",
    "REPORT_FILENAME",
    "RESULT_FILENAME",
    "VALIDATION_FILENAME",
    "VALIDATION_RECORD_FILENAME",
    "RenderReportOutcome",
    "RenderReportRequest",
    "ReportFormat",
    "check_run_export_eligibility",
    "render_report_operation",
]