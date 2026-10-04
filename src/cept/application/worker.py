"""Launch a packaged CEPT workflow worker with the active interpreter.

This is process infrastructure, not command logic, so it lives in the
application layer: the neutral comparison operation
(:mod:`cept.application.operations.compare`) launches packaged lane workers
without importing the CLI.

A worker is a normal Python module invoked as ``python -m <module>`` so the
public surface keeps an installed-product process boundary. The lane workers a
public install may invoke are only those the installed runtime actually ships;
when one is not present, this fails closed with a single named message and
:data:`EXIT_POLICY` rather than leaking a subprocess traceback or pretending the
lane succeeded.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from collections.abc import Sequence

from cept.application.exit_codes import EXIT_ERROR, EXIT_POLICY


def worker_available(module: str) -> bool:
    """Return whether ``module`` can be imported in the active environment."""
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def run_packaged_worker(module: str, arguments: Sequence[str]) -> int:
    """Run a packaged worker module with the active Python environment.

    Returns the worker's own exit code, :data:`EXIT_ERROR` if the process could
    not be started, or :data:`EXIT_POLICY` when the named worker module is not
    part of this install (a bounded refusal that records a blocked lane rather
    than a fabricated comparison).
    """

    if not worker_available(module):
        print(
            f"blocked: comparison lane worker {module!r} is not part of this "
            "install; no comparison was run and no cross-engine agreement is claimed",
            file=sys.stderr,
        )
        return EXIT_POLICY
    try:
        completed = subprocess.run(
            [sys.executable, "-m", module, *(str(argument) for argument in arguments)],
            check=False,
        )
    except OSError as exc:
        print(f"unable to start CEPT worker {module!r}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    return int(completed.returncode)


__all__ = ["run_packaged_worker", "worker_available"]