"""Fail-closed provenance for rendered SLD evidence.

Native evidence binds the final PowerFactory ComWr PNG to canonical geometry,
canonical replay mode, exact source identity, and—when running inside
PowerFactory—the actual post-replay ``IntGrfcon`` readback audit. Unit tests and
non-PF tooling may still write provenance without licensed runtime access, but
release visual builders can require the readback/source fields explicitly.
"""

from __future__ import annotations

from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import struct
from typing import Any
import zlib

from cept.application.software_identity import source_identity

NATIVE_SLD_PROVENANCE_SCHEMA = "cept-native-sld-provenance-v1"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_HEX = frozenset("0123456789abcdefABCDEF")
_READBACK_SCHEMA = "cept-powerfactory-sld-readback-v1"
_READBACK_COVERAGE = "all-finite-intgrfcon-segments"
_REPO_ROOT = Path(__file__).resolve().parents[3]


def sha256_file(path: str | Path) -> str:
    source = Path(path)
    digest = sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_sha256(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text.lower() if len(text) == 64 and all(char in _HEX for char in text) else None


def _valid_revision(value: object) -> str | None:
    text = str(value or "").strip().lower()
    return text if len(text) == 40 and all(char in _HEX for char in text) else None


def _decode_text(value: bytes) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return value.decode("latin-1", errors="replace")


def png_text_metadata(path: str | Path) -> dict[str, str]:
    """Read PNG textual metadata with stdlib only."""
    source = Path(path)
    payload = source.read_bytes()
    if not payload.startswith(_PNG_SIGNATURE):
        raise ValueError(f"not a PNG file: {source}")

    result: dict[str, str] = {}
    offset = len(_PNG_SIGNATURE)
    while offset + 12 <= len(payload):
        length = struct.unpack(">I", payload[offset : offset + 4])[0]
        kind = payload[offset + 4 : offset + 8]
        start = offset + 8
        end = start + length
        if end + 4 > len(payload):
            break
        data = payload[start:end]
        offset = end + 4
        try:
            if kind == b"tEXt":
                key, value = data.split(b"\x00", 1)
                result[_decode_text(key)] = _decode_text(value)
            elif kind == b"zTXt":
                key, rest = data.split(b"\x00", 1)
                if len(rest) >= 2 and rest[0] == 0:
                    result[_decode_text(key)] = _decode_text(zlib.decompress(rest[1:]))
            elif kind == b"iTXt":
                key, rest = data.split(b"\x00", 1)
                if len(rest) < 2:
                    continue
                compressed = rest[0] == 1
                rest = rest[2:]
                _language, rest = rest.split(b"\x00", 1)
                _translated, text = rest.split(b"\x00", 1)
                if compressed:
                    text = zlib.decompress(text)
                result[_decode_text(key)] = _decode_text(text)
        except (ValueError, IndexError, zlib.error):
            continue
        if kind == b"IEND":
            break
    return result


def native_sld_provenance_path(path: str | Path) -> Path:
    source = Path(path)
    return source.with_name(f"{source.stem}.provenance.json")


def _reject_known_fallback_renderer(path: Path) -> dict[str, str]:
    metadata = png_text_metadata(path)
    software = metadata.get("Software", "")
    if "matplotlib" in software.lower():
        raise RuntimeError(
            "native SLD evidence is a Matplotlib-rendered PNG, not a PowerFactory ComWr export"
        )
    return metadata


def _powerfactory_readback_if_available(
    page_name: str | None, app: Any | None = None
) -> dict[str, object] | None:
    """Run licensed readback when the PowerFactory module is importable.

    ``app`` is the already-connected application from the exporting adapter;
    without it the readback reconnects via ``GetApplication()``, which PF 2023
    rejects once an application object already exists in the process.
    """
    if app is None:
        # "Is the licensed engine's Python API importable here?" is a question
        # about the environment, and ``find_spec`` asks it without an ``import``
        # statement — which matters because this module now ships in the free
        # wheel, where naming the vendor module in an import is a blocked
        # private import and would reach around the capability boundary.
        #
        # This must stay "is the API importable", not "is the Advance tier
        # installed": the two differ on a developer machine where the private
        # checkout is importable but the vendor API is not. Asking the wrong
        # question made the readback raise "PowerFactory module unavailable"
        # where the previous probe returned None and let non-PowerFactory tests
        # proceed — eight of them failed in Phase 12 before this was corrected.
        if importlib.util.find_spec("powerfactory") is None:
            return None
    from cept.adapters import audit_active_native_diagram

    # If the module exists but the active diagram is wrong/diagonal, fail the
    # evidence seal. We only tolerate *module absence* for non-PF unit tests.
    return audit_active_native_diagram(page_name, app)


def write_native_sld_provenance(
    path: str | Path,
    *,
    geometry_sha256: str | None,
    render_contract_sha256: str | None = None,
    native_sld_mode: str | None,
    page_name: str | None,
    producer: str = "powerfactory-comwr",
    app: Any | None = None,
) -> Path:
    """Write the sidecar that makes a final native PNG reviewable."""
    source = Path(path).resolve()
    if not source.is_file() or source.stat().st_size <= 0:
        raise RuntimeError("cannot bind provenance to a missing/empty native SLD PNG")
    geometry = _valid_sha256(geometry_sha256)
    if geometry is None:
        raise RuntimeError("cannot seal native SLD evidence without an exact canonical geometry SHA-256")
    render_contract = _valid_sha256(render_contract_sha256)
    if render_contract_sha256 is not None and render_contract is None:
        raise RuntimeError("cannot seal native SLD evidence with an invalid shared render contract SHA-256")
    if native_sld_mode != "command-layout+canonical-replay":
        raise RuntimeError("cannot seal native SLD evidence without canonical PowerFactory replay mode")
    metadata = _reject_known_fallback_renderer(source)
    identity = source_identity(_REPO_ROOT)
    payload: dict[str, Any] = {
        "schema": NATIVE_SLD_PROVENANCE_SCHEMA,
        "renderer": "powerfactory-native",
        "producer": producer,
        "artifact": source.name,
        "artifact_sha256": sha256_file(source),
        "geometry_sha256": geometry,
        "native_sld_mode": native_sld_mode,
        "page_name": page_name,
        "png_software": metadata.get("Software"),
        "source_revision": identity.get("source_revision"),
        "source_dirty": identity.get("source_dirty"),
        "cept_version": identity.get("cept_version"),
        "build_id": identity.get("build_id"),
    }
    if render_contract is not None:
        payload["render_contract_sha256"] = render_contract
    if producer == "powerfactory-comwr":
        readback = _powerfactory_readback_if_available(page_name, app)
        if readback is not None:
            payload["post_replay_readback"] = readback
    target = native_sld_provenance_path(source)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def _validate_release_readback(readback: Any) -> None:
    if not isinstance(readback, dict) or readback.get("passed") is not True:
        raise RuntimeError(
            "native SLD provenance is missing a passed PowerFactory post-replay geometry readback"
        )
    if readback.get("schema") != _READBACK_SCHEMA:
        raise RuntimeError("native SLD post-replay readback schema mismatch")
    if readback.get("coverage") != _READBACK_COVERAGE:
        raise RuntimeError("native SLD post-replay readback lacks complete finite-connector coverage")
    if int(readback.get("finding_count") or 0) != 0:
        raise RuntimeError("native SLD post-replay readback contains geometry findings")
    if int(readback.get("graphic_count") or 0) <= 0:
        raise RuntimeError("native SLD post-replay readback audited no PowerFactory graphics")
    if int(readback.get("connection_count") or 0) <= 0:
        raise RuntimeError("native SLD post-replay readback audited no finite connections")
    if int(readback.get("finite_point_count") or 0) <= 0:
        raise RuntimeError("native SLD post-replay readback audited no finite connector points")
    if int(readback.get("audited_segment_count") or 0) <= 0:
        raise RuntimeError("native SLD post-replay readback audited no real route segments")


def validate_native_sld_provenance(
    path: str | Path,
    *,
    provenance_path: str | Path | None = None,
    expected_geometry_sha256: str | None = None,
    expected_render_contract_sha256: str | None = None,
    expected_source_revision: str | None = None,
    require_clean_source: bool = False,
    require_render_contract: bool = False,
    require_post_replay_readback: bool = False,
) -> dict[str, Any]:
    """Verify that ``path`` is bound to a native PowerFactory export sidecar."""
    source = Path(path).resolve()
    _reject_known_fallback_renderer(source)
    sidecar = Path(provenance_path).resolve() if provenance_path else native_sld_provenance_path(source)
    if not sidecar.is_file():
        raise RuntimeError(f"native SLD provenance sidecar is missing: {sidecar}")
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    if payload.get("schema") != NATIVE_SLD_PROVENANCE_SCHEMA:
        raise RuntimeError("native SLD provenance schema mismatch")
    if payload.get("renderer") != "powerfactory-native" or payload.get("producer") != "powerfactory-comwr":
        raise RuntimeError("native SLD provenance does not identify a PowerFactory ComWr export")
    if payload.get("native_sld_mode") != "command-layout+canonical-replay":
        raise RuntimeError("native SLD provenance is not bound to canonical PowerFactory replay")
    geometry = _valid_sha256(payload.get("geometry_sha256"))
    if geometry is None:
        raise RuntimeError("native SLD provenance is missing an exact canonical geometry SHA-256")
    actual_hash = sha256_file(source)
    if payload.get("artifact_sha256") != actual_hash:
        raise RuntimeError("native SLD PNG hash does not match its provenance sidecar")
    if expected_geometry_sha256 is not None:
        expected = _valid_sha256(expected_geometry_sha256)
        if expected is None or geometry != expected:
            raise RuntimeError("native SLD geometry hash does not match the expected canonical geometry")
    render_contract = _valid_sha256(payload.get("render_contract_sha256"))
    if require_render_contract and render_contract is None:
        raise RuntimeError("native SLD provenance is missing the shared render contract SHA-256")
    if expected_render_contract_sha256 is not None:
        expected_contract = _valid_sha256(expected_render_contract_sha256)
        if expected_contract is None or render_contract != expected_contract:
            raise RuntimeError("native SLD render contract hash does not match the expected shared contract")
    if expected_source_revision is not None:
        expected_revision = _valid_revision(expected_source_revision)
        actual_revision = _valid_revision(payload.get("source_revision"))
        if expected_revision is None:
            raise RuntimeError("expected native SLD source revision is not an exact Git SHA")
        if actual_revision != expected_revision:
            raise RuntimeError(
                "native SLD provenance source revision does not match the qualified source revision"
            )
    if require_clean_source and payload.get("source_dirty") is not False:
        raise RuntimeError("native SLD provenance was not produced from a clean source tree")
    if require_post_replay_readback:
        _validate_release_readback(payload.get("post_replay_readback"))
    return payload


__all__ = [
    "NATIVE_SLD_PROVENANCE_SCHEMA",
    "native_sld_provenance_path",
    "png_text_metadata",
    "sha256_file",
    "validate_native_sld_provenance",
    "write_native_sld_provenance",
]
