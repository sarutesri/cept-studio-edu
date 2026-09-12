"""Inline network models: lines, transformers, loads, generators, buses.

Extracted from `cept.schema.case` (mechanical move, no behavior change);
re-exported by the package so the old import paths keep working.
"""

from __future__ import annotations

import math
from typing import Any, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)


# Meta
# --------------------------------------------------------------------------- #


class Meta(BaseModel):
    """Human/agent-facing description of the study."""

    name: str = Field(..., description="Short, unique study name.")
    author: str = Field("agent", description="Who/what created this case.")
    description: str = Field("", description="Free-text purpose of the study.")
    language: Literal["en", "th"] = Field("en", description="Report language.")
    mode: Literal["research", "demonstrator"] = Field(
        "research",
        description="Research mode rejects material defaults; demonstrator mode records them.",
    )


# --------------------------------------------------------------------------- #
# Network
# --------------------------------------------------------------------------- #


class GridSource(BaseModel):
    """How the microgrid is energized."""

    kind: Literal["grid", "islanded"] = "grid"
    grid_forming_der: Optional[str] = Field(
        None,
        description="DER id acting as swing/grid-forming source. Required when kind == 'islanded'.",
    )


class InlineBus(BaseModel):
    name: str
    kv: float = Field(..., gt=0, description="Nominal line-to-line voltage (kV).")
    phases: int = Field(3, ge=1, le=3)
    powerfactory_phase_technology: Optional[int] = Field(
        None,
        ge=0,
        description="Source ElmTerm.phtech enum, when the network came from PowerFactory.",
    )

    @model_serializer(mode="wrap")
    def _omit_absent_pf_phase_technology(
        self, handler: SerializerFunctionWrapHandler, info: SerializationInfo
    ) -> Any:
        # Additive field: keep fingerprints and stored case.json for existing
        # studies stable by not emitting this PowerFactory-only detail when it
        # was never provided (same contract as StudySpec intent).
        payload = handler(self)
        if isinstance(payload, dict) and payload.get("powerfactory_phase_technology") is None:
            payload.pop("powerfactory_phase_technology", None)
        return payload


class InlineLineTotalParameters(BaseModel):
    """Positive/zero-sequence values for one complete branch.

    Some established transmission source formats (for example PSS/E RAW and
    MATPOWER) give the series impedance and line charging for the *complete*
    branch.  A physical route length cannot be recovered from those values.
    Keeping this payload separate from the per-kilometre fields prevents a
    fictitious ``1 km`` length from becoming part of a Case or report.
    """

    r1_ohm: float = Field(..., ge=0, description="Total positive-sequence resistance (ohm).")
    x1_ohm: float = Field(..., ge=0, description="Total positive-sequence reactance (ohm).")
    b1_us: float = Field(..., ge=0, description="Total positive-sequence shunt susceptance (microsiemens).")
    r0_ohm: Optional[float] = Field(
        None, ge=0, description="Total zero-sequence resistance (ohm), when supplied."
    )
    x0_ohm: Optional[float] = Field(
        None, ge=0, description="Total zero-sequence reactance (ohm), when supplied."
    )
    b0_us: Optional[float] = Field(
        None,
        ge=0,
        description=(
            "Total zero-sequence shunt susceptance (microsiemens), when the "
            "source supplies it.  A distribution feeder's zero-sequence "
            "charging differs from 3x the positive value; omitting it falls "
            "back to the adapter's positive-sequence-derived default."
        ),
    )


def _complex_inverse(matrix: list[list[complex]]) -> list[list[complex]]:
    """Invert a small dense complex matrix by Gauss-Jordan with partial pivoting."""
    n = len(matrix)
    work = [list(row) + [1.0 + 0j if i == j else 0j for j in range(n)] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(work[r][col]))
        if abs(work[pivot][col]) == 0.0:
            raise ValueError("singular conductor sub-matrix; cannot eliminate the neutral")
        work[col], work[pivot] = work[pivot], work[col]
        scale = work[col][col]
        work[col] = [value / scale for value in work[col]]
        for row in range(n):
            if row == col:
                continue
            factor = work[row][col]
            if factor:
                work[row] = [a - factor * b for a, b in zip(work[row], work[col])]
    return [row[n:] for row in work]


class InlineLineMatrix(BaseModel):
    """Untransposed line constants, as the source's own conductor matrices.

    PowerFactory computes these from tower geometry and conductor data and
    exposes them on ``TypTow`` as ``R_c``/``X_c``/``C_c`` -- full matrices over
    every conductor, neutrals included, with unequal mutuals.  They are carried
    here unreduced because that is what the source declares; eliminating the
    neutral is a derivation, and it happens in one place
    (:meth:`phase_matrices`) rather than separately per adapter.

    Reproducing these from geometry would mean reimplementing Carson's
    equations and matching PowerFactory's earth-return model exactly; reading
    what it already computed is both simpler and more faithful.
    """

    phases: int = Field(..., ge=1, le=3, description="Number of phase conductors.")
    neutrals: int = Field(0, ge=0, description="Number of neutral/earth conductors to eliminate.")
    r_ohm_per_km: list[list[float]]
    x_ohm_per_km: list[list[float]]
    c_uf_per_km: Optional[list[list[float]]] = None

    @model_validator(mode="after")
    def _square_and_consistent(self) -> "InlineLineMatrix":
        order = self.phases + self.neutrals
        for name, matrix in (
            ("r_ohm_per_km", self.r_ohm_per_km),
            ("x_ohm_per_km", self.x_ohm_per_km),
            ("c_uf_per_km", self.c_uf_per_km),
        ):
            if matrix is None:
                continue
            if len(matrix) != order or any(len(row) != order for row in matrix):
                raise ValueError(
                    f"{name} must be {order}x{order} for {self.phases} phase(s) "
                    f"and {self.neutrals} neutral(s); got {len(matrix)} rows"
                )
        return self

    def phase_matrices(self) -> tuple[list[list[float]], list[list[float]], list[list[float]] | None]:
        """Return (R, X, C) reduced to the phase conductors.

        The series matrix is Kron-reduced: the neutral carries return current,
        which raises the phase self-impedances and the mutuals between them.

        Capacitance needs no elimination.  A grounded neutral is held at
        ``V_n = 0``, so ``Q_p = C_pp V_p + C_pn V_n`` loses its second term and
        the phase block is already the answer.  (Reducing the Maxwell potential
        matrix ``P = C^-1`` and inverting back gives the same ``C_pp`` by block
        inversion -- two inversions to arrive where slicing already is.)
        """
        p, n = self.phases, self.neutrals
        z = [
            [complex(self.r_ohm_per_km[i][j], self.x_ohm_per_km[i][j]) for j in range(p + n)]
            for i in range(p + n)
        ]
        if n:
            z_nn_inv = _complex_inverse([[z[p + a][p + b] for b in range(n)] for a in range(n)])
            reduced = [
                [
                    z[i][j]
                    - sum(z[i][p + a] * z_nn_inv[a][b] * z[p + b][j] for a in range(n) for b in range(n))
                    for j in range(p)
                ]
                for i in range(p)
            ]
        else:
            reduced = [[z[i][j] for j in range(p)] for i in range(p)]
        r = [[value.real for value in row] for row in reduced]
        x = [[value.imag for value in row] for row in reduced]
        c: list[list[float]] | None = None
        if self.c_uf_per_km is not None:
            c = [[self.c_uf_per_km[i][j] for j in range(p)] for i in range(p)]
        return r, x, c


