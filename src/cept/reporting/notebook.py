"""Assemble a Jupyter notebook from a stored CEPT run directory.

This is the notebook output of the reporting owner (``src/cept/reporting/``).
It derives everything from the artifacts already on disk in one run directory
and nothing else:

* it never runs a solver;
* it never re-derives the Case fingerprint;
* it never invents a verdict, a value, or an artifact name;
* it embeds no solver/model/validation implementation in the generated cells.

Every generated code cell must execute against the run directory it was
assembled from. When an allowlisted result view cannot run against a stored run
— ``display_run`` needs the ``public-verification.json`` receipt that only
``cept.public.run_study`` writes, and ``cept run`` writes none — the
assembler emits the view that does run (``display_sld``, which reads
``results.json``) and states in prose what the full view needs and what produces
it. It never emits a cell that fails for a reason the notebook itself documents,
and it never writes a verification artifact of its own.

The generated code cells may only reach engineering values through the
allowlisted public result-view API :mod:`cept.public_notebook`, whose
``__all__`` (``display_run``, ``display_sld``, ``render_run_html``,
``render_sld_html``, ``render_study_html``) is the documented notebook surface.
Anything else a cell needs is read from the stored artifacts with the standard
library. Nothing in this module imports an engine, an adapter, or a licensed
host.

Target rule
-----------
A plain notebook does not imply Colab readiness. ``target="plain"`` is the only
implemented target. ``target="colab"`` raises
:class:`NotebookTargetUnsupported` until a Colab runtime, its pinned
dependencies, its kernel/startup behaviour, and an executed-notebook acceptance
run are implemented and tested here — the plan's target rule forbids shipping a
Colab claim that is not backed by a real Colab execution.

Exit codes
----------
The outcome carries the canonical CEPT codes, mirroring
``cept.application.exit_codes`` without importing the CLI (reporting must not
depend on the CLI layer):

* ``0`` — the notebook was written and the stored verdict is a pass;
* ``1`` — fail-closed: no notebook is written because a required artifact is
  missing, malformed, self-contradictory, or comes from a licensed host;
* ``3`` — the notebook was written and carries the run's honest non-pass
  verdict, exactly like ``cept verify`` for an unverified run.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

from cept.util import require_distinct_output, sha256_file, write_json

NotebookTarget = Literal["plain", "colab"]

NOTEBOOK_SCHEMA = "cept-run-notebook-v1"
NOTEBOOK_FORMAT_VERSION = 4
NOTEBOOK_MINOR_VERSION = 5

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_POLICY = 3

_SUPPORTED_TARGETS = frozenset({"plain"})
_RESULT_VIEW_MODULE = "cept.public_notebook"
# The full run view re-validates a CEPT Public run directory through
# ``cept.public.verify_study``, which requires the ``public-verification.json``
# receipt. Only ``cept.public.run_study`` writes that receipt; the ``cept study
# run`` execution path does not.
_FULL_RUN_VIEW = "display_run"
# The SLD view reads ``results.json`` only, so it executes against any stored
# run directory, including one produced by the ``cept run`` CLI.
_SLD_VIEW = "display_sld"
_BLOCKED_CLAIM = "BLOCKED"

_MANIFEST = "manifest.json"
_RESULTS = "results.json"
_VALIDATION_REPORT = "validation_report.json"
_VALIDATION_RECORD = "validation-record.json"
_SLD_FIDELITY = "sld-fidelity.json"
_IDENTITY_MAP = "identity-map.json"
_PUBLIC_VERIFICATION = "public-verification.json"

# Validation verdict vocabulary that a stored artifact may use. Anything else is
# carried verbatim instead of being interpreted.
_PASS_LABELS = frozenset({"pass", "passed", "ok", "success"})
_FAIL_LABELS = frozenset({"fail", "failed", "fail-closed", "blocked", "error", "not_passed"})

# Engines this notebook output refuses to represent. A licensed-host run would
# need that host's internals to render honestly, and this module must stay free
# of licensed-host imports.
_LICENSED_HOST_ENGINES = frozenset({"powerfactory", "pf", "pscad", "pscad_slpower"})


class NotebookAssemblyError(RuntimeError):
    """Stored run evidence is missing, malformed, or self-contradictory."""


class NotebookTargetUnsupported(NotImplementedError):
    """A declared notebook target has no implemented and tested runtime."""


@dataclass(frozen=True)
class AssembleNotebookRequest:
    """Everything needed to turn one stored run directory into one notebook."""

    run_dir: Path
    output_path: Path
    target: NotebookTarget = "plain"


@dataclass(frozen=True)
class AssembleNotebookOutcome:
    """Result of one assembly attempt.

    ``cells_emitted`` counts every emitted cell (markdown and code). On a
    fail-closed attempt no notebook is written, ``cells_emitted`` is ``0``, and
    ``output_path`` is the requested path so the caller can report where the
    notebook would have gone.
    """

    exit_code: int
    output_path: Path
    cells_emitted: int
    verdict: str
    claim: str
    warnings: list[str]
    limitations: list[str]


@dataclass(frozen=True)
class _StoredEvidence:
    """Verbatim stored evidence, already cross-checked for self-consistency."""

    run_dir: Path
    case_fingerprint: str
    case_name: str
    study_type: str
    engine: str
    engine_version: str
    attempt_id: str
    assessment_id: str
    execution_key: str
    claim: str
    claim_cap: str
    scope: str
    validation_file: str
    verdict: str
    passed: bool
    public_verified: bool
    identity_rows: tuple[tuple[str, str, str], ...]
    sld_rows: tuple[tuple[str, str, str], ...]
    verdict_rows: tuple[tuple[str, str, str], ...]
    artifacts: tuple[tuple[str, str], ...]
    warnings: tuple[str, ...]
    limitations: tuple[str, ...]


# ---------------------------------------------------------------------------
# Stored-artifact reading (fail closed)
# ---------------------------------------------------------------------------


def _read_optional(path: Path) -> dict[str, Any]:
    """Return a JSON object, or ``{}`` when the artifact is absent.

    A file that exists but does not parse into a JSON object is corruption in
    the evidence set, not an absent artifact, and fails closed.
    """
    if not path.is_file():
        return {}
    return _read_required(path)


def _read_required(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise NotebookAssemblyError(f"required run artifact is missing: {path.name}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NotebookAssemblyError(f"run artifact is not readable JSON: {path.name} ({exc})") from exc
    if not isinstance(payload, Mapping):
        raise NotebookAssemblyError(f"run artifact must contain a JSON object: {path.name}")
    return dict(payload)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _agreed(field: str, sources: Mapping[str, Mapping[str, Any]]) -> str:
    """Return one stored value that every artifact holding it already agrees on.

    A disagreement between two stored copies of the same identity field is
    drift in the evidence set, and reporting it as one of them would be a
    second interpretation of run truth.
    """
    seen = {
        name: _text(payload.get(field))
        for name, payload in sources.items()
        if isinstance(payload, Mapping) and _text(payload.get(field))
    }
    if not seen:
        return ""
    distinct = set(seen.values())
    if len(distinct) > 1:
        detail = ", ".join(f"{name}={value!r}" for name, value in sorted(seen.items()))
        raise NotebookAssemblyError(f"stored {field} values disagree across artifacts: {detail}")
    return distinct.pop()


def _required_agreed(field: str, sources: Mapping[str, Mapping[str, Any]]) -> str:
    value = _agreed(field, sources)
    if not value:
        names = ", ".join(sorted(sources))
        raise NotebookAssemblyError(f"no stored {field} in any of: {names}")
    return value


def _label_passness(label: str) -> bool | None:
    normalized = label.strip().lower().replace(" ", "-")
    if normalized in _PASS_LABELS:
        return True
    if normalized in _FAIL_LABELS:
        return False
    return None


def _resolve_verdict(report: Mapping[str, Any], record: Mapping[str, Any]) -> tuple[bool, str]:
    """Read the run's stored verdict, or fail closed when it is not there.

    Only a literal boolean in the stored validation report decides pass/fail. A
    status or verdict *label* is accepted as the wording of the verdict and as
    a contradiction check, never as a substitute for that boolean.
    """
    declared = report.get("passed")
    if declared is None or not isinstance(declared, bool):
        raise NotebookAssemblyError(
            f"{_VALIDATION_REPORT} is absent or stores no boolean 'passed'; found "
            f"{type(declared).__name__}. A stored label such as "
            f"{_VALIDATION_RECORD}['verdict'] is the wording of a verdict, never a "
            f"substitute for that boolean, so this fails closed. Re-run the study so "
            f"the run stores {_VALIDATION_REPORT} with a literal boolean."
        )
    labels = {
        _VALIDATION_RECORD: _text(record.get("verdict")),
        _VALIDATION_REPORT: _text(report.get("status")),
    }
    labels = {name: value for name, value in labels.items() if value}
    known = {name: value for name, value in labels.items() if _label_passness(value) is not None}
    if known and len(set(_label_passness(value) for value in known.values())) > 1:
        detail = ", ".join(f"{name}={known[name]!r}" for name in sorted(known))
        raise NotebookAssemblyError(f"stored validation labels contradict each other: {detail}")
    if known and next(iter(_label_passness(value) for value in known.values())) is not declared:
        raise NotebookAssemblyError(
            f"{_VALIDATION_REPORT} 'passed'={declared!r} contradicts its stored status label"
        )
    stored_label = labels.get(_VALIDATION_RECORD) or labels.get(_VALIDATION_REPORT)
    return declared, stored_label or ("pass" if declared else "failed")


def _artifact_inventory(run_dir: Path) -> tuple[tuple[str, str], ...]:
    return tuple(
        (entry.name, sha256_file(entry))
        for entry in sorted(run_dir.iterdir(), key=lambda item: item.name)
        if entry.is_file()
    )


def _validation_warnings(report: Mapping[str, Any]) -> list[str]:
    """Carry the run's own failed checks verbatim; invent no severity."""
    warnings: list[str] = []
    checks = report.get("checks")
    if not isinstance(checks, list):
        return warnings
    for check in checks:
        if not isinstance(check, Mapping) or check.get("passed") is not False:
            continue
        label = _text(check.get("name")) or _text(check.get("label")) or "unnamed check"
        value = check.get("value")
        detail = f" ({value})" if isinstance(value, (str, int, float)) and str(value) else ""
        violations = check.get("violations")
        extra = ""
        if isinstance(violations, list) and violations:
            extra = ": " + ", ".join(str(item) for item in violations)
        warnings.append(f"{_VALIDATION_REPORT} check {label!r} did not pass{detail}{extra}")
    return warnings


