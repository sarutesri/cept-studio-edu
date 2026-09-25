"""Shared one-command setup for the CEPT Public lessons.

Each lesson setup cell downloads this pinned file (URL plus SHA-256 recorded
in the notebook), verifies the hash, and execs it. Importing this module
directly is not supported: it configures the *notebook* namespace (lesson
working directory, solver CLI, short stage helpers) as a side effect.

Provided names (all lessons share these; stage cells stay a few lines each):

- ``WORKSPACE`` — notebook working directory, captured before any solver
  call. OpenDSS ``DataPath`` changes the process working directory, so stage
  cells must use ``WORKSPACE`` instead of ``Path.cwd()``.
- ``CLI`` — installed public ``cept`` launcher path (pinned wheel).
- Stage cells run literal ``!cept ...`` shell commands — the exact grammar a
  learner types in a terminal or Colab cell. Human-facing CEPT output comes
  from the CLI text renderer; Python reads persisted JSON only for optional
  comparison/assertion details.
- ``read(path)`` — read a JSON artifact.
- ``table(headers, rows)`` — print a compact aligned terminal table.
- ``terminal_panel(title, rows, next_command=None)`` — print notebook-only
  review/status information using the same plain terminal rhythm as CEPT.
- ``first_circuit_case()`` — lesson-01 demonstrator Case payload.
- ``ieee13_master()`` — bundled IEEE13 master DSS path from the installed
  public wheel.

No engineering values are decided here: lesson inputs live in the builders
above and are echoed back as result tables, never silently invented.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from importlib.resources import files
from pathlib import Path

WHEEL_URL_ENV = "CEPT_WHEEL_URL"
WHEEL_SHA_ENV = "CEPT_WHEEL_SHA256"
DEFAULT_WHEEL_URL = (
    "https://github.com/sarutesri/cept-studio-edu/releases/download/"
    "v0.2.0-edu.1/cept_power_studio-0.2.0.dev0-py3-none-any.whl"
)
DEFAULT_WHEEL_SHA256 = (
    "c7e609a1d9cc85b322bfb615f0c796c7ea5c43815b197289eb555786964478bc"
)

configured_url = os.environ.get(WHEEL_URL_ENV)
WHEEL_URL = (
    DEFAULT_WHEEL_URL
    if configured_url is None and importlib.util.find_spec("cept") is None
    else (configured_url or "")
).strip()
WHEEL_SHA256 = os.environ.get(
    WHEEL_SHA_ENV, DEFAULT_WHEEL_SHA256 if WHEEL_URL == DEFAULT_WHEEL_URL else ""
).strip().lower()
if WHEEL_URL:
    if len(WHEEL_SHA256) != 64 or any(
        character not in "0123456789abcdef" for character in WHEEL_SHA256
    ):
        raise ValueError(
            "CEPT_WHEEL_SHA256 must be the caller-provided 64-character SHA-256"
        )
    wheel_path = Path.cwd() / Path(urllib.parse.urlparse(WHEEL_URL).path).name
    print(f"Downloading caller-provided wheel: {WHEEL_URL}")
    try:
        urllib.request.urlretrieve(WHEEL_URL, wheel_path)
        digest = hashlib.sha256(wheel_path.read_bytes()).hexdigest()
        if digest != WHEEL_SHA256:
            raise ValueError(f"wheel hash mismatch: expected {WHEEL_SHA256}, got {digest}")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", str(wheel_path)],
            check=True,
        )
    except Exception as exc:
        print("What happened: the pinned wheel could not be downloaded, verified, or installed.")
        print(f"Details: {type(exc).__name__}: {exc}")
        print("Next: check the public wheel URL and SHA-256, then rerun this setup cell.")
        raise SystemExit(1) from None
else:
    print("CEPT_WHEEL_URL not supplied; using the existing installed environment.")

CLI_BIN = Path(sys.executable).parent / ("cept.exe" if os.name == "nt" else "cept")
if not CLI_BIN.is_file():
    print("What happened: the installed CEPT launcher was not found next to Python.")
    print("Next: install the pinned public wheel, then rerun this setup cell.")
    raise SystemExit(1)
CLI = str(CLI_BIN)
print("CLI: cept --version")
try:
    print(subprocess.run([CLI, "--version"], capture_output=True, text=True, check=True).stdout.strip())
except Exception as exc:
    print("What happened: the installed CEPT launcher could not report its version.")
    print(f"Details: {type(exc).__name__}: {exc}")
    print("Next: reinstall the pinned public wheel, then rerun this setup cell.")
    raise SystemExit(1) from None

# Notebook workspace root, captured before any solver call: OpenDSS DataPath
# changes the process working directory, so stage cells use WORKSPACE.
WORKSPACE = Path.cwd()


def read(path):
    """Read a JSON artifact from the run directory."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def table(headers, rows):
    """Print a terminal table without truncating identity or path values."""
    rendered_rows = [[str(value).replace("\n", " ") for value in row] for row in rows]
    rendered_headers = [str(value) for value in headers]
    all_rows = [rendered_headers, *rendered_rows]
    widths = [
        max(len(row[index]) if index < len(row) else 0 for row in all_rows)
        for index in range(len(rendered_headers))
    ]

    def line(row):
        return "  ".join(
            (row[index] if index < len(row) else "").ljust(widths[index])
            for index in range(len(rendered_headers))
        ).rstrip()

    print(line(rendered_headers))
    print("  ".join("-" * width for width in widths).rstrip())
    for row in rendered_rows:
        print(line(row))

