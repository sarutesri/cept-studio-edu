"""Neutral application operations for auditing persisted evidence.

These are the single implementation owners of four read-only audit
capabilities. Each one is a fail-closed inspection of artifacts that already
exist on disk: none of them runs a solver, edits a Case, invents an engineering
value, or promotes a claim.

- :func:`physics_audit_operation` runs the identity/finiteness/consistency
  audit over one persisted run directory.
- :func:`artifacts_audit_operation` inventories run directories under an
  explicit root and hashes their bound evidence files.
- :func:`per_unit_audit_operation` checks one typed Case's declared per-unit
  and kV bases for internal consistency.
- :func:`release_qualify_operation` builds a release-qualification record from
  explicitly named, already-existing artifacts.

The matching CLI handlers are thin wrappers over these functions, and the
``run-audit``/``release-qualification`` recipe stages call the same functions
in process, so a recipe and a CLI invocation can never describe the same audit
differently.

Claim ceiling: WORKFLOW_VALIDATED. A physics audit is identity, finiteness, and
self-consistency; it is not a re-solve and not a correctness proof.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cept.application.exit_codes import EXIT_ERROR, EXIT_OK, EXIT_POLICY
from cept.artifacts import artifact_root
from cept.fidelity.perunit import audit_perunit
from cept.schema import Case
from cept.util import read_json, sha256_file
from cept.verification.audits.physics import audit_run


@dataclass(frozen=True)
class PhysicsAuditRequest:
    """Explicit inputs for one physics audit.

    ``run_dir`` is the one audited directory; nothing is discovered by mtime,
    by "latest", or by a worker claim. ``out`` is an optional explicit
    destination for the encoded record.
    """

    run_dir: Path
    out: Path | None = None


@dataclass(frozen=True)
class PhysicsAuditOutcome:
    """Result of one physics audit.

    ``passed`` is the audit's own literal verdict. It is not a correctness
    claim: a passing audit means the run's quantities are finite, identified,
    and internally consistent, nothing more.
    """

    run_dir: Path
    record: dict[str, Any]
    passed: bool
    exit_code: int


@dataclass(frozen=True)
class ArtifactsAuditRequest:
    """Explicit inputs for one artifact-inventory audit."""

    root: Path


@dataclass(frozen=True)
class ArtifactsAuditOutcome:
    """Result of one artifact-inventory audit.

    ``runs`` is sorted by path so the inventory is byte-stable across runs and
    across filesystems; it is never ordered by mtime.
    """

    root: Path
    record: dict[str, Any]
    exit_code: int


@dataclass(frozen=True)
class PerUnitAuditRequest:
    """Explicit inputs for one per-unit base consistency audit."""

    case_path: Path


@dataclass(frozen=True)
class PerUnitAuditOutcome:
    """Result of one per-unit audit.

    ``error`` is set when the Case could not be read at all. Unreadable input
    is not a verdict, so it is reported separately from a BLOCKED audit.
    """

    case_path: Path
    record: dict[str, Any] | None
    passed: bool
    error: str | None
    exit_code: int


@dataclass(frozen=True)
class ReleaseQualifyRequest:
    """Explicit inputs for one release-qualification record.

    Every artifact, run, and OpenDER run is named by the caller. An empty
    request is BLOCKED rather than quietly reporting READY, and a missing
    named artifact blocks the record instead of being skipped.
    """

    root: Path
    artifacts: tuple[Path, ...] = ()
    runs: tuple[Path, ...] = ()
    opender_runs: tuple[Path, ...] = ()
    out: Path | None = None


@dataclass(frozen=True)
class ReleaseQualifyOutcome:
    """Result of one release-qualification pass."""

    root: Path
    record: dict[str, Any]
    status: str
    exit_code: int


def _write_record(out: Path | None, encoded: str) -> None:
    if out is None:
        return
    target = Path(out).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(encoded + "\n", encoding="utf-8")


def _opender_evidence(run_dir: Path) -> dict[str, Any]:
    """Report one run's OpenDER co-simulation evidence, fail-closed."""
    path = run_dir / "results.json"
    try:
        payload = read_json(path)
    except (OSError, ValueError):
        return {
            "run_dir": str(run_dir),
            "status": "BLOCKED",
            "reasons": ["missing or invalid results.json"],
        }
    records = (payload.get("extra") or {}).get("opender") or []
    reasons: list[str] = []
    if not records:
        reasons.append("results.json has no OpenDER metadata")
    if any(item.get("status") != "CO_SIMULATED" for item in records if isinstance(item, dict)):
        reasons.append("one or more OpenDER records are not CO_SIMULATED")
    return {
        "run_dir": str(run_dir),
        "status": "PASS" if not reasons else "BLOCKED",
        "records": records,
        "results_sha256": sha256_file(path),
        "reasons": reasons,
    }


