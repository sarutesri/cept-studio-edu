"""Reporting layer — turn engine-agnostic results into shareable HTML.

Produces self-contained, English-language reports with an SLD-like network
diagram, interactive Plotly charts, result tables with standards checks, and
(for validation) a computed-vs-published comparison. Plotly.js is loaded from
CDN to keep file size small.
"""

from importlib import import_module

_RENDER_EXPORTS = frozenset(
    {
        "render_dynamics_report",
        "render_emt_report",
        "render_fault_report",
        "render_gic_report",
        "render_harmonics_report",
        "render_hosting_capacity_report",
        "render_ibr_report",
        "render_load_flow_report",
        "render_open_reference_comparison",
        "render_open_reference_report",
        "render_pfd_pdf_validation_report",
        "render_protection_report",
        "render_three_way_comparison_report",
        "render_time_series_report",
        "render_validation_report",
    }
)


def __getattr__(name: str):
    if name not in _RENDER_EXPORTS:
        raise AttributeError(name)
    render_module = import_module(f"{__name__}.render")
    runtime_module = import_module(f"{__name__}.sld_runtime")
    setattr(render_module, "build_sld_option", runtime_module.build_sld_option_v2)
    value = getattr(render_module, name)
    globals()[name] = value
    return value

__all__ = [
    "render_load_flow_report",
    "render_validation_report",
    "render_pfd_pdf_validation_report",
    "render_open_reference_report",
    "render_open_reference_comparison",
    "render_three_way_comparison_report",
    "render_fault_report",
    "render_hosting_capacity_report",
    "render_ibr_report",
    "render_dynamics_report",
    "render_emt_report",
    "render_gic_report",
    "render_time_series_report",
    "render_harmonics_report",
    "render_protection_report",
]

# export_pdf/export_docx (cept.reporting.export) depend on optional extras
# (playwright/python-docx/matplotlib, `pip install -e ".[export]"`), so
# they're intentionally not imported here — import from
# cept.reporting.export directly to keep the base install lightweight.
