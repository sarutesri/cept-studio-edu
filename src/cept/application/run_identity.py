"""Identity primitives for solver attempts and assessment artifacts.

A plan fingerprint answers "what execution was requested".  An attempt id
answers "which invocation wrote this run leaf".  Artifact and assessment
identities are derived separately so a cache key, a solver attempt, and a
validation assessment cannot be confused with one another.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Mapping


def patch_digest(patch: Mapping[str, Any]) -> str:
    """Hash an edit payload without depending on dictionary insertion order."""
    encoded = json.dumps(dict(patch), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def model_revision_id(parent_case_fingerprint: str, case_fingerprint: str, edit_digest: str) -> str:
    """Identify a typed child model revision derived from an editable action."""
    payload = {
        "parent_case_fingerprint": parent_case_fingerprint,
        "case_fingerprint": case_fingerprint,
        "edit_digest": edit_digest,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"cept-model-revision-{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:24]}"


def new_attempt_id() -> str:
    """Return an opaque id for one invocation, including repeated run paths."""
    return f"cept-attempt-{uuid.uuid4().hex}"


def artifact_set_digest(artifact_hashes: Mapping[str, str]) -> str:
    """Hash the named artifact set without including self-referential receipts."""
    payload = json.dumps(dict(sorted(artifact_hashes.items())), sort_keys=True, separators=(",", ":"))
    return f"cept-artifacts-{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def assessment_id(
    attempt_id: str,
    execution_key: str,
    result_hash: str,
    validation: Mapping[str, Any],
) -> str:
    """Derive an assessment identity from one attempt and its validation view."""
    payload = {
        "attempt_id": attempt_id,
        "execution_key": execution_key,
        "result_hash": result_hash,
        "validation": dict(validation),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"cept-assessment-{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:24]}"


__all__ = [
    "artifact_set_digest",
    "assessment_id",
    "model_revision_id",
    "new_attempt_id",
    "patch_digest",
]
