"""Immutable software identity attached to CEPT research artifacts.

A package version is not enough to reproduce a study while several commits may
share that version. This module records the exact source revision when it can be
proven and also hashes the executable CEPT source tree so dirty development
checkouts cannot collapse to the same result-cache identity.

Identity is a pure function of pinned inputs, and it is computed once.

* **Once per process.** The snapshot is memoized on the resolved checkout root
  and on every environment input that can change the answer, so one run makes
  one ``git`` probe rather than one per graph node, per study and per artifact
  writer. Repeated calls in the same process cannot disagree with each other.
* **Fail closed, with one value.** The working-tree state has exactly three
  possible recorded answers: :data:`DIRTY_CLEAN` (``False``),
  :data:`DIRTY_DIRTY` (``True``) and :data:`DIRTY_UNREADABLE`
  (``"unreadable"``). The third used to be ``None``, which a slow
  ``git status --porcelain`` produced by hitting its five-second timeout and
  then collapsing into ``subprocess.SubprocessError`` — a third state
  indistinguishable in kind from "unknown" and quietly different from both a
  clean and a dirty answer. It flows into the ``case-node-*`` fingerprints,
  ``result_identity`` and the result-cache keys, so it is identity, not a
  cosmetic field. The sentinel is a string, so it can never equal a legitimate
  boolean answer, and it is used for every way the state can be unreadable:
  git missing, the checkout not being a repository, a non-zero status, or a
  timeout.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

from cept import __version__
from cept.application.environment_identity import research_environment_identity
from cept.util import read_json, sha256_file

#: Recorded working-tree answers. The sentinel is a string precisely so it
#: cannot compare equal to either boolean a real ``git status`` can produce.
DIRTY_CLEAN = False
DIRTY_DIRTY = True
DIRTY_UNREADABLE = "unreadable"

#: Every environment input that can change the recorded identity. They are part
#: of the memoization key so a test or a launcher that sets one gets a new
#: snapshot instead of a stale one.
_ENV_IDENTITY_KEYS = (
    "CEPT_SOURCE_REVISION",
    "CEPT_SOURCE_DIRTY",
    "CEPT_RELEASE_TAG",
    "CEPT_BUILD_ID",
    "CEPT_SOURCE_TREE_SHA256",
)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _env_bool(name: str) -> bool | None:
    value = os.environ.get(name)
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "dirty"}:
        return True
    if normalized in {"0", "false", "no", "clean"}:
        return False
    raise ValueError(f"{name} must be one of true/false, 1/0, yes/no, dirty/clean")


def _git(args: list[str], root: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip()


def _checkout_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _source_tree_sha256(checkout: Path) -> str | None:
    """Hash CEPT runtime source content, including untracked development files."""
    baked = os.environ.get("CEPT_SOURCE_TREE_SHA256")
    if baked:
        normalized = baked.strip().lower()
        if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
            raise ValueError("CEPT_SOURCE_TREE_SHA256 must be a 64-character hexadecimal SHA-256")
        return normalized

    roots = [checkout / "src" / "cept"]
    files: list[Path] = []
    source_root = roots[0]
    if source_root.is_dir():
        files.extend(
            path
            for path in source_root.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix.lower() not in {".pyc", ".pyo"}
        )
    for relative in ("pyproject.toml",):
        path = checkout / relative
        if path.is_file():
            files.append(path)
    requirements = checkout / "requirements"
    if requirements.is_dir():
        files.extend(path for path in requirements.rglob("*") if path.is_file())
    if not files:
        return None

    digest = hashlib.sha256()
    for path in sorted(set(files), key=lambda item: item.relative_to(checkout).as_posix()):
        relative_bytes = path.relative_to(checkout).as_posix().encode("utf-8")
        digest.update(len(relative_bytes).to_bytes(8, "big"))
        digest.update(relative_bytes)
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=8)
def _source_identity_snapshot(
    checkout: str,
    env_key: tuple[tuple[str, str | None], ...],
) -> dict[str, Any]:
    """Compute one identity snapshot for a checkout and a set of environment inputs."""

    root = Path(checkout)
    environment = dict(env_key)
    revision = environment.get("CEPT_SOURCE_REVISION") or _git(["rev-parse", "HEAD"], root)
    dirty: bool | str
    if environment.get("CEPT_SOURCE_DIRTY") is not None:
        # An explicit override is the caller's pinned answer. `_env_bool`
        # rejects anything that is not a real boolean, so this can never
        # produce the unreadable sentinel by accident.
        dirty = _env_bool("CEPT_SOURCE_DIRTY")  # type: ignore[assignment]
    else:
        status = _git(["status", "--porcelain"], root)
        # One unreadable value for every way the state can fail to be read:
        # git absent, not a repository, non-zero status, or the five-second
        # timeout (a `subprocess.TimeoutExpired`, which is also a
        # `subprocess.SubprocessError` and used to land here as `None`).
        dirty = DIRTY_UNREADABLE if status is None else (DIRTY_DIRTY if status else DIRTY_CLEAN)
    release_tag = environment.get("CEPT_RELEASE_TAG")
    build_id = environment.get("CEPT_BUILD_ID")
    if build_id is None:
        build_id = f"git:{revision[:12]}" if revision else f"package:{__version__}"
    return {
        "cept_version": __version__,
        "source_revision": revision,
        "source_dirty": dirty,
        "source_tree_sha256": _source_tree_sha256(root),
        "build_id": build_id,
        "release_tag": release_tag,
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "executable": sys.executable,
        "dependency_environment": research_environment_identity(root),
    }


def source_identity(root: str | Path | None = None) -> dict[str, Any]:
    """Return exact software/dependency identity without upgrading unknowns.

    The snapshot is taken once per process per checkout and per set of
    environment inputs, and a fresh deep copy is returned so a caller cannot
    mutate what the next caller sees. Identity therefore cannot disagree with
    itself between two nodes of one execution graph, two studies of one run, or
    the manifest and the receipt written by one run.
    """

    checkout = Path(root).resolve() if root is not None else _checkout_root()
    env_key = tuple((name, os.environ.get(name)) for name in _ENV_IDENTITY_KEYS)
    return deepcopy(_source_identity_snapshot(str(checkout), env_key))


def write_run_source_identity(run_dir: str | Path, identity: dict[str, Any] | None = None) -> dict[str, Any]:
    """Write and bind software identity to manifest and run validation receipt."""
    run_dir = Path(run_dir)
    identity = dict(identity or source_identity())
    identity_path = run_dir / "source-identity.json"
    _write_json(identity_path, identity)

    manifest_path = run_dir / "manifest.json"
    if manifest_path.is_file():
        manifest = read_json(manifest_path)
        manifest.update(identity)
        manifest["source_identity_artifact"] = identity_path.name
        _write_json(manifest_path, manifest)

    record_path = run_dir / "validation-record.json"
    if record_path.is_file():
        record = read_json(record_path)
        record["source_identity"] = identity
        record["source_identity_artifact"] = {
            "path": identity_path.name,
            "sha256": sha256_file(identity_path),
        }
        if "manifest_hash" in record and manifest_path.is_file():
            record["manifest_hash"] = sha256_file(manifest_path)
        _write_json(record_path, record)
    return identity


__all__ = [
    "DIRTY_CLEAN",
    "DIRTY_DIRTY",
    "DIRTY_UNREADABLE",
    "source_identity",
    "write_run_source_identity",
]
