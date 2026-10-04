"""Portable artifact-root and output-path policy."""

from __future__ import annotations

import os
from pathlib import Path


def artifact_root(cwd: str | Path | None = None) -> Path:
    """Return the configured portable root for generated research artifacts."""
    configured = os.environ.get("CEPT_ARTIFACT_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    base = Path(cwd or Path.cwd()).resolve()
    return base / "workspace" / "benchmark-validation" / "runs"


def reject_desktop(path: str | Path) -> Path:
    """Reject new generated artifacts on a user's Desktop."""
    resolved = Path(path).expanduser().resolve()
    # Allow artifacts written inside the project root, even if the project itself
    # is cloned under the Desktop (e.g. for developer checkouts).
    local_project = Path.cwd().resolve()
    if resolved == local_project or local_project in resolved.parents:
        return resolved
    local_workspace = (Path.cwd() / "workspace").resolve()
    if resolved == local_workspace or local_workspace in resolved.parents:
        return resolved
    desktop_candidates = {
        (Path.home() / "Desktop").resolve(),
        (Path.home() / "OneDrive" / "Desktop").resolve(),
    }
    if any(resolved == desktop or desktop in resolved.parents for desktop in desktop_candidates):
        raise ValueError(
            f"Generated artifacts may not be written under Desktop: {resolved}. "
            "Use workspace/benchmark-validation/runs or CEPT_ARTIFACT_ROOT."
        )
    return resolved


def portable_run_dir(path: str | Path | None, *, cwd: str | Path | None = None) -> Path:
    """Resolve an output directory and apply the Desktop guard."""
    return reject_desktop(path or artifact_root(cwd))


__all__ = ["artifact_root", "portable_run_dir", "reject_desktop"]
