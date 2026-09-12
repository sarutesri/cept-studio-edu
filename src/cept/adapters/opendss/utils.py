"""OpenDSS adapter — utility functions and constants."""

from __future__ import annotations

import math
from importlib.resources import files
from pathlib import Path

_TESTSYSTEMS = Path(str(files("cept").joinpath("testsystems")))
BUILTIN = {
    "ieee13": _TESTSYSTEMS / "ieee13" / "IEEE13Nodeckt.dss",
    "ieee34": _TESTSYSTEMS / "ieee34" / "master.dss",
    "ieee123": _TESTSYSTEMS / "ieee123" / "master.dss",
    "cigre_lv": _TESTSYSTEMS / "cigre_lv" / "master_snapshot.dss",
    "kundur": _TESTSYSTEMS / "kundur" / "kundur_run.dss",
    "gic": _TESTSYSTEMS / "gic" / "GIC_Example.dss",
}

DER_KIND = {
    "pv": "pv",
    "wind": "wind",
    "hydro": "hydro",
    "storage": "battery",
    "generator": "syncgen",
    "syncgen": "syncgen",
    "indmach": "indmach",
}

LEN_UNITS = {0: "", 1: "mi", 2: "kft", 3: "km", 4: "m", 5: "ft", 6: "in", 7: "cm", 8: "mm"}


def dss_safe_label(value: object) -> str:
    """Return a label OpenDSS's command parser can carry unambiguously.

    Quoting protects spaces and colons, but not parentheses: inside an array
    such as ``buses=(...)`` OpenDSS still treats ``(`` and ``)`` as array
    delimiters even within quotes, so a PowerFactory terminal legitimately
    named ``Terminal(2)`` terminates the list early and the remaining tokens
    are parsed as the next property.  Substituting them keeps one stable name
    that every emitted command and the identity map agree on.
    """
    return str(value).replace("(", "_").replace(")", "_")


def dss_quote(value: object) -> str:
    """Quote a DSS string token so labels such as ``Bus 5`` stay intact."""
    return '"' + dss_safe_label(value).replace('"', '""') + '"'


def dss_object(class_name: str, name: object) -> str:
    """Return a quoted ``Class.Name`` reference accepted by DSS commands."""
    return dss_quote(f"{class_name}.{name}")


def transformer_core_options(transformer) -> str:
    """Return the OpenDSS core-branch options for one transformer.

    PowerFactory's ``curmg`` is the *total* no-load current I0; OpenDSS's
    ``%imag`` is only its reactive (magnetising) component, with the resistive
    part carried separately by ``%noloadloss``.  Passing I0 straight into
    ``%imag`` double-counts the core-loss current inside the magnetising branch.

    Both the live adapter and the exported portable package call this, so the
    package a reviewer runs by hand cannot drift from the run CEPT reported.
    """
    core_loss_pct = (
        100.0 * transformer.no_load_loss_kw / (transformer.mva * 1000.0)
        if transformer.no_load_loss_kw is not None
        else None
    )
    option = ""
    if transformer.no_load_current_pct is not None:
        i_total = float(transformer.no_load_current_pct)
        i_core = core_loss_pct if core_loss_pct is not None else 0.0
        option += f" %imag={math.sqrt(max(i_total * i_total - i_core * i_core, 0.0))}"
    if core_loss_pct is not None:
        option += f" %noloadloss={core_loss_pct}"
    return option


def inline_load_model_options(inline, load) -> tuple[int, str]:
    """Return one load-model policy for live and exported OpenDSS paths."""
    use_zip = load.model == "zip" and getattr(inline, "load_flow_voltage_dependency", None) is True
    zipv = (
        " ZIPV=(" + " ".join(f"{value:.12g}" for value in (load.zipv or [])) + ")"
        if use_zip and load.zipv
        else ""
    )
    return (8 if use_zip else 1), f" Vminpu=0.8 Vmaxpu=1.2{zipv}"


def aggregate_parallel_generator(generator) -> tuple[float, float]:
    """Fold PowerFactory's per-machine values into one OpenDSS generator."""
    units = max(int(getattr(generator, "parallel_units", 1) or 1), 1)
    return generator.kw * units, generator.mva * units
