"""Study orchestration — pick an engine for a case and run it.

Routing rule: ``case.engine`` wins when set. Otherwise, ``opendss`` is the
default for every study type it supports (all of them through Phase 1).
PowerFactory only ever runs when explicitly requested via
``case.engine='powerfactory'`` (and only for study types it supports —
balanced load_flow/fault, plus RMS dynamics as of Phase 2), since it additionally
requires ``network.kind='inline'`` and a local installation/license that
not every researcher has (see ``cept doctor``).

WP11 phase 1: dispatch goes through the adapter registry — no ``if/elif``
and no ``isinstance`` here.  ``StudyRun`` is a ``NamedTuple`` so it *is* a
tuple and existing 2-tuple unpacking callers need zero changes.
"""

from __future__ import annotations

from typing import Iterable, NamedTuple, Optional

from cept.ports.engine import EngineAdapter
from cept.schema.case import Case
from cept.schema.result import StudyResult
from cept.studies.planning import execute_run, prepare_run
from cept.studies.scenarios import apply_scenario

Adapter = EngineAdapter


class StudyRun(NamedTuple):
    """A completed study: the result plus the adapter that produced it.

    Kept a tuple so ``(result, adapter)`` unpacking everywhere else keeps
    working unchanged.
    """

    result: StudyResult
    adapter: Adapter


def run_case(
    case: Case,
    *,
    extra_commands: Optional[Iterable[str]] = None,
    solver: str = "native",
    preserve_powerfactory: bool = False,
    model_package_paths: Optional[Iterable[str]] = None,
) -> StudyRun:
    """Run a case and return (result, adapter).

    The adapter is returned too so callers can pull geometry (bus coords,
    topology) for the network diagram while the circuit is still loaded.
    """
    plan = prepare_run(
        case,
        extra_commands=extra_commands,
        solver=solver,
        preserve_powerfactory=preserve_powerfactory,
        model_package_paths=model_package_paths,
    )
    result, adapter = execute_run(plan)
    return StudyRun(result, adapter)


__all__ = ["Adapter", "StudyRun", "run_case", "apply_scenario"]
