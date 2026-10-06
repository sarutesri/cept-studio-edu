"""Typed workflow-recipe model and loader.

A person writes a `workflow-recipe-v2` document (`cept.recipes.v2`);
`load_recipe` translates it into the typed model below, which carries the
same `workflow-recipe-v2` name: there is one recipe format, not two.

A recipe *describes* which registered operation to run, with which declared
inputs, and which named transition to take afterwards. It never contains
executable content, loops, retries, parallelism, scheduling, or recursion.
Those prohibitions are hard failures here, never warnings, so a malformed or
over-capable recipe fails closed before a runner can act on it.

The JSON Schema a recipe *author* validates against is
``src/cept/schema/recipe-v2.schema.json``; it describes the document, not this
typed model.

This module deliberately does not import the CLI. Operation ids come from
:mod:`cept.recipes.registry` (imported lazily inside the validators to keep the
registry/exception dependency one-directional) and never from a parser or
command-handler module.
"""

from __future__ import annotations

import hashlib
import json
import re
import warnings
from pathlib import Path
from typing import Any, Iterator, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

RECIPE_SCHEMA_ID = "workflow-recipe-v2"

_YAML_SUFFIXES = frozenset({".yaml", ".yml"})
_JSON_SUFFIXES = frozenset({".json"})
_RECIPE_SUFFIXES = _YAML_SUFFIXES | _JSON_SUFFIXES

#: Keys that would smuggle executable content into recipe data.
FORBIDDEN_CODE_KEYS: frozenset[str] = frozenset(
    {
        "script",
        "scripts",
        "python",
        "code",
        "source",
        "run",
        "command",
        "cmd",
        "eval",
        "exec",
        "shell",
        "bash",
        "sh",
        "notebook",
        "cell",
        "cells",
        "inline",
        "body",
    }
)

#: Keys that would add a loop / retry / parallel / schedule control surface.
FORBIDDEN_CONTROL_KEYS: frozenset[str] = frozenset(
    {
        "loop",
        "repeat",
        "for_each",
        "foreach",
        "each",
        "while",
        "retry",
        "retries",
        "backoff",
        "parallel",
        "concurrency",
        "schedule",
        "cron",
        "queue",
        "fan_out",
    }
)

#: Value shapes that read as embedded Python or shell source rather than data.
_CODE_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^#!"),  # shebang
    re.compile(r"\b(?:python3?|bash|sh|zsh|pwsh|powershell)\s+-[a-z]*c\b"),
    re.compile(r"\bcmd(?:\.exe)?\s+/[ck]\b", re.IGNORECASE),
    re.compile(r"(^|\n)\s*(?:import\s+\w+|from\s+[\w.]+\s+import\s)"),
    re.compile(r"\bsubprocess\.\w+"),
    re.compile(r"\bos\.system\s*\("),
    re.compile(r"\beval\s*\("),
    re.compile(r"\bexec\s*\("),
    re.compile(r"\$\([^)]*\)"),  # shell command substitution
)

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

class RecipeError(Exception):
    """Raised when a recipe document is unusable.

    Carries one actionable message naming the offending document path so a
    model can fix the recipe without guessing.
    """

# --------------------------------------------------------------------------- #
# Document-level prohibitions
# --------------------------------------------------------------------------- #

def _format_location(path: str, suffix: str) -> str:
    return f"{path}.{suffix}" if path else suffix

def _reject_forbidden_content(node: Any, path: str) -> None:
    """Reject inline executable content and loop/control keys anywhere in ``node``."""
    if isinstance(node, dict):
        for raw_key, value in node.items():
            key = str(raw_key)
            lowered = key.strip().lower()
            location = _format_location(path, key)
            if lowered in FORBIDDEN_CODE_KEYS:
                raise RecipeError(
                    f"{location}: inline executable content is not allowed in a workflow recipe "
                    "(recipes declare an operation, they never carry code)"
                )
            if lowered in FORBIDDEN_CONTROL_KEYS:
                raise RecipeError(
                    f"{location}: loops, retries, parallelism, and scheduling are not allowed "
                    "in a workflow recipe"
                )
            _reject_forbidden_content(value, location)
        return
    if isinstance(node, (list, tuple)):
        for index, item in enumerate(node):
            _reject_forbidden_content(item, f"{path}[{index}]")
        return
    if isinstance(node, str):
        for pattern in _CODE_VALUE_PATTERNS:
            if pattern.search(node):
                raise RecipeError(
                    f"{path}: value looks like embedded Python/shell source; "
                    "recipes declare an operation, they never carry code"
                )

# --------------------------------------------------------------------------- #
# Typed recipe models
# --------------------------------------------------------------------------- #

