"""Expand allowlisted one-level recipe stages into a deterministic flat workflow."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cept.recipes.schema import (
    Recipe,
    RecipeError,
    RecipeIncludeStage,
    RecipeStage,
    RecipeTransition,
    _load_sub_recipe,
    recipe_sha256,
    validate_recipe_document,
)


def _transition(
    decision: RecipeTransition,
    targets: dict[str, str],
    *,
    terminal_next: str | None = None,
) -> dict[str, Any]:
    document = decision.model_dump(exclude_unset=True)
    for name, target in decision.named_targets().items():
        if target is not None:
            document[name] = targets[target]
        elif name == "next":
            document[name] = terminal_next
    return document


def expand_recipe(recipe: Recipe, recipe_path: Path) -> tuple[Recipe, dict[str, str]]:
    """Return an executable flat recipe and the raw-byte identities of its children.

    Every child input must be explicitly mapped, including optional/defaulted
    inputs. Child types, required flags, and defaults remain effective obligations
    of the bound parent input. Children cannot include children or write receipts.
    Required child failures always take the wrapper's terminal failure decision.
    """
    base_dir = Path(recipe_path).resolve().parent
    children: dict[str, Recipe] = {}
    hashes: dict[str, str] = {}
    for name in recipe.includes:
        children[name] = _load_sub_recipe(base_dir, name)
        hashes[name] = recipe_sha256(base_dir / name)

    inputs = {item.name: item.model_dump() for item in recipe.inputs}
    parent_defaults = {item.name: item.default for item in recipe.inputs}
    defaults: dict[str, str | int | bool] = {}
    entries = {stage.id: stage.id for stage in recipe.stages}
    for wrapper in recipe.stages:
        if not isinstance(wrapper, RecipeIncludeStage):
            continue
        child = children[wrapper.include]
        if not child.stages:
            raise RecipeError(f"include stage '{wrapper.id}': child must declare stages")
        entries[wrapper.id] = f"{wrapper.id}.{child.stages[0].id}"
        declared = {item.name for item in child.inputs}
        missing = declared - wrapper.inputs.keys()
        unknown = wrapper.inputs.keys() - declared
        if missing or unknown:
            raise RecipeError(
                f"include stage '{wrapper.id}': incomplete input mapping "
                f"(missing: {', '.join(sorted(missing)) or 'none'}; "
                f"unknown: {', '.join(sorted(unknown)) or 'none'})"
            )
        for item in child.inputs:
            parent_name = wrapper.inputs[item.name]
            bound = inputs[parent_name]
            if bound["type"] != item.type:
                raise RecipeError(
                    f"include stage '{wrapper.id}': input '{item.name}' type {item.type} "
                    f"does not match parent input '{parent_name}' type {bound['type']}"
                )
            bound["required"] = bound["required"] or item.required
            if parent_defaults[parent_name] is None and item.default is not None:
                if parent_name in defaults and defaults[parent_name] != item.default:
                    raise RecipeError(
                        f"include stage '{wrapper.id}': conflicting child defaults "
                        f"for parent input '{parent_name}'"
                    )
                defaults[parent_name] = item.default
                bound["default"] = item.default

    stages: list[dict[str, Any]] = []
    artifacts = list(recipe.expected_artifacts)
    conditions = list(recipe.blocked_conditions)
    for stage in recipe.stages:
        if isinstance(stage, RecipeStage):
            document = stage.model_dump()
            document["on_success"] = _transition(stage.on_success, entries)
            document["on_failure"] = _transition(stage.on_failure, entries)
            stages.append(document)
            continue
        child = children[stage.include]
        targets = {item.id: f"{stage.id}.{item.id}" for item in child.stages}
        continuation = entries[stage.on_success.next] if stage.on_success.next is not None else None
        for child_stage in child.stages:
            # _load_sub_recipe refuses nested includes before any descent.
            if not isinstance(child_stage, RecipeStage):
                raise RecipeError(f"include stage '{stage.id}': nested includes are not allowed")
            document = child_stage.model_dump()
            document["id"] = targets[child_stage.id]
            document["inputs"] = {
                name: stage.inputs[reference] for name, reference in child_stage.inputs.items()
            }
            document["on_success"] = _transition(
                child_stage.on_success, targets, terminal_next=continuation
            )
            document["on_failure"] = (
                _transition(child_stage.on_failure, targets, terminal_next=continuation)
                if child_stage.optional
                else stage.on_failure.model_dump(exclude_unset=True)
            )
            stages.append(document)
        artifacts.extend(name for name in child.expected_artifacts if name not in artifacts)
        conditions.extend(value for value in child.blocked_conditions if value not in conditions)

    effective = recipe.model_dump()
    effective.update(
        inputs=list(inputs.values()),
        stages=stages,
        includes=[],
        expected_artifacts=artifacts,
        blocked_conditions=conditions,
    )
    return validate_recipe_document(effective), hashes


__all__ = ["expand_recipe"]
