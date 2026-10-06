"""Deterministic run-result document for the workflow-recipe runner.

``recipe-run.json`` is the only record of what a recipe run actually did: exact
stage results, exit codes, artifact hashes, and the transition each stage took.
It is written fail-closed: ``passed`` defaults to ``False`` and can only become
``True`` when every required stage completed, every expected artifact is
present, and a stored validation artifact states a literal pass.

Every recipe stage runs in process, so ``command_line`` is always ``None``. The
field stays in the document contract because a receipt reader asks whether the
stage executed a command or an in-process operation, and ``None`` answers that
positively rather than by omission.

See :mod:`cept.recipes.schema` for the ``workflow-recipe-v1`` document that
produced the run.
"""

from __future__ import annotations

import hashlib
import json
import warnings
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from cept.util import require_distinct_output, sha256_file, write_json
from cept.verification.run_set import verify_run_set

RUN_RESULT_SCHEMA_ID: Literal["workflow-recipe-run-v1"] = "workflow-recipe-run-v1"

StageStatus = Literal["completed", "failed", "skipped", "blocked"]
RunStatus = Literal["completed", "failed", "blocked"]

#: A run result is never valid without an explicit claim string.
_UNSET_CLAIM = ""

#: The one claim level that asserts a verified workflow. It is earned by literal
#: verification evidence, so the model refuses it while ``passed`` is false and
#: refuses every other claim level while ``passed`` is true. The runner composes
#: it from the evidence it read (``CLAIM_PASSED`` there); the string itself is
#: part of this document's contract, not a runner-local detail.
CLAIM_VERIFIED = "workflow-verified"

#: Every recorded transition reads ``<label>.<decision>=<target>``.
_FAILURE_EDGE_PREFIX = "on_failure."

#: The decision name a runner falls back to when the recipe declared no named
#: failure transition at all.
_SYNTHESIZED_DECISION = "next"

#: The recorded target of a declared decision that routes nowhere.
_NO_TARGET = "none"


def _declared_failure_edge(next_transition: str | None) -> tuple[str, str] | None:
    """Return ``(decision, target)`` for a recorded declared failure edge.

    ``None`` means the recorded transition is not a failure edge at all: a
    success edge (``on_success...``), an absent transition, or a name that
    carries no decision.
    """
    if not next_transition:
        return None
    label, separator, target = next_transition.partition("=")
    if not separator or not label.startswith(_FAILURE_EDGE_PREFIX):
        return None
    decision = label[len(_FAILURE_EDGE_PREFIX) :].strip()
    if not decision:
        return None
    return decision, target.strip()


