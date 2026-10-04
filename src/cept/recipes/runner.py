"""Single ordered runtime for ``workflow-recipe-v1``.

Explicit include stages expand one level into bound, namespaced child stages.
The runner executes registered operations synchronously and records declared
decisions, output hashes, required-stage completion, and canonical verification.
It never supplies engineering data or overrides the Case, contract, or solver.
There are no loops, recursion, retries, parallelism, inline code, or "latest"
artifact selection. Identity binds parent/child bytes and resolved inputs.

A pass requires every required stage, every expected artifact, and byte-bound
literal study verification. A declared optional blocked comparison is recorded
without claiming cross-engine agreement. The ceiling is WORKFLOW_VALIDATED,
never project validation.

Every stage runs in process through :data:`PYTHON_OPERATIONS`, which calls the
neutral application functions in :mod:`cept.application.operations`. No stage
spawns a subprocess, resolves a command line, or imports the CLI: a recipe and
the matching ``cept <noun> <verb>`` command reach the same function, so they
cannot disagree. Every stage therefore records ``command_line: null``.

``python -m cept.recipes <recipe-path-or-bundled-name>`` uses the installed
environment without path injection. ``scripts/run_recipe.py`` is the developer
bootstrap: it selects pinned checkout imports before loading this owner unless
``--installed`` (or ``--no-pin``) is requested. Every run prints its mode and
resolved ``cept`` module path. Dry runs execute no operations but write a
nonpassing plan receipt.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import sys
import sysconfig
from typing import Any, Callable, Mapping

from pydantic import ValidationError

from cept.recipes.registry import (
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
    RunStatus,
    StageResult,
    StageStatus,
    VerificationEvidence,
    write_run_result,
)
from cept.recipes.schema import (
    Recipe,
    RecipeError,
    RecipeInput,
    RecipeStage,
    load_recipe,
    recipe_sha256,
)
from cept.util import require_distinct_output


# The developer bootstrap chooses pinned imports before loading this module.
# Installed module execution never adds a checkout to sys.path or PYTHONPATH.
_ROOT_SRC = ""
MODE_PINNED = "pinned"
MODE_INSTALLED = "installed"
INSTALL_FLAG = "--installed"
INSTALL_FLAG_ALIAS = "--no-pin"
ENVIRONMENT_MODE = MODE_INSTALLED




RESULT_FILENAME = "recipe-run.json"

EXIT_PASSED = 0
EXIT_INVALID_RECIPE = 1
EXIT_FAILED = 2
EXIT_BLOCKED = 3

#: Solver logs can be enormous. stderr is capped in-band so the result keeps a
#: bounded head and tail plus an explicit truncation marker; stdout is never
#: stored in the result document at all.
STDERR_HEAD_CHARS = 6000
STDERR_TAIL_CHARS = 2000

#: Recipe input that carries a run directory; declared outputs of a stage that
#: binds it are resolved inside that run directory.
RUN_DIR_INPUT = "run_dir"

#: Recipe inputs that name a file *inside* that run directory. A stage declares
#: its outputs relative to the run directory, so a destination the operation
#: writes must resolve to the same place. Leaving one of these relative would
#: make the operation resolve it against this process's working directory while
#: the runner looked for the declared output in the run directory — the two
#: would silently disagree, and the stage would be reported incomplete.
RUN_DIR_RELATIVE_INPUTS = frozenset({"notebook_output"})


#: Operations whose completion is verification evidence. The registry owns the
#: operation vocabulary but carries no verification flag, so the runner reads
#: these ids explicitly rather than guessing from a summary string.
VERIFICATION_OPERATION_IDS = frozenset({"study.verify"})

#: Named failure transitions in the order the runner evaluates them.
FAILURE_TRANSITION_PRIORITY = ("blocked", "review", "next")

CLAIM_PASSED = "workflow-verified"
CLAIM_PLAN = "plan-only"
CLAIM_BLOCKED = "blocked"
CLAIM_UNVERIFIED = "stages-completed-unverified"




def active_cept_path() -> str:
    """Return the file the ``cept`` package actually resolved to, or ``""``.

    Read from ``sys.modules`` rather than by re-resolving a spec, so this reports
    the module that is really loaded and cannot disagree with it.
    """
    module = sys.modules.get("cept")
    path = getattr(module, "__file__", None)
    return str(Path(path).resolve()) if path else ""


# --------------------------------------------------------------------------- #
# Local helpers: hashing, capping, evidence reading. No execution logic.
# --------------------------------------------------------------------------- #


def sha256_file(path: Path) -> str:
    """Return the SHA-256 of one file, read in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cap_stderr(text: str) -> str:
    """Cap captured stderr, recording the truncation inside the text itself."""
    if len(text) <= STDERR_HEAD_CHARS + STDERR_TAIL_CHARS:
        return text
    omitted = len(text) - STDERR_HEAD_CHARS - STDERR_TAIL_CHARS
    return (
        f"{text[:STDERR_HEAD_CHARS]}\n"
        f"...[{omitted} characters truncated by run_recipe]...\n"
        f"{text[-STDERR_TAIL_CHARS:]}"
    )