class InlineLine(BaseModel):
    name: str
    from_bus: str
    to_bus: str
    length_km: Optional[float] = Field(None, gt=0)
    r1_ohm_per_km: Optional[float] = Field(None, ge=0, description="Positive-sequence resistance.")
    x1_ohm_per_km: Optional[float] = Field(None, ge=0, description="Positive-sequence reactance.")
    r0_ohm_per_km: Optional[float] = Field(
        None, ge=0, description="Zero-sequence resistance; defaults to 3x r1 if omitted."
    )
    x0_ohm_per_km: Optional[float] = Field(
        None, ge=0, description="Zero-sequence reactance; defaults to 3x x1 if omitted."
    )
    b1_us_per_km: Optional[float] = Field(
        0.0, ge=0, description="Positive-sequence shunt susceptance (microsiemens/km)."
    )
    normal_amps: Optional[float] = Field(None, gt=0, description="Thermal rating.")
    matrix: Optional[InlineLineMatrix] = Field(
        None,
        description=(
            "Untransposed conductor matrices from the source's line type. When "
            "present these are authoritative and the sequence scalars above are "
            "not used: an untransposed feeder cannot be described by r1/x1."
        ),
    )
    phase_nodes: Optional[list[int]] = Field(
        None,
        description=(
            "Which phase conductors this branch actually connects, as 1-based "
            "node numbers (e.g. [1, 3] for a two-phase lateral on A and C). "
            "Required reading for an unbalanced feeder, where a lateral's "
            "identity is which phases it carries."
        ),
    )

    powerfactory_ishclne: Optional[bool] = Field(
        None,
        description=(
            "Source-disclosed PowerFactory RMS line-fault preparation flag "
            "(ElmLne.ishclne); omitted means the source did not expose it."
        ),
    )
    powerfactory_fshcloc: Optional[float] = Field(
        None,
        ge=0,
        le=100,
        description=(
            "Source-disclosed PowerFactory relative RMS line-fault location "
            "in percent (ElmLne.fshcloc, 0 at terminal i and 100 at terminal j)."
        ),
    )
    total_parameters: Optional[InlineLineTotalParameters] = Field(
        None,
        exclude_if=lambda value: value is None,
        description=(
            "Total branch R/X/B from a source that does not supply a physical "
            "length. Mutually exclusive with length_km and all per-kilometre fields."
        ),
    )

    @model_validator(mode="after")
    def _check_parameter_basis(self) -> "InlineLine":
        if self.total_parameters is not None:
            mixed = [
                field
                for field in (
                    "length_km",
                    "r1_ohm_per_km",
                    "x1_ohm_per_km",
                    "r0_ohm_per_km",
                    "x0_ohm_per_km",
                    "b1_us_per_km",
                )
                # A Case round-tripped through ``model_dump`` retains legacy
                # keys as JSON null.  Null is not a competing parameter value.
                if field in self.model_fields_set and getattr(self, field) is not None
            ]
            if mixed:
                raise ValueError(
                    "InlineLine total_parameters cannot be mixed with physical-length "
                    "or per-kilometre fields: " + ", ".join(mixed)
                )
            # Keep a total-parameter line unambiguous even though the legacy
            # shunt field retains a zero default for backwards-compatible cases.
            self.b1_us_per_km = None
            return self

        if self.matrix is not None:
            # An untransposed feeder is described by its conductor matrices;
            # r1/x1 do not exist for it and inventing them would describe a
            # different, balanced line.
            missing = ["length_km"] if self.length_km is None else []
        else:
            missing = [
                field
                for field in ("length_km", "r1_ohm_per_km", "x1_ohm_per_km")
                if getattr(self, field) is None
            ]
        if missing:
            raise ValueError(
                "InlineLine requires length_km, r1_ohm_per_km, and x1_ohm_per_km "
                "unless total_parameters is supplied; missing " + ", ".join(missing)
            )
        return self

    @property
    def uses_total_parameters(self) -> bool:
        return self.total_parameters is not None

    @property
    def display_length_km(self) -> Optional[float]:
        """Return a physical length only when the Case actually supplied one."""
        return None if self.uses_total_parameters else self.length_km

    @property
    def adapter_length_km(self) -> float:
        """Adapter scaling length; ``1`` is an internal total-parameter normalisation.

        It is never a physical length and must not be rendered in reports.
        """
        return 1.0 if self.uses_total_parameters else float(self.length_km)

    @property
    def r1_for_adapter_ohm_per_km(self) -> float:
        return self.total_parameters.r1_ohm if self.uses_total_parameters else float(self.r1_ohm_per_km)

    @property
    def x1_for_adapter_ohm_per_km(self) -> float:
        return self.total_parameters.x1_ohm if self.uses_total_parameters else float(self.x1_ohm_per_km)

    @property
    def b1_for_adapter_us_per_km(self) -> float:
        return self.total_parameters.b1_us if self.uses_total_parameters else float(self.b1_us_per_km)

    @property
    def r0_for_adapter_ohm_per_km(self) -> Optional[float]:
        if self.uses_total_parameters:
            return self.total_parameters.r0_ohm
        # A missing OR non-physical (<=0) zero-sequence resistance is a common
        # gap in positive-sequence-only sources (e.g. a PFD ingest that leaves
        # r0=0).  A zero z0 makes the OpenDSS 3-phase Y-matrix singular ("Matrix
        # Inversion Error"); fall back to the 3*R1 convention.  Balanced load
        # flow is unaffected because no zero-sequence current flows.
        if self.r0_ohm_per_km is None or self.r0_ohm_per_km <= 0:
            return 3 * float(self.r1_ohm_per_km)
        return self.r0_ohm_per_km

    @property
    def x0_for_adapter_ohm_per_km(self) -> Optional[float]:
        if self.uses_total_parameters:
            return self.total_parameters.x0_ohm
        # See r0_for_adapter_ohm_per_km: guard the non-physical zero/negative
        # zero-sequence reactance the same way (3*X1 convention).
        if self.x0_ohm_per_km is None or self.x0_ohm_per_km <= 0:
            return 3 * float(self.x1_ohm_per_km)
        return self.x0_ohm_per_km

    @model_validator(mode="after")
    def _matrix_phase_nodes_agree(self) -> "InlineLine":
        if self.matrix is None:
            return self
        nodes = self.phase_nodes
        if nodes is None:
            return self
        if len(nodes) != self.matrix.phases:
            raise ValueError(
                f"line '{self.name}' declares {len(nodes)} phase node(s) "
                f"{nodes} but its matrix carries {self.matrix.phases} phase conductor(s)"
            )
        if len(set(nodes)) != len(nodes) or any(node < 1 or node > 3 for node in nodes):
            raise ValueError(f"line '{self.name}' phase_nodes must be distinct values in 1..3; got {nodes}")
        return self


