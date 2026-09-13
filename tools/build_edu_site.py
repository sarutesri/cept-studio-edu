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
        "title_en": "Environment and claim boundary",
        "title_th": "สภาพแวดล้อมและขอบเขตของข้ออ้าง",
        "summary_en": "Identify the installed solver, CEPT version, and what a workflow receipt can and cannot prove.",
        "summary_th": "ตรวจสอบ solver และรุ่นของ CEPT พร้อมแยกหลักฐานของ workflow ออกจากการรับรองโครงการ",
    },
    {
        "stem": "01_first_circuit_load_flow",
        "number": "01",
        "title_en": "First circuit and load flow",
        "title_th": "วงจรแรกและ load flow",
        "summary_en": "Build a small source-line-load circuit and compare solver-returned voltage from OpenDSS and CEPT.",
        "summary_th": "สร้างวงจร source-line-load ขนาดเล็ก แล้วเปรียบเทียบแรงดันจาก OpenDSS และ CEPT",
    },
    {
        "stem": "02_ieee13_unbalanced",
        "number": "02",
        "title_en": "IEEE 13-node unbalanced feeder",
        "title_th": "ระบบจำหน่ายไม่สมดุล IEEE 13-node",
        "summary_en": "Keep single- and two-phase laterals visible while comparing phase-specific voltage magnitudes.",
        "summary_th": "รักษาข้อมูลสายย่อยหนึ่งเฟสและสองเฟส แล้วเปรียบเทียบแรงดันแยกตามเฟส",
    },
    {
        "stem": "03_hosting_capacity",
        "number": "03",
        "title_en": "PV hosting capacity",
        "title_th": "ความสามารถรองรับ PV",
        "summary_en": "Treat hosting capacity as a declared criterion-bound search, not a universal number.",
        "summary_th": "เรียนรู้ว่า hosting capacity เป็นการค้นหาภายใต้เกณฑ์ที่ประกาศ ไม่ใช่ตัวเลขสากล",
    },
    {
        "stem": "04_fault_study",
        "number": "04",
        "title_en": "Single-line-to-ground fault",
        "title_th": "ความขัดข้องสายหนึ่งเฟสลงดิน",
        "summary_en": "Apply a declared fault and distinguish solver-returned current from a protection decision.",
        "summary_th": "ใส่ fault ตามสมมติฐาน และแยกกระแสจาก solver ออกจากการตัดสินใจด้าน protection",
    },
    {
        "stem": "05_validation_reproducibility",
        "number": "05",
        "title_en": "Validation receipt and reproducibility",
        "title_th": "ใบรับรองการตรวจสอบและการทำซ้ำ",
        "summary_en": "Follow Case fingerprints, solver identity, artifacts, and the boundary of workflow validation.",
        "summary_th": "ติดตาม Case fingerprint, solver, artifacts และขอบเขตของ WORKFLOW_VALIDATED",
    },
    {
        "stem": "06_colab_tui",
        "number": "06",
        "title_en": "Colab TUI feel: /cept CLI, then /cept agent",
        "title_th": "ฟีล TUI ใน Colab: /cept CLI แล้วต่อ /cept agent",
        "summary_en": "Tour every public CLI verb, then chat with the /cept agent using your own key.",
        "summary_th": "ทัวร์ CLI ครบทุก verb แล้วคุยกับ agent /cept ด้วย key ของตัวเอง",
    },
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
        return (
            '<pre class="output"><code>'
            f'{html.escape(_text(traceback))}'
            "</code></pre>"
        )

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
    if "text/markdown" in data:
        return f'<div class="output output-markdown">{_render_markdown(_text(data["text/markdown"]))}</div>'
    if "text/plain" in data:
        return f'<pre class="output"><code>{html.escape(_text(data["text/plain"]))}</code></pre>'
    if "text/html" in data:
        return (
            '<details class="output-source"><summary>HTML output source</summary>'
            f'<pre class="output"><code>{html.escape(_text(data["text/html"]))}</code></pre></details>'
        )
    return ""