class StageResult(BaseModel):
    """One executed (or deliberately skipped) recipe stage."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stage_id: str
    operation: str
    status: StageStatus
    inputs: dict[str, str] = Field(default_factory=dict)
    outputs: list[str] = Field(default_factory=list)
    command_line: list[str] | None = None
    exit_code: int | None = None
    stderr: str | None = None
    artifact_hashes: dict[str, str] = Field(default_factory=dict)
    next_transition: str | None = None
    started_at: str
    finished_at: str
    #: True when the recipe declared this stage ``optional: true``, so that a
    #: declared failure/blocked transition is a recorded state rather than a
    #: veto of the whole run.
    optional: bool = False
    #: True only when this stage reached a *real declared* failure/blocked
    #: decision: it is optional, it did not complete, and the recorded
    #: ``next_transition`` is a declared failure edge naming a decision
    #: (``on_failure.<decision>=<target>``).
    #:
    #: It deliberately does NOT cover a stage that decided nothing: a skipped
    #: stage reached no decision, a stage whose only recorded edge is the
    #: runner's synthesized ``on_failure.next=none`` stand-in for "the recipe
    #: declared no route here" did not decide either, and an absent transition
    #: records nothing at all. Those stages still veto the run verdict.
    did_not_veto: bool = False

    @model_validator(mode="after")
    def _status_is_honest(self) -> "StageResult":
        if not self.stage_id.strip():
            raise ValueError("stage_id: must be a non-empty stage id")
        if not self.operation.strip():
            raise ValueError("operation: must be a non-empty registered operation id")
        if self.status == "completed":
            if self.exit_code != 0:
                raise ValueError(
                    f"stage_id {self.stage_id}: status 'completed' requires exit_code 0, "
                    f"got {self.exit_code}"
                )
        elif self.exit_code == 0:
            raise ValueError(
                f"stage_id {self.stage_id}: status {self.status!r} must not report exit_code 0"
            )
        for name, digest in self.artifact_hashes.items():
            if not name.strip():
                raise ValueError(f"stage_id {self.stage_id}.artifact_hashes: empty artifact path")
            if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
                raise ValueError(
                    f"stage_id {self.stage_id}.artifact_hashes.{name}: "
                    "value must be a lowercase sha256 hex digest"
                )
        if self.did_not_veto and not self.optional:
            raise ValueError(
                f"stage_id {self.stage_id}: did_not_veto requires the stage to be optional"
            )
        if self.did_not_veto and self.status == "completed":
            raise ValueError(
                f"stage_id {self.stage_id}: a completed stage cannot be recorded as not vetoing"
            )
        if self.did_not_veto and self.status == "skipped":
            raise ValueError(
                f"stage_id {self.stage_id}: a skipped stage reached no declared "
                "failure/blocked decision, so it cannot be recorded as not vetoing"
            )
        if self.did_not_veto:
            edge = _declared_failure_edge(self.next_transition)
            if edge is None:
                raise ValueError(
                    f"stage_id {self.stage_id}: did_not_veto requires a recorded declared "
                    "failure/blocked transition 'on_failure.<decision>=<target>'; got "
                    f"{self.next_transition!r}"
                )
            decision, target = edge
            if not target:
                raise ValueError(
                    f"stage_id {self.stage_id}: did_not_veto requires the declared "
                    f"transition {self.next_transition!r} to name its target"
                )
            if decision == _SYNTHESIZED_DECISION and target.lower() == _NO_TARGET:
                raise ValueError(
                    f"stage_id {self.stage_id}: did_not_veto requires a real declared "
                    f"failure/blocked decision; {self.next_transition!r} is the synthesized "
                    "'no route was declared' edge, not a decision by this stage"
                )
        return self


class VerificationEvidence(BaseModel):
    """The exact declared output read by a completed study verification."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stage_id: str = Field(min_length=1)
    operation: Literal["verify.study"]
    artifact: str = Field(min_length=1)
    artifact_path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    passed: Literal[True]

    @field_validator("passed", mode="before")
    @classmethod
    def _literal_pass(cls, value: object) -> object:
        if value is not True:
            raise ValueError("verification evidence requires literal passed=true")
        return value

    @model_validator(mode="after")
    def _absolute_artifact(self) -> "VerificationEvidence":
        if not self.stage_id.strip() or not self.artifact.strip():
            raise ValueError("verification evidence requires a non-empty stage and artifact")
        if not Path(self.artifact_path).is_absolute():
            raise ValueError("verification evidence artifact_path must be absolute")
        return self


# Same intentional discriminator-field name as `Recipe.schema`.
warnings.filterwarnings(
    "ignore",
    message='Field name "schema" in "RecipeRunResult" .*',
    category=UserWarning,
)

