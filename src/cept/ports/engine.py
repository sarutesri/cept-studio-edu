"""The engine boundary of CEPT: one primary port, three optional ports.

``EngineAdapter`` is the whole contract the domain core needs to run a
study.  The optional ``Supports*`` ports replace the ``getattr`` duck-typing
that used to exist at ``semantics/identity.py`` and
``validation/benchmark.py``: an adapter that does not implement them simply
keeps the fallback behavior.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Protocol, runtime_checkable

from cept.ports.options import RunOptions
from cept.schema.case import Case
from cept.schema.result import StudyResult


@runtime_checkable
class EngineAdapter(Protocol):
    """The primary engine port.

    ``engine`` and ``version`` identify the solver; ``run`` translates the
    Case into solver input, executes it, and returns engine-agnostic results.
    """

    engine: str
    version: str

    def run(self, case: Case, options: RunOptions) -> StudyResult: ...


@runtime_checkable
class AssetIdentityRecord(Protocol):
    """Engine-neutral structural shape of one adapter-declared asset identity.

    The concrete immutable record lives in :mod:`cept.semantics.identity`.
    Keeping only this structural contract in ``ports`` preserves dependency
    direction: the engine boundary never imports semantic implementation code.
    """

    @property
    def canonical_id(self) -> str: ...

    @property
    def canonical_class(self) -> str: ...

    @property
    def engine_native_name(self) -> str: ...

    @property
    def source_name(self) -> str: ...

    @property
    def source_full_name(self) -> str: ...

    @property
    def aliases(self) -> tuple[str, ...]: ...

    @property
    def mapping_method(self) -> str: ...

    @property
    def review_status(self) -> str: ...


@runtime_checkable
class SupportsIdentity(Protocol):
    """Adapters that can declare their own native asset identities.

    The Case is passed in because identity is declared at materialization
    time: the adapter knows what ``New Line.<name>`` it emitted for this
    Case, not for any other.
    """

    engine: str

    def asset_identities(self, case: Case) -> Iterable[AssetIdentityRecord]: ...


@runtime_checkable
class SupportsYbusLane(Protocol):
    """Adapters able to compile the passive-Ybus snapshot of a Case.

    The explainable ybus-nr lane (WP18) drives its own Newton iterations
    over this snapshot plus solver diagnostics; the compile itself stays
    behind the adapter so all engine state changes (DataPath, cwd) happen
    inside one seam.  The snapshot type is solver-native, so the port keeps
    ``Any`` rather than importing the adapter's types into ``cept.ports``.
    """

    def ybus_system(self, case: Case) -> Any: ...


@runtime_checkable
class SupportsLifecycle(Protocol):
    """Adapters that need an explicit teardown (e.g. temporary projects)."""

    def cleanup(self) -> None: ...


@runtime_checkable
class SupportsArtifactExport(Protocol):
    """Adapters that can export native solver artifacts (e.g. project files)."""

    def export_project(self, path: str | Path) -> Path: ...

    def export_native_sld(self, path: str | Path) -> Path: ...


__all__ = [
    "AssetIdentityRecord",
    "EngineAdapter",
    "SupportsArtifactExport",
    "SupportsIdentity",
    "SupportsLifecycle",
    "SupportsYbusLane",
]