def read_json(path: Path) -> Any:
    """Read one JSON artifact, or None when it is missing or unreadable."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None




def timestamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Recipe inputs
# --------------------------------------------------------------------------- #


def parse_assignments(assignments: list[str] | None) -> dict[str, str]:
    """Parse repeated ``NAME=VALUE`` CLI assignments."""
    values: dict[str, str] = {}
    for raw in assignments or ():
        if "=" not in raw:
            raise RecipeError(f"input assignment must be NAME=VALUE, got {raw!r}")
        name, _, value = raw.partition("=")
        name = name.strip()
        if not name:
            raise RecipeError(f"input assignment has an empty name: {raw!r}")
        if name in values:
            raise RecipeError(f"input {name!r} was supplied more than once")
        values[name] = value
    return values


def coerce_input(declared: RecipeInput, raw: str) -> str:
    """Validate one supplied value against its declared type. Returns its canonical form."""
    if declared.type == "integer":
        try:
            return str(int(raw.strip()))
        except ValueError as exc:
            raise RecipeError(
                f"recipe input {declared.name!r} is declared 'integer' but got {raw!r}"
            ) from exc
    if declared.type == "boolean":
        lowered = raw.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return "true"
        if lowered in {"false", "0", "no"}:
            return "false"
        raise RecipeError(
            f"recipe input {declared.name!r} is declared 'boolean' but got {raw!r}"
        )
    return raw


def resolve_recipe_inputs(
    recipe: Recipe,
    supplied: dict[str, str],
    cwd: Path,
) -> dict[str, str]:
    """Resolve declared recipe inputs from explicit CLI values only.

    Nothing is discovered, globbed, scanned for, or taken from mtime: a required
    input that was not supplied is a hard failure, and a declared file/directory
    input is resolved against the caller's working directory.
    """
    resolved: dict[str, str] = {}
    declared_inputs: list[RecipeInput] = list(recipe.inputs)
    # Resolve the run directory first: a run-directory-scoped input is anchored
    # to it, so the declaration order must not matter.
    ordered_inputs = sorted(
        declared_inputs, key=lambda item: item.name != RUN_DIR_INPUT
    )
    for declared in ordered_inputs:
        name = declared.name
        if name in supplied:
            value = coerce_input(declared, supplied[name])
        elif declared.default is not None:
            value = coerce_input(declared, str(declared.default))
        elif declared.required:
            raise RecipeError(
                f"required recipe input {name!r} was not supplied; pass --input {name}=<value>"
            )
        else:
            continue
        if declared.type in {"file", "directory"}:
            path = Path(value)
            # Inputs are typed by the caller, so a relative value is resolved
            # against the caller's working directory and then carried as an
            # absolute path: stages execute inside the run directory, not here.
            candidate = path.resolve() if path.is_absolute() else (cwd / path).resolve()
            if not candidate.exists():
                raise RecipeError(
                    f"recipe input {name!r} points at a missing "
                    f"{declared.type}: {value} (resolved to {candidate})"
                )
            value = str(candidate)
        elif name == RUN_DIR_INPUT or name in RUN_DIR_RELATIVE_INPUTS:
            # The run directory is a *declared output* of the execution stage, so
            # it need not exist yet and cannot use the file/directory existence
            # check above. It — and every input naming a file inside it — still
            # has to be carried as an absolute path: a CLI stage runs with cwd
            # set to the output directory while an in-process stage runs in this
            # process, so a relative value would resolve to two different places
            # and the operation and the declared-output lookup would disagree.
            anchor = resolved.get(RUN_DIR_INPUT) if name != RUN_DIR_INPUT else None
            path = Path(value)
            base = Path(anchor) if anchor else cwd
            value = str(path.resolve() if path.is_absolute() else (base / path).resolve())
        resolved[name] = value
    for name in supplied:
        if name not in resolved:
            raise RecipeError(
                f"recipe {recipe.id!r} does not declare an input named {name!r}; "
                f"declared inputs: {', '.join(item.name for item in declared_inputs) or '(none)'}"
            )
    return resolved




# --------------------------------------------------------------------------- #
# Planning: bind inputs, resolve operations, validate transitions
# --------------------------------------------------------------------------- #


@dataclass
class PlannedStage:
    """One fully resolved stage: operation, bound inputs, outputs, transitions.

    There is no ``argv`` field: every registered operation runs in process, so a
    stage has no command line and its result records ``command_line: None``.
    """

    stage_id: str
    operation: Operation
    inputs: dict[str, str]
    outputs: list[str]
    optional: bool
    output_root: Path
    on_success: dict[str, str | None]
    on_failure: dict[str, str | None]
    #: Set when an optional stage cannot be bound on this host (for example its
    #: comparison leaves were not supplied). The stage is never executed; it
    #: records the blocked state and takes its declared failure transition.
    blocked_reason: str | None = None


def artifact_root(bound: dict[str, str], base_dir: Path) -> Path:
    """Resolve declared artifact names against the bound run directory, else the base dir."""
    run_dir = bound.get(RUN_DIR_INPUT)
    if not run_dir:
        return base_dir
    path = Path(run_dir)
    return path if path.is_absolute() else (base_dir / path)


def plan_stages(recipe: Recipe, values: dict[str, str], base_dir: Path) -> list[PlannedStage]:
    """Bind every stage to a registered operation and validate its transitions."""
    if not recipe.stages:
        raise RecipeError(f"recipe {recipe.id!r} declares no stages")

    planned: list[PlannedStage] = []
    for stage in recipe.stages:
        if not isinstance(stage, RecipeStage):
            raise RecipeError("include stages must be expanded before planning")
        stage_id = stage.id
        try:
            operation: Operation = resolve_operation(stage.operation)
        except UnknownOperationError as exc:  # load_recipe already checks this; keep it explicit.
            known = ", ".join(sorted(item.id for item in all_operations()))
            raise RecipeError(f"stage {stage_id!r}: {exc} (known: {known})") from exc
        if stage.owner != operation.owner:
            raise RecipeError(
                f"stage {stage_id!r}: owner {stage.owner!r} contradicts "
                f"registered owner {operation.owner!r} for {operation.id}"
            )

        if operation.owner != PYTHON_OWNER:
            # Unreachable while the registry registers only in-process
            # operations, and kept so a future subprocess owner is refused here
            # rather than silently executed through the wrong path.
            raise RecipeError(
                f"stage {stage_id!r}: operation {operation.id!r} declares owner="
                f"{operation.owner!r}; this runner executes every stage in process as "
                f"owner={PYTHON_OWNER!r}"
            )

        if operation.id not in PYTHON_OPERATIONS:
            # Fail closed at plan time, before anything runs: a python-owner
            # operation this runner cannot execute is a hard error, never a
            # silently skipped stage. The message names what *is* dispatchable so
            # the fix is obvious from the failure alone.
            raise RecipeError(
                f"stage {stage_id!r}: operation {operation.id!r} declares owner={PYTHON_OWNER!r} "
                f"but this runner has no in-process implementation for it; dispatchable "
                f"in-process operations: {', '.join(sorted(PYTHON_OPERATIONS))}"
            )

        try:
            bound: dict[str, str] = {}
            for name, reference in stage.inputs.items():
                if reference not in values:
                    raise RecipeError(
                        f"stage {stage_id!r} input {name!r} needs recipe input {reference!r}, "
                        "which was neither supplied nor defaulted"
                    )
                bound[name] = values[reference]
            # In-process: there is no argv to render and no CLI verb to spawn.
            # Binding still has to be complete, so every declared required
            # input must be bound to a non-empty value before the stage runs.
            missing = [
                name
                for name in operation.required_inputs
                if not str(bound.get(name, "")).strip()
            ]
            if missing:
                raise RecipeError(
                    f"operation {operation.id!r} is missing bound input(s): "
                    + ", ".join(missing)
                )
        except RecipeError as exc:
            # An optional stage is allowed to be unavailable on this host. It is
            # planned as declared-blocked so it records the reason and takes its
            # declared failure transition instead of failing the whole recipe. A
            # required stage still fails closed here.
            if not stage.optional:
                raise RecipeError(f"stage {stage_id!r}: {exc}") from exc
            planned.append(
                PlannedStage(
                    stage_id=stage_id,
                    operation=operation,
                    inputs={},
                    outputs=[],
                    optional=True,
                    output_root=artifact_root(values, base_dir),
                    on_success=dict(stage.on_success.named_targets()),
                    on_failure=dict(stage.on_failure.named_targets()),
                    blocked_reason=str(exc),
                )
            )
            continue

        planned.append(
            PlannedStage(
                stage_id=stage_id,
                operation=operation,
                inputs=bound,
                outputs=list(stage.outputs),
                optional=stage.optional,
                output_root=artifact_root(bound, base_dir),
                on_success=dict(stage.on_success.named_targets()),
                on_failure=dict(stage.on_failure.named_targets()),
            )
        )

    protected = [
        artifact_root(values, base_dir) / name for name in recipe.expected_artifacts
    ]
    protected.extend(
        Path(values[item.name]) for item in recipe.inputs
        if item.type == "file" and item.name in values
    )
    protected.extend(
        item.output_root / name for item in planned
        if item.operation.id != "receipt.write" for name in item.outputs
    )
    for item in planned:
        if item.operation.id == "receipt.write" and item.blocked_reason is None:
            destination = resolve_bound_path(item.inputs["receipt_path"], item.output_root)
            payload = read_json(destination)
            existing_receipt = isinstance(payload, dict) and payload.get("schema") == RUN_RESULT_SCHEMA_ID
            existing_sources = (
                entry for directory in {other.output_root for other in planned}
                if directory.is_dir()
                and ((directory / "manifest.json").is_file() or (directory / "results.json").is_file())
                for entry in directory.rglob("*") if entry.is_file()
                and not (existing_receipt and entry.resolve() == destination)
            )
            try:
                require_distinct_output(destination, [*protected, *existing_sources])
            except ValueError as exc:
                raise RecipeError(f"stage {item.stage_id!r}: {exc}") from exc

    _reject_cycles(planned)
    return planned


def _reject_cycles(planned: list[PlannedStage]) -> None:
    """Fail closed on any stage loop; a recipe may not loop."""
    edges = {
        item.stage_id: [
            target for target in list(item.on_success.values()) + list(item.on_failure.values())
            if target is not None
        ]
        for item in planned
    }
    state: dict[str, int] = {}

    def visit(node: str, stack: list[str]) -> None:
        state[node] = 1
        stack.append(node)
        for neighbour in edges[node]:
            if state.get(neighbour, 0) == 1:
                cycle = " -> ".join(stack[stack.index(neighbour) :] + [neighbour])
                raise RecipeError(f"recipe declares a stage loop, which is not permitted: {cycle}")
            if state.get(neighbour, 0) == 0:
                visit(neighbour, stack)
        stack.pop()
        state[node] = 2

    for item in planned:
        if state.get(item.stage_id, 0) == 0:
            visit(item.stage_id, [])


def failure_decision(item: PlannedStage) -> tuple[str, str | None]:
    """Return ``(transition_name, target)`` for a failed stage's declared decision."""
    for name in FAILURE_TRANSITION_PRIORITY:
        if name in item.on_failure:
            return name, item.on_failure[name]
    return "next", None


