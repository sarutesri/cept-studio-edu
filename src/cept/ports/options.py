"""Run options carried from the domain core to an engine adapter.

``RunOptions`` is the union of today's divergent per-engine keyword
arguments.  A per-engine option hierarchy would reintroduce ``isinstance``
one level up, so the port stays flat: every adapter ignores the keys it
does not use.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional


class Solver(str, Enum):
    """The solver lanes a Case may run on (WP11/WP18 boundary).

    ``native`` is the engine's own solver; ``ybus-nr`` is the explainable
    OpenDSS polar Newton-Raphson lane.  ``RunOptions.solver`` accepts plain
    strings for back-compat (call sites and JSON still say ``"ybus-nr"``);
    the enum removes the magic string from new code.
    """

    NATIVE = "native"
    YBUS_NR = "ybus-nr"


@dataclass(frozen=True)
class RunOptions:
    """Everything a solver invocation may need beyond the Case itself."""

    extra_commands: Optional[Iterable[str]] = None
    solver: Solver | str = Solver.NATIVE
    preserve_project: bool = False
    model_package_paths: Optional[Iterable[str]] = None


def as_solver(value: str | Solver) -> Solver:
    """Coerce a solver lane name to the typed constant, failing on typos."""
    if isinstance(value, Solver):
        return value
    try:
        return Solver(value)
    except ValueError:
        raise ValueError(
            f"Unknown solver lane '{value}' (expected one of {[s.value for s in Solver]})."
        ) from None


__all__ = ["RunOptions", "Solver", "as_solver"]
