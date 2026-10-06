"""Verb-only CLI shipped in the CEPT Public wheel.

The public grammar is verb-only (owner-decided 2026-10-03), and this wheel is
the surface learners actually install, so it speaks the same verbs the full CLI
does: ``cept doctor``, ``cept run``, and ``cept verify``.

Two deliberate decisions:

* ``--demo`` is a mode of ``cept run``, not a ``study demo`` noun+verb route.
  The bundled demonstration is the same operation (``run_study``) over a Case
  CEPT builds for you, and the verb set is closed, so it becomes a flag on the
  verb that owns running rather than a second grammar.
* ``capability show`` becomes ``cept doctor --capabilities``. The verb set is
  closed, and ``capability`` would be an eighth verb, while a bare
  ``cept capabilities`` would be exactly the noun-less noun this migration
  exists to remove. The support boundary is an environment fact: ``doctor``
  already carries ``support`` in its JSON payload, and ``--verbose`` already
  means "capability details" in the full CLI. So the flag is the same question
  asked of the same verb. The noun does not survive.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from cept.public import (
    PublicBoundaryError,
    demo_case,
    load_case,
    public_capabilities,
    public_version,
    run_study,
    verify_study,
)


def _add_format(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--format",
        choices=("auto", "text", "json"),
        default="auto",
        help="human terminal text or stable machine JSON (default: auto)",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cept",
        description="CEPT Public — OpenDSS-first reproducible power-system studies.",
    )
    parser.add_argument("--version", action="version", version=f"cept-power-studio {public_version()}")
    verbs = parser.add_subparsers(dest="verb", required=True)

    check = verbs.add_parser("check", help="read a Case before running it")
    check.add_argument("case", type=Path)
    check.add_argument(
        "--per-unit",
        dest="per_unit",
        action="store_true",
        help="also audit the Case's declared per-unit and kV bases for internal consistency",
    )
    _add_format(check)

    doctor = verbs.add_parser("doctor", help="check the CEPT teaching environment")
    doctor.add_argument(
        "--capabilities",
        action="store_true",
        help="show the public support boundary instead of running the readiness trial",
    )
    _add_format(doctor)

    run = verbs.add_parser("run", help="run a JSON Case with OpenDSS")
    run.add_argument(
        "case",
        type=Path,
        nargs="?",
        help="Case JSON to run. Omit it only with --demo, which runs a bundled Case.",
    )
    run.add_argument("--out", type=Path)
    run.add_argument("--force", action="store_true")
    run.add_argument(
        "--demo",
        nargs="?",
        const="load-flow",
        choices=["load-flow", "unbalanced-load-flow", "hosting-capacity", "fault"],
        default=None,
        help="run a bundled demonstration Case instead of naming a Case path",
    )
    run.add_argument("--network", choices=["ieee13"], default="ieee13")
    run.add_argument(
        "--recipe",
        default=None,
        metavar="NAME|FILE",
        help="run a whole workflow: a bundled recipe name, or a path to one",
    )
    run.add_argument(
        "--input",
        "--inputs",
        dest="inputs",
        action="append",
        default=None,
        metavar="NAME=VALUE",
        help="supply one declared recipe input; repeatable, e.g. case=case.json",
    )
    run.add_argument(
        "--verbose",
        action="store_true",
        help="with --recipe: print each stage's operation, status, exit code and transition",
    )
    run.add_argument(
        "--explain",
        action="store_true",
        help=(
            "with --recipe: print what the workflow needs, does in order, must produce, "
            "and cannot complete, then stop. Resolves no input and runs nothing."
        ),
    )
    run.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="with --recipe: render the plan and execute nothing",
    )
    _add_format(run)

    verify = verbs.add_parser("verify", help="verify a persisted public run")
    verify.add_argument("run_dir", type=Path)
    verify.add_argument(
        "--physics",
        action="store_true",
        help="also run the physics audit over this run and write physics-audit.json beside its evidence",
    )
    _add_format(verify)
    return parser


def _resolved_format(requested: str) -> str:
    if requested in {"text", "json"}:
        return requested
    is_tty = getattr(sys.stdout, "isatty", lambda: False)()
    return "text" if is_tty else "json"

HUMAN_STUDY_NAMES = {
    "load_flow": "Load flow",
    "unbalanced_load_flow": "Unbalanced load flow",
    "hosting_capacity": "Hosting capacity",
    "fault": "Fault study",
}

HUMAN_STUDY_CONTEXT = {
    "load_flow": "balanced load flow",
    "unbalanced_load_flow": "unbalanced load flow (IEEE 13-node)",
    "hosting_capacity": "hosting-capacity search",
    "fault": "fault study",
}

CHECK_GROUPS = (
    ("Case identity", ("case_fingerprint", "study_identity", "manifest_identity", "stored_receipt_identity")),
    (
        "Solver result",
        (
            "solver_identity",
            "load_flow_convergence",
            "load_flow_quantities",
            "hosting_capacity_rows",
            "fault_result",
        ),
    ),
    (
        "Saved evidence",
        (
            "manifest_schema",
            "manifest_study_identity",
            "manifest_engine_identity",
            "validation_identity",
            "attempt_identity",
            "validation_receipt",
            "artifact_integrity",
        ),
    ),
)

MEANING_RUN_OK = (
    "The study completed and saved solver-backed evidence.",
    "It does NOT approve a real project or field installation.",
)

MEANING_ENVIRONMENT_OK = (
    "CEPT and OpenDSS are installed and can run a teaching study.",
    "This checks the teaching environment, not a real electrical system.",
)

MEANING_SCOPE = (
    "These are solver-backed teaching examples.",
    "They do NOT approve a real project or field installation.",
)

MEANING_ATTENTION = (
    "CEPT could not complete this step safely.",
    "Fix the issue above before continuing.",
)

MEANING_VERIFY_OK = (
    "The saved result matches its Case, solver run, and saved evidence.",
    "It does NOT approve a real project or field installation.",
)
MEANING_CHECK_OK = (
    "The Case is readable and its declared values are internally consistent.",
    "It does NOT approve a real project or field installation.",
)

def _human_study(study_type: Any) -> str:
    key = str(study_type or "")
    named = HUMAN_STUDY_NAMES.get(key)
    if named is not None:
        return named
    return key.replace("_", " ").strip().title() or "Unknown study"


def _human_engine(engine: Any) -> str:
    key = str(engine or "").strip().lower()
    if key == "opendss":
        return "OpenDSS"
    return str(engine) if engine else "Unknown engine"


def _check_group_counts(checks: Any) -> list[tuple[str, int, bool]]:
    by_name = {}
    if isinstance(checks, list):
        for check in checks:
            if isinstance(check, dict) and check.get("name") is not None:
                by_name[str(check.get("name"))] = check.get("passed") is True
    grouped: list[tuple[str, int, bool]] = []
    for label, names in CHECK_GROUPS:
        present = [name for name in names if name in by_name]
        if present:
            grouped.append((label, len(present), all(by_name[name] for name in present)))
    return grouped


def _terminal_header(title: str) -> None:
    print(title)
    print("-" * max(28, min(72, len(title))))


def _terminal_row(label: str, value: Any) -> None:
    lines = str(value).splitlines() or [""]
    print(f"{label:<18} {lines[0]}")
    for line in lines[1:]:
        print(f"{'':18} {line}")


def _terminal_next(command: str) -> None:
    print()
    print("Next")
    print(f"  {command}")


def _display_path(value: Any) -> str:
    path = Path(str(value))
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)

def _human_error(exc: Exception) -> str:
    if isinstance(exc, FileNotFoundError):
        target = str(exc.filename or "the requested file")
        return f"I couldn't find {target}."
    if isinstance(exc, FileExistsError):
        target = str(exc.filename or "that output location")
        return f"{target} already exists. Use --force to replace it."
    if isinstance(exc, PermissionError):
        return "CEPT could not access that file or folder. Check its permissions."
    if isinstance(exc, OSError):
        return "CEPT could not read or write a required file. Check the path and permissions."
    detail = str(exc).strip()
    if detail.startswith("cannot read JSON file ") and "[Errno 2]" in detail:
        target = detail[len("cannot read JSON file ") :].split(": [Errno 2]", 1)[0]
        return f"I couldn't find {target}."
    return detail or "The input could not be used."


def _terminal_detail(command: str) -> None:
    print()
    print("For the full check list")
    print(f"  {command}")


def _engine_versions(value: Any) -> list[tuple[str, str]]:
    # Retained for backwards-compatible imports; the human text renderer now
    # summarizes the installed OpenDSS version in one line.
    lines = [line.strip() for line in str(value).splitlines() if line.strip()]
    rows: list[tuple[str, str]] = []
    for line in lines:
        if line.startswith("DSS C-API Library version "):
            version = line.removeprefix("DSS C-API Library version ").split()[0]
            rows.append(("OpenDSS", version))
        elif line.startswith("DSS-Python version:"):
            rows.append(("DSS-Python", line.split(":", 1)[1].strip()))
        elif line.startswith("OpenDSSDirect.py version:"):
            rows.append(("Direct.py", line.split(":", 1)[1].strip()))
    return rows or [("Version", str(value))]


def _engine_version_summary(value: Any) -> str:
    text = str(value)
    for line in (item.strip() for item in text.splitlines() if item.strip()):
        if line.startswith("DSS C-API Library version "):
            return f"OpenDSS {line.removeprefix('DSS C-API Library version ').split()[0]}"
    return text


def _terminal_meaning(*lines: str) -> None:
    print()
    print("What this means")
    for line in lines:
        print(f"  {line}")


def _print_terminal(kind: str, payload: dict[str, Any]) -> None:
    if kind == "environment":
        ok = payload.get("status") == "PASS"
        state = "READY" if ok else "NEEDS ATTENTION"
        _terminal_header(f"CEPT environment check: {state}")
        if ok:
            _terminal_row("Solver", f"{_human_engine(payload.get('engine'))} (ready)")
            _terminal_row("Version", _engine_version_summary(payload.get("engine_version", "unknown")))
            _terminal_meaning(*MEANING_ENVIRONMENT_OK)
            _terminal_next("cept doctor --capabilities --format text")
        else:
            _terminal_row("Solver", f"{_human_engine(payload.get('engine'))} (not ready)")
            _terminal_row("Problem", "CEPT could not start OpenDSS or finish the trial study.")
            _terminal_meaning(*MEANING_ATTENTION)
            _terminal_next("cept doctor --format json")
        return

    if kind == "capability":
        _terminal_header("CEPT studies you can try")
        print("Teaching examples (not project approval)")
        capabilities = payload.get("capabilities", {})
        if isinstance(capabilities, dict):
            for study, details in sorted(capabilities.items()):
                if isinstance(details, dict):
                    print(f"  {_human_study(study)} ({_human_engine(details.get('engine'))})")
        _terminal_meaning(*MEANING_SCOPE)
        _terminal_next("cept doctor --format text")
        return

    if kind == "check":
        ok = payload.get("status") == "PASS"
        _terminal_header(f"CEPT Case check: {'PASSED' if ok else 'NEEDS ATTENTION'}")
        for line in payload.get("lines") or ():
            print(line)
        audit = payload.get("per_unit_audit")
        if isinstance(audit, dict):
            reasons = audit.get("reasons") or []
            print()
            _terminal_row(
                "Per-unit audit",
                "passed" if audit.get("passed") is True else f"{len(reasons)} problem(s) found",
            )
            for reason in reasons[:5]:
                print(f"    - {reason}")
        _terminal_meaning(*(MEANING_CHECK_OK if ok else MEANING_ATTENTION))
        _terminal_next("cept run <case.json> --out <run-dir>")
        return

    if kind in {"run", "demo"}:
        ok = payload.get("status") == "PASS"
        state = "FINISHED" if ok else "NEEDS ATTENTION"
        _terminal_header(f"CEPT study result: {state}")
        context = HUMAN_STUDY_CONTEXT.get(str(payload.get("study_type", "")), "study")
        if ok:
            _terminal_row("Result", f"Finished the {context} and saved the evidence")
        else:
            _terminal_row("Result", f"The {context} did not finish cleanly")
        run_dir = payload.get("run_dir")
        display_run = _display_path(run_dir) if run_dir else ""
        if display_run:
            _terminal_row("Saved run", display_run)
        fingerprint = payload.get("case_fingerprint", "")
        if fingerprint:
            _terminal_row("Case fingerprint", f"{fingerprint} (matches the case you ran)")
        _terminal_meaning(*(MEANING_RUN_OK if ok else MEANING_ATTENTION))
        if display_run:
            _terminal_next(f"cept verify {display_run} --format text")
        return

    if kind == "verify":
        ok = payload.get("passed") is True
        state = "PASSED" if ok else "NEEDS ATTENTION"
        _terminal_header(f"CEPT study check: {state}")
        _terminal_row(
            "Study",
            f"{_human_study(payload.get('study_type'))} ({_human_engine(payload.get('engine'))})",
        )
        fingerprint = payload.get("case_fingerprint", "")
        if fingerprint:
            _terminal_row("Case fingerprint", f"{fingerprint} (matches the case you ran)")
        grouped = _check_group_counts(payload.get("checks"))
        if grouped:
            print()
            checked = sum(count for _, count, _ in grouped)
            summary = "all passed" if ok else "needs attention"
            print(f"Checked   {len(grouped)} groups, {checked} checks, {summary}")
            for label, count, group_ok in grouped:
                print(f"  [{'PASS' if group_ok else 'FAIL'}] {label} ({count} checks)")
        run_dir = payload.get("run_dir")
        physics = payload.get("physics_audit")
        if isinstance(physics, dict):
            audit_ok = physics.get("passed") is True
            reasons = physics.get("reasons") or []
            detail = "passed" if audit_ok else f"{len(reasons)} problem(s) found"
            _terminal_row("Physics audit", detail)
            if not audit_ok:
                for reason in reasons[:5]:
                    print(f"    - {reason}")
        _terminal_meaning(*(MEANING_VERIFY_OK if ok else MEANING_ATTENTION))
        if run_dir:
            print()
            display_run = _display_path(run_dir)
            _terminal_row("Saved evidence", Path(display_run) / "public-verification.json")
            _terminal_detail(f"cept verify {display_run} --format json")
            if not isinstance(physics, dict):
                _terminal_next(f"cept verify {display_run} --physics --format text")
            else:
                _terminal_row("Audit record", Path(display_run) / str(physics.get("record_path")))
        return

    raise ValueError(f"unknown terminal payload kind: {kind}")

def _emit(kind: str, payload: dict[str, Any], requested: str) -> None:
    if _resolved_format(requested) == "json":
        print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
        return
    _print_terminal(kind, payload)


def _environment_check_payload() -> tuple[int, dict[str, Any]]:
    try:
        import opendssdirect as dss

        version = str(dss.Basic.Version())
    except Exception as exc:  # pragma: no cover - exact native error varies by platform
        payload = {
            "status": "BLOCKED",
            "edition": "public",
            "engine": "opendss",
            "engine_version": "unavailable",
            "trial_solve": "BLOCKED",
            "trial_detail": f"OpenDSSDirect unavailable: {exc}",
            "support": public_capabilities()["support"],
        }
        return 1, payload
    try:
        trial = run_study(demo_case("load-flow", network="ieee13"))
        trial_ok = bool(trial.verification.get("passed"))
        trial_detail = str(trial.verification.get("status"))
    except Exception as exc:
        trial_ok = False
        trial_detail = f"{type(exc).__name__}: {exc}"
    payload = {
        "status": "PASS" if trial_ok else "BLOCKED",
        "edition": "public",
        "engine": "opendss",
        "engine_version": version,
        "trial_solve": "PASS" if trial_ok else "BLOCKED",
        "trial_detail": trial_detail,
        "support": public_capabilities()["support"],
    }
    return (0 if trial_ok else 1), payload


def _refuse(message: str) -> int:
    """Print a grammar refusal and return the refusal exit code."""
    print("error: " + message, file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.verb == "doctor":
            if args.capabilities:
                _emit("capability", public_capabilities(), args.format)
                return 0
            returncode, payload = _environment_check_payload()
            _emit("environment", payload, args.format)
            return returncode
        if args.verb == "check":
            return _check_verb(args)
        if args.verb == "verify":
            return _verify_verb(args)
        if args.verb == "run":
            return _run_verb(args)
    except (PublicBoundaryError, FileExistsError, OSError, ValueError) as exc:
        print("CEPT could not complete this command.", file=sys.stderr)
        print(f"Problem: {_human_error(exc)}", file=sys.stderr)
        print("Next: fix the issue above, then run the command again.", file=sys.stderr)
        return 1
    raise RuntimeError("unhandled public CLI route")


def _check_verb(args: argparse.Namespace) -> int:
    """``cept check``: read one typed Case before anything runs on it.

    The same neutral operations the internal verb calls, so the two front doors
    cannot describe a Case differently. ``--per-unit`` adds the base audit as a
    separate verdict: a readiness pass is not an audit pass.
    """
    from cept.application.operations.audit import (
        PerUnitAuditRequest,
        per_unit_audit_operation,
    )
    from cept.application.operations.check import (
        CheckCaseRequest,
        check_case_operation,
    )

    case_path = Path(args.case)
    readiness = check_case_operation(CheckCaseRequest(case_path=case_path))
    audit = None
    if args.per_unit:
        audit = per_unit_audit_operation(PerUnitAuditRequest(case_path=case_path))
    payload: dict[str, Any] = {
        "status": "PASS" if readiness.exit_code == 0 else "BLOCKED",
        "lines": readiness.lines,
    }
    if audit is not None:
        payload["per_unit_audit"] = {
            **audit.record,
            "error": audit.error,
        }
    _emit("check", payload, args.format)
    statuses = [readiness.exit_code]
    if audit is not None:
        statuses.append(0 if (audit.error is None and audit.passed) else 1)
    return 0 if all(code == 0 for code in statuses) else 1


def _verify_verb(args: argparse.Namespace) -> int:
    """``cept verify``: verify one persisted run, and with ``--physics`` audit it.

    The two verdicts stay separate claims in one payload. ``passed`` is still
    the verification's own verdict over its own artifacts; ``physics_audit`` is
    the audit's separate verdict. They are never merged into one flag, because
    a verification pass is not an audit pass.

    The audit runs only over a run that verified. An unverified run has no
    artifact set the audit could honestly read, so it is refused instead of
    audited and no record is written.
    """
    result = verify_study(args.run_dir)
    if not args.physics:
        _emit("verify", result, args.format)
        return 0 if result.get("passed") is True else 1
    if result.get("passed") is not True:
        print(
            "error: --physics needs a run that passed verification; an unverified "
            "run is not audited and no physics-audit.json is written.",
            file=sys.stderr,
        )
        _emit("verify", result, args.format)
        return 1
    from cept.application.operations.audit import (
        PHYSICS_AUDIT_RECORD,
        PhysicsAuditRequest,
        physics_audit_operation,
    )

    run_dir = Path(args.run_dir).resolve()
    outcome = physics_audit_operation(
        PhysicsAuditRequest(run_dir=run_dir, out=run_dir / PHYSICS_AUDIT_RECORD)
    )
    result["physics_audit"] = {**outcome.record, "record_path": PHYSICS_AUDIT_RECORD}
    _emit("verify", result, args.format)
    return 0 if outcome.passed else 1


def _run_verb(args: argparse.Namespace) -> int:
    """``cept run``: exactly one of a Case path or ``--demo``, never a guess."""
    if args.recipe:
        return _run_recipe(args)
    # Recipe-only options on a Case or demo run would be silently ignored, and a
    # caller who wrote `--dry-run` would get a real run. Refuse, as the full CLI does.
    stray = [
        flag
        for flag, used in (
            ("--input", args.inputs),
            ("--dry-run", args.dry_run),
            ("--explain", args.explain),
            ("--verbose", args.verbose),
        )
        if used
    ]
    if stray:
        return _refuse(
            f"`cept run` was given recipe-only option(s) {' '.join(stray)} without --recipe."
        )
    if args.case and args.demo:
        return _refuse(
            "`cept run` was given both a Case path and --demo. Use "
            "`cept run <case.json> --out <run-dir>` or `cept run --demo --out <run-dir>`."
        )
    if not args.case and not args.demo:
        return _refuse(
            "`cept run` was given neither a Case path nor --demo. Use "
            "`cept run <case.json> --out <run-dir>` or `cept run --demo --out <run-dir>`."
        )
    if args.out is None:
        return _refuse("`cept run` needs `--out <run-dir>` to write this run's evidence.")

    if args.demo:
        case = demo_case(args.demo, network=args.network)
        study_type = case.study.type
        kind = "demo"
    else:
        case = load_case(args.case)
        study_type = case.study.type
        kind = "run"
    run = run_study(case, out=args.out, force=args.force)
    payload = {
        "status": run.verification["status"],
        "claim": run.verification["claim"],
        "run_dir": str(run.run_dir) if run.run_dir else None,
        "case_fingerprint": run.case.fingerprint(),
    }
    if kind == "demo":
        payload["study_type"] = run.result.study_type
    else:
        payload["study_type"] = study_type
    _emit(kind, payload, args.format)
    return 0 if run.verification["passed"] else 1


def _run_recipe(args: argparse.Namespace) -> int:
    """``cept run --recipe``: hand the whole workflow to the shipped recipe runtime.

    The recipes ship in this wheel, so refusing the flag would deny a capability
    that is present. The runner owns the recipe grammar — bundled name or path,
    declared inputs, and ``--dry-run`` — and writes its own receipt, so this
    function only forwards the arguments and returns the runner's exit code.
    """

    from cept.recipes.runner import main as recipe_main

    # A run writes evidence, so where it lands is the caller's decision, as it
    # is for `cept run <case.json>`. `--explain` writes nothing and needs none.
    if args.out is None and not getattr(args, "explain", False):
        return _refuse(
            "`cept run --recipe` needs `--out <dir>` for the workflow receipt; "
            "`--explain` alone reads the recipe and writes nothing."
        )
    argv = [str(args.recipe)]
    for item in args.inputs or ():
        argv += ["--input", item]
    if args.out is not None:
        argv += ["--out", str(args.out)]
    if getattr(args, "explain", False):
        argv.append("--explain")
    if args.dry_run:
        argv.append("--dry-run")
    if args.verbose:
        argv.append("--verbose")
    return recipe_main(argv)

if __name__ == "__main__":
    raise SystemExit(main())
