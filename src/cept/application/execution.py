"""Shared application service for solver runs and evidence artifacts.

CLI, report review, and public callers use this boundary instead of importing
private command orchestration.  The service preserves the existing run artifact
contract; presentation adapters decide whether console text is emitted.
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from cept.application.artifacts import (
    _ReportEdits,
    _inject_opendss_sld_status,
    _inject_powerfactory_sld,
    _inject_report_context,
    _inject_sld_collision,
    _inject_sld_fidelity,
    _inject_verified_report_payload,
    _render_report_html,
    _validation_summary,
    _write_command_log,
    _write_failure,
    _write_identity_map,
    _write_manifest,
    _write_sld_collision,
    _write_sld_fidelity,
    _write_sld_layout,
    _write_validation,
    _write_validation_record,
)
from cept.application.run_identity import new_attempt_id
from cept.application.staging import _stage_model_packages
from cept.application.trust_summary import inject_trust_summary
from cept.artifacts import portable_run_dir
from cept.application.exit_codes import EXIT_OK, EXIT_POLICY
from cept.export import export_dss
from cept.application.software_identity import source_identity, write_run_source_identity
from cept.schema import Case
from cept.schema.result import StudyResult
from cept.studies.planning import (
    build_execution_plan,
    execute_run,
    execution_plan_summary,
    prepare_case,
    result_cache_decision,
)
from cept.studies.result_cache import restore_solver_evidence, store_solver_evidence
from cept.util import read_json, write_json as _write_json

_logger = logging.getLogger(__name__)


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip().lower()).strip("-")
    return slug or "study"


@dataclass(frozen=True)
class StudyExecutionRequest:
    """Explicit application inputs for one immutable study attempt."""

    case: Case
    run_dir: Path
    export: bool
    include_show_commands: bool
    force: bool
    strict: bool
    argv: list[str]
    extra_commands: list[str] | None = None
    solver: str = "native"
    experiment_context: dict[str, Any] | None = None
    console_output: bool = True
    software_identity: dict[str, Any] | None = None


@dataclass(frozen=True)
class StudyExecutionOutcome:
    """Stable result of the shared run-to-artifacts service."""

    exit_code: int
    run_dir: Path
    report_path: Path | None
    claim: str | None
    execution_key: str | None


def _bind_research_support(run_dir: Path, capability: dict[str, Any]) -> None:
    support = {
        "research_status": capability.get("research_status"),
        "research_benchmark": capability.get("research_benchmark"),
        "validated_runtime_version": capability.get("validated_runtime_version"),
        "validated_platforms": capability.get("validated_platforms", []),
        "last_verified_release": capability.get("last_verified_release"),
    }
    manifest_path = run_dir / "manifest.json"
    if manifest_path.is_file():
        manifest = read_json(manifest_path)
        manifest.update(support)
        _write_json(manifest_path, manifest)
    record_path = run_dir / "validation-record.json"
    if record_path.is_file():
        record = read_json(record_path)
        record.update(support)
        _write_json(record_path, record)


def execute_study_to_artifacts(request: StudyExecutionRequest) -> StudyExecutionOutcome:

    case = request.case
    run_dir = request.run_dir
    export = request.export
    include_show_commands = request.include_show_commands
    force = request.force
    strict = request.strict
    argv = request.argv
    extra_commands = request.extra_commands
    solver = request.solver
    experiment_context = request.experiment_context
    console_output = request.console_output
    software_identity = request.software_identity or source_identity()
    prepared = prepare_case(case)
    capability = prepared.evidence_capability
    _prepare_run_dir(run_dir, force=force)
    _write_json(run_dir / "source-identity.json", software_identity)
    _write_json(run_dir / "case.json", case.model_dump(mode="json"))
    _write_command_log(run_dir, argv)

    preserve_pf = case.engine == "powerfactory" or (
        case.study.type == "emt" and case.emt is not None and case.emt.engine == "powerfactory"
    )
    try:
        cache_hit = False
        cache_lookup_reason = "cache_ineligible"
        model_packages = list((experiment_context or {}).get("model_packages", []))
        model_package_hashes, staged_model_packages = _stage_model_packages(model_packages, run_dir)
        run_context = dict(experiment_context or {})
        run_context["model_package_hashes"] = model_package_hashes
        run_context["model_package_artifacts"] = staged_model_packages
        model_revision = run_context.get("model_revision")
        plan = build_execution_plan(
            prepared,
            extra_commands=extra_commands,
            solver=solver,
            preserve_powerfactory=preserve_pf,
            model_package_paths=model_packages,
            software_identity=software_identity,
        )
        attempt_id = new_attempt_id()
        _write_json(
            run_dir / "attempt.json",
            {
                "schema": "cept-run-attempt-v1",
                "attempt_id": attempt_id,
                "execution_key": plan.plan_fingerprint,
                "plan_fingerprint": plan.plan_fingerprint,
                "case_fingerprint": plan.case_fingerprint,
                "engine": plan.engine,
                "study_type": plan.study_type,
                "solver": plan.options.solver,
                "model_revision_id": (
                    model_revision.get("revision_id") if isinstance(model_revision, dict) else None
                ),
                "run_dir": ".",
                "created_at": datetime.now().isoformat(),
            },
        )
        if isinstance(model_revision, dict):
            _write_json(run_dir / "model-revision.json", model_revision)
        plan_summary = execution_plan_summary(plan)
        _write_json(run_dir / "execution-plan.json", plan_summary)
        planned_cache = result_cache_decision(plan, probe_runtime=True)
        if planned_cache.eligible and planned_cache.key is not None:
            cache_hit, cache_lookup_reason = restore_solver_evidence(
                planned_cache.key, plan.plan_fingerprint, run_dir
            )
        else:
            cache_lookup_reason = planned_cache.reason
        if cache_hit:
            result = StudyResult.model_validate(read_json(run_dir / "results.json"))
            _adapter = None
            actual_cache = planned_cache
        else:
            result, _adapter = execute_run(plan)
            actual_cache = result_cache_decision(plan, _adapter)
    except Exception as exc:
        _write_failure(run_dir, exc)
        raise
    if not cache_hit:
        _write_json(run_dir / "results.json", result.model_dump(mode="json"))
        _write_identity_map(run_dir, case, _adapter)
    _write_sld_layout(run_dir, case, result)
    _write_sld_collision(run_dir, result)
    _write_sld_fidelity(run_dir, case, result)
    report_path, report_html = _render_report_html(result, case, run_dir)
    # One rewrite for the whole run, and no read-back: the report is ~7.5 MB
    # and all injection points below only add bounded review sections/metadata.
    report_edits = _ReportEdits(report_path)
    report_edits.set(report_html)
    _inject_report_context(report_path, run_dir, edits=report_edits)
    inject_trust_summary(
        report_path,
        capability,
        run_claim=run_context.get("claim", "demonstrator"),
        edits=report_edits,
    )
    _inject_sld_collision(report_path, result, edits=report_edits)
    _inject_sld_fidelity(report_path, case, result, edits=report_edits)
    _inject_verified_report_payload(report_path, case, result, edits=report_edits)

    export_path = None
    if export:
        export_path = export_dss(
            case,
            stem=_slug(case.meta.name),
            out_dir=run_dir / "dss_export",
            include_show_commands=include_show_commands,
        )

    solver_package = None
    solver_package_error = None
    adapter_cleanup = {"status": "not-required", "error": None}
    native_sld = None
    native_sld_error = None
    if preserve_pf:
        from cept.adapters import PowerFactoryAdapter, export_verified_native_sld

        try:
            native_sld = export_verified_native_sld(
                _adapter,
                run_dir / "powerfactory" / "native_sld.png",
            )
        except Exception as exc:
            # Native means native. Do not synthesize a canonical/Matplotlib
            # replacement and then let the report or public build mistake it
            # for a PowerFactory vendor raster. The native lane remains absent
            # until the actual PF page passes the visual receipt gate.
            native_sld_error = str(exc)
            package_dir = run_dir / "powerfactory"
            package_dir.mkdir(parents=True, exist_ok=True)
            (package_dir / "native_sld_error.txt").write_text(
                native_sld_error + "\n",
                encoding="utf-8",
            )
        try:
            solver_package = _adapter.export_project(
                run_dir / "powerfactory" / f"{_slug(case.meta.name)}.pfd"
            )
        except Exception as exc:
            solver_package_error = str(exc)
            package_dir = run_dir / "powerfactory"
            package_dir.mkdir(parents=True, exist_ok=True)
            (package_dir / "package_error.txt").write_text(solver_package_error + "\n", encoding="utf-8")
        finally:
            if isinstance(_adapter, PowerFactoryAdapter):
                try:
                    _adapter.cleanup()
                    adapter_cleanup = {"status": "completed", "error": None}
                except Exception as exc:
                    adapter_cleanup = {"status": "failed", "error": str(exc)}
                    _logger.warning("PowerFactory cleanup after successful solve failed: %s", exc)

    # Native solver graphics are portable artifacts, not report dependencies.
    # The HTML report keeps the engine-neutral SLD so it renders everywhere;
    # users open the PowerFactory page from the exported .pfd in PowerFactory.
    if not preserve_pf and result.engine == "opendss" and export_path:
        native_sld_error = (
            "OpenDSSDirect headless mode does not expose a native plot image; "
            "run Plot Circuit from the bundled master script in OpenDSS-G"
        )
        _inject_opendss_sld_status(report_path, export_path, run_dir, edits=report_edits)
    elif preserve_pf:
        _inject_powerfactory_sld(
            report_path,
            Path(native_sld) if native_sld else None,
            run_dir,
            error=native_sld_error,
            edits=report_edits,
        )
    report_edits.flush()

    validation = _validation_summary(case, result)
    status = "pass" if validation["passed"] else "warning"
    _write_manifest(
        run_dir,
        case,
        result,
        report_path,
        export_path,
        status=status,
        validation=validation,
        solver_package=solver_package,
        solver_package_error=solver_package_error,
        capability=capability,
        experiment_context=run_context,
        attempt_id=attempt_id,
        execution_key=plan.plan_fingerprint,
        model_revision=model_revision,
        execution_plan=plan_summary,
        adapter_cleanup=adapter_cleanup,
    )
    _bind_research_support(run_dir, capability)
    assessment = _write_validation(
        run_dir,
        case,
        result,
        summary=validation,
        claim=run_context.get("claim", "demonstrator"),
        capability=capability,
        attempt_id=attempt_id,
        execution_key=plan.plan_fingerprint,
        model_revision=model_revision,
    )
    _write_validation_record(
        run_dir,
        case,
        result,
        validation=validation,
        claim=run_context.get("claim", "demonstrator"),
        model_package_hashes=run_context.get("model_package_hashes", {}),
        capability=capability,
        attempt_id=attempt_id,
        execution_key=plan.plan_fingerprint,
        assessment=assessment,
        model_revision=model_revision,
        execution_plan=plan_summary,
    )
    _bind_research_support(run_dir, capability)
    write_run_source_identity(run_dir, software_identity)

    cache_store_reason = "not_stored"
    if not cache_hit and actual_cache.eligible and actual_cache.key is not None:
        try:
            stored = store_solver_evidence(actual_cache.key, plan.plan_fingerprint, run_dir)
            cache_store_reason = str(stored.get("reason") or "cache_store_unknown")
        except Exception as exc:
            cache_store_reason = f"cache_store_failed:{type(exc).__name__}:{exc}"
    _write_json(
        run_dir / "result-cache-use.json",
        {
            "schema": "cept-result-cache-use-v1",
            "plan_fingerprint": plan.plan_fingerprint,
            "cache_key": actual_cache.key,
            "eligible": actual_cache.eligible,
            "eligibility_reason": actual_cache.reason,
            "lookup_reason": cache_lookup_reason,
            "cache_hit": cache_hit,
            "execution_disposition": "reused-verified-solver-evidence" if cache_hit else "fresh-solve",
            "evidence_disposition": actual_cache.evidence_disposition,
            "store_reason": cache_store_reason,
        },
    )

    claim = str(run_context.get("claim", "demonstrator"))
    claim_cap = capability.get("claim_cap")
    solver_verdict = "PASS" if validation["solver_valid"] else "BLOCKED"
    compliance_verdict = "PASS" if validation["engineering_compliance"] else "FINDINGS"
    if console_output:
        print(f"Run complete: {status.upper()}")
        print(f"Execution integrity: {status.upper()}")
        print(f"Study: {plan.study_type} | Engine: {plan.engine}")
        print(f"Attempt: {attempt_id}")
        print(f"Execution plan: {run_dir / 'execution-plan.json'}")
        print(f"Current claim: {claim}")
        if claim_cap:
            print(f"Claim ceiling: {claim_cap} (ceiling only; not current validation)")
        print(f"Solver validation: {solver_verdict}")
        print(f"Engineering compliance: {compliance_verdict}")
        print("Project validation: NOT CLAIMED")
        print(f"Output: {run_dir}")
        print(f"Report: {report_path}")
        print(f"Next: cept verify {run_dir}")
        if preserve_pf:
            if native_sld is not None:
                print(f"Native SLD: PASS ({native_sld})")
            else:
                print("Native SLD: BLOCKED")
                if native_sld_error:
                    print(f"Native SLD reason: {native_sld_error}")

    return StudyExecutionOutcome(
        exit_code=EXIT_POLICY if strict and status == "warning" else EXIT_OK,
        run_dir=run_dir,
        report_path=report_path,
        claim=claim,
        execution_key=plan.plan_fingerprint,
    )


def _prepare_run_dir(run_dir: Path, *, force: bool) -> None:
    run_dir = portable_run_dir(run_dir)
    if run_dir.exists() and any(run_dir.iterdir()) and not force:
        raise FileExistsError(
            f"Output directory is not empty: {run_dir}. Use --force to overwrite CEPT artifacts."
        )
    run_dir.mkdir(parents=True, exist_ok=True)
    if force:
        # --force owns only CEPT artifacts. Unrelated researcher files in the
        # directory are deliberately preserved.
        owned = (
            "case.json",
            "results.json",
            "attempt.json",
            "model-revision.json",
            "execution-plan.json",
            "identity-map.json",
            "report.html",
            "manifest.json",
            "failure.json",
            "validation_report.json",
            "validation_report.md",
            "validation-record.json",
            "source-identity.json",
            "result-cache-receipt.json",
            "result-cache-use.json",
            "draft-patch.json",
            ".cept-launch-token",
            "sld-layout.json",
            "sld-fidelity.json",
            "sld_collision.json",
            "public-verification.json",
            ".cept-launch-binding.json",
            "command_log.txt",
            "cross_engine_report.json",
            "cross_engine_report.md",
            "opendss",
            "powerfactory",
            "dss_export",
        )
        for name in owned:
            path = run_dir / name
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()

__all__ = [
    "StudyExecutionOutcome",
    "StudyExecutionRequest",
    "execute_study_to_artifacts",
]
