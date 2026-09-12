"""Immutable preparation and execution planning for CEPT studies.

The public :func:`cept.studies.run_case` contract stays unchanged.  This module
makes the policy boundary behind it explicit:

``Case document -> PreparedCase -> ExecutionPlan -> EngineAdapter``.

``PreparedCase`` stores canonical serialized Case data rather than a mutable
Pydantic object, so callers cannot change the engineering input after capability
resolution.  ``ExecutionPlan`` additionally binds source/model content, CEPT
software/environment identity, solver lane, and the observed engine runtime
before a solver adapter is constructed.
"""

from __future__ import annotations

import hashlib
import json
import importlib
from dataclasses import dataclass
from typing import Any, Iterable, Optional, cast

from cept.adapters import default_probe
from cept.capability import resolve_study_capability
from cept.model_package_identity import inspect_model_packages, model_package_identity_json
from cept.ports.capabilities import EvidenceCapability, ExecutionCapability, ResolvedStudyCapability
from cept.ports.engine import EngineAdapter
from cept.ports.options import RunOptions, Solver, as_solver
from cept.schema.case import Case
from cept.schema.result import StudyResult


@dataclass(frozen=True)
class PreparedCase:
    """Capability-resolved, immutable snapshot of one solver-facing Case."""

    case_json: str
    case_fingerprint: str
    study_type: str
    engine: str
    representation_family: str
    capability_json: str
    assumption_paths: tuple[str, ...]
    powerfactory_method: str | None
    frequency_hz: float
    event_json: str
    requested_signal_json: str
    execution_status: str
    execution_reason: str
    evidence_claim_cap: str | None
    evidence_research_status: str | None
    validated_runtime_version: str | None
    source_hashes: tuple[str, ...]

    @property
    def execution_capability(self) -> ExecutionCapability:
        capability = json.loads(self.capability_json)
        return cast(ExecutionCapability, capability["execution"])

    @property
    def evidence_capability(self) -> EvidenceCapability:
        capability = json.loads(self.capability_json)
        return cast(EvidenceCapability, capability["evidence"])

    def materialize_case(self) -> Case:
        """Return an isolated Case and fail if frozen identity no longer round-trips."""
        case = Case.model_validate_json(self.case_json)
        fingerprint = case.fingerprint()
        if fingerprint != self.case_fingerprint:
            raise RuntimeError(
                "prepared Case identity drifted during materialization: "
                f"expected {self.case_fingerprint}, got {fingerprint}"
            )
        return case


@dataclass(frozen=True)
class ExecutionPlan:
    """Immutable solver invocation and result-reuse identity contract."""

    prepared: PreparedCase
    options: RunOptions
    plan_fingerprint: str
    solver_runtime_version: str | None
    model_package_identity_json: str
    software_identity_json: str
    software_identity_fingerprint: str

    @property
    def engine(self) -> str:
        return self.prepared.engine

    @property
    def study_type(self) -> str:
        return self.prepared.study_type

    @property
    def case_fingerprint(self) -> str:
        return self.prepared.case_fingerprint

    @property
    def evidence_capability(self) -> EvidenceCapability:
        return self.prepared.evidence_capability

    @property
    def execution_capability(self) -> ExecutionCapability:
        return self.prepared.execution_capability


@dataclass(frozen=True)
class ResultCacheDecision:
    """Fail-closed result-reuse decision derived from one ExecutionPlan."""

    eligible: bool
    key: str | None
    reason: str
    runtime_version: str | None

    @property
    def execution_disposition(self) -> str:
        return "reuse-eligible" if self.eligible else "fresh-solve-required"

    @property
    def evidence_disposition(self) -> str:
        return "hash-verified-cache-required" if self.eligible else "fresh-evidence-required"

    def as_dict(self) -> dict[str, object]:
        return {
            "eligible": self.eligible,
            "key": self.key,
            "reason": self.reason,
            "runtime_version": self.runtime_version,
            "execution_disposition": self.execution_disposition,
            "evidence_disposition": self.evidence_disposition,
        }


