#!/usr/bin/env python3
"""Build the public education site from an exported staging directory.

The builder deliberately has no repository or git dependency.  The staging
directory must contain the public notebook files, ``public/site/education.css``
and the export manifest written by ``public_export.py``.  Notebook output is
rendered only when it is present in the exported JSON; an unexecuted code cell
gets an explicit notice instead of a guessed result.

Design contract (see ``docs/cept/DEVELOPMENT_STATE.md``):

* one short story on the homepage, with detail behind hover/focus/tap;
* every lesson is the same compact shape: question, steps, takeaway;
* plain words first; a precise term gets a short tooltip, not a paragraph;
* every lesson states what its result does *not* show.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import quote


PUBLIC_REPOSITORY = "sarutesri/cept-studio-edu"
PUBLIC_BRANCH = "main"
MANIFEST_NAME = "PUBLIC-EXPORT-MANIFEST.json"
STYLESHEET = Path("public/site/education.css")


@dataclass(frozen=True)
class Lesson:
    """One lesson: the single source of truth for site copy about it."""

    stem: str
    number: str
    track: str
    title: str
    question: str
    sees: tuple[str, ...]
    takeaway: str
    limit: str
    key_cell: str


TRACKS: tuple[tuple[str, str, str], ...] = (
    ("start", "Start", "Check your setup, then see why the voltage base matters."),
    ("build", "Build", "Describe a network, then meet unbalance and missing data."),
    ("apply", "Apply", "Ask two planning questions: how much solar, and what fault current."),
    ("trust", "Trust", "Trace a result back to the inputs that produced it."),
)

LESSONS: tuple[Lesson, ...] = (
    Lesson(
        "00_environment", "00", "start", "Check your setup",
        "Is the OpenDSS runtime ready here?",
        ("Runtime check", "Course scope", "Claim level"),
        "A passing check means the tools run here. It says nothing about a real network.",
        "Not a test of any real network or project.",
        "environment-headline",
    ),
    Lesson(
        "01_why_solvers_lie", "01", "start", "Same feeder, two voltage bases",
        "Can a solve converge and still mislead?",
        ("Two runs", "One feeder", "Node 4 voltage"),
        "Declaring the voltage base moved the Node 4 readout from about 0.32 to about 0.95 pu.",
        "A teaching example, not a field measurement.",
        "comparison-table",
    ),
    Lesson(
        "02_first_circuit_sld", "02", "build", "Build your first network",
        "How do I describe a small feeder as data?",
        ("Case file", "Automatic diagram", "Voltage plot"),
        "One structured input drives the run, the diagram and the saved results.",
        "One example feeder; not a real project.",
        "first-circuit-visual",
    ),
    Lesson(
        "03_unbalanced_feeder", "03", "build", "Unbalanced phases",
        "Do all three phases see the same voltage?",
        ("Phase A, B and C", "Voltage spread", "Bus 671"),
        "Phases can differ. A single average would hide that.",
        "One standard test feeder; not a statement about yours.",
        "ieee13-result",
    ),
    Lesson(
        "04_incomplete_data", "04", "build", "When data is missing",
        "What happens when required inputs are missing?",
        ("Known and missing", "Three input policies", "A blocked run"),
        "Missing inputs stay visible. A default needs explicit approval and is never treated as measured.",
        "The demo run is separate from your own intake.",
        "resolution-validate",
    ),
    Lesson(
        "05_solar_hosting_capacity", "05", "apply", "Solar hosting capacity",
        "How much solar can this feeder take under one limit?",
        ("A PV sweep", "One voltage limit", "A capacity range"),
        "The answer is a range under one stated criterion, not a universal limit.",
        "Not an interconnection study.",
        "hosting-result",
    ),
    Lesson(
        "06_fault_study", "06", "apply", "Short-circuit current",
        "What current flows for one declared fault?",
        ("Fault location", "Current by phase", "A comparison plot"),
        "A fault current for the declared case. It is not a protection setting.",
        "Not a protection or coordination decision.",
        "fault-result",
    ),
    Lesson(
        "07_digital_evidence", "07", "trust", "Trace a result",
        "Can I show which inputs produced a result?",
        ("Two identical runs", "IDs and hashes", "An overlay plot"),
        "IDs and hashes show a run is unchanged. They do not show it is physically right.",
        "Hashes detect changes; they do not authenticate a solver.",
        "receipt-table",
    ),
)

GLOSSARY: tuple[tuple[str, str], ...] = (
    ("typed Case", "A structured input file: network, units and study settings."),
    ("OpenDSS", "A free, open-source power-system simulator. It does the solving."),
    ("voltage base", "The nominal voltage that per-unit values are measured against."),
    ("per-unit", "Voltage as a fraction of its nominal value."),
    ("pu", "Per-unit: voltage as a fraction of its nominal value."),
    ("SLD", "Single-line diagram: a one-line schematic of the network."),
    ("load flow", "A steady-state calculation of voltages and power flows."),
    ("hosting capacity", "How much solar a feeder can take before a limit is reached."),
    ("fingerprint", "A short ID computed from the exact input content."),
    ("SHA-256", "A checksum that changes whenever the file changes."),
    ("converged", "The solver's iterations settled on a solution."),
    ("unbalance", "Phases carrying unequal voltage or load."),
)

_SKIP_TAGS = frozenset({"a", "button", "code", "pre", "summary", "script", "style", "h1", "h2", "h3", "h4", "h5", "h6"})
_TIP_PATTERN = re.compile(
    "|".join(
        f"(?P<g{index}>(?<![\\w-]){re.escape(term)}(?![\\w-]))"
        for index, (term, _) in enumerate(sorted(GLOSSARY, key=lambda item: -len(item[0])))
    )
)
_TIP_ORDER = tuple(term for term, _ in sorted(GLOSSARY, key=lambda item: -len(item[0])))
_TIP_TEXT = dict(GLOSSARY)

_LESSON_BY_STEM = {lesson.stem: lesson for lesson in LESSONS}


class SiteBuildError(ValueError):
    """Raised when the exported public stage is incomplete or malformed."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SiteBuildError(f"cannot read JSON file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SiteBuildError(f"JSON file must contain an object: {path}")
    return payload


