"""Fail-honest identity for the Python dependency environment used by CEPT.

A committed lock file is only a dependency *contract*. It does not prove the
active virtual environment was installed from that lock. The lock must also be
paired with generation metadata whose hash, source input, target runtime, and
clean-install smoke agree, and bootstrap writes an environment marker inside
the venv. ``LOCKED`` is accepted only when all identities agree with the active
interpreter.
"""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from importlib import metadata
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _sha256(path: Path) -> str:
    """Digest the newline-normalised bytes of ``path``.

    ``.gitattributes`` pins these files to ``eol=lf``, so a Windows checkout
    holds CRLF and a Linux checkout holds LF. Hashing raw bytes would make a
    lock attest as valid only on the machine that generated it, so every
    producer and consumer of these digests normalises first.
    """

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        pending = b""
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            buffer = pending + chunk
            cut = buffer.rfind(b"\r\n")
            if cut == -1:
                pending = buffer
                continue
            digest.update(buffer[: cut + 2].replace(b"\r\n", b"\n"))
            pending = buffer[cut + 2 :]
    if pending:
        digest.update(pending.replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def environment_marker_path(prefix: str | Path | None = None) -> Path:
    root = Path(prefix).resolve() if prefix is not None else Path(sys.prefix).resolve()
    return root / ".cept-research-environment.json"


def research_lock_path(root: str | Path | None = None) -> Path:
    repo = Path(root).resolve() if root is not None else _repo_root()
    return repo / "requirements" / "research.lock"


def research_lock_metadata_path(root: str | Path | None = None) -> Path:
    repo = Path(root).resolve() if root is not None else _repo_root()
    return repo / "requirements" / "research-lock-metadata.json"


def _pip_version() -> str | None:
    try:
        return metadata.version("pip")
    except metadata.PackageNotFoundError:
        return None


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def research_environment_identity(
    root: str | Path | None = None,
    *,
    prefix: str | Path | None = None,
) -> dict[str, Any]:
    """Describe the active dependency environment without upgrading evidence.

    States:
    - ``UNLOCKED-DEVELOPMENT``: repository has no research lock.
    - ``LOCKED``: lock + generation metadata + active-venv marker all agree.
    - ``LOCKFILE-PRESENT-UNVERIFIED``: a lock exists, but its generation
      metadata or active-interpreter attestation is missing/mismatched.
    """

    repo = Path(root).resolve() if root is not None else _repo_root()
    lock_path = research_lock_path(repo)
    lock_sha = _sha256(lock_path) if lock_path.is_file() else None
    pyproject_path = repo / "pyproject.toml"
    pyproject_sha = _sha256(pyproject_path) if pyproject_path.is_file() else None
    current_python = platform.python_version()
    current_platform = platform.platform()
    current_pip = _pip_version()

    lock_metadata_path = research_lock_metadata_path(repo)
    lock_metadata = _load_json(lock_metadata_path)
    lock_metadata_sha = _sha256(lock_metadata_path) if lock_metadata_path.is_file() else None
    lock_metadata_valid = bool(
        lock_sha is not None
        and pyproject_sha is not None
        and lock_metadata is not None
        and lock_metadata.get("schema_version") == 1
        and lock_metadata.get("source") == "pyproject.toml"
        and lock_metadata.get("extras") == ["dev", "docs", "export"]
        and lock_metadata.get("hashes_required") is True
        and lock_metadata.get("research_lock_sha256") == lock_sha
        and lock_metadata.get("pyproject_sha256") == pyproject_sha
        and lock_metadata.get("python_version") == current_python
        and lock_metadata.get("platform") == current_platform
        and lock_metadata.get("bootstrap_pip_version") == current_pip
        and lock_metadata.get("clean_install_smoke") == "PASS"
        and lock_metadata.get("github_actions_used") is False
    )

    marker_path = environment_marker_path(prefix)
    marker = _load_json(marker_path)
    marker_sha = _sha256(marker_path) if marker_path.is_file() else None

    mode = "UNLOCKED-DEVELOPMENT"
    marker_matches = False
    if lock_sha is not None:
        mode = "LOCKFILE-PRESENT-UNVERIFIED"
        if marker is not None and lock_metadata_valid:
            marker_python = marker.get("python_executable")
            marker_matches = (
                marker.get("schema_version") == 1
                and marker.get("mode") == "LOCKED"
                and marker.get("research_lock_sha256") == lock_sha
                and marker.get("research_lock_metadata_sha256") == lock_metadata_sha
                and marker.get("pyproject_sha256") == pyproject_sha
                and marker.get("python_version") == current_python
                and marker.get("platform") == current_platform
                and marker.get("pip_version") == current_pip
                and marker.get("chromium_installed") is True
                and isinstance(marker_python, str)
                and Path(marker_python).resolve() == Path(sys.executable).resolve()
            )
            if marker_matches:
                mode = "LOCKED"

    return {
        "schema_version": 1,
        "environment_mode": mode,
        "pyproject_sha256": pyproject_sha,
        "research_lock_sha256": lock_sha,
        "research_lock_metadata_sha256": lock_metadata_sha,
        "research_lock_metadata_valid": lock_metadata_valid,
        "environment_marker_path": str(marker_path) if marker_path.is_file() else None,
        "environment_marker_sha256": marker_sha,
        "environment_marker_matches": marker_matches,
        "python_version": current_python,
        "python_implementation": platform.python_implementation(),
        "python_executable": sys.executable,
        "platform": current_platform,
        "pip_version": current_pip,
    }


__all__ = [
    "environment_marker_path",
    "research_environment_identity",
    "research_lock_metadata_path",
    "research_lock_path",
]
