"""Historical geometry import facade.

The implementation is owned by :mod:`cept.domain.sld.engineering_layout`.
This module preserves the established import path without maintaining a second
layout policy.
"""

from __future__ import annotations

from cept.domain.sld.engineering_layout import (
    ENGINEERING_SWITCHBOARD_PORT_SPACING,
    SLDTopologyIntent,
    bus_orientations,
    classify_inline_topology,
    hierarchical_layout,
    inline_layout,
    orient_finite_page_layout,
    sld_topology_layout,
)

__all__ = [
    "ENGINEERING_SWITCHBOARD_PORT_SPACING",
    "SLDTopologyIntent",
    "bus_orientations",
    "classify_inline_topology",
    "hierarchical_layout",
    "inline_layout",
    "orient_finite_page_layout",
    "sld_topology_layout",
]
