"""Small filesystem helpers shared across CLI and gridcode/convert modules.

Kept dependency-free (stdlib only) so every module in the package can import
from here without risk of a circular import.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def read_json(path: str | Path) -> Any:
    """Load JSON, tolerating a UTF-8 byte-order mark (common from Windows editors)."""
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha256_file(path: str | Path) -> str:
    """SHA-256 hex digest of a file's raw bytes."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


__all__ = ["read_json", "sha256_file"]
