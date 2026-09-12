"""Opt-in polar Newton-Raphson solve over an OpenDSS ``SystemY`` snapshot.

This is deliberately separate from OpenDSS's native power-flow path.  The
engine compiles the network and supplies the admittance matrix; CEPT solves the
constant-power operating point over that frozen matrix.  The lane is limited
to balanced inline Cases so a missing phase or a hidden engine control cannot
silently become a different mathematical problem.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Iterable

from cept.schema.case import Case, InlineNetwork
from cept.schema.result import BranchFlow, BusVoltage, DEROutput, LoadFlowResult


class YbusPowerFlowError(RuntimeError):
    """The extracted matrix or the requested representation is not usable."""


class YbusSingularJacobianError(YbusPowerFlowError):
    """The Newton Jacobian is singular at the current state.

    A distinct type (rather than message inspection) lets the caller retry a
    degenerate initialisation without depending on error wording.
    """


@dataclass(frozen=True)
class YbusSnapshot:
    """A hashable description of the matrix and the engine ordering it uses."""

    node_order: tuple[str, ...]
    identities: tuple[tuple[str, int], ...]
    matrix: tuple[tuple[complex, ...], ...]
    matrix_sha256: str
    node_order_sha256: str
    passive_solve_converged: bool
    disabled_elements: tuple[str, ...]
    element_yprims: tuple["_ElementYPrim", ...]


@dataclass(frozen=True)
class _ElementYPrim:
    class_name: str
    name: str
    node_refs: tuple[int, ...]
    matrix: tuple[tuple[complex, ...], ...]


@dataclass(frozen=True)
class _LoadCoefficients:
    p0_mw: float
    q0_mvar: float


@dataclass(frozen=True)
class NewtonSolution:
    """One polar Newton-Raphson outcome from a single initial state."""

    converged: bool
    iterations: int
    history: tuple[dict[str, Any], ...]
    magnitudes: tuple[float, ...]
    angles: tuple[float, ...]
    failure: YbusPowerFlowError | None = None


def _json_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _bus_key(name: str) -> str:
    return str(name).strip().casefold()


def map_ybus_nodes(node_order: Iterable[str], inline: InlineNetwork) -> tuple[tuple[str, int], ...]:
    """Map OpenDSS ``YNodeOrder`` entries to Case bus/phase identities.

    OpenDSS ordering is an engine detail and is not stable enough to infer by
    position.  Every node must therefore be a one-to-one match for a declared
    balanced Case bus.  In particular, temporary ``__open_terminal`` buses and
    ground/neutral nodes are refused rather than silently discarded.
    """
    buses = {_bus_key(bus.name): bus for bus in inline.buses}
    expected = {(_bus_key(bus.name), phase) for bus in inline.buses for phase in range(1, bus.phases + 1)}
    identities: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for raw in node_order:
        token = str(raw).strip()
        if "." not in token:
            raise YbusPowerFlowError(f"Ybus node '{token}' has no phase suffix; expected '<bus>.<phase>'.")
        raw_bus, raw_phase = token.rsplit(".", 1)
        try:
            phase = int(raw_phase)
        except ValueError as exc:
            raise YbusPowerFlowError(f"Ybus node '{token}' has a non-numeric phase suffix.") from exc
        key = (_bus_key(raw_bus), phase)
        if key not in expected or key[0] not in buses:
            raise YbusPowerFlowError(
                f"Ybus node '{token}' is not a declared Case bus/phase; "
                "the engine-to-Case mapping is incomplete."
            )
        if key in seen:
            raise YbusPowerFlowError(f"Ybus node '{token}' maps to a duplicate Case phase.")
        seen.add(key)
        identities.append((buses[key[0]].name, phase))

    missing = sorted(expected - seen)
    if missing:
        missing_text = ", ".join(f"{bus}.{phase}" for bus, phase in missing)
        raise YbusPowerFlowError(f"Ybus is missing declared Case phase(s): {missing_text}.")
    if len(identities) != len(expected):
        raise YbusPowerFlowError(
            f"Ybus has {len(identities)} nodes but the balanced Case declares {len(expected)} phases."
        )
    return tuple(identities)


def _matrix_from_system_y(values: Iterable[float], n: int) -> tuple[tuple[complex, ...], ...]:
    raw = list(values or [])
    expected = 2 * n * n
    if len(raw) != expected:
        raise YbusPowerFlowError(
            f"OpenDSS SystemY returned {len(raw)} doubles for {n} nodes; expected {expected}."
        )
    matrix: list[tuple[complex, ...]] = []
    for row in range(n):
        values_row: list[complex] = []
        for col in range(n):
            real = float(raw[2 * (row * n + col)])
            imag = float(raw[2 * (row * n + col) + 1])
            if not math.isfinite(real) or not math.isfinite(imag):
                raise YbusPowerFlowError(f"OpenDSS SystemY contains a non-finite value at ({row}, {col}).")
            values_row.append(complex(real, imag))
        matrix.append(tuple(values_row))
    return tuple(matrix)


def _positive_sequence_projection(
    snapshot: YbusSnapshot, inline: InlineNetwork
) -> tuple[tuple[complex, ...], ...]:
    """Project the engine phase matrix onto its positive-sequence subspace."""
    phase = complex(-0.5, math.sqrt(3.0) / 2.0)
    transform = (
        (1.0 + 0j, 1.0 + 0j, 1.0 + 0j),
        (1.0 + 0j, phase.conjugate(), phase),
        (1.0 + 0j, phase, phase.conjugate()),
    )
    inverse = tuple(tuple(value.conjugate() / 3.0 for value in row) for row in transform)
    node_indices = {
        (_bus_key(bus), phase_number): index for index, (bus, phase_number) in enumerate(snapshot.identities)
    }
    bus_keys = [_bus_key(bus.name) for bus in inline.buses]
    projected = [[0j for _ in snapshot.identities] for _ in snapshot.identities]
    for row_key in bus_keys:
        for col_key in bus_keys:
            block = [
                [
                    snapshot.matrix[node_indices[(row_key, row_phase)]][node_indices[(col_key, col_phase)]]
                    for col_phase in (1, 2, 3)
                ]
                for row_phase in (1, 2, 3)
            ]
            positive = sum(
                inverse[1][row_phase] * block[row_phase][col_phase] * transform[col_phase][1]
                for row_phase in range(3)
                for col_phase in range(3)
            )
            for row_phase in range(3):
                for col_phase in range(3):
                    row_index = node_indices[(row_key, row_phase + 1)]
                    col_index = node_indices[(col_key, col_phase + 1)]
                    projected[row_index][col_index] = (
                        transform[row_phase][1] * positive * inverse[1][col_phase]
                    )
    return tuple(tuple(row) for row in projected)


def _element_states(dss, class_name: str, collection_name: str) -> list[tuple[str, bool]]:
    collection = getattr(dss, collection_name, None)
    if collection is None or not hasattr(collection, "AllNames"):
        return []
    states: list[tuple[str, bool]] = []
    for raw_name in collection.AllNames() or []:
        name = str(raw_name)
        if not name or name.casefold() == "none":
            continue
        collection.Name(name)
        states.append((name, bool(dss.CktElement.Enabled())))
    return states


def _set_enabled(dss, class_name: str, name: str, enabled: bool) -> None:
    from cept.adapters.opendss.utils import dss_object

    dss.Text.Command(f"Edit {dss_object(class_name, name)} enabled={'y' if enabled else 'n'}")


def _capture_element_yprims(dss, inline: InlineNetwork, node_count: int) -> tuple[_ElementYPrim, ...]:
    captured: list[_ElementYPrim] = []
    elements = [
        ("Line", "Lines", [line.name for line in inline.lines]),
        ("Transformer", "Transformers", [tr.name for tr in inline.transformers]),
    ]
    for class_name, collection_name, names in elements:
        collection = getattr(dss, collection_name, None)
        if collection is None:
            raise YbusPowerFlowError(
                f"OpenDSS does not expose the {class_name} collection needed for "
                "solver-backed branch results."
            )
        for name in names:
            collection.Name(name)
            refs = tuple(int(ref) for ref in dss.CktElement.NodeRef())
            if not refs or any(ref < 0 or ref > node_count for ref in refs):
                raise YbusPowerFlowError(f"OpenDSS returned invalid NodeRef data for {class_name}.{name}.")
            matrix = _matrix_from_system_y(dss.CktElement.YPrim(), len(refs))
            captured.append(_ElementYPrim(class_name, name, refs, matrix))
    return tuple(captured)


def extract_passive_ybus(dss, inline: InlineNetwork) -> YbusSnapshot:
    """Extract the compiled network admittance without nonlinear injections.

    Constant-power loads and generator controls are removed only for the
    matrix snapshot.  Their Case injections are supplied to the NR equations.
    Original enabled states are restored before returning so callers can reuse
    the adapter without leaking the snapshot mutation.
    """
    initial_node_order = tuple(str(item) for item in dss.Circuit.YNodeOrder())
    initial_identities = map_ybus_nodes(initial_node_order, inline)
    states = []
    for class_name, collection_name in (
        ("Load", "Loads"),
        ("Generator", "Generators"),
        ("PVSystem", "PVsystems"),
        ("Storage", "Storages"),
        # The NR formulation fixes the reference voltage directly.  Remove
        # OpenDSS's Norton source admittance from the passive matrix rather
        # than counting it without its matching current source.
        ("Vsource", "Vsources"),
    ):
        states.extend(
            (class_name, name, enabled) for name, enabled in _element_states(dss, class_name, collection_name)
        )

    disabled: list[str] = []
    try:
        for class_name, name, enabled in states:
            if enabled:
                _set_enabled(dss, class_name, name, False)
                disabled.append(f"{class_name}.{name}")
        dss.Solution.Solve()
        passive_solve_converged = bool(dss.Solution.Converged())
        node_order = tuple(str(item) for item in dss.Circuit.YNodeOrder())
        identities = map_ybus_nodes(node_order, inline)
        if set(identities) != set(initial_identities):
            raise YbusPowerFlowError(
                "OpenDSS changed the compiled node identity set while extracting "
                "SystemY; the matrix snapshot is stale or incomplete."
            )
        matrix = _matrix_from_system_y(dss.Circuit.SystemY(), len(node_order))
        element_yprims = _capture_element_yprims(dss, inline, len(node_order))
    finally:
        for class_name, name, enabled in states:
            _set_enabled(dss, class_name, name, enabled)

    matrix_payload = [[[value.real, value.imag] for value in row] for row in matrix]
    return YbusSnapshot(
        node_order=node_order,
        identities=identities,
        matrix=matrix,
        matrix_sha256=_json_hash({"nodes": node_order, "matrix": matrix_payload}),
        node_order_sha256=_json_hash(list(node_order)),
        passive_solve_converged=passive_solve_converged,
        disabled_elements=tuple(disabled),
        element_yprims=element_yprims,
    )


def _validate_case(case: Case, inline: InlineNetwork) -> None:
    if case.network.kind != "inline":
        raise YbusPowerFlowError(
            "ybus-nr requires network.kind='inline'; a dss_file/builtin Case "
            "does not provide typed bus injections and reference controls."
        )
    if inline.is_unbalanced():
        raise YbusPowerFlowError(
            "ybus-nr currently requires a balanced inline Case; phase-specific "
            "or matrix data needs a reviewed sequence mapping."
        )
    if case.ders:
        raise YbusPowerFlowError(
            "ybus-nr does not support DER/controller overlays; use native OpenDSS "
            "until their equations and Q/P control semantics are reviewed."
        )
    if inline.open_elements:
        raise YbusPowerFlowError(
            "ybus-nr does not yet accept open inline elements because the engine "
            "node set would contain temporary topology identities."
        )
    if inline.three_winding_transformers:
        raise YbusPowerFlowError(
            "ybus-nr does not yet emit three-winding branch evidence; use the "
            "native OpenDSS or PowerFactory lane for this Case."
        )
    unsupported_loads = [
        load.id
        for load in inline.loads
        if load.model != "constant_power"
        or load.connection not in {"wye", "delta"}
        or (load.phase_nodes is not None and set(load.phase_nodes) != {1, 2, 3})
    ]
    if unsupported_loads:
        raise YbusPowerFlowError(
            "ybus-nr supports only balanced wye/delta constant-power loads; unsupported "
            "load(s): " + ", ".join(unsupported_loads)
        )
    unsupported_controls = [
        generator.name
        for generator in inline.generators
        if generator.control_mode not in {None, "pq", "pv", "slack"}
    ]
    if unsupported_controls:
        raise YbusPowerFlowError(
            "ybus-nr does not support generator control mode(s): " + ", ".join(unsupported_controls)
        )
    buses = [_bus_key(generator.bus) for generator in inline.generators]
    if len(buses) != len(set(buses)):
        raise YbusPowerFlowError(
            "ybus-nr requires at most one typed generator per bus; aggregate "
            "parallel machines in one InlineGenerator instead."
        )
    if len(inline.external_grids) > 1:
        raise YbusPowerFlowError(
            "ybus-nr requires one reference machine; multiple external grids "
            "need a reviewed multi-slack formulation."
        )
    slacks = [generator for generator in inline.generators if generator.bus_type == "slack"]
    if inline.external_grids and slacks:
        raise YbusPowerFlowError(
            "ybus-nr requires the reference to be either one external grid or one slack generator, not both."
        )
    if not inline.external_grids and len(slacks) != 1:
        raise YbusPowerFlowError(
            "ybus-nr requires exactly one slack generator when no external grid is declared."
        )


def _system_base_mva(inline: InlineNetwork) -> float:
    capacities = [abs(float(load.kw or 0.0)) / 1000.0 for load in inline.loads]
    capacities.extend(
        float(generator.mva) * max(int(generator.parallel_units or 1), 1) for generator in inline.generators
    )
    return max([1.0, *capacities])


def _load_q_kvar(load) -> float:
    """Return a load's reactive power in kvar, deriving it from power factor
    when only a pf is given."""
    if load.q_kvar is not None:
        return float(load.q_kvar)
    return float(load.kw) * math.tan(math.acos(load.pf))


def _load_coefficients(inline: InlineNetwork) -> dict[str, list[_LoadCoefficients]]:
    loads: dict[str, list[_LoadCoefficients]] = {}
    for load in inline.loads:
        q_kvar = _load_q_kvar(load)
        loads.setdefault(_bus_key(load.bus), []).append(
            _LoadCoefficients(
                p0_mw=float(load.kw) / 1000.0,
                q0_mvar=float(q_kvar) / 1000.0,
            )
        )
    return loads


def _load_at_voltage(models: list[_LoadCoefficients] | None) -> tuple[float, float]:
    """Return constant-power (P, Q) for one balanced bus in MW/Mvar."""
    p_mw = q_mvar = 0.0
    for model in models or []:
        p_mw += model.p0_mw
        q_mvar += model.q0_mvar
    return p_mw, q_mvar


def _generator_power(generator) -> tuple[float, float]:
    units = max(int(generator.parallel_units or 1), 1)
    p_mw = float(generator.kw) * units / 1000.0
    if generator.q_mvar is not None:
        q_mvar = float(generator.q_mvar)
    elif generator.bus_type == "pq" and generator.kw:
        q_mvar = p_mw * math.tan(math.acos(abs(float(generator.pf))))
        if float(generator.pf) < 0:
            q_mvar = -q_mvar
    else:
        q_mvar = 0.0
    return p_mw, q_mvar


def _solve_linear(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    """Solve a small dense real system with partial pivoting."""
    n = len(rhs)
    if any(len(row) != n for row in matrix):
        raise YbusPowerFlowError("Newton Jacobian is not square.")
    work = [list(row) + [value] for row, value in zip(matrix, rhs)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda row: abs(work[row][col]))
        pivot_value = abs(work[pivot][col])
        if not math.isfinite(pivot_value) or pivot_value <= 1e-12:
            raise YbusSingularJacobianError(
                f"Newton Jacobian is singular at column {col}; the reference/control mapping is not solvable."
            )
        work[col], work[pivot] = work[pivot], work[col]
        scale = work[col][col]
        for index in range(col, n + 1):
            work[col][index] /= scale
        for row in range(n):
            if row == col:
                continue
            factor = work[row][col]
            if factor == 0.0:
                continue
            for index in range(col, n + 1):
                work[row][index] -= factor * work[col][index]
    return [work[row][n] for row in range(n)]


def _power_values(
    matrix: tuple[tuple[complex, ...], ...], magnitudes: list[float], angles: list[float]
) -> tuple[list[float], list[float]]:
    voltage = [
        magnitude * complex(math.cos(angle), math.sin(angle)) for magnitude, angle in zip(magnitudes, angles)
    ]
    p_values: list[float] = []
    q_values: list[float] = []
    for row, value in enumerate(voltage):
        current = sum(matrix[row][col] * voltage[col] for col in range(len(voltage)))
        power = value * current.conjugate()
        p_values.append(power.real)
        q_values.append(power.imag)
    return p_values, q_values


def _jacobian(
    matrix: tuple[tuple[complex, ...], ...],
    magnitudes: list[float],
    angles: list[float],
    rows: list[tuple[str, int]],
    angle_nodes: list[int],
    magnitude_nodes: list[int],
) -> list[list[float]]:
    def derivative(kind: str, i: int, j: int, variable: str) -> float:
        vi = magnitudes[i]
        vj = magnitudes[j]
        gij = matrix[i][j].real
        bij = matrix[i][j].imag
        delta = angles[i] - angles[j]
        if variable == "angle":
            if i == j:
                if kind == "p":
                    return sum(
                        vi
                        * magnitudes[k]
                        * (
                            -matrix[i][k].real * math.sin(angles[i] - angles[k])
                            + matrix[i][k].imag * math.cos(angles[i] - angles[k])
                        )
                        for k in range(len(matrix))
                        if k != i
                    )
                return sum(
                    vi
                    * magnitudes[k]
                    * (
                        matrix[i][k].real * math.cos(angles[i] - angles[k])
                        + matrix[i][k].imag * math.sin(angles[i] - angles[k])
                    )
                    for k in range(len(matrix))
                    if k != i
                )
            if kind == "p":
                return vi * vj * (gij * math.sin(delta) - bij * math.cos(delta))
            return -vi * vj * (gij * math.cos(delta) + bij * math.sin(delta))
        if i == j:
            if kind == "p":
                return 2.0 * vi * matrix[i][i].real + sum(
                    magnitudes[k]
                    * (
                        matrix[i][k].real * math.cos(angles[i] - angles[k])
                        + matrix[i][k].imag * math.sin(angles[i] - angles[k])
                    )
                    for k in range(len(matrix))
                    if k != i
                )
            return -2.0 * vi * matrix[i][i].imag + sum(
                magnitudes[k]
                * (
                    matrix[i][k].real * math.sin(angles[i] - angles[k])
                    - matrix[i][k].imag * math.cos(angles[i] - angles[k])
                )
                for k in range(len(matrix))
                if k != i
            )
        if kind == "p":
            return vi * (gij * math.cos(delta) + bij * math.sin(delta))
        return vi * (gij * math.sin(delta) - bij * math.cos(delta))

    columns = [("angle", node) for node in angle_nodes]
    columns.extend(("magnitude", node) for node in magnitude_nodes)
    jacobian: list[list[float]] = []
    for kind, node in rows:
        row: list[float] = []
        for variable, column_node in columns:
            value = derivative(kind, node, column_node, variable)
            row.append(value)
        jacobian.append(row)
    return jacobian


def _validate_state(magnitudes: list[float], angles: list[float]) -> None:
    if any(not math.isfinite(value) or value <= 0.0 for value in magnitudes):
        raise YbusPowerFlowError("Newton produced a non-finite or non-positive voltage magnitude.")
    if any(not math.isfinite(value) for value in angles):
        raise YbusPowerFlowError("Newton produced a non-finite voltage angle.")


def _branch_flows_from_yprims(
    snapshot: YbusSnapshot,
    inline: InlineNetwork,
    magnitudes: list[float],
    angles: list[float],
) -> list[BranchFlow]:
    """Evaluate branch terminal powers using engine YPrim and NR voltages."""
    bus_by_key = {_bus_key(bus.name): bus for bus in inline.buses}
    voltage_bases = [
        bus_by_key[_bus_key(bus)].kv * 1000.0 / math.sqrt(3.0) for bus, _phase in snapshot.identities
    ]
    voltage = [
        magnitude * voltage_bases[index] * complex(math.cos(angles[index]), math.sin(angles[index]))
        for index, magnitude in enumerate(magnitudes)
    ]
    by_name = {(item.class_name.casefold(), item.name.casefold()): item for item in snapshot.element_yprims}
    flows: list[BranchFlow] = []

    def add_flow(class_name: str, branch, bus_from: str, bus_to: str, rating_a: float | None) -> None:
        item = by_name.get((class_name.casefold(), branch.name.casefold()))
        if item is None:
            raise YbusPowerFlowError(
                f"OpenDSS did not return YPrim for {class_name}.{branch.name}; "
                "branch result evidence is incomplete."
            )
        groups = 2
        if len(item.node_refs) % groups != 0:
            raise YbusPowerFlowError(
                f"{class_name}.{branch.name} has unsupported YPrim terminal shape "
                f"({len(item.node_refs)} nodes)."
            )
        terminal_nodes = len(item.node_refs) // groups
        if terminal_nodes not in {3, 4}:
            raise YbusPowerFlowError(f"{class_name}.{branch.name} is not a balanced three-phase branch.")
        local_voltage = [0j if ref == 0 else voltage[ref - 1] for ref in item.node_refs]
        currents = [
            sum(item.matrix[row][col] * local_voltage[col] for col in range(len(local_voltage)))
            for row in range(len(local_voltage))
        ]
        phase_indices = (0, 1, 2)
        from_power = sum(local_voltage[index] * currents[index].conjugate() for index in phase_indices)
        to_power = sum(
            local_voltage[index] * currents[index].conjugate()
            for index in (terminal_nodes + phase for phase in phase_indices)
        )
        loading = None
        if rating_a and rating_a > 0:
            loading = max(abs(currents[index]) for index in phase_indices) / rating_a * 100.0
        flows.append(
            BranchFlow(
                name=f"{class_name}.{branch.name}",
                bus_from=bus_from,
                bus_to=bus_to,
                p_kw=round(from_power.real / 1000.0, 4),
                q_kvar=round(from_power.imag / 1000.0, 4),
                p_to_kw=round(to_power.real / 1000.0, 4),
                q_to_kvar=round(to_power.imag / 1000.0, 4),
                losses_kw=round((from_power + to_power).real / 1000.0, 4),
                loading_pct=round(loading, 4) if loading is not None else None,
            )
        )

    for line in inline.lines:
        add_flow(
            "Line",
            line,
            line.from_bus,
            line.to_bus,
            float(line.normal_amps) if line.normal_amps else None,
        )
    for transformer in inline.transformers:
        rating_a = (
            float(transformer.mva)
            * max(int(transformer.parallel_units or 1), 1)
            * 1000.0
            / (math.sqrt(3.0) * float(transformer.hv_kv))
        )
        add_flow("Transformer", transformer, transformer.hv_bus, transformer.lv_bus, rating_a)
    return flows


def solve_ybus_power_flow(
    dss, case: Case, *, max_iterations: int = 30
) -> tuple[LoadFlowResult, dict[str, Any]]:
    """Solve a balanced inline Case over a Ybus extracted from OpenDSS."""
    inline = case.network.inline
    if inline is None:
        raise YbusPowerFlowError("ybus-nr requires an inline network.")
    _validate_case(case, inline)
    snapshot = extract_passive_ybus(dss, inline)
    n = len(snapshot.identities)
    positive_sequence_matrix = _positive_sequence_projection(snapshot, inline)
    bus_by_key = {_bus_key(bus.name): bus for bus in inline.buses}
    identity_indices = {
        (_bus_key(bus), phase): index for index, (bus, phase) in enumerate(snapshot.identities)
    }
    voltage_bases = [
        bus_by_key[_bus_key(bus)].kv * 1000.0 / math.sqrt(3.0) for bus, _phase in snapshot.identities
    ]
    # The common base only scales the extracted SI admittance and the mismatch;
    # it is a numerical reference, not an engineering value added to the Case.
    sbase_mva = _system_base_mva(inline)
    sbase_phase_va = sbase_mva * 1_000_000.0 / 3.0
    y_pu = tuple(
        tuple(
            value * voltage_bases[row] * voltage_bases[col] / sbase_phase_va
            for col, value in enumerate(matrix_row)
        )
        for row, matrix_row in enumerate(positive_sequence_matrix)
    )

    loads = _load_coefficients(inline)
    generators = {_bus_key(generator.bus): generator for generator in inline.generators}
    if inline.external_grids:
        reference_bus = _bus_key(inline.external_grids[0].bus)
        reference_pu = float(inline.external_grids[0].pu)
        reference_angle_deg = float(inline.external_grids[0].angle_deg)
    else:
        reference = next(generator for generator in inline.generators if generator.bus_type == "slack")
        reference_bus = _bus_key(reference.bus)
        reference_pu = float(reference.pu)
        reference_angle_deg = 0.0
    bus_types = {
        _bus_key(bus.name): (
            "slack"
            if _bus_key(bus.name) == reference_bus
            else generators[_bus_key(bus.name)].bus_type
            if _bus_key(bus.name) in generators
            else "pq"
        )
        for bus in inline.buses
    }
    phase_offsets = {1: 0.0, 2: -2.0 * math.pi / 3.0, 3: 2.0 * math.pi / 3.0}
    magnitudes = [
        reference_pu
        if _bus_key(bus) == reference_bus
        else float(generators[_bus_key(bus)].pu)
        if bus_types[_bus_key(bus)] == "pv"
        else 1.0
        for bus, _phase in snapshot.identities
    ]
    angles = [
        math.radians(reference_angle_deg) + phase_offsets[phase]
        if _bus_key(bus) == reference_bus
        else phase_offsets[phase]
        for bus, phase in snapshot.identities
    ]
    _validate_state(magnitudes, angles)

    generator_p_spec = [0.0] * n
    generator_q_spec = [0.0] * n
    for bus, phase in snapshot.identities:
        key = _bus_key(bus)
        gen = generators.get(key)
        gen_p_mw, gen_q_mvar = _generator_power(gen) if gen is not None else (0.0, 0.0)
        index = identity_indices[(key, phase)]
        generator_p_spec[index] = gen_p_mw / sbase_mva
        generator_q_spec[index] = gen_q_mvar / sbase_mva

    angle_nodes = [
        index
        for index, (bus, _phase) in enumerate(snapshot.identities)
        if bus_types[_bus_key(bus)] != "slack"
    ]
    magnitude_nodes = [
        index for index, (bus, _phase) in enumerate(snapshot.identities) if bus_types[_bus_key(bus)] == "pq"
    ]
    rows = [("p", index) for index in angle_nodes]
    rows.extend(("q", index) for index in magnitude_nodes)
    source_tolerance_kva = inline.load_flow_node_tolerance_kva
    if source_tolerance_kva is None:
        mismatch_tolerance_pu = 1e-8
        tolerance_label = "cept-default-1e-8-pu"
    else:
        mismatch_tolerance_pu = float(source_tolerance_kva) / (sbase_mva * 1000.0)
        tolerance_label = "PowerFactory ComLdf.errlf"

    def specification_values() -> tuple[list[float], list[float]]:
        p_spec = list(generator_p_spec)
        q_spec = list(generator_q_spec)
        for index, (bus, _phase) in enumerate(snapshot.identities):
            p_mw, q_mvar = _load_at_voltage(loads.get(_bus_key(bus)))
            p_spec[index] -= p_mw / sbase_mva
            q_spec[index] -= q_mvar / sbase_mva
        return p_spec, q_spec

    def mismatch_values(voltage_magnitudes: list[float], voltage_angles: list[float]) -> list[float]:
        p_spec, q_spec = specification_values()
        p_calc, q_calc = _power_values(y_pu, voltage_magnitudes, voltage_angles)
        return [
            (p_spec[index] if kind == "p" else q_spec[index])
            - (p_calc[index] if kind == "p" else q_calc[index])
            for kind, index in rows
        ]

    def newton_solve(start_magnitudes: list[float], start_angles: list[float]) -> NewtonSolution:
        """Run polar Newton-Raphson from one initial state."""
        mags = list(start_magnitudes)
        angs = list(start_angles)
        history: list[dict[str, Any]] = []
        for iteration in range(max_iterations + 1):
            mismatch = mismatch_values(mags, angs)
            max_mismatch_pu = max((abs(value) for value in mismatch), default=0.0)
            history.append(
                {
                    "iteration": iteration,
                    "max_mismatch_pu": max_mismatch_pu,
                    "max_mismatch_kva": max_mismatch_pu * sbase_mva * 1000.0,
                    "max_mismatch_mw": max_mismatch_pu * sbase_mva,
                }
            )
            if max_mismatch_pu <= mismatch_tolerance_pu:
                return NewtonSolution(True, iteration, tuple(history), tuple(mags), tuple(angs))
            if iteration == max_iterations:
                return NewtonSolution(False, iteration, tuple(history), tuple(mags), tuple(angs))
            try:
                jacobian = _jacobian(
                    y_pu,
                    mags,
                    angs,
                    rows,
                    angle_nodes,
                    magnitude_nodes,
                )
                update = _solve_linear(jacobian, mismatch)
            except YbusPowerFlowError as exc:
                return NewtonSolution(
                    False,
                    iteration,
                    tuple(history),
                    tuple(mags),
                    tuple(angs),
                    failure=exc,
                )
            old_norm = max_mismatch_pu
            accepted = False
            alpha = 1.0
            while alpha >= 1.0 / 128.0:
                trial_angles = list(angs)
                trial_magnitudes = list(mags)
                for value, index in zip(update, angle_nodes):
                    trial_angles[index] += alpha * value
                for value, index in zip(update[len(angle_nodes) :], magnitude_nodes):
                    trial_magnitudes[index] += alpha * value
                try:
                    _validate_state(trial_magnitudes, trial_angles)
                    trial_mismatch = mismatch_values(trial_magnitudes, trial_angles)
                    trial_norm = max((abs(value) for value in trial_mismatch), default=0.0)
                except YbusPowerFlowError:
                    trial_norm = math.inf
                if trial_norm <= old_norm or alpha <= 1.0 / 128.0:
                    angs, mags = trial_angles, trial_magnitudes
                    accepted = True
                    break
                alpha /= 2.0
            if not accepted:
                return NewtonSolution(
                    False,
                    iteration,
                    tuple(history),
                    tuple(mags),
                    tuple(angs),
                    failure=YbusPowerFlowError("Newton line search could not produce a finite trial state."),
                )
        return NewtonSolution(False, max_iterations, tuple(history), tuple(mags), tuple(angs))

    # A perfectly balanced flat start (every bus at the same 120-degree phase
    # pattern) is a degenerate point for some balanced topologies: the
    # zero/negative-sequence modes decouple and leave the Jacobian singular at
    # iteration 0 even though a solution exists and Newton converges from any
    # nearby point (measured on the IEEE-14 holdout).  Retry once from a tiny
    # deterministic angle perturbation that breaks the phase symmetry without
    # moving the reference bus or any bus magnitude.  The perturbation is a
    # numerical initialisation only: it is orders of magnitude below the
    # tolerance and vanishes from the converged state, and the flat-start path
    # is kept untouched for networks that start cleanly.
    solution = newton_solve(magnitudes, angles)
    initialisation = "flat-start-balanced-phases"
    if (
        not solution.converged
        and solution.iterations == 0
        and isinstance(solution.failure, YbusSingularJacobianError)
    ):
        perturbed_angles = list(solution.angles)
        for index, (bus, _phase) in enumerate(snapshot.identities):
            if _bus_key(bus) != reference_bus:
                perturbed_angles[index] += 1e-7 * (index + 1)
        solution = newton_solve(list(solution.magnitudes), perturbed_angles)
        initialisation = (
            "flat-start-balanced-phases; singular flat-start Jacobian, "
            "retried from a deterministic 1e-7 rad per-node angle perturbation"
        )

    magnitudes = list(solution.magnitudes)
    angles = list(solution.angles)
    converged = solution.converged
    iterations = solution.iterations
    mismatch_history = list(solution.history)
    failure = solution.failure

    p_calc, q_calc = _power_values(y_pu, magnitudes, angles)
    evidence: dict[str, Any] = {
        "schema": "cept-ybus-newton-r1",
        "solver_lane": "newton_extracted_ybus",
        "matrix_source": "OpenDSS Circuit.SystemY",
        "matrix_units": "siemens",
        "matrix_domain": "positive_sequence_projection_of_three_phase_engine_order",
        "matrix_dimension": n,
        "matrix_sha256": snapshot.matrix_sha256,
        "positive_sequence_matrix_sha256": _json_hash(
            [[[value.real, value.imag] for value in row] for row in positive_sequence_matrix]
        ),
        "node_order_sha256": snapshot.node_order_sha256,
        "node_order": list(snapshot.node_order),
        "node_identities": [list(identity) for identity in snapshot.identities],
        "passive_snapshot": {
            "solve_converged": snapshot.passive_solve_converged,
            "disabled_elements": list(snapshot.disabled_elements),
        },
        "reference_bus": next(bus.name for bus in inline.buses if _bus_key(bus.name) == reference_bus),
        "sbase_mva": sbase_mva,
        "initialisation": initialisation,
        "convergence": {
            "converged": converged,
            "iterations": iterations,
            "criterion": tolerance_label,
            "source_errlf_kva": source_tolerance_kva,
            "threshold_pu": mismatch_tolerance_pu,
            "mismatch_history": mismatch_history,
            "q_limit_switches": [],
        },
    }
    if failure is not None:
        evidence["convergence"]["failure"] = str(failure)

    if not converged:
        return (
            LoadFlowResult(converged=False, iterations=iterations),
            evidence,
        )

    def bus_power(bus_key: str, values: list[float]) -> float:
        indices = [identity_indices[(bus_key, phase)] for phase in (1, 2, 3)]
        return sum(values[index] for index in indices) * sbase_mva / 3.0

    bus_voltages = [
        BusVoltage(
            bus=bus.name,
            phase=phase,
            v_pu=round(magnitudes[identity_indices[(_bus_key(bus.name), phase)]], 6),
            v_angle_deg=round(
                math.degrees(angles[identity_indices[(_bus_key(bus.name), phase)]]) % 360.0
                - (
                    360.0
                    if math.degrees(angles[identity_indices[(_bus_key(bus.name), phase)]]) % 360.0 > 180.0
                    else 0.0
                ),
                4,
            ),
        )
        for bus in inline.buses
        for phase in (1, 2, 3)
    ]
    der_outputs: list[DEROutput] = []
    for generator in inline.generators:
        key = _bus_key(generator.bus)
        load_p_mw, load_q_mvar = _load_at_voltage(loads.get(key))
        gen_p_mw = bus_power(key, p_calc) + load_p_mw
        gen_q_mvar = bus_power(key, q_calc) + load_q_mvar
        der_outputs.append(
            DEROutput(
                name=generator.name,
                kind="generator",
                bus=generator.bus,
                p_kw=round(gen_p_mw * 1000.0, 4),
                q_kvar=round(gen_q_mvar * 1000.0, 4),
                v_mean_pu=round(
                    sum(magnitudes[identity_indices[(key, phase)]] for phase in (1, 2, 3)) / 3.0,
                    6,
                ),
            )
        )
    reference_p_mw = bus_power(reference_bus, p_calc)
    reference_q_mvar = bus_power(reference_bus, q_calc)
    total_loss_mw = sum(p_calc) * sbase_mva / 3.0
    total_loss_mvar = sum(q_calc) * sbase_mva / 3.0
    total_load_mw = 0.0
    total_load_mvar = 0.0
    for key, models in loads.items():
        load_p_mw, load_q_mvar = _load_at_voltage(models)
        total_load_mw += load_p_mw
        total_load_mvar += load_q_mvar
    result = LoadFlowResult(
        converged=True,
        iterations=iterations,
        bus_voltages=bus_voltages,
        branch_flows=_branch_flows_from_yprims(snapshot, inline, magnitudes, angles),
        total_load_kw=round(total_load_mw * 1000.0, 4),
        total_load_kvar=round(total_load_mvar * 1000.0, 4),
        total_loss_kw=round(total_loss_mw * 1000.0, 4),
        total_loss_kvar=round(total_loss_mvar * 1000.0, 4),
        source_p_kw=round(reference_p_mw * 1000.0, 4),
        source_q_kvar=round(reference_q_mvar * 1000.0, 4),
        der_outputs=der_outputs,
        device_outputs=[
            DEROutput(
                name=load.id,
                kind="load",
                bus=load.bus,
                p_kw=round(float(load.kw), 4),
                q_kvar=round(_load_q_kvar(load), 4),
            )
            for load in inline.loads
        ],
    )
    return result, evidence


__all__ = [
    "YbusPowerFlowError",
    "YbusSingularJacobianError",
    "NewtonSolution",
    "YbusSnapshot",
    "extract_passive_ybus",
    "map_ybus_nodes",
    "solve_ybus_power_flow",
]