def _sld_rows(fidelity: Mapping[str, Any]) -> tuple[tuple[str, str, str], ...]:
    rows: list[tuple[str, str, str]] = []
    for field, label in (("verdict", "SLD fidelity verdict"), ("passed", "SLD fidelity passed")):
        value = fidelity.get(field)
        if isinstance(value, bool):
            rows.append((label, "true" if value else "false", _SLD_FIDELITY))
        elif isinstance(value, str) and value.strip():
            rows.append((label, value.strip(), _SLD_FIDELITY))
    for field, label in (("n_nodes", "SLD nodes"), ("n_edges", "SLD branches")):
        value = fidelity.get(field)
        if isinstance(value, int) and not isinstance(value, bool):
            rows.append((label, str(value), _SLD_FIDELITY))
    reasons = fidelity.get("reasons")
    if isinstance(reasons, list) and reasons:
        rows.append(("SLD fidelity reasons", "; ".join(str(item) for item in reasons), _SLD_FIDELITY))
    return tuple(rows)


def _verify_stored_evidence(
    run_dir: Path, manifest: Mapping[str, Any], record: Mapping[str, Any], *, passed: bool
) -> None:
    """Use the canonical verifier without replacing the stored verdict."""
    try:
        if (
            manifest.get("schema") == "cept-public-run-manifest-v1"
            or manifest.get("edition") == "public"
            or (run_dir / _PUBLIC_VERIFICATION).exists()
        ):
            from cept.public import verify_study

            verification = verify_study(run_dir)
            # Solver/acceptance failures are legitimate stored non-pass evidence;
            # metadata and byte-integrity failures are never representable.
            integrity_checks = {
                "case_fingerprint", "solver_identity", "study_identity",
                "manifest_schema", "manifest_identity", "manifest_study_identity",
                "manifest_engine_identity", "validation_identity",
                "stored_receipt_identity", "attempt_identity", "artifact_integrity",
            }
            reasons = [
                check["detail"]
                for check in verification["checks"]
                if check["passed"] is not True
                and (passed or check["name"] in integrity_checks)
            ]
        else:
            from cept.verification.run_set import verify_run_set

            hashes = record.get("artifact_hashes")
            required = ("case.json", _RESULTS, "report.html")
            if not isinstance(hashes, Mapping) or any(
                not isinstance(hashes.get(name), str) or not hashes[name]
                for name in required
            ):
                raise NotebookAssemblyError(
                    "validation-record.json lacks required byte-bound artifact hashes"
                )
            verification = verify_run_set([run_dir])
            reasons = list(verification["runs"][0]["reasons"])
            if not passed:
                reasons = [
                    reason for reason in reasons
                    if reason != "validation report does not explicitly report passed=true"
                    and not reason.startswith("manifest status is ")
                ]
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
        raise NotebookAssemblyError(f"canonical run verification failed: {exc}") from exc
    if reasons:
        raise NotebookAssemblyError("canonical run verification failed: " + "; ".join(reasons))


