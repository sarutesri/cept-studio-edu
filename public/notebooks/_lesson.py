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