def _render_code_cell(cell: dict[str, Any]) -> tuple[str, bool]:
    code = html.escape(_text(cell.get("source")), quote=False)
    outputs = cell.get("outputs")
    rendered_outputs: list[str] = []
    if isinstance(outputs, list):
        for output in outputs:
            if isinstance(output, dict):
                rendered = _render_output(output)
                if rendered:
                    rendered_outputs.append(rendered)
    if rendered_outputs:
        return (
            '<section class="cell cell-code">'
            '<div class="cell-label">Code</div>'
            f'<pre class="code"><code>{code}</code></pre>'
            + "".join(rendered_outputs)
            + "</section>",
            False,
        )
    if isinstance(outputs, list) and outputs:
        return (
            '<section class="cell cell-code">'
            '<div class="cell-label">Code</div>'
            f'<pre class="code"><code>{code}</code></pre>'
            '<p class="not-executed"><strong>Output present but not renderable here.</strong> '
            "The exported value is not replaced with a derived value."
            '<span lang="th">มี output ในไฟล์ export แต่ไม่สามารถแสดงรูปแบบนี้ได้ และจะไม่แทนที่ด้วยค่าที่คำนวณเอง</span></p>'
            "</section>",
            False,
        )
    return (
        '<section class="cell cell-code">'
        '<div class="cell-label">Code</div>'
        f'<pre class="code"><code>{code}</code></pre>'
        '<p class="not-executed"><strong>Not executed in this exported notebook.</strong> '
        "Run this lesson in Colab to produce solver output; no result is inferred here."
        '<span lang="th">ไฟล์ export นี้ยังไม่ได้รัน ให้เปิดใน Colab เพื่อสร้างผลจาก solver โดยไม่มีการเดาผลลัพธ์</span></p>'
        "</section>",
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
        relative = Path("public/notebooks") / f'{lesson["stem"]}.ipynb'
        path = stage_root / relative
        if not path.is_file():
            raise SiteBuildError(f"staging root is missing lesson notebook: {relative.as_posix()}")
        if staged_files and relative.as_posix() not in staged_files:
            raise SiteBuildError(f"lesson notebook is not recorded in export manifest: {relative.as_posix()}")
        selected.append((lesson, path))
    extras = sorted(
        path.name
        for path in (stage_root / "public/notebooks").glob("*.ipynb")
        if path.stem not in configured
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


def _header(*, home_href: str, label: str) -> str:
    return f'''
<header class="site-header">
  <div class="shell header-inner">
    <a class="brand" href="{home_href}" aria-label="CEPT Education home">
      <span class="brand-mark" aria-hidden="true">C</span>
      <span><strong>CEPT</strong><small>POWER EDUCATION</small></span>
    </a>
    <nav aria-label="Primary navigation">
      <a href="{home_href}">Learning index</a>
      <span class="nav-current" aria-current="page">{html.escape(label)}</span>
    </nav>
  </div>
</header>
'''


def _disclosure(revision: str, manifest_hash: str) -> str:
    return f'''
<aside class="disclosure" aria-label="Source disclosure">
  <strong>Source-bound export</strong>
  <span>Generated from revision <code>{html.escape(revision)}</code>.</span>
  <span>Export manifest SHA-256 <code>{manifest_hash}</code>.</span>
  <span>Notebook URLs below follow the public <code>main</code> branch; they are not a claim that this revision has been published.</span>
</aside>
'''


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
        f"{missing_output_count} code cell(s) have no exported output."
        if missing_output_count
        else "Exported notebook outputs are shown below."
    )
    body = f'''
{_header(home_href="../index.html", label=f'Lesson {lesson["number"]}')}
<main id="content" class="shell lesson-page">
  <div class="lesson-kicker">LESSON {html.escape(lesson["number"])}</div>
  <div class="lesson-heading">
    <div>
      <h1>{html.escape(lesson["title_en"])}</h1>
      <p class="thai-title" lang="th">{html.escape(lesson["title_th"])}</p>
    </div>
    <div class="lesson-actions" aria-label="Lesson links">
      <a class="button button-primary" href="{html.escape(colab_url, quote=True)}">Run in Colab<span lang="th">เปิดใน Colab</span></a>
      <a class="button button-secondary" href="{html.escape(read_url, quote=True)}">Read source<span lang="th">อ่าน notebook</span></a>
    </div>
  </div>
  <p class="lead">{html.escape(lesson["summary_en"])}</p>
  <p class="thai-copy" lang="th">{html.escape(lesson["summary_th"])}</p>
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
      <p class="section-note">Markdown, code, and only exported outputs are rendered. Missing outputs stay visible as missing.</p>
    </div>
    {notebook_html}
  </section>
</main>
<footer class="site-footer"><div class="shell"><span>CEPT Education</span><span>Workflow evidence is not project validation.</span></div></footer>
'''
    return _page_document(lesson["title_en"], body, stylesheet="../assets/education.css", description=lesson["summary_en"])


def _index_page(
    lessons: Iterable[dict[str, str]],
    statuses: dict[str, tuple[int, int]],
    revision: str,
    manifest_hash: str,
    repository: str,
) -> str:
    cards: list[str] = []
    for lesson in lessons:
        code_count, missing_count = statuses[lesson["stem"]]
        notebook_path = f'public/notebooks/{lesson["stem"]}.ipynb'
        read_url, colab_url = _urls(repository, notebook_path)
        output_label = (
            "Not executed in exported notebook"
            if missing_count
            else "Exported outputs included"
        )
        cards.append(f'''
<article class="lesson-card">
  <div class="card-number">{html.escape(lesson["number"])}</div>
  <div class="card-content">
    <h3><a href="lessons/{html.escape(lesson["stem"])}.html">{html.escape(lesson["title_en"])}</a></h3>
    <p class="thai-title" lang="th">{html.escape(lesson["title_th"])}</p>
    <p>{html.escape(lesson["summary_en"])}</p>
    <div class="card-status"><span class="status-dot" aria-hidden="true"></span>{html.escape(output_label)} <span class="muted">· {code_count} code cell(s)</span></div>
    <div class="card-links">
      <a href="lessons/{html.escape(lesson["stem"])}.html">Read lesson</a>
      <a href="{html.escape(colab_url, quote=True)}">Run in Colab<span class="sr-only">: {html.escape(lesson["title_en"])}</span></a>
      <a href="{html.escape(read_url, quote=True)}">Notebook source<span class="sr-only">: {html.escape(lesson["title_en"])}</span></a>
    </div>
  </div>
</article>
''')
    body = f'''
{_header(home_href="index.html", label="Learning index")}
<main id="content">
  <section class="hero shell">
    <div class="hero-copy">
      <div class="eyebrow">OPEN DSS · TYPED CASE · SIX LESSONS</div>
      <h1>Learn the study.<br><em>Keep the evidence.</em></h1>
      <p class="hero-lead">A source-readable, solver-visible introduction to CEPT for power-system learners.</p>
      <p class="thai-copy" lang="th">บทเรียนแบบเปิดที่พาเห็นทั้งโจทย์ การคำนวณ และขอบเขตของหลักฐาน โดยไม่สร้างผลลัพธ์ที่ยังไม่ได้รัน</p>
      <div class="hero-actions" aria-label="Start learning">
        <a class="button button-primary" href="#lessons">Start with the lessons<span lang="th">เริ่มเรียน</span></a>
        <a class="text-link" href="{html.escape(_urls(repository, "public/notebooks/00_environment.ipynb")[1], quote=True)}">Open lesson 00 in Colab <span aria-hidden="true">↗</span></a>
      </div>
    </div>
    <div class="hero-card" aria-label="The learning loop">
      <div class="hero-card-label">THE LEARNING LOOP</div>
      <ol>
        <li><span>01</span><strong>Read</strong><small>See the model and assumptions.</small></li>
        <li><span>02</span><strong>Run</strong><small>Execute the declared notebook path.</small></li>
        <li><span>03</span><strong>Inspect</strong><small>Keep solver output and verification separate.</small></li>
      </ol>
    </div>
  </section>
  <section class="principles shell" aria-labelledby="principles-heading">
    <div class="section-heading">
      <div><div class="eyebrow">A BOUNDED INTRODUCTION</div><h2 id="principles-heading">What this course teaches</h2></div>
      <p class="section-note" lang="th">หลักสำคัญของหลักสูตร</p>
    </div>
    <div class="principle-grid">
      <article><span class="principle-index">01</span><h3>Solver truth</h3><p>Numerical results come from the declared OpenDSS run. Derived explanations are not relabeled as solver output.</p></article>
      <article><span class="principle-index">02</span><h3>Typed setup</h3><p>A CEPT Case makes topology, units, study type, and assumptions visible before execution.</p></article>
      <article><span class="principle-index">03</span><h3>Honest scope</h3><p><code>WORKFLOW_VALIDATED</code> is not field evidence, project validation, or a protection decision.</p></article>
    </div>
  </section>
  <section id="lessons" class="lesson-section shell" aria-labelledby="lessons-heading">
    <div class="section-heading">
      <div><div class="eyebrow">THE COURSE</div><h2 id="lessons-heading">Seven small, source-bound steps</h2></div>
      <p class="section-note" lang="th">เลือกอ่านหรือเปิดใน Colab</p>
    </div>
    <div class="lesson-grid">{"".join(cards)}</div>
  </section>
  <section class="trust-band shell" aria-labelledby="trust-heading">
    <div><div class="eyebrow">READ THE BOUNDARY</div><h2 id="trust-heading">A result is only as strong as its evidence.</h2></div>
    <p>These lessons use public demonstrators and a bundled feeder. They show a reproducible workflow, not a decision about a real network. No agent replay, private input, or interactive control is implied by this static site.</p>
  </section>
  <section class="shell source-section" aria-labelledby="source-heading">
    <h2 id="source-heading">Export provenance</h2>
    {_disclosure(revision, manifest_hash)}
  </section>
</main>
<footer class="site-footer"><div class="shell"><span>CEPT Education</span><span lang="th">หลักฐานของ workflow ไม่ใช่การรับรองโครงการ</span></div></footer>
'''
    return _page_document(
        "Learning index",
        body,
        stylesheet="assets/education.css",
        description="Seven solver-visible CEPT power-system education lessons.",
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
        relative = f'public/notebooks/{lesson["stem"]}.ipynb'
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
        (lesson_dir / f'{lesson["stem"]}.html').write_text(page, encoding="utf-8", newline="\n")

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