class InlineTransformer(BaseModel):
    name: str
    hv_bus: str
    lv_bus: str
    mva: float = Field(..., gt=0)
    parallel_units: int = Field(
        1,
        ge=1,
        description="Number of identical transformer units represented by the source element.",
    )
    hv_kv: float = Field(..., gt=0)
    lv_kv: float = Field(..., gt=0)
    uk_pct: float = Field(
        ...,
        ge=0,
        description=(
            "Short-circuit impedance (%) on the transformer's own MVA base. "
            "Zero is a real source statement, not missing data: a step voltage "
            "regulator is an ideal ratio changer with no leakage impedance, and "
            "IEEE 13-node's VregA/B/C declare uktr=0 exactly so."
        ),
    )
    x_r_ratio: float = Field(10.0, gt=0, description="X/R ratio used to split uk_pct into R/X.")
    zero_resistance: bool = Field(
        False,
        description=(
            "True only when source transformer data explicitly gives zero copper loss/resistance. "
            "Uses r=0 and x=uk_pct instead of inventing a finite X/R ratio."
        ),
    )
    vector_group: str = Field(
        "Dyn11", description="Informational only; not enforced in Phase 1 balanced studies."
    )
    tap_pu: float = Field(1.0, gt=0, description="Fixed positive-sequence tap ratio from the source model.")
    tap_min_step: Optional[int] = Field(
        None, description="Source transformer minimum tap position, when exposed."
    )
    tap_max_step: Optional[int] = Field(
        None, description="Source transformer maximum tap position, when exposed."
    )
    tap_position: Optional[int] = Field(
        None, description="Source transformer current tap position, when exposed."
    )
    tap_step_percent: Optional[float] = Field(
        None, gt=0, description="Source transformer per-step tap change (%), when exposed."
    )
    powerfactory_tap_side: Optional[int] = Field(
        None,
        ge=0,
        le=1,
        description="Source TypTr2.tap_side enum (0=HV, 1=LV), when exposed.",
    )
    phase_nodes: Optional[list[int]] = Field(
        None,
        description=(
            "Phases this unit serves, as 1-based node numbers. A step voltage "
            "regulator bank is three independent single-phase units, each with "
            "its own tap; carrying them as parallel three-phase transformers "
            "would apply every tap to every phase."
        ),
    )
    zero_sequence_uk_pct: Optional[float] = Field(
        None, ge=0, description="Source transformer zero-sequence impedance (%), when exposed."
    )
    phase_shift_deg: Optional[float] = Field(
        None, description="Source-derived tap phase shift at tap_pu, when exposed."
    )
    no_load_loss_kw: Optional[float] = Field(
        None, ge=0, description="Source transformer no-load/core loss in kW, when exposed."
    )
    no_load_current_pct: Optional[float] = Field(
        None, ge=0, description="Source transformer magnetizing current as percent, when exposed."
    )

    @model_validator(mode="after")
    def _check_impedance_basis(self) -> "InlineTransformer":
        # ``model_dump()`` retains the legacy default of 10, so it cannot be
        # distinguished from a reloaded lossless Case. A non-default explicit
        # X/R is genuinely contradictory; the default is ignored when the
        # source has selected zero resistance.
        if self.zero_resistance and "x_r_ratio" in self.model_fields_set and self.x_r_ratio != 10.0:
            raise ValueError(
                "InlineTransformer zero_resistance cannot be combined with a non-default "
                "x_r_ratio; the source must select one impedance basis."
            )
        return self

    @property
    def series_r_x_pct(self) -> tuple[float, float]:
        """Return source-preserving series resistance/reactance percentages."""
        if self.zero_resistance:
            return 0.0, self.uk_pct
        r_pct = self.uk_pct / (1 + self.x_r_ratio**2) ** 0.5
        return r_pct, r_pct * self.x_r_ratio