class RecipeRunResult(BaseModel):
    """The complete ``recipe-run.json`` document for one recipe run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Same as `Recipe.schema`: the contract names the discriminator `schema`.
    schema: Literal["workflow-recipe-run-v1"]  # type: ignore[assignment]
    recipe_id: str
    recipe_version: str
    recipe_path: str
    recipe_sha256: str
    run_id: str
    started_at: str
    finished_at: str
    stages: list[StageResult]
    expected_artifact_root: str
    expected_artifacts: dict[str, str] = Field(default_factory=dict)
    missing_expected_artifacts: list[str] = Field(default_factory=list)
    verification_evidence: VerificationEvidence | None = None
    required_stage_ids: list[str] = Field(default_factory=list)
    included_recipe_hashes: dict[str, str] = Field(default_factory=dict)
    status: RunStatus
    passed: StrictBool = False
    blocked_reason: str | None = None
    claim: str = _UNSET_CLAIM

    @model_validator(mode="after")
    def _passed_requires_literal_evidence(self) -> "RecipeRunResult":
        if not self.claim.strip():
            raise ValueError("claim: must record the earned claim level; it is never auto-promoted")
        if len(self.recipe_sha256) != 64:
            raise ValueError("recipe_sha256: must be the raw recipe file sha256 hex digest")
        if not Path(self.expected_artifact_root).is_absolute():
            raise ValueError("expected_artifact_root: must be an absolute artifact directory")
        stage_ids = [stage.stage_id for stage in self.stages]
        if len(set(stage_ids)) != len(stage_ids):
            raise ValueError("stages: a stage may appear at most once in a run result")
        if self.status == "blocked" and not (self.blocked_reason or "").strip():
            raise ValueError("blocked_reason: required when status is 'blocked'")
        if self.passed:
            if self.status != "completed":
                raise ValueError(
                    f"passed: cannot be true while status is {self.status!r}"
                )
            if self.missing_expected_artifacts:
                raise ValueError(
                    "passed: cannot be true with missing expected artifacts: "
                    + ", ".join(self.missing_expected_artifacts)
                )
            # A stage that reached a *declared* optional failure/blocked
            # decision is a recorded state, not an incomplete one: "compare
            # only when the recipe declares comparison" must not veto the
            # workflow forever on a host without a licensed second engine.
            # Everything else still vetoes -- including a `skipped` stage,
            # which executed nothing at all and so verifies nothing.
            incomplete = [
                stage.stage_id
                for stage in self.stages
                if stage.status != "completed" and not stage.did_not_veto
            ]
            if incomplete:
                raise ValueError(
                    "passed: cannot be true while stages are incomplete: " + ", ".join(incomplete)
                )
            if not self.stages:
                raise ValueError(
                    "passed: cannot be true with no stages recorded; a run that executed "
                    "nothing verified nothing"
                )
            if not self.verified_stage_ids():
                raise ValueError(
                    "passed: cannot be true unless at least one stage completed; a run of "
                    "skipped or abandoned stages verified nothing"
                )
            if not any(
                stage.artifact_hashes for stage in self.stages if stage.status == "completed"
            ):
                raise ValueError(
                    "passed: cannot be true with no recorded artifact evidence: every "
                    "completed stage recorded no hashed output, so nothing was read to verify"
                )
            if not self.required_stage_ids or any(
                not stage_id.strip() for stage_id in self.required_stage_ids
            ):
                raise ValueError("passed: requires non-empty required_stage_ids")
            if len(set(self.required_stage_ids)) != len(self.required_stage_ids):
                raise ValueError("required_stage_ids: a stage may be required at most once")
            completed = {
                stage.stage_id: stage for stage in self.stages if stage.status == "completed"
            }
            missing_required = [
                stage_id for stage_id in self.required_stage_ids if stage_id not in completed
            ]
            if missing_required:
                raise ValueError(
                    "passed: required stages did not complete: " + ", ".join(missing_required)
                )
            if not self.expected_artifacts or any(
                not name.strip()
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
                for name, digest in self.expected_artifacts.items()
            ):
                raise ValueError("passed: requires non-empty hashed expected artifact evidence")
            evidence = self.verification_evidence
            if evidence is None:
                raise ValueError("passed: requires explicit verify.study verification_evidence")
            stage = completed.get(evidence.stage_id)
            if stage is None or stage.operation != evidence.operation:
                raise ValueError("passed: verification evidence must name a completed verify.study")
            if (
                evidence.artifact not in stage.outputs
                or stage.artifact_hashes.get(evidence.artifact) != evidence.sha256
                or self.expected_artifacts.get(evidence.artifact) != evidence.sha256
            ):
                raise ValueError(
                    "passed: verification evidence must match its declared output, stage digest "
                    "and expected artifact digest"
                )
        claimed_verified = self.claim.startswith(CLAIM_VERIFIED)
        if self.passed and not claimed_verified:
            raise ValueError(
                f"claim: a passed run must state the {CLAIM_VERIFIED!r} claim it earned; got "
                f"{self.claim!r}"
            )
        if claimed_verified and not self.passed:
            raise ValueError(
                f"claim: {CLAIM_VERIFIED!r} may not be claimed while passed is false; got "
                f"{self.claim!r}"
            )
        return self

    def verified_stage_ids(self) -> tuple[str, ...]:
        """Stage ids whose execution evidence is a literal completed result."""
        return tuple(stage.stage_id for stage in self.stages if stage.status == "completed")


def write_run_result(path: Path, result: RecipeRunResult) -> None:
    """Atomically publish a revalidated receipt, checking live evidence for a pass."""
    result = RecipeRunResult.model_validate(result.model_dump(mode="python"))
    path = Path(path)
    root = Path(result.expected_artifact_root)
    recipe_path = Path(result.recipe_path).resolve()
    protected = [recipe_path, *(recipe_path.parent / name for name in result.included_recipe_hashes)]
    protected.extend(root / name for name in result.expected_artifacts)
    protected.extend(root / name for name in result.missing_expected_artifacts)
    run_dirs = {root}
    for stage in result.stages:
        if stage.operation == "write.receipt":
            continue
        stage_root = Path(stage.inputs.get("run_dir", str(root)))
        run_dirs.add(stage_root)
        protected.extend(stage_root / name for name in stage.outputs)
        protected.extend(Path(value) for value in stage.inputs.values() if Path(value).is_file())
    # Existing completion receipts may be regenerated; other run files are
    # source evidence, even if they were not part of the canonical solver gate.
    existing_receipt = False
    if path.is_file():
        try:
            payload = json.loads(path.read_bytes())
            existing_receipt = isinstance(payload, dict) and payload.get("schema") == RUN_RESULT_SCHEMA_ID
        except (OSError, ValueError):
            pass
    protected.extend(
        entry for directory in run_dirs if directory.is_dir()
        and ((directory / "manifest.json").is_file() or (directory / "results.json").is_file())
        for entry in directory.rglob("*") if entry.is_file()
        and not (existing_receipt and entry.resolve() == path.resolve())
    )
    require_distinct_output(path, protected)
    if result.passed:
        evidence = result.verification_evidence
        assert evidence is not None  # Guaranteed by model revalidation above.
        artifact_path = Path(evidence.artifact_path)
        try:
            raw = artifact_path.read_bytes()
            payload = json.loads(raw)
            if hashlib.sha256(raw).hexdigest() != evidence.sha256:
                raise ValueError("verification artifact digest changed since verification")
            if not isinstance(payload, dict) or payload.get("passed") is not True:
                raise ValueError("verification artifact does not report literal passed=true")
            if verify_run_set([artifact_path.parent]).get("passed") is not True:
                raise ValueError("current run artifacts do not pass canonical verification")
            if sha256_file(artifact_path) != evidence.sha256:
                raise ValueError("verification artifact changed during verification")
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read verification artifact: {artifact_path}") from exc
        if not recipe_path.is_file() or sha256_file(recipe_path) != result.recipe_sha256:
            raise ValueError("root recipe bytes no longer match the receipt")
        for name, digest in result.included_recipe_hashes.items():
            child = recipe_path.parent / name
            if not child.is_file() or sha256_file(child) != digest:
                raise ValueError(f"included recipe bytes no longer match the receipt: {name}")
        for name, digest in result.expected_artifacts.items():
            artifact = root / name
            if not artifact.is_file() or sha256_file(artifact) != digest:
                raise ValueError(f"expected artifact is missing or changed since verification: {name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, result.model_dump(mode="json"))


__all__ = [
    "CLAIM_VERIFIED",
    "RUN_RESULT_SCHEMA_ID",
    "RecipeRunResult",
    "RunStatus",
    "StageResult",
    "StageStatus",
    "VerificationEvidence",
    "write_run_result",
]