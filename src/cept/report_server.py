"""Small localhost report bridge for review-confirmed child runs."""

from __future__ import annotations

import json
import hashlib
import secrets
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse
from typing import Any
from cept.application.run_identity import model_revision_id, patch_digest

from cept.artifacts import portable_run_dir
from cept.schema import Case
from cept.util import read_json

IDLE_TIMEOUT_SECONDS = 1800.0

# Honest provenance label for a review-confirmed web rerun.  The child run is
# dispatched by this in-process HTTP bridge, NOT by a CLI process, so there is
# no `cept <noun> <verb>` argv to record.  `cept report rerun` is not a
# registered public route, and the noun+verb grammar is frozen, so inventing
# one here would either overstate the child run's provenance or widen the
# public CLI.  The child run's ``command_log.txt`` therefore records this
# clearly non-CLI ``component:verb`` label; it must never be mistaken for a
# runnable ``cept`` command line.
REPORT_SERVER_RERUN_COMMAND = "report_server:rerun"


def apply_case_patch(case: Case, patch: dict[str, Any], *, require_fingerprint: bool = False) -> Case:
    """Apply the intentionally small, schema-backed HTML edit surface."""
    if patch.get("topology") is not None:
        raise ValueError("solver topology edits are deferred; edit layout and allow-listed parameters only")
    patch_fingerprint = patch.get("case_fingerprint")
    if require_fingerprint and not patch_fingerprint:
        raise ValueError("patch must include the immutable parent case_fingerprint")
    if patch_fingerprint and patch_fingerprint != case.fingerprint():
        raise ValueError("patch Case fingerprint does not match the immutable parent run")
    payload = case.model_dump(mode="json")
    if patch.get("study") is not None:
        study = patch["study"]
        if not isinstance(study, dict) or not isinstance(study.get("type"), str):
            raise ValueError("study patch requires a typed study object with type")
        payload["study"] = study
        # New Cases declare runnable studies under ``studies[]`` while the
        # top-level field remains a compatibility view.  Updating only the
        # latter creates a child whose receipt is valid but whose runner still
        # executes the old study type.  A single-study Case is unambiguous;
        # multi-study edits must name their target in a future patch contract.
        declared = payload.get("studies")
        if isinstance(declared, list):
            if len(declared) == 1:
                declared[0]["study"] = study
            elif len(declared) > 1:
                raise ValueError("study patch for a multi-study Case requires an explicit study id")
        legacy = payload.get("experiments")
        if isinstance(legacy, list):
            if len(legacy) == 1:
                legacy[0]["study"] = study
            elif len(legacy) > 1 and not isinstance(declared, list):
                raise ValueError("study patch for a multi-experiment Case requires an explicit experiment id")
    # Dynamic result mappings are an explicit, typed part of the Case.  Keep
    # them under the same fingerprint-bound derive path as study edits so a
    # source result contract can be converted into a provenance-preserving
    # candidate Case without hand-editing JSON.
    if patch.get("dynamics") is not None:
        dynamics = patch["dynamics"]
        if not isinstance(dynamics, dict):
            raise ValueError("dynamics patch requires a typed dynamics object")
        payload["dynamics"] = dynamics

    changes = list(patch.get("changes", []))
    network_changes: list[dict[str, Any]] = []
    for change in changes:
        path = str(change.get("path", ""))
        if path in {"standards.v_min_pu", "standards.v_max_pu"}:
            value = change.get("value")
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"numeric value required for {path}")
            if float(value) <= 0:
                raise ValueError(f"positive per-unit value required for {path}")
            payload.setdefault("standards", {})[path.rsplit(".", 1)[-1]] = float(value)
            continue
        network_changes.append(change)

    standards = payload.get("standards", {})
    v_min = float(standards.get("v_min_pu", 0.95))
    v_max = float(standards.get("v_max_pu", 1.05))
    if v_min >= v_max:
        raise ValueError("standards.v_min_pu must be less than standards.v_max_pu")

    network_spec = payload.get("network", {})
    if network_spec.get("kind") != "inline" or not isinstance(network_spec.get("inline"), dict):
        # A report layout is view-only metadata.  It is retained in the child
        # run by ``_preserve_layout_patch`` and does not require an inline
        # network.  Only actual network edits need the inline Case model.
        if network_changes:
            raise ValueError("network HTML edits require an inline Case")
        return Case.model_validate(payload)
    network = network_spec["inline"]
    for change in network_changes:
        path = str(change.get("path", ""))
        parts = path.split(".")
        if (
            len(parts) != 4
            or parts[0] != "network"
            or parts[1] not in {"loads", "generators", "transformers"}
        ):
            raise ValueError(f"unsupported patch path: {path}")
        collection, identity, field = parts[1:]
        allowed = {
            "loads": {"kw", "pf"},
            "generators": {"kw", "pu", "pf"},
            "transformers": {"uk_pct", "x_r_ratio"},
        }
        if field not in allowed[collection]:
            raise ValueError(f"parameter is not editable in v1: {path}")
        rows = network.get(collection, [])
        key = "id" if collection == "loads" else "name"
        row = next((item for item in rows if item.get(key) == identity), None)
        if row is None:
            raise ValueError(f"unknown {collection[:-1]} '{identity}'")
        value = change.get("value")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"numeric value required for {path}")
        row[field] = value

    layout = patch.get("layout")
    if layout is not None:
        nodes = layout.get("nodes", {})
        buses = {bus["name"] for bus in network.get("buses", [])}
        if set(nodes) - buses:
            raise ValueError("layout contains an unknown bus")
        current = dict(network.get("sld_layout") or {})
        for name, point in nodes.items():
            if not isinstance(point, dict) or not all(k in point for k in ("x", "y")):
                raise ValueError(f"invalid layout point for {name}")
            current[name] = (float(point["x"]), float(point["y"]))
        network["sld_layout"] = current

    return Case.model_validate(payload)


