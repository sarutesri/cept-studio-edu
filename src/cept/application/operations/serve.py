"""Neutral application operations for the local report review bridge.

``cept report serve`` and ``cept report open`` are presentation-only CLI
surfaces over one localhost HTTP server.  Both CLI handlers are thin wrappers
around the operations below so scripts, notebooks, and future workflow recipes
share the same bridge instead of re-implementing it.

The module owns orchestration only: bind, announce, serve until the reviewer
stops it, close.  HTTP endpoints, the localhost/CSRF policy, and the
review-confirmed child rerun stay in :mod:`cept.report_server`, whose rerun
continues to call the shared execution service
:func:`cept.application.execution.execute_study_to_artifacts`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cept.report_server import serve_report

DEFAULT_REPORT_HOST = "127.0.0.1"
DEFAULT_REPORT_PORT = 0


@dataclass(frozen=True)
class ServeReportRequest:
    """Typed inputs for :func:`serve_report_operation`.

    The fields mirror the flags ``cept report serve`` actually accepts.
    """

    run_dir: Path
    host: str = DEFAULT_REPORT_HOST
    port: int = DEFAULT_REPORT_PORT
    open_browser: bool = False


@dataclass(frozen=True)
class OpenReportRequest:
    """Typed inputs for :func:`open_report_operation`.

    ``cept report open`` is the "open an existing report locally" verb.  It has
    no ``--no-browser`` flag: launching the browser is the point of the verb,
    so browser launch is not part of this input contract.
    """

    run_dir: Path
    host: str = DEFAULT_REPORT_HOST
    port: int = DEFAULT_REPORT_PORT


@dataclass(frozen=True)
class ServeReportOutcome:
    """Result of a finished report review session.

    ``port`` is the port actually bound, which is the resolved ephemeral port
    whenever the request asked for port ``0``.  ``report_url`` is the exact URL
    the bridge announces.
    """

    exit_code: int
    host: str
    port: int
    report_url: str


def serve_report_operation(request: ServeReportRequest) -> ServeReportOutcome:
    """Serve ``request.run_dir`` on localhost until the reviewer stops it."""
    server = serve_report(
        request.run_dir,
        host=request.host,
        port=request.port,
        open_browser=request.open_browser,
    )
    host = request.host
    port = int(server.server_address[1])
    report_url = f"http://{host}:{port}/report.html"
    print(f"Report server: {report_url}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return ServeReportOutcome(exit_code=0, host=host, port=port, report_url=report_url)


def open_report_operation(request: OpenReportRequest) -> ServeReportOutcome:
    """Open ``request.run_dir`` in the default browser on the localhost bridge.

    The bridge itself is the same server ``cept report serve`` starts; the two
    verbs differ only in browser launch, which is why they stay two named
    operations instead of one option-driven mega-operation.
    """
    return serve_report_operation(
        ServeReportRequest(
            run_dir=request.run_dir,
            host=request.host,
            port=request.port,
            open_browser=True,
        )
    )


__all__ = [
    "DEFAULT_REPORT_HOST",
    "DEFAULT_REPORT_PORT",
    "OpenReportRequest",
    "ServeReportOutcome",
    "ServeReportRequest",
    "open_report_operation",
    "serve_report_operation",
]