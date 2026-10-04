"""Immutable solver-evidence cache for fully qualified CEPT execution plans.

The cache never stores an unqualified result.  Entries are content-addressed by
the planning layer and contain only solver evidence needed to avoid a fresh
solve while letting each new run regenerate its own report/manifest context.
Every restored file is SHA-256 verified before it is copied into a run.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from cept.util import read_json, sha256_file

_CACHE_SCHEMA = "cept-result-cache-receipt-v1"
_REQUIRED = ("results.json", "identity-map.json")


def result_cache_root() -> Path:
    configured = os.environ.get("CEPT_RESULT_CACHE_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".cept" / "result-cache").resolve()


def _entry_dir(cache_key: str) -> Path:
    return result_cache_root() / cache_key


def _selected_solver_artifacts(run_dir: Path) -> list[Path]:
    selected: list[Path] = []
    for name in _REQUIRED:
        path = run_dir / name
        if path.is_file():
            selected.append(path)
    powerfactory = run_dir / "powerfactory"
    if powerfactory.is_dir():
        selected.extend(path for path in powerfactory.rglob("*") if path.is_file())
    return sorted(selected, key=lambda path: path.relative_to(run_dir).as_posix())


def _receipt_payload(cache_key: str, plan_fingerprint: str, run_dir: Path) -> dict[str, Any]:
    files = _selected_solver_artifacts(run_dir)
    missing = [name for name in _REQUIRED if not (run_dir / name).is_file()]
    if missing:
        raise FileNotFoundError("cannot cache incomplete solver evidence: " + ", ".join(missing))
    return {
        "schema": _CACHE_SCHEMA,
        "cache_key": cache_key,
        "plan_fingerprint": plan_fingerprint,
        "artifacts": [
            {
                "path": path.relative_to(run_dir).as_posix(),
                "sha256": sha256_file(path),
            }
            for path in files
        ],
    }


def _verify_entry(entry: Path, cache_key: str, plan_fingerprint: str) -> tuple[bool, str, dict[str, Any] | None]:
    receipt_path = entry / "receipt.json"
    if not receipt_path.is_file():
        return False, "cache_receipt_missing", None
    try:
        receipt = read_json(receipt_path)
    except Exception as exc:
        return False, f"cache_receipt_unreadable:{type(exc).__name__}", None
    if not isinstance(receipt, dict) or receipt.get("schema") != _CACHE_SCHEMA:
        return False, "cache_receipt_schema_mismatch", None
    if receipt.get("cache_key") != cache_key or receipt.get("plan_fingerprint") != plan_fingerprint:
        return False, "cache_receipt_identity_mismatch", None
    rows = receipt.get("artifacts")
    if not isinstance(rows, list) or not rows:
        return False, "cache_receipt_artifacts_missing", None
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            return False, "cache_receipt_artifact_invalid", None
        raw = str(row.get("path") or "")
        relative = Path(raw)
        if not raw or relative.is_absolute() or ".." in relative.parts:
            return False, "cache_receipt_artifact_path_invalid", None
        normalized = relative.as_posix()
        if normalized in seen:
            return False, "cache_receipt_artifact_duplicate", None
        seen.add(normalized)
        source = (entry / "artifacts" / relative).resolve()
        artifact_root = (entry / "artifacts").resolve()
        if artifact_root not in source.parents or not source.is_file():
            return False, f"cache_artifact_missing:{normalized}", None
        actual = sha256_file(source).lower()
        expected = str(row.get("sha256") or "").lower()
        if actual != expected:
            return False, f"cache_artifact_hash_mismatch:{normalized}", None
    if not set(_REQUIRED).issubset(seen):
        return False, "cache_required_artifact_missing", None
    return True, "cache_hit_verified", receipt


def restore_solver_evidence(cache_key: str, plan_fingerprint: str, run_dir: Path) -> tuple[bool, str]:
    """Verify and transactionally restore one immutable solver-evidence entry."""
    entry = _entry_dir(cache_key)
    if not entry.is_dir():
        return False, "cache_entry_missing"
    valid, reason, receipt = _verify_entry(entry, cache_key, plan_fingerprint)
    if not valid or receipt is None:
        return False, reason

    # Stage every byte outside the run directory first.  A disk/full-share
    # error during copy must not leave half a cache hit in researcher output.
    transaction = run_dir.parent / f".{run_dir.name}.cept-cache-restore-{uuid.uuid4().hex}.tmp"
    staged_root = transaction / "artifacts"
    backup_root = transaction / "backups"
    promoted: list[tuple[Path, Path | None]] = []
    created_dirs: list[Path] = []
    try:
        staged_root.mkdir(parents=True, exist_ok=False)
        promotions: list[tuple[Path, Path]] = []
        for row in receipt["artifacts"]:
            relative = Path(str(row["path"]))
            source = entry / "artifacts" / relative
            staged = staged_root / relative
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, staged)
            if sha256_file(staged).lower() != str(row["sha256"]).lower():
                raise OSError(f"staged cache artifact hash mismatch: {relative.as_posix()}")
            promotions.append((staged, run_dir / relative))

        staged_receipt = transaction / "receipt.json"
        shutil.copy2(entry / "receipt.json", staged_receipt)
        promotions.append((staged_receipt, run_dir / "result-cache-receipt.json"))

        for staged, destination in promotions:
            missing: list[Path] = []
            parent = destination.parent
            while parent != run_dir.parent and not parent.exists():
                missing.append(parent)
                parent = parent.parent
            destination.parent.mkdir(parents=True, exist_ok=True)
            created_dirs.extend(reversed(missing))

            backup: Path | None = None
            if destination.exists():
                relative = destination.relative_to(run_dir)
                backup = backup_root / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                os.replace(destination, backup)
            promoted.append((destination, backup))
            os.replace(staged, destination)
    except Exception as exc:
        for destination, backup in reversed(promoted):
            try:
                if destination.exists():
                    destination.unlink()
                if backup is not None and backup.exists():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(backup, destination)
            except OSError:
                # Preserve the original restore failure.  A rollback failure is
                # still fail-closed to the caller and leaves no cache-hit claim.
                pass
        for directory in reversed(created_dirs):
            try:
                directory.rmdir()
            except OSError:
                pass
        shutil.rmtree(transaction, ignore_errors=True)
        return False, f"cache_restore_io_error:{type(exc).__name__}"

    shutil.rmtree(transaction, ignore_errors=True)
    return True, reason


def store_solver_evidence(cache_key: str, plan_fingerprint: str, run_dir: Path) -> dict[str, Any]:
    """Publish one immutable entry atomically; existing verified entries win."""
    root = result_cache_root()
    root.mkdir(parents=True, exist_ok=True)
    entry = _entry_dir(cache_key)
    if entry.exists():
        valid, reason, receipt = _verify_entry(entry, cache_key, plan_fingerprint)
        if valid and receipt is not None:
            shutil.copy2(entry / "receipt.json", run_dir / "result-cache-receipt.json")
            return {"stored": False, "reason": reason, "receipt": receipt}
        return {"stored": False, "reason": f"existing_cache_entry_invalid:{reason}", "receipt": None}

    receipt = _receipt_payload(cache_key, plan_fingerprint, run_dir)
    temp = root / f".{cache_key}.{uuid.uuid4().hex}.tmp"
    artifact_root = temp / "artifacts"
    artifact_root.mkdir(parents=True, exist_ok=False)
    try:
        for row in receipt["artifacts"]:
            relative = Path(str(row["path"]))
            source = run_dir / relative
            destination = artifact_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        (temp / "receipt.json").write_text(
            json.dumps(receipt, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        try:
            temp.rename(entry)
        except FileExistsError:
            shutil.rmtree(temp, ignore_errors=True)
            valid, reason, existing = _verify_entry(entry, cache_key, plan_fingerprint)
            if valid and existing is not None:
                shutil.copy2(entry / "receipt.json", run_dir / "result-cache-receipt.json")
                return {"stored": False, "reason": reason, "receipt": existing}
            return {"stored": False, "reason": f"cache_publish_race_invalid:{reason}", "receipt": None}
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    shutil.copy2(entry / "receipt.json", run_dir / "result-cache-receipt.json")
    return {"stored": True, "reason": "cache_entry_created", "receipt": receipt}


__all__ = [
    "result_cache_root",
    "restore_solver_evidence",
    "store_solver_evidence",
]
