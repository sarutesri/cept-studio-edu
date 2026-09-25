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

def ieee4_node_case():
    """IEEE 4-Node Radial Distribution Test Feeder with step-down transformer and balanced load."""
    return {
        'meta': {
            'name': 'ieee4_node_feeder',
            'author': 'IEEE PES / CEPT',
            'description': 'IEEE 4-Node Radial Distribution Test Feeder with step-down transformer and balanced load.',
            'mode': 'demonstrator',
        },
        'network': {
            'kind': 'inline',
            'frequency_hz': 60,
            'inline': {
                'buses': [
                    {'name': 'node1', 'kv': 12.47, 'phases': 3},
                    {'name': 'node2', 'kv': 12.47, 'phases': 3},
                    {'name': 'node3', 'kv': 4.16, 'phases': 3},
                    {'name': 'node4', 'kv': 4.16, 'phases': 3},
                ],
                'external_grids': [
                    {'name': 'utility', 'bus': 'node1', 'pu': 1.0, 'angle_deg': 0.0, 'sk3_mva': 1000.0, 'x_r_ratio': 10.0}
                ],
                'transformers': [
                    {'name': 't1', 'hv_bus': 'node2', 'lv_bus': 'node3', 'hv_kv': 12.47, 'lv_kv': 4.16, 'mva': 6.0, 'uk_pct': 6.08, 'x_r_ratio': 6.0, 'vector_group': 'Dyn1'}
                ],
                'lines': [
                    {'name': 'line12', 'from_bus': 'node1', 'to_bus': 'node2', 'length_km': 0.6096, 'r1_ohm_per_km': 0.249, 'x1_ohm_per_km': 0.373, 'r0_ohm_per_km': 0.536, 'x0_ohm_per_km': 1.118, 'b1_us_per_km': 0.0},
                    {'name': 'line34', 'from_bus': 'node3', 'to_bus': 'node4', 'length_km': 0.7620, 'r1_ohm_per_km': 0.249, 'x1_ohm_per_km': 0.373, 'r0_ohm_per_km': 0.536, 'x0_ohm_per_km': 1.118, 'b1_us_per_km': 0.0},
                ],
                'loads': [
                    {'id': 'load4', 'bus': 'node4', 'phases': 3, 'kw': 1800.0, 'pf': 0.9, 'model': 'constant_power'}
                ],
            },
        },
        'study': {'type': 'load_flow'},
    }



def ieee13_master():
    """Bundled IEEE13 master DSS path from the installed public wheel."""
    return Path(str(files("cept").joinpath("testsystems", "ieee13", "IEEE13Nodeckt.dss")))


print("Lesson helpers ready. Stage cells below run the same CEPT commands as a normal terminal.")


def _compat_symbol(kind, x, y, label, detail, bus_y=None):
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
    lead = ""
    if bus_y is not None:
        lead = f'<line x1="0" y1="0" x2="0" y2="{bus_y - y:.1f}" stroke="#0f172a" stroke-width="2"/>'
    return (
        f'<g class="compat-terminal-symbol" data-symbol="{_html.escape(symbol, quote=True)}" '
        f'transform="translate({x:.1f} {y:.1f})">'
        f'<title>{_html.escape(f"{label}: {detail}")}</title>{lead}{shape}{text}</g>'
    )


