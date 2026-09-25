"""Small filesystem helpers shared across CLI and gridcode/convert modules.

Kept dependency-free (stdlib only) so every module in the package can import
from here without risk of a circular import.
"""

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def read_json(path: str | Path) -> Any:
    """Load JSON, tolerating a UTF-8 byte-order mark (common from Windows editors)."""
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha256_file(path: str | Path) -> str:
    """SHA-256 hex digest of a file's raw bytes."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path: str | Path, payload: Any) -> None:
    """Atomically write deterministic UTF-8 JSON with one trailing newline."""

    target = Path(path)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(payload, temporary, indent=2, sort_keys=True, ensure_ascii=False)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, target)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)



__all__ = ["read_json", "sha256_file", "write_json"]
