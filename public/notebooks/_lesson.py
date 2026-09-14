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
  learner types in a terminal or Colab cell. Python only parses the returned
  JSON (`read`) and renders result tables/cards.
- ``read(path)`` — read a JSON artifact.
- ``table(headers, rows)`` — print a Markdown pipe table (detail rows).
- ``cards(items, title)`` — display headline result cards (HTML).
- ``first_circuit_case()`` — lesson-01 demonstrator Case payload.
- ``ieee13_master()`` — bundled IEEE13 master DSS path from the installed
  public wheel.

No engineering values are decided here: lesson inputs live in the builders
above and are echoed back as result tables, never silently invented.
"""

from __future__ import annotations

import hashlib
import html
import importlib.util
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from importlib.resources import files
from pathlib import Path

from IPython.display import HTML, display

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
    urllib.request.urlretrieve(WHEEL_URL, wheel_path)
    digest = hashlib.sha256(wheel_path.read_bytes()).hexdigest()
    if digest != WHEEL_SHA256:
        raise ValueError(f"wheel hash mismatch: expected {WHEEL_SHA256}, got {digest}")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", str(wheel_path)],
        check=True,
    )
else:
    print("CEPT_WHEEL_URL not supplied; using the existing installed environment.")

CLI_BIN = Path(sys.executable).parent / ("cept.exe" if os.name == "nt" else "cept")
if not CLI_BIN.is_file():
    raise RuntimeError(
        "cept launcher not found next to Python; reinstall the pinned public wheel"
    )
CLI = str(CLI_BIN)
print("CLI: cept --version")
print(
    subprocess.run([CLI, "--version"], capture_output=True, text=True, check=True).stdout.strip()
)

# Notebook workspace root, captured before any solver call: OpenDSS DataPath
# changes the process working directory, so stage cells use WORKSPACE.
WORKSPACE = Path.cwd()


def read(path):
    """Read a JSON artifact from the run directory."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def table(headers, rows):
    """Print a Markdown pipe table (detail rows; headlines use cards)."""
    print("| " + " | ".join(headers) + " |")
    print("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        print("| " + " | ".join(str(value) for value in row) + " |")


def cards(items, title="CEPT Studio"):
    """Display headline result cards (HTML)."""
    blocks = []
    for label, value, note in items:
        blocks.append(
            f"""<div style="flex:1;min-width:180px;border:1px solid #d9dee8;border-radius:14px;padding:14px 16px;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.05)"><div style="font-size:12px;color:#667085;text-transform:uppercase;letter-spacing:.04em">{html.escape(str(label))}</div><div style="font-size:22px;font-weight:700;margin:4px 0;color:#182230">{html.escape(str(value))}</div><div style="font-size:12px;color:#667085">{html.escape(str(note))}</div></div>"""
        )
    display(
        HTML(
            f"""<div style="font-family:Inter,Arial,sans-serif;margin:10px 0 18px"><div style="font-size:18px;font-weight:700;margin-bottom:9px">{html.escape(title)}</div><div style="display:flex;gap:10px;flex-wrap:wrap">{"".join(blocks)}</div></div>"""
        )
    )


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


print("lesson helpers ready: cli/read/table/cards + WORKSPACE.")
