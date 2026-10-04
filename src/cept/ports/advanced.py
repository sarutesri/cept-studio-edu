"""The pure contract for capabilities that live in the CEPT Advance tier.

This module is a *port*: it names the contract and the policy, and nothing else,
so the dependency direction stays one-way. The implementation — the entry-point
lookup and the ``importlib`` call — lives on the adapter side at
:mod:`cept.adapters.advanced`, exactly as :mod:`cept.ports.probe` is implemented
by :mod:`cept.adapters.probe`.

The rule this file keeps is the one ``tests/test_ports_are_pure.py`` enforces:
``cept.ports`` imports nothing outside ``typing`` and its own modules. A resolver
that reached for ``importlib.metadata`` would put a module loader inside the
contract layer, which is the direction the whole package exists to prevent.
"""

from __future__ import annotations

from typing import Mapping, Protocol

__all__ = [
    "ENTRY_POINT_GROUP",
    "AdvanceCapabilityRegistry",
    "AdvanceUnavailable",
    "require_declaration",
    "target_module",
]

#: Entry-point group through which an installed Advance package is reached.
#: The Advance package is a separate top-level distribution; this group is how
#: core discovers an installed one.
ENTRY_POINT_GROUP = "cept.advanced"


class AdvanceUnavailable(ImportError):
    """A CEPT Advance capability was requested and the Advance tier is absent.

    Raised only when this environment genuinely cannot provide the capability.
    It is an :class:`ImportError` because that is what a caller that used to
    receive a missing licensed module would have caught; the message always
    names the capability and the tier, so the refusal is actionable.

    A declaration that is *present but broken* — a missing module, a missing
    attribute, a third-party import error — must **not** become this exception.
    That is a broken installation, and reporting it as a licensing problem is how
    a working paid feature silently disappears.
    """

    def __init__(self, name: str, *, detail: str | None = None) -> None:
        self.capability_name = name
        self.detail = detail
        message = (
            f"{name} is a CEPT Advance capability and the Advance tier is not "
            "installed in this environment."
        )
        if detail:
            message = f"{message} {detail}"
        super().__init__(message)


class AdvanceCapabilityRegistry(Protocol):
    """The declaration an installed Advance package publishes.

    ``declared()`` maps a public capability name to its ``module:attribute``
    target inside that package. A target with no ``:`` names a module.
    """

    def declared(self) -> Mapping[str, str]: ...


def require_declaration(registry: AdvanceCapabilityRegistry | None, name: str) -> str:
    """Return the declared target for ``name``, or fail closed.

    ``registry`` is ``None`` when no Advance tier is installed here, which is the
    public-wheel case. Both refusals raise :class:`AdvanceUnavailable` naming the
    capability: from core's point of view there is one fact to report — this
    environment cannot provide it, and here is which capability and which tier.
    """

    if not isinstance(name, str) or not name:
        raise ValueError("an Advance capability name must be a non-empty string")
    if registry is None:
        raise AdvanceUnavailable(name)
    declared = registry.declared()
    target = declared.get(name)
    if target is None:
        raise AdvanceUnavailable(
            name,
            detail=(
                f"The installed CEPT Advance package declares no capability named "
                f"{name!r}; it declares {sorted(declared)}."
            ),
        )
    return target


def target_module(target: str) -> str:
    """Return the module half of a ``module:attribute`` declaration.

    Core needs the dotted module name in exactly one place: launching a packaged
    lane worker as ``python -m <module>``. Reading it here keeps the shape of a
    declaration in one pure place while the ``importlib`` call stays on the
    adapter side.
    """

    return target.partition(":")[0]