class InlineThreeWindingTransformer(BaseModel):
    """A three-winding transformer, carried in the source's own star form.

    PowerFactory's ``TypTr3`` stores the positive-sequence short-circuit
    voltages as the star (per-winding) equivalent -- ``uktr3_h/m/l``, each on
    its own winding rating -- rather than per winding pair.  That form is kept
    here verbatim because it is what the source declares; engines that want
    pair impedances (OpenDSS wants XHL/XHT/XLT) derive them through
    :meth:`pair_impedances_pct`, so the star/pair algebra lives in one place
    instead of being repeated per adapter.
    """

    name: str
    hv_bus: str
    mv_bus: str
    lv_bus: str
    hv_kv: float = Field(..., gt=0)
    mv_kv: float = Field(..., gt=0)
    lv_kv: float = Field(..., gt=0)
    hv_mva: float = Field(..., gt=0)
    mv_mva: float = Field(..., gt=0)
    lv_mva: float = Field(..., gt=0)
    # Star-equivalent short-circuit voltage per winding, each on that winding's
    # own MVA rating, as PowerFactory declares it.
    hv_uk_pct: float = Field(..., ge=0)
    mv_uk_pct: float = Field(..., ge=0)
    lv_uk_pct: float = Field(..., ge=0)
    hv_r_pu: float = Field(0.0, ge=0, description="Star-equivalent resistance, pu on the winding rating.")
    mv_r_pu: float = Field(0.0, ge=0)
    lv_r_pu: float = Field(0.0, ge=0)
    hv_connection: str = Field("YN", description="Source winding connection, e.g. YN / Y / D.")
    mv_connection: str = "YN"
    lv_connection: str = "D"
    hv_clock: int = Field(0, ge=0, le=11, description="Vector-group clock number per winding.")
    mv_clock: int = Field(0, ge=0, le=11)
    lv_clock: int = Field(0, ge=0, le=11)
    hv_tap_position: int = 0
    mv_tap_position: int = 0
    lv_tap_position: int = 0
    hv_tap_step_percent: float = 0.0
    mv_tap_step_percent: float = 0.0
    lv_tap_step_percent: float = 0.0
    in_service: bool = True

    @property
    def base_mva(self) -> float:
        """The common base the pair impedances are expressed on (HV rating)."""
        return float(self.hv_mva)

    def _star_pct_on_base(self) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        base = self.base_mva
        windings = (
            (self.hv_uk_pct, self.hv_r_pu, self.hv_mva),
            (self.mv_uk_pct, self.mv_r_pu, self.mv_mva),
            (self.lv_uk_pct, self.lv_r_pu, self.lv_mva),
        )
        # Percentages scale with the base MVA ratio; a 10% winding uk on a
        # 50 MVA winding is 70% on a 350 MVA base.
        x_pct: list[float] = []
        r_pct: list[float] = []
        for uk_pct, r_pu, mva in windings:
            scale = base / float(mva)
            uk_on_base = float(uk_pct) * scale
            r_on_base = float(r_pu) * 100.0 * scale
            # uk is the magnitude of the winding impedance; the reactance is
            # what remains once the declared resistance is taken out.
            x_on_base = math.sqrt(max(uk_on_base**2 - r_on_base**2, 0.0))
            x_pct.append(x_on_base)
            r_pct.append(r_on_base)
        return (x_pct[0], x_pct[1], x_pct[2]), (r_pct[0], r_pct[1], r_pct[2])

    def pair_impedances_pct(self) -> dict[str, float]:
        """Return per-pair R/X percentages on :attr:`base_mva`.

        Star to pair is additive: ``z_hm = z_h + z_m``.  Engines that model a
        three-winding transformer as three coupled pairs (OpenDSS) need this
        form; the star values remain the source of truth.
        """
        (x_h, x_m, x_l), (r_h, r_m, r_l) = self._star_pct_on_base()
        return {
            "xhm_pct": x_h + x_m,
            "xhl_pct": x_h + x_l,
            "xml_pct": x_m + x_l,
            "rhm_pct": r_h + r_m,
            "rhl_pct": r_h + r_l,
            "rml_pct": r_m + r_l,
            "r_hv_pct": r_h,
            "r_mv_pct": r_m,
            "r_lv_pct": r_l,
        }

    @model_validator(mode="after")
    def _distinct_terminals(self) -> "InlineThreeWindingTransformer":
        buses = (self.hv_bus, self.mv_bus, self.lv_bus)
        if len(set(buses)) != 3:
            raise ValueError(
                f"three-winding transformer '{self.name}' must connect three distinct terminals; got {buses}"
            )
        return self