def terminal_panel(title, rows, next_command=None):
    """Print notebook-only status using the same plain terminal rhythm as CEPT."""
    print(title)
    print("-" * max(28, min(72, len(title))))
    for label, value in rows:
        lines = str(value).splitlines() or [""]
        print(f"{str(label):<13} {lines[0]}")
        for continuation in lines[1:]:
            print(f"{'':13} {continuation}")
    if next_command:
        print()
        print("Next")
        print(f"  {next_command}")


def first_circuit_case():
    """Lesson-01 demonstrator Case payload (moved verbatim from the lesson)."""
    return {
        'meta': {'name': 'public_first_circuit', 'author': 'CEPT', 'description': 'Small three-phase source-line-load example used by the public first load-flow lesson.', 'mode': 'demonstrator'},
        'network': {
            'kind': 'inline', 'frequency_hz': 60,
            'inline': {
                'buses': [
                    {'name': 'source', 'kv': 12.47, 'phases': 3},
                    {'name': 'load', 'kv': 12.47, 'phases': 3},
                ],
                'lines': [{'name': 'line1', 'from_bus': 'source', 'to_bus': 'load', 'length_km': 1.0, 'r1_ohm_per_km': 0.2, 'x1_ohm_per_km': 0.4, 'r0_ohm_per_km': 0.6, 'x0_ohm_per_km': 1.2, 'b1_us_per_km': 0.0}],
                'loads': [{'id': 'load1', 'bus': 'load', 'phases': 3, 'kw': 100.0, 'pf': 0.95}],
                'external_grids': [{'name': 'grid', 'bus': 'source', 'pu': 1.0, 'angle_deg': 0.0, 'sk3_mva': 1000.0, 'x_r_ratio': 10.0}],
            },
        },
        'study': {'type': 'load_flow'},
    }


def ieee13_master():
    """Bundled IEEE13 master DSS path from the installed public wheel."""
    return Path(str(files("cept").joinpath("testsystems", "ieee13", "IEEE13Nodeckt.dss")))


print("Lesson helpers ready. Stage cells below run the same CEPT commands as a normal terminal.")


def _compat_symbol(kind, x, y, label, detail):
    import html as _html

    normalized = str(kind or "generator").lower()
    symbol = {
        "grid": "external-grid",
        "indmach": "motor",
        "syncgen": "generator",
        "generator": "generator",
    }.get(normalized, normalized)
    glyph = {
        "external-grid": "G",
        "motor": "M",
        "pv": "PV",
        "wind": "W",
        "hydro": "H",
        "battery": "B",
        "generator": "G",
    }.get(symbol, "G")
    if symbol == "load":
        shape = '<polygon points="-10,-6 10,-6 0,10" fill="#f8fafc" stroke="#0f172a" stroke-width="2"/>'
        text = ""
    elif symbol == "external-grid":
        shape = '<circle cx="0" cy="0" r="10" fill="#f8fafc" stroke="#0f172a" stroke-width="2"/><path d="M-7,-7 L7,7 M7,-7 L-7,7" stroke="#0f172a" stroke-width="2"/>'
        text = '<text x="0" y="25" text-anchor="middle">G</text>'
    elif symbol == "motor":
        shape = '<circle cx="0" cy="0" r="10" fill="#f8fafc" stroke="#0f172a" stroke-width="2"/>'
        text = '<text x="0" y="4" text-anchor="middle">M</text>'
    elif symbol in {"capacitor", "reactor", "statcom", "svc"}:
        shape = '<path d="M-9,-5 H9 M-9,0 H9 M-9,5 H9" stroke="#0f172a" stroke-width="2"/>'
        text = ""
    else:
        shape = '<circle cx="0" cy="0" r="10" fill="#f8fafc" stroke="#0f172a" stroke-width="2"/>'
        text = f'<text x="0" y="4" text-anchor="middle">{glyph}</text>'
    return (
        f'<g class="compat-terminal-symbol" data-symbol="{_html.escape(symbol, quote=True)}" '
        f'transform="translate({x:.1f} {y:.1f})">'
        f'<title>{_html.escape(f"{label}: {detail}")}</title>{shape}{text}</g>'
    )


