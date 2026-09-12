"""Typed study intent (WP14) — an acceptance predicate that lives in the Case.

A Case types the network; an intent types the *question*.  An intent is a
declarative acceptance predicate over solver results: it names the criteria
that must hold and the connection context they apply to, and it is evaluated
against ``results.json`` by :mod:`cept.intent.evaluate`, which reuses the
existing grid-code criteria evaluation (:func:`cept.gridcode.evaluate_criteria`)
rather than inventing a second predicate language.

This module is deliberately free of any ``cept.gridcode`` import: the grid-code
package depends on the Case schema (via ``cept.validation.thai_grid_code``), so
the Case schema must stay importable without the grid-code chain.  The intent's
criteria mirror ``Criterion`` field-for-field; ``evaluate_intent`` converts
them into a ``GridCodeProfile`` and hands them to the shared evaluator, so the
*validation rules* and the *pass/fail semantics* are exactly the grid-code
ones.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


# Same criterion kinds as cept.gridcode.schema.Criterion.  Duplicated here
# (instead of imported) so the Case schema never imports the grid-code chain;
# the conversion in `cept.intent.evaluate` re-validates against Criterion, so a
# drift between the two Literals fails closed at evaluation time.
IntentCriterionKind = Literal[
    "convergence",
    "voltage_min",
    "voltage_max",
    "frequency_min",
    "frequency_max",
    "thd_max",
    "fault_current_max",
    "ramp_rate_max",
    "ride_through",
    "reactive_response",
    "flicker_max",
    "unbalance_max",
    "protection",
    "islanding",
    "custom",
]

_MIN_KINDS = frozenset({"voltage_min", "frequency_min"})
_MAX_KINDS = frozenset(
    {
        "voltage_max",
        "frequency_max",
        "thd_max",
        "fault_current_max",
        "ramp_rate_max",
        "flicker_max",
        "unbalance_max",
    }
)

_ASSET_TYPES = Literal[
    "synchronous_generator",
    "pv",
    "wind",
    "bess",
    "hybrid_ibr",
    "data_center",
    "microgrid",
    "vpp",
    "der_fleet",
    "other",
]


class IntentCriterion(BaseModel):
    """One acceptance limit of the typed study question.

    Field-for-field mirror of ``cept.gridcode.schema.Criterion`` (minus the
    applicability rule, which is a grid-code ingestion concept and is always
    "applies" for an intent).  ``status`` defaults to ``draft`` so a criterion
    that was never explicitly marked verified fails closed as
    ``needs_source`` during evaluation, exactly like the grid-code path.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    label: str
    kind: IntentCriterionKind
    limit: Optional[float] = None
    unit: str = ""
    direction: Literal["min", "max", "boolean"] = "boolean"
    source_ref: str = Field(..., min_length=1, description="Page/section/clause reference.")
    scope: Literal["all_buses", "pcc"] = "all_buses"
    status: Literal["draft", "verified"] = "draft"

    @model_validator(mode="after")
    def _limit_for_numeric(self) -> "IntentCriterion":
        numeric = _MIN_KINDS | _MAX_KINDS
        if self.kind in numeric and self.limit is None:
            raise ValueError(f"intent criterion '{self.id}' requires a numeric limit")
        if self.kind in _MIN_KINDS and self.direction != "min":
            raise ValueError(f"intent criterion '{self.id}' kind '{self.kind}' requires direction='min'")
        if self.kind in _MAX_KINDS and self.direction != "max":
            raise ValueError(f"intent criterion '{self.id}' kind '{self.kind}' requires direction='max'")
        if self.kind not in numeric and self.direction != "boolean":
            raise ValueError(f"intent criterion '{self.id}' kind '{self.kind}' requires direction='boolean'")
        if self.kind in {"voltage_min", "voltage_max"} and self.unit not in {"pu", "kV"}:
            raise ValueError(f"intent criterion '{self.id}' voltage unit must be pu or kV")
        return self


class IntentConnection(BaseModel):
    """Connection context the intent criteria are evaluated against.

    Field-for-field mirror of ``cept.gridcode.schema.ConnectionContext``.
    """

    model_config = ConfigDict(extra="forbid")

    utility: str
    jurisdiction: str
    code_version: str
    asset_type: _ASSET_TYPES
    pcc_kv: float = Field(..., gt=0)
    pcc_bus: Optional[str] = None
    capacity_mva: float = Field(..., ge=0)
    import_export: Literal["import", "export", "bidirectional"] = "import"
    connection_category: str = "unknown"
    operating_modes: list[str] = Field(default_factory=list)


class Intent(BaseModel):
    """One typed research question, stored declaratively in the Case.

    Example: ``"Bus 7 stays above 0.95 pu under every declared N-1
    contingency across all summer-peak hours"`` becomes a
    ``voltage_min`` criterion with ``limit=0.95``, ``scope='pcc'``,
    ``pcc_bus='Bus 7'``, evaluated against every row the solver produced.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., pattern=r"^[A-Za-z0-9_.-]+$")
    description: str = Field("", description="The research question in prose.")
    criteria: list[IntentCriterion] = Field(..., min_length=1)
    connection: IntentConnection

    @model_validator(mode="after")
    def _unique_criterion_ids(self) -> "Intent":
        ids = [criterion.id for criterion in self.criteria]
        if len(ids) != len(set(ids)):
            raise ValueError("intent criterion ids must be unique")
        return self


# --------------------------------------------------------------------------- #
# Verdict over solver results (produced by cept.intent.evaluate)
# --------------------------------------------------------------------------- #


class ViolationRow(BaseModel):
    """One solver row that decided a FAIL verdict."""

    model_config = ConfigDict(extra="forbid")

    bus: Optional[str] = None
    phase: Optional[int] = None
    t_s: Optional[float] = None
    observed: float
    limit: float
    unit: str = ""


class IntentCheck(BaseModel):
    """Evidence row for one intent criterion: its verdict plus the rows behind it."""

    model_config = ConfigDict(extra="forbid")

    criterion_id: str
    label: str = ""
    status: Literal["pass", "fail", "blocked", "needs_source"]
    observed: Optional[float] = None
    limit: Optional[float] = None
    unit: str = ""
    source_ref: str = ""
    reason: str = ""
    study_type: str = ""
    engine: str = ""
    violations: list[ViolationRow] = Field(default_factory=list)


class IntentVerdict(BaseModel):
    """Verdict for one intent plus the evidence rows that decided it."""

    model_config = ConfigDict(extra="forbid")

    intent_id: str
    description: str = ""
    verdict: Literal["pass", "fail", "blocked", "needs_source"]
    passed: bool
    reason: str = ""
    checks: list[IntentCheck] = Field(default_factory=list)
    failing_criterion_ids: list[str] = Field(default_factory=list)
    case_fingerprint: str = ""
    case_name: str = ""
    study_type: str = ""
    engine: str = ""
    calculation_method: str = ""


__all__ = [
    "Intent",
    "IntentCheck",
    "IntentConnection",
    "IntentCriterion",
    "IntentCriterionKind",
    "IntentVerdict",
    "ViolationRow",
]
