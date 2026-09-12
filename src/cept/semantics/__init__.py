"""Reviewed semantics: what a raw name means, and which asset it belongs to.

Two files, one rule.  ``registry`` answers "what quantity is this channel?" and
``identity`` answers "which asset is this?".  Both answer ``None`` rather than
guessing, because an unproven mapping must become a blocked row instead of a
comparison.
"""

from cept.semantics.identity import (
    AssetIdentity,
    IdentityMap,
    build_identity_map,
    identity_from_case,
    load_identity_map,
    write_identity_map,
)
from cept.semantics.registry import (
    ChannelSemantics,
    registry_version,
    resolve_any,
    resolve_channel,
    unreviewed_channel,
)

__all__ = [
    "AssetIdentity",
    "ChannelSemantics",
    "IdentityMap",
    "build_identity_map",
    "identity_from_case",
    "load_identity_map",
    "registry_version",
    "resolve_any",
    "resolve_channel",
    "unreviewed_channel",
    "write_identity_map",
]
