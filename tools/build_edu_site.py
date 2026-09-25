#!/usr/bin/env python3
"""Build the public education site from an exported staging directory.

The builder deliberately has no repository or git dependency.  The staging
directory must contain the public notebook files, ``public/site/education.css``
and the export manifest written by ``public_export.py``.  Notebook output is
rendered only when it is present in the exported JSON; an unexecuted code cell
gets an explicit notice instead of a guessed result.
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
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote


PUBLIC_REPOSITORY = "sarutesri/cept-studio-edu"
PUBLIC_BRANCH = "main"
MANIFEST_NAME = "PUBLIC-EXPORT-MANIFEST.json"
STYLESHEET = Path("public/site/education.css")

LESSONS: tuple[dict[str, str], ...] = (
    {
        "stem": "00_environment",
        "number": "00",
        "stage": "foundation",
        "title_en": "Check the runtime",
        "summary_en": "See which OpenDSS version is installed and what a passing workflow check does—and does not—prove.",
        "outcome_en": "Runtime identity and a bounded readiness result",
    },
    {
        "stem": "01_first_circuit_load_flow",
        "number": "01",
        "stage": "foundation",
        "title_en": "Run a two-bus load flow",
        "summary_en": "Predict the load-bus voltage, run the study, and inspect phase values and losses.",
        "outcome_en": "Example: 0.9998 pu at the load bus and 0.0143 kW loss",
    },
    {
        "stem": "02_ieee13_unbalanced",
        "number": "02",
        "stage": "foundation",
        "title_en": "Explore an unbalanced feeder",
        "summary_en": "Inspect phase-specific voltage on the IEEE 13-node feeder instead of hiding the imbalance inside one average.",
        "outcome_en": "Phase A, B, and C voltage profiles",
    },
    {
        "stem": "03_hosting_capacity",
        "number": "03",
        "stage": "apply",
        "title_en": "Find a PV hosting-capacity bracket",
        "summary_en": "Change the declared voltage criterion and see how the admissible PV range moves.",
        "outcome_en": "A capacity bracket under one explicit criterion",
    },
    {
        "stem": "04_fault_study",
        "number": "04",
        "stage": "apply",
        "title_en": "Run a line-to-ground fault",
        "summary_en": "Apply a declared fault and inspect the solver-returned current.",
        "outcome_en": "Fault current for the declared study—not a protection decision",
    },
    {
        "stem": "05_validation_reproducibility",
        "number": "05",
        "stage": "evidence",
        "title_en": "Verify the saved run",
        "summary_en": "Follow the Case fingerprint and verification receipt for one exact run.",
        "outcome_en": "The Case, artifacts, and checks behind the result",
    },
    {
        "stem": "06_colab_tui",
        "number": "06",
        "stage": "evidence",
        "title_en": "Review incomplete Case information",
        "summary_en": "See how missing inputs remain visible and how strict, assisted, and exploratory policies change what can run.",
        "outcome_en": "Known, missing, default, and AI-selected information kept separate",
    },
)
LESSON_STAGES: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "foundation",
        "01",
        "FOUNDATION",
        "Start with the basics",
        "Build the runtime and result-reading habits.",
    ),
    (
        "apply",
        "02",
        "APPLY",
        "Try a different engineering question",
        "Change one declared input and inspect the response.",
    ),
    (
        "evidence",
        "03",
        "EVIDENCE",
        "Check what the result proves",
        "Follow the saved run and the evidence behind it.",
    ),
)


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
        raise SiteBuildError(f"cannot read JSON artifact {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SiteBuildError(f"JSON artifact must contain an object: {path}")
    return payload


def _text(value: Any) -> str:
    if isinstance(value, list):
        return "".join(str(item) for item in value)
    return str(value) if value is not None else ""


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


def _render_markdown(source: str) -> str:
    """Render the small Markdown vocabulary used by the seven lessons."""

    lines = source.replace("\r\n", "\n").split("\n")
    blocks: list[str] = []
    paragraph: list[str] = []
    list_items: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(f"<p>{_inline_markdown(' '.join(item.strip() for item in paragraph))}</p>")
            paragraph.clear()

    def flush_list() -> None:
        if list_items:
            blocks.append("<ul>" + "".join(f"<li>{item}</li>" for item in list_items) + "</ul>")
            list_items.clear()

    for line in lines:
        stripped = line.strip()
        heading = re.match(r"^(#{1,4})\s+(.+?)\s*#*$", stripped)
        bullet = re.match(r"^[-*+]\s+(.+)$", stripped)
        if heading:
            flush_paragraph()
            flush_list()
            level = len(heading.group(1))
            blocks.append(f"<h{level}>{_inline_markdown(heading.group(2))}</h{level}>")
        elif bullet:
            flush_paragraph()
            list_items.append(_inline_markdown(bullet.group(1)))
        elif not stripped:
            flush_paragraph()
            flush_list()
        else:
            flush_list()
            paragraph.append(stripped)
    flush_paragraph()
    flush_list()
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


def _titled_cell_title(source_text):
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


def _render_code_cell(cell: dict[str, Any]) -> tuple[str, bool]:
    source_text = _text(cell.get("source"))
    code = html.escape(source_text, quote=False)
    title = _titled_cell_title(source_text)
    if title is not None:
        code_block = (
            '<details class="cell-code-setup"><summary>'
            f"{html.escape(title)}</summary>"
            f'<pre class="code"><code>{code}</code></pre></details>'
        )
    else:
        code_block = f'<pre class="code"><code>{code}</code></pre>'
    outputs = cell.get("outputs")
    rendered_outputs: list[str] = []
    if isinstance(outputs, list):
        for output in outputs:
            if isinstance(output, dict):
                rendered = _render_output(output)
                if rendered:
                    rendered_outputs.append(rendered)
    code_section = (
        '<section class="cell cell-code">'
        '<div class="cell-label">Code</div>'
        + code_block
        + "</section>"
    )
    if rendered_outputs:
        return (
            code_section
            + '<section class="cell-result"><div class="cell-label">Result</div>'
            + "".join(rendered_outputs)
            + "</section>",
            False,
        )
    if isinstance(outputs, list) and outputs:
        return (
            code_section
            + '<section class="cell-result"><div class="cell-label">Result</div>'
            + '<p class="not-executed"><strong>This result cannot be displayed on this page.</strong> '
            + "Run the notebook in Colab to inspect the complete solver output."
            + "</p></section>",
            False,
        )
    return (
        code_section
        + '<section class="cell-result"><div class="cell-label">Result</div>'
        + '<p class="not-executed"><strong>Result not generated yet.</strong> '
        + "Run this lesson in Colab to produce the solver-backed result."
        + "</p></section>",
        True,
    )


def _render_notebook(notebook: dict[str, Any]) -> tuple[str, int, int]:
    cells = notebook.get("cells")
    if not isinstance(cells, list):
        raise SiteBuildError("notebook has no valid cells array")
    rendered: list[str] = []
    code_count = 0
    missing_output_count = 0
    first_markdown = True
    for raw_cell in cells:
        if not isinstance(raw_cell, dict):
            continue
        cell_type = raw_cell.get("cell_type")
        if cell_type == "markdown":
            source = _text(raw_cell.get("source"))
            if first_markdown:
                source = re.sub(r"^\s*#\s+[^\n]+\n?", "", source, count=1)
                first_markdown = False
            rendered.append(f'<section class="cell cell-markdown">{_render_markdown(source)}</section>')
        elif cell_type == "code":
            code_count += 1
            content, missing = _render_code_cell(raw_cell)
            missing_output_count += int(missing)
            rendered.append(content)
    return "\n".join(rendered), code_count, missing_output_count


def _source_revision(stage_root: Path) -> tuple[str, str]:
    manifest_path = stage_root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise SiteBuildError(f"missing exported source manifest: {manifest_path}")
    manifest = _read_json(manifest_path)
    revision = manifest.get("canonical_source_commit")
    if not isinstance(revision, str) or not revision.strip() or revision.strip() == "unknown":
        raise SiteBuildError("export manifest must disclose canonical_source_commit")
    return revision.strip(), _sha256(manifest_path)


def _notebook_paths(stage_root: Path) -> list[tuple[dict[str, str], Path]]:
    configured = {item["stem"]: item for item in LESSONS}
    manifest = _read_json(stage_root / MANIFEST_NAME)
    paths = manifest.get("files")
    if not isinstance(paths, list):
        raise SiteBuildError("export manifest must contain a files inventory")
    staged_files = {
        str(item.get("path"))
        for item in paths
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    selected: list[tuple[dict[str, str], Path]] = []
    for lesson in LESSONS:
        relative = Path("public/notebooks") / f"{lesson['stem']}.ipynb"
        path = stage_root / relative
        if not path.is_file():
            raise SiteBuildError(f"staging root is missing lesson notebook: {relative.as_posix()}")
        if staged_files and relative.as_posix() not in staged_files:
            raise SiteBuildError(f"lesson notebook is not recorded in export manifest: {relative.as_posix()}")
        selected.append((lesson, path))
    extras = sorted(
        path.name for path in (stage_root / "public/notebooks").glob("*.ipynb") if path.stem not in configured
    )
    if extras:
        raise SiteBuildError("unexpected notebook files in public stage: " + ", ".join(extras))
    return selected


def _urls(repository: str, relative: str) -> tuple[str, str]:
    encoded = "/".join(quote(part) for part in relative.split("/"))
    base = f"https://github.com/{repository}/blob/{PUBLIC_BRANCH}/{encoded}"
    colab = f"https://colab.research.google.com/github/{repository}/blob/{PUBLIC_BRANCH}/{encoded}"
    return base, colab


def _page_document(title: str, body: str, *, stylesheet: str, description: str) -> str:
    return f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{html.escape(description, quote=True)}">
  <title>{html.escape(title)} | CEPT Education</title>
  <link rel="stylesheet" href="{stylesheet}">
</head>
<body>
  <a class="skip-link" href="#content">Skip to content</a>
  {body}
</body>
</html>
'''


def _header(*, home_href: str, label: str | None = None) -> str:
    if label is None:
        navigation = f'<a href="{home_href}" aria-current="page">Learning index</a>'
    else:
        navigation = (
            f'<a href="{home_href}">Learning index</a>\n'
            f'      <span class="nav-current" aria-current="page">{html.escape(label)}</span>'
        )
    return f'''
<header class="site-header">
  <div class="shell header-inner">
    <a class="brand" href="{home_href}" aria-label="CEPT Education home">
      <span class="brand-mark" aria-hidden="true">C</span>
      <span><strong>CEPT</strong><small>POWER EDUCATION</small></span>
    </a>
    <nav aria-label="Primary navigation">
      {navigation}
    </nav>
  </div>
</header>
'''


def _lesson_card(
    lesson: dict[str, str],
    *,
    code_count: int,
    missing_count: int,
    repository: str,
) -> str:
    notebook_path = f"public/notebooks/{lesson['stem']}.ipynb"
    read_url, colab_url = _urls(repository, notebook_path)
    output_label = "Run in Colab to generate this result" if missing_count else "Example result included"
    return f'''
<article class="lesson-card">
  <div class="card-number">{html.escape(lesson["number"])}</div>
  <div class="card-content">
    <h4><a href="lessons/{html.escape(lesson["stem"])}.html">{html.escape(lesson["title_en"])}</a></h4>
    <p>{html.escape(lesson["summary_en"])}</p>
    <p class="lesson-outcome"><span>What you can inspect</span>{html.escape(lesson["outcome_en"])}</p>
    <div class="card-status"><span class="status-dot" aria-hidden="true"></span>{html.escape(output_label)} <span class="muted">· {code_count} code cells</span></div>
    <div class="card-links">
      <a href="lessons/{html.escape(lesson["stem"])}.html">View lesson</a>
      <a href="{html.escape(colab_url, quote=True)}">Run in Colab<span class="sr-only">: {html.escape(lesson["title_en"])}</span></a>
      <a href="{html.escape(read_url, quote=True)}">Notebook source<span class="sr-only">: {html.escape(lesson["title_en"])}</span></a>
    </div>
  </div>
</article>
'''


def _disclosure(_revision: str, _manifest_hash: str) -> str:
    return """
