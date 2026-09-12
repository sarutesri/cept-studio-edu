"""Content-addressed identity for CEPT model packages.

This is the single non-CLI contract used by planning and run staging.  A model
package is identified by the package JSON plus every portable, hash-declared
file it references; absolute source locations never participate in the cache
identity.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from cept.util import read_json, sha256_file


@dataclass(frozen=True)
class ModelPackageFile:
    relative_path: str
    sha256: str
    source_path: str


@dataclass(frozen=True)
class ModelPackageIdentity:
    package_name: str
    package_sha256: str
    content_fingerprint: str
    files: tuple[ModelPackageFile, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "package_name": self.package_name,
            "package_sha256": self.package_sha256,
            "content_fingerprint": self.content_fingerprint,
            "files": [
                {"path": item.relative_path, "sha256": item.sha256}
                for item in self.files
            ],
        }


def _portable_reference(source: Path, raw: object, *, label: str) -> tuple[Path, str]:
    relative = Path(str(raw))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"{label} path is not portable: {raw}")
    resolved = (source.parent / relative).resolve()
    parent = source.parent.resolve()
    if parent not in resolved.parents or not resolved.is_file():
        raise FileNotFoundError(f"{label} is missing: {resolved}")
    return resolved, relative.as_posix()


def _declared_file(
    source: Path,
    ref: dict[str, Any],
    *,
    label: str,
) -> ModelPackageFile | None:
    raw = ref.get("path")
    if not raw:
        return None
    resolved, relative = _portable_reference(source, raw, label=label)
    actual = sha256_file(resolved).lower()
    expected = str(ref.get("sha256") or "").strip().lower()
    if actual != expected:
        raise ValueError(f"{label} hash mismatch: {resolved}")
    return ModelPackageFile(relative_path=relative, sha256=actual, source_path=str(resolved))


def inspect_model_package(path: str | Path) -> ModelPackageIdentity:
    """Validate one package and return path-independent content identity."""
    source = Path(path).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"model package is missing: {source}")
    payload = read_json(source)
    if not isinstance(payload, dict):
        raise ValueError(f"model package must contain a JSON object: {source}")

    by_path: dict[str, ModelPackageFile] = {}
    for representation in payload.get("representations", []) or []:
        if not isinstance(representation, dict):
            continue
        pf_ref = representation.get("powerfactory_input") or {}
        if isinstance(pf_ref, dict):
            item = _declared_file(source, pf_ref, label="PowerFactory model input")
            if item is not None:
                by_path[item.relative_path] = item
        for raw_ref in representation.get("files", []) or []:
            if not isinstance(raw_ref, dict):
                continue
            item = _declared_file(source, raw_ref, label="PowerFactory model file")
            if item is None:
                continue
            existing = by_path.get(item.relative_path)
            if existing is not None and existing.sha256 != item.sha256:
                raise ValueError(
                    f"model package declares conflicting hashes for {item.relative_path}"
                )
            by_path[item.relative_path] = item

    package_sha = sha256_file(source).lower()
    files = tuple(by_path[key] for key in sorted(by_path))
    identity_payload = {
        "package_name": source.name,
        "package_sha256": package_sha,
        "files": [{"path": item.relative_path, "sha256": item.sha256} for item in files],
    }
    digest = hashlib.sha256(
        json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return ModelPackageIdentity(
        package_name=source.name,
        package_sha256=package_sha,
        content_fingerprint=digest,
        files=files,
    )


def inspect_model_packages(paths: Iterable[str]) -> tuple[ModelPackageIdentity, ...]:
    """Validate model packages in declared order and freeze their content identities."""
    return tuple(inspect_model_package(path) for path in paths)


def model_package_identity_json(identities: Iterable[ModelPackageIdentity]) -> str:
    return json.dumps(
        [identity.as_dict() for identity in identities],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


__all__ = [
    "ModelPackageFile",
    "ModelPackageIdentity",
    "inspect_model_package",
    "inspect_model_packages",
    "model_package_identity_json",
]
