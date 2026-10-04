"""The optional experiment-diagnostics capability, owned by core.

An experiment-bound run's admissibility is decided by a research state machine
(``state``, ``classification``). That machine lives in ``cept_advanced.research``, which is
**not** shipped in the public wheel: the whole point of the public boundary is
that a public install can neither reach the research runtime nor vouch for a
verdict it did not compute.

The two requirements are therefore not in conflict, as long as core never names
``cept_advanced.research``:

- core declares the *slot* — the callable it needs, and the refusal it gives
  when nobody provides it;
- the research runtime provides the callable, at import time, in a checkout that
  has it installed.

So an internal install keeps full experiment validation and export, while a public
install refuses by name instead of silently degrading. Refusal is the default and
there is no fallback that guesses a verdict.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

__all__ = [
    "ExperimentDiagnostics",
    "ExperimentRuntimeUnavailable",
    "experiment_diagnostics",
    "experiment_diagnostics_available",
    "register_experiment_diagnostics",
    "registered_provider",
]


class ExperimentRuntimeUnavailable(RuntimeError):
    """No experiment-diagnostics provider is installed in this environment."""


ExperimentDiagnostics = Callable[[Path], Mapping[str, Any]]
"""Reads an experiment manifest and reports its state and classification."""

UNAVAILABLE_MESSAGE = (
    "experiment-bound validation and export need the CEPT research runtime, "
    "which is not installed in this package; refusing to derive an experiment "
    "verdict without it"
)

_PROVIDER: ExperimentDiagnostics | None = None


def register_experiment_diagnostics(provider: ExperimentDiagnostics) -> None:
    """Install the research implementation of this slot.

    Called by ``cept_advanced.research`` at import time. Registering a second, different
    provider is an error: two providers would mean two different verdicts for one
    manifest. Re-registering the same callable is a no-op so a repeated import
    cannot fail.
    """

    global _PROVIDER
    if _PROVIDER is not None and _PROVIDER is not provider:
        raise RuntimeError("experiment diagnostics provider is already registered")
    _PROVIDER = provider


def experiment_diagnostics_available() -> bool:
    """Whether this environment can decide an experiment verdict at all."""

    return _PROVIDER is not None


def registered_provider() -> ExperimentDiagnostics | None:
    """The installed provider, or ``None``. Exposed so tests can assert identity."""

    return _PROVIDER


def experiment_diagnostics(manifest_path: Path) -> Mapping[str, Any]:
    """Read ``manifest_path`` through the registered provider.

    Raises :class:`ExperimentRuntimeUnavailable` when no research runtime is
    installed. Callers must let that refusal stand; substituting a default state
    or classification would invent evidence.
    """

    if _PROVIDER is None:
        raise ExperimentRuntimeUnavailable(UNAVAILABLE_MESSAGE)
    return _PROVIDER(manifest_path)