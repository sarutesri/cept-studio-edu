"""Versioned workflow recipes: schema, operation registry, and run result.

The recipe document names which registered operation to run and which named
transition to take next. It never carries executable content, loops, retries,
parallelism, or recursion. Execution belongs to ``cept.recipes.runner``; this
facade exposes the shared typed document and receipt contracts.

Public surface:

- :mod:`cept.recipes.schema` — the ``workflow-recipe-v1`` document schema.
- :mod:`cept.recipes.registry` — the closed set of recipe operations and the
  in-process application function each one calls.
- :mod:`cept.recipes.run_result` — the ``workflow-recipe-run-v1`` result document.
- :mod:`cept.recipes.composition` — bounded one-level expansion.
- :mod:`cept.recipes.runner` — the installed ordered execution owner.
"""

from __future__ import annotations

from cept.recipes.registry import (
    BUNDLED_RECIPE_NAMES,
    OPERATION_IDS,
    PYTHON_OWNER,
    Operation,
    UnknownOperationError,
    all_operations,
    bundled_recipe_path,
    is_bundled_recipe,
    resolve_operation,
)
from cept.recipes.run_result import (
    RUN_RESULT_SCHEMA_ID,
    RecipeRunResult,
    StageResult,
    VerificationEvidence,
    write_run_result,
)
from cept.recipes.schema import (
    RECIPE_SCHEMA_ID,
    Recipe,
    RecipeError,
    RecipeIncludeStage,
    RecipeInput,
    RecipeStage,
    RecipeTransition,
    load_recipe,
    recipe_sha256,
    validate_recipe_document,
)

__all__ = [
    "BUNDLED_RECIPE_NAMES",
    "OPERATION_IDS",
    "PYTHON_OWNER",
    "Operation",
    "RECIPE_SCHEMA_ID",
    "RUN_RESULT_SCHEMA_ID",
    "Recipe",
    "RecipeError",
    "RecipeIncludeStage",
    "RecipeInput",
    "RecipeRunResult",
    "RecipeStage",
    "RecipeTransition",
    "StageResult",
    "UnknownOperationError",
    "VerificationEvidence",
    "all_operations",
    "bundled_recipe_path",
    "is_bundled_recipe",
    "load_recipe",
    "recipe_sha256",
    "resolve_operation",
    "validate_recipe_document",
    "write_run_result",
]