<aside class="disclosure" aria-label="Learning note">
  <strong>Reproduce the result</strong>
  <span>Run the notebook in Colab to execute the declared OpenDSS workflow and inspect the saved evidence.</span>
  <span>Use the tables and plots to check the result, then read the interpretation before changing an input.</span>
</aside>
"""


def _lesson_page(
    lesson: dict[str, str],
    notebook_path: str,
    notebook_html: str,
    code_count: int,
    missing_output_count: int,
    revision: str,
    manifest_hash: str,
    repository: str,
) -> str:
    read_url, colab_url = _urls(repository, notebook_path)
    status = (
        f"{missing_output_count} result cell(s) can be generated in Colab."
        if missing_output_count
        else "Solver-backed results are shown below."
    )
    lesson_label = f"Lesson {lesson['number']}"
    body = f'''
{_header(home_href="../index.html", label=lesson_label)}
<main id="content" class="shell lesson-page">
  <div class="lesson-kicker">LESSON {html.escape(lesson["number"])}</div>
  <div class="lesson-heading">
    <div>
      <h1>{html.escape(lesson["title_en"])}</h1>
    </div>
    <div class="lesson-actions" aria-label="Lesson links">
      <a class="button button-primary" href="{html.escape(colab_url, quote=True)}">Run in Colab</a>
      <a class="button button-secondary" href="{html.escape(read_url, quote=True)}">Read notebook source</a>
    </div>
  </div>
  <p class="lead">{html.escape(lesson["summary_en"])}</p>
  <div class="lesson-meta">
    <span>{code_count} code cell(s)</span>
    <span>{html.escape(status)}</span>
  </div>
  {_disclosure(revision, manifest_hash)}
  <section class="notebook" aria-labelledby="notebook-heading">
    <div class="section-heading">
      <div>
        <div class="eyebrow">NOTEBOOK RENDER</div>
        <h2 id="notebook-heading">Read the lesson</h2>
      </div>
      <p class="section-note">Read the lesson, then run it in Colab to generate solver-backed results.</p>
    </div>
    {notebook_html}
  </section>
</main>
<footer class="site-footer"><div class="shell"><span>CEPT Education</span><span>Workflow evidence is not project validation.</span></div></footer>
'''
    return _page_document(
        lesson["title_en"], body, stylesheet="../assets/education.css", description=lesson["summary_en"]
    )


def _index_page(
    lessons: Iterable[dict[str, str]],
    statuses: dict[str, tuple[int, int]],
    revision: str,
    manifest_hash: str,
    repository: str,
) -> str:
    lessons_by_stage: dict[str, list[dict[str, str]]] = {stage[0]: [] for stage in LESSON_STAGES}
    for lesson in lessons:
        lessons_by_stage[lesson["stage"]].append(lesson)

    course_stages: list[str] = []
    for stage_key, number, label, title, note in LESSON_STAGES:
        stage_lessons = lessons_by_stage[stage_key]
        cards = "".join(
            _lesson_card(
                lesson,
                code_count=statuses[lesson["stem"]][0],
                missing_count=statuses[lesson["stem"]][1],
                repository=repository,
            )
            for lesson in stage_lessons
        )
        heading_id = f"stage-{stage_key}"
        course_stages.append(f'''
<section class="course-stage" aria-labelledby="{heading_id}">
  <div class="course-stage-heading">
    <div class="eyebrow">{number} · {label}</div>
    <div>
      <h3 id="{heading_id}">{title}</h3>
      <p>{note}</p>
    </div>
  </div>
  <div class="lesson-grid" data-count="{len(stage_lessons)}">{cards}</div>
</section>
''')

    repository_url = f"https://github.com/{html.escape(repository, quote=True)}"
    body = f'''
{_header(home_href="index.html")}
<main id="content">
  <section class="hero shell">
    <div class="hero-copy">
      <div class="eyebrow">OPEN DSS · 7 SHORT LESSONS · RUNNABLE IN COLAB</div>
      <h1>Run a small study.<br><em>See where it came from.</em></h1>
      <p class="hero-lead">Build a load flow, explore an unbalanced feeder, test PV hosting capacity, and run a ground fault. Each lesson shows the inputs, the OpenDSS result, and the boundary of what it proves.</p>
      <div class="hero-actions" aria-label="Start learning">
        <a class="button button-primary" href="lessons/01_first_circuit_load_flow.html">Start with load flow</a>
        <a class="text-link" href="lessons/05_validation_reproducibility.html">See the evidence workflow <span aria-hidden="true">→</span></a>
      </div>
      <p class="hero-scope">Public demonstrator workflows. They do not establish project approval, field validation, or PowerFactory parity.</p>
    </div>
    <div class="hero-card" aria-label="The learning loop">
      <div class="hero-card-label">THE LEARNING LOOP</div>
      <ol>
        <li><span>01</span><strong>Read</strong><small>Start with the inputs and assumptions.</small></li>
        <li><span>02</span><strong>Run</strong><small>Execute the declared study in Colab.</small></li>
        <li><span>03</span><strong>Inspect</strong><small>Compare the result with its saved evidence.</small></li>
      </ol>
    </div>
  </section>
  <section class="principles shell" aria-labelledby="principles-heading">
    <div class="section-heading">
      <div><div class="eyebrow">A BOUNDED WAY TO LEARN</div><h2 id="principles-heading">See the method without losing the limits</h2></div>
      <p class="section-note">Concrete studies, explicit inputs, and claims that stop where the evidence stops.</p>
    </div>
    <div class="principle-grid">
      <article><span class="principle-index">01</span><h3>See the source of a result</h3><p>Numerical values stay tied to the declared OpenDSS run. Interpretation remains separate.</p></article>
      <article><span class="principle-index">02</span><h3>Start from clear inputs</h3><p>Topology, units, study type, and assumptions stay visible before execution.</p></article>
      <article><span class="principle-index">03</span><h3>Know what it proves</h3><p><code>WORKFLOW_VALIDATED</code> supports workflow checks, not project approval, field validation, or a protection decision.</p></article>
    </div>
  </section>
  <section id="lessons" class="lesson-section shell" aria-labelledby="lessons-heading">
    <div class="section-heading">
      <div><div class="eyebrow">THE COURSE</div><h2 id="lessons-heading">Seven short studies, one visible trail</h2></div>
      <p class="section-note">Choose a concrete question, inspect the example result, then run it in Colab.</p>
    </div>
    {"".join(course_stages)}
  </section>
  <section class="trust-band shell" aria-labelledby="trust-heading">
    <div>
      <div class="eyebrow">PUBLIC FACTS</div>
      <h2 id="trust-heading">A result should come with a trail.</h2>
      <p>Each lesson keeps declared inputs, solver output, interpretation, and verification in one reviewable path. Change one input and compare the next result.</p>
    </div>
    <dl class="proof-grid">
      <div><dt>7</dt><dd>short lessons</dd></div>
      <div><dt>OpenDSS</dt><dd>teaching runtime</dd></div>
      <div><dt>Python 3.10+</dt><dd>public runtime</dd></div>
      <div><dt>Workflow-level</dt><dd>verification checks</dd></div>
    </dl>
  </section>
  <section class="shell source-section" aria-labelledby="source-heading">
    <div class="eyebrow">NEXT STEP</div>
    <h2 id="source-heading">Choose where to begin</h2>
    <p>Not sure where to start? Begin with a two-bus load flow, then change one declared input and compare the result.</p>
    <div class="next-actions">
      <a class="button button-primary" href="lessons/01_first_circuit_load_flow.html">Start with lesson 01</a>
      <a class="text-link" href="{repository_url}">View the public source <span aria-hidden="true">↗</span></a>
    </div>
  </section>
</main>
<footer class="site-footer"><div class="shell">
  <span>CEPT Education · Apache-2.0</span>
  <div class="footer-links">
    <a href="{repository_url}">Source</a>
    <a href="{repository_url}/releases/tag/v0.2.0-edu.1">Release</a>
    <a href="{repository_url}/blob/main/LICENSE">License</a>
  </div>
</div></footer>
'''
    return _page_document(
        "Learning index",
        body,
        stylesheet="assets/education.css",
        description="Run seven short OpenDSS power-system studies in Colab and inspect the inputs, results, and verification behind each answer.",
    )


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
    for lesson, path in lessons:
        notebook = _read_json(path)
        notebook_html, code_count, missing_count = _render_notebook(notebook)
        statuses[lesson["stem"]] = (code_count, missing_count)
        relative = f"public/notebooks/{lesson['stem']}.ipynb"
        lesson_dir = output_dir / "lessons"
        lesson_dir.mkdir(exist_ok=True)
        page = _lesson_page(
            lesson,
            relative,
            notebook_html,
            code_count,
            missing_count,
            revision,
            manifest_hash,
            repository,
        )
        (lesson_dir / f"{lesson['stem']}.html").write_text(page, encoding="utf-8", newline="\n")

    index = _index_page((lesson for lesson, _ in lessons), statuses, revision, manifest_hash, repository)
    (output_dir / "index.html").write_text(index, encoding="utf-8", newline="\n")
    return {
        "schema": "cept-education-site-v1",
        "source_revision": revision,
        "export_manifest_sha256": manifest_hash,
        "repository": repository,
        "branch": PUBLIC_BRANCH,
        "lessons": [lesson["stem"] for lesson, _ in lessons],
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
