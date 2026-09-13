"""Stable, OpenDSS-first CEPT Public workflow.

This module is the deliberately small education-facing surface.  It reuses
the canonical :class:`cept.schema.case.Case`, OpenDSS adapter, and result
models; it does not create a second solver or expose PowerFactory internals.

The current CEPT Public boundary supports inline Cases and the bundled IEEE
13-node feeder for load flow, unbalanced load flow, hosting capacity, and fault
studies. Every file-backed run carries a case fingerprint, solver identity, an
explicit ``WORKFLOW_VALIDATED`` claim boundary, and a fail-closed verification
receipt.
"""

from __future__ import annotations
import hashlib
import importlib.metadata
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from cept.application.readiness import readiness_for_case
from cept.application.run_identity import artifact_set_digest, assessment_id, new_attempt_id
from cept.schema import Case
from cept.schema.result import StudyResult
from cept.studies.planning import build_execution_plan, execute_run, execution_plan_summary

PUBLIC_STUDIES = frozenset({"load_flow", "unbalanced_load_flow", "hosting_capacity", "fault"})
PUBLIC_BUILTINS = frozenset({"ieee13"})
PUBLIC_CLAIM = "WORKFLOW_VALIDATED"
PUBLIC_SUPPORT: dict[str, dict[str, str]] = {
    "desktop": {"platform": "windows", "python": "3.10"},
    "notebook": {"runtime": "google_colab", "platform": "linux", "status": "candidate"},
    "standalone_linux": {"status": "not_supported_initially"},
}
_OWNED_ARTIFACTS = (
    "attempt.json",
    "case.json",
    "results.json",
    "manifest.json",
    "validation_report.json",
    "public-verification.json",
)

class PublicBoundaryError(ValueError):
    """A Case or operation outside the CEPT Public support boundary."""

@dataclass(frozen=True)
class PublicRun:
    """The in-memory result and verification receipt for one public run."""

    case: Case
    result: StudyResult
    verification: dict[str, Any]
    run_dir: Path | None = None
    execution_plan: dict[str, Any] | None = None


def public_capabilities() -> dict[str, Any]:
    """Return the public capability contract without contacting a solver."""

    return {
        "edition": "public",
        "distribution": "cept-power-studio",
        "version": public_version(),
        "engine": "opendss",
        "support": {name: dict(details) for name, details in PUBLIC_SUPPORT.items()},
        "capabilities": {
            name: {"engine": "opendss", "status": "candidate"} for name in sorted(PUBLIC_STUDIES)
        },
        "excluded": {
            "dynamics": "excluded_initially",
            "powerfactory": "pro_only",
        },
        "claim_boundary": "WORKFLOW_VALIDATED only; not PROJECT_VALIDATED or field-evidence acceptance",
    }


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON file {path}: {exc}") from exc


def load_case(path: str | Path) -> Case:
    """Load and validate a JSON Case for the public workflow."""

    case_path = Path(path).resolve()
    if case_path.suffix.lower() != ".json":
        raise ValueError("public Cases must be JSON files")
    payload = _read_json(case_path)
    if not isinstance(payload, Mapping):
        raise ValueError("Case JSON must contain an object")
    return Case.model_validate(payload)


def validate_public_case(case: Case) -> dict[str, Any]:
    """Fail closed unless ``case`` is inside the reviewed public boundary."""

    if case.engine not in (None, "opendss"):
        raise PublicBoundaryError(
            "CEPT Public supports only engine='opendss'; PowerFactory is a Pro-only capability."
        )
    study_type = case.study.type
    if study_type not in PUBLIC_STUDIES:
        raise PublicBoundaryError(
            f"study.type={study_type!r} is not in the CEPT Public scope; "
            "unsupported studies are blocked rather than approximated."
        )
    kind = case.network.kind
    if kind == "builtin":
        name = str(case.network.name or "").lower()
        if name not in PUBLIC_BUILTINS:
            raise PublicBoundaryError(
                f"builtin feeder {case.network.name!r} is not in the CEPT Public scope; "
                f"available public feeders: {sorted(PUBLIC_BUILTINS)}"
            )
    elif kind == "inline":
        if case.network.inline is None:  # Defensive; Case validation already checks this.
            raise PublicBoundaryError("inline network is missing its typed network payload")
    else:
        raise PublicBoundaryError(
            "CEPT Public accepts inline Cases and the bundled IEEE13 feeder only; "
            "arbitrary dss_file paths are not yet part of the public release."
        )
    return {
        "study_type": study_type,
        "engine": "opendss",
        "network_kind": kind,
        "network_name": str(case.network.name).lower() if kind == "builtin" else None,
        "claim": PUBLIC_CLAIM,
    }


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail}


