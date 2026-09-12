"""Asset identity written by the adapter that created the object.

The comparator used to recover engine naming with regular expressions -- strip
``Line.``/``Transformer.`` prefixes, squash punctuation, hope the two sides
land on the same string.  That produced pairs, but not evidence: a match meant
"these names look alike", and a near-miss silently became a missing-counterpart
blocker (272 of 457 rows on one 39-bus run).

The adapter does not have to guess.  At materialization time it knows that Case
asset ``gen_0002`` became ``Generator.gen_0002`` in OpenDSS and came from source
object ``G2``.  This module is where that knowledge is written down, so the
comparator can look it up instead of re-deriving it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from cept.ports.engine import AssetIdentityRecord, SupportsIdentity
from cept.semantics.registry import registry_version

IDENTITY_MAP_FILENAME = "identity-map.json"
IDENTITY_MAP_SCHEMA = "cept-identity-map-v1"


@dataclass(frozen=True)
class AssetIdentity:
    """One asset, named in every vocabulary that will ever refer to it."""

    canonical_id: str
    canonical_class: str
    engine_native_name: str = ""
    source_name: str = ""
    source_full_name: str = ""
    # Extra spellings the engine is known to report for the same object -- for
    # example OpenDSS, whose names are case-insensitive and come back
    # lowercased.  Declaring them keeps the comparator's lookup exact instead
    # of pushing case folding back into the join.
    aliases: tuple[str, ...] = ()
    mapping_method: str = "adapter-materialization"
    review_status: str = "reviewed"

    def as_dict(self) -> dict[str, Any]:
        return {
            "canonical_id": self.canonical_id,
            "canonical_class": self.canonical_class,
            "engine_native_name": self.engine_native_name,
            "source_name": self.source_name,
            "source_full_name": self.source_full_name,
            "aliases": list(self.aliases),
            "mapping_method": self.mapping_method,
            "review_status": self.review_status,
        }


@dataclass
class IdentityMap:
    """Lookup from any known alias of an asset to its canonical id."""

    engine: str = ""
    case_fingerprint: str = ""
    registry_version: str = ""
    assets: list[AssetIdentity] = field(default_factory=list)
    _index: dict[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.reindex()

    def reindex(self) -> None:
        index: dict[str, str] = {}
        for asset in self.assets:
            for alias in (
                asset.canonical_id,
                asset.engine_native_name,
                asset.source_name,
                asset.source_full_name,
                *asset.aliases,
            ):
                alias = str(alias or "").strip()
                if not alias:
                    continue
                # A canonical id always wins an alias collision; otherwise a
                # source object that happens to share a name with a different
                # canonical asset could redirect its rows.
                if alias in index and index[alias] != asset.canonical_id:
                    if alias == asset.canonical_id:
                        index[alias] = asset.canonical_id
                    continue
                index[alias] = asset.canonical_id
        self._index = index

    def resolve(self, name: str) -> str | None:
        """Canonical id for any recorded alias, or ``None`` when unrecorded.

        ``None`` is a real answer: it means no adapter ever claimed this name,
        so pairing it with anything would be a guess.
        """
        name = str(name or "").strip()
        if not name:
            return None
        direct = self._index.get(name)
        if direct is not None:
            return direct
        # OpenDSS records a monitored element as ``Generator.gen_0002``.  The
        # class-qualified form is an exact, documented OpenDSS convention, not
        # a name-similarity heuristic, so splitting on the single separator is
        # a lookup rather than a guess.
        if "." in name:
            return self._index.get(name.split(".", 1)[1])
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": IDENTITY_MAP_SCHEMA,
            "engine": self.engine,
            "case_fingerprint": self.case_fingerprint,
            "registry_version": self.registry_version or registry_version(),
            "assets": [asset.as_dict() for asset in self.assets],
        }


def build_identity_map(
    engine: str,
    case_fingerprint: str,
    assets: Iterable[AssetIdentityRecord],
) -> IdentityMap:
    """Freeze adapter-declared structural identity records into semantic records."""
    frozen = [
        asset
        if isinstance(asset, AssetIdentity)
        else AssetIdentity(
            canonical_id=asset.canonical_id,
            canonical_class=asset.canonical_class,
            engine_native_name=asset.engine_native_name,
            source_name=asset.source_name,
            source_full_name=asset.source_full_name,
            aliases=tuple(asset.aliases),
            mapping_method=asset.mapping_method,
            review_status=asset.review_status,
        )
        for asset in assets
    ]
    return IdentityMap(
        engine=engine,
        case_fingerprint=case_fingerprint,
        registry_version=registry_version(),
        assets=frozen,
    )


def write_identity_map(run_dir: str | Path, identity_map: IdentityMap) -> Path:
    path = Path(run_dir) / IDENTITY_MAP_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(identity_map.as_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def load_identity_map(run_dir: str | Path) -> IdentityMap | None:
    """Read an adapter-written identity map, or ``None`` when absent.

    Absent is not an error here.  It is reported by the caller as an unmapped
    role so that artifacts produced before adapters emitted this file are
    visibly unproven rather than quietly compared by name.
    """
    path = Path(run_dir) / IDENTITY_MAP_FILENAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("schema") != IDENTITY_MAP_SCHEMA:
        return None
    assets = [
        AssetIdentity(
            canonical_id=str(item.get("canonical_id", "")),
            canonical_class=str(item.get("canonical_class", "")),
            engine_native_name=str(item.get("engine_native_name", "")),
            source_name=str(item.get("source_name", "")),
            source_full_name=str(item.get("source_full_name", "")),
            aliases=tuple(str(alias) for alias in item.get("aliases", []) if alias),
            mapping_method=str(item.get("mapping_method", "adapter-materialization")),
            review_status=str(item.get("review_status", "reviewed")),
        )
        for item in payload.get("assets", [])
        if isinstance(item, dict) and item.get("canonical_id")
    ]
    return IdentityMap(
        engine=str(payload.get("engine", "")),
        case_fingerprint=str(payload.get("case_fingerprint", "")),
        registry_version=str(payload.get("registry_version", "")),
        assets=assets,
    )


def publish_identity_map(run_dir: str | Path, case: Any, adapter: Any) -> Path:
    """Write the identity map for a run, asking the adapter that built it.

    Every path that produces a run directory goes through here, so a run
    generated by the benchmark harness carries the same identity evidence as
    one produced by the CLI.  When they did not, the benchmark's own candidate
    roles compared by bare name and reported reviewed source channels as
    missing counterparts.
    """
    engine = str(getattr(adapter, "engine", "") or getattr(case, "engine", "") or "")
    identities = None
    if isinstance(adapter, SupportsIdentity):
        try:
            identities = list(adapter.asset_identities(case))
        except Exception:
            identities = None
    if identities:
        try:
            fingerprint = str(case.fingerprint())
        except Exception:
            fingerprint = ""
        identity_map = build_identity_map(engine, fingerprint, identities)
    else:
        identity_map = identity_from_case(case, engine)
    return write_identity_map(run_dir, identity_map)


def identity_from_case(case: Any, engine: str) -> IdentityMap:
    """Derive the canonical asset inventory from a typed Case.

    Adapters enrich the result with their own native names; this gives every
    adapter the same starting point and keeps the canonical class vocabulary in
    one place.
    """
    inline = None
    network = getattr(case, "network", None)
    if network is not None and getattr(network, "kind", "") == "inline":
        inline = getattr(network, "inline", None)
    assets: list[AssetIdentity] = []
    if inline is not None:
        for attribute, canonical_class in (
            ("buses", "bus"),
            ("lines", "line"),
            ("transformers", "transformer"),
            ("loads", "load"),
            ("generators", "sync_generator"),
        ):
            for item in getattr(inline, attribute, None) or []:
                name = str(getattr(item, "name", "") or "")
                if not name:
                    continue
                assets.append(AssetIdentity(canonical_id=name, canonical_class=canonical_class))
    fingerprint = ""
    try:
        fingerprint = str(case.fingerprint())
    except Exception:
        fingerprint = ""
    return build_identity_map(engine, fingerprint, assets)


__all__ = [
    "AssetIdentity",
    "IDENTITY_MAP_FILENAME",
    "IDENTITY_MAP_SCHEMA",
    "IdentityMap",
    "build_identity_map",
    "identity_from_case",
    "load_identity_map",
    "write_identity_map",
]