def success_transition(item: PlannedStage) -> str:
    """Return the recorded ``next_transition`` for a completed, complete stage.

    Shared by :func:`execute` and by the ``receipt.write`` handler: the handler
    builds its own result document, so it must record the same transition the
    runner would have recorded, or the file and the run would disagree.
    """
    target = item.on_success.get("next")
    return f"on_success.next={target}" if target else "on_success.next=none"


# --------------------------------------------------------------------------- #
# Execution: declared order, one at a time, synchronous, stop on first failure
# --------------------------------------------------------------------------- #


@dataclass
class StageRun:
    """What one stage actually did."""

    planned: PlannedStage
    started_at: str
    finished_at: str = ""
    status: StageStatus = "skipped"
    exit_code: int | None = None
    stderr: str = ""
    artifact_hashes: dict[str, str] = field(default_factory=dict)
    missing_outputs: list[str] = field(default_factory=list)
    transition: str | None = None
    notes: list[str] = field(default_factory=list)
    #: Set only by ``receipt.write``. The runner adopts this document as the run
    #: result instead of writing a second one, so the file on disk is written
    #: exactly once by a python-owner stage.
    receipt_document: RecipeRunResult | None = None
    receipt_path: Path | None = None

    @property
    def stage_id(self) -> str:
        return self.planned.stage_id

    @property
    def operation_id(self) -> str:
        return self.planned.operation.id




# --------------------------------------------------------------------------- #
# In-process dispatch.
#
# Every registered operation is ``owner: python`` and every handler below calls
# the neutral application function in ``cept.application.operations.*`` — the
# exact same function the matching CLI handler imports. ``cept case check``
# dispatches to ``check_case_operation``; ``cept study run`` to
# ``run_case_operation``; ``cept study verify`` to ``verify_run_operation``;
# ``cept report compare`` to ``compare_runs_operation``; ``cept report
# export`` to ``render_report_operation``; ``cept report serve`` to
# ``serve_report_operation``; ``cept report notebook`` to
# ``assemble_notebook_operation``. ``receipt.write`` is inherently in-process
# because the runner itself owns ``recipe-run.json``.
#
# There is therefore no command line to render, no subprocess to spawn, and no
# second interpretation of a Case fingerprint or validation verdict: a recipe
# and a direct Python caller reach the same function.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PythonStageContext:
    """The deterministic inputs one in-process operation may read."""

    recipe: Recipe
    recipe_path: Path
    recipe_sha256: str
    base_dir: Path
    values: dict[str, str]
    dry_run: bool
    verbose: bool
    #: Every stage recorded before this one, in order. A handler never reaches
    #: forward: it can only read what already happened.
    recorded_runs: tuple[StageRun, ...]
    #: The live record of the stage being executed. A handler that writes the
    #: run result fills this in first, so the in-memory record and the document
    #: on disk can never drift apart.
    stage_run: StageRun
    included_recipe_hashes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PythonStageOutcome:
    """What one in-process operation reported, in the shape a CLI stage reports."""

    exit_code: int
    stderr: str = ""
    notes: tuple[str, ...] = ()
    #: Set only when this stage wrote the run's own result document.
    receipt_document: RecipeRunResult | None = None
    receipt_path: Path | None = None


def _check_case(item: PlannedStage, context: PythonStageContext) -> PythonStageOutcome:
    """Check the typed Case's readiness through the admission owner, in process."""
    from cept.application.operations.check import CheckCaseRequest, check_case_operation

    outcome = check_case_operation(CheckCaseRequest(case_path=Path(item.inputs["case"])))
    return PythonStageOutcome(
        exit_code=outcome.exit_code,
        notes=outcome.lines,
    )


