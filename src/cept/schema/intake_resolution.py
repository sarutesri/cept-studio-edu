"""Typed review contract for incomplete or AI-assisted Case intake.

This module is deliberately separate from :mod:`cept.schema.case`. The
canonical ``Case`` remains the only solver-facing study model; this ledger
records how uncertain intake fields were resolved before a Case is built.

The ledger never makes an engineering value true. It only records whether a
value came from a source, was derived from source data, remains unresolved, or
was explicitly accepted as a documented/default or AI-selected demonstrator
assumption.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ResolutionPolicy = Literal["strict", "assisted", "exploratory"]
ResolutionStatus = Literal[
    "source",
    "derived",
    "unresolved",
    "documented_default",
    "ai_selected_assumption",
]


class CaseInfoResolutionItem(BaseModel):
    """One reviewable decision about a Case input field."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(..., min_length=1)
    status: ResolutionStatus
    value: Any | None = None
    source_ref: str | None = Field(default=None, min_length=1)
    reason: str | None = Field(default=None, min_length=1)
    default_id: str | None = Field(default=None, min_length=1)
    approved_by_user: bool = False

    @model_validator(mode="after")
    def _check_status_contract(self) -> "CaseInfoResolutionItem":
        if self.status == "unresolved":
            if self.value is not None:
                raise ValueError("unresolved items must not carry a value")
            if self.approved_by_user:
                raise ValueError("unresolved items cannot be approved as resolved")
            return self

        if self.value is None:
            raise ValueError(f"{self.status} items require a value")

        if self.status == "source":
            if not self.source_ref:
                raise ValueError("source items require source_ref")
        elif self.status == "derived":
            if not self.source_ref or not self.reason:
                raise ValueError("derived items require source_ref and reason")
        elif self.status == "documented_default":
            if not self.default_id or not self.reason:
                raise ValueError("documented_default items require default_id and reason")
            if not self.approved_by_user:
                raise ValueError("documented_default items require explicit user approval")
        elif self.status == "ai_selected_assumption":
            if not self.reason:
                raise ValueError("ai_selected_assumption items require a reason")
            if not self.approved_by_user:
                raise ValueError("ai_selected_assumption items require explicit user approval")
        return self


class CaseInfoResolutionLedger(BaseModel):
    """Review record that sits before the canonical typed ``Case``.

    ``strict`` accepts only source/derived values.
    ``assisted`` additionally permits documented defaults after explicit user
    approval. ``exploratory`` additionally permits AI-selected assumptions
    after explicit user approval. Every policy blocks unresolved fields.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_id: Literal["cept-case-info-resolution-v1"] = Field(
        default="cept-case-info-resolution-v1",
        alias="schema",
    )
    policy: ResolutionPolicy = "strict"
    items: list[CaseInfoResolutionItem] = Field(..., min_length=1)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_fields(self) -> "CaseInfoResolutionLedger":
        fields = [item.field for item in self.items]
        if len(fields) != len(set(fields)):
            raise ValueError("case-info resolution fields must be unique")
        return self

    def blockers(self, *, research: bool = False) -> list[CaseInfoResolutionItem]:
        """Return items that make this ledger unsafe to use for Case intake."""
        blocked: list[CaseInfoResolutionItem] = []
        for item in self.items:
            if item.status == "unresolved":
                blocked.append(item)
                continue
            if research and item.status in {"documented_default", "ai_selected_assumption"}:
                blocked.append(item)
                continue
            if self.policy == "strict" and item.status in {
                "documented_default",
                "ai_selected_assumption",
            }:
                blocked.append(item)
            elif self.policy == "assisted" and item.status == "ai_selected_assumption":
                blocked.append(item)
        return blocked

    def assert_ready_for_ingest(self, *, research: bool = False) -> None:
        """Fail closed unless every recorded resolution is allowed by policy."""
        blocked = self.blockers(research=research)
        if not blocked:
            return
        detail = ", ".join(f"{item.field}={item.status}" for item in blocked[:8])
        if research:
            raise ValueError(
                "research intake requires source-backed/derived values only; "
                f"resolve these items from evidence before ingest: {detail}"
            )
        raise ValueError(
            f"case-info resolution policy '{self.policy}' blocks intake: {detail}. "
            "Provide the missing source data or explicitly choose an allowed, approved resolution."
        )