class RecipeInput(BaseModel):
    """One declared recipe input (file, directory, string, integer, boolean)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    type: Literal["file", "directory", "string", "integer", "boolean"]
    required: bool = True
    description: str
    default: str | int | bool | None = None

class RecipeTransition(BaseModel):
    """A bounded decision using only next, blocked, or review targets."""

    model_config = ConfigDict(extra="forbid")

    next: str | None
    reason: str
    blocked: str | None = None
    review: str | None = None

    @model_validator(mode="after")
    def _reason_is_declared(self) -> "RecipeTransition":
        if not self.reason.strip():
            raise ValueError("reason: must be a non-empty human-readable reason")
        return self

    def named_targets(self) -> dict[str, str | None]:
        """Return only declared decision targets (including explicit null targets)."""
        targets: dict[str, str | None] = {}
        for name in ("blocked", "review"):
            if name in self.model_fields_set:
                targets[name] = getattr(self, name)
        targets["next"] = self.next
        return targets

class RecipeStage(BaseModel):
    """One deterministic stage: exactly one operation, one implementation owner."""

    model_config = ConfigDict(extra="forbid")

    id: str
    operation: str
    owner: Literal["cli", "script", "python"]
    inputs: dict[str, str]
    outputs: list[str]
    on_success: RecipeTransition
    on_failure: RecipeTransition
    optional: bool = False

    @model_validator(mode="after")
    def _outputs_are_named(self) -> "RecipeStage":
        for index, name in enumerate(self.outputs):
            if not str(name).strip():
                raise ValueError(f"outputs[{index}]: output artifact name must be non-empty")
        if len(set(self.outputs)) != len(self.outputs):
            raise ValueError("outputs: output artifact names must be unique")
        return self

    def transition_targets(self) -> Iterator[tuple[str, str | None]]:
        """Yield ``(transition_name, target_stage_id)`` for every named decision."""
        for label, transition in (("on_success", self.on_success), ("on_failure", self.on_failure)):
            for name, target in transition.named_targets().items():
                yield f"{label}.{name}", target

class RecipeIncludeStage(BaseModel):
    """One required, bounded invocation of an allowlisted one-level child."""

    model_config = ConfigDict(extra="forbid")

    id: str
    include: str
    inputs: dict[str, str]
    on_success: RecipeTransition
    on_failure: RecipeTransition
    optional: Literal[False] = False

    @model_validator(mode="after")
    def _bounded_include(self) -> "RecipeIncludeStage":
        if not _is_bare_recipe_name(self.include):
            raise ValueError("include: must be a bare one-level sub-recipe file name")
        if any(target is not None for target in self.on_failure.named_targets().values()):
            raise ValueError("on_failure: an included recipe failure must stop the workflow")
        return self

    def transition_targets(self) -> Iterator[tuple[str, str | None]]:
        for label, transition in (("on_success", self.on_success), ("on_failure", self.on_failure)):
            for name, target in transition.named_targets().items():
                yield f"{label}.{name}", target

# The published contract names the discriminator field `schema`
# (`schema: workflow-recipe-v2`). Pydantic warns that this shadows the
# deprecated `BaseModel.schema` classmethod; the field is what callers read and
# `model_dump` still emits it, so the notice is noise for an imported package.
warnings.filterwarnings(
    "ignore",
    message='Field name "schema" in "Recipe" .*',
    category=UserWarning,
)

class Recipe(BaseModel):
    """One versioned workflow recipe document."""

    model_config = ConfigDict(extra="forbid")

    # The published contract names the discriminator `schema`; mypy sees pydantic's
    # deprecated `BaseModel.schema` classmethod under that name.
    schema: Literal["workflow-recipe-v2"]  # type: ignore[assignment]
    id: str
    version: str
    title: str
    description: str = ""
    inputs: list[RecipeInput]
    stages: list[RecipeStage | RecipeIncludeStage]
    expected_artifacts: list[str]
    blocked_conditions: list[str]
    includes: list[str] = Field(default_factory=list)

    # -- cross-field prohibitions ------------------------------------------- #

    @model_validator(mode="after")
    def _declared_names_are_unique(self) -> "Recipe":
        input_names = [item.name for item in self.inputs]
        if len(set(input_names)) != len(input_names):
            duplicates = sorted({name for name in input_names if input_names.count(name) > 1})
            raise ValueError(f"inputs: duplicate input names are not allowed: {', '.join(duplicates)}")

        stage_ids = [stage.id for stage in self.stages]
        if len(set(stage_ids)) != len(stage_ids):
            duplicates = sorted({sid for sid in stage_ids if stage_ids.count(sid) > 1})
            raise ValueError(f"stages: duplicate stage ids are not allowed: {', '.join(duplicates)}")

        if not self.expected_artifacts:
            raise ValueError("expected_artifacts: at least one expected artifact must be declared")
        for index, artifact in enumerate(self.expected_artifacts):
            if not artifact.strip():
                raise ValueError(f"expected_artifacts[{index}]: expected artifact name must be non-empty")
        if len(set(self.expected_artifacts)) != len(self.expected_artifacts):
            raise ValueError("expected_artifacts: expected artifact names must be unique")

        for index, condition in enumerate(self.blocked_conditions):
            if not condition.strip():
                raise ValueError(f"blocked_conditions[{index}]: blocked condition must be non-empty")

        if len(set(self.includes)) != len(self.includes):
            raise ValueError("includes: duplicate include names are not allowed")
        for index, name in enumerate(self.includes):
            if not _is_bare_recipe_name(name):
                raise ValueError(
                    f"includes[{index}]: '{name}' is not a bare one-level sub-recipe file name "
                    "(no directories, no '..')"
                )
        referenced = {
            stage.include for stage in self.stages if isinstance(stage, RecipeIncludeStage)
        }
        undeclared = referenced - set(self.includes)
        if undeclared:
            raise ValueError(
                f"includes: stages reference non-allowlisted children: {', '.join(sorted(undeclared))}"
            )
        unused = set(self.includes) - referenced
        if unused:
            raise ValueError(
                f"includes: declared children must be referenced by a stage: {', '.join(sorted(unused))}"
            )
        return self

    @model_validator(mode="after")
    def _stages_resolve(self) -> "Recipe":
        # Imported here so the registry can depend on RecipeError without a cycle.
        from cept.recipes.registry import OPERATION_IDS

        declared_inputs = {item.name for item in self.inputs}
        stage_ids = {stage.id for stage in self.stages}

        for index, stage in enumerate(self.stages):
            if isinstance(stage, RecipeStage) and stage.operation not in OPERATION_IDS:
                known = ", ".join(sorted(OPERATION_IDS))
                raise ValueError(
                    f"stages[{index}].operation: '{stage.operation}' is not a registered operation "
                    f"(known: {known})"
                )
            for name, reference in stage.inputs.items():
                if reference not in declared_inputs:
                    raise ValueError(
                        f"stages[{index}].inputs.{name}: references undeclared recipe input "
                        f"'{reference}'"
                    )
            for label, target in stage.transition_targets():
                if target is None:
                    continue
                if target not in stage_ids:
                    raise ValueError(
                        f"stages[{index}].{label}: '{target}' is not a declared stage id"
                    )

        self._reject_cycles()
        return self

    def _reject_cycles(self) -> None:
        edges: dict[str, list[str]] = {stage.id: [] for stage in self.stages}
        for stage in self.stages:
            for _label, target in stage.transition_targets():
                if target is not None:
                    edges[stage.id].append(target)

        state: dict[str, int] = {}
        stack: list[str] = []

        def visit(node: str) -> None:
            state[node] = 1
            stack.append(node)
            for neighbour in edges[node]:
                if state.get(neighbour, 0) == 1:
                    cycle = stack[stack.index(neighbour) :] + [neighbour]
                    raise ValueError(
                        f"stages: transition cycle is not allowed: {' -> '.join(cycle)}"
                    )
                if state.get(neighbour, 0) == 0:
                    visit(neighbour)
            stack.pop()
            state[node] = 2

        for stage in self.stages:
            if state.get(stage.id, 0) == 0:
                visit(stage.id)

# --------------------------------------------------------------------------- #
# Loading and validation
# --------------------------------------------------------------------------- #

def recipe_sha256(path: Path) -> str:
    """Return the sha256 of the raw recipe file bytes (identity, not content model)."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def _read_document(path: Path) -> Any:
    suffix = path.suffix.lower()
    if suffix in _JSON_SUFFIXES:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RecipeError(f"{path.name}: invalid JSON ({exc})") from exc
    if suffix in _YAML_SUFFIXES:
        try:
            import yaml
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on install extras
            raise RecipeError(
                f"{path.name}: YAML recipes need PyYAML; run 'python -m pip install PyYAML' "
                "or use a JSON recipe"
            ) from exc
        try:
            return yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise RecipeError(f"{path.name}: invalid YAML ({exc})") from exc
    supported = ", ".join(sorted(_RECIPE_SUFFIXES))
    raise RecipeError(f"{path.name}: unsupported recipe file type (supported: {supported})")