def _run_study(item: PlannedStage, context: PythonStageContext) -> PythonStageOutcome:
    """Execute the Case's declared studies through the one execution owner."""
    from cept.application.operations.run import RunCaseRequest, run_case_operation

    outcome = run_case_operation(
        RunCaseRequest(
            case_path=Path(item.inputs["case"]),
            run_dir=Path(item.inputs[RUN_DIR_INPUT]),
        )
    )
    return PythonStageOutcome(
        exit_code=outcome.exit_code,
        notes=(f"in-process run: {outcome.run_dir}",),
    )


def _verify_run(item: PlannedStage, context: PythonStageContext) -> PythonStageOutcome:
    """Verify the declared run directory through the single verdict owner."""
    from cept.application.operations.verify import VerifyRunRequest, verify_run_operation

    outcome = verify_run_operation(VerifyRunRequest(run_dir=Path(item.inputs[RUN_DIR_INPUT])))
    return PythonStageOutcome(
        exit_code=outcome.exit_code,
        notes=(
            f"in-process verify: {outcome.verdict['passed']=}; "
            f"validation artifacts: {len(outcome.validation_artifacts)}",
        ),
    )


def _compare_runs(item: PlannedStage, context: PythonStageContext) -> PythonStageOutcome:
    """Relate two completed runs under the declared parity lane, in process."""
    from cept.application.operations.compare import CompareRunsRequest, compare_runs_operation

    outcome = compare_runs_operation(
        CompareRunsRequest(
            left_run_dir=Path(item.inputs["powerfactory_run"]),
            right_run_dir=Path(item.inputs["opendss_run"]),
            case_path=Path(item.inputs["case"]),
            out_dir=Path(item.inputs["out_dir"]),
            lane=item.inputs["lane"],
        )
    )
    return PythonStageOutcome(
        exit_code=outcome.exit_code,
        notes=(
            f"in-process compare lane={outcome.lane}: worker={outcome.worker}, "
            f"out={outcome.out_dir}",
        ),
    )


def _render_report(item: PlannedStage, context: PythonStageContext) -> PythonStageOutcome:
    """Render one run's HTML report through the reporting owner, in process."""
    # Imported here, not at module scope: this runner must stay importable and
    # `--help` must stay usable even when the reporting owner cannot load.
    from cept.application.operations.report import (
        RenderReportRequest,
        ReportFormat,
        render_report_operation,
    )

    outcome = render_report_operation(
        RenderReportRequest(
            run_dir=Path(item.inputs[RUN_DIR_INPUT]),
            format=ReportFormat.HTML,
        )
    )
    return PythonStageOutcome(
        exit_code=outcome.exit_code,
        notes=(f"in-process {outcome.format.value}: {outcome.output_path}",),
    )


def _serve_report(item: PlannedStage, context: PythonStageContext) -> PythonStageOutcome:
    """Serve an already rendered run report over the local review bridge."""
    from cept.application.operations.serve import ServeReportRequest, serve_report_operation

    outcome = serve_report_operation(ServeReportRequest(run_dir=Path(item.inputs[RUN_DIR_INPUT])))
    return PythonStageOutcome(
        exit_code=outcome.exit_code,
        notes=(f"in-process serve: {outcome.report_url}",),
    )


def _assemble_notebook(item: PlannedStage, context: PythonStageContext) -> PythonStageOutcome:
    """Assemble the run's notebook from its stored artifacts, in process."""
    from cept.application.operations.notebook import (
        AssembleNotebookRequest,
        assemble_notebook_operation,
    )

    outcome = assemble_notebook_operation(
        AssembleNotebookRequest(
            run_dir=Path(item.inputs[RUN_DIR_INPUT]),
            output_path=resolve_bound_path(item.inputs["out_dir"], item.output_root),
        )
    )
    return PythonStageOutcome(
        exit_code=outcome.exit_code,
        notes=(
            f"in-process notebook: {outcome.output_path} "
            f"({outcome.cells_emitted} cells, verdict={outcome.verdict})",
        ),
    )



def _physics_audit(item: PlannedStage, context: PythonStageContext) -> PythonStageOutcome:
    """Audit one persisted run directory through the single audit owner."""
    from cept.application.operations.audit import PhysicsAuditRequest, physics_audit_operation

    outcome = physics_audit_operation(
        PhysicsAuditRequest(
            run_dir=Path(item.inputs[RUN_DIR_INPUT]),
            out=resolve_bound_path(item.inputs["audit_out"], item.output_root),
        )
    )
    return PythonStageOutcome(
        exit_code=outcome.exit_code,
        notes=(f"in-process physics audit: passed={outcome.passed}",),
    )


