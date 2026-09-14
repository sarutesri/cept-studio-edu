#!/usr/bin/env python3
"""Execute the public notebooks with a supplied, freshly installed Python.

The runner is intentionally a subprocess boundary.  The Python executable is
provided by the caller (normally a clean virtual environment containing the
public wheel and its ``notebooks`` extra), and the notebook kernel runs from a
separate output directory with the source checkout removed from
``PYTHONPATH``.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable


def discover_notebooks(paths: Iterable[Path] | None = None) -> list[Path]:
    """Return sorted notebook paths, rejecting missing or non-notebook inputs."""

    selected = (
        list(paths)
        if paths is not None
        else sorted((Path(__file__).resolve().parents[1] / "public" / "notebooks").glob("*.ipynb"))
    )
    if not selected:
        raise ValueError("no public notebooks were selected")
    notebooks: list[Path] = []
    for path in selected:
        resolved = path.resolve()
        if resolved.suffix.lower() != ".ipynb" or not resolved.is_file():
            raise FileNotFoundError(f"public notebook does not exist: {path}")
        notebooks.append(resolved)
    return sorted(notebooks)


def run_notebook(
    notebook: Path,
    *,
    python_executable: str,
    output_dir: Path,
    timeout: int,
) -> dict[str, Any]:
    """Execute one notebook and return a deterministic status record."""

    work_dir = output_dir / notebook.stem
    work_dir.mkdir(parents=True, exist_ok=True)
    source_copy = work_dir / "source.ipynb"
    shutil.copy2(notebook, source_copy)
    executed_output = work_dir / "executed.ipynb"
    command = [
        python_executable,
        "-m",
        "jupyter",
        "nbconvert",
        "--to",
        "notebook",
        "--execute",
        f"--ExecutePreprocessor.timeout={timeout}",
        "--ExecutePreprocessor.kernel_name=python3",
        "--output-dir",
        str(work_dir),
        "--output",
        executed_output.name,
        source_copy.name,
    ]
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"
    # The kernel shell must resolve the same interpreter environment that
    # executes the notebook: lessons type literal `!cept ...` commands, so the
    # Scripts directory of the supplied Python leads PATH. An unactivated venv
    # would otherwise let an unrelated `cept` shadow the intended one.
    env["PATH"] = str(Path(python_executable).resolve().parent) + os.pathsep + env.get("PATH", "")
    completed = subprocess.run(
        command,
        cwd=work_dir,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    record: dict[str, Any] = {
        "notebook": str(notebook),
        "status": "PASS" if completed.returncode == 0 else "FAIL",
        "output": str(executed_output),
        "returncode": completed.returncode,
        "command": command,
    }
    if completed.stdout:
        record["stdout_tail"] = completed.stdout[-4000:]
    if completed.stderr:
        record["stderr_tail"] = completed.stderr[-4000:]
    return record


def run_notebooks(
    *,
    python_executable: str,
    output_dir: Path,
    notebooks: Iterable[Path] | None = None,
    timeout: int = 300,
) -> dict[str, Any]:
    """Execute all selected notebooks and return a machine-readable report."""

    if timeout <= 0:
        raise ValueError("notebook timeout must be positive")
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    selected = discover_notebooks(notebooks)
    records = [
        run_notebook(
            notebook,
            python_executable=python_executable,
            output_dir=output_dir,
            timeout=timeout,
        )
        for notebook in selected
    ]
    return {
        "schema": "cept-public-notebook-run-v1",
        "python": str(Path(python_executable).resolve()),
        "status": "PASS" if all(item["status"] == "PASS" for item in records) else "FAIL",
        "notebooks": records,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", dest="python_executable", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--notebook", dest="notebooks", action="append", type=Path)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        result = run_notebooks(
            python_executable=args.python_executable,
            output_dir=args.output_dir,
            notebooks=args.notebooks,
            timeout=args.timeout,
        )
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(f"public notebook runner: BLOCKED — {exc}", file=sys.stderr)
        return 1
    if args.report:
        report = args.report.resolve()
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