def _text(value: Any) -> str:
    if isinstance(value, list):
        return "".join(str(item) for item in value)
    return str(value) if value is not None else ""


# ---------------------------------------------------------------------------
# Tooltips and glossary
# ---------------------------------------------------------------------------


class _Tips:
    """Per-page tooltip registry: unique ids and first-use-only glossary terms."""

    def __init__(self) -> None:
        self._count = 0
        self._used: set[str] = set()

    def tip(self, label: str, text: str, *, extra_class: str = "") -> str:
        """Return a focusable term with a short hover/focus/tap explanation."""

        self._count += 1
        tip_id = f"tip-{self._count}"
        classes = f"tip {extra_class}".strip()
        return (
            f'<span class="tipwrap"><button type="button" class="{classes}" '
            f'aria-describedby="{tip_id}" aria-expanded="false">{label}</button>'
            f'<span class="tip-body" role="tooltip" id="{tip_id}">{html.escape(text)}</span></span>'
        )

    def info(self, text: str, *, label: str = "More") -> str:
        """A small ⓘ button for a detail that should stay out of the way."""

        return self.tip(f'<span aria-hidden="true">i</span><span class="sr-only">{html.escape(label)}</span>', text, extra_class="tip-info")

    def glossary(self, fragment: str) -> str:
        """Wrap the first use of each glossary term outside code and links."""

        parts = re.split(r"(<[^>]+>)", fragment)
        depth = 0
        for index, part in enumerate(parts):
            if part.startswith("<"):
                match = re.match(r"<(/?)([A-Za-z0-9]+)", part)
                if match and match.group(2).lower() in _SKIP_TAGS and not part.endswith("/>"):
                    depth += -1 if match.group(1) else 1
                    depth = max(depth, 0)
                continue
            if depth or not part.strip():
                continue

            def replace(match: re.Match[str]) -> str:
                term = _TIP_ORDER[int(match.lastgroup[1:])]  # type: ignore[index]
                if term in self._used:
                    return match.group(0)
                self._used.add(term)
                return self.tip(html.escape(match.group(0), quote=False), _TIP_TEXT[term], extra_class="tip-term")

            parts[index] = _TIP_PATTERN.sub(replace, part)
        return "".join(parts)


# ---------------------------------------------------------------------------
# Markdown and notebook output
# ---------------------------------------------------------------------------


def _inline_markdown(value: str) -> str:
    escaped = html.escape(value, quote=False)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", escaped)

    def link(match: re.Match[str]) -> str:
        label = match.group(1)
        target = html.unescape(match.group(2))
        if not target.startswith(("https://", "http://", "#", "/")):
            return label
        return f'<a href="{html.escape(target, quote=True)}">{label}</a>'

    return re.sub(r"\[([^]]+)\]\(([^)]+)\)", link, escaped)


def _table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _render_markdown(source: str) -> str:
    """Render the small Markdown vocabulary used by the configured lessons."""

    lines = source.replace("\r\n", "\n").split("\n")
    blocks: list[str] = []
    paragraph: list[str] = []
    list_items: list[tuple[str, str]] = []
    table_rows: list[list[str]] = []

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(f"<p>{_inline_markdown(' '.join(item.strip() for item in paragraph))}</p>")
            paragraph.clear()

    def flush_list() -> None:
        if list_items:
            blocks.append(
                "<ul>" + "".join(f'<li class="{kind}">{item}</li>' if kind else f"<li>{item}</li>" for kind, item in list_items) + "</ul>"
            )
            list_items.clear()

    def flush_table() -> None:
        if not table_rows:
            return
        head, *body = table_rows
        blocks.append(
            '<div class="table-scroll"><table><thead><tr>'
            + "".join(f'<th scope="col">{_inline_markdown(cell)}</th>' for cell in head)
            + "</tr></thead><tbody>"
            + "".join("<tr>" + "".join(f"<td>{_inline_markdown(cell)}</td>" for cell in row) + "</tr>" for row in body)
            + "</tbody></table></div>"
        )
        table_rows.clear()

    for line in lines:
        stripped = line.strip()
        heading = re.match(r"^(#{1,4})\s+(.+?)\s*#*$", stripped)
        bullet = re.match(r"^[-*+]\s+(.+)$", stripped)
        if stripped.startswith("|") and stripped.endswith("|"):
            flush_paragraph()
            flush_list()
            if not re.fullmatch(r"\|[\s:|-]+\|", stripped):
                table_rows.append(_table_cells(stripped))
            continue
        flush_table()
        if heading:
            flush_paragraph()
            flush_list()
            level = min(len(heading.group(1)) + 1, 5)
            blocks.append(f"<h{level}>{_inline_markdown(heading.group(2))}</h{level}>")
        elif bullet:
            flush_paragraph()
            raw = bullet.group(1)
            kind = ""
            if raw.startswith("**Supports"):
                kind = "pos"
            elif re.match(r"\*\*(Does not|Boundary|Not )", raw):
                kind = "neg"
            list_items.append((kind, _inline_markdown(raw)))
        elif not stripped:
            flush_paragraph()
            flush_list()
        else:
            flush_list()
            paragraph.append(stripped)
    flush_paragraph()
    flush_list()
    flush_table()
    return "\n".join(blocks)


