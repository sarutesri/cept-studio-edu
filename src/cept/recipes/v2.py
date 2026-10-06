"""Recipe format v2: a document a person can read, translated to what runs.

The v1 document was written for the runner. Every stage restated the owner, the
input bindings, the outputs and the declared transition out of the stage, so a
reader had to hold 26 distinct keys to follow five steps -- and nothing checked
that the restated values agreed with the registry, which already declares them.

v2 keeps the same guarantees and drops the repetition:

    steps:
      - verify.study run_dir      # the verb is the operation; the rest is what it reads

The owner, the outputs and the transition out of a step come from the registry,
and `on_failure` is the only thing a step may declare, because continuing past a
failure is the one thing a reader has to see.

Translation, not a parallel runtime: a v2 document is validated into the same
typed models the runner, the plan, the receipt and `--explain` already use. That
is why this is a document change and not a behavioural one, and it is why the
`Operation.defaults` the registry gained matters -- an input a step does not name
is resolved from there, or the recipe fails closed naming it.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from cept.recipes.registry import Operation, resolve_operation
from cept.recipes.schema import (
    Recipe,
    RecipeError,
    RecipeIncludeStage,
    RecipeInput,
    RecipeStage,
    RecipeTransition,
)

SCHEMA = "workflow-recipe-v2"

#: What a step may say after the operation. `optional` is derived: a stage that
#: may continue is optional, and a stage that stops is required.
_STEP_KEYS = frozenset({"on_failure", "reason", "needs", "include", "operation", "id"})


def reasons_on_success() -> str:
    """Why a stage hands on: its declared outputs exist."""
    return "The declared outputs exist, so the workflow may continue."


def reasons_on_failure() -> str:
    """Why a stage that is not optional stops. Same words for every recipe."""
    return (
        "This step did not complete. The workflow stops here rather than reporting a "
        "result it cannot support."
    )


def _stop(reason: str) -> RecipeTransition:
    """A failure with no declared destination: the workflow stops.

    `blocked` and `review` are left unset rather than set to null. The runner
    decides which decision was *declared* by looking at which fields the document
    actually wrote (`named_targets` reads `model_fields_set`), and an explicit
    `blocked: null` reads as a declared blocked decision -- so every ordinary
    failure in a v2 recipe was reported as blocked, exit 3, instead of failed,
    exit 2. A decision the writer did not make has to stay unmade.
    """
    return RecipeTransition(next=None, reason=reason)


def _input(name: str, declaration: Any) -> RecipeInput:
    """One declared input. v2 carries its type; the description lives in the
    registry and the limits, not restated per input."""

    if isinstance(declaration, str):
        return RecipeInput(name=name, type="string", required=False, description="", default=declaration)
    if not isinstance(declaration, dict):
        raise RecipeError(f"needs.{name}: must be a mapping or a default value, got {declaration!r}")
    unknown = set(declaration) - {"type", "default", "optional"}
    if unknown:
        raise RecipeError(f"needs.{name}: unknown key(s): {', '.join(sorted(unknown))}")
    kind = declaration.get("type", "string")
    if kind not in {"file", "directory", "string", "integer", "boolean"}:
        raise RecipeError(f"needs.{name}: type must be file, directory, string, integer or boolean")
    has_default = "default" in declaration
    if has_default and declaration.get("optional"):
        raise RecipeError(f"needs.{name}: a default already makes it optional; pick one")
    return RecipeInput(
        name=name,
        type=kind,
        required=not (has_default or bool(declaration.get("optional"))),
        description="",
        default=declaration.get("default"),
    )


def _bind(
    operation: Operation,
    consumed: dict[str, str],
    known: set[str],
    stage_id: str,
) -> dict[str, str]:
    """Bind the recipe inputs this step names to the operation's own parameters.

    Binding is by name on both sides, so a typo is refused instead of silently
    producing a stage that reads nothing.
    """

    parameters = set(operation.required_inputs)
    supplied: dict[str, str] = {}
    for parameter, source in consumed.items():
        if source not in known:
            raise RecipeError(
                f"steps[{stage_id}]: {source!r} is not a declared input of this recipe; "
                f"declared: {', '.join(sorted(known))}"
            )
        if parameter not in parameters:
            raise RecipeError(
                f"steps[{stage_id}]: {operation.id} does not take '{parameter}'; "
                f"it takes: {', '.join(sorted(parameters))}"
            )
        supplied[parameter] = source
    missing = [name for name in operation.required_inputs if name not in supplied]
    defaults = dict(operation.defaults)
    unresolved = [name for name in missing if name not in defaults]
    if unresolved:
        raise RecipeError(
            f"steps[{stage_id}]: {operation.id} needs {', '.join(unresolved)} and neither the "
            "step names it nor the registry declares a default for it"
        )
    # The default is not written into the stage: the typed model guarantees a
    # stage's input values are declared input names, and a defaulted value is a
    # literal. The planner applies it instead, where a supplied value is applied.
    return supplied


def _step(entry: Any, index: int, known: set[str]) -> RecipeStage | RecipeIncludeStage:
    """One entry of `steps`, which is either an operation or an include."""

    if isinstance(entry, str):
        parts = entry.split()
        operation_id, consumed = parts[0], parts[1:]
        try:
            operation = resolve_operation(operation_id)
        except RecipeError as exc:
            raise RecipeError(f"steps[{index}]: {exc}") from None
        inputs = _bind(operation, {name: name for name in consumed}, known, operation_id)
        return RecipeStage(
            id=f"{index}-{operation_id}",
            operation=operation_id,
            owner=operation.owner,
            inputs=inputs,
            outputs=list(operation.required_outputs),
            on_success=RecipeTransition(next=None, blocked=None, reason=reasons_on_success()),
            on_failure=_stop(reasons_on_failure()),
        )

    if isinstance(entry, dict):
        unknown = set(entry) - _STEP_KEYS
        if unknown:
            raise RecipeError(f"steps[{index}]: unknown key(s): {', '.join(sorted(unknown))}")
        reason = entry.get("reason", "The workflow continues from here.")
        if "operation" in entry:
            operation_id = entry["operation"]
            try:
                operation = resolve_operation(operation_id)
            except RecipeError as exc:
                raise RecipeError(f"steps[{index}]: {exc}") from None
            needed = entry.get("needs", [])
            if isinstance(needed, str):
                consumed = {n: n for n in needed.split()}
            elif isinstance(needed, list):
                consumed = {str(k): str(k) for k in needed}
            elif isinstance(needed, dict):
                consumed = {str(k): str(v) for k, v in needed.items()}
            else:
                raise RecipeError(f"steps[{index}]: needs must be a list or a mapping")
            target = str(entry["on_failure"]) if entry.get("on_failure") else None
            return RecipeStage(
                # `id` is accepted in the mapping form, so it has to take effect:
                # a transition target written by a reader names this id.
                id=str(entry.get("id") or f"{index}-{operation_id}"),
                operation=operation_id,
                owner=operation.owner,
                inputs=_bind(operation, consumed, known, operation_id),
                outputs=list(operation.required_outputs),
                on_success=RecipeTransition(next=None, blocked=None, reason=reasons_on_success()),
                on_failure=RecipeTransition(
                    **({"next": target, "blocked": target} if target else {"next": None}),
                    reason=entry.get("reason", reason),
                ),
            )
        if "include" in entry:
            name = entry["include"]
            if not isinstance(name, str):
                raise RecipeError(f"steps[{index}]: include must be a bare recipe file name")
            needed = entry.get("needs", [])
            if isinstance(needed, str):
                needed = needed.split()
            if not isinstance(needed, list):
                raise RecipeError(f"steps[{index}]: needs must be a list of input names")
            # An included tail is addressed by its own name, so a step that
            # continues past a failure points at it the way a reader would.
            return RecipeIncludeStage(
                id=str(entry.get("id") or PurePosixPath(name).stem),
                include=name,
                inputs={str(item): str(item) for item in needed},
                on_success=RecipeTransition(next=None, blocked=None, reason=reason),
                on_failure=_stop(reason),
            )
        raise RecipeError(f"steps[{index}]: a mapping step needs an 'include'")

    raise RecipeError(f"steps[{index}]: must be 'operation [inputs]' or a mapping with 'include'")


def translate(document: dict[str, Any]) -> dict[str, Any]:
    """Render a v2 document as the shape the typed models already accept.

    The output is a v1 document because the models, the runner, the plan, the
    receipt and `--explain` all speak it. v2 changes what a person writes, not
    what runs.

    Two things the writer no longer states are filled in here. The runner walks
    the workflow through each stage's success transition, so the chain of
    `on_success.next` is built from the order the steps appear in -- a document
    that says "these run in this order" means exactly that. And a step that may
    continue past a failure is optional, which is what `optional` meant and why
    the receipt does not require it.
    """

    needs = document.get("needs") or {}
    if not isinstance(needs, dict):
        raise RecipeError("needs: must be a mapping of input name to its declaration")
    known = set(needs)
    entries = document.get("steps") or []
    if not entries:
        raise RecipeError("steps: a v2 recipe must declare at least one step")

    models = [_step(entry, index, known) for index, entry in enumerate(entries, start=1)]
    successor: dict[str, str] = {}
    for current, following in zip(models, models[1:]):
        successor[current.id] = following.id

    stages: list[dict[str, Any]] = []
    for model in models:
        next_id = successor.get(model.id)
        if isinstance(model, RecipeIncludeStage):
            stages.append(
                {
                    "id": model.id,
                    "include": model.include,
                    "inputs": model.inputs,
                    "on_success": {"next": next_id, "reason": reasons_on_success()},
                    "on_failure": {"next": None, "reason": reasons_on_failure()},
                }
            )
            continue
        continues = isinstance(model, RecipeStage) and bool(model.on_failure.next)
        stages.append(
            {
                "id": model.id,
                "operation": model.operation,
                "owner": model.owner,
                "inputs": model.inputs,
                "outputs": model.outputs,
                "on_success": {"next": next_id, "reason": reasons_on_success()},
                # Only the decisions the document actually wrote. `optional` in
                # v1 always carried `blocked: null` alongside its target, which
                # the runner reads as a declared blocked decision; a v2 step
                # writes `blocked` only when it means it.
                "on_failure": {
                    # next is the primary decision and is always written; the
                    # other two only when the document actually meant them.
                    "next": model.on_failure.next,
                    **({"blocked": model.on_failure.blocked} if model.on_failure.blocked else {}),
                    "reason": model.on_failure.reason,
                },
                # A step that may continue past a failure is optional, so the
                # receipt does not demand it and an unbound host input does not
                # stop the workflow.
                **({"optional": True} if continues else {}),
            }
        )

    return {
        "schema": "workflow-recipe-v1",
        "id": document["id"],
        "version": document.get("version", "1.0.0"),
        "title": document["title"],
        "description": document.get("description", ""),
        "inputs": [_input(name, declaration).model_dump() for name, declaration in needs.items()],
        "stages": stages,
        "expected_artifacts": document.get("must_produce") or [],
        "blocked_conditions": document.get("limits") or [],
        "includes": sorted({stage["include"] for stage in stages if "include" in stage}),
    }


def build(document: dict[str, Any]) -> Recipe:
    """Validate a parsed v2 document into the typed recipe the runner uses."""

    for required in ("id", "title", "steps", "must_produce"):
        if required not in document:
            raise RecipeError(f"{required}: a v2 recipe must declare it")
    return Recipe.model_validate(translate(document))