"""Small noun+verb CLI shipped in the CEPT Public wheel."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cept.public import (
    PublicBoundaryError,
    demo_case,
    load_case,
    public_capabilities,
    public_version,
    run_study,
    verify_study,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cept",
        description="CEPT Public — OpenDSS-first reproducible power-system studies.",
    )
    parser.add_argument("--version", action="version", version=f"cept-power-studio {public_version()}")
    nouns = parser.add_subparsers(dest="noun", required=True)

    system = nouns.add_parser("system", help="inspect the public runtime")
    system_verbs = system.add_subparsers(dest="verb", required=True)
    system_verbs.add_parser("doctor", help="check the installed OpenDSS runtime")

    study = nouns.add_parser("study", help="run or verify a public study")
    study_verbs = study.add_subparsers(dest="verb", required=True)
    run = study_verbs.add_parser("run", help="run a JSON Case with OpenDSS")
    run.add_argument("case", type=Path)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--force", action="store_true")
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
    verify = study_verbs.add_parser("verify", help="verify a persisted public run")
    verify.add_argument("run_dir", type=Path)

    capability = nouns.add_parser("capability", help="show the public support boundary")
    capability_verbs = capability.add_subparsers(dest="verb", required=True)
    capability_verbs.add_parser("show", help="show supported public study types")
    return parser


def _doctor() -> int:
    try:
        import opendssdirect as dss

        version = str(dss.Basic.Version())
    except Exception as exc:  # pragma: no cover - exact native error varies by platform
        print(f"CEPT Public doctor: BLOCKED — OpenDSSDirect unavailable: {exc}", file=sys.stderr)
        return 1
    try:
        trial = run_study(demo_case("load-flow", network="ieee13"))
        trial_ok = bool(trial.verification.get("passed"))
        trial_detail = str(trial.verification.get("status"))
    except Exception as exc:
        trial_ok = False
        trial_detail = f"{type(exc).__name__}: {exc}"
    print(
        json.dumps(
            {
                "status": "PASS" if trial_ok else "BLOCKED",
                "edition": "public",
                "engine": "opendss",
                "engine_version": version,
                "trial_solve": "PASS" if trial_ok else "BLOCKED",
                "trial_detail": trial_detail,
                "support": public_capabilities()["support"],
            },
            indent=2,
        )
    )
    return 0 if trial_ok else 1


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.noun == "system" and args.verb == "doctor":
            return _doctor()
        if args.noun == "capability" and args.verb == "show":
            print(json.dumps(public_capabilities(), indent=2, sort_keys=True))
            return 0
        if args.noun == "study" and args.verb == "verify":
            result = verify_study(args.run_dir)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result.get("passed") is True else 1
        if args.noun == "study" and args.verb == "run":
            case = load_case(args.case)
            run = run_study(case, out=args.out, force=args.force)
            print(
                json.dumps(
                    {
                        "status": run.verification["status"],
                        "claim": run.verification["claim"],
                        "run_dir": str(run.run_dir) if run.run_dir else None,
                        "case_fingerprint": run.case.fingerprint(),
                    },
                    indent=2,
                )
            )
            return 0 if run.verification["passed"] else 1
        if args.noun == "study" and args.verb == "demo":
            case = demo_case(args.study, network=args.network)
            run = run_study(case, out=args.out, force=args.force)
            print(
                json.dumps(
                    {
                        "status": run.verification["status"],
                        "claim": run.verification["claim"],
                        "study_type": run.result.study_type,
                        "run_dir": str(run.run_dir) if run.run_dir else None,
                        "case_fingerprint": run.case.fingerprint(),
                    },
                    indent=2,
                )
            )
            return 0 if run.verification["passed"] else 1
    except (PublicBoundaryError, FileExistsError, OSError, ValueError) as exc:
        print(f"CEPT Public: BLOCKED — {exc}", file=sys.stderr)
        return 1
    raise RuntimeError("unhandled public CLI route")


if __name__ == "__main__":
    raise SystemExit(main())
