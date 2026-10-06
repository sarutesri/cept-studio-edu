"""Registry of workflow-recipe operations.

A recipe stage names one operation id from this registry. The registry is the
only place that knows which operations a recipe may name; the runner owns how
each one is executed.

Every registered operation is ``owner="python"``: it runs in process by calling
the neutral application function in :mod:`cept.application.operations`, which is
the same function the matching ``cept <noun> <verb>`` command handler calls. A
recipe therefore depends on no CLI grammar, spawns no ``cept`` subprocess, and
cannot describe a Case, a run, or a verdict differently from the CLI or from a
direct Python caller.

The recipe document vocabulary still admits ``cli`` and ``script``; a stage that
claims one of them for a registered operation is refused at planning time,
because the registered owner is the truth.

This module must not import CLI parser or command-handler modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping

from cept.recipes.schema import RecipeError

#: Owner declared by the recipe *document* vocabulary. Every registered
#: operation is ``python``; the other two values are legal in a document and are
#: refused against a registered operation.
OperationOwner = Literal["cli", "script", "python"]

#: The one owner a registered operation may declare, and the one the runner
#: dispatches in process through ``PYTHON_OPERATIONS``.
PYTHON_OWNER: Literal["python"] = "python"


class UnknownOperationError(RecipeError):
    """Raised when a recipe names an operation id that is not registered."""


@dataclass(frozen=True)
class Operation:
    """One registered operation a recipe stage may invoke."""

    id: str
    owner: OperationOwner
    summary: str
    required_inputs: tuple[str, ...]
    required_outputs: tuple[str, ...]


_OPERATIONS: tuple[Operation, ...] = (
    # Declared in family order so a reader scanning this file sees one audit
    # family rather than four singletons in four namespaces: all four reach the
    # same application function, all read evidence that already exists, and none
    # runs a solver or promotes a claim. `study.verify` is deliberately not an
    # audit: it produces the run's own verdict, and an audit passing is
    # explicitly not a correctness proof.
    Operation(
        id="case.check",
        owner="python",
        summary=(
            "Validate a typed Case and its build receipt before any study runs, through "
            "cept.application.operations.check."
        ),
        required_inputs=("case",),
        required_outputs=(),
    ),
    Operation(
        id="study.run",
        owner="python",
        summary=(
            "Execute the Case's declared studies through the one execution owner, through "
            "cept.application.operations.run."
        ),
        required_inputs=("case", "run_dir"),
        required_outputs=("results.json", "manifest.json", "validation_report.json", "report.html"),
    ),
    Operation(
        id="study.verify",
        owner="python",
        summary=(
            "Verify an explicit set of run artifacts before claiming completion, through "
            "cept.application.operations.verify."
        ),
        required_inputs=("run_dir",),
        required_outputs=(),
    ),
    Operation(
        id="compare.runs",
        owner="python",
        summary=(
            "Compare two completed solver runs under a declared parity lane, through "
            "cept.application.operations.compare."
        ),
        required_inputs=("lane", "case", "powerfactory_run", "opendss_run", "out_dir"),
        required_outputs=(),
    ),
    Operation(
        id="report.render",
        owner="python",
        summary=(
            "Render the run's HTML report through the reporting owner, through "
            "cept.application.operations.report. `cept run` writes report.html "
            "during the run; this operation renders an existing run on demand."
        ),
        required_inputs=("run_dir",),
        required_outputs=("report.html",),
    ),
    Operation(
        id="report.serve",
        owner="python",
        summary=(
            "Serve an already rendered run report over localhost for review, through "
            "cept.application.operations.serve."
        ),
        required_inputs=("run_dir",),
        required_outputs=(),
    ),
    Operation(
        id="report.notebook",
        owner="python",
        summary=(
            "Assemble notebook output from persisted run artifacts, through "
            "cept.application.operations.notebook. The public `cept report notebook` "
            "route delegates to that same operation."
        ),
        required_inputs=("run_dir", "out_dir"),
        required_outputs=("run-notebook.ipynb",),
    ),
    Operation(
        id="receipt.write",
        owner="python",
        summary=(
            "Write the deterministic workflow completion receipt (recipe-run.json) from the "
            "recorded stage results."
        ),
        required_inputs=("receipt_path",),
        required_outputs=("recipe-run.json",),
    ),
    Operation(
        id="audit.physics",
        owner="python",
        summary=(
            "Audit one persisted run directory for identity, finiteness, and self-consistency, "
            "through cept.application.operations.audit. A pass is not a correctness proof."
        ),
        required_inputs=("run_dir", "audit_out"),
        required_outputs=("physics-audit.json",),
    ),
    Operation(
        id="audit.artifacts",
        owner="python",
        summary=(
            "Inventory run directories under an explicit root and hash their bound evidence "
            "files, through cept.application.operations.audit. Read-only."
        ),
        required_inputs=("audit_root", "audit_out"),
        required_outputs=("artifacts-audit.json",),
    ),
    Operation(
        id="audit.perunit",
        owner="python",
        summary=(
            "Check one typed Case's declared per-unit and kV bases for internal consistency, "
            "through cept.application.operations.audit."
        ),
        required_inputs=("case", "audit_out"),
        required_outputs=("per-unit-audit.json",),
    ),
    Operation(
        id="audit.release",
        owner="python",
        summary=(
            "Build a release-qualification record from explicitly named, already-existing "
            "artifacts, through cept.application.operations.audit. No claim is promoted when "
            "evidence is missing or blocked."
        ),
        required_inputs=("qualify_root", "run_dir", "audit_out"),
        required_outputs=("release-qualification.json",),
    ),
)

OPERATIONS: Mapping[str, Operation] = {operation.id: operation for operation in _OPERATIONS}

#: Exactly the operation ids a recipe stage may name.
OPERATION_IDS: frozenset[str] = frozenset(OPERATIONS)


def resolve_operation(operation_id: str) -> Operation:
    """Return the registered operation, or fail closed with an actionable message."""
    operation = OPERATIONS.get(operation_id)
    if operation is None:
        raise UnknownOperationError(
            f"unknown operation {operation_id!r}; registered operations: "
            + ", ".join(sorted(OPERATION_IDS))
        )
    return operation


def all_operations() -> tuple[Operation, ...]:
    """Return every registered operation in declaration order."""
    return _OPERATIONS


#: The recipe documents this product ships. ``python -m cept.recipes <name>``
#: resolves one of these by name; anything else is treated as a path.
BUNDLED_RECIPE_NAMES: tuple[str, ...] = (
    "study-evidence",
    "report-preparation",
    "run-audit",
    "release-qualification",
)


def is_bundled_recipe(name: str) -> bool:
    """Return whether ``name`` is one of the shipped recipe document names."""
    return name in BUNDLED_RECIPE_NAMES


def bundled_recipe_path(name: str, search_roots: tuple[Path, ...]) -> Path:
    """Locate a bundled recipe document under the given layout roots.

    ``search_roots`` is ordered and explicit: the installed layout resolves the
    recipe from the wheel's ``share/cept/recipes`` data directory, while a
    source checkout resolves it from the repository's ``recipes/`` directory.
    Nothing is globbed, scanned, or chosen by mtime: the first exact
    ``<root>/<name>.yaml`` that exists is the recipe, and if none does, this
    fails closed with one message naming every root it tried.
    """
    for root in search_roots:
        candidate = root / f"{name}.yaml"
        if candidate.is_file():
            return candidate
    tried = ", ".join(str(root / f"{name}.yaml") for root in search_roots)
    raise RecipeError(
        f"bundled recipe {name!r} was not found in this environment; tried: {tried}. "
        "Install the 'cept-power-studio' wheel (its recipe data lives under "
        "share/cept/recipes) or run from a source checkout that contains recipes/."
    )


__all__ = [
    "BUNDLED_RECIPE_NAMES",
    "OPERATION_IDS",
    "OPERATIONS",
    "PYTHON_OWNER",
    "Operation",
    "OperationOwner",
    "RecipeError",
    "UnknownOperationError",
    "all_operations",
    "bundled_recipe_path",
    "is_bundled_recipe",
    "resolve_operation",
]