def _render_output(output: dict[str, Any]) -> str:
    output_type = output.get("output_type")
    if output_type == "stream":
        return f'<pre class="output"><code>{html.escape(_text(output.get("text")))}</code></pre>'
    if output_type == "error":
        traceback = output.get("traceback")
        return f'<pre class="output"><code>{html.escape(_text(traceback))}</code></pre>'

    data = output.get("data")
    if not isinstance(data, dict):
        return ""
    if "image/png" in data:
        encoded = _text(data["image/png"]).replace("\n", "")
        try:
            base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            return '<p class="output-note">Output image was malformed and was not rendered.</p>'
        return (
            '<figure class="output-figure">'
            f'<img src="data:image/png;base64,{html.escape(encoded, quote=True)}" alt="Notebook output image">'
            "</figure>"
        )
    if "text/html" in data:
        # Headline result cards are our own deterministic markup from committed
        # executed outputs: render them, not their escaped source. This branch
        # must stay above text/plain, whose accompanying repr
        # (<IPython...HTML object>) carries no information.
        return f'<div class="output output-html">{_text(data["text/html"])}</div>'
    if "text/plain" in data:
        return f'<pre class="output"><code>{html.escape(_text(data["text/plain"]))}</code></pre>'
    return ""


def _titled_cell_title(source_text: str) -> str | None:
    """Return the Colab `#@title` cell title, if the cell declares one."""
    for line in source_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#@title"):
            return stripped[len("#@title") :].strip() or "Setup"
        if stripped.startswith("# @title"):
            return stripped[len("# @title") :].strip() or "Setup"
        return None
    return None


def _code_is_short(source_text: str) -> bool:
    """Short terminal-style cells stay open; long scripts are folded."""

    lines = [line for line in source_text.splitlines() if line.strip() and not line.strip().startswith("#")]
    if len(lines) <= 4:
        return True
    return any(line.lstrip().startswith("!cept ") for line in lines) and len(lines) <= 10


def _render_code_cell(cell: dict[str, Any]) -> tuple[str, bool]:
    """Render one code cell as folded/open code plus its saved output."""

    source_text = _text(cell.get("source"))
    code = html.escape(source_text, quote=False)
    title = _titled_cell_title(source_text)
    line_count = len([line for line in source_text.splitlines() if line.strip()])
    if title is not None:
        summary = re.sub(r"^\d+\.\s*", "", title)
        is_open = False
    else:
        summary = f"{line_count} line{'s' if line_count != 1 else ''}"
        is_open = _code_is_short(source_text)
    open_attr = " open" if is_open else ""
    code_block = (
        f'<details class="code"{open_attr}><summary><span class="code-tag">Code</span> {html.escape(summary)}</summary>'
        f'<pre class="code-body"><code>{code}</code></pre></details>'
    )
    outputs = cell.get("outputs")
    rendered_outputs: list[str] = []
    if isinstance(outputs, list):
        for output in outputs:
            if isinstance(output, dict):
                rendered = _render_output(output)
                if rendered:
                    rendered_outputs.append(rendered)

    def result(inner: str) -> str:
        return f'<div class="result"><div class="result-label">Saved output</div>{inner}</div>'

    if rendered_outputs:
        return code_block + result('<div class="out" tabindex="0" role="region" aria-label="Saved output">' + "".join(rendered_outputs) + "</div>"), False
    if isinstance(outputs, list) and outputs:
        return (
            code_block
            + result('<p class="not-executed"><strong>This output cannot be displayed here.</strong> Run the notebook in Colab to see it.</p>'),
            False,
        )
    return (
        code_block
        + result('<p class="not-executed"><strong>No saved output yet.</strong> Run this lesson in Colab to generate it.</p>'),
        True,
    )


# ---------------------------------------------------------------------------
# Lesson body: notebook cells -> compact steps
# ---------------------------------------------------------------------------

_HEADING = re.compile(r"^(#{2,3})\s+(.*)$")


def _split_label(heading: str) -> tuple[str, str]:
    """Split ``⚙ Run — load flow`` into ``("Run", "load flow")``."""

    text = re.sub(r"^[^\w\s#]+\s*", "", heading.strip())
    parts = re.split(r"\s+[—–-]\s+", text, maxsplit=1)
    verb = parts[0].strip()
    detail = parts[1].strip() if len(parts) > 1 else ""
    return verb, detail


def _sentence(value: str) -> str:
    return value[:1].upper() + value[1:] if value else value


class _LessonBody(NamedTuple):
    about: str
    steps: str
    interpret: str
    optional: str
    code_count: int
    missing_count: int