def _read_evidence(run_dir: Path) -> _StoredEvidence:
    manifest = _read_required(run_dir / _MANIFEST)
    results = _read_required(run_dir / _RESULTS)
    if not (run_dir / _VALIDATION_REPORT).is_file() and not (run_dir / _VALIDATION_RECORD).is_file():
        raise NotebookAssemblyError(
            f"required run artifact is missing: {_VALIDATION_REPORT} (and {_VALIDATION_RECORD})"
        )
    report = _read_optional(run_dir / _VALIDATION_REPORT)
    record = _read_optional(run_dir / _VALIDATION_RECORD)
    fidelity = _read_optional(run_dir / _SLD_FIDELITY)
    identity_map = _read_optional(run_dir / _IDENTITY_MAP)
    public_receipt = _read_optional(run_dir / _PUBLIC_VERIFICATION)

    identity_sources: dict[str, Mapping[str, Any]] = {_MANIFEST: manifest, _RESULTS: results}
    if report:
        identity_sources[_VALIDATION_REPORT] = report
    if record:
        identity_sources[_VALIDATION_RECORD] = record
    if identity_map:
        identity_sources[_IDENTITY_MAP] = identity_map
    # The identity map carries registry provenance, not run-verdict provenance.
    verdict_sources = {name: payload for name, payload in identity_sources.items() if name != _IDENTITY_MAP}

    case_fingerprint = _required_agreed("case_fingerprint", identity_sources)
    engine = _required_agreed("engine", _engine_sources(manifest, results, report, record))
    study_type = _required_agreed("study_type", identity_sources)
    if engine.strip().lower() in _LICENSED_HOST_ENGINES:
        raise NotebookAssemblyError(
            f"run engine {engine!r} is a licensed host; this notebook output stays free of "
            "PowerFactory/licensed-host internals and will not represent that run"
        )

    engine_version = _agreed("engine_version", verdict_sources)
    claim = _agreed("claim", verdict_sources) or _text(public_receipt.get("claim"))
    if not claim:
        raise NotebookAssemblyError(f"no stored claim in any run artifact of {run_dir}")
    claim_cap = _agreed("claim_cap", verdict_sources) or _text(public_receipt.get("claim_boundary"))
    scope = _agreed("scope", verdict_sources) or _text(public_receipt.get("claim_boundary"))

    # `_resolve_verdict` raises unless the stored validation REPORT carries a
    # literal boolean, so reaching here means that report is present. Naming the
    # record here would imply a fallback that deliberately does not exist.
    passed, verdict = _resolve_verdict(report, record)
    _verify_stored_evidence(run_dir, manifest, record, passed=passed)
    validation_file = _VALIDATION_REPORT

    warnings: list[str] = []
    stored_fidelity_warnings = fidelity.get("warnings")
    if isinstance(stored_fidelity_warnings, list):
        warnings.extend(str(item) for item in stored_fidelity_warnings)
    if not fidelity:
        warnings.append(
            f"{_SLD_FIDELITY} is absent; this notebook states no SLD fidelity verdict for this run."
        )
    elif _label_passness(_text(fidelity.get("verdict"))) is False or fidelity.get("passed") is False:
        warnings.append(f"{_SLD_FIDELITY} records a non-pass SLD fidelity verdict.")
    warnings.extend(_validation_warnings(report))
    assumptions = manifest.get("assumptions")
    if isinstance(assumptions, list) and assumptions:
        warnings.append(
            f"{_MANIFEST} records {len(assumptions)} declared assumption(s): "
            + ", ".join(str(item) for item in assumptions)
        )
    if not public_receipt:
        warnings.append(
            f"{_PUBLIC_VERIFICATION} is absent from this run directory, so the full "
            f"{_RESULT_VIEW_MODULE}.{_FULL_RUN_VIEW} result view cannot render it; this "
            f"notebook renders the stored SLD view "
            f"({_RESULT_VIEW_MODULE}.{_SLD_VIEW}) instead and says so in section 6. The run's "
            "stored verdict is unchanged by this."
        )

    case_name = _text(manifest.get("case")) or _text(results.get("case_name")) or case_fingerprint
    identity_rows = tuple(
        row
        for row in (
            ("Case fingerprint", case_fingerprint, _MANIFEST),
            ("Case name", _text(manifest.get("case")) or _text(results.get("case_name")), _MANIFEST),
            ("Study type", study_type, _MANIFEST),
            ("Engine", engine, _MANIFEST),
            ("Engine version", engine_version, _MANIFEST),
            ("Attempt id", _agreed("attempt_id", verdict_sources), _MANIFEST),
            ("Assessment id", _agreed("assessment_id", verdict_sources), _MANIFEST),
            ("Execution key", _agreed("execution_key", verdict_sources), _MANIFEST),
            ("Registry version", _text(identity_map.get("registry_version")), _IDENTITY_MAP),
            ("Source revision", _text(manifest.get("source_revision")), _MANIFEST),
            ("CEPT version", _text(manifest.get("cept_version")) or _text(manifest.get("package_version")), _MANIFEST),
            ("Python version", _text(manifest.get("python_version")), _MANIFEST),
            ("Platform", _text(manifest.get("platform")), _MANIFEST),
            ("Claim", claim, validation_file),
            ("Claim ceiling (not a claim)", claim_cap, validation_file),
            ("Stored validation scope", scope, validation_file),
        )
        if row[1]
    )
    return _StoredEvidence(
        run_dir=run_dir,
        case_fingerprint=case_fingerprint,
        case_name=case_name,
        study_type=study_type,
        engine=engine,
        engine_version=engine_version,
        attempt_id=_agreed("attempt_id", verdict_sources),
        assessment_id=_agreed("assessment_id", verdict_sources),
        execution_key=_agreed("execution_key", verdict_sources),
        claim=claim,
        claim_cap=claim_cap,
        scope=scope,
        validation_file=validation_file,
        verdict=verdict,
        passed=passed,
        public_verified=bool(public_receipt),
        identity_rows=identity_rows,
        sld_rows=_sld_rows(fidelity),
        verdict_rows=(
            ("Validation verdict", verdict, validation_file),
            ("Validation passed", "true" if passed else "false", validation_file),
        ),
        artifacts=_artifact_inventory(run_dir),
        warnings=tuple(warnings),
        limitations=tuple(_stored_limitations(record, report, public_receipt)),
    )


