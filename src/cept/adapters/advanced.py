"""Adapter-side implementation of the CEPT Advance capability port.

Core must never import :mod:`cept_advanced` — not at module scope, not by name.
What core is allowed to do is *ask* for a capability by its public name and get
one of two honest answers: the object, or a refusal that names the tier.

The one string core owns is the **entry-point group**, ``cept.advanced``, and
the policy lives in the pure port at :mod:`cept.ports.advanced`. The capabilities
themselves are declared by the Advance package in
:mod:`cept_advanced.registry`, next to the code they name, and validated there
when that package is imported. That is deliberate: the previous arrangement kept
a ``dict[str, str]`` of ``cept.adapters.pf.*`` module paths inside core's
adapter facade, where a renamed module left a stale entry that reported itself as
"PowerFactory is a CEPT Pro capability and is not installed" *inside a working
Advance installation* — a paid feature silently disappearing. Core now holds no
module path at all, so it cannot go stale.

Resolution order, one resolver, two acquisition strategies:

1. the installed distribution's ``cept.advanced`` entry point (a real
   ``pip install`` of ``cept-advance``); then
2. :mod:`cept_advanced.registry` directly, for a source checkout where ``src``
   is on ``sys.path`` but no distribution metadata exists.

Both strategies end at the same declaration and the same object. Only *"the
Advance package is not installed here"* is translated into
:class:`~cept.ports.advanced.AdvanceUnavailable`; a present-but-broken
declaration propagates unchanged.
"""

from __future__ import annotations

import importlib
import importlib.metadata
from typing import Any

from cept.ports.advanced import (
    AdvanceUnavailable,
    require_declaration,
    target_module,
)

__all__ = [
    "AdvanceUnavailable",
    "available",
    "capability",
    "declared_names",
    "module_name",
    "registry",
]

_REGISTRY_MODULE = "cept_advanced.registry"
_ADVANCE_ROOT = "cept_advanced"


def _entry_point_registry() -> Any | None:
    """Return the registry object published by an installed Advance package."""

    try:
        found = importlib.metadata.entry_points(group="cept.advanced")
    except TypeError:  # pragma: no cover - Python 3.9-style mapping API
        found = importlib.metadata.entry_points().get("cept.advanced", ())  # type: ignore[attr-defined,assignment]
    for entry in found:
        if entry.name == "registry":
            module_name, _, attribute = entry.value.partition(":")
            published = importlib.import_module(module_name)
            return getattr(published, attribute or "registry")
    return None


def _imported_registry() -> Any | None:
    """Return :mod:`cept_advanced.registry` when the package is importable."""

    try:
        return importlib.import_module(_REGISTRY_MODULE)
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        if missing == _ADVANCE_ROOT or missing.startswith(f"{_ADVANCE_ROOT}."):
            return None
        raise


def registry() -> Any | None:
    """Return the Advance capability registry, or ``None`` when not installed.

    ``None`` means exactly one thing: this environment has no Advance tier. Any
    other failure — a broken declaration inside an installed Advance package — is
    raised, never absorbed.
    """

    published = _entry_point_registry()
    if published is not None:
        return published
    return _imported_registry()


def available() -> bool:
    """Return whether the Advance tier is installed here."""

    try:
        return registry() is not None
    except Exception:
        return False


def declared_names() -> frozenset[str]:
    """Return the capability names the installed Advance package declares."""

    found = registry()
    if found is None:
        return frozenset()
    return frozenset(found.declared())


def capability(name: str) -> Any:
    """Return the named Advance capability, or fail closed.

    Import and attribute errors from a present-but-broken declaration propagate
    unchanged, so a mis-declared capability is reported as a defect rather than
    as a missing licence tier.
    """

    target = require_declaration(registry(), name)
    target_module_name, _, attribute = target.partition(":")
    module = importlib.import_module(target_module_name)
    if not attribute:
        return module
    return getattr(module, attribute)


def module_name(name: str) -> str:
    """Return the importable module an Advance capability is declared in.

    Core needs the dotted module name in exactly one place: launching a packaged
    lane worker as ``python -m <module>`` through
    :func:`cept.application.worker.run_packaged_worker`. The string comes from
    the Advance package's own declaration, so core still holds no module path and
    a moved Advance module cannot leave a stale literal behind.
    """

    return target_module(require_declaration(registry(), name))