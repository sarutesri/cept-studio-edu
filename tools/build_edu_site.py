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
        "stage": "prologue",
        "title_en": "Environment and study scope",
        "summary_en": "Check the runtime and the limits of workflow evidence.",
        "outcome_en": "Runtime identity and claim scope",
    },
    {
        "stem": "01_why_solvers_lie",
        "number": "01",
        "stage": "aha",
        "title_en": "Convergence and model integrity",
        "summary_en": "Compare two voltage-base paths in the same feeder example.",
        "outcome_en": "Recorded example: 0.316 pu versus 0.948 pu",
    },
    {
        "stem": "02_first_circuit_sld",
        "number": "02",
        "stage": "build",
        "title_en": "Typed network modelling",
        "summary_en": "Define the IEEE 4-node feeder and inspect its single-line diagram.",
        "outcome_en": "Example: 0.9477 pu at Node 4 with step-down transformer",
    },
    {
        "stem": "03_unbalanced_feeder",
        "number": "03",
        "stage": "real",
        "title_en": "Unbalanced feeder analysis",
        "summary_en": "Inspect each phase of the IEEE 13-node feeder.",
        "outcome_en": "Phase A, B, and C voltage profiles",
    },
    {
        "stem": "04_incomplete_data",
        "number": "04",
        "stage": "real",
        "title_en": "Incomplete engineering data",
        "summary_en": "Separate known inputs, missing data, and approved assumptions.",
        "outcome_en": "Input status and resolution policies",
    },
    {
        "stem": "05_solar_hosting_capacity",
        "number": "05",
        "stage": "active",
        "title_en": "Solar hosting capacity",
        "summary_en": "Sweep PV against a declared voltage limit.",
        "outcome_en": "A capacity bracket under one explicit criterion",
    },
    {
        "stem": "06_fault_study",
        "number": "06",
        "stage": "active",
        "title_en": "Short-circuit analysis",
        "summary_en": "Inspect the current from a declared line-to-ground fault.",
        "outcome_en": "Fault current for the declared study—not a protection decision",
    },
    {
        "stem": "07_digital_evidence",
        "number": "07",
        "stage": "professional",
        "title_en": "Run evidence and reproducibility",
        "summary_en": "Follow the Case fingerprint, saved artifacts, and verification checks.",
        "outcome_en": "Run identity and artifact integrity",
    },
)
LESSON_STAGES: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "prologue",
        "00",
        "ENVIRONMENT & SCOPE",
        "Prepare the runtime",
        "Confirm the engine and claim scope.",
    ),
    (
        "aha",
        "01",
        "MODEL INTEGRITY",
        "Inspect model assumptions",
        "Distinguish convergence from correctness.",
    ),
    (
        "build",
        "02",
        "MODEL CONSTRUCTION",
        "Build a typed network",
        "Define a Case and inspect its SLD.",
    ),
    (
        "real",
        "03",
        "NETWORK CONDITIONS",
        "Evaluate phases and input quality",
        "Inspect unbalance and missing inputs.",
    ),
    (
        "active",
        "04",
        "APPLIED STUDIES",
        "Study solar integration and faults",
        "Use declared study criteria.",
    ),
    (
        "professional",
        "05",
        "EVIDENCE & REPRODUCIBILITY",
        "Review the run evidence",
        "Inspect identity and artifact integrity.",
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
    """Render the small Markdown vocabulary used by the configured lessons."""

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
        '<section class="cell cell-code"><div class="cell-label">Code</div>' + code_block + "</section>"
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
  <meta http-equiv="Cache-Control" content="no-store, no-cache, must-revalidate, max-age=0">
  <meta http-equiv="Pragma" content="no-cache">
  <meta http-equiv="Expires" content="0">
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
        navigation = (
            f'<a href="{home_href}" aria-current="page">Learning index</a>'
            '<a href="#why-cept">Why CEPT</a><a href="#lessons">Explore lessons</a>'
        )
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
    missing_count: int,
    repository: str,
) -> str:
    notebook_path = f"public/notebooks/{lesson['stem']}.ipynb"
    read_url, colab_url = _urls(repository, notebook_path)
    output_label = "Run to generate" if missing_count else "Result included"
    return f'''
<article class="lesson-card">
  <div class="card-number">{html.escape(lesson["number"])}</div>
  <div class="card-content">
    <h4><a href="lessons/{html.escape(lesson["stem"])}.html">{html.escape(lesson["title_en"])}</a></h4>
    <p class="lesson-outcome">{html.escape(lesson["outcome_en"])}</p>
    <div class="card-status"><span class="status-dot" aria-hidden="true"></span>{html.escape(output_label)}</div>
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
  <strong>Read → Run → Inspect</strong>
  <span>Open Colab, run the cells, then review the saved results. Demonstrator evidence is not project validation.</span>
</aside>
"""


def _lesson_navigation(current_stem: str) -> tuple[str, str]:
    lessons = list(LESSONS)
    current_index = next(index for index, item in enumerate(lessons) if item["stem"] == current_stem)
    stage_by_key = {stage[0]: stage[2].title() for stage in LESSON_STAGES}
    toc_items: list[str] = []
    for item in lessons:
        is_current = item["stem"] == current_stem
        current_class = " is-current" if is_current else ""
        current_attr = ' aria-current="page"' if is_current else ""
        stem = html.escape(item["stem"], quote=True)
        number = html.escape(item["number"])
        title = html.escape(item["title_en"])
        stage = html.escape(stage_by_key[item["stage"]])
        toc_items.append(
            f'<li><a class="lesson-toc-link{current_class}" href="{stem}.html"{current_attr}>'
            f'<span class="lesson-toc-number">{number}</span>'
            f"<span><strong>{title}</strong><small>{stage}</small></span></a></li>"
        )

    pagination_items: list[str] = []
    prev_item = lessons[current_index - 1] if current_index else None
    next_item = lessons[current_index + 1] if current_index < len(lessons) - 1 else None

    if prev_item is not None:
        stem = html.escape(prev_item["stem"], quote=True)
        title = html.escape(prev_item["title_en"])
        pagination_items.append(
            f'<a class="lesson-page-link lesson-page-link--prev" rel="prev" href="{stem}.html">'
            f"<small>&larr; Previous lesson</small><strong>{title}</strong></a>"
        )
    elif next_item is not None:
        pagination_items.append('<div class="lesson-page-link-spacer" aria-hidden="true"></div>')

    if next_item is not None:
        stem = html.escape(next_item["stem"], quote=True)
        title = html.escape(next_item["title_en"])
        pagination_items.append(
            f'<a class="lesson-page-link lesson-page-link--next" rel="next" href="{stem}.html">'
            f"<small>Next lesson &rarr;</small><strong>{title}</strong></a>"
        )

    sidebar_html = f"""
<div class="lesson-sidebar">
  <details class="lesson-nav-disclosure" open>
    <summary><span>Lesson contents</span><small>{len(lessons)} lessons</small></summary>
    <nav class="lesson-toc" aria-labelledby="lesson-toc-heading">
      <div class="lesson-toc-heading">
        <div><div class="eyebrow">THE COURSE</div><h2 id="lesson-toc-heading">All lessons</h2></div>
        <a href="../index.html">Back to index</a>
      </div>
      <p class="lesson-toc-note">Follow the sequence or jump to the question you want to explore.</p>
      <ol class="lesson-toc-list">{"".join(toc_items)}</ol>
    </nav>
  </details>
  <script>
    if (window.matchMedia("(max-width: 820px)").matches) {{
      document.querySelector(".lesson-nav-disclosure").open = false;
    }}
  </script>
 </div>
"""
    pagination_html = f'<nav class="lesson-pagination" aria-label="Lesson pagination">{"".join(pagination_items)}</nav>'
    return sidebar_html, pagination_html


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
    lesson_sidebar, lesson_pagination = _lesson_navigation(lesson["stem"])
    body = f'''
{_header(home_href="../index.html", label=lesson_label)}
<main id="content" class="shell lesson-page">
  <div class="lesson-layout">
    {lesson_sidebar}
    <div class="lesson-main">
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
      {lesson_pagination}
    </div>
  </div>
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
                missing_count=statuses[lesson["stem"]][1],
                repository=repository,
            )
            for lesson in stage_lessons
        )
        heading_id = f"stage-{stage_key}"
        course_stages.append(f'''
<section class="course-stage" aria-labelledby="{heading_id}">
  <div class="course-stage-heading">
    <div class="eyebrow">{html.escape(number)} · {html.escape(label)}</div>
    <div>
      <h3 id="{heading_id}">{title}</h3>
    </div>
  </div>
  <div class="lesson-grid" data-count="{len(stage_lessons)}">{cards}</div>
</section>
''')

    repository_url = f"https://github.com/{html.escape(repository, quote=True)}"
    body = f'''
{_header(home_href="index.html")}
<main id="content" class="edu-home">
  <section class="hero shell" aria-labelledby="hero-heading">
    <div class="hero-copy">
      <div class="eyebrow">CEPT POWER STUDIO / EDUCATION</div>
      <h1 id="hero-heading">Power-system studies.<br><em>Clear inputs.<br>Traceable results.</em></h1>
      <p class="hero-lead">Model the network. Run OpenDSS. Inspect diagrams, results, and evidence in one CEPT workflow.</p>
      <div class="hero-actions" aria-label="Start learning">
        <a class="button button-primary" href="lessons/01_why_solvers_lie.html">Discover the difference <span aria-hidden="true">→</span></a>
        <a class="button button-secondary" href="#lessons">Explore the course</a>
      </div>
      <ul class="hero-facts" aria-label="Course at a glance">
        <li>7 lessons + setup</li><li>OpenDSS-backed</li><li>Colab notebooks</li>
      </ul>
    </div>
    <figure class="study-preview">
      <div class="preview-heading"><span class="preview-dot" aria-hidden="true"></span>FROM MODEL TO EVIDENCE <span class="preview-tag">Conceptual view</span></div>
      <svg class="network-diagram" viewBox="0 0 520 260" role="img" aria-labelledby="network-title network-desc">
        <title id="network-title">A network model made visible</title>
        <desc id="network-desc">An illustrative single-line diagram connects a source through a transformer to two load branches and a photovoltaic branch. This is a concept illustration, not a simulated lesson circuit.</desc>
        <defs><pattern id="network-grid" width="20" height="20" patternUnits="userSpaceOnUse"><path d="M 20 0 L 0 0 0 20" fill="none" stroke="#dce8ee" stroke-width=".6"/></pattern></defs>
        <rect width="520" height="260" rx="10" fill="url(#network-grid)"/>
        <g fill="none" stroke="#23445a" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="55" cy="110" r="21"/><path d="M42 110 Q48 95 55 110 T68 110 M76 110 H139 M175 110 H240 M240 76 V148 M240 110 H353 M353 76 V148 M353 110 H463 V175 M240 148 V175 M353 76 V43"/>
          <circle cx="149" cy="110" r="17"/><circle cx="170" cy="110" r="17"/>
          <path d="M228 175 H252 L240 196 Z M451 175 H475 L463 196 Z"/>
          <rect x="336" y="19" width="34" height="24" rx="3"/><path d="M341 31 H365 M347 23 V39 M358 23 V39"/>
        </g>
        <g fill="#067d77"><circle cx="240" cy="110" r="5"/><circle cx="353" cy="110" r="5"/></g>
        <g fill="#526777" font-family="Segoe UI, Arial, sans-serif" font-size="12" text-anchor="middle">
          <text x="55" y="155">Source</text><text x="160" y="155">Transformer</text><text x="240" y="220">Load</text><text x="463" y="220">Load</text><text x="398" y="36">PV</text>
          <text x="240" y="63">Bus A</text><text x="353" y="172">Bus B</text>
        </g>
      </svg>
      <div class="preview-artifacts">
        <div><span>01 / DEFINE</span><strong>Typed Case</strong><small>Topology · units · assumptions</small></div>
        <div><span>02 / INSPECT</span><strong>Solver results</strong><small>Voltages · currents · plots</small></div>
        <div><span>03 / REVIEW</span><strong>Run evidence</strong><small>Identity · artifacts · checks</small></div>
      </div>
      <figcaption>Illustrative network—not a simulated lesson circuit.</figcaption>
    </figure>
  </section>
  <section id="why-cept" class="principles shell" aria-labelledby="principles-heading">
    <div class="section-heading">
      <div><div class="eyebrow">WHY CEPT</div><h2 id="principles-heading">Four practical advantages.</h2></div>
      <p class="section-note">OpenDSS solves the model. CEPT makes the study easier to inspect.</p>
    </div>
    <div class="benefit-grid">
      <article><span class="principle-index">01</span><h3>Explicit inputs</h3><p>Keep topology, units, and input decisions in one typed Case.</p><a href="lessons/04_incomplete_data.html">Input policies <span aria-hidden="true">→</span></a></article>
      <article><span class="principle-index">02</span><h3>Visible networks</h3><p>Inspect SLDs and phase results without manually placing every bus.</p><a href="lessons/02_first_circuit_sld.html">Network modelling <span aria-hidden="true">→</span></a></article>
      <article><span class="principle-index">03</span><h3>Traceable studies</h3><p>Retain the Case, run artifacts, and checks behind each result.</p><a href="lessons/07_digital_evidence.html">Run evidence <span aria-hidden="true">→</span></a></article>
      <article><span class="principle-index">04</span><h3>Applied learning</h3><p>Explore unbalance, solar integration, and faults through bounded examples.</p><a href="#lessons">Explore studies <span aria-hidden="true">→</span></a></article>
    </div>
  </section>
  <section class="workflow-section" aria-labelledby="workflow-heading">
    <div class="shell">
      <div class="section-heading">
        <div><div class="eyebrow">STUDY WORKFLOW</div><h2 id="workflow-heading">One Case. Five clear steps.</h2></div>
        <p class="section-note">No AI API key required.</p>
      </div>
      <figure class="workflow-figure">
        <ol class="workflow-diagram" aria-label="CEPT study workflow">
          <li><span class="workflow-step">01</span><h3>Define</h3><p>Network &amp; study inputs</p></li>
          <li><span class="workflow-step">02</span><h3>Check</h3><p>Readiness &amp; missing data</p></li>
          <li class="solver-step"><span class="workflow-step">03</span><h3>Solve</h3><p>OpenDSS execution</p></li>
          <li><span class="workflow-step">04</span><h3>Inspect</h3><p>SLD &amp; phase results</p></li>
          <li><span class="workflow-step">05</span><h3>Verify</h3><p>Run identity &amp; integrity</p></li>
        </ol>
        <figcaption>CEPT structures the study; OpenDSS supplies the numerical solution. Engineering judgement remains yours.</figcaption>
      </figure>
    </div>
  </section>
  <section class="comparison-band shell" aria-labelledby="comparison-heading">
    <div class="section-heading">
      <div><div class="eyebrow">MODEL INTEGRITY</div><h2 id="comparison-heading">Converged ≠ correct.</h2></div>
      <p class="section-note">An omitted voltage base can mislead the per-unit readout.</p>
    </div>
    <div class="discovery-grid">
      <article class="discovery-copy"><h3>One feeder.<br>Two voltage-base paths.</h3><p>The direct example omits the downstream base. CEPT carries the declared bus voltage into the adapter.</p><a class="text-link" href="lessons/01_why_solvers_lie.html">Inspect lesson 01 <span aria-hidden="true">→</span></a></article>
      <figure class="result-comparison">
        <div class="result-row result-row--warning"><div><span>DIRECT SCRIPT / OMITTED BASE</span><strong>0.316 <small>pu</small></strong></div><p>Converged, but misleading per-unit readout.</p></div>
        <div class="result-row"><div><span>CEPT / DECLARED BASE</span><strong>0.948 <small>pu</small></strong></div><p>Readout tied to the declared voltage base.</p></div>
        <figcaption>Rounded Node 4 outputs from Lesson 01. Demonstrator values—not field measurements.</figcaption>
      </figure>
    </div>
  </section>
  <section id="lessons" class="lesson-section shell" aria-labelledby="lessons-heading">
    <div class="section-heading">
      <div><div class="eyebrow">COURSE</div><h2 id="lessons-heading">Choose your next study.</h2></div>
      <p class="section-note">Seven lessons + setup. Read, run, and inspect.</p>
    </div>
    <div class="course-entry"><p><strong>New to CEPT?</strong> Start with runtime setup.</p><a href="lessons/00_environment.html">Start here <span aria-hidden="true">→</span></a></div>
    {"".join(course_stages)}
  </section>
  <section class="trust-band shell" aria-labelledby="trust-heading">
    <div>
      <div class="eyebrow">EVIDENCE LIMITS</div>
      <h2 id="trust-heading">Traceable.<br>Not project validated.</h2>
      <p>Verification checks run identity and artifact integrity—not physical correctness.</p>
    </div>
    <div class="claim-card"><span class="claim-label">COURSE CLAIM CEILING</span><strong><code>WORKFLOW_VALIDATED</code></strong><p>Workflow checks for the declared examples.</p><details class="claim-details"><summary>What this does not establish</summary><p>Field validation, project approval, protection acceptance, or PowerFactory agreement. Each needs separate evidence and review. Hashes detect artifact changes; they do not authenticate a solver.</p></details></div>
  </section>
  <section class="shell source-section" aria-labelledby="source-heading">
    <div><div class="eyebrow">GET STARTED</div><h2 id="source-heading">Build. Inspect. Understand.</h2></div>
    <div class="next-actions">
      <a class="button button-primary" href="lessons/01_why_solvers_lie.html">Start lesson 01 <span aria-hidden="true">→</span></a>
      <a class="button button-secondary" href="lessons/02_first_circuit_sld.html">Build the first circuit</a>
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
        description="Learn power-system studies with CEPT: explicit models, OpenDSS simulation, rendered diagrams, and traceable run evidence. Seven lessons plus setup.",
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