def validate_recipe_document(doc: dict, *, base_dir: Optional[Path] = None) -> Recipe:
    """Validate a parsed recipe mapping and return the typed recipe.

    ``base_dir`` resolves the one-level ``includes`` names so nested includes can
    be rejected; it is the directory holding the recipe file.
    """
    if not isinstance(doc, dict):
        raise RecipeError(f"<document>: recipe must be a mapping, got {type(doc).__name__}")

    _reject_forbidden_content(doc, "")

    try:
        recipe = Recipe.model_validate(doc)
    except ValidationError as exc:
        raise RecipeError(_format_validation_error(exc)) from exc

    if base_dir is not None:
        try:
            for name in recipe.includes:
                _load_sub_recipe(base_dir, name)
        except RecursionError as exc:
            raise RecipeError(
                "includes: a sub-recipe's includes never resolve; an include refers "
                "to itself, directly or through another recipe"
            ) from exc
    return recipe

def load_recipe(path: Path) -> Recipe:
    """Load and fully validate one recipe file (.yaml/.yml/.json)."""
    path = Path(path)
    if not path.is_file():
        raise RecipeError(f"{path}: recipe file not found")
    try:
        document = _read_document(path)
    except RecursionError as exc:
        raise RecipeError(
            f"{path}: the recipe's includes never resolve; an include refers to "
            "itself, directly or through another recipe"
        ) from exc
    return validate_recipe_document(
        _translate_v2(document), base_dir=path.resolve().parent
    )