def display_run_compat(run_dir):
    """Render a persisted public result with expressive engineering SLD graphics."""
    import html as _html
    from IPython.display import HTML, display

    result = read(Path(run_dir) / "results.json")
    sld = result.get("sld") or result.get("sld_after") or {}
    nodes, edges = sld.get("nodes") or [], sld.get("edges") or []
    if not nodes:
        display(HTML("<p><strong>CEPT result:</strong> this run does not carry an SLD.</p>"))
        return

    # Filter virtual GridLink sources so they become grid infeed terminals instead of fake bus pills
    grid_links = [e for e in edges if str(e.get("id", "")).casefold().startswith("gridlink.")]
    virtual_ids = set()
    for gl in grid_links:
        s, d = str(gl.get("src", "")).lower(), str(gl.get("dst", "")).lower()
        node_kinds = {str(n.get("id", "")).lower(): n.get("kind") for n in nodes}
        if node_kinds.get(s) == "substation":
            virtual_ids.add(s)
        elif node_kinds.get(d) == "substation":
            virtual_ids.add(d)
        else:
            virtual_ids.add(s)

    physical_nodes = [n for n in nodes if str(n.get("id", "")).lower() not in virtual_ids]
    if not physical_nodes:
        physical_nodes = nodes
        virtual_ids.clear()

    physical_edges = [
        e for e in edges
        if not str(e.get("id", "")).casefold().startswith("gridlink.")
        and str(e.get("src", "")).lower() not in virtual_ids
        and str(e.get("dst", "")).lower() not in virtual_ids
    ]

    xs, ys = [float(n["x"]) for n in physical_nodes], [float(n["y"]) for n in physical_nodes]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    dx, dy = max(x1 - x0, 1.0), max(y1 - y0, 1.0)

    left_margin = 150 if grid_links else 90
    usable_w = 1000 - left_margin - 110
    mid_y = 230.0 if dy <= 1e-9 else 60.0

    def xy(node):
        norm_x = (float(node["x"]) - x0) / dx if dx > 1e-9 else 0.5
        norm_y = (float(node["y"]) - y0) / dy if dy > 1e-9 else 0.0
        return left_margin + norm_x * usable_w, mid_y + norm_y * 360

    pos = {str(n["id"]).lower(): xy(n) for n in physical_nodes}

    svg_parts = []
    # Grid infeed symbols
    for gl in grid_links:
        s, d = str(gl.get("src", "")).lower(), str(gl.get("dst", "")).lower()
        target = d if d in pos else s if s in pos else None
        if target:
            bx, by = pos[target]
            gx, gy = bx - 72, by
            g_name = _html.escape(str(gl.get("id", "grid")).replace("GridLink.", "").upper())
            svg_parts.append(f'<line x1="{gx+16:.1f}" y1="{gy:.1f}" x2="{bx-33:.1f}" y2="{gy:.1f}" stroke="#16a34a" stroke-width="2.5"/>')
            svg_parts.append(f'<polygon points="{bx-33:.1f},{gy:.1f} {bx-41:.1f},{gy-5:.1f} {bx-41:.1f},{gy+5:.1f}" fill="#16a34a"/>')
            svg_parts.append(f'<circle cx="{gx:.1f}" cy="{gy:.1f}" r="17" fill="#f0fdf4" stroke="#16a34a" stroke-width="2.5"/>')
            svg_parts.append(f'<path d="M {gx-7:.1f} {gy:.1f} Q {gx-3.5:.1f} {gy-6:.1f} {gx:.1f} {gy:.1f} T {gx+7:.1f} {gy:.1f}" fill="none" stroke="#16a34a" stroke-width="2"/>')
            svg_parts.append(f'<text x="{gx:.1f}" y="{gy-23:.1f}" text-anchor="middle" font-size="11" font-weight="700" fill="#16a34a">{g_name}</text>')
            svg_parts.append(f'<text x="{gx:.1f}" y="{gy+29:.1f}" text-anchor="middle" font-size="9.5" fill="#15803d">1.0 pu · Slack</text>')

    # Branches
    for edge in physical_edges:
        a = pos.get(str(edge.get("src", "")).lower())
        b = pos.get(str(edge.get("dst", "")).lower())
        if a is None or b is None:
            continue
        edge_id = str(edge.get("id", "branch"))
        kind = str(edge.get("kind", "")).lower()
        is_tx = kind == "transformer" or edge_id.startswith("Transformer.")
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        dash = ' stroke-dasharray="8 6"' if edge.get("status") == "open" else ""

        if is_tx:
            r = 18
            svg_parts.append(f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{mx-r+3:.1f}" y2="{my:.1f}" stroke="#334155" stroke-width="2.5"{dash}/>')
            svg_parts.append(f'<line x1="{mx+r-3:.1f}" y1="{my:.1f}" x2="{b[0]:.1f}" y2="{b[1]:.1f}" stroke="#334155" stroke-width="2.5"{dash}/>')
            svg_parts.append(f'<circle cx="{mx-8:.1f}" cy="{my:.1f}" r="{r}" fill="#ffffff" stroke="#b45309" stroke-width="2.5"/>')
            svg_parts.append(f'<circle cx="{mx+8:.1f}" cy="{my:.1f}" r="{r}" fill="none" stroke="#b45309" stroke-width="2.5"/>')
            tx_label = _html.escape(edge_id.replace("Transformer.", ""))
            svg_parts.append(f'<text x="{mx:.1f}" y="{my-24:.1f}" text-anchor="middle" font-size="11" font-weight="700" fill="#b45309">{tx_label}</text>')
        else:
            svg_parts.append(f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" y2="{b[1]:.1f}" stroke="#64748b" stroke-width="2.5"{dash}><title>{_html.escape(edge_id)}</title></line>')
            name = _html.escape(edge_id.replace("Line.", ""))
            svg_parts.append(f'<text x="{mx:.1f}" y="{my-11:.1f}" text-anchor="middle" font-size="11" font-weight="600" fill="#475569">{name}</text>')
            p_kw = edge.get("p_kw")
            if p_kw:
                svg_parts.append(f'<text x="{mx:.1f}" y="{my+17:.1f}" text-anchor="middle" font-size="10" fill="#0284c7">{p_kw:.1f} kW</text>')

    # Buses & terminals
    vmin, vmax = float(sld.get("v_min_pu", .95)), float(sld.get("v_max_pu", 1.05))
    rows = []
    for node in physical_nodes:
        volts = {int(k): float(v) for k, v in (node.get("v_pu") or {}).items()}
        angles = {int(k): float(v) for k, v in (node.get("angle_deg") or {}).items()}
        values = list(volts.values())
        v_mean = sum(values) / len(values) if values else 1.0
        low, high = any(v < vmin for v in values), any(v > vmax for v in values)
        status = "NO DATA" if not values else "OUT" if low and high else "UNDER" if low else "OVER" if high else "OK"
        fill = {"OK": "#e8f5ec", "UNDER": "#fff3d9", "OVER": "#ffe7e1", "OUT": "#f7e7ff", "NO DATA": "#eef1f5"}[status]
        stroke = {"OK": "#2f7d4a", "UNDER": "#a46700", "OVER": "#b8432e", "OUT": "#8147a6", "NO DATA": "#7b8796"}[status]
        x, y = pos[str(node["id"]).lower()]

        def phase(p):
            return "—" if p not in volts else f"{volts[p]:.4f} pu" + (f" @ {angles[p]:.2f}°" if p in angles else "")

        tip = _html.escape("Bus " + str(node["id"]) + "
Status: " + status + "
" + "
".join(f"{label}: {phase(p)}" for p, label in [(1, "A"), (2, "B"), (3, "C")] if p in volts))
        label = _html.escape(str(node["id"]).upper())
        bw, bh = 66, 24
        svg_parts.append(f'<g tabindex="0"><title>{tip}</title><rect x="{x-bw/2:.1f}" y="{y-bh/2:.1f}" width="{bw}" height="{bh}" rx="5" fill="{fill}" stroke="{stroke}" stroke-width="2.2"/><text x="{x:.1f}" y="{y+4:.1f}" text-anchor="middle" font-size="12" font-weight="750" fill="#0f172a">{label}</text></g>')
        svg_parts.append(f'<text x="{x:.1f}" y="{y+26:.1f}" text-anchor="middle" font-size="10" font-weight="700" fill="{stroke}">{v_mean:.4f} pu</text>')

        # Loads & generators
        for index, ld in enumerate(node.get("loads") or []):
            lx, ly = x, y + 40 + index * 32
            svg_parts.append(f'<line x1="{x:.1f}" y1="{y+bh/2:.1f}" x2="{lx:.1f}" y2="{ly:.1f}" stroke="#0f172a" stroke-width="2"/>')
            svg_parts.append(f'<polygon points="{lx-8:.1f},{ly:.1f} {lx+8:.1f},{ly:.1f} {lx:.1f},{ly+13:.1f}" fill="#dc2626" stroke="#991b1b" stroke-width="1.5"/>')
            svg_parts.append(f'<text x="{lx:.1f}" y="{ly+25:.1f}" text-anchor="middle" font-size="10.5" font-weight="700" fill="#991b1b">{ld.get("kw", 0.0):.0f} kW</text>')
        for index, gen in enumerate(node.get("gens") or []):
            gx, gy = x, y - 40 - index * 32
            svg_parts.append(_compat_symbol(gen.get("kind", "generator"), gx, gy, gen.get("name", "generator"), f"{gen.get('kw', 0.0):.1f} kW", bus_y=y))
        for index, sh in enumerate(node.get("shunts") or []):
            sx, sy = x, y + 40 + (len(node.get("loads") or []) + index) * 32
            svg_parts.append(_compat_symbol(sh.get("kind", "capacitor"), sx, sy, sh.get("name", "shunt"), f"{sh.get('kvar', 0.0):.1f} kvar", bus_y=y))

        rows.append("<tr><th>" + label + "</th><td>" + phase(1) + "</td><td>" + phase(2) + "</td><td>" + phase(3) + "</td><td><strong>" + status + "</strong></td></tr>")

    view_h = 510 if dy > 1e-9 else 380
    display(HTML('<div style="font-family:system-ui,sans-serif"><h3>Interactive CEPT SLD</h3><p style="color:#657187">Hover or focus a bus to inspect solver-returned values.</p><div style="overflow:hidden;border:1px solid #d9dee8;border-radius:10px;background:#f8fafc"><svg viewBox="0 0 1000 ' + str(int(view_h)) + '" style="width:100%;height:auto;display:block">' + "".join(svg_parts) + '</svg></div><div style="overflow-x:auto;margin-top:10px"><table style="border-collapse:collapse;width:100%;min-width:650px"><thead><tr><th>Bus</th><th>Phase A</th><th>Phase B</th><th>Phase C</th><th>Status</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div></div>"))
