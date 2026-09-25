"""Canonical solver-independent SLD geometry and rendering contracts.

Vendor-native rendering stays in engine adapters; CEPT SVG/report rendering
stays in the reporting package.  The implementation modules live beside this
facade so the domain owns the contracts rather than re-exporting legacy roots.
"""

from .plan import *  # noqa: F401,F403
from .plan import __all__ as _PLAN_EXPORTS
from .geometry import *  # noqa: F401,F403
from .geometry import __all__ as _GEOMETRY_EXPORTS
from .geometry_receipt import *  # noqa: F401,F403
from .geometry_receipt import __all__ as _GEOMETRY_RECEIPT_EXPORTS
from .layout_contract import *  # noqa: F401,F403
from .layout_contract import __all__ as _LAYOUT_EXPORTS
from .legibility import *  # noqa: F401,F403
from .legibility import __all__ as _LEGIBILITY_EXPORTS
from .render_contract import *  # noqa: F401,F403
from .render_contract import __all__ as _RENDER_EXPORTS

__all__ = [
    *_PLAN_EXPORTS,
    *_GEOMETRY_EXPORTS,
    *_GEOMETRY_RECEIPT_EXPORTS,
    *_LAYOUT_EXPORTS,
    *_LEGIBILITY_EXPORTS,
    *_RENDER_EXPORTS,
]
