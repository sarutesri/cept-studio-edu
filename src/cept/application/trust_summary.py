"""Human-readable trust summary for solver-backed CEPT reports.

This is a presentation layer over the existing engine-passport capability data.
It deliberately creates no new evidence or claim authority.
"""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from cept.application.artifacts import _ReportEdits, _insert_before_closing_body


def inject_trust_summary(
    report_path: Path,
    capability: dict[str, Any],
    *,
    run_claim: str,
    edits: _ReportEdits | None = None,
) -> None:
    """Add one visible current-vs-maximum claim summary to ``report.html``."""
    current = str(capability.get("current_claim") or capability.get("research_status") or "unknown")
    maximum = str(
        capability.get("maximum_claim_if_external_evidence_satisfied")
        or capability.get("claim_cap")
        or "demonstrator"
    )
    missing = list(capability.get("missing_evidence_for_higher_claim") or [])
    benchmark = capability.get("research_benchmark")

    missing_html = ""
    if missing:
        items = "".join(f"<li>{escape(str(item))}</li>" for item in missing)
        missing_html = (
            "<p><strong>Missing evidence for the higher claim:</strong></p>"
            f"<ul>{items}</ul>"
        )
    elif maximum == "demonstrator":
        missing_html = (
            "<p>This engine/study lane is currently bounded to demonstrator capability; "
            "no higher project-validation claim is implied.</p>"
        )

    benchmark_html = (
        f"<p><strong>Qualification basis:</strong> {escape(str(benchmark))}</p>" if benchmark else ""
    )
    section = (
        '<section class="trust-summary" aria-label="Trust summary">'
        "<h2>Trust summary</h2>"
        "<div class=\"grid\">"
        '<div class="card"><div class="k">Run claim</div>'
        f'<div class="v">{escape(run_claim)}</div></div>'
        '<div class="card"><div class="k">Current engine capability</div>'
        f'<div class="v">{escape(current)}</div></div>'
        '<div class="card"><div class="k">Maximum claim if evidence is satisfied</div>'
        f'<div class="v">{escape(maximum)}</div></div>'
        "</div>"
        f"{benchmark_html}{missing_html}"
        "<p class=\"note\">The maximum claim is a ceiling, not the current result status. "
        "Run artifacts, independent evidence, project-specific criteria, and human review remain authoritative.</p>"
        "</section>"
    )

    target = edits or _ReportEdits(report_path)
    target.set(_insert_before_closing_body(target.html(), section))
    if edits is None:
        target.flush()


__all__ = ["inject_trust_summary"]
