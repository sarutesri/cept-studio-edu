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

#: Owner-approved homepage wording. Used verbatim, which is why the delivery
#: test asserts these two strings rather than paraphrasing them.
HEADLINE = "See your own grid's numbers before you make the big decisions"
SUBTITLE = "CEPT Studio helps you learn power systems. Put in the data you have, run real numbers, and see what is missing instead of having it guessed."



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
    ("assist", "Assist", "Let an AI drive the commands, and see what still decides."),
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
    Lesson(
        "08_ask_in_plain_words", "08", "assist", "Ask in plain words",
        "Can an AI drive CEPT without deciding the result?",
        ("Saved agent session", "A blocked command", "The checks that decide"),
        "An AI can issue the same commands you would. The checks, not the transcript, decide.",
        "A recorded session; not a live or repeatable model run.",
        "assist-verdict",
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
    ("recipe", "A fixed, versioned list of CEPT operations with declared inputs and outputs."),
    ("Dyn1", "A transformer winding connection: delta primary, wye secondary with neutral."),
    ("bus", "A junction point where network elements connect."),
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

    fence: list[str] | None = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```") and (fence is None or stripped == "```"):
            if fence is None:
                flush_paragraph()
                flush_list()
                flush_table()
                fence = []
            else:
                blocks.append('<pre class="code-body fenced"><code>' + html.escape("\n".join(fence)) + "</code></pre>")
                fence = None
            continue
        if fence is not None:
            fence.append(line.rstrip())
            continue
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
            level = min(max(len(heading.group(1)), 2), 5)  # steps own the h2 level; sub-headings start at h3
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
    if fence is not None:  # an unterminated fence keeps its text instead of dropping it
        blocks.append('<pre class="code-body fenced"><code>' + html.escape("\n".join(fence)) + "</code></pre>")
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
                f'<h2 id="step-{number}">{html.escape(_sentence(title))}</h2></div></header>'
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
      requestAnimationFrame(function () { place(t); });
      e.stopPropagation();
    });
    var wrap = t.parentNode;
    ['pointerover', 'focusin'].forEach(function (name) {
      wrap.addEventListener(name, function () { t.removeAttribute('data-dismissed'); requestAnimationFrame(function () { place(t); }); });
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
    body.style.left = '';
    body.style.right = '';
    var r = body.getBoundingClientRect();
    var vw = document.documentElement.clientWidth;
    var dx = 0;
    if (r.right > vw - 8) { dx = vw - 8 - r.right; }
    if (r.left + dx < 8) { dx = 8 - r.left; }
    if (dx) {
      var origin = body.parentNode.getBoundingClientRect().left;
      body.style.right = 'auto';
      body.style.left = (r.left - origin + dx) + 'px';
    }
  }
  var rail = document.querySelector('.rail');
  if (rail && window.matchMedia('(max-width: 900px)').matches) { rail.open = false; }

  // The contents list starts open on a wide screen and folded away on a narrow
  // one, so a phone opens the lesson rather than a wall of links.
  var toc = document.querySelector('.toc');
  if (!toc) { return; }
  if (window.matchMedia('(max-width: 760px)').matches) { toc.open = false; }

  var links = Array.prototype.slice.call(toc.querySelectorAll('.toc-list a'));
  var targets = links.map(function (a) { return document.getElementById(decodeURIComponent(a.hash.slice(1))); });
  if (!('IntersectionObserver' in window) || targets.indexOf(null) !== -1) { return; }
  var lit = [];
  var spy = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      var at = targets.indexOf(entry.target);
      if (at > -1) { lit[at] = entry.isIntersecting; }
    });
    var here = lit.indexOf(true);
    if (here === -1) { return; }
    links.forEach(function (a, i) {
      if (here === i) { a.setAttribute('aria-current', 'true'); } else { a.removeAttribute('aria-current'); }
    });
  }, { rootMargin: '-150px 0px -65% 0px' });
  targets.forEach(function (el) { spy.observe(el); });
})();
"""


def _plain(markup: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", markup)).strip()


_TOC_HEADING = re.compile(r'<h2 id="([^"]+)">(.*?)</h2>', re.S)


def _insert_toc(body: str) -> str:
    """Put a contents list between the header and the main content.

    The list is read back out of the finished markup instead of being declared
    next to it, so a heading that is renamed or removed takes its entry with it
    and the list can never point at an anchor that is not on the page. A heading
    with no id is not a link target, so it is not listed.
    """

    items = [
        f'<li><a href="#{anchor}">{html.escape(_plain(text))}</a></li>'
        for anchor, text in _TOC_HEADING.findall(body)
    ]
    if not items:
        return body
    toc = (
        '<div class="toc-bar"><details class="toc" open>'
        f'<summary><span>Contents</span><small>{len(items)} sections</small></summary>'
        f'<ol class="toc-list">{"".join(items)}</ol></details></div>'
    )
    header, sep, rest = body.partition("<main")
    return f"{header}{toc}{sep}{rest}"


def _page_document(title: str, body: str, *, stylesheet: str, description: str, lang: str = "en") -> str:
    return f'''<!doctype html>
<html lang="{html.escape(lang, quote=True)}">
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
        links = (
            '<a href="#start">Start</a><a href="#how">Five steps</a>'
            '<a href="#editions">Free / Advance</a><a href="#course">Lessons</a><a href="#limits">Limits</a>'
        )
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


def _footer(repository_url: str, *, note: str = "Demonstration results; not field validation.") -> str:
    return f'''
<footer class="site-footer"><div class="shell">
  <span>CEPT Education · Apache-2.0</span>
  <span class="footer-note">{html.escape(note)}</span>
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
        f'<details class="rail" open><summary><span>Course</span><small>{len(LESSONS)} lessons</small></summary>'
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
    page = _page_document(
        lesson.title,
        _insert_toc(body),
        stylesheet="../assets/education.css",
        description=lesson.question,
    )
    return page, code_count, missing_count


# ---------------------------------------------------------------------------
# Free vs Advance — derived from the code boundary, never hand-listed
# ---------------------------------------------------------------------------

#: The machine-readable restatement of the code boundary, written by
#: ``tools/build_edition_boundary.py`` and shipped under ``public/site/**``.
EDITION_BOUNDARY = Path("public/site/edition-boundary.json")

# Reader-facing wording for the derived rows, keyed by the identity the code
# boundary produced.  These are translations, not a capability list: a row only
# exists here if the derivation produced one, and the build refuses to publish a
# derived row it has no wording for, so a new capability cannot reach the page
# unwritten and a retired one cannot be left behind.
FREE_VERB_COPY: dict[str, tuple[str, str]] = {
    "doctor": ("`cept doctor`", "Check that this computer can run OpenDSS, and what it supports"),
    "check": (
        "`cept check`",
        "Read a Case file before you run — it lists what is missing, and `--per-unit` checks the per-unit base",
    ),
    "run": ("`cept run`", "Run one Case file, or a demo case that ships with the program"),
    "verify": (
        "`cept verify`",
        "Check the evidence a run recorded, and check that run's physics with `--physics`",
    ),
}
FREE_STUDY_COPY = "4 OpenDSS studies — load flow, unbalanced load flow, hosting capacity, fault"
FREE_RECIPE_COPY = "Bundled workflow recipes"
FREE_LESSON_COPY = "Colab lessons"

#: How the free wheel's own exclusion list reads on the page.  Keyed by the
#: derived identity, so an unknown entry is refused rather than passed through.
EXCLUDED_COPY = {"powerfactory": "PowerFactory"}
EXCLUDED_STATUS_COPY = {"excluded_initially": "left out from the start", "pro_only": "Advance only"}

ADVANCE_SECTION_COPY: dict[str, str] = {
    "licensed engine adapter": "PowerFactory connection — project import, native SLD, dynamic lane",
    "controller synthesis / PERC1 frames": "Controller synthesis and PERC1 frames",
    "PFD source intake": "Read PowerFactory `.pfd` files into a Case",
    "licensed reference runs": "Run licensed reference cases and read PDFs into an inventory",
    "research runtime": "Run research jobs through a model package",
    "comparison lanes": "Compare results across engines, and compare a PFD against its PDF",
    "PowerFactory QSTS benchmark evidence producer": "QSTS recipes that run on PowerFactory — the result stays a cross-engine diagnostic until a cross-engine comparison is reviewed and accepted",
    "benchmark harness": "Benchmark harness and a reviewed catalogue",
    "source-to-Case fidelity": "Kron reduction rules for comparing a PowerFactory source file with a Case",
    "packaged lane workers (launched as ``python -m <module>``)": "Workers for dynamic jobs and for parity comparison",
}


def _edition_boundary(staging_root: Path) -> dict[str, Any]:
    """Load the derived edition boundary, or refuse to build a partial table."""

    path = staging_root / EDITION_BOUNDARY
    if not path.is_file():
        raise SiteBuildError(
            f"staging root is missing the derived edition boundary: {EDITION_BOUNDARY.as_posix()}"
        )
    boundary = _read_json(path)
    if boundary.get("schema") != "cept-edition-boundary-v1":
        raise SiteBuildError(f"edition boundary has an unexpected schema: {boundary.get('schema')!r}")
    for section in boundary.get("advance", {}).get("sections", []):
        if section.get("label") not in ADVANCE_SECTION_COPY:
            raise SiteBuildError(f"no wording for derived Advance section {section.get('label')!r}")
    for name in boundary["free"]["excluded"]:
        if name not in EXCLUDED_COPY:
            raise SiteBuildError(f"no wording for derived exclusion {name!r}")
    for verb in boundary.get("free", {}).get("verbs", []):
        if verb.get("name") not in FREE_VERB_COPY:
            raise SiteBuildError(f"no wording for derived public verb {verb.get('name')!r}")
    return boundary


def _edition_table(boundary: dict[str, Any]) -> str:
    """Render the Free-vs-Advance feature table from the derived boundary."""

    free, advance = boundary["free"], boundary["advance"]
    rows: list[tuple[str, str, str]] = [
        (f"{command} — {detail}", "✓", "–")
        for command, detail in (FREE_VERB_COPY[verb["name"]] for verb in free["verbs"])
    ]
    rows.append((FREE_STUDY_COPY, "✓", "–"))
    rows.append((f"{FREE_RECIPE_COPY}, {len(free['recipes'])} in all", "✓", "–"))
    rows.append((f"{FREE_LESSON_COPY}, {free['lesson_count']} in all", "✓", "–"))
    rows.extend(
        (ADVANCE_SECTION_COPY[section["label"]], "–", "✓") for section in advance["sections"]
    )

    body = "".join(
        f'<tr><th scope="row">{_inline_markdown(label)}</th><td class="yes">{yes}</td>'
        f'<td class="no">{no}</td></tr>'
        for label, yes, no in rows
    )
    excluded = ", ".join(
        f"{EXCLUDED_COPY.get(name, name)} ({EXCLUDED_STATUS_COPY.get(status, status)})"
        for name, status in free["excluded"].items()
    )
    return f'''
<div class="table-scroll">
<table class="editions">
  <caption class="sr-only">What each edition can do, taken from the code boundary that was actually derived</caption>
  <thead><tr><th scope="col">Capability</th><th scope="col">Declared in<br><small>{html.escape(free["distribution"])}</small></th><th scope="col">Declared in<br><small>{html.escape(advance["distribution"])}</small></th></tr></thead>
  <tbody>{body}</tbody>
</table>
</div>
<p class="editions-note">A <strong>–</strong> means that edition does not declare that capability for itself.
It does not mean the feature is broken: the two editions install separately, and an installed Free wheel cannot call Advance's capabilities.</p>
<p class="editions-note">Left out of the Free wheel from the start: {html.escape(excluded)}</p>
'''


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
    """The three commands a visitor can actually run, with real output.

    Every line here is copied from a committed notebook's saved output, and the
    same commands were re-run against a freshly installed public wheel.
    ``test_homepage_commands_come_from_committed_notebook_output`` keeps the two
    from drifting apart.  The previous version of this figure showed
    ``cept check`` and a ``cept run`` with no ``--out``; neither is accepted by
    the installed wheel.
    """

    return '''
<figure class="terminal" aria-label="Three cept commands and their real output">
  <div class="terminal-bar" aria-hidden="true"><span></span><span></span><span></span></div>
<pre lang="en"><code><span class="t-cmd">cept doctor</span>
<span class="t-ok">[READY]</span> <span class="t-out">CEPT environment check: READY</span>
<span class="t-out">Solver             OpenDSS (ready)</span>
<span class="t-cmd">cept run</span> <span class="t-out">--demo load-flow --network ieee13 --out run1</span>
<span class="t-ok">[FINISHED]</span> <span class="t-out">CEPT study result: FINISHED</span>
<span class="t-cmd">cept verify</span> <span class="t-out">run1</span>
<span class="t-ok">[PASSED]</span> <span class="t-out">CEPT study check: PASSED</span>
<span class="t-out">Checked   3 groups, 14 checks, all passed</span></code></pre>
  <figcaption>Real output from the installed public wheel, recorded in lessons 00 and 04</figcaption>
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
    <title id="cmp-t">Voltage at Node 4 with and without a declared voltage base</title>
    <desc id="cmp-d">A per-unit bar chart. A direct script that does not declare the voltage base reads about 0.316 at the far end, and the CEPT run that declares the base reads about 0.948. A dashed reference line marks 1.0.</desc>
    <g class="cmp-grid"><line x1="{left}" y1="24" x2="{left}" y2="124"/><line class="cmp-ref" x1="{tick}" y1="24" x2="{tick}" y2="124"/></g>
    <text class="cmp-lbl" x="{left - 12}" y="58" text-anchor="end">Direct script</text>
    <text class="cmp-sub" x="{left - 12}" y="76" text-anchor="end">no base declared</text>
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


def _course_card(lesson: Lesson, *, tips: _Tips, missing_count: int, repository: str) -> str:
    _, colab_url = _urls(repository, f"public/notebooks/{lesson.stem}.ipynb")
    status = "no saved results yet" if missing_count else "results saved"
    start = '<span class="badge">Start here</span>' if lesson.stem == "01_why_solvers_lie" else ""
    return f'''
<article class="course-card" data-lesson="{lesson.stem}">
  <div class="cc-top"><span class="cc-num">{lesson.number}</span>{start}{tips.info(lesson.takeaway, label=f"Summary of lesson {lesson.number}")}</div>
  <h4 lang="en"><a href="lessons/{lesson.stem}.html">{html.escape(lesson.title)}</a></h4>
  <p class="cc-meta"><span>{status}</span><a href="{html.escape(colab_url, quote=True)}">Colab<span class="sr-only"> for lesson {lesson.number}</span></a></p>
</article>
'''


def _lesson_wheel(staging_root: Path) -> tuple[str, str]:
    """The wheel URL and digest the shipped lesson bootstrap pins.

    Read from ``public/notebooks/_lesson.py`` rather than restated, so this page
    cannot point at a different artifact from the one the notebooks fetch.
    """
    source = (staging_root / "public" / "notebooks" / "_lesson.py").read_text(encoding="utf-8")
    url = re.search(r'DEFAULT_WHEEL_URL = \(\s*"([^"]+)"', source)
    digest = re.search(r'DEFAULT_WHEEL_SHA256 = \(\s*"([0-9a-f]{64})"', source)
    if not url or not digest:
        raise SiteBuildError(
            "the lesson bootstrap no longer pins a wheel URL and digest; the start "
            "steps cannot name an artifact the notebooks would not fetch"
        )
    return url.group(1), digest.group(1)


def _index_page(
    statuses: dict[str, tuple[int, int]],
    repository: str,
    boundary: dict[str, Any],
    staging_root: Path,
) -> str:
    tips = _Tips()
    repository_url = f"https://github.com/{repository}"
    tracks_html: list[str] = []
    for key, label, note in TRACKS:
        cards = "".join(
            _course_card(lesson, tips=tips, missing_count=statuses[lesson.stem][1], repository=repository)
            for lesson in LESSONS
            if lesson.track == key
        )
        tracks_html.append(
            f'<section class="track" aria-labelledby="track-{key}"><div class="track-head">'
            f'<h3 id="track-{key}" lang="en">{label}</h3></div>'
            f'<div class="cards">{cards}</div></section>'
        )

    # These three assume the wheel is already installed. The zero-install path a
    # newcomer should take first is Colab, and the pinned terminal install lives
    # in lesson 00; repeating either here would make the landing page longer
    # without making the first step executable.
    start_steps = (
        ("cept doctor", "Check that this computer has a working OpenDSS", "READY"),
        ("cept run --demo load-flow --network ieee13 --out run1", "Run a demo case that ships with the program", "FINISHED"),
        ("cept verify run1", "Check the evidence the run recorded", "PASSED"),
    )
    start_html = "".join(
        f'<li class="start-step"><code lang="en">{html.escape(command)}</code>'
        f'<p>{line} <span class="start-out" lang="en">{result}</span></p></li>'
        for command, line, result in start_steps
    )

    helps = (
        ("inputs", "See your own system", "Put your feeder data into one Case file and run it",
         "A Case is a JSON file that keeps the network, the units and the study settings in one place. A value that is missing is refused, not guessed"),
        ("repeat", "Ask what-if questions", "Change a value in the Case and run again — the same network gives a new answer",
         "Every Case file gets its own fingerprint, such as 748c8026c9d6. Edit the file and it changes, so you can tell the two runs apart"),
        ("limits", "Know whether you have enough data", "If you do not, it says what is missing and does not run in your place",
         "Measured on the installed wheel: a Case asking for dynamics is refused with the message not in the CEPT Public scope; unsupported studies are blocked rather than approximated"),
    )
    help_html = "".join(
        f'<li class="benefit">{_icon(icon)}<div><h3>{title}</h3><p>{line}</p></div>{tips.info(detail, label=f"More about {title}")}</li>'
        for icon, title, line, detail in helps
    )

    steps = (
        ("Write a Case", "Network, units and settings live in one file", "Edit the file — you do not edit the solver's script"),
        ("Run", "Hand the Case to OpenDSS and keep the raw results", "cept run always needs --out, so nothing has to guess where to write files"),
        ("Solve", "OpenDSS solves the electrical equations, not CEPT", "If the iteration does not settle, it says so instead of handing you a number anyway"),
        ("Read", "A one-line diagram, per-phase voltages and the study's plots", "The diagram is drawn from the same model that was solved, not drawn by hand"),
        ("Check the evidence", "Verify file identity and checksums", "Knowing a file was not changed does not mean the answer is physically right"),
    )
    step_html = "".join(
        f'<li class="flow-step{" flow-solve" if name == "Solve" else ""}"><span class="flow-num" aria-hidden="true">{index}</span>'
        f"<h3>{name}</h3><p>{line}</p>{tips.info(detail, label=f'More about step {index}')}</li>"
        for index, (name, line, detail) in enumerate(steps, start=1)
    )

    shows = (
        "Examples actually run on OpenDSS, with the results saved in the lesson files",
        "A path from data to results to evidence, with no hidden step",
        "Where an assumption changes the answer, and by how much",
    )
    not_shows = (
        "Field validation or project sign-off — the highest claim level on this site is WORKFLOW_VALIDATED",
        "Deciding grid code compliance, and which cases (including BESS) have to be studied — that belongs to the system owner and the regulator, not to the program",
        "Agreement with PowerFactory in the Free wheel — there is none, and we do not claim the results match",
        "Values measured from a real system — every number on this site comes from the demo cases that ship with the program",
        "The bundled workflow recipes do run on the public wheel's own run results: cept run --recipe completes and then checks that run's own evidence",
        "A live AI model call — lesson 8 is a recorded session that is replayed, with no model connection",
    )
    body = f'''
{_header(home_href="index.html", repository_url=repository_url, home=True)}
<main id="content" class="home">
  <section class="hero shell" aria-labelledby="hero-heading">
    <div class="hero-copy">
      <p class="kicker">CEPT Power Studio — learn power systems with real numbers</p>
      <h1 id="hero-heading">{HEADLINE}</h1>
      <p class="hero-lead">{SUBTITLE}</p>
      <div class="hero-actions">
        <a class="button button-primary" href="lessons/01_why_solvers_lie.html">Start lesson 1 <span aria-hidden="true">→</span></a>
        <a class="button button-quiet" href="#start">See how to start in 5 minutes</a>
      </div>
    </div>
    {_hero_terminal()}
  </section>

  <section id="start" class="band band-start" aria-labelledby="start-heading">
    <div class="shell">
      <h2 id="start-heading">Start in 5 minutes</h2>
      <p class="section-sub">Open it in Colab with nothing to install, or run it here with Python 3.10 — lesson 00 has the install</p>
      <ol class="start">{start_html}</ol>
      <p class="start-note">Then open <code lang="en">run1/case.json</code> — the real Case the program wrote — change it to your own data,
        then run <code lang="en">cept run my-case.json --out run2</code></p>
    </div>
  </section>

  <section class="band band-benefits" aria-labelledby="why-heading">
    <div class="shell">
      <h2 id="why-heading">What it helps with</h2>
      <ul class="benefits benefits-three">{help_html}</ul>
    </div>
  </section>

  <section class="shell compare-section" aria-labelledby="cmp-heading">
    <div class="compare-copy">
      <p class="kicker">A real example from lesson 1</p>
      <h2 id="cmp-heading">Same network, different voltage base</h2>
      <a class="text-link" href="lessons/01_why_solvers_lie.html">See both runs in lesson 1 <span aria-hidden="true">→</span></a>
    </div>
    {_comparison_chart()}
    <p class="compare-note">Voltage at Node 4 from lesson 1's saved OpenDSS output — a teaching example, not a field measurement</p>
  </section>

  <section id="how" class="band band-flow" aria-labelledby="how-heading">
    <div class="shell">
      <h2 id="how-heading">Five steps from data to evidence</h2>
      <ol class="flow">{step_html}</ol>
      <p class="flow-note">CEPT handles the steps and keeps the evidence. The engineering decisions stay yours.</p>
    </div>
  </section>

  <section id="editions" class="band band-editions" aria-labelledby="editions-heading">
    <div class="shell">
      <h2 id="editions-heading">Free wheel vs Advance</h2>
      <p class="section-sub">Derived from the real code boundary of both editions at build time, not written by hand.</p>
      {_edition_table(boundary)}
      <p class="limits-claim">Claim level for everything on this page:
        {tips.tip(boundary["free"]["claim"], "The workflow runs to the end and the checks recorded for these examples pass. Nothing on this page claims more than that.", extra_class="tip-code")}</p>
    </div>
  </section>

  <section id="course" class="shell course" aria-labelledby="course-heading">
    <h2 id="course-heading">{len(LESSONS)} lessons</h2>
    <p class="section-sub">Every lesson opens in Google Colab and shows results saved from a real run</p>
    {"".join(tracks_html)}
  </section>

  <section id="limits" class="band band-limits" aria-labelledby="limits-heading">
    <div class="shell limits">
      <h2 id="limits-heading">What we do and do not claim</h2>
      <div class="limits-grid">
        <div class="limit limit-yes"><h3>We claim</h3><ul>{"".join(f"<li>{item}</li>" for item in shows)}</ul></div>
        <div class="limit limit-no"><h3>We do not claim</h3><ul>{"".join(f"<li>{item}</li>" for item in not_shows)}</ul></div>
      </div>
    </div>
  </section>

  <section class="shell final-cta">
    <h2 id="try-heading">See what your own data would say</h2>
    <div class="hero-actions">
      <a class="button button-primary" href="lessons/01_why_solvers_lie.html">Start lesson 1 <span aria-hidden="true">→</span></a>
      <a class="text-link" href="{html.escape(repository_url, quote=True)}">Browse the source <span aria-hidden="true">↗</span></a>
    </div>
  </section>
</main>
{_footer(repository_url, note="Teaching examples, not field validation")}
'''
    return _page_document(
        HEADLINE,
        _insert_toc(body),
        stylesheet="assets/education.css",
        lang="en",
        description=(
            "CEPT Power Studio teaches power systems with OpenDSS: put in the data you "
            "have, run real numbers, and see what is missing instead of having it guessed."
        ),
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
    boundary = _edition_boundary(staging_root)

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

    (output_dir / "index.html").write_text(
        _index_page(statuses, repository, boundary, staging_root),
        encoding="utf-8",
        newline="\n",
    )
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