def _render_lesson_body(notebook: dict[str, Any], lesson: Lesson, tips: _Tips) -> _LessonBody:
    """Turn notebook cells into compact steps, a takeaway extra, and optional sections."""

    cells = notebook.get("cells")
    if not isinstance(cells, list):
        raise SiteBuildError("notebook has no valid cells array")

    about: list[str] = []
    interpret: list[str] = []
    blocks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    code_count = 0
    missing_count = 0
    first_markdown = True
    key_found = False

    for raw in cells:
        if not isinstance(raw, dict):
            continue
        cell_type = raw.get("cell_type")
        if cell_type == "markdown":
            source = _text(raw.get("source"))
            if first_markdown:
                first_markdown = False
                about.append(_render_markdown(re.sub(r"^\s*#\s+[^\n]+\n?", "", source, count=1)))
                continue
            first_line, _, rest = source.strip().partition("\n")
            match = _HEADING.match(first_line.strip())
            if match and len(match.group(1)) == 2:
                verb, detail = _split_label(match.group(2))
                lowered = verb.lower()
                if lowered.startswith("interpret"):
                    current = None
                    interpret.append(_render_markdown(rest))
                    continue
                kind = "optional" if lowered.startswith("optional") else "setup" if lowered in {"setup", "runtime"} else "step"
                current = {"kind": kind, "verb": verb, "detail": detail, "body": [_render_markdown(rest)], "cells": []}
                blocks.append(current)
            else:
                body = _render_markdown(source)
                if current is None:
                    about.append(body)
                else:
                    current["body"].append(body)
        elif cell_type == "code":
            code_count += 1
            if current is None:
                current = {"kind": "step", "verb": "Run", "detail": "", "body": [], "cells": []}
                blocks.append(current)
            content, missing = _render_code_cell(raw)
            missing_count += int(missing)
            cell_id = _text(raw.get("id"))
            if cell_id == lesson.key_cell:
                key_found = True
                content = f'<div class="key-result" id="key-result">{content}</div>'
            current["cells"].append(content)

    if not key_found:
        raise SiteBuildError(f"lesson {lesson.stem} has no key result cell {lesson.key_cell!r}")

    rendered: list[str] = []
    optional: list[str] = []
    number = 0
    for block in blocks:
        inner = "".join(tips.glossary(part) for part in block["body"]) + "".join(block["cells"])
        if block["kind"] == "setup":
            title = block["detail"] or block["verb"]
            rendered.append(
                f'<details class="step step-setup"><summary><span class="step-verb">{html.escape(block["verb"])}</span> '
                f"{html.escape(_sentence(title))}</summary><div class=\"step-inner\">{inner}</div></details>"
            )
        elif block["kind"] == "optional":
            title = block["detail"] or block["verb"]
            optional.append(
                f'<details class="step step-optional"><summary><span class="step-verb">Optional</span> '
                f"{html.escape(_sentence(title))}</summary><div class=\"step-inner\">{inner}</div></details>"
            )
        else:
            number += 1
            title = block["detail"] or block["verb"]
            rendered.append(
                f'<section class="step"><header class="step-head"><span class="step-num" aria-hidden="true">{number}</span>'
                f'<div><span class="step-verb">{html.escape(block["verb"])}</span>'
                f"<h3>{html.escape(_sentence(title))}</h3></div></header>"
                f'<div class="step-inner">{inner}</div></section>'
            )
    about_html = "".join(tips.glossary(part) for part in about)
    interpret_html = "".join(tips.glossary(part) for part in interpret)
    return _LessonBody(about_html, "\n".join(rendered), interpret_html, "\n".join(optional), code_count, missing_count)


# ---------------------------------------------------------------------------
# Export plumbing
# ---------------------------------------------------------------------------


def _source_revision(stage_root: Path) -> tuple[str, str]:
    manifest_path = stage_root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise SiteBuildError(f"missing exported source manifest: {manifest_path}")
    manifest = _read_json(manifest_path)
    revision = manifest.get("canonical_source_commit")
    if not isinstance(revision, str) or not revision.strip() or revision.strip() == "unknown":
        raise SiteBuildError("export manifest must disclose canonical_source_commit")
    return revision.strip(), _sha256(manifest_path)