class InlineLoad(BaseModel):
    id: str
    bus: str
    kw: float = Field(..., ge=0)
    pf: float = Field(0.95, gt=0, le=1.0)
    q_kvar: Optional[float] = Field(
        None,
        description=(
            "Optional source-preserved reactive power at the operating point. "
            "Signed values retain capacitive (negative) loads; when omitted, "
            "the magnitude is derived from kw and pf for legacy Cases."
        ),
    )
    phase_nodes: Optional[list[int]] = Field(
        None,
        description=(
            "Phases this load is connected to, as 1-based node numbers. A "
            "distribution feeder's single-phase loads are defined by which "
            "phase they sit on; omitting it describes a balanced three-phase "
            "load the source never had."
        ),
    )
    kw_per_phase: Optional[list[float]] = Field(
        None,
        description=(
            "Active power per entry of phase_nodes. When present this is authoritative and kw is its sum."
        ),
    )
    kvar_per_phase: Optional[list[float]] = None
    connection: Literal["wye", "delta"] = Field(
        "wye",
        description=(
            "Winding connection. A phase-to-phase load sees sqrt(3) times the "
            "phase-to-neutral voltage, so this is not cosmetic."
        ),
    )
    model: Literal["constant_power", "zip"] = "constant_power"
    zipv: Optional[list[float]] = Field(
        None,
        description="OpenDSS ZIPV coefficients [pZ,pI,pP,qZ,qI,qP,Vcut]; required for model='zip'.",
    )

    @model_validator(mode="after")
    def _validate_zip(self) -> "InlineLoad":
        if self.q_kvar is not None and not math.isfinite(float(self.q_kvar)):
            raise ValueError("InlineLoad q_kvar must be finite when provided")
        if self.model == "zip":
            if self.zipv is None or len(self.zipv) != 7:
                raise ValueError("InlineLoad model='zip' requires seven zipv coefficients")
            if abs(sum(self.zipv[:3]) - 1.0) > 1e-6 or abs(sum(self.zipv[3:6]) - 1.0) > 1e-6:
                raise ValueError("InlineLoad zipv active/reactive fractions must each sum to 1")
        return self

    @model_validator(mode="after")
    def _per_phase_agrees(self) -> "InlineLoad":
        nodes = self.phase_nodes
        if nodes is not None:
            if len(set(nodes)) != len(nodes) or any(n < 1 or n > 3 for n in nodes):
                raise ValueError(f"load '{self.id}' phase_nodes must be distinct values in 1..3; got {nodes}")
        for name, values in (("kw_per_phase", self.kw_per_phase), ("kvar_per_phase", self.kvar_per_phase)):
            if values is None:
                continue
            if nodes is None:
                raise ValueError(f"load '{self.id}' {name} requires phase_nodes")
            if len(values) != len(nodes):
                raise ValueError(
                    f"load '{self.id}' {name} has {len(values)} entries for {len(nodes)} phase node(s)"
                )
        return self


class InlineExternalGrid(BaseModel):
    """The network's voltage reference (slack), described by its Thevenin
    equivalent so both OpenDSS ('New Circuit') and PowerFactory (ElmXnet)
    build the identical source."""

    name: str
    bus: str
    pu: float = Field(1.0, gt=0, description="Voltage setpoint (pu).")
    angle_deg: float = 0.0
    sk3_mva: float = Field(..., gt=0, description="Three-phase short-circuit level (MVA).")
    sk1_mva: Optional[float] = Field(
        None,
        gt=0,
        description="Single-line-to-ground short-circuit level (MVA); defaults to sk3_mva if omitted.",
    )
    x_r_ratio: float = Field(10.0, gt=0, description="X/R ratio of the source impedance.")


class GenrouDynamics(BaseModel):
    """Round-rotor synchronous-machine dynamic model parameters, on the
    generator's own MVA base — used for PowerFactory RMS stability
    (``ComSim``), setting TypSym's intrinsic dynamic-tab attributes. No
    AVR/governor is attached (constant field voltage / constant mechanical
    power), matching a "classical" no-control swing study.

    This needs the full parameter set (Xd, Xq, Xd', Xq', Xd'', Td0', Tq0');
    a source like Kundur's SMIB Example 13.1 only specifies H, D, Xd' (a
    reduced classical model), so the remaining fields are typically not
    project-specific data — they must be supplied explicitly (research mode)
    or accepted as documented typical large-synchronous-machine defaults
    (demonstrator mode).
    """

    h: float = Field(..., gt=0, description="Inertia constant H (MW*s/MVA).")
    d: float = Field(0.0, ge=0, description="Damping coefficient.")
    xd: float = Field(..., gt=0, description="Synchronous reactance Xd (pu).")
    xq: float = Field(..., gt=0, description="Synchronous reactance Xq (pu).")
    xdp: float = Field(..., gt=0, description="Transient reactance Xd' (pu).")
    xqp: float = Field(..., gt=0, description="Transient reactance Xq' (pu).")
    xdpp: float = Field(..., gt=0, description="Subtransient reactance Xd'' (pu).")
    xqpp: float = Field(..., gt=0, description="Subtransient reactance Xq'' (pu).")
    xl: float = Field(..., gt=0, description="Leakage reactance Xl (pu).")
    td0p: float = Field(..., gt=0, description="Open-circuit transient time constant Td0' (s).")
    tq0p: float = Field(
        ...,
        ge=0,
        description=(
            "Open-circuit transient time constant Tq0' (s); zero is a valid "
            "source-preserved value when the q-axis transient state is absent."
        ),
    )
    ra: float = Field(0.0, ge=0, description="Armature resistance (pu).")
    td0pp: float | None = Field(
        None,
        ge=0,
        description="Open-circuit subtransient time constant Td0'' (s), when declared.",
    )
    tq0pp: float | None = Field(
        None,
        ge=0,
        description="Open-circuit subtransient time constant Tq0'' (s), when declared.",
    )
    powerfactory_xstr: float | None = Field(
        None,
        ge=0,
        description=(
            "PowerFactory TypSym.xstr, carried verbatim under its source name "
            "because CEPT does not claim to know its general meaning. On the "
            "Nine-bus machines it is the reactance the source's own rotor "
            "angle is measured behind -- back-solving the initial angle "
            "returns 0.15047, 0.23000 and 0.23217 against declared values of "
            "0.15048, 0.230016 and 0.232064 -- and dropping it left a rebuilt "
            "type at the dialog default of 0.2."
        ),
    )
    salient_pole: bool = Field(
        False,
        description=(
            "True when the source declares a salient-pole rotor "
            "(PowerFactory TypSym.iturbo = 0). Such a machine has no q-axis "
            "transient state, so Xq' is degenerate with Xq."
        ),
    )

    @model_validator(mode="after")
    def _reactances_decrease(self) -> "GenrouDynamics":
        """Xd >= Xd' >= Xd'' and Xq >= Xq' >= Xq'', which physics requires.

        Flux cannot change instantaneously, so each faster subtransient path
        presents a smaller reactance.  Without this check a value the source
        never meant as data reached the engine unexamined: the Nine-bus G1 is
        salient pole, so PowerFactory leaves its q-axis transient reactance at
        the dialog default of 0.3 while Xq is 0.2398, and PowerFactory itself
        then refused the rebuilt machine with "xq must be greater than xq'".
        Catching it here names the Case as the thing that is wrong.
        """
        for axis, (sync, trans, subtrans) in {
            "d": (self.xd, self.xdp, self.xdpp),
            "q": (self.xq, self.xqp, self.xqpp),
        }.items():
            if not (sync >= trans >= subtrans):
                raise ValueError(
                    f"{axis}-axis reactances must satisfy "
                    f"X{axis} >= X{axis}' >= X{axis}''; got "
                    f"{sync} >= {trans} >= {subtrans}"
                )
        return self


