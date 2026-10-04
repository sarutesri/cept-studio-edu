"""Comparison harness: computed result vs published reference -> report."""

from __future__ import annotations

from typing import Optional

from cept.schema.result import LoadFlowResult, ValidationItem, ValidationReport


def validate_energy(
    result: LoadFlowResult,
    *,
    case_name: str,
    reference_name: str,
    source_kw_min: Optional[float] = None,
    source_kw_max: Optional[float] = None,
    loss_pct_max: Optional[float] = None,
    v_min_acceptable: Optional[float] = None,
    v_max_acceptable: Optional[float] = None,
    tol_pct: float = 2.0,
) -> ValidationReport:
    """Energy-conservation and range validation.

    Checks:
    1. Source power within expected range (kW).
    2. Loss fraction below maximum.
    3. All node voltages within acceptable window.
    Each check produces one ValidationItem.
    """
    items: list[ValidationItem] = []

    src = result.source_p_kw or 0.0
    loss = result.total_loss_kw or 0.0

    # source kW range
    if source_kw_min is not None and source_kw_max is not None:
        ref_mid = (source_kw_min + source_kw_max) / 2
        err = max(0.0, max(source_kw_min - src, src - source_kw_max))
        items.append(
            ValidationItem(
                label="source_kw",
                computed=round(src, 2),
                reference=round(ref_mid, 2),
                tol_abs=round((source_kw_max - source_kw_min) / 2, 2),
                error_abs=round(err, 2),
                error_rel_pct=round(err / ref_mid * 100, 3) if ref_mid else None,
                passed=(source_kw_min <= src <= source_kw_max),
            )
        )

    # loss fraction
    if loss_pct_max is not None and src > 0:
        loss_pct = loss / src * 100
        items.append(
            ValidationItem(
                label="loss_pct",
                computed=round(loss_pct, 3),
                reference=round(loss_pct_max / 2, 3),
                tol_abs=round(loss_pct_max / 2, 3),
                error_abs=round(max(0.0, loss_pct - loss_pct_max), 3),
                error_rel_pct=None,
                passed=(loss_pct <= loss_pct_max),
            )
        )

    # voltage range
    if v_min_acceptable is not None or v_max_acceptable is not None:
        vmin_lim = v_min_acceptable or 0.0
        vmax_lim = v_max_acceptable or 99.0
        violations = [bv for bv in result.bus_voltages if bv.v_pu < vmin_lim or bv.v_pu > vmax_lim]
        n_out = len(violations)
        items.append(
            ValidationItem(
                label="voltage_range",
                computed=float(n_out),
                reference=0.0,
                tol_abs=0.0,
                error_abs=float(n_out),
                error_rel_pct=None,
                passed=(n_out == 0),
            )
        )

    return ValidationReport(
        case_name=case_name,
        reference_name=reference_name,
        quantity="generic",
        items=items,
    )


def validate_voltages(
    result: LoadFlowResult,
    reference: dict[str, dict[int, float]],
    *,
    case_name: str,
    reference_name: str,
    tol_abs: float = 0.005,
) -> ValidationReport:
    """Compare per-unit bus voltages against a published reference table.

    ``tol_abs`` is in per-unit. A node passes when
    ``|computed - reference| <= tol_abs``. Nodes present in the reference but
    missing from the result are recorded as failures so silent gaps surface.
    """
    items: list[ValidationItem] = []
    for bus, phases in reference.items():
        for phase, v_ref in phases.items():
            v_comp = result.voltage(bus, phase)
            label = f"{bus.upper()}.{phase}"
            if v_comp is None:
                items.append(
                    ValidationItem(
                        label=label,
                        computed=float("nan"),
                        reference=v_ref,
                        tol_abs=tol_abs,
                        error_abs=float("inf"),
                        error_rel_pct=None,
                        passed=False,
                    )
                )
                continue
            err = abs(v_comp - v_ref)
            items.append(
                ValidationItem(
                    label=label,
                    computed=round(v_comp, 6),
                    reference=v_ref,
                    tol_abs=tol_abs,
                    error_abs=round(err, 6),
                    error_rel_pct=round(100.0 * err / v_ref, 4) if v_ref else None,
                    passed=err <= tol_abs,
                )
            )

    return ValidationReport(
        case_name=case_name,
        reference_name=reference_name,
        quantity="voltage_pu",
        items=items,
    )