def _canonical_case_json(case: Case) -> str:
    return json.dumps(
        case.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _canonical_capability_json(capability: ResolvedStudyCapability) -> str:
    return json.dumps(capability, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _apply_legacy_preparation_policy(case: Case) -> None:
    """Apply execution-only legacy policy without mutating the user Case document."""
    selected = case.provenance.selected_study_case if case.provenance else None
    if selected and case.study.type == "fault" and "method c" in selected.name.lower():
        if case.study.options.get("powerfactory_method") is None:
            case.study.options["powerfactory_method"] = "method_c"
        for study in case.studies:
            if study.study.type == "fault" and study.study.options.get("powerfactory_method") is None:
                study.study.options["powerfactory_method"] = "method_c"


def prepare_case(case: Case) -> PreparedCase:
    """Resolve all pre-solver capability policy against an isolated Case snapshot."""
    effective = case.model_copy(deep=True)
    _apply_legacy_preparation_policy(effective)
    capability = resolve_study_capability(effective)
    execution = capability["execution"]
    evidence = capability["evidence"]
    engine = str(capability["engine"])
    study_type = str(capability["study_type"])
    representation = str(execution.get("representation_family") or effective.network.kind)

    if str(evidence.get("engine") or engine) != engine:
        raise RuntimeError("prepared evidence capability engine drifted after aggregate resolution")

    method = effective.study.options.get("powerfactory_method")
    event_payload: list[dict[str, object]] = []
    signal_payload: list[dict[str, object]] = []
    if effective.dynamics is not None:
        event_payload = [item.model_dump(mode="json") for item in effective.dynamics.events]
        signal_payload = [item.model_dump(mode="json") for item in effective.dynamics.monitor_signals]
    elif effective.emt is not None:
        event_payload = [dict(item) for item in effective.emt.events]
        signal_payload = [
            {
                "channel": channel,
                "unit": effective.emt.channel_units.get(channel, ""),
                "source": effective.emt.channel_sources.get(channel, ""),
            }
            for channel in effective.emt.channels
        ]
    validated_runtime = evidence.get("validated_runtime_version")
    source_hashes: tuple[str, ...] = ()
    if effective.provenance is not None:
        source_hashes = (f"source-manifest:{effective.provenance.source_manifest_sha256.lower()}",)
    return PreparedCase(
        case_json=_canonical_case_json(effective),
        case_fingerprint=effective.fingerprint(),
        study_type=study_type,
        engine=engine,
        representation_family=representation,
        capability_json=_canonical_capability_json(capability),
        assumption_paths=tuple(sorted(item.path for item in effective.assumptions)),
        powerfactory_method=str(method) if method is not None else None,
        frequency_hz=float(effective.network.frequency_hz),
        event_json=_canonical_json(event_payload),
        requested_signal_json=_canonical_json(signal_payload),
        execution_status=str(execution.get("status") or ""),
        execution_reason=str(execution.get("reason") or ""),
        evidence_claim_cap=(str(evidence.get("claim_cap")) if evidence.get("claim_cap") is not None else None),
        evidence_research_status=(
            str(evidence.get("research_status")) if evidence.get("research_status") is not None else None
        ),
        validated_runtime_version=(str(validated_runtime) if validated_runtime is not None else None),
        source_hashes=source_hashes,
    )


def _normalized_optional_tuple(values: Optional[Iterable[str]]) -> tuple[str, ...] | None:
    return tuple(values) if values is not None else None


def build_execution_plan(
    prepared: PreparedCase,
    *,
    extra_commands: Optional[Iterable[str]] = None,
    solver: str = "native",
    preserve_powerfactory: bool = False,
    model_package_paths: Optional[Iterable[str]] = None,
    software_identity: dict[str, Any] | None = None,
    solver_runtime_version: str | None = None,
) -> ExecutionPlan:
    """Bind engineering, software/environment, and solver identity pre-solver."""
    try:
        solver_lane = as_solver(solver)
    except ValueError:
        raise ValueError(f"Unknown solver lane '{solver}'.") from None

    if solver_lane is not Solver.NATIVE and prepared.engine != "opendss":
        raise ValueError(
            f"solver='{solver}' is only available with engine='opendss', not '{prepared.engine}'."
        )
    if solver_lane is not Solver.NATIVE and prepared.study_type not in {
        "load_flow",
        "unbalanced_load_flow",
    }:
        raise ValueError(
            f"solver='{solver}' is only available for load_flow, not study.type='{prepared.study_type}'."
        )

    commands = _normalized_optional_tuple(extra_commands)
    packages = _normalized_optional_tuple(model_package_paths)
    options = RunOptions(
        extra_commands=commands,
        solver=solver_lane.value,
        preserve_project=preserve_powerfactory,
        model_package_paths=packages,
    )
    package_identities = inspect_model_packages(packages or ())
    package_json = model_package_identity_json(package_identities)
    if software_identity is not None:
        software_payload = dict(software_identity)
    else:
        # Keep the research identity implementation optional for the public
        # package; the production checkout still resolves the same function.
        identity_module = importlib.import_module("cept.research.source_identity")
        software_payload = dict(identity_module.source_identity())
    software_json = _canonical_json(software_payload)
    software_fingerprint = f"cept-software-{_sha256_text(software_json)[:20]}"
    observed_runtime = solver_runtime_version
    if observed_runtime is None:
        observed_runtime = default_probe.engine_version(prepared.engine)
    observed_runtime = str(observed_runtime).strip() if observed_runtime else None

    identity_payload = {
        "case_fingerprint": prepared.case_fingerprint,
        "study_type": prepared.study_type,
        "engine": prepared.engine,
        "representation_family": prepared.representation_family,
        "frequency_hz": prepared.frequency_hz,
        "events": json.loads(prepared.event_json),
        "requested_signals": json.loads(prepared.requested_signal_json),
        "execution_status": prepared.execution_status,
        "execution_reason": prepared.execution_reason,
        "evidence_claim_cap": prepared.evidence_claim_cap,
        "evidence_research_status": prepared.evidence_research_status,
        "validated_runtime_version": prepared.validated_runtime_version,
        "solver_runtime_version": observed_runtime,
        "solver": solver_lane.value,
        "extra_commands": list(commands or ()),
        "preserve_project": bool(preserve_powerfactory),
        "source_hashes": list(prepared.source_hashes),
        "model_packages": json.loads(package_json),
        "software_identity_fingerprint": software_fingerprint,
    }
    digest = _sha256_text(_canonical_json(identity_payload))[:16]
    return ExecutionPlan(
        prepared=prepared,
        options=options,
        plan_fingerprint=f"cept-plan-{digest}",
        solver_runtime_version=observed_runtime,
        model_package_identity_json=package_json,
        software_identity_json=software_json,
        software_identity_fingerprint=software_fingerprint,
    )


def prepare_run(
    case: Case,
    *,
    extra_commands: Optional[Iterable[str]] = None,
    solver: str = "native",
    preserve_powerfactory: bool = False,
    model_package_paths: Optional[Iterable[str]] = None,
    software_identity: dict[str, Any] | None = None,
    solver_runtime_version: str | None = None,
) -> ExecutionPlan:
    """Convenience composition for direct callers of the single-Case API."""
    prepared = prepare_case(case)
    return build_execution_plan(
        prepared,
        extra_commands=extra_commands,
        solver=solver,
        preserve_powerfactory=preserve_powerfactory,
        model_package_paths=model_package_paths,
        software_identity=software_identity,
        solver_runtime_version=solver_runtime_version,
    )


def _software_identity_cache_reason(plan: ExecutionPlan) -> str | None:
    try:
        identity = json.loads(plan.software_identity_json)
    except (TypeError, ValueError):
        return "software_identity_unreadable"
    source_tree = str(identity.get("source_tree_sha256") or "")
    if len(source_tree) != 64:
        return "source_tree_identity_unavailable"
    environment = identity.get("dependency_environment")
    if not isinstance(environment, dict) or environment.get("environment_mode") != "LOCKED":
        return "dependency_environment_not_locked"
    if not environment.get("environment_marker_matches"):
        return "dependency_environment_not_attested"
    return None


def result_cache_decision(
    plan: ExecutionPlan,
    adapter: EngineAdapter | None = None,
    *,
    probe_runtime: bool = False,
) -> ResultCacheDecision:
    """Return the exact reuse decision; every uncertainty requires a fresh solve.

    ``probe_runtime`` is used immediately before a cache restore.  It closes the
    plan-to-reuse race by proving the installed runtime still matches the one
    frozen into the plan.  A post-solve adapter is checked against that same
    frozen runtime before a fresh result may be stored.
    """
    actual_engine = plan.engine
    runtime = plan.solver_runtime_version
    if adapter is not None:
        actual_engine = str(getattr(adapter, "engine", "") or "")
        raw_runtime = str(getattr(adapter, "version", "") or "").strip()
        runtime = raw_runtime or None
        if actual_engine != plan.engine:
            return ResultCacheDecision(False, None, "engine_identity_mismatch", runtime)
    elif probe_runtime:
        probed = default_probe.engine_version(plan.engine)
        runtime = str(probed).strip() if probed else None

    if runtime != plan.solver_runtime_version:
        return ResultCacheDecision(False, None, "solver_runtime_drift_since_plan", runtime)

    validated = str(plan.prepared.validated_runtime_version or "").strip()
    if not validated:
        return ResultCacheDecision(False, None, "validated_runtime_version_missing", runtime)
    if not runtime:
        return ResultCacheDecision(False, None, "solver_runtime_unavailable", None)
    if runtime != validated:
        return ResultCacheDecision(False, None, "solver_runtime_not_validated", runtime)
    if plan.options.preserve_project:
        return ResultCacheDecision(False, None, "live_project_artifacts_require_fresh_solver", runtime)
    identity_reason = _software_identity_cache_reason(plan)
    if identity_reason is not None:
        return ResultCacheDecision(False, None, identity_reason, runtime)

    payload = {
        "plan_fingerprint": plan.plan_fingerprint,
        "engine": plan.engine,
        "runtime_version": runtime,
        "solver": plan.options.solver,
        "source_hashes": list(plan.prepared.source_hashes),
        "model_packages": json.loads(plan.model_package_identity_json),
        "software_identity_fingerprint": plan.software_identity_fingerprint,
    }
    digest = _sha256_text(_canonical_json(payload))[:20]
    return ResultCacheDecision(True, f"cept-result-cache-{digest}", "eligible", runtime)


def result_cache_key(plan: ExecutionPlan, adapter: EngineAdapter) -> str | None:
    """Compatibility helper returning only the qualified cache key."""
    return result_cache_decision(plan, adapter).key


def execution_plan_summary(plan: ExecutionPlan) -> dict[str, object]:
    """Small pre-solver view for guided UX; contains no live adapter state."""
    package_rows = json.loads(plan.model_package_identity_json)
    return {
        "schema": "cept-execution-plan-summary-v1",
        "case_fingerprint": plan.case_fingerprint,
        "plan_fingerprint": plan.plan_fingerprint,
        "study_type": plan.study_type,
        "engine": plan.engine,
        "representation_family": plan.prepared.representation_family,
        "frequency_hz": plan.prepared.frequency_hz,
        "events": json.loads(plan.prepared.event_json),
        "requested_signals": json.loads(plan.prepared.requested_signal_json),
        "solver": plan.options.solver,
        "solver_runtime_version": plan.solver_runtime_version,
        "assumption_paths": list(plan.prepared.assumption_paths),
        "powerfactory_method": plan.prepared.powerfactory_method,
        "execution_supported": plan.prepared.execution_status == "supported",
        "execution_status": plan.prepared.execution_status,
        "execution_reason": plan.prepared.execution_reason,
        "research_status": plan.prepared.evidence_research_status,
        "claim_cap": plan.prepared.evidence_claim_cap,
        "validated_runtime_version": plan.prepared.validated_runtime_version,
        "source_hashes": list(plan.prepared.source_hashes),
        "model_package_content": package_rows,
        "software_identity_fingerprint": plan.software_identity_fingerprint,
        "cache": result_cache_decision(plan).as_dict(),
    }


def execute_run(plan: ExecutionPlan) -> tuple[StudyResult, EngineAdapter]:

    """Construct exactly one adapter at the execution boundary and run the frozen Case."""
    # The lazy ``cept.adapters`` facade keeps the public package OpenDSS-only;
    # the full ``cept.adapters.registry`` remains the Pro/CLI wiring boundary.
    from cept.adapters import adapter_for
    case = plan.prepared.materialize_case()
    adapter = adapter_for(plan.engine)
    result = adapter.run(case, plan.options)
    return result, adapter


__all__ = [
    "ExecutionPlan",
    "PreparedCase",
    "ResultCacheDecision",
    "build_execution_plan",
    "execution_plan_summary",
    "execute_run",
    "prepare_case",
    "prepare_run",
    "result_cache_decision",
    "result_cache_key",
]