class WECCCompositeSpec(BaseModel):
    """Explicit PowerFactory WECC composite-frame parameters."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["pv", "type3", "type4"] = "pv"
    regc_a: dict[str, float] = Field(default_factory=dict)
    reec_a: dict[str, float] = Field(default_factory=dict)
    repc_a: dict[str, float] = Field(default_factory=dict)
    auxiliary: dict[str, dict[str, float]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _required_frames(self) -> "WECCCompositeSpec":
        missing = [
            name
            for name, values in (("REGC_A", self.regc_a), ("REEC_A", self.reec_a), ("REPC_A", self.repc_a))
            if not values
        ]
        if missing:
            raise ValueError("WECC composite spec requires explicit parameters for " + ", ".join(missing))
        return self


class InlineDCBus(BaseModel):
    """A DC busbar (PowerFactory ``ElmTerm`` with ``systype=1`` DC system).

    Carried separately from AC buses so the two systems can never share a
    name: the adapters build DC terminals with the DC system flag and refuse
    a collision fail-closed.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    kv: float = Field(..., gt=0, description="Nominal DC pole-to-pole voltage (kV).")


class InlineConverter(BaseModel):
    """A voltage-source converter between one AC bus and a DC bus pair.

    Engine-neutral form of PowerFactory ``ElmVsc`` (bipolar, ``dc_minus_bus``
    set) / ``ElmVscmono`` (monopolar, ``dc_minus_bus`` omitted). Proven
    load-flow control mapping on PowerFactory 2023 SP1 (T-038 probes):
    ``following`` selects the PQ-following ``i_acdc`` mode (no angle or AC
    voltage control, converges alongside an AC slack), ``forming`` keeps the
    factory ``Vac-phi`` default (controls angle; fails closed at the solver
    when a second angle reference is present). OpenDSS has no VSC/DC model
    and refuses a network containing converters.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    bus: str = Field(..., min_length=1, description="AC point of connection.")
    dc_plus_bus: str = Field(..., min_length=1, description="DC positive-pole bus.")
    dc_minus_bus: Optional[str] = Field(
        None, description="DC negative-pole bus; omitted selects the monopolar form."
    )
    mva: float = Field(..., gt=0, description="Rated apparent power (MVA).")
    ac_kv: float = Field(..., gt=0, description="Rated AC line-to-line voltage (kV).")
    dc_kv: float = Field(..., gt=0, description="Rated DC pole-to-pole voltage (kV).")
    control: Literal["following", "forming"] = Field(
        "following",
        description="Load-flow/EMT control posture; see class notes for the native mapping.",
    )
    p_mw: float = Field(0.0, description="Active-power setpoint, AC to DC positive (MW).")
    q_mvar: float = Field(0.0, description="Reactive-power setpoint (Mvar).")


class InlineDCSource(BaseModel):
    """A DC voltage-holding source (PowerFactory ``ElmDcu`` battery unit).

    The element that balances a DC island in load flow, mirroring the AC
    external grid/slack requirement: a DC island with a converter in
    ``following`` mode and no DC source has no voltage reference and the
    native solver leaves it de-energized or fails to converge.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    bus: str = Field(..., min_length=1, description="DC bus this source holds.")
    kv: float = Field(..., gt=0, description="Nominal DC voltage (kV).")
    voltage_setpoint_pu: float = Field(..., gt=0, description="Held DC voltage (pu of kv).")