def display_run_compat(run_dir):
    """Render a persisted public result with stable edges and terminal symbols."""
    import html as _html
    from IPython.display import HTML, display

    result = read(Path(run_dir) / "results.json")
    sld = result.get("sld") or result.get("sld_after") or {}
    nodes, edges = sld.get("nodes") or [], sld.get("edges") or []
    if not nodes:
        display(HTML("<p><strong>CEPT result:</strong> this run does not carry an SLD.</p>"))
        return
    xs, ys = [float(n["x"]) for n in nodes], [float(n["y"]) for n in nodes]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    dx, dy = max(x1 - x0, 1.0), max(y1 - y0, 1.0)

    def xy(node):
        return 65 + (float(node["x"]) - x0) / dx * 870, 45 + (float(node["y"]) - y0) / dy * 420

    pos = {str(n["id"]).lower(): xy(n) for n in nodes}
    line_svg = []
    for edge in edges:
        a = pos.get(str(edge.get("src", "")).lower())
        b = pos.get(str(edge.get("dst", "")).lower())
        if a is None or b is None:
            continue
        dash = ' stroke-dasharray="8 6"' if edge.get("status") == "open" else ""
        edge_label = _html.escape(str(edge.get("id", "branch")))
        line_svg.append(
            f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" y2="{b[1]:.1f}" '
            f'stroke="#7a879a" stroke-width="3"{dash}><title>{edge_label}</title></line>'
        )

    symbol_svg = []
    for node in nodes:
        x, y = pos[str(node["id"]).lower()]
        for index, generator in enumerate(node.get("gens") or []):
            symbol_svg.append(_compat_symbol(generator.get("kind", "generator"), x, y - 42 - index * 26, generator.get("name", "generator"), f"{generator.get('kw', 0.0):.1f} kW"))
        for index, load in enumerate(node.get("loads") or []):
            symbol_svg.append(_compat_symbol("load", x, y + 42 + index * 26, load.get("name", "load"), f"{load.get('kw', 0.0):.1f} kW"))
        for index, shunt in enumerate(node.get("shunts") or []):
            symbol_svg.append(_compat_symbol(shunt.get("kind", "capacitor"), x, y + 42 + (len(node.get("loads") or []) + index) * 26, shunt.get("name", "shunt"), f"{shunt.get('kvar', 0.0):.1f} kvar"))

    vmin, vmax = float(sld.get("v_min_pu", .95)), float(sld.get("v_max_pu", 1.05))
    bus_svg, rows = [], []
    for node in nodes:
        volts = {int(k): float(v) for k, v in (node.get("v_pu") or {}).items()}
        angles = {int(k): float(v) for k, v in (node.get("angle_deg") or {}).items()}
        values = list(volts.values())
        low, high = any(v < vmin for v in values), any(v > vmax for v in values)
        status = "NO DATA" if not values else "OUT" if low and high else "UNDER" if low else "OVER" if high else "OK"
        fill = {"OK": "#e8f5ec", "UNDER": "#fff3d9", "OVER": "#ffe7e1", "OUT": "#f7e7ff", "NO DATA": "#eef1f5"}[status]
        stroke = {"OK": "#2f7d4a", "UNDER": "#a46700", "OVER": "#b8432e", "OUT": "#8147a6", "NO DATA": "#7b8796"}[status]
        x, y = pos[str(node["id"]).lower()]

        def phase(p):
            return "—" if p not in volts else f"{volts[p]:.4f} pu" + (f" @ {angles[p]:.2f}°" if p in angles else "")

        tip = _html.escape("Bus " + str(node["id"]) + "\nStatus: " + status + "\n" + "\n".join(f"{label}: {phase(p)}" for p, label in [(1, "A"), (2, "B"), (3, "C")] if p in volts))
        label = _html.escape(str(node["id"]))
        bus_svg.append(f'<g tabindex="0"><title>{tip}</title><rect x="{x-31:.1f}" y="{y-11:.1f}" width="62" height="22" rx="5" fill="{fill}" stroke="{stroke}" stroke-width="2"/><text x="{x:.1f}" y="{y+4:.1f}" text-anchor="middle" font-size="12" font-weight="700">{label}</text></g>')
        rows.append("<tr><th>" + label + "</th><td>" + phase(1) + "</td><td>" + phase(2) + "</td><td>" + phase(3) + "</td><td><strong>" + status + "</strong></td></tr>")
    display(HTML('<div style="font-family:system-ui,sans-serif"><h3>Interactive CEPT SLD</h3><p style="color:#657187">Hover or focus a bus to inspect solver-returned values.</p><div style="overflow:hidden;border:1px solid #d9dee8;border-radius:10px"><svg viewBox="0 0 1000 510" style="width:100%;height:auto;display:block">' + "".join(line_svg) + "".join(symbol_svg) + "".join(bus_svg) + '</svg></div><div style="overflow-x:auto;margin-top:10px"><table style="border-collapse:collapse;width:100%;min-width:650px"><thead><tr><th>Bus</th><th>Phase A</th><th>Phase B</th><th>Phase C</th><th>Status</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div></div>"))
