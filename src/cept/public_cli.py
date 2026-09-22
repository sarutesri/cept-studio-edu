"""Small noun+verb CLI shipped in the CEPT Public wheel."""

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
    nouns = parser.add_subparsers(dest="noun", required=True)

    environment = nouns.add_parser("environment", help="check the CEPT teaching environment")
    environment_verbs = environment.add_subparsers(dest="verb", required=True)
    check = environment_verbs.add_parser("check", help="check CEPT and the installed OpenDSS solver")
    _add_format(check)

    study = nouns.add_parser("study", help="run or verify a public study")
    study_verbs = study.add_subparsers(dest="verb", required=True)
    run = study_verbs.add_parser("run", help="run a JSON Case with OpenDSS")
    run.add_argument("case", type=Path)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--force", action="store_true")
    _add_format(run)
    demo = study_verbs.add_parser("demo", help="run a bundled public demonstration")
    demo.add_argument(
        "study",
        choices=["load-flow", "unbalanced-load-flow", "hosting-capacity", "fault"],
        nargs="?",
        default="load-flow",
    )
    demo.add_argument("--network", choices=["ieee13"], default="ieee13")
    demo.add_argument("--out", type=Path, required=True)
    demo.add_argument("--force", action="store_true")
    _add_format(demo)
    verify = study_verbs.add_parser("verify", help="verify a persisted public run")
    verify.add_argument("run_dir", type=Path)
    _add_format(verify)

    capability = nouns.add_parser("capability", help="show the public support boundary")
    capability_verbs = capability.add_subparsers(dest="verb", required=True)
    show = capability_verbs.add_parser("show", help="show supported public study types")
    _add_format(show)
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
            _terminal_next("cept capability show --format text")
        else:
            _terminal_row("Solver", f"{_human_engine(payload.get('engine'))} (not ready)")
            _terminal_row("Problem", "CEPT could not start OpenDSS or finish the trial study.")
            _terminal_meaning(*MEANING_ATTENTION)
            _terminal_next("cept environment check --format json")
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
        _terminal_next("cept environment check --format text")
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
            _terminal_next(f"cept study verify {display_run} --format text")
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
        _terminal_meaning(*(MEANING_VERIFY_OK if ok else MEANING_ATTENTION))
        if run_dir:
            print()
            display_run = _display_path(run_dir)
            _terminal_row("Saved evidence", Path(display_run) / "public-verification.json")
            _terminal_detail(f"cept study verify {display_run} --format json")
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


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.noun == "environment" and args.verb == "check":
            returncode, payload = _environment_check_payload()
            _emit("environment", payload, args.format)
            return returncode
        if args.noun == "capability" and args.verb == "show":
            _emit("capability", public_capabilities(), args.format)
            return 0
        if args.noun == "study" and args.verb == "verify":
            result = verify_study(args.run_dir)
            _emit("verify", result, args.format)
            return 0 if result.get("passed") is True else 1
        if args.noun == "study" and args.verb == "run":
            case = load_case(args.case)
            run = run_study(case, out=args.out, force=args.force)
            payload = {
                "status": run.verification["status"],
                "claim": run.verification["claim"],
                "run_dir": str(run.run_dir) if run.run_dir else None,
                "case_fingerprint": run.case.fingerprint(),
            }
            _emit("run", payload, args.format)
            return 0 if run.verification["passed"] else 1
        if args.noun == "study" and args.verb == "demo":
            case = demo_case(args.study, network=args.network)
            run = run_study(case, out=args.out, force=args.force)
            payload = {
                "status": run.verification["status"],
                "claim": run.verification["claim"],
                "study_type": run.result.study_type,
                "run_dir": str(run.run_dir) if run.run_dir else None,
                "case_fingerprint": run.case.fingerprint(),
            }
            _emit("demo", payload, args.format)
            return 0 if run.verification["passed"] else 1
    except (PublicBoundaryError, FileExistsError, OSError, ValueError) as exc:
        print("CEPT could not complete this command.", file=sys.stderr)
        print(f"Problem: {_human_error(exc)}", file=sys.stderr)
        print("Next: fix the issue above, then run the command again.", file=sys.stderr)
        return 1
    raise RuntimeError("unhandled public CLI route")


if __name__ == "__main__":
    raise SystemExit(main())
