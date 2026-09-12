"""Typed study intent (WP14) — acceptance predicates that live in the Case.

``cept.intent.schema`` is deliberately free of any grid-code import: the Case
schema embeds :class:`~cept.intent.schema.Intent`, and the grid-code package
depends on the Case schema, so importing ``cept.intent`` here must not pull in
that chain.  Evaluation (:mod:`cept.intent.evaluate`) imports the grid-code
evaluator and is loaded on demand.
"""

from cept.intent.schema import (
    Intent,
    IntentCheck,
    IntentConnection,
    IntentCriterion,
    IntentCriterionKind,
    IntentVerdict,
    ViolationRow,
)

__all__ = [
    "Intent",
    "IntentCheck",
    "IntentConnection",
    "IntentCriterion",
    "IntentCriterionKind",
    "IntentVerdict",
    "ViolationRow",
]