class InlineDCLoad(BaseModel):
    """A constant-power DC load (PowerFactory ``ElmLoddc``)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    bus: str = Field(..., min_length=1, description="DC bus this load draws from.")
    kw: float = Field(..., ge=0, description="Active power demand (kW).")

class InlineSwitch(BaseModel):
    name: str
    bus1: str
    bus2: str
    closed: bool = True


class InlineTerminalSwitch(BaseModel):
    """A source switch installed at one terminal of a branch."""

    name: str
    element: str
    bus: str
    closed: bool = True


class InlineGenerator(BaseModel):
    name: str
    bus: str
    bus_type: Literal["slack", "pv", "pq"] = "pv"
    mva: float = Field(..., gt=0, description="Rated apparent power.")
    mva_basis: Literal["rated_apparent_power", "machine_base"] = Field(
        "rated_apparent_power",
        description=(
            "Meaning of mva. machine_base is a cited per-machine solver base, "
            "not a nameplate rating; it is PowerFactory load-flow-only."
        ),
    )
    kv: float = Field(..., gt=0, description="Rated (nominal) voltage.")
    rated_power_factor: Optional[float] = Field(
        None,
        gt=0,
        le=1,
        description=(
            "Source-disclosed generator nominal power factor (cosn). "
            "Kept separate from operating pf so dynamic p.u. bases remain auditable."
        ),
    )
    pu: float = Field(1.0, gt=0, description="Voltage setpoint, used when bus_type in {slack, pv}.")
    kw: float = Field(0.0, description="Scheduled active power output.")
    q_mvar: Optional[float] = Field(
        None, description="Optional explicit reactive power setpoint/output (MVar)."
    )
    control_mode: Optional[Literal["pq", "pv", "slack", "droop_pinned"]] = Field(
        None, description="Detailed control mode, e.g. droop_pinned for source-snapshot pinned generators."
    )
    reference_machine: Optional[bool] = Field(
        None,
        description=(
            "Source-disclosed PowerFactory reference-machine flag (ip_ctrl). "
            "None means the adapter may use its reviewed fallback."
        ),
    )
    powerfactory_iv_mode: Optional[int] = Field(
        None,
        ge=0,
        le=1,
        description=(
            "Source-disclosed PowerFactory voltage-initialisation/regulator mode "
            "(ElmSym.iv_mode). None means the source did not expose it."
        ),
    )
    parallel_units: int = Field(1, ge=1, description="Source-declared number of parallel machine units.")
    pf: float = Field(1.0, gt=-1.0, le=1.0, description="Power factor, used when bus_type == 'pq'.")
    xdpp_pu: float = Field(
        0.20,
        gt=0,
        description="Subtransient reactance on machine base, used for short-circuit. "
        "A typical value is assumed unless project-specific machine data is supplied.",
    )
    dynamics: Optional[GenrouDynamics] = Field(
        None,
        description="GENROU RMS-dynamics parameters. Required on any generator "
        "involved in a study.type='dynamics' run against PowerFactory.",
    )
    wecc: Optional[WECCCompositeSpec] = Field(
        None, description="PowerFactory WECC composite dynamic model parameters."
    )


class InlineShunt(BaseModel):
    """A shunt capacitor or reactor connected at one bus.

    Both engines model this natively -- OpenDSS as ``Capacitor``/``Reactor``,
    PowerFactory as ``ElmShnt`` -- so it is represented rather than blocked.
    The rating is the source's per-step nominal reactive power at nominal voltage;
    a capacitor is positive Mvar and a reactor negative, which is the sign
    convention both engines use for a shunt's rating.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    bus: str = Field(..., min_length=1)
    q_mvar: float = Field(
        ...,
        description="Per-step nominal reactive power at nominal voltage: positive for a "
        "capacitor, negative for a reactor.",
    )
    kv: Optional[float] = Field(None, gt=0, description="Rated voltage; defaults to the bus nominal.")
    steps: int = Field(1, ge=1, description="Number of switchable steps the source declares.")
    steps_in_service: Optional[int] = Field(
        None,
        ge=0,
        description="Steps actually connected; omitted means all declared steps.",
    )
    powerfactory_input_mode: Optional[Literal["DEF", "LAY"]] = Field(
        None,
        description="Source ElmShnt.mode_inp when the bank uses PowerFactory's layout model.",
    )
    powerfactory_bcap: Optional[float] = Field(
        None,
        ge=0,
        description="Source ElmShnt.bcap input, retained verbatim for LAY mode.",
    )
    series_reactor_r_ohm: Optional[float] = Field(None, ge=0)
    series_reactor_x_ohm: Optional[float] = Field(None, ge=0)
    in_service: bool = True
    phase_nodes: Optional[list[int]] = Field(
        None,
        description=(
            "Phases this bank is connected to, as 1-based node numbers. A "
            "single-phase capacitor on a single-phase lateral is not a "
            "three-phase bank at a third of the rating."
        ),
    )


