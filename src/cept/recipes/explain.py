"""Print a recipe so a person can read it without reading the YAML.

A recipe document is written for the runner: it carries the owner, the input
bindings, the outputs and the declared transition out of every stage, so that a
plan is fixed before anything executes. That is the right shape for a machine and
the wrong shape for a reader, and the reader is who has to decide whether a
workflow does what they meant.

This renders the same loaded document as prose. It is a reading aid and nothing
more: it resolves nothing, reads no input value, runs no operation, writes no
receipt, and creates no directory. It runs before input resolution on purpose --
the point is to read a recipe before you have the values it wants.
"""

from __future__ import annotations

from cept.recipes.schema import Recipe, RecipeIncludeStage, RecipeStage


def _needs_block(recipe: Recipe) -> list[str]:
    """The declared inputs, one line each, with whether the caller must supply it."""

    lines = ["needs"]
    width = max((len(item.name) for item in recipe.inputs), default=0)
    for item in recipe.inputs:
        state = "required" if item.required else _optional_state(item.default)
        lines.append(f"  {item.name:<{width}}  {state:<28}  {_one_line(item.description, 66)}")
    if not recipe.inputs:
        lines.append("  (none)")
    return lines


def _step_lines(recipe: Recipe) -> list[str]:
    """The stage list as the sequence of actions it actually is.

    Only a stage whose failure transition deviates from stopping is called out.
    Every stage declaring where a failure goes is the norm, not the exception, and
    printing twenty identical "stops on failure" rows would bury the two that
    deliberately continue.
    """

    lines = ["steps"]
    number = 0
    for stage in recipe.stages:
        number += 1
        if isinstance(stage, RecipeIncludeStage):
            lines.append(f"  {number:>2}.  {stage.id}  --  runs {stage.include} inline")
        elif isinstance(stage, RecipeStage):
            takes = ", ".join(sorted(stage.inputs)) or "nothing"
            lines.append(f"  {number:>2}.  {stage.operation:<20} {stage.id}   takes {takes}")
            failure = stage.on_failure
            where = failure.next or failure.blocked or failure.review
            if where:
                lines.append(f"      if this fails -> {where} instead of stopping")
                if failure.reason:
                    lines.append(f"      {_one_line(failure.reason, 108)}")
        else:  # pragma: no cover - a stage model that is neither of the two
            lines.append(f"  {number:>2}.  (unrecognised stage {stage!r})")
    lines.append("  every other step stops the workflow when it fails")
    return lines


def _one_line(text: str, limit: int) -> str:
    """First sentence, clipped. The full text stays in the file this replaces.

    `--explain` exists so a reader does not have to open the YAML. Repainting
    that YAML's prose verbatim defeats it: a reader wants to know which inputs
    exist and which are optional, and can go to the file for the reasoning.
    """

    flat = " ".join(text.split())
    sentence = flat.split(". ", 1)[0].rstrip(".")
    if len(sentence) <= limit:
        return sentence
    return sentence[: limit - 1].rstrip() + "…"


def _optional_state(default: object) -> str:
    """How an optional input reads. ``default None`` is an implementation detail."""

    if default is None:
        return "optional"
    return f"optional, default {default!r}"


def explain(recipe: Recipe) -> str:
    """Render one loaded recipe as text for a person."""

    lines: list[str] = [f"{recipe.id} {recipe.version} — {recipe.title}"]
    if recipe.description.strip():
        lines.append("")
        lines.extend(f"  {line.strip()}" for line in recipe.description.strip().splitlines())
    lines.append("")
    lines.extend(_needs_block(recipe))
    lines.append("")
    lines.extend(_step_lines(recipe))
    lines.append("")
    lines.append(f"must produce {len(recipe.expected_artifacts)} artifact(s), or the run does not pass")
    for name in recipe.expected_artifacts:
        lines.append(f"  {name}")
    if recipe.blocked_conditions:
        # An expanded include contributes its own limits, so the same sentence can
        # arrive twice. Saying it twice makes the list look longer than the set of
        # things that cannot complete, which is the opposite of what it is for.
        unique: list[str] = []
        for condition in recipe.blocked_conditions:
            line = _one_line(condition, 118)
            if line not in unique:
                unique.append(line)
        lines.append("")
        lines.append(f"limits — what cannot complete, and why ({len(unique)})")
        for line in unique:
            lines.append(f"  - {line}")
    lines.append("")
    lines.append("run it")
    supplied = " ".join(f"--input {item.name}=<{item.type}>" for item in recipe.inputs if item.required)
    lines.append(f"  cept run --recipe {recipe.id} {supplied}".rstrip())
    return "\n".join(lines)