def physics_audit_operation(request: PhysicsAuditRequest) -> PhysicsAuditOutcome:
    """Audit one persisted run directory and report its literal verdict."""
    record = audit_run(request.run_dir)
    _write_record(request.out, _encode(record))
    passed = bool(record["passed"])
    return PhysicsAuditOutcome(
        run_dir=request.run_dir,
        record=record,
        passed=passed,
        exit_code=EXIT_OK if passed else EXIT_POLICY,
    )


def artifacts_audit_operation(request: ArtifactsAuditRequest) -> ArtifactsAuditOutcome:
    """Inventory run directories and hash their bound evidence files.

    The inventory is read-only. It reports where the runs are and whether they
    sit inside the configured artifact root; it never moves, prunes, or
    migrates anything, and it never claims a run is valid.
    """
    root = Path(request.root).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"audit root does not exist: {root}")
    configured = artifact_root()
    runs: list[dict[str, Any]] = []
    for report in root.rglob("report.html"):
        run = report.parent
        files = {
            name: sha256_file(run / name)
            for name in ("case.json", "results.json", "manifest.json", "validation-record.json")
            if (run / name).is_file()
        }
        if files:
            runs.append(
                {
                    "path": str(run),
                    "inside_configured_root": run == configured or configured in run.parents,
                    "files": files,
                }
            )
    record = {
        "root": str(root),
        "configured_artifact_root": str(configured),
        "runs": sorted(runs, key=lambda item: item["path"]),
        "migration": "not performed; use the listed paths for an explicit review",
    }
    return ArtifactsAuditOutcome(root=root, record=record, exit_code=EXIT_OK)


def per_unit_audit_operation(request: PerUnitAuditRequest) -> PerUnitAuditOutcome:
    """Check one typed Case's declared per-unit and kV bases.

    An unreadable Case is reported as an error, never as a verdict: exit code
    ``EXIT_ERROR`` with ``record=None`` means the audit did not run.
    """
    path = Path(request.case_path)
    try:
        case = Case.model_validate(read_json(path))
    except Exception as exc:  # unreadable input is not a verdict
        return PerUnitAuditOutcome(
            case_path=path,
            record=None,
            passed=False,
            error=f"unreadable case '{path}': {type(exc).__name__}: {exc}",
            exit_code=EXIT_ERROR,
        )
    record = audit_perunit(case)
    passed = bool(record["passed"])
    return PerUnitAuditOutcome(
        case_path=path,
        record=record,
        passed=passed,
        error=None,
        exit_code=EXIT_OK if passed else EXIT_POLICY,
    )


def release_qualify_operation(request: ReleaseQualifyRequest) -> ReleaseQualifyOutcome:
    """Build a release-qualification record from named, existing artifacts.

    A request that names nothing is BLOCKED: an empty qualification record
    would read as a pass, and no claim is promoted when evidence is missing.
    """
    root = Path(request.root).resolve()
    required = [Path(item).resolve() for item in request.artifacts]
    missing = [str(path) for path in required if not path.is_file()]
    audits = [audit_run(path) for path in request.runs]
    opender = [_opender_evidence(Path(path).resolve()) for path in request.opender_runs]
    blocked = [item for item in audits if not item["passed"]]
    blocked.extend(item for item in opender if item["status"] != "PASS")
    status = "READY" if not missing and not blocked and bool(required or audits) else "BLOCKED"
    record: dict[str, Any] = {
        "schema": "cept-release-qualification-v1",
        "root": str(root),
        "status": status,
        "required_artifacts": [str(path) for path in required],
        "artifact_sha256": {
            str(path): sha256_file(path) for path in required if path.is_file()
        },
        "missing_artifacts": missing,
        "physics_audits": audits,
        "opender_runs": opender,
        "scope": "named evidence only; no claim is promoted when evidence is missing or blocked",
    }
    _write_record(request.out, _encode(record))
    return ReleaseQualifyOutcome(
        root=root,
        record=record,
        status=status,
        exit_code=EXIT_OK if status == "READY" else EXIT_POLICY,
    )


def _encode(record: dict[str, Any]) -> str:
    import json

    return json.dumps(record, indent=2, ensure_ascii=False)


__all__ = [
    "ArtifactsAuditOutcome",
    "ArtifactsAuditRequest",
    "PerUnitAuditOutcome",
    "PerUnitAuditRequest",
    "PhysicsAuditOutcome",
    "PhysicsAuditRequest",
    "ReleaseQualifyOutcome",
    "ReleaseQualifyRequest",
    "artifacts_audit_operation",
    "per_unit_audit_operation",
    "physics_audit_operation",
    "release_qualify_operation",
]
