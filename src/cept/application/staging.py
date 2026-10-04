"""Model-package staging for runs that carry PowerFactory model packages.

Planning and staging share :mod:`cept.model_package_identity`; this module only
copies already validated portable/hash-bound content into the run directory.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from cept.model_package_identity import inspect_model_packages


def _stage_model_packages(paths: list[str], run_dir: Path) -> tuple[dict[str, str], list[str]]:
    """Validate and copy content-addressed model packages beside the run."""
    hashes: dict[str, str] = {}
    staged: list[str] = []
    identities = inspect_model_packages(paths)
    for raw, identity in zip(paths, identities):
        source = Path(raw).resolve()
        dest_root = run_dir / "models" / source.stem
        dest_root.mkdir(parents=True, exist_ok=True)
        dest_package = dest_root / source.name
        shutil.copy2(source, dest_package)
        hashes[str(source)] = identity.package_sha256
        staged.append(dest_package.relative_to(run_dir).as_posix())
        for file_identity in identity.files:
            file_source = Path(file_identity.source_path)
            file_dest = dest_root / Path(file_identity.relative_path)
            file_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file_source, file_dest)
            hashes[str(file_source)] = file_identity.sha256
            staged.append(file_dest.relative_to(run_dir).as_posix())
    return hashes, staged


__all__ = ["_stage_model_packages"]