def _artifacts_audit(item: PlannedStage, context: PythonStageContext) -> PythonStageOutcome:
    """Inventory run directories and hash their bound evidence files."""
    from cept.application.operations.audit import (
        ArtifactsAuditRequest,
        artifacts_audit_operation,
    )

    outcome = artifacts_audit_operation(
        ArtifactsAuditRequest(root=Path(item.inputs["audit_root"]))
    )
    encoded = json.dumps(outcome.record, indent=2, ensure_ascii=False)
    destination = resolve_bound_path(item.inputs["audit_out"], item.output_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(encoded + "\n", encoding="utf-8")
    return PythonStageOutcome(
        exit_code=outcome.exit_code,
        notes=(f"in-process artifact inventory: {len(outcome.record['runs'])} run(s)",),
    )


def _per_unit_audit(item: PlannedStage, context: PythonStageContext) -> PythonStageOutcome:
    """Check one typed Case's declared per-unit and kV bases."""
    from cept.application.operations.audit import (
        PerUnitAuditRequest,
        per_unit_audit_operation,
    )

    outcome = per_unit_audit_operation(PerUnitAuditRequest(case_path=Path(item.inputs["case"])))
    if outcome.error is not None:
        return PythonStageOutcome(exit_code=outcome.exit_code, stderr=outcome.error)
    destination = resolve_bound_path(item.inputs["audit_out"], item.output_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(outcome.record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return PythonStageOutcome(
        exit_code=outcome.exit_code,
        notes=(f"in-process per-unit audit: passed={outcome.passed}",),
    )


def _release_qualify(item: PlannedStage, context: PythonStageContext) -> PythonStageOutcome:
    """Build a release-qualification record from the named, existing run."""
    from cept.application.operations.audit import (
        ReleaseQualifyRequest,
        release_qualify_operation,
    )

    outcome = release_qualify_operation(
        ReleaseQualifyRequest(
            root=Path(item.inputs["qualify_root"]),
            runs=(Path(item.inputs[RUN_DIR_INPUT]),),
            out=resolve_bound_path(item.inputs["audit_out"], item.output_root),
        )
    )
    return PythonStageOutcome(
        exit_code=outcome.exit_code,
        notes=(f"in-process release qualification: {outcome.status}",),
    )






def _write_receipt(item: PlannedStage, context: PythonStageContext) -> PythonStageOutcome:
    """Write the completion receipt from the stage results recorded so far.

    building the document.

    A document cannot contain its own sha256, so this stage's declared output
    is recorded as produced but deliberately not hashed: hashing
    ``recipe-run.json`` into ``recipe-run.json`` is impossible, and recording
    the pre-write digest would be a hash that does not describe the file on
    disk. ``main`` therefore adopts this document instead of writing a second
    one over the same path.
    """
    receipt_path = resolve_bound_path(item.inputs["receipt_path"], item.output_root)

    run = context.stage_run
    run.status = "completed"
    run.exit_code = 0
    run.finished_at = timestamp()
    # Recorded here, using the same helper `execute` uses, so the transition in
    # the file is the transition the runner would have recorded for this stage.
    run.transition = success_transition(item)
    run.notes.append(
        f"in-process receipt: {receipt_path}; the run result document cannot record its own "
        "sha256, so this stage's declared output is recorded as produced but not hashed"
    )

    document = build_result(
        recipe=context.recipe,
        recipe_path=context.recipe_path,
        recipe_sha=context.recipe_sha256,
        base_dir=context.base_dir,
        values=context.values,
        runs=[*context.recorded_runs, run],
        failed_run=None,
        dry_run=False,
        included_recipe_hashes=context.included_recipe_hashes,
    )
    write_run_result(receipt_path, document)
    return PythonStageOutcome(
        exit_code=0,
        notes=(f"in-process receipt written: {receipt_path}",),
        receipt_document=document,
        receipt_path=receipt_path,
    )


#: Exactly the operation ids this runner can execute in process. A registered
#: operation absent here fails closed at planning time.
PYTHON_OPERATIONS: Mapping[str, Callable[[PlannedStage, PythonStageContext], PythonStageOutcome]] = {
    "case.check": _check_case,
    "study.run": _run_study,
    "study.verify": _verify_run,
    "compare.runs": _compare_runs,
    "report.render": _render_report,
    "report.serve": _serve_report,
    "report.notebook": _assemble_notebook,
    "physics.audit": _physics_audit,
    "artifacts.audit": _artifacts_audit,
    "perunit.audit": _per_unit_audit,
    "release.qualify": _release_qualify,
    "receipt.write": _write_receipt,
}


def resolve_bound_path(value: str, base_dir: Path) -> Path:
    """Resolve one bound operation path the same way declared outputs resolve."""
    path = Path(value)
    return path.resolve() if path.is_absolute() else (base_dir / path).resolve()


def dispatch_python_operation(
    item: PlannedStage,
    context: PythonStageContext,
    run: StageRun,
) -> PythonStageOutcome:
    """Execute one ``owner: python`` stage in process, fail-closed on any error.

    An operation that raises is a failed stage, never a crashed runner: the
    error is recorded as stderr with a non-zero exit code, and ``execute``
    takes the stage's declared failure transition exactly as it would for a
    CLI stage whose process exited non-zero.
    """
    handler = PYTHON_OPERATIONS.get(item.operation.id)
    if handler is None:
        raise RecipeError(
            f"stage {item.stage_id!r}: operation {item.operation.id!r} declares "
            f"owner={PYTHON_OWNER!r} but this runner has no in-process implementation "
            f"for it; registered in-process operations: {', '.join(sorted(PYTHON_OPERATIONS))}"
        )
    try:
        return handler(item, context)
    except Exception as exc:  # noqa: BLE001 - a failed stage, not a crashed runner
        return PythonStageOutcome(exit_code=1, stderr=f"{type(exc).__name__}: {exc}")


@dataclass(frozen=True)
class RecipeContext:
    """The one resolved recipe and its resolved inputs, shared by every stage."""

    recipe: Recipe
    recipe_path: Path
    recipe_sha256: str
    values: dict[str, str]
    base_dir: Path
    included_recipe_hashes: dict[str, str] = field(default_factory=dict)


def run_stage(
    item: PlannedStage,
    context: RecipeContext,
    recorded_runs: list[StageRun],
    dry_run: bool,
    verbose: bool,
) -> StageRun:
    """Execute one stage in process, or record its plan when ``dry_run``.

    Every stage is dispatched through :data:`PYTHON_OPERATIONS`, so the stage
    record always has ``command_line: None`` and takes its ``exit_code`` from
    the operation outcome.
    """
    run = StageRun(planned=item, started_at=timestamp())
    if dry_run:
        # Side-effect free: nothing is executed and nothing is written.
        run.finished_at = timestamp()
        return run

    outcome = dispatch_python_operation(
        item,
        PythonStageContext(
            recipe=context.recipe,
            recipe_path=context.recipe_path,
            recipe_sha256=context.recipe_sha256,
            base_dir=context.base_dir,
            values=context.values,
            dry_run=dry_run,
            verbose=verbose,
            recorded_runs=tuple(recorded_runs),
            stage_run=run,
            included_recipe_hashes=context.included_recipe_hashes,
        ),
        run,
    )
    run.exit_code = outcome.exit_code
    run.stderr = cap_stderr(outcome.stderr)
    run.notes.extend(outcome.notes)
    run.receipt_document = outcome.receipt_document
    run.receipt_path = outcome.receipt_path
    if outcome.receipt_document is None:
        _hash_declared_outputs(run, item)
        run.status = "completed" if run.exit_code == 0 else "failed"
        run.finished_at = timestamp()
    # A stage that wrote the run result document finalizes its own record
    # before that document was built, so re-stamping it here would make the
    # in-memory record describe something the file on disk does not say.
    return run


def _hash_declared_outputs(run: StageRun, item: PlannedStage) -> None:
    """Hash every declared output that exists, and name every one that does not."""
    for name in item.outputs:
        path = item.output_root / name
        if path.is_file():
            run.artifact_hashes[name] = sha256_file(path)
        else:
            run.missing_outputs.append(name)


def execute(
    planned: list[PlannedStage],
    context: RecipeContext,
    dry_run: bool,
    verbose: bool,
) -> tuple[list[StageRun], StageRun | None]:
    """Run the declared stages in order. Returns ``(runs, failed_run_that_stopped_it)``."""
    by_id = {item.stage_id: item for item in planned}
    runs: list[StageRun] = []
    failed_run: StageRun | None = None
    current: PlannedStage | None = planned[0]

    while current is not None:
        if current.blocked_reason is not None:
            # Declared-blocked optional stage: never executed. Record the state
            # and follow its declared failure transition, which is how a recipe
            # says "compare only when the recipe declares comparison".
            run = StageRun(planned=current, started_at=timestamp(), status="blocked")
            run.finished_at = timestamp()
            run.notes.append(f"stage was not run: {current.blocked_reason}")
            name, target = failure_decision(current)
            run.transition = (
                f"on_failure.{name}={target}" if target else f"on_failure.{name}=none"
            )
            runs.append(run)
            fallback = current.on_success.get("next")
            current = by_id.get(target) if target else (by_id.get(fallback) if fallback else None)
            continue

        run = run_stage(current, context, runs, dry_run, verbose)
        runs.append(run)

        if dry_run:
            name, target = "next", current.on_success.get("next")
            run.transition = f"on_success.{name}={target}" if target else "on_success.next=none"
            current = by_id.get(target) if target else None
            continue

        # A stage is complete only when the operation exits 0 *and* produced every
        # declared output. An exit-0 stage that produced nothing it declared is
        # incomplete, so it takes its failure transition and stops the run.
        if run.status == "completed" and not run.missing_outputs:
            target = current.on_success.get("next")
            run.transition = success_transition(current)
            current = by_id.get(target) if target else None
            continue

        name, target = failure_decision(current)
        run.transition = f"on_failure.{name}={target}" if target else f"on_failure.{name}=none"
        if run.planned.optional:
            # An optional stage never stops the run. Its declared failure target
            # wins; with none declared the workflow continues along the declared
            # success path, and `passed` stays false because the stage did not
            # complete.
            fallback = current.on_success.get("next")
            current = by_id.get(target) if target else (by_id.get(fallback) if fallback else None)
            run.notes.append(
                "stage is optional: true; the run continues at "
                + (f"the declared failure target {target!r}" if target else "the declared next stage")
            )
        else:
            failed_run = run
            current = None

    return runs, failed_run


# --------------------------------------------------------------------------- #
# Result assembly
# --------------------------------------------------------------------------- #


def verification_evidence(runs: list[StageRun]) -> VerificationEvidence | None:
    """Bind a completed verification stage to its current canonical run evidence."""
    from cept.verification.run_set import verify_run_set

    for run in runs:
        if run.operation_id not in VERIFICATION_OPERATION_IDS or run.status != "completed":
            continue
        for name, digest in run.artifact_hashes.items():
            path = (run.planned.output_root / name).resolve()
            payload = read_json(path)
            if (
                path.is_file()
                and sha256_file(path) == digest
                and isinstance(payload, dict)
                and payload.get("passed") is True
                and verify_run_set([run.planned.output_root])["passed"] is True
            ):
                return VerificationEvidence(
                    stage_id=run.stage_id,
                    operation="study.verify",
                    artifact=name,
                    artifact_path=str(path),
                    sha256=digest,
                    passed=True,
                )
    return None


def verification_rejection(runs: list[StageRun]) -> str:
    """Explain why no literal verification evidence was found, when one was expected."""
    verifying = [run for run in runs if run.operation_id in VERIFICATION_OPERATION_IDS]
    if not verifying:
        return "no verification stage ran; passed stays false"
    for run in verifying:
        if run.status != "completed":
            return f"verification stage {run.stage_id!r} did not complete"
        if not run.artifact_hashes:
            return (
                f"verification stage {run.stage_id!r} declares no output artifact to read "
                "passed=true from"
            )
    return "no verification artifact stated the literal passed=true"


def run_identity(
    recipe_sha: str, values: dict[str, str], file_input_names: set[str], base_dir: Path,
    included_recipe_hashes: dict[str, str] | None = None,
) -> str:
    """Deterministic identity from the recipe hash and the supplied input hashes."""
    bound = {}
    for name, value in sorted(values.items()):
        path = Path(value)
        candidate = path if path.is_absolute() else (base_dir / path)
        digest = (
            sha256_file(candidate) if name in file_input_names and candidate.is_file() else None
        )
        bound[name] = {"value": value, "sha256": digest}
    payload = json.dumps(
        {"recipe_sha256": recipe_sha, "includes": included_recipe_hashes or {}, "inputs": bound},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def optional_decision_did_not_veto(run: StageRun) -> bool:
    """Do not confuse an unvisited stage or synthesized stop with a decision."""
    return (
        run.planned.optional
        and run.status in {"failed", "blocked"}
        and (run.transition or "").startswith("on_failure.")
        and run.transition != "on_failure.next=none"
    )


def build_result(
    recipe: Recipe,
    recipe_path: Path,
    recipe_sha: str,
    base_dir: Path,
    values: dict[str, str],
    runs: list[StageRun],
    failed_run: StageRun | None,
    dry_run: bool,
    included_recipe_hashes: dict[str, str] | None = None,
) -> RecipeRunResult:
    """Assemble the one result document, with ``passed`` derived fail-closed."""
    root = artifact_root(values, base_dir)
    expected: dict[str, str] = {}
    missing_expected: list[str] = []
    if not dry_run:
        for name in recipe.expected_artifacts:
            path = root / name
            if path.is_file():
                expected[name] = sha256_file(path)
            else:
                missing_expected.append(name)

    # An optional stage that took its *declared* failure/blocked transition is a
    # recorded state, not a failure: the plan says "compare only when the recipe
    # declares comparison", so an unavailable licensed host or an undeclared
    # leaf must not veto the workflow forever. A non-optional stage, or an
    # optional one that was skipped rather than decided, still fails the run.
    required_stage_ids = [
        stage.id for stage in recipe.stages
        if not (isinstance(stage, RecipeStage) and stage.optional)
    ]
    completed_ids = {
        run.stage_id for run in runs if run.status == "completed" and not run.missing_outputs
    }
    missing_stages = sorted(set(required_stage_ids) - completed_ids)
    all_completed = bool(runs) and not missing_stages and all(
        (run.status == "completed" and not run.missing_outputs)
        or optional_decision_did_not_veto(run)
        for run in runs
    )
    includes = included_recipe_hashes or {}
    changed_includes = [
        name for name, digest in includes.items()
        if not (recipe_path.parent / name).is_file()
        or sha256_file(recipe_path.parent / name) != digest
    ]
    evidence = None if dry_run else verification_evidence(runs)
    passed = bool(
        not dry_run and all_completed and not missing_expected and not changed_includes and evidence
    )

    notes: list[str] = []
    if missing_stages:
        notes.append("required stages did not complete: " + ", ".join(missing_stages))
    if changed_includes:
        notes.append("included recipes changed during execution: " + ", ".join(changed_includes))
    if failed_run is not None:
        if failed_run.exit_code == 0:
            notes.append(
                f"stage {failed_run.stage_id!r} is incomplete: the operation exited 0 but did "
                f"not produce its declared output(s): " + ", ".join(failed_run.missing_outputs)
            )
        else:
            notes.append(f"stage {failed_run.stage_id!r} failed (exit {failed_run.exit_code})")
    for run in runs:
        if run.missing_outputs:
            notes.append(
                f"stage {run.stage_id!r} declared output(s) not produced: "
                + ", ".join(run.missing_outputs)
            )
        notes.extend(run.notes)
    if missing_expected:
        notes.append("missing expected artifact(s): " + ", ".join(missing_expected))
    if not dry_run and not evidence:
        notes.append(verification_rejection(runs))

    status: RunStatus
    blocked_reason: str | None = None
    if dry_run:
        status = "blocked"
        blocked_reason = "dry run: the plan was rendered and nothing was executed"
        claim = f"{CLAIM_PLAN}: {len(runs)} stage(s) rendered, 0 executed"
    elif failed_run is not None:
        name = failure_decision(failed_run.planned)[0]
        blocked = name == "blocked" or name in recipe.blocked_conditions
        status = "blocked" if blocked else "failed"
        blocked_reason = f"{notes[0]}; declared transition {failed_run.transition}"
        claim = f"{CLAIM_BLOCKED if blocked else 'incomplete'}: {notes[0]}"
    elif passed:
        assert evidence is not None
        status = "completed"
        claim = (
            f"{CLAIM_PASSED}: {len(completed_ids)}/{len(recipe.stages)} stages completed, "
            f"{len(expected)}/{len(recipe.expected_artifacts)} expected artifacts hashed, "
            f"literal passed=true in {evidence.artifact} from stage {evidence.stage_id!r}"
        )
    else:
        status = "failed"
        claim = f"{CLAIM_UNVERIFIED}: " + (notes[0] if notes else "verification evidence missing")

    return RecipeRunResult(
        schema=RUN_RESULT_SCHEMA_ID,
        recipe_id=recipe.id,
        recipe_version=recipe.version,
        recipe_path=str(recipe_path),
        recipe_sha256=recipe_sha,
        run_id=run_identity(
            recipe_sha, values, {item.name for item in recipe.inputs if item.type == "file"},
            base_dir, includes,
        ),
        started_at=runs[0].started_at if runs else timestamp(),
        finished_at=runs[-1].finished_at if runs else timestamp(),
        stages=[
            StageResult(
                stage_id=run.stage_id,
                operation=run.operation_id,
                status=run.status,
                inputs=run.planned.inputs,
                outputs=run.planned.outputs,
                # Every stage runs in process, so there is no command line to
                # record. `None` states that positively instead of storing an
                # empty argv a reader could mistake for an unrendered one.
                command_line=None,
                exit_code=run.exit_code,
                stderr=run.stderr,
                artifact_hashes=run.artifact_hashes,
                next_transition=run.transition,
                started_at=run.started_at,
                finished_at=run.finished_at,
                optional=run.planned.optional,
                # A declared-blocked optional stage is a recorded decision, not
                # an abandoned one, so it does not veto the run verdict. The
                # result model is the single owner of that rule.
                did_not_veto=optional_decision_did_not_veto(run),
            )
            for run in runs
        ],
        expected_artifact_root=str(root.resolve()),
        expected_artifacts=expected,
        missing_expected_artifacts=missing_expected,
        status=status,
        passed=passed,
        blocked_reason=blocked_reason,
        claim=claim,
        required_stage_ids=required_stage_ids,
        verification_evidence=evidence,
        included_recipe_hashes=includes,
    )



def write_result(result: RecipeRunResult, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = (out_dir / RESULT_FILENAME).resolve()
    write_run_result(path, result)
    return path


def error_result(message: str, recipe_path: Path, recipe_sha: str) -> RecipeRunResult:
    """A valid result document for a recipe that never loaded."""
    now = timestamp()
    return RecipeRunResult(
        schema=RUN_RESULT_SCHEMA_ID,
        recipe_id=recipe_path.stem or "unknown-recipe",
        recipe_version="",
        recipe_path=str(recipe_path),
        recipe_sha256=recipe_sha,
        run_id=hashlib.sha256(f"{recipe_sha}:{message}".encode("utf-8")).hexdigest()[:32],
        started_at=now,
        finished_at=now,
        stages=[],
        expected_artifact_root=str(recipe_path.resolve().parent),
        expected_artifacts={},
        missing_expected_artifacts=[],
        status="failed",
        passed=False,
        blocked_reason=None,
        claim=f"invalid: {message}",
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_recipe.py",
        description=(
            "Run exactly one deterministic workflow recipe. Stages run in declared order, one "
            "at a time, with no retries, no parallelism, and no scheduling. This is not a "
            "second Conduct and it never overrides contract requirements."
        ),
        epilog=(
            "Exit codes: 0 passed (status completed), 1 invalid recipe or usage, 2 failed run, "
            "3 blocked run. A blocked run is either a declared blocked transition or a --dry-run "
            "plan: a plan never executes, so its result is status blocked with passed false. "
            "recipe-run.json is written to --out (default: the working directory)."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "recipe_path",
        type=Path,
        help="Recipe path (.json/.yaml/.yml), or bundled study-evidence/report-preparation.",
    )
    parser.add_argument(
        "--input",
        "--inputs",
        dest="inputs",
        action="append",
        metavar="NAME=VALUE",
        help=(
            "Supply one declared recipe input; repeatable. Alias: --inputs. A missing required "
            "input, or a declared file/directory input that does not exist, is a hard failure. "
            "Nothing is discovered, globbed, or selected by mtime."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            "Directory that receives recipe-run.json and that resolves relative artifact names "
            "for stages that do not bind a run_dir input. Defaults to the caller's working "
            "directory."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Validate the recipe, resolve every input, and render the plan without running "
            "operations. Creates the output directory and writes a nonpassing plan receipt."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print each stage's operation, status, exit code, and transition to stderr.",
    )
    parser.add_argument(
        INSTALL_FLAG,
        INSTALL_FLAG_ALIAS,
        dest="installed",
        action="store_true",
        help=(
            "Select ambient imports when using the developer scripts/run_recipe.py bootstrap. "
            "python -m cept.recipes already uses the installed/ambient package and never "
            "injects checkout paths into sys.path or PYTHONPATH. The bootstrap otherwise "
            "pins its own checkout. Every run prints its import mode and resolved package."
        ),
    )
    return parser


def report(result: RecipeRunResult, runs: list[StageRun], path: Path, verbose: bool) -> None:
    print(f"recipe:   {result.recipe_id} {result.recipe_version}".rstrip())
    print(f"run id:   {result.run_id}")
    print(f"status:   {result.status}")
    print(f"passed:   {str(result.passed).lower()}")
    for run in runs:
        exit_code = "-" if run.exit_code is None else str(run.exit_code)
        if verbose:
            print(
                f"  stage {run.stage_id}: {run.status} exit={exit_code} "
                f"op={run.operation_id} -> {run.transition}"
            )
        else:
            print(f"  stage {run.stage_id}: {run.status} ({run.operation_id})")
    print(f"claim:    {result.claim}")
    print(f"result:   {path}")


def main(
    argv: list[str] | None = None,
    *,
    environment_mode: str = MODE_INSTALLED,
    source_root: Path | None = None,
) -> int:
    global ENVIRONMENT_MODE, _ROOT_SRC
    if environment_mode not in {MODE_INSTALLED, MODE_PINNED}:
        raise ValueError("unknown recipe environment mode")
    if environment_mode == MODE_PINNED and source_root is None:
        raise ValueError("pinned recipe execution requires an explicit source root")
    ENVIRONMENT_MODE = environment_mode
    _ROOT_SRC = str(source_root.resolve()) if source_root is not None else ""
    parser = build_parser()
    args = parser.parse_args(argv)

    # The flag is read from argv before import time, because it decides which
    # 'cept' gets imported. Asking for the installed mode in-process cannot undo
    # an import that already happened against the checkout, so fail closed
    # rather than run under one mode while reporting the other.
    if args.installed and ENVIRONMENT_MODE != MODE_INSTALLED:
        print(
            f"error: {INSTALL_FLAG} must be on the command line of the interpreter that runs "
            f"run_recipe.py, because it selects the 'cept' imported before any parsing. This "
            f"process started in {ENVIRONMENT_MODE} mode and has already imported 'cept' from "
            f"{active_cept_path() or 'an unknown location'}; re-run as "
            f"'python scripts/run_recipe.py {INSTALL_FLAG} ...' from the environment to verify.",
            file=sys.stderr,
        )
        return EXIT_INVALID_RECIPE

    print(f"environment: {ENVIRONMENT_MODE}")
    print(f"cept module: {active_cept_path() or '(not importable)'}")

    selector = str(args.recipe_path)
    if is_bundled_recipe(selector):
        # Installed layout: the wheel's data directory. Checkout layout: the
        # repository's recipes/, derived from where this package was imported —
        # not from the working directory and not by mtime. The checkout root is
        # offered only when it really is a repository (it has a pyproject.toml),
        # so a stray recipes/ directory beside an install cannot be picked up.
        search_roots = (
            (Path(sysconfig.get_path("data")) / "share" / "cept" / "recipes",)
            if ENVIRONMENT_MODE != MODE_PINNED
            else (Path(_ROOT_SRC).parent / "recipes",)
        )
        active = active_cept_path()
        checkout_root = Path(active).parents[2] if active else None
        if checkout_root is not None and (checkout_root / "pyproject.toml").is_file():
            search_roots = search_roots + (checkout_root / "recipes",)
        try:
            recipe_path = bundled_recipe_path(selector, search_roots)
        except RecipeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_INVALID_RECIPE
    else:
        recipe_path = args.recipe_path.expanduser().resolve()
    out_dir = args.out.expanduser().resolve() if args.out else Path.cwd()

    # Create the output directory first: an invalid recipe still has to leave a
    # result document behind, so the directory must exist before validation.
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"error: --out directory cannot be created: {out_dir} ({exc})", file=sys.stderr)
        return EXIT_INVALID_RECIPE

    recipe_sha = ""
    try:
        # Hash the raw bytes first: an invalid recipe still gets a result document.
        if recipe_path.is_file():
            recipe_sha = recipe_sha256(recipe_path)
        recipe = load_recipe(recipe_path)

        from cept.recipes.composition import expand_recipe

        recipe, included_hashes = expand_recipe(recipe, recipe_path)
        values = resolve_recipe_inputs(recipe, parse_assignments(args.inputs), Path.cwd())
        planned = plan_stages(recipe, values, out_dir)
        if included_hashes:
            print("includes: " + ", ".join(included_hashes) + " (expanded in declared order)")
        context = RecipeContext(
            recipe=recipe,
            recipe_path=recipe_path,
            recipe_sha256=recipe_sha,
            values=values,
            base_dir=out_dir,
            included_recipe_hashes=included_hashes,
        )
        runs, failed_run = execute(planned, context, args.dry_run, args.verbose)
        # Receipt ordering: when a `receipt.write` stage ran in process it already
        # wrote this run's result document, built from every recorded stage. The
        # runner adopts that exact document instead of writing a second one over
        # the same path — one write, one document, no truncated intermediate.
        adopted = runs[-1].receipt_document if runs else None
        if adopted is not None:
            result, path = adopted, runs[-1].receipt_path
            if path is None:
                raise RecipeError("receipt operation returned a document without a destination")
        else:
            try:
                result = build_result(
                    recipe=recipe,
                    recipe_path=recipe_path,
                    recipe_sha=recipe_sha,
                    base_dir=out_dir,
                    values=values,
                    runs=runs,
                    failed_run=failed_run,
                    dry_run=args.dry_run,
                    included_recipe_hashes=included_hashes,
                )
            except ValidationError as exc:
                # The result model refused to describe this run. Report that as a
                # failed run with a written document rather than letting a
                # pydantic traceback escape: --help promises recipe-run.json is
                # always written, and a crash would break that promise while
                # hiding the real reason.
                message = f"run result could not be assembled: {exc}"
                print(f"error: {message}", file=sys.stderr)
                result = error_result(message, recipe_path, recipe_sha)
            path = write_result(result, out_dir)
        report(result, runs, path, args.verbose)
        if result.passed:
            return EXIT_PASSED
        return EXIT_BLOCKED if result.status == "blocked" else EXIT_FAILED
    except (RecipeError, ValueError, OSError) as exc:
        message = str(exc)
        print(f"error: {message}", file=sys.stderr)
        if recipe_sha:
            try:
                path = write_result(error_result(message, recipe_path, recipe_sha), out_dir)
                print(f"result:  {path}", file=sys.stderr)
            except Exception as write_exc:  # noqa: BLE001 - reporting must not hide the failure
                print(f"result:  (not written: {write_exc})", file=sys.stderr)
        else:
            print("result:  (not written: the recipe file itself could not be hashed)", file=sys.stderr)
        return EXIT_INVALID_RECIPE


if __name__ == "__main__":
    raise SystemExit(main())