def _notebook_paths(stage_root: Path) -> list[tuple[Lesson, Path]]:
    manifest = _read_json(stage_root / MANIFEST_NAME)
    paths = manifest.get("files")
    if not isinstance(paths, list):
        raise SiteBuildError("export manifest must contain a files inventory")
    staged_files = {
        str(item.get("path"))
        for item in paths
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    selected: list[tuple[Lesson, Path]] = []
    for lesson in LESSONS:
        relative = Path("public/notebooks") / f"{lesson.stem}.ipynb"
        path = stage_root / relative
        if not path.is_file():
            raise SiteBuildError(f"staging root is missing lesson notebook: {relative.as_posix()}")
        if staged_files and relative.as_posix() not in staged_files:
            raise SiteBuildError(f"lesson notebook is not recorded in export manifest: {relative.as_posix()}")
        selected.append((lesson, path))
    extras = sorted(
        path.name for path in (stage_root / "public/notebooks").glob("*.ipynb") if path.stem not in _LESSON_BY_STEM
    )
    if extras:
        raise SiteBuildError("unexpected notebook files in public stage: " + ", ".join(extras))
    return selected


def _urls(repository: str, relative: str) -> tuple[str, str]:
    encoded = "/".join(quote(part) for part in relative.split("/"))
    base = f"https://github.com/{repository}/blob/{PUBLIC_BRANCH}/{encoded}"
    colab = f"https://colab.research.google.com/github/{repository}/blob/{PUBLIC_BRANCH}/{encoded}"
    return base, colab


# ---------------------------------------------------------------------------
# Page chrome
# ---------------------------------------------------------------------------

_SCRIPT = """
(function () {
  var tips = Array.prototype.slice.call(document.querySelectorAll('.tip'));
  function closeAll(except) {
    tips.forEach(function (t) { if (t !== except) { t.setAttribute('aria-expanded', 'false'); } });
  }
  tips.forEach(function (t) {
    t.addEventListener('click', function (e) {
      var open = t.getAttribute('aria-expanded') === 'true';
      closeAll(t);
      t.removeAttribute('data-dismissed');
      t.setAttribute('aria-expanded', open ? 'false' : 'true');
      e.stopPropagation();
    });
    var wrap = t.parentNode;
    ['pointerover', 'focusin'].forEach(function (name) {
      wrap.addEventListener(name, function () { t.removeAttribute('data-dismissed'); place(t); });
    });
  });
  document.addEventListener('click', function () { closeAll(null); });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      tips.forEach(function (t) { t.setAttribute('aria-expanded', 'false'); t.setAttribute('data-dismissed', ''); });
    }
  });
  function place(t) {
    var body = t.nextElementSibling;
    if (!body) { return; }
    body.style.transform = '';
    var r = body.getBoundingClientRect();
    var vw = document.documentElement.clientWidth;
    var dx = 0;
    if (r.right > vw - 8) { dx = vw - 8 - r.right; }
    if (r.left + dx < 8) { dx = 8 - r.left; }
    if (dx) { body.style.transform = 'translateX(' + dx + 'px)'; }
  }
  function placeAll() { tips.forEach(place); }
  placeAll();
  window.addEventListener('resize', placeAll);
  window.addEventListener('load', placeAll);
  if (document.fonts && document.fonts.ready) { document.fonts.ready.then(placeAll); }
  var rail = document.querySelector('.rail');
  if (rail && window.matchMedia('(max-width: 900px)').matches) { rail.open = false; }
})();
"""


def _page_document(title: str, body: str, *, stylesheet: str, description: str) -> str:
    return f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{html.escape(description, quote=True)}">
  <meta http-equiv="Cache-Control" content="no-store, no-cache, must-revalidate, max-age=0">
  <meta http-equiv="Pragma" content="no-cache">
  <meta http-equiv="Expires" content="0">
  <title>{html.escape(title)} | CEPT Education</title>
  <link rel="stylesheet" href="{stylesheet}">
</head>
<body>
  <a class="skip-link" href="#content">Skip to content</a>
  {body}
  <script>{_SCRIPT}</script>
</body>
</html>
'''


def _header(*, home_href: str, repository_url: str, home: bool) -> str:
    if home:
        links = '<a href="#how">How it works</a><a href="#course">Course</a><a href="#limits">Limits</a>'
    else:
        links = f'<a href="{home_href}#course">Course</a>'
    return f'''
<header class="site-header">
  <div class="shell header-inner">
    <a class="brand" href="{home_href}" aria-label="CEPT Education home">
      <span class="brand-mark" aria-hidden="true">C</span>
      <span class="brand-text"><strong>CEPT</strong><small>Education</small></span>
    </a>
    <nav aria-label="Primary">{links}<a class="nav-source" href="{html.escape(repository_url, quote=True)}">Source</a></nav>
  </div>
</header>
'''


def _footer(repository_url: str) -> str:
    return f'''
<footer class="site-footer"><div class="shell">
  <span>CEPT Education · Apache-2.0</span>
  <span class="footer-note">Demonstration results; not field validation.</span>
  <span class="footer-links"><a href="{html.escape(repository_url, quote=True)}">Source</a><a href="{html.escape(repository_url, quote=True)}/blob/main/LICENSE">License</a></span>
</div></footer>
'''


def _track_label(track: str) -> str:
    return next(label for key, label, _ in TRACKS if key == track)


# ---------------------------------------------------------------------------
# Lesson page
# ---------------------------------------------------------------------------


def _lesson_rail(current: Lesson) -> str:
    groups: list[str] = []
    for key, label, _ in TRACKS:
        items = []
        for lesson in LESSONS:
            if lesson.track != key:
                continue
            if lesson.stem == current.stem:
                items.append(
                    f'<li><a class="rail-link" href="{lesson.stem}.html" aria-current="page">'
                    f'<span class="rail-num">{lesson.number}</span>{html.escape(lesson.title)}</a></li>'
                )
            else:
                items.append(
                    f'<li><a class="rail-link" href="{lesson.stem}.html">'
                    f'<span class="rail-num">{lesson.number}</span>{html.escape(lesson.title)}</a></li>'
                )
        groups.append(f'<div class="rail-group"><div class="rail-track">{html.escape(label)}</div><ol>{"".join(items)}</ol></div>')
    return (
        '<aside class="lesson-aside" aria-label="Course">'
        '<details class="rail" open><summary><span>Course</span><small>8 lessons</small></summary>'
        f'<nav aria-label="Lessons">{"".join(groups)}</nav></details></aside>'
    )


def _lesson_pagination(current: Lesson) -> str:
    index = LESSONS.index(current)
    items: list[str] = []
    if index > 0:
        prev = LESSONS[index - 1]
        items.append(
            f'<a class="page-link page-prev" rel="prev" href="{prev.stem}.html"><small>Previous</small>'
            f"<strong>{html.escape(prev.title)}</strong></a>"
        )
    if index < len(LESSONS) - 1:
        nxt = LESSONS[index + 1]
        items.append(
            f'<a class="page-link page-next" rel="next" href="{nxt.stem}.html"><small>Next</small>'
            f"<strong>{html.escape(nxt.title)}</strong></a>"
        )
    return f'<nav class="pagination" aria-label="Lesson pagination">{"".join(items)}</nav>'


def _lesson_page(
    lesson: Lesson,
    notebook_path: str,
    notebook: dict[str, Any],
    repository: str,
) -> tuple[str, int, int]:
    tips = _Tips()
    body_parts = _render_lesson_body(notebook, lesson, tips)
    about, steps, interpret, optional = body_parts.about, body_parts.steps, body_parts.interpret, body_parts.optional
    code_count, missing_count = body_parts.code_count, body_parts.missing_count
    read_url, colab_url = _urls(repository, notebook_path)
    repository_url = f"https://github.com/{repository}"
    status = "results to generate in Colab" if missing_count else "saved results shown"
    chips = "".join(f"<li>{html.escape(item)}</li>" for item in lesson.sees)
    about_block = (
        f'<details class="about"><summary>About this lesson</summary><div class="about-body">{about}</div></details>'
        if about.strip()
        else ""
    )
    interpret_block = (
        f'<details class="about"><summary>Full interpretation</summary><div class="about-body">{interpret}</div></details>'
        if interpret.strip()
        else ""
    )
    optional_block = f'<div class="optional">{optional}</div>' if optional.strip() else ""
    body = f'''
{_header(home_href="../index.html", repository_url=repository_url, home=False)}
<main id="content" class="shell lesson-page">
  <div class="lesson-layout">
    {_lesson_rail(lesson)}
    <article class="lesson-main">
      <header class="lesson-hero">
        <p class="kicker"><span>Lesson {html.escape(lesson.number)}</span> · {html.escape(_track_label(lesson.track))}</p>
        <h1>{html.escape(lesson.title)}</h1>
        <p class="question">{html.escape(lesson.question)}</p>
        <ul class="sees" aria-label="You will see">{chips}</ul>
        <div class="lesson-actions">
          <a class="button button-primary" href="{html.escape(colab_url, quote=True)}">Open in Colab</a>
          <a class="button button-quiet" href="#key-result">Jump to key result</a>
          <a class="text-link" href="{html.escape(read_url, quote=True)}">Notebook source</a>
        </div>
        <p class="lesson-meta">{code_count} code cells · {html.escape(status)}</p>
      </header>
      {about_block}
      <div class="steps">{steps}</div>
      <section class="takeaway" aria-labelledby="takeaway-heading">
        <h2 id="takeaway-heading">Takeaway</h2>
        <p class="takeaway-main">{html.escape(lesson.takeaway)}</p>
        <p class="takeaway-limit"><span>Not shown</span> {html.escape(lesson.limit)}</p>
        {interpret_block}
      </section>
      {optional_block}
      {_lesson_pagination(lesson)}
    </article>
  </div>
</main>
{_footer(repository_url)}
'''
    page = _page_document(lesson.title, body, stylesheet="../assets/education.css", description=lesson.question)
    return page, code_count, missing_count


# ---------------------------------------------------------------------------
# Homepage
# ---------------------------------------------------------------------------

_ICONS = {
    "inputs": '<path d="M7 3h7l4 4v14H7z"/><path d="M14 3v4h4M10 12h5M10 16h5"/>',
    "network": '<circle cx="6" cy="6" r="2"/><circle cx="18" cy="8" r="2"/><circle cx="12" cy="18" r="2"/><path d="M8 6l8 2M7 8l4 8M17 10l-4 6"/>',
    "repeat": '<path d="M4 12a8 8 0 0 1 14-5M20 4v4h-4M20 12a8 8 0 0 1-14 5M4 20v-4h4"/>',
    "limits": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
}


def _icon(name: str) -> str:
    return (
        '<svg class="icon" viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" '
        f'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">{_ICONS[name]}</svg>'
    )


def _hero_terminal() -> str:
    return '''
<figure class="terminal" aria-label="Example CEPT commands and output">
  <div class="terminal-bar" aria-hidden="true"><span></span><span></span><span></span></div>
<pre><code><span class="t-cmd">cept case check</span> &lt;case&gt;.json
<span class="t-out">Readiness: PASS · Engine: opendss</span>
<span class="t-cmd">cept study run</span> &lt;case&gt;.json
<span class="t-out">Status   PASSED</span>
<span class="t-cmd">cept study verify</span> &lt;run&gt;
<span class="t-ok">[PASS]</span> <span class="t-out">Case identity</span>
<span class="t-ok">[PASS]</span> <span class="t-out">Solver result</span>
<span class="t-ok">[PASS]</span> <span class="t-out">Saved evidence</span></code></pre>
  <figcaption>Output as saved in Lesson 01. Paths shortened.</figcaption>
</figure>
'''


def _comparison_chart() -> str:
    scale = 380.0
    left = 170
    direct = round(0.315818 * scale)
    declared = round(0.947691 * scale)
    tick = left + round(scale)
    return f'''
<figure class="compare">
  <svg viewBox="0 0 580 170" role="img" aria-labelledby="cmp-t cmp-d">
    <title id="cmp-t">Node 4 voltage with and without a declared voltage base</title>
    <desc id="cmp-d">Bar chart in per-unit. The direct script that omits the downstream voltage base reads about 0.316. The CEPT run with the declared base reads about 0.948. A dashed line marks 1.0.</desc>
    <g class="cmp-grid"><line x1="{left}" y1="24" x2="{left}" y2="124"/><line class="cmp-ref" x1="{tick}" y1="24" x2="{tick}" y2="124"/></g>
    <text class="cmp-lbl" x="{left - 12}" y="58" text-anchor="end">Direct script</text>
    <text class="cmp-sub" x="{left - 12}" y="76" text-anchor="end">base omitted</text>
    <rect class="cmp-bar cmp-bar--direct" x="{left}" y="40" width="{direct}" height="36" rx="3"/>
    <text class="cmp-val" x="{left + direct + 10}" y="64">0.316 pu</text>
    <text class="cmp-lbl" x="{left - 12}" y="108" text-anchor="end">With CEPT</text>
    <text class="cmp-sub" x="{left - 12}" y="126" text-anchor="end">base declared</text>
    <rect class="cmp-bar cmp-bar--declared" x="{left}" y="90" width="{declared}" height="36" rx="3"/>
    <text class="cmp-val" x="{left + declared - 10}" y="114" text-anchor="end">0.948 pu</text>
    <text class="cmp-axis" x="{tick}" y="146" text-anchor="middle">1.0 pu</text>
    <text class="cmp-axis" x="{left}" y="146" text-anchor="middle">0</text>
  </svg>
</figure>
'''


def _course_card(lesson: Lesson, *, tips: _Tips, code_count: int, missing_count: int, repository: str) -> str:
    _, colab_url = _urls(repository, f"public/notebooks/{lesson.stem}.ipynb")
    status = "Run to generate" if missing_count else "Saved results"
    start = '<span class="badge">Start here</span>' if lesson.stem == "01_why_solvers_lie" else ""
    return f'''
<article class="course-card" data-lesson="{lesson.stem}">
  <div class="cc-top"><span class="cc-num">{lesson.number}</span>{start}{tips.info(lesson.takeaway, label=f"Takeaway for lesson {lesson.number}")}</div>
  <h4><a href="lessons/{lesson.stem}.html">{html.escape(lesson.title)}</a></h4>
  <p class="cc-q">{html.escape(lesson.question)}</p>
  <p class="cc-meta"><span>{code_count} code cells</span><span>{status}</span><a href="{html.escape(colab_url, quote=True)}">Colab<span class="sr-only"> for lesson {lesson.number}</span></a></p>
</article>
'''


def _index_page(statuses: dict[str, tuple[int, int]], repository: str) -> str:
    tips = _Tips()
    repository_url = f"https://github.com/{repository}"
    tracks_html: list[str] = []
    for key, label, note in TRACKS:
        cards = "".join(
            _course_card(lesson, tips=tips, code_count=statuses[lesson.stem][0], missing_count=statuses[lesson.stem][1], repository=repository)
            for lesson in LESSONS
            if lesson.track == key
        )
        tracks_html.append(
            f'<section class="track" aria-labelledby="track-{key}"><div class="track-head"><h3 id="track-{key}">{label}</h3>'
            f"<p>{html.escape(note)}</p></div><div class=\"cards\">{cards}</div></section>"
        )

    benefits = (
        ("inputs", "Clear inputs", "One structured file holds the network, units and settings.",
         "Missing data stays flagged instead of being filled in silently."),
        ("network", "A visible network", "Get a one-line diagram and per-phase results from the same model.",
         "Diagrams are drawn from the model, not placed by hand."),
        ("repeat", "Repeatable runs", "Each run keeps its inputs, outputs and checks together.",
         "Re-run the same inputs and compare. Hashes detect changes to saved files."),
        ("limits", "Stated limits", "Every lesson says what its result does not show.",
         "Course results are demonstrations, not field validation."),
    )
    benefit_html = "".join(
        f'<li class="benefit">{_icon(icon)}<div><h3>{title}</h3><p>{line}</p></div>{tips.info(detail, label=f"More about {title}")}</li>'
        for icon, title, line, detail in benefits
    )
    steps = (
        ("Define", "Describe the network and study.", "Network, units and study settings go in one typed Case."),
        ("Check", "See what is missing.", "CEPT reports unresolved required inputs before it runs anything."),
        ("Solve", "OpenDSS does the solving.", "CEPT hands the model to OpenDSS and keeps the raw output."),
        ("Inspect", "Read diagrams and results.", "Single-line diagram, phase voltages and study plots."),
        ("Verify", "Check the saved run.", "Checks run identity and file integrity. They do not check physical correctness."),
    )
    step_html = "".join(
        f'<li class="flow-step{" flow-solve" if name == "Solve" else ""}"><span class="flow-num" aria-hidden="true">{index}</span>'
        f"<h3>{name}</h3><p>{line}</p>{tips.info(detail, label=f'More about {name}')}</li>"
        for index, (name, line, detail) in enumerate(steps, start=1)
    )
    chips = (
        tips.tip("OpenDSS solver", "A free, open-source power-system simulator. CEPT calls it; it does not replace it."),
        tips.tip("Opens in Colab", "Each lesson is a notebook that opens in Google Colab."),
        tips.tip("Saved results", "Every lesson page shows real solver output saved from a run."),
    )
    shows = ("Worked OpenDSS examples with saved outputs.", "How inputs, results and checks stay linked.", "Where assumptions enter a study.")
    not_shows = (
        "Field validation or project approval.",
        "Protection-setting or planning decisions.",
        "Agreement with other tools, for example PowerFactory.",
    )
    body = f'''
{_header(home_href="index.html", repository_url=repository_url, home=True)}
<main id="content" class="home">
  <section class="hero shell" aria-labelledby="hero-heading">
    <div class="hero-copy">
      <p class="kicker">Power-system studies with CEPT</p>
      <h1 id="hero-heading">See exactly what produced each result.</h1>
      <p class="hero-lead">CEPT keeps the inputs, the OpenDSS run, the diagram and the checks together, so you can trace a number back to its Case and re-run it. Eight short lessons in Google Colab.</p>
      <div class="hero-actions">
        <a class="button button-primary" href="lessons/01_why_solvers_lie.html">Start with Lesson 1 <span aria-hidden="true">→</span></a>
        <a class="button button-quiet" href="#how">See how it works</a>
      </div>
      <ul class="chips">{"".join(f"<li>{chip}</li>" for chip in chips)}</ul>
    </div>
    {_hero_terminal()}
  </section>

  <section class="band band-benefits" aria-labelledby="why-heading">
    <div class="shell">
      <h2 id="why-heading">What CEPT adds</h2>
      <ul class="benefits">{benefit_html}</ul>
    </div>
  </section>

  <section class="shell compare-section" aria-labelledby="cmp-heading">
    <div class="compare-copy">
      <p class="kicker">One example</p>
      <h2 id="cmp-heading">Same feeder. One declared voltage base.</h2>
      <p>Both runs converge. Only one reads the downstream voltage base the Case declares.</p>
      <a class="text-link" href="lessons/01_why_solvers_lie.html">Inspect the runs in Lesson 1 <span aria-hidden="true">→</span></a>
    </div>
    {_comparison_chart()}
    <p class="compare-note">Node 4 voltage from Lesson 01's saved OpenDSS output. A teaching example, not a field measurement.</p>
  </section>

  <section id="how" class="band band-flow" aria-labelledby="how-heading">
    <div class="shell">
      <h2 id="how-heading">One Case. Five steps.</h2>
      <ol class="flow">{step_html}</ol>
      <p class="flow-note">CEPT organises the study, OpenDSS solves it, and the engineering judgement stays with you.</p>
    </div>
  </section>

  <section id="course" class="shell course" aria-labelledby="course-heading">
    <h2 id="course-heading">Choose where to start</h2>
    <p class="section-sub">Eight lessons in four stages. Each opens in Colab and shows its saved results here.</p>
    {"".join(tracks_html)}
  </section>

  <section id="limits" class="band band-limits" aria-labelledby="limits-heading">
    <div class="shell limits">
      <h2 id="limits-heading">What this course does and does not show</h2>
      <div class="limits-grid">
        <div class="limit limit-yes"><h3>It shows</h3><ul>{"".join(f"<li>{item}</li>" for item in shows)}</ul></div>
        <div class="limit limit-no"><h3>It does not show</h3><ul>{"".join(f"<li>{item}</li>" for item in not_shows)}</ul></div>
      </div>
      <p class="limits-claim">Course claim level: {tips.tip("WORKFLOW_VALIDATED", "The workflow ran and its recorded checks passed for these examples. Nothing stronger is claimed.", extra_class="tip-code")}</p>
    </div>
  </section>

  <section class="shell final-cta">
    <h2>Ready to try it?</h2>
    <div class="hero-actions">
      <a class="button button-primary" href="lessons/01_why_solvers_lie.html">Start with Lesson 1 <span aria-hidden="true">→</span></a>
      <a class="text-link" href="{html.escape(repository_url, quote=True)}">View the source <span aria-hidden="true">↗</span></a>
    </div>
  </section>
</main>
{_footer(repository_url)}
'''
    return _page_document(
        "Learn power-system studies",
        body,
        stylesheet="assets/education.css",
        description="Eight short Colab lessons on running power-system studies with CEPT and OpenDSS, with inputs, results and checks kept together.",
    )


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def build_site(
    staging_root: Path,
    output_dir: Path,
    *,
    repository: str = PUBLIC_REPOSITORY,
    force: bool = False,
) -> dict[str, Any]:
    """Build the static site from one exported stage and return its inventory."""

    staging_root = staging_root.resolve()
    output_dir = output_dir.resolve()
    if not staging_root.is_dir():
        raise SiteBuildError(f"staging root does not exist: {staging_root}")
    if output_dir == staging_root or staging_root in output_dir.parents:
        raise SiteBuildError("output directory must be outside the exported staging root")
    if not repository or "/" not in repository or any(char in repository for char in " <>\"'"):
        raise SiteBuildError("repository must be an owner/name value")
    revision, manifest_hash = _source_revision(staging_root)
    lessons = _notebook_paths(staging_root)
    stylesheet = staging_root / STYLESHEET
    if not stylesheet.is_file():
        raise SiteBuildError(f"staging root is missing site asset: {STYLESHEET.as_posix()}")

    if output_dir.exists():
        if not force:
            if any(output_dir.iterdir()):
                raise SiteBuildError(f"output directory is not empty: {output_dir}; use --force")
        else:
            shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "assets").mkdir()
    shutil.copy2(stylesheet, output_dir / "assets" / "education.css")
    (output_dir / "assets" / "education.css").chmod(0o644)

    statuses: dict[str, tuple[int, int]] = {}
    lesson_dir = output_dir / "lessons"
    lesson_dir.mkdir(exist_ok=True)
    for lesson, path in lessons:
        notebook = _read_json(path)
        page, code_count, missing_count = _lesson_page(lesson, f"public/notebooks/{lesson.stem}.ipynb", notebook, repository)
        statuses[lesson.stem] = (code_count, missing_count)
        (lesson_dir / f"{lesson.stem}.html").write_text(page, encoding="utf-8", newline="\n")

    (output_dir / "index.html").write_text(_index_page(statuses, repository), encoding="utf-8", newline="\n")
    return {
        "schema": "cept-education-site-v1",
        "source_revision": revision,
        "export_manifest_sha256": manifest_hash,
        "repository": repository,
        "branch": PUBLIC_BRANCH,
        "lessons": [lesson.stem for lesson, _ in lessons],
        "output_dir": str(output_dir),
    }


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("staging_root", nargs="?", type=Path)
    parser.add_argument("output_dir", nargs="?", type=Path)
    parser.add_argument("--staging-root", dest="staging_root_flag", type=Path)
    parser.add_argument("--output-dir", dest="output_dir_flag", type=Path)
    parser.add_argument("--repository", default=PUBLIC_REPOSITORY)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    args.staging_root = args.staging_root_flag or args.staging_root
    args.output_dir = args.output_dir_flag or args.output_dir
    if args.staging_root is None or args.output_dir is None:
        parser.error("staging_root and output_dir are required")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = build_site(
            args.staging_root,
            args.output_dir,
            repository=args.repository,
            force=args.force,
        )
    except (OSError, SiteBuildError) as exc:
        print(f"education site: BLOCKED — {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
