"""Filesystem checks for Case intake provenance.

This is pure evidence reading — hashes, receipts, template files — with no
command logic and no engine.  It is core because the Case-loading operations
(:mod:`cept.application.operations.check` / ``.run``) verify a Case's intake
provenance before any study runs; keeping it here lets those operations import
it directly instead of reaching up into ``cept.cli`` (this module's former
home).  The topology-evidence models it consumes live in
:mod:`cept.schema.topology`, alongside the provenance schema in
:mod:`cept.schema.provenance`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any

from cept.schema.case import Case
from cept.schema.provenance import SourceManifest
from cept.schema.topology import load_topology_manifest, validate_topology_manifest


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_source_manifest(path: Path) -> tuple[SourceManifest, str]:
    if not path.is_file():
        raise FileNotFoundError(f"Source manifest not found: {path}")
    raw = path.read_bytes()
    manifest = SourceManifest.model_validate(json.loads(raw))
    return manifest, hashlib.sha256(raw).hexdigest()


def _resolve_reference(uri: str, base: Path) -> Path | None:
    # URLs and opaque document IDs are recorded but cannot be checked locally.
    if "://" in uri:
        return None
    candidate = Path(uri)
    return candidate if candidate.is_absolute() else (base / candidate).resolve()


def verify_manifest_sources(manifest: SourceManifest, manifest_path: Path) -> None:
    """Verify local source hashes; remote/opaque URIs remain auditable refs."""
    for source in manifest.sources:
        path = _resolve_reference(source.uri, manifest_path.parent)
        if path is None:
            continue
        if not path.is_file():
            raise ValueError(f"Source manifest file is missing: {path}")
        actual = sha256_file(path)
        if actual.lower() != source.sha256.lower():
            raise ValueError(f"Source hash mismatch for {source.uri}: expected {source.sha256}, got {actual}")


def _normalise_evidence(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).casefold().strip()


def _source_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            try:
                from PyPDF2 import PdfReader  # type: ignore
            except ImportError:
                interpreter = shutil.which("python") or sys.executable
                script = (
                    "import sys; from PyPDF2 import PdfReader; "
                    "r=PdfReader(sys.argv[1]); "
                    "print('\\n'.join(p.extract_text() or '' for p in r.pages))"
                )
                try:
                    completed = subprocess.run(
                        [interpreter, "-c", script, str(path)],
                        capture_output=True,
                        text=True,
                        check=True,
                        shell=False,
                    )
                    return completed.stdout
                except Exception as exc:
                    raise ValueError("citation verification needs pypdf or PyPDF2 for PDF sources") from exc
        try:
            return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
        except Exception as exc:
            raise ValueError(f"citation verification could not extract text from {path}: {exc}") from exc
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ValueError(f"citation verification could not read {path}: {exc}") from exc


def verify_manifest_evidence(
    manifest: SourceManifest,
    manifest_path: Path,
    *,
    extraction_path: Path | None = None,
    topology_manifest_path: Path | None = None,
) -> None:
    """Prove research citations occur in the local source text.

    PFD object-graph evidence is API-derived and binary, so it is checked by
    the input-only inventory rather than pretending the PFD bytes are text.
    """
    if manifest.topology.status not in {"VERIFIED_STRUCTURED", "VERIFIED_VISION"}:
        return
    text_sources: dict[str, str] = {}
    for source in manifest.sources:
        path = _resolve_reference(source.uri, manifest_path.parent)
        if path is None:
            raise ValueError(f"citation verification requires a local source: {source.uri}")
        if path.suffix.lower() == ".pfd" and manifest.topology.method == "pfd-object-graph":
            continue
        text_sources[source.uri] = _normalise_evidence(_source_text(path))
    if not text_sources:
        return

    missing: list[str] = []
    for source in manifest.sources:
        corpus = text_sources.get(source.uri)
        if corpus is None:
            continue
        for locator in source.locators:
            if _normalise_evidence(locator) not in corpus:
                missing.append(f"{source.uri}: {locator}")
    topology_source = text_sources.get(manifest.topology.source_ref)
    if topology_source is None:
        topology_source = next(iter(text_sources.values()), "")
    if manifest.topology.excerpt and _normalise_evidence(manifest.topology.excerpt) not in topology_source:
        missing.append(f"{manifest.topology.source_ref}: {manifest.topology.excerpt}")

    if topology_manifest_path is not None:
        topology, _ = load_topology_manifest(topology_manifest_path)
        evidence_source = text_sources.get(topology.source_ref, topology_source)
        if topology.locator and _normalise_evidence(topology.locator) not in evidence_source:
            missing.append(f"{topology_manifest_path.name}: {topology.locator}")
        for edge in topology.edges:
            if edge.evidence and _normalise_evidence(edge.evidence) not in evidence_source:
                missing.append(f"{topology_manifest_path.name}: {edge.id}: {edge.evidence}")

    if extraction_path is not None and extraction_path.is_file():
        extraction = extraction_path.read_text(encoding="utf-8", errors="replace")
        for line in extraction.splitlines():
            if "page/table/figure" in line.lower() or line.lstrip().startswith("#"):
                continue
            for locator in re.findall(
                r"(?i)\b(?:page|p\.|table|tab\.|figure|fig\.)\s*[A-Za-z0-9][A-Za-z0-9.-]*", line
            ):
                if not any(_normalise_evidence(locator) in corpus for corpus in text_sources.values()):
                    missing.append(f"{extraction_path.name}: {locator}")
    if missing:
        raise ValueError("source citation does not occur in the cited source text: " + "; ".join(missing[:8]))


def template_hashes(folder: Path) -> dict[str, str]:
    return {
        name: sha256_file(folder / name)
        for name in (
            "buses.csv",
            "lines.csv",
            "transformers.csv",
            "loads.csv",
            "external_grids.csv",
            "generators.csv",
        )
        if (folder / name).is_file()
    }


def _template_dir_reference(template_dir: Path, manifest_path: Path) -> str:
    """Store the intake directory relative to the manifest for portability."""
    return Path(os.path.relpath(template_dir.resolve(), manifest_path.parent.resolve())).as_posix()


def _resolve_template_dir(receipt: dict[str, Any], manifest_path: Path) -> tuple[Path, bool]:
    """Resolve receipt metadata, falling back to the legacy manifest directory."""
    if "template_dir" not in receipt:
        return manifest_path.parent, True
    template_ref = receipt["template_dir"]
    if not isinstance(template_ref, str) or not template_ref:
        raise ValueError("Invalid Case provenance template_dir; re-ingest the Case")
    return (manifest_path.parent / template_ref).resolve(), False


def write_ingest_receipt(
    *,
    receipt_path: Path,
    case: Case,
    source_manifest_path: Path,
    source_manifest_sha256: str,
    template_dir: Path,
    topology_manifest_path: Path | None = None,
    topology_manifest_sha256: str | None = None,
) -> None:
    payload = {
        "case_fingerprint": case.fingerprint(),
        "source_manifest": str(source_manifest_path),
        "source_manifest_sha256": source_manifest_sha256,
        "template_dir": _template_dir_reference(template_dir, source_manifest_path),
        "template_hashes": template_hashes(template_dir),
    }
    if topology_manifest_path is not None and topology_manifest_sha256 is not None:
        payload["topology_manifest"] = Path(
            os.path.relpath(topology_manifest_path.resolve(), receipt_path.parent.resolve())
        ).as_posix()
        payload["topology_manifest_sha256"] = topology_manifest_sha256
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def verify_case_provenance(case: Case, case_path: Path) -> None:
    """Fail closed when an ingested Case or its evidence was edited."""
    binding = case.provenance
    if binding is None:
        return
    manifest_path = (case_path.parent / binding.source_manifest).resolve()
    manifest, manifest_hash = load_source_manifest(manifest_path)
    if manifest_hash.lower() != binding.source_manifest_sha256.lower():
        raise ValueError("Case provenance source manifest hash mismatch; re-ingest the Case")
    verify_manifest_sources(manifest, manifest_path)

    receipt_path = (case_path.parent / binding.ingest_receipt).resolve()
    if not receipt_path.is_file():
        raise ValueError(f"Case provenance receipt is missing: {receipt_path}")
    try:
        receipt: dict[str, Any] = json.loads(receipt_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid Case provenance receipt: {receipt_path}") from exc
    if receipt.get("source_manifest_sha256", "").lower() != manifest_hash.lower():
        raise ValueError("Case provenance receipt does not match the source manifest")
    if receipt.get("case_fingerprint") != case.fingerprint():
        raise ValueError(
            "Case provenance receipt does not match the Case; re-ingest or restore the original Case"
        )

    topology_ref = receipt.get("topology_manifest")
    topology_hash = receipt.get("topology_manifest_sha256")
    topology_path: Path | None = None
    if topology_ref or topology_hash:
        if not isinstance(topology_ref, str) or not isinstance(topology_hash, str):
            raise ValueError("Invalid topology manifest binding in Case receipt; re-ingest the Case")
        topology_path = (receipt_path.parent / topology_ref).resolve()
        topology, actual_hash = load_topology_manifest(topology_path)
        if actual_hash.lower() != topology_hash.lower():
            raise ValueError("Case topology manifest hash mismatch; re-ingest the Case")
        if case.network.kind == "inline" and case.network.inline is not None:
            validate_topology_manifest(topology, case.network.inline, research=case.meta.mode == "research")

    verify_manifest_evidence(manifest, manifest_path, topology_manifest_path=topology_path)

    expected_templates = receipt.get("template_hashes", {})
    template_dir, legacy_template_dir = _resolve_template_dir(receipt, manifest_path)
    if not template_dir.is_dir():
        raise ValueError(f"Case provenance template directory is missing: {template_dir}; re-ingest the Case")
    actual_templates = template_hashes(template_dir)
    if expected_templates != actual_templates:
        if legacy_template_dir:
            raise ValueError(
                "Case provenance template hash mismatch; legacy receipt lacks template_dir; "
                "re-ingest the Case"
            )
        raise ValueError("Case provenance template hash mismatch; source intake files were edited")