def _translate_v2(document: Any) -> dict:
    """Refuse any schema this product does not run, and translate v2.

    v2 is the only accepted schema. It is translated into the shape the typed
    models describe, so the runner, the plan, the receipt and `--explain` speak
    one vocabulary while a reader writes a much shorter document.
    """
    from cept.recipes import v2

    declared = document.get("schema") if isinstance(document, dict) else None
    if declared != v2.SCHEMA:
        raise RecipeError(
            f"schema is {declared!r}; this product reads {v2.SCHEMA!r}. "
            "Read a recipe with: cept run --recipe <name> --explain"
        )
    return v2.translate(document)

def _load_sub_recipe(base_dir: Path, name: str) -> Recipe:
    child_path = (Path(base_dir) / name).resolve()
    if not child_path.is_file():
        raise RecipeError(f"includes: '{name}' does not resolve to a recipe file")
    document = _read_document(child_path)
    # Inspect before descent: a self- or cyclic include must never recurse through
    # load_recipe. Both key spellings are read because the document is whichever
    # schema the caller handed us: v2 writes its steps under `steps` and derives
    # the allowlist from them, so a guard that only looked at `stages` and
    # `includes` passed every v2 child through and a cycle recursed until Python
    # gave up -- a raw RecursionError, not the fail-closed refusal the runner
    # promises --writing a receipt.
    steps = None
    if isinstance(document, dict):
        steps = document.get("steps")
        if steps is None:
            steps = document.get("stages")
    if isinstance(document, dict) and (
        document.get("includes")
        or (
            isinstance(steps, list)
            and any(isinstance(step, dict) and "include" in step for step in steps)
        )
    ):
        raise RecipeError(f"includes: sub-recipe '{name}' declares nested includes")
    # The child goes through the same translation as any other recipe, or an
    # included tail would be read as a v1 document and fail on a schema literal.
    child = validate_recipe_document(_translate_v2(document), base_dir=base_dir)
    if any(isinstance(stage, RecipeIncludeStage) for stage in child.stages):
        raise RecipeError(f"includes: sub-recipe '{name}' declares nested includes")
    if any(
        stage.operation == "write.receipt"
        for stage in child.stages
        if isinstance(stage, RecipeStage)
    ):
        raise RecipeError(f"includes: sub-recipe '{name}' must not write a completion receipt")
    return child

def _format_validation_error(exc: ValidationError) -> str:
    """Render pydantic failures as ``<document path>: <reason>`` lines."""
    parts = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error["loc"])
        parts.append(f"{location}: {error['msg']}" if location else error["msg"])
    return "; ".join(parts)

def _is_bare_recipe_name(name: str) -> bool:
    if not name or not _NAME_RE.match(name):
        return False
    if "/" in name or "\\" in name or ".." in name:
        return False
    return Path(name).suffix.lower() in _RECIPE_SUFFIXES

__all__ = [
    "RECIPE_SCHEMA_ID",
    "FORBIDDEN_CODE_KEYS",
    "FORBIDDEN_CONTROL_KEYS",
    "Recipe",
    "RecipeError",
    "RecipeInput",
    "RecipeIncludeStage",
    "RecipeStage",
    "RecipeTransition",
    "load_recipe",
    "recipe_sha256",
    "validate_recipe_document",
]
