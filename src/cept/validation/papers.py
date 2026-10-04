"""Primary paper/benchmark cross-check registry.

The registry distinguishes a numeric cross-check already executed by this
repository from a paper that is only a future reference. A citation alone is
never reported as validation evidence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

CrosscheckStatus = Literal["passed", "reference_only", "blocked"]


@dataclass(frozen=True)
class PaperCrosscheck:
    id: str
    title: str
    authors: str
    year: int
    url: str
    doi: str | None
    benchmark: str
    status: CrosscheckStatus
    evidence: str
    notes: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


PAPER_CROSSCHECKS: tuple[PaperCrosscheck, ...] = (
    PaperCrosscheck(
        id="kersting-ieee13",
        title="Distribution Feeder Voltage Regulation Control",
        authors="William H. Kersting",
        year=2010,
        url="https://doi.org/10.1109/TIA.2010.2040060",
        doi="10.1109/TIA.2010.2040060",
        benchmark="IEEE 13-node test feeder",
        status="passed",
        evidence="tests/test_ieee13_validation.py::test_matches_published_solution",
        notes="OpenDSS solver voltages match the published IEEE feeder solution with max error < 0.002 pu.",
    ),
    PaperCrosscheck(
        id="noda-west10-emt",
        title="A study of electromagnetic transient simulations using IEEJ's West-10 benchmark power system model",
        authors="T. Noda et al.",
        year=2016,
        url="https://doi.org/10.1016/j.epsr.2016.03.028",
        doi="10.1016/j.epsr.2016.03.028",
        benchmark="IEEJ West-10 EMT benchmark",
        status="reference_only",
        evidence="",
        notes="Primary EMT benchmark reference found; West-10 data and paper curves are not imported yet, so no numeric pass is claimed.",
    ),
    PaperCrosscheck(
        id="sano-grid-connected-inverter-emt",
        title="An electromagnetic transient simulation model of grid-connected inverters for dynamic voltage analysis of distribution systems",
        authors="Kenichiro Sano",
        year=2019,
        url="https://doi.org/10.1002/eej.23179",
        doi="10.1002/eej.23179",
        benchmark="Grid-connected inverter EMT model",
        status="reference_only",
        evidence="",
        notes="Useful model-family reference for the next converter validation pack; no vendor/paper waveform has been loaded into CEPT yet.",
    ),
)


def paper_crosschecks() -> list[dict[str, object]]:
    """Return a JSON-ready immutable snapshot of the registry."""
    return [item.as_dict() for item in PAPER_CROSSCHECKS]


__all__ = ["PAPER_CROSSCHECKS", "PaperCrosscheck", "paper_crosschecks"]
