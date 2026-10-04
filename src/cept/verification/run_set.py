"""Fail-closed verification of an explicit CEPT run set.

This module is the single verdict owner for a run set. ``cept study verify``
reaches it through :mod:`cept.application.operations.verify`, so the CLI, a
recipe, and a notebook all read the same verdict instead of re-deriving one.

Why it lives here
-----------------
The work is verification of engineering evidence — read the run's manifest,
results, validation report, identity map, and receipt; bind their bytes
together; return pass or *incomplete* with the exact reasons. That is what
``cept.verification`` owns (comparison, parity, audits, benchmarks), and it is
not CLI wiring, so it does not belong under ``cept.cli``.

The exit-code contract once lived here as :mod:`cept.cli.exit_codes`. It is now
:mod:`cept.application.exit_codes`, which this module imports directly so
verification never reaches into ``cept.cli``.

The lazy ``cept.application.run_identity`` import inside
:func:`_bind_validation_record_hashes` keeps module import cheap and preserves
the original evaluation order.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from cept.application.exit_codes import EXIT_OK, EXIT_POLICY
from cept.application.run_identity import CONDUCT_MODE_FILENAME
from cept.semantics.identity import load_identity_map
from cept.util import sha256_file


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


_VERIFIED_REPORT_RE = re.compile(r"__ceptVerifiedReport=(\{.*?\});")


def _extract_verified_payload(html: str) -> dict[str, Any] | None:
    """Read the machine-readable verified-report payload out of report.html."""
    match = _VERIFIED_REPORT_RE.search(html)
    if not match:
        return None
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _bind_report_to_artifacts(
    run: Path,
    manifest: dict[str, Any] | None,
    reasons: list[str],
) -> dict[str, Any]:
    """Pillar C — assert report.html embeds the same fingerprint + SLD verdicts
    that the run's own artifacts record; otherwise the report has drifted from
    the verified data and must not be trusted as if it agreed."""
    report = run / "report.html"
    if not report.is_file():
        reasons.append("no report.html in run")
        return {}
    payload = _extract_verified_payload(
        report.read_text(encoding="utf-8", errors="replace")
    )
    if payload is None:
        reasons.append("report.html does not embed a cept-verified-report payload")
        return {}

    results = _read_json(run / "results.json")
    expected_fp = (
        manifest.get("case_fingerprint") if manifest else None
    ) or (results.get("case_fingerprint") if results else None)
    if expected_fp and payload.get("case_fingerprint") != expected_fp:
        reasons.append("report case_fingerprint does not match results/manifest")
    attempt = _read_json(run / "attempt.json")
    if attempt is not None:
        for field in ("attempt_id", "execution_key"):
            if payload.get(field) != attempt.get(field):
                reasons.append(f"report {field} does not match attempt.json")

    fidelity = _read_json(run / "sld-fidelity.json")
    if fidelity is not None:
        payload_fid = payload.get("sld_fidelity") or {}
        if payload_fid.get("passed") != fidelity.get("passed") or payload_fid.get(
            "verdict"
        ) != fidelity.get("verdict"):
            reasons.append("report SLD-fidelity verdict does not match sld-fidelity.json")

    collision = _read_json(run / "sld_collision.json")
    if collision is not None:
        payload_col = payload.get("sld_collision") or {}
        if payload_col.get("verdict") != collision.get("verdict"):
            reasons.append("report SLD-collision verdict does not match sld_collision.json")
    return payload


_VALIDATION_RECORD_HASHED_ARTIFACTS = (
    "case.json",
    "results.json",
    "report.html",
    "sld-layout.json",
    "model-revision.json",
    "execution-plan.json",
)


def _bind_validation_record_hashes(run: Path, reasons: list[str]) -> None:
    """Bind exact artifact bytes through validation-record.json when present.

    Runs written without a validation record skip this gate; a present record
    with a drifted or missing artifact fails closed.
    """
    record = _read_json(run / "validation-record.json")
    if record is None:
        return
    expected = record.get("artifact_hashes")
    if not isinstance(expected, dict):
        return
    for name in _VALIDATION_RECORD_HASHED_ARTIFACTS:
        digest = expected.get(name)
        if digest is None:
            continue
        path = run / name
        if not path.is_file():
            reasons.append(f"missing artifact listed in validation record: {name}")
            continue
        try:
            actual = sha256_file(path)
        except OSError:
            reasons.append(f"unreadable artifact listed in validation record: {name}")
            continue
        if actual != digest:
            reasons.append(f"{name} does not match validation-record.json artifact hash")
    declared_set_digest = record.get("artifact_set_digest")
    if isinstance(declared_set_digest, str):
        from cept.application.run_identity import artifact_set_digest

        if artifact_set_digest(expected) != declared_set_digest:
            reasons.append("artifact_set_digest does not match validation-record.json artifact hashes")


def _bind_attempt_identity(
    run: Path,
    manifest: dict[str, Any] | None,
    validation: dict[str, Any] | None,
    reasons: list[str],
) -> None:
    """Require the invocation/plan identity to agree across new run artifacts."""
    attempt = _read_json(run / "attempt.json")
    record = _read_json(run / "validation-record.json")
    identity_fields = ("attempt_id", "execution_key", "case_fingerprint")
    attempt_fields = ("attempt_id", "execution_key")
    declared = [
        payload.get(field)
        for payload in (manifest, validation, record)
        if isinstance(payload, dict)
        for field in attempt_fields
        if payload.get(field) is not None
    ]
    if not declared and attempt is None:
        return
    if attempt is None:
        reasons.append("missing attempt.json for a run carrying attempt identity")
        return
    for field in identity_fields:
        expected = attempt.get(field)
        if expected is None:
            reasons.append(f"attempt.json is missing {field}")
            continue
        for label, payload in (("manifest", manifest), ("validation", validation), ("validation-record", record)):
            if not isinstance(payload, dict):
                reasons.append(f"{label} is missing {field} for an attempt-bound run")
            elif payload.get(field) is None:
                reasons.append(f"{label} is missing {field} for an attempt-bound run")
            elif payload.get(field) != expected:
                reasons.append(f"{label} {field} does not match attempt.json")
    validation_assessment = (validation or {}).get("assessment_id")
    record_assessment = (record or {}).get("assessment_id")
    if validation_assessment is None or record_assessment is None:
        reasons.append("assessment_id is missing from validation artifacts")
    elif validation_assessment != record_assessment:
        reasons.append("validation-record assessment_id does not match validation_report.json")

def verify_run_set(run_dirs: list[str | Path]) -> dict[str, Any]:
    """Return a deterministic summary for only the supplied run directories."""
    entries: list[dict[str, Any]] = []
    for raw in run_dirs:
        run = Path(raw).resolve()
        reasons: list[str] = []
        manifest_path = run / "manifest.json"
        manifest = _read_json(manifest_path)
        if manifest and manifest.get("claim") == "powerfactory_reference":
            result = _read_json(run / "results.json")
            receipt = _read_json(run / "validation-record.json")
            contract = _read_json(run / "result-contract.json")
            source_manifest = _read_json(run / "source-manifest.json")
            for name in (
                "source.pfd",
                "source-manifest.json",
                "result-contract.json",
                "native-reference.pfd",
                "results.json",
                "manifest.json",
                "validation-record.json",
                "report.html",
                "raw",
            ):
                if not (run / name).exists():
                    reasons.append(f"missing reference artifact: {name}")
            if result is None or result.get("status") != "reference-run":
                reasons.append("reference solver did not complete")
            expected_lane = "source-faithful-reference"
            if manifest.get("lane") != expected_lane:
                reasons.append("reference lane metadata is missing or invalid")
            if source_manifest is not None and source_manifest.get("lane") != expected_lane:
                reasons.append("source-manifest lane metadata does not match reference lane")
            if contract is not None and contract.get("lane") != expected_lane:
                reasons.append("result-contract lane metadata does not match reference lane")
            if result is not None and result.get("lane") != expected_lane:
                reasons.append("results lane metadata does not match reference lane")
            manifest_identity = manifest.get("study_case_identity")
            manifest_case = manifest.get("study_case") or {}
            contract_case = (contract or {}).get("study_case") or {}
            result_case = (result or {}).get("study_case") or {}
            for label, case in (("contract", contract_case), ("results", result_case)):
                identity = (case.get("full_name") or case.get("name")) if isinstance(case, dict) else None
                if manifest_identity and identity != manifest_identity:
                    reasons.append(f"{label} Study Case identity does not match manifest")
            if not manifest_identity and not (manifest_case.get("full_name") or manifest_case.get("name")):
                reasons.append("missing Study Case identity metadata")
            if receipt is None or receipt.get("verdict") != "reference-run-only":
                reasons.append("missing reference-only validation receipt")
            if contract is None or contract.get("schema") != "cept-source-result-contract-v1":
                reasons.append("missing source result contract")
            else:
                if (
                    result is not None
                    and result.get("status") == "reference-run"
                    and contract.get("status") != "captured"
                ):
                    reasons.append("reference solver completed but source result contract is not captured")
                if contract.get("results_sha256") != sha256_file(run / "results.json"):
                    reasons.append("source result contract does not match results.json")
                if manifest.get("result_contract_sha256") != sha256_file(run / "result-contract.json"):
                    reasons.append("manifest result-contract hash mismatch")
            if manifest.get("source_sha256") and manifest.get("source_sha256") != sha256_file(
                run / "source.pfd"
            ):
                reasons.append("source PFD hash mismatch")
            if manifest.get("results_sha256") and manifest.get("results_sha256") != sha256_file(
                run / "results.json"
            ):
                reasons.append("manifest results hash mismatch")
            if (
                contract
                and contract.get("source_sha256")
                and contract.get("source_sha256") != sha256_file(run / "source.pfd")
            ):
                reasons.append("source result contract does not match source.pfd")
            if (
                receipt
                and receipt.get("source_manifest_sha256")
                and receipt.get("source_manifest_sha256") != sha256_file(run / "source-manifest.json")
            ):
                reasons.append("validation record source-manifest hash mismatch")
            if (
                receipt
                and receipt.get("result_contract_sha256")
                and receipt.get("result_contract_sha256") != sha256_file(run / "result-contract.json")
            ):
                reasons.append("validation record result-contract hash mismatch")
            source_identity = load_identity_map(run)
            entries.append(
                {
                    "run_dir": str(run),
                    "status": "pass" if not reasons else "incomplete",
                    "manifest_status": manifest.get("status"),
                    "claim": "powerfactory_reference",
                    "reasons": reasons,
                    "case_fingerprint": None,
                    "identity_map_present": source_identity is not None,
                    "semantic_registry_version": (
                        source_identity.registry_version if source_identity else None
                    ),
                }
            )
            continue
        if manifest and manifest.get("claim") == "independent-reference":
            result = _read_json(run / "results.json")
            receipt = _read_json(run / "validation-record.json")
            for name in (
                "input",
                "raw",
                "results.json",
                "manifest.json",
                "validation-record.json",
                "report.html",
            ):
                if not (run / name).exists():
                    reasons.append(f"missing independent-reference artifact: {name}")
            if result is None or result.get("engine") != manifest.get("engine"):
                reasons.append("independent reference result/engine mismatch")
            if receipt is None or receipt.get("fresh_solver_run") is not True:
                reasons.append("missing fresh-solver independent-reference receipt")
            entries.append(
                {
                    "run_dir": str(run),
                    "status": "pass" if not reasons else "incomplete",
                    "manifest_status": manifest.get("status"),
                    "claim": "independent-reference",
                    "reasons": reasons,
                    "case_fingerprint": result.get("case_fingerprint") if result else None,
                }
            )
            continue
        if manifest is None:
            reasons.append("missing or invalid manifest.json")
        status = manifest.get("status") if manifest else None
        if status != "pass":
            reasons.append(f"manifest status is {status or 'missing'}")

        result_paths = [run / "results.json"]
        result_paths.extend(sorted(run.glob("*/results.json")))
        if not any(path.is_file() for path in result_paths):
            reasons.append("missing results.json")

        validation_paths = [run / "validation_report.json", run / "cross_engine_report.json"]
        validation = next((_read_json(path) for path in validation_paths if path.is_file()), None)
        if validation is None:
            reasons.append("missing validation_report.json or cross_engine_report.json")
        elif validation.get("passed") is not True:
            reasons.append("validation report does not explicitly report passed=true")

        # The identity map is how a later comparison knows which engine object
        # each Case asset became.  Its absence does not fail the run -- the
        # solver result is still real -- but it is reported so a comparison
        # built on this leaf can say the mapping was name-based.
        identity_map = load_identity_map(run)
        if identity_map is None:
            identity_map = next(
                (found for path in sorted(run.glob("*/")) if (found := load_identity_map(path)) is not None),
                None,
            )

        verified_payload = _bind_report_to_artifacts(run, manifest, reasons)
        _bind_validation_record_hashes(run, reasons)
        _bind_attempt_identity(run, manifest, validation, reasons)

        entries.append(
            {
                "run_dir": str(run),
                "status": "pass" if not reasons else "incomplete",
                "manifest_status": status,
                "reasons": reasons,
                "case_fingerprint": manifest.get("case_fingerprint") if manifest else None,
                "identity_map_present": identity_map is not None,
                "semantic_registry_version": identity_map.registry_version if identity_map else None,
                "report_verified_payload": verified_payload or None,
            }
        )

    passed = bool(entries) and all(item["status"] == "pass" for item in entries)
    summary: dict[str, Any] = {"passed": passed, "runs": entries}
    conduct_mode = _conduct_mode(run_dirs)
    if conduct_mode is not None:
        # Carried into every verification summary so a harness-assisted run can
        # never be read as an unaided model result further downstream.
        summary["conduct_mode"] = conduct_mode
    return summary


def _conduct_mode(run_dirs: list[str | Path]) -> dict[str, Any] | None:
    """Find the conduct mode record covering these runs, if there is one."""
    for raw in run_dirs:
        run = Path(raw).resolve()
        for candidate in (run, *run.parents):
            record = candidate / CONDUCT_MODE_FILENAME
            if record.is_file():
                payload = _read_json(record)
                if payload is None:
                    continue
                return {
                    "mode": payload.get("mode"),
                    "model": payload.get("model"),
                    "model_driven_stages": payload.get("model_driven_stages"),
                    "unaided": payload.get("unaided", False),
                    "record": str(record),
                }
    return None


def write_verification_summary(
    run_dirs: list[str | Path],
    out: str | Path | None = None,
) -> tuple[int, dict[str, Any]]:
    summary = verify_run_set(run_dirs)
    encoded = json.dumps(summary, indent=2, ensure_ascii=False)
    print(encoded)
    if out:
        target = Path(out).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(encoded + "\n", encoding="utf-8")
    return (EXIT_OK if summary["passed"] else EXIT_POLICY), summary


__all__ = ["verify_run_set", "write_verification_summary"]