def _result_checks(case: Case, result: StudyResult) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    checks.append(
        _check(
            "case_fingerprint",
            result.case_fingerprint == case.fingerprint(),
            "StudyResult.case_fingerprint equals Case.fingerprint().",
        )
    )
    checks.append(
        _check(
            "solver_identity",
            result.engine == "opendss" and bool(result.engine_version),
            "result identifies the OpenDSS solver and version.",
        )
    )
    if result.study_type != case.study.type:
        checks.append(
            _check(
                "study_identity",
                False,
                f"result.study_type={result.study_type!r} does not match Case study.type={case.study.type!r}.",
            )
        )
    else:
        checks.append(_check("study_identity", True, "result.study_type matches Case study.type."))

    if result.load_flow is not None:
        load_flow = result.load_flow
        values = [
            value for voltage in load_flow.bus_voltages for value in (voltage.v_pu, voltage.v_angle_deg)
        ]
        values.extend(value for flow in load_flow.branch_flows for value in (flow.p_kw, flow.q_kvar))
        checks.append(
            _check(
                "load_flow_convergence",
                load_flow.converged is True,
                "OpenDSS reported a converged load-flow solution.",
            )
        )
        checks.append(
            _check(
                "load_flow_quantities",
                bool(load_flow.bus_voltages) and all(_finite(value) for value in values),
                "solver-returned bus and branch quantities are present and finite.",
            )
        )

    if result.hosting_capacity is not None:
        hosting = result.hosting_capacity
        checks.append(
            _check(
                "hosting_capacity_rows",
                bool(hosting.items)
                and all(
                    _finite(item.hc_kw)
                    and item.hc_kw >= 0
                    and item.limit in {"overvoltage", "thermal", "maxed", "none"}
                    for item in hosting.items
                ),
                "hosting-capacity rows are finite, non-negative, and use declared limits.",
            )
        )

    if result.fault is not None:
        fault = result.fault
        checks.append(
            _check(
                "fault_result",
                fault.fault_type in {"3ph", "slg", "ll", "llg"}
                and _finite(fault.total_fault_current_a)
                and float(fault.total_fault_current_a or 0) > 0
                and bool(fault.currents)
                and all(
                    _finite(current.i_amp) and _finite(current.i_angle_deg) for current in fault.currents
                ),
                "fault type and phase currents are solver-returned, finite, and positive.",
            )
        )
    return checks


def _verification_record(case: Case, result: StudyResult) -> dict[str, Any]:
    checks = _result_checks(case, result)
    passed = all(item["passed"] for item in checks)
    return {
        "schema": "cept-public-verification-v1",
        "passed": passed,
        "status": "PASS" if passed else "BLOCKED",
        "claim": PUBLIC_CLAIM if passed else "BLOCKED",
        "claim_boundary": (
            "solver-backed workflow, convergence, finite result quantities, and identity only; "
            "not project validation or field-evidence acceptance"
        ),
        "case_fingerprint": case.fingerprint(),
        "public_version": _public_version(),
        "study_type": case.study.type,
        "engine": result.engine,
        "engine_version": result.engine_version,
        "checks": checks,
    }


def _public_version() -> str:
    """Read the package version without importing any engine adapter."""

    try:
        return importlib.metadata.version("cept-power-studio")
    except importlib.metadata.PackageNotFoundError:
        import cept

        return str(cept.__version__)


def public_version() -> str:
    """Return the installed public distribution version used by receipts."""

    return _public_version()