def _json(handler: SimpleHTTPRequestHandler, status: int, payload: Any) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def serve_report(
    run_dir: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    open_browser: bool = False,
    idle_timeout: float = IDLE_TIMEOUT_SECONDS,
) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("report server must bind to localhost only")
    root = portable_run_dir(run_dir)
    if not (root / "report.html").is_file() or not (root / "case.json").is_file():
        raise FileNotFoundError("report server requires report.html and case.json")

    csrf_token = secrets.token_urlsafe(32)

    class ReportServer(ThreadingHTTPServer):
        allow_reuse_address = True

        def __init__(self, address, handler):
            super().__init__(address, handler)
            self._cept_last_activity = time.monotonic()
            self._cept_started = False
            self._shutdown_request = False

        def touch(self):
            self._cept_last_activity = time.monotonic()

        def serve_forever(self, *args, **kwargs):
            self._cept_started = True
            return super().serve_forever(*args, **kwargs)

    def _watch_idle(server: ReportServer) -> None:
        while not server._cept_started and not server._shutdown_request:
            time.sleep(0.01)
        while not server._shutdown_request and idle_timeout > 0:
            time.sleep(min(1.0, max(0.1, idle_timeout / 10)))
            if time.monotonic() - server._cept_last_activity >= idle_timeout:
                server.shutdown()
                return

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root), **kwargs)

        def log_message(self, *_args):
            return

        def _local_host(self) -> bool:
            host_header = self.headers.get("Host", "")
            try:
                hostname = urlparse("//" + host_header).hostname
            except ValueError:
                return False
            return hostname in {"127.0.0.1", "localhost"}

        def _guard_request(self) -> bool:
            if not self._local_host():
                _json(self, 403, {"ok": False, "error": "localhost Host header required"})
                return False
            path = unquote(urlparse(self.path).path)
            if any(part == ".." for part in path.replace("\\", "/").split("/")):
                _json(self, 403, {"ok": False, "error": "path traversal rejected"})
                return False
            self.server.touch()
            return True

        def do_GET(self):  # noqa: N802
            if not self._guard_request():
                return
            path = urlparse(self.path).path
            if path == "/api/health":
                _json(self, 200, {"ok": True, "run_dir": str(root)})
                return
            if path == "/report.html":
                # Inject a per-server CSRF token at request time.  The report
                # remains self-contained on disk; only localhost API calls
                # need this ephemeral bridge context.
                #
                # The stored file is served as BYTES, never as decoded text.
                # ``read_text()`` applies universal-newline translation, which
                # silently rewrote the report's own CRLF line endings to LF and
                # handed the browser a document that was not the stored one.
                # The injected bridge context is the only intended difference
                # between the stored bytes and the served bytes.
                stored = (root / "report.html").read_bytes()
                context = (
                    "<script>window.__ceptReportContext=Object.assign({},"
                    "window.__ceptReportContext||{},"
                    f"{json.dumps({'run_dir': str(root), 'csrf_token': csrf_token}, ensure_ascii=False)});</script>"
                ).encode("utf-8")
                data = stored.replace(b"</body>", context + b"</body>", 1)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            super().do_GET()

        def do_POST(self):  # noqa: N802
            if not self._guard_request():
                return
            path = urlparse(self.path).path
            try:
                if self.headers.get("X-CEPT-CSRF") != csrf_token:
                    _json(self, 403, {"ok": False, "error": "missing or invalid CEPT CSRF token"})
                    return
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                case = Case.model_validate(read_json(root / "case.json"))
                candidate = apply_case_patch(case, payload, require_fingerprint=True)
                if path == "/api/patch":
                    (root / "draft-patch.json").write_text(
                        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
                    )
                    _json(self, 200, {"ok": True, "case_fingerprint": candidate.fingerprint()})
                    return
                if path == "/api/review":
                    diff = []
                    for change in payload.get("changes", []):
                        target = str(change.get("path", ""))
                        old = _lookup_case_path(case, target)
                        new = _lookup_case_path(candidate, target)
                        diff.append({"path": target, "old": old, "new": new})
                    _json(
                        self,
                        200,
                        {
                            "ok": True,
                            "case_fingerprint": candidate.fingerprint(),
                            "changes": payload.get("changes", []),
                            "diff": diff,
                            "layout_nodes": len(payload.get("layout", {}).get("nodes", {})),
                            "confirmation_required": True,
                        },
                    )
                    return
                if path == "/api/run":
                    if payload.get("confirm") is not True:
                        _json(self, 400, {"ok": False, "error": "explicit confirmation required"})
                        return
                    from cept.application.execution import StudyExecutionRequest, execute_study_to_artifacts

                    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                    child = portable_run_dir(root.parent / f"{root.name}-rerun-{stamp}")
                    parent_fingerprint = case.fingerprint()
                    child_fingerprint = candidate.fingerprint()
                    edit_digest = patch_digest(payload)
                    model_revision = {
                        "schema": "cept-model-revision-v1",
                        "revision_id": model_revision_id(parent_fingerprint, child_fingerprint, edit_digest),
                        "parent_case_fingerprint": parent_fingerprint,
                        "case_fingerprint": child_fingerprint,
                        "edit_patch_sha256": edit_digest,
                        "editable_copy": "report-review-patch-v1",
                        "reimport_status": "typed-case-child; native re-import not claimed",
                        "parent_run_dir": str(root),
                        "review_confirmed": True,
                    }
                    outcome = execute_study_to_artifacts(
                        StudyExecutionRequest(
                            case=candidate,
                            run_dir=child,
                            export=True,
                            include_show_commands=False,
                            force=False,
                            strict=False,
                            # Records the bridge that actually dispatched this
                            # child run, not an invented CLI invocation.
                            argv=[REPORT_SERVER_RERUN_COMMAND, str(root)],
                            experiment_context={"model_revision": model_revision},
                            console_output=False,
                        )
                    )
                    rc = outcome.exit_code
                    _preserve_layout_patch(child, payload.get("layout"))
                    _json(self, 200, {"ok": rc == 0, "run_dir": str(child), "exit_code": rc})
                    return
                _json(self, 404, {"ok": False, "error": "unknown endpoint"})
            except Exception as exc:
                _json(self, 400, {"ok": False, "error": str(exc)})

    server = ReportServer((host, port), Handler)
    threading.Thread(target=_watch_idle, args=(server,), daemon=True).start()
    if open_browser:
        threading.Timer(
            0.2, lambda: webbrowser.open(f"http://{host}:{server.server_address[1]}/report.html")
        ).start()
    return server