class InlineNetwork(BaseModel):
    """Engine-neutral structured network — the same definition is buildable
    in both the OpenDSS and PowerFactory adapters, which is what makes
    cross-engine validation (:mod:`cept.verification.comparison.cross_engine`) possible.

    Deliberately scoped to positive/zero-sequence balanced data: enough for
    load-flow and short-circuit studies, not a replacement for a detailed
    unbalanced OpenDSS feeder model (use ``kind='dss_file'``/``'builtin'``
    for that).
    """

    buses: list[InlineBus]
    lines: list[InlineLine] = Field(default_factory=list)
    transformers: list[InlineTransformer] = Field(default_factory=list)
    three_winding_transformers: list[InlineThreeWindingTransformer] = Field(
        default_factory=list,
        description="Three-winding transformers, kept separate from the two-winding list.",
    )
    loads: list[InlineLoad] = Field(default_factory=list)
    external_grids: list[InlineExternalGrid] = Field(default_factory=list)
    generators: list[InlineGenerator] = Field(default_factory=list)
    shunts: list[InlineShunt] = Field(default_factory=list)
    converters: Optional[list[InlineConverter]] = Field(
        None,
        description="Voltage-source converters (AC/DC). None when the study has none.",
    )
    dc_buses: Optional[list[InlineDCBus]] = Field(
        None, description="DC busbars referenced by converters, sources, and DC loads."
    )
    dc_sources: Optional[list[InlineDCSource]] = Field(
        None, description="DC voltage-holding sources (one per balanced DC island)."
    )
    dc_loads: Optional[list[InlineDCLoad]] = Field(
        None, description="Constant-power DC loads."
    )
    switches: list[InlineSwitch] = Field(default_factory=list)
    terminal_switches: list[InlineTerminalSwitch] = Field(default_factory=list)
    open_elements: list[str] = Field(
        default_factory=list,
        description="Source-resolved elements de-energized by an open terminal switch.",
    )
    load_flow_voltage_dependency: Optional[bool] = Field(
        None,
        description=(
            "Reviewed decision to apply the declared voltage-dependent load model. "
            "None means the source did not expose a setting with proven semantics."
        ),
    )
    load_flow_automatic_tap: Optional[bool] = None
    load_flow_automatic_shunt: Optional[bool] = None
    load_flow_node_tolerance_kva: Optional[float] = Field(
        None,
        description=(
            "Source-declared maximum acceptable node power mismatch "
            "(PowerFactory ComLdf.errlf, kVA). A rebuild that solves with a "
            "different criterion is not comparable to the source on channels "
            "whose magnitude falls inside this band: the source reports its "
            "own remaining mismatch there as if it were an injection. "
            "None means the source did not disclose a criterion."
        ),
    )
    load_flow_equation_tolerance_pct: Optional[float] = Field(
        None,
        description=(
            "Source-declared maximum acceptable model-equation error "
            "(PowerFactory ComLdf.erreq, percent). Carried for the same "
            "reason as load_flow_node_tolerance_kva."
        ),
    )
    sld_layout: Optional[dict[str, tuple[float, float]]] = Field(
        None,
        description=(
            "Optional explicit SLD placement: bus name -> (x, y) grid "
            "position (+y is up). Bus orientation is inferred from topology "
            "and may be horizontal or vertical. When present it "
            "overrides the automatic diagram layout; connections are still "
            "routed orthogonally. Buses not listed fall back to automatic "
            "placement."
        ),
    )

    @model_serializer(mode="wrap")
    def _omit_absent_load_flow_tolerances(
        self, handler: SerializerFunctionWrapHandler, info: SerializationInfo
    ) -> Any:
        # Additive tuning fields (source-declared PF load-flow criteria) and
        # the VSC/DC lane (T-038): omit them when not provided so fingerprints
        # and stored case.json for existing studies never move (same contract
        # as StudySpec intent / InlineBus phase technology).  Values that
        # differ from None are still serialized, so a set criterion or a
        # declared converter is preserved.
        payload = handler(self)
        if isinstance(payload, dict):
            for key in (
                "load_flow_node_tolerance_kva",
                "load_flow_equation_tolerance_pct",
                "converters",
                "dc_buses",
                "dc_sources",
                "dc_loads",
            ):
                if payload.get(key) is None:
                    payload.pop(key, None)
        return payload

    def is_unbalanced(self) -> bool:
        """True when this network cannot be described in positive sequence.

        A feeder with a single- or two-phase lateral, an untransposed line
        matrix, or per-phase load has no balanced equivalent -- solving it in
        positive sequence returns zeros for the very channels a comparison
        reads.  Both engines ask the Case rather than each carrying its own
        assumption about the representation.
        """
        if any(bus.phases != 3 for bus in self.buses):
            return True
        for line in self.lines:
            if line.matrix is not None:
                return True
            if line.phase_nodes is not None and len(line.phase_nodes) != 3:
                return True
        for load in self.loads:
            if load.kw_per_phase is not None:
                return True
            if load.phase_nodes is not None and len(load.phase_nodes) != 3:
                return True
        for shunt in self.shunts:
            if shunt.phase_nodes is not None and len(shunt.phase_nodes) != 3:
                return True
        for transformer in self.transformers:
            if transformer.phase_nodes is not None and len(transformer.phase_nodes) != 3:
                return True
        return False

    @model_validator(mode="after")
    def _check_refs(self) -> "InlineNetwork":
        names = {b.name for b in self.buses}
        if len(names) != len(self.buses):
            raise ValueError("InlineNetwork bus names must be unique")

        def _check(bus: str, where: str) -> None:
            if bus not in names:
                raise ValueError(f"{where} references unknown bus '{bus}'")

        kv_by_bus = {b.name: b.kv for b in self.buses}
        for sw in self.switches:
            _check(sw.bus1, f"switch '{sw.name}'.bus1")
            _check(sw.bus2, f"switch '{sw.name}'.bus2")
        element_names = (
            {line.name for line in self.lines}
            | {transformer.name for transformer in self.transformers}
            | {load.id for load in self.loads}
            | {generator.name for generator in self.generators}
            | {grid.name for grid in self.external_grids}
        )
        if len(set(self.open_elements)) != len(self.open_elements):
            raise ValueError("InlineNetwork.open_elements must be unique")
        branch_names = {line.name for line in self.lines} | {tr.name for tr in self.transformers}
        for sw in self.terminal_switches:
            _check(sw.bus, f"terminal switch '{sw.name}'.bus")
            if sw.element not in branch_names:
                raise ValueError(f"terminal switch '{sw.name}' references unknown branch '{sw.element}'")
        unknown_open = sorted(set(self.open_elements) - element_names)
        if unknown_open:
            raise ValueError(
                "InlineNetwork.open_elements references unknown elements: " + ", ".join(unknown_open)
            )
        for ln in self.lines:
            _check(ln.from_bus, f"line '{ln.name}'.from_bus")
            _check(ln.to_bus, f"line '{ln.name}'.to_bus")
            if ln.from_bus in kv_by_bus and ln.to_bus in kv_by_bus:
                kv1, kv2 = kv_by_bus[ln.from_bus], kv_by_bus[ln.to_bus]
                if abs(kv1 - kv2) > 1e-6:
                    raise ValueError(
                        f"line '{ln.name}' connects buses at different nominal "
                        f"voltages ({kv1} kV vs {kv2} kV) — a line can't bridge "
                        "voltage levels; use a transformer instead. This exact "
                        "mistake produced a severe voltage-collapse artifact in "
                        "examples/qsts_inline/case.json before it was caught."
                    )
        for tr in self.transformers:
            _check(tr.hv_bus, f"transformer '{tr.name}'.hv_bus")
            _check(tr.lv_bus, f"transformer '{tr.name}'.lv_bus")
        for ld in self.loads:
            _check(ld.bus, f"load '{ld.id}'.bus")
        for eg in self.external_grids:
            _check(eg.bus, f"external_grid '{eg.name}'.bus")
        for g in self.generators:
            _check(g.bus, f"generator '{g.name}'.bus")
        dc_names = {b.name for b in self.dc_buses or []}
        if len(dc_names) != len(self.dc_buses or []):
            raise ValueError("InlineNetwork DC bus names must be unique")
        if names & dc_names:
            clash = sorted(names & dc_names)
            raise ValueError(
                "InlineNetwork AC and DC buses must not share names: " + ", ".join(clash)
            )

        def _check_dc(bus: str, where: str) -> None:
            if bus not in dc_names:
                raise ValueError(f"{where} references unknown DC bus '{bus}'")

        for c in self.converters or []:
            _check(c.bus, f"converter '{c.name}'.bus")
            _check_dc(c.dc_plus_bus, f"converter '{c.name}'.dc_plus_bus")
            if c.dc_minus_bus is not None:
                _check_dc(c.dc_minus_bus, f"converter '{c.name}'.dc_minus_bus")
        for s in self.dc_sources or []:
            _check_dc(s.bus, f"dc_source '{s.name}'.bus")
        for d in self.dc_loads or []:
            _check_dc(d.bus, f"dc_load '{d.name}'.bus")
        if not self.external_grids and not any(g.bus_type == "slack" for g in self.generators):
            raise ValueError(
                "InlineNetwork needs at least one external_grid or a slack generator as a voltage reference."
            )
        if self.sld_layout:
            for bus_name in self.sld_layout:
                _check(bus_name, "sld_layout")
        return self
