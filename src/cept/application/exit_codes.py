"""Canonical process exit codes, owned by the application layer.

These three values are the exit-code contract of the whole product, not of one
front door.  ``cept.application.operations`` returns them as an operation
outcome, ``cept.verification.run_set`` reports a verdict with them, and the CLI
front doors map them onto a process status.  They therefore live here, below
both callers, so core never has to import ``cept.cli`` to name a failure.

This module deliberately imports nothing.  ``cept.cli.main`` needs
:data:`EXIT_ERROR` for its catch-all handler, and defining the constants in a
leaf module keeps ``main`` from importing the study engine (and through it
reporting/validation/networkx/plotly) merely to know that failure is exit 1.

The former home, ``cept.cli.exit_codes``, was deleted: there is exactly one
definition of each constant, here.
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_POLICY = 3

__all__ = ["EXIT_OK", "EXIT_ERROR", "EXIT_POLICY"]