def _engine_sources(
    manifest: Mapping[str, Any],
    results: Mapping[str, Any],
    report: Mapping[str, Any],
    record: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    """Engine identity, normalising the manifest's ``engine``/``backend`` keys."""
    sources: dict[str, Mapping[str, Any]] = {}
    engine = _text(manifest.get("engine")) or _text(manifest.get("backend"))
    if engine:
        sources[_MANIFEST] = {"engine": engine}
    for name, payload in ((_RESULTS, results), (_VALIDATION_REPORT, report), (_VALIDATION_RECORD, record)):
        value = _text(payload.get("engine"))
        if value:
            sources[name] = {"engine": value}
    if not sources:
        raise NotebookAssemblyError("no stored engine identity in the run artifacts")
    return sources


def _stored_limitations(
    record: Mapping[str, Any],
    report: Mapping[str, Any],
    public_receipt: Mapping[str, Any],
) -> list[str]:
    """Return the run's stored limitations verbatim, or say that there are none."""
    stored = record.get("limitations")
    if not isinstance(stored, list):
        stored = report.get("limitations")
    limitations = [str(item) for item in stored] if isinstance(stored, list) else []
    scope = _text(record.get("scope")) or _text(report.get("scope"))
    if scope:
        limitations.append(f"Stored validation scope: {scope}")
    if not limitations:
        limitations.append(
            "This run stores no limitations record; this notebook asserts none and does not "
            "substitute an assumption of its own."
        )
    return limitations


# ---------------------------------------------------------------------------
# Notebook emission
# ---------------------------------------------------------------------------


def _md(cell_id: str, source: str) -> dict[str, Any]:
    return {"cell_type": "markdown", "id": cell_id, "metadata": {}, "source": _lines(source)}


def _code(cell_id: str, source: str) -> dict[str, Any]:
    return {
        "cell_type": "code",
        "execution_count": None,
        "id": cell_id,
        "metadata": {},
        "outputs": [],
        "source": _lines(source),
    }


def _lines(text: str) -> list[str]:
    """Notebook cell sources are line lists that keep their trailing newlines."""
    if not text:
        return []
    lines = text.split("\n")
    return [line + "\n" for line in lines[:-1]] + ([lines[-1]] if lines[-1] else [])


def _cell(text: str) -> str:
    """Flatten a notebook source list back to text for markdown tables."""
    return "".join(text) if isinstance(text, list) else str(text)


def _table(headers: tuple[str, str], rows: tuple[tuple[str, str, str], ...]) -> str:
    if not rows:
        return "_No stored rows._\n"
    lines = [f"| {headers[0]} | {headers[1]} | Stored artifact |", "| --- | --- | --- |"]
    for label, value, artifact in rows:
        clean = " ".join(value.split()).replace("|", "\\|")
        lines.append(f"| {label} | {clean} | `{artifact}` |")
    return "\n".join(lines) + "\n"


def _bullets(items: tuple[str, ...] | list[str], empty: str) -> str:
    if not items:
        return empty + "\n"
    return "\n".join(f"- {item}" for item in items) + "\n"


def _relative_run_dir(run_dir: Path, notebook_dir: Path) -> str | None:
    """Path of the run directory relative to the notebook, or ``None`` across drives."""
    try:
        return os.path.relpath(run_dir, notebook_dir)
    except ValueError:  # different Windows drives have no relative path
        return None


def _setup_source(run_dir: Path, output_path: Path) -> tuple[str, bool]:
    """Return the setup cell source and whether it needs CEPT_RUN_DIR."""
    relative = _relative_run_dir(run_dir, output_path.parent)
    if relative is None:
        return (
            "# The run directory is on a different volume than this notebook, so no relative\n"
            "# path can be embedded. Point CEPT_RUN_DIR at the run directory before running this cell.\n"
            "import os\n"
            "from pathlib import Path\n"
            "\n"
            'RUN_DIR = Path(os.environ["CEPT_RUN_DIR"]).expanduser().resolve()\n'
            'print("run directory:", RUN_DIR)\n',
            True,
        )
    return (
        "# Locate the persisted run directory this notebook reads.\n"
        "#\n"
        "# Nothing in this notebook runs a solver: the stored run artifacts are the only source\n"
        "# of engineering values here. The default path is relative to this notebook's directory;\n"
        "# set CEPT_RUN_DIR to read a run directory stored somewhere else.\n"
        "import os\n"
        "from pathlib import Path\n"
        "\n"
        'RUN_DIR = Path(os.environ.get("CEPT_RUN_DIR") or r"' + relative + '").expanduser().resolve()\n'
        'print("run directory:", RUN_DIR)\n',
        False,
    )


def _validation_cell_source(validation_file: str) -> str:
    return (
        "# Read the run's stored validation record verbatim, so the verdict shown above can be\n"
        "# checked against the artifact instead of taken on trust.\n"
        "import json\n"
        "\n"
        f'STORED_VALIDATION = RUN_DIR / "{validation_file}"\n'
        'stored = json.loads(STORED_VALIDATION.read_text(encoding="utf-8"))\n'
        'print(STORED_VALIDATION.name, "passed:", stored.get("passed"))\n'
        'print(STORED_VALIDATION.name, "status:", stored.get("status"))\n'
        'for check in stored.get("checks", []) or []:\n'
        '    print("   ", check.get("name") or check.get("label"), "->", check.get("passed"))\n'
    )


def _render_cell_source(call: str) -> str:
    return (
        "# Render the persisted solver result through the public result-view API.\n"
        f"from {_RESULT_VIEW_MODULE} import {call}\n"
        "\n"
        f"{call}(RUN_DIR)\n"
    )


def _render_section(evidence: _StoredEvidence) -> str:
    """Markdown for the result view this run directory can actually render.

    The rule this section exists to enforce: a notebook never emits a code cell
    that fails for a reason the notebook itself documents. ``display_run``
    re-validates through ``cept.public.verify_study``, which requires the
    ``public-verification.json`` receipt that only ``cept.public.run_study``
    writes, so it is emitted only when that receipt is stored.
    """
    heading = "## 6. Render the persisted result\n\n"
    if evidence.public_verified:
        return (
            heading
            + f"This run directory stores `{_PUBLIC_VERIFICATION}`, so the full public result "
            f"view `{_RESULT_VIEW_MODULE}.{_FULL_RUN_VIEW}` can re-validate this run and render "
            "it. The cell below runs against the run directory as stored.\n"
        )
    return (
        heading
        + f"**The full public result view `{_RESULT_VIEW_MODULE}.{_FULL_RUN_VIEW}` is not "
        f"available for this run.** It re-validates a CEPT Public run directory before it "
        f"renders, and that validation reads `{_PUBLIC_VERIFICATION}` from the run directory. "
        "This run stores no such receipt, because the `cept run` execution path writes "
        "none. This notebook therefore emits no `"
        f"{_FULL_RUN_VIEW}` cell here: against this run directory it would raise `ValueError: "
        f"cannot read JSON file .../{_PUBLIC_VERIFICATION}`.\n\n"
        f"The cell below instead renders the stored engineering SLD, bus table, and study plot "
        f"through `{_RESULT_VIEW_MODULE}.{_SLD_VIEW}`, which reads `results.json` only. It runs "
        "against this run directory exactly as stored.\n\n"
        "What that view does not include is the public verification summary that `"
        f"{_FULL_RUN_VIEW}` adds alongside the result. `{_PUBLIC_VERIFICATION}` is written by "
        "the CEPT Public API `cept.public.run_study(...)`, and by nothing else. In the CEPT "
        "Public edition that entry point is the `cept run` / `cept study demo` route — "
        "the public edition installs `cept = cept.public_cli:main`, so it is reachable as "
        "`python -m cept.public_cli study run <case.json> --out <run-dir>`. The internal "
        "`cept run` execution path that produced this run directory does not write that "
        "receipt. Produce a run through the public-edition route, then assemble a notebook from "
        "that run directory, to get the full run view.\n\n"
        "This is a statement about which result view this notebook can render. It is not a "
        f"change to the run's stored verdict: the verdict above is the verdict stored in "
        f"`{evidence.validation_file}`, unchanged.\n"
    )


def _boundary_section(view_call: str, *, revalidated: bool) -> str:
    reads = (
        "`results.json` from the run directory and re-validates the stored evidence against "
        f"`{_PUBLIC_VERIFICATION}`"
        if revalidated
        else "`results.json` from the run directory and reads no other artifact"
    )
    return (
        "## 5. What these cells are allowed to do\n\n"
        f"The only CEPT API the code cells call is `{_RESULT_VIEW_MODULE}.{view_call}`, an "
        f"allowlisted public result view. It reads {reads}. It does not run a solver and does "
        "not recompute an engineering quantity. Everything else is a standard-library read of a "
        "file in the run directory.\n"
    )


def _build_cells(evidence: _StoredEvidence, output_path: Path, setup_cell: str) -> list[dict[str, Any]]:
    view_call = _FULL_RUN_VIEW if evidence.public_verified else _SLD_VIEW
    run_label = _relative_run_dir(evidence.run_dir, output_path.parent) or "CEPT_RUN_DIR"
    verdict_word = "PASS" if evidence.passed else "NOT PASSED"
    banner = (
        f"**Stored validation verdict: `{evidence.verdict}` ({verdict_word})** — read verbatim from "
        f"`{evidence.validation_file}`. This notebook re-presents that verdict; it cannot upgrade it.\n"
    )
    header = (
        f"# CEPT run notebook — {evidence.case_fingerprint}\n\n"
        f"Stored run directory: `{run_label}`  \n"
        f"Case: {evidence.case_name}  \n"
        f"Engine: {evidence.engine}  \n"
        f"Claim: `{evidence.claim}`\n\n"
        + banner
        + "\n"
        "This notebook contains no solver, model, or validation implementation. Every engineering "
        "value it shows is read from the persisted run artifacts at execution time.\n"
    )
    evidence_md = (
        "## 1. Stored identity and verdict\n\n"
        "Each value below is copied from the artifact named in the last column. Nothing here is "
        "recomputed by this notebook.\n\n"
        + _table(("Item", "Stored value"), evidence.verdict_rows + evidence.identity_rows)
    )
    sld_md = "## 2. Stored SLD fidelity state\n\n" + _table(("Item", "Stored value"), evidence.sld_rows)
    caveats_md = (
        "## 3. Stored warnings and limitations\n\n"
        "### Warnings\n\n"
        + _bullets(evidence.warnings, "_No stored warnings._")
        + "\n### Limitations\n\n"
        + _bullets(evidence.limitations, "_No stored limitations._")
    )
    inventory_md = (
        "## 4. Artifact inventory read by this notebook\n\n"
        "SHA-256 of every file in the run directory at assembly time.\n\n"
        "| Artifact | SHA-256 |\n| --- | --- |\n"
        + "".join(f"| `{name}` | `{digest}` |\n" for name, digest in evidence.artifacts)
    )
    boundary_md = _boundary_section(view_call, revalidated=evidence.public_verified)
    return [
        _md("cept-00-header", header),
        _md("cept-01-identity", evidence_md),
        _md("cept-02-sld", sld_md),
        _md("cept-03-caveats", caveats_md),
        _md("cept-04-inventory", inventory_md),
        _md("cept-05-boundary", boundary_md),
        _code("cept-06-setup", setup_cell),
        _code("cept-07-stored-validation", _validation_cell_source(evidence.validation_file)),
        _md("cept-08-render", _render_section(evidence)),
        _code("cept-09-render", _render_cell_source(view_call)),
    ]


def _build_notebook(evidence: _StoredEvidence, output_path: Path, setup_cell: str) -> dict[str, Any]:
    cells = _build_cells(evidence, output_path, setup_cell)
    for cell in cells:
        if cell["cell_type"] == "code":
            compile(_cell(cell["source"]), f"{output_path.name}:{cell['id']}", "exec")
    language_info: dict[str, Any] = {"name": "python"}
    python_version = next(
        (value for label, value, _ in evidence.identity_rows if label == "Python version"), ""
    )
    if python_version:
        language_info["version"] = python_version
    return {
        "cells": cells,
        "metadata": {
            "cept": {
                "schema": NOTEBOOK_SCHEMA,
                "source": "stored run artifacts",
                "case_fingerprint": evidence.case_fingerprint,
                "engine": evidence.engine,
                "study_type": evidence.study_type,
                "attempt_id": evidence.attempt_id,
                "assessment_id": evidence.assessment_id,
                "execution_key": evidence.execution_key,
                "claim": evidence.claim,
                "claim_cap": evidence.claim_cap,
                "validation_artifact": evidence.validation_file,
                "verdict": evidence.verdict,
                "passed": evidence.passed,
                "warnings": list(evidence.warnings),
                "limitations": list(evidence.limitations),
            },
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": language_info,
        },
        "nbformat": NOTEBOOK_FORMAT_VERSION,
        "nbformat_minor": NOTEBOOK_MINOR_VERSION,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _require_supported_target(target: str) -> None:
    if target in _SUPPORTED_TARGETS:
        return
    if target == "colab":
        raise NotebookTargetUnsupported(
            "target='colab' is not implemented. A Colab notebook output is permitted only once its "
            "runtime declaration, pinned dependencies, kernel/startup behaviour, and an executed "
            "notebook acceptance run are implemented and tested here; until then this module emits "
            "target='plain' only, because a plain notebook does not imply Colab readiness. "
            "Assemble target='plain' and run it in a real Colab session before claiming Colab support."
        )
    raise NotebookTargetUnsupported(
        f"unknown notebook target {target!r}; supported targets are {sorted(_SUPPORTED_TARGETS)}"
    )


def assemble_run_notebook(request: AssembleNotebookRequest) -> AssembleNotebookOutcome:
    """Assemble one ``.ipynb`` from the artifacts stored in ``request.run_dir``.

    Fails closed: a missing, malformed, self-contradictory, or licensed-host
    evidence set produces no notebook and a non-zero exit code. A readable run
    whose stored verdict is not a pass still gets a notebook that states that
    verdict, with a non-zero exit code.
    """
    _require_supported_target(request.target)

    run_dir = Path(request.run_dir)
    output_path = Path(request.output_path)
    try:
        if not run_dir.is_dir():
            raise NotebookAssemblyError(f"run directory does not exist: {run_dir}")
        evidence = _read_evidence(run_dir.resolve())
        protected = [
            entry for entry in evidence.run_dir.rglob("*")
            if entry.is_file() and entry.suffix.lower() != ".ipynb"
        ]
        record = _read_optional(evidence.run_dir / _VALIDATION_RECORD)
        hashes = record.get("artifact_hashes")
        if isinstance(hashes, Mapping):
            protected.extend(evidence.run_dir / name for name in hashes)
        try:
            require_distinct_output(output_path, protected)
        except ValueError as exc:
            raise NotebookAssemblyError(str(exc)) from exc
    except NotebookAssemblyError as exc:
        return AssembleNotebookOutcome(
            exit_code=EXIT_ERROR,
            output_path=output_path,
            cells_emitted=0,
            verdict="failed",
            claim=_BLOCKED_CLAIM,
            warnings=[str(exc)],
            limitations=["No stored run evidence was read, so this notebook asserts no limitation."],
        )

    setup_cell, needs_env = _setup_source(evidence.run_dir, output_path)
    notebook = _build_notebook(evidence, output_path, setup_cell)
    if needs_env:
        notebook["metadata"]["cept"]["run_dir_env"] = "CEPT_RUN_DIR"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(output_path, notebook)

    exit_code = EXIT_OK if evidence.passed else EXIT_POLICY
    return AssembleNotebookOutcome(
        exit_code=exit_code,
        output_path=output_path,
        cells_emitted=len(notebook["cells"]),
        verdict=evidence.verdict,
        claim=evidence.claim,
        warnings=list(evidence.warnings),
        limitations=list(evidence.limitations),
    )


__all__ = [
    "AssembleNotebookOutcome",
    "AssembleNotebookRequest",
    "NotebookAssemblyError",
    "NotebookTarget",
    "NotebookTargetUnsupported",
    "assemble_run_notebook",
]