def _json_dump(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _prepare_run_dir(path: Path, *, force: bool) -> Path:
    run_dir = path.resolve()
    if run_dir.exists() and any(run_dir.iterdir()) and not force:
        raise FileExistsError(f"output directory is not empty: {run_dir}; use force=True explicitly")
    run_dir.mkdir(parents=True, exist_ok=True)
    if force:
        for name in _OWNED_ARTIFACTS:
            artifact = run_dir / name
            if artifact.is_file():
                artifact.unlink()
    return run_dir


def _write_run_artifacts(
    run_dir: Path,
    case: Case,
    result: StudyResult,
    verification: dict[str, Any],
    execution_plan: dict[str, Any],
    attempt_id: str,
) -> None:
    execution_key = execution_plan.get("plan_fingerprint")
    _json_dump(run_dir / "case.json", case.model_dump(mode="json"))
    _json_dump(run_dir / "results.json", result.model_dump(mode="json"))
    _json_dump(
        run_dir / "attempt.json",
        {
            "schema": "cept-run-attempt-v1",
            "attempt_id": attempt_id,
            "execution_key": execution_key,
            "plan_fingerprint": execution_key,
            "case_fingerprint": case.fingerprint(),
            "engine": result.engine,
            "study_type": result.study_type,
            "solver": execution_plan.get("solver"),
            "run_dir": str(run_dir),
        },
    )
    assessment = assessment_id(
        attempt_id,
        str(execution_key),
        _sha256(run_dir / "results.json"),
        verification,
    )
    verification = dict(verification)
    verification.update(
        {
            "attempt_id": attempt_id,
            "execution_key": execution_key,
            "assessment_id": assessment,
        }
    )
    manifest = {
        "schema": "cept-public-run-manifest-v1",
        "edition": "public",
        "public_version": public_version(),
        "claim": PUBLIC_CLAIM if verification["passed"] else "BLOCKED",
        "case_fingerprint": case.fingerprint(),
        "attempt_id": attempt_id,
        "execution_key": execution_key,
        "assessment_id": assessment,
        "execution_plan": execution_plan,
        "study_type": result.study_type,
        "engine": result.engine,
        "engine_version": result.engine_version,
        "calculation_method": result.calculation_method,
        "artifacts": [
            "attempt.json",
            "case.json",
            "results.json",
            "manifest.json",
            "validation_report.json",
            "public-verification.json",
        ],
    }
    _json_dump(run_dir / "manifest.json", manifest)
    validation = {
        "schema": "cept-public-validation-v1",
        "passed": verification["passed"],
        "status": verification["status"],
        "case_fingerprint": case.fingerprint(),
        "attempt_id": attempt_id,
        "execution_key": execution_key,
        "assessment_id": assessment,
        "study_type": result.study_type,
        "engine": result.engine,
        "checks": verification["checks"],
        "scope": verification["claim_boundary"],
    }
    _json_dump(run_dir / "validation_report.json", validation)
    artifact_hashes = {
        name: _sha256(run_dir / name)
        for name in ("attempt.json", "case.json", "results.json", "manifest.json", "validation_report.json")
    }
    receipt = dict(verification)
    receipt["run_dir"] = str(run_dir)
    receipt["artifact_sha256"] = artifact_hashes
    receipt["artifact_set_digest"] = artifact_set_digest(artifact_hashes)
    _json_dump(run_dir / "public-verification.json", receipt)


def run_study(
    case: Case | Mapping[str, Any],
    *,
    out: str | Path | None = None,
    force: bool = False,
    solver: str = "native",
) -> PublicRun:
    """Run one supported public Case through the canonical OpenDSS adapter.

    ``out`` is optional for interactive teaching, but file-backed runs are
    recommended because they preserve the evidence needed to reproduce and
    verify the workflow later.
    """

    typed_case = case if isinstance(case, Case) else Case.model_validate(case)
    validate_public_case(typed_case)
    if solver != "native":
        raise PublicBoundaryError("CEPT Public exposes the native OpenDSS solver only.")

    readiness = readiness_for_case(typed_case)
    if readiness.status != "PASS" or readiness.prepared is None:
        reason = readiness.gaps[0]["reason"] if readiness.gaps else "Case admission was blocked."
        raise PublicBoundaryError(str(reason))
    software_identity = {"edition": "public", "public_version": public_version()}
    plan = build_execution_plan(
        readiness.prepared,
        solver=solver,
        software_identity=software_identity,
    )
    plan_summary = dict(execution_plan_summary(plan))
    plan_summary["claim_cap"] = PUBLIC_CLAIM
    plan_summary["research_status"] = PUBLIC_CLAIM

    run_dir = _prepare_run_dir(Path(out), force=force) if out is not None else None
    if run_dir is not None:
        try:
            execution_module = importlib.import_module("cept.application.execution")
        except ModuleNotFoundError as exc:
            if exc.name != "cept.application.execution":
                raise
            # The positive public export intentionally omits the private
            # artifact service. Preserve the public file-backed contract with
            # its existing bounded writer in that staged edition.
            result, _adapter = execute_run(plan)
            attempt_id = new_attempt_id()
        else:
            request_type = execution_module.StudyExecutionRequest
            execution_module.execute_study_to_artifacts(
                request_type(
                    case=typed_case,
                    run_dir=run_dir,
                    export=False,
                    include_show_commands=False,
                    force=force,
                    strict=False,
                    argv=["cept", "study", "run", "--public"],
                    solver=solver,
                    experiment_context={"claim": PUBLIC_CLAIM},
                    software_identity=software_identity,
                    console_output=False,
                )
            )
            result = StudyResult.model_validate(_read_json(run_dir / "results.json"))
            plan_summary = dict(_read_json(run_dir / "execution-plan.json"))
            plan_summary["claim_cap"] = PUBLIC_CLAIM
            plan_summary["research_status"] = PUBLIC_CLAIM
            attempt_payload = _read_json(run_dir / "attempt.json")
            attempt_id = str(attempt_payload["attempt_id"])
    else:
        result, _adapter = execute_run(plan)
        attempt_id = new_attempt_id()
    verification = _verification_record(typed_case, result)
    verification.update(
        {
            "attempt_id": attempt_id,
            "execution_key": plan_summary.get("plan_fingerprint", plan.plan_fingerprint),
        }
    )
    if run_dir is not None:
        _write_run_artifacts(run_dir, typed_case, result, verification, plan_summary, attempt_id)
    return PublicRun(typed_case, result, verification, run_dir, plan_summary)


def verify_study(run_dir: str | Path) -> dict[str, Any]:
    """Re-validate a public run from its persisted Case and result artifacts.

    Read-only: the run directory is never modified.  The returned receipt is
    freshly computed (artifact hashes included); only ``study run`` writes
    ``public-verification.json``.
    """

    path = Path(run_dir).resolve()
    case_payload = _read_json(path / "case.json")
    result_payload = _read_json(path / "results.json")
    attempt = _read_json(path / "attempt.json")
    manifest = _read_json(path / "manifest.json")
    validation = _read_json(path / "validation_report.json")
    stored_receipt = _read_json(path / "public-verification.json")
    if not isinstance(manifest, Mapping) or not isinstance(validation, Mapping):
        raise ValueError("public run is missing a valid manifest.json or validation_report.json")
    if not isinstance(stored_receipt, Mapping):
        raise ValueError("public run is missing a valid public-verification.json")
    case = Case.model_validate(case_payload)
    validate_public_case(case)
    result = StudyResult.model_validate(result_payload)
    verification = _verification_record(case, result)
    if manifest.get("schema") != "cept-public-run-manifest-v1":
        verification["checks"].append(
            _check("manifest_schema", False, "manifest.json has an unknown public-run schema.")
        )
    else:
        verification["checks"].append(_check("manifest_schema", True, "manifest schema is supported."))
    if manifest.get("case_fingerprint") != case.fingerprint():
        verification["checks"].append(
            _check("manifest_identity", False, "manifest.json case_fingerprint does not match case.json.")
        )
    else:
        verification["checks"].append(
            _check("manifest_identity", True, "manifest identity matches case.json.")
        )
    identity_bound = (
        isinstance(attempt, Mapping)
        and all(
            attempt.get(field) == payload.get(field)
            for field in ("attempt_id", "execution_key", "case_fingerprint")
            for payload in (manifest, validation, stored_receipt)
        )
        and attempt.get("case_fingerprint") == case.fingerprint()
        and validation.get("assessment_id") == manifest.get("assessment_id") == stored_receipt.get("assessment_id")
    )
    metadata_checks = (
        (
            "manifest_study_identity",
            manifest.get("study_type") == result.study_type,
            "manifest.json study_type matches results.json.",
        ),
        (
            "manifest_engine_identity",
            manifest.get("engine") == result.engine == "opendss",
            "manifest.json and results.json identify OpenDSS.",
        ),
        (
            "validation_identity",
            validation.get("case_fingerprint") == case.fingerprint()
            and validation.get("study_type") == result.study_type
            and validation.get("engine") == result.engine,
            "validation_report.json identity matches the Case and result.",
        ),
        (
            "stored_receipt_identity",
            stored_receipt.get("case_fingerprint") == case.fingerprint()
            and stored_receipt.get("study_type") == result.study_type
            and stored_receipt.get("engine") == result.engine,
            "public-verification.json identity matches the Case and result.",
        ),
        (
            "attempt_identity",
            identity_bound if isinstance(attempt, Mapping) else not (manifest.get("attempt_id") or validation.get("attempt_id")),
            "attempt.json binds the invocation, execution plan, Case, and assessment across public artifacts.",
        ),
    )
    for name, passed, detail in metadata_checks:
        verification["checks"].append(_check(name, passed, detail))
    if validation.get("passed") is not True:
        verification["checks"].append(
            _check(
                "validation_receipt", False, "validation_report.json does not explicitly report passed=true."
            )
        )
    else:
        verification["checks"].append(
            _check("validation_receipt", True, "validation_report.json reports passed=true.")
        )
    stored_hashes = stored_receipt.get("artifact_sha256")
    expected_files = (
        ("attempt.json", "case.json", "results.json", "manifest.json", "validation_report.json")
        if isinstance(attempt, Mapping)
        else ("case.json", "results.json", "manifest.json", "validation_report.json")
    )
    hashes_passed = isinstance(stored_hashes, Mapping) and all(
        isinstance(stored_hashes.get(name), str) and stored_hashes[name] == _sha256(path / name)
        for name in expected_files
    )
    declared_set_digest = stored_receipt.get("artifact_set_digest")
    if isinstance(declared_set_digest, str) and isinstance(stored_hashes, Mapping):
        hashes_passed = hashes_passed and artifact_set_digest(dict(stored_hashes)) == declared_set_digest
    verification["checks"].append(
        _check(
            "artifact_integrity",
            hashes_passed,
            "stored artifact SHA-256 values match the persisted public receipt.",
        )
    )
    verification["passed"] = all(item["passed"] for item in verification["checks"])
    verification["status"] = "PASS" if verification["passed"] else "BLOCKED"
    verification["claim"] = PUBLIC_CLAIM if verification["passed"] else "BLOCKED"
    if isinstance(attempt, Mapping):
        verification.update(
            {
                "attempt_id": attempt.get("attempt_id"),
                "execution_key": attempt.get("execution_key"),
                "assessment_id": stored_receipt.get("assessment_id"),
            }
        )
    verification["artifact_sha256"] = {
        name: _sha256(path / name)
        for name in (
            "attempt.json",
            "case.json",
            "results.json",
            "manifest.json",
            "validation_report.json",
        )
        if (path / name).is_file()
    }
    if isinstance(stored_receipt.get("artifact_set_digest"), str):
        verification["artifact_set_digest"] = stored_receipt["artifact_set_digest"]
    verification["run_dir"] = str(path)
    return verification


def demo_case(study: str = "load_flow", *, network: str = "ieee13") -> Case:
    """Create a supported, clearly-labelled public demonstration Case."""

    normalized = study.replace("-", "_").lower()
    if normalized == "load_flow":
        spec: dict[str, Any] = {"type": "load_flow"}
    elif normalized == "unbalanced_load_flow":
        spec = {"type": "unbalanced_load_flow"}
    elif normalized == "hosting_capacity":
        spec = {
            "type": "hosting_capacity",
            "options": {"criterion": "overvoltage", "v_max": 1.05, "max_kw": 5000.0, "phases": 3},
        }
    elif normalized == "fault":
        spec = {
            "type": "fault",
            "options": {"bus": "675", "type": "slg", "phase": 1, "rf": 0.001, "run_faultstudy": True},
        }
    else:
        raise PublicBoundaryError(
            f"unsupported public demo study {study!r}; choose load-flow, unbalanced-load-flow, "
            "hosting-capacity, or fault"
        )
    return Case.model_validate(
        {
            "meta": {
                "name": f"public_{network}_{normalized}",
                "description": f"CEPT Public {normalized} demonstration on {network}.",
                "mode": "demonstrator",
            },
            "network": {"kind": "builtin", "name": network, "frequency_hz": 60},
            "study": spec,
        }
    )


__all__ = [
    "PUBLIC_BUILTINS",
    "PUBLIC_CLAIM",
    "PUBLIC_STUDIES",
    "PublicBoundaryError",
    "PublicRun",
    "demo_case",
    "load_case",
    "public_capabilities",
    "public_version",
    "run_study",
    "validate_public_case",
    "verify_study",
]