def _lookup_case_path(case: Case, path: str) -> Any:
    """Read only the same allow-listed scalar paths accepted by the patcher."""
    payload = case.model_dump(mode="json")
    if path in {"standards.v_min_pu", "standards.v_max_pu"}:
        return payload.get("standards", {}).get(path.rsplit(".", 1)[-1])
    parts = path.split(".")
    if len(parts) != 4 or parts[0] != "network":
        return None
    collection, identity, field = parts[1:]
    network = payload.get("network", {}).get("inline", {})
    rows = network.get(collection, [])
    key = "id" if collection == "loads" else "name"
    row = next((item for item in rows if item.get(key) == identity), None)
    return row.get(field) if row is not None else None


def _preserve_layout_patch(run_dir: Path, layout: Any) -> None:
    """Keep view-only rotation/orientation metadata with a child run.

    Layout edits never alter solver topology.  They are nevertheless retained
    in the child artifact so the report can be reopened and the exact edit
    reviewed alongside the immutable parent run.
    """
    if not isinstance(layout, dict):
        return
    path = run_dir / "sld-layout.json"
    if not path.exists():
        return
    stored = read_json(path)
    nodes = layout.get("nodes", {})
    if not isinstance(nodes, dict):
        return
    for name, edit in nodes.items():
        if name not in stored.get("nodes", {}) or not isinstance(edit, dict):
            continue
        target = stored["nodes"][name]
        for key in ("x", "y", "rotation", "bus_orientation"):
            if key in edit:
                target[key] = edit[key]
    stored["parent_layout_patch_hash"] = hashlib.sha256(
        json.dumps(layout, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    path.write_text(json.dumps(stored, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


__all__ = ["REPORT_SERVER_RERUN_COMMAND", "apply_case_patch", "serve_report"]


