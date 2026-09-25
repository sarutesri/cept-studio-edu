"""OpenDSS adapter â€” network loading and inline compilation."""

from __future__ import annotations

import importlib
import math
import re
from pathlib import Path

from cept.schema.case import NetworkSpec

from cept.adapters.opendss.utils import (
    BUILTIN,
    aggregate_parallel_generator,
    dss_object,
    dss_quote,
    dss_safe_label,
    inline_load_model_options,
    transformer_core_options,
)


def inline_asset_identities(net: NetworkSpec) -> list:
    """Declare the OpenDSS object name this module gives each Case asset.

    This lives beside :func:`compile_inline` on purpose: the module that emits
    ``New Line.<name>`` is the only one entitled to say what that object is
    called.  The comparator reads the resulting identity map instead of
    recovering the class prefix with a regular expression, which is how a
    39-bus run once turned 272 comparable branch rows into blockers.
    """
    from cept.semantics.identity import AssetIdentity

    inline = net.inline
    if inline is None:
        return []
    # ``New Circuit.X`` gives OpenDSS a source element always named
    # ``Vsource.source``, whatever the circuit is called.  Whichever object
    # became the circuit therefore answers to that name and not to its own --
    # declaring otherwise sends the comparator looking for an element that
    # does not exist.  See compile_inline: the first external grid becomes the
    # circuit, or the slack machine does when there is no external grid.
    first_grid = inline.external_grids[0] if inline.external_grids else None
    slack = next((gen for gen in inline.generators if gen.bus_type == "slack"), None)
    circuit_owner = first_grid.name if first_grid is not None else (slack.name if slack is not None else None)
    slack_generators = {gen.name for gen in inline.generators if gen.bus_type == "slack"}
    identities: list[AssetIdentity] = []

    def declare(canonical_id: str, canonical_class: str, native: str) -> None:
        name = str(canonical_id or "")
        if not name:
            return
        # Emission substitutes characters OpenDSS's parser cannot carry, so the
        # name the engine reports back is the substituted one.  Declare that as
        # the native name, or the comparator looks up an element that does not
        # exist under the spelling it was given.
        native = dss_safe_label(native)
        # OpenDSS object names are case-insensitive and are reported back
        # lowercased, so the lowercase spelling is a second name the engine
        # genuinely uses for this object.  Declaring it here keeps the
        # comparator's lookup exact rather than case-folding at join time,
        # which is the kind of normalization that cannot prove a pairing.
        aliases = tuple(
            dict.fromkeys(alias for alias in (name.lower(), native.lower()) if alias not in {name, native})
        )
        identities.append(
            AssetIdentity(
                canonical_id=name,
                canonical_class=canonical_class,
                engine_native_name=native,
                # A PFD-derived Case keeps the source element name verbatim
                # (see cept/convert/pfd.py), so the canonical id is also the
                # name the source result artifact uses.
                source_name=name,
                aliases=aliases,
            )
        )

    for bus in inline.buses:
        declare(bus.name, "bus", str(bus.name))
    for line in inline.lines:
        declare(line.name, "line", f"Line.{line.name}")
    for transformer in inline.transformers:
        declare(transformer.name, "transformer", f"Transformer.{transformer.name}")
    for transformer in getattr(inline, "three_winding_transformers", None) or []:
        declare(transformer.name, "three_winding_transformer", f"Transformer.{transformer.name}")
    for load in inline.loads:
        declare(load.id, "load", f"Load.{load.id}")
    for gen in inline.generators:
        # A slack machine is emitted as a Vsource, not a Generator; and when it
        # is the circuit's own source that Vsource is named ``source``.
        if gen.name == circuit_owner:
            native = "Vsource.source"
        elif gen.name in slack_generators:
            native = f"Vsource.{gen.name}"
        else:
            native = f"Generator.{gen.name}"
        declare(gen.name, "sync_generator", native)
    for grid in inline.external_grids:
        native = "Vsource.source" if grid.name == circuit_owner else f"Vsource.{grid.name}"
        declare(grid.name, "external_grid", native)
    for shunt in getattr(inline, "shunts", None) or []:
        native = "Capacitor" if shunt.q_mvar >= 0 else "Reactor"
        declare(shunt.name, "shunt", f"{native}.{shunt.name}")
    return identities


# A vector-group clock is a phase shift of ``clock * 30`` degrees lagging.
# OpenDSS gives two independent devices for it: ``leadlag`` supplies the
# +/-30 degree delta-wye shift, and permuting the winding's node list supplies a
# 120 or 240 degree rotation.  Every clock reachable by combining them is
# therefore expressible exactly; the rest need three single-phase units.
#   clock -> (node permutation, leadlag)
_CLOCK_WINDING_REFS: dict[int, tuple[tuple[int, int, int], str | None]] = {
    0: ((1, 2, 3), None),  #   0
    1: ((1, 2, 3), "lag"),  #  +30
    3: ((3, 1, 2), "lead"),  # 120 - 30
    5: ((3, 1, 2), "lag"),  # 120 + 30
    7: ((2, 3, 1), "lead"),  # 240 - 30
    9: ((2, 3, 1), "lag"),  # 240 + 30
    11: ((1, 2, 3), "lead"),  #  -30
}


def _winding_ref(bus: str, clock: int, owner: str) -> tuple[str, str | None]:
    """Return the OpenDSS bus reference and leadlag for one winding's clock."""
    entry = _CLOCK_WINDING_REFS.get(clock % 12)
    if entry is None:
        raise ValueError(
            f"OpenDSS inline transformer '{owner}' has unsupported vector-group clock "
            f"{clock % 12}; use a reviewed three-single-phase transformer mapping."
        )
    nodes, leadlag = entry
    return f"{bus}." + ".".join(str(n) for n in nodes), leadlag


def _transformer_phase_refs(tr) -> tuple[str, str, str | None]:
    """Map the source vector-group clock to OpenDSS winding references.

    Expressing the rotation on the LV terminal keeps the solved bus frame
    source-driven instead of post-processing angles.
    """
    group = (getattr(tr, "vector_group", None) or "Ynyn0").strip()
    match = re.fullmatch(r"(?:YN|Y|D|ZN|Z)(?:yn|y|d|zn|z)(\d{1,2})", group)
    hv = f"{tr.hv_bus}.1.2.3"
    if match is None:
        return hv, f"{tr.lv_bus}.1.2.3", None
    lv, leadlag = _winding_ref(tr.lv_bus, int(match.group(1)), tr.name)
    return hv, lv, leadlag


# OpenDSS builds a transformer's primitive admittance from its leakage
# reactance, so a genuinely ideal ratio changer (a step voltage regulator,
# uk=0) makes that admittance singular: the winding conducts nothing and every
# bus behind it collapses.  On IEEE 13-node, RG60 sat at 0.0155 pu with xhl=0
# and at 0.94 pu with 0.01.  The floor is the value OpenDSS's own IEEE 13-node
# model uses for its regulators, and it is applied only when the source says
# zero -- never to a declared impedance.
_IDEAL_TRANSFORMER_XHL_PCT = 0.01


def ideal_ratio_changer_approximations(inline) -> list[dict[str, object]]:
    """Declare the leakage-reactance floor applied to ideal ratio changers.

    A step voltage regulator declares uk=0, and the floor that keeps OpenDSS's
    admittance non-singular necessarily produces a small reactive loss where
    the source reports essentially zero.  Stating it here means the comparison
    can attribute that difference rather than reporting the row as an
    unexplained block; it does not change any tolerance.
    """
    out: list[dict[str, object]] = []
    for tr in getattr(inline, "transformers", []) or []:
        if float(getattr(tr, "uk_pct", 0.0) or 0.0) > 0:
            continue
        out.append(
            {
                "asset": tr.name,
                "quantity": "reactive_loss",
                "source_value": 0.0,
                "applied_value": _IDEAL_TRANSFORMER_XHL_PCT,
                "reason": (
                    "the source declares an ideal ratio changer (uk=0); OpenDSS "
                    "builds a transformer's admittance from its leakage reactance, "
                    f"so xhl was floored at {_IDEAL_TRANSFORMER_XHL_PCT}%, which "
                    "produces a small reactive loss the source reports as zero"
                ),
                "provenance": (
                    "the value OpenDSS's own IEEE 13-node model uses for its "
                    "regulators; measured alternatives 0.001 and 0.0001 shrink the "
                    "reactive loss without changing any verdict"
                ),
            }
        )
    return out


def _dss_matrix(rows: list[list[float]]) -> str:
    """Render a symmetric matrix in OpenDSS's lower-triangle ``|`` notation."""
    return (
        "[" + " | ".join(" ".join(f"{rows[i][j]:.10g}" for j in range(i + 1)) for i in range(len(rows))) + "]"
    )


def matrix_line_commands(line, frequency_hz: float, open_elements: set[str]) -> list[str]:
    """Build the OpenDSS commands for one untransposed line.

    Returned as text rather than executed so the live adapter and the exported
    portable package emit the identical pair of commands -- the package a
    reviewer runs by hand cannot drift from the run CEPT reported.
    """
    r, x, c = line.matrix.phase_matrices()
    phases = line.matrix.phases
    nodes = line.phase_nodes or list(range(1, phases + 1))
    code = f"lc_{line.name}"
    # Case matrices store capacitance in uF/km while OpenDSS LineCode.Cmatrix
    # is specified in nF per declared length unit.  R/X already share the
    # ohm/km basis, but capacitance therefore needs the explicit 1000x unit
    # conversion.  Passing the Case value verbatim silently under-states line
    # charging by three orders of magnitude on every matrix line.
    c_nf_per_km = [[value * 1000.0 for value in row] for row in c] if c is not None else None
    capacitance = f" cmatrix={_dss_matrix(c_nf_per_km)}" if c_nf_per_km is not None else ""
    suffix = "." + ".".join(str(node) for node in nodes)
    normamps = f" normamps={line.normal_amps}" if line.normal_amps else ""
    return [
        f"New {dss_object('LineCode', code)} nphases={phases} baseFreq={frequency_hz} "
        f"rmatrix={_dss_matrix(r)} xmatrix={_dss_matrix(x)}{capacitance} units=km",
        f"New {dss_object('Line', line.name)} "
        f"bus1={dss_quote(line.from_bus + suffix)} bus2={dss_quote(line.to_bus + suffix)} "
        f"linecode={dss_quote(code)} phases={phases} "
        f"length={line.adapter_length_km} units=km{normamps}"
        + (" enabled=n" if line.name in open_elements else ""),
    ]


def _emit_matrix_line(dss, line, net: NetworkSpec, inline) -> None:
    """Run the shared matrix-line commands against the live OpenDSS context.

    OpenDSS's ``Rmatrix``/``Xmatrix``/``Cmatrix`` take exactly the phase-domain
    form the Case carries once the neutral is eliminated, so the unequal mutuals
    of an untransposed feeder survive intact -- collapsing them to r1/x1 would
    describe a different, balanced line.  ``phase_nodes`` decides which physical
    phases the branch lands on, which is what makes a two-phase lateral a
    two-phase lateral rather than a generic two-wire branch.
    """
    for command in matrix_line_commands(
        line, float(net.frequency_hz), set(getattr(inline, "open_elements", []) or [])
    ):
        dss.Text.Command(command)


def load_commands(
    load,
    bus_kv_ll: float,
    model: int,
    load_options: str,
    disabled: str,
    kvar: float,
) -> list[str]:
    """Build the OpenDSS Load commands for one Case load.

    A balanced three-phase load is one object.  An unbalanced one is not: each
    energised phase carries its own kW and kvar, and a phase-to-phase load sees
    the line voltage rather than the phase-to-neutral voltage.  Emitting one
    object per phase is what keeps those distinct -- a single three-phase Load
    would divide the total equally and put a delta load on the wrong voltage.
    """
    nodes = load.phase_nodes
    if not nodes or (len(nodes) == 3 and load.connection == "wye" and not load.kw_per_phase):
        return [
            f"New {dss_object('Load', load.id)} bus1={dss_quote(load.bus)} phases=3 "
            f"kV={bus_kv_ll} kW={load.kw} kvar={kvar} model={model}{load_options}{disabled}"
        ]
    kw_split = load.kw_per_phase or [load.kw / len(nodes)] * len(nodes)
    kvar_split = load.kvar_per_phase or [kvar / len(nodes)] * len(nodes)
    delta = load.connection == "delta"
    commands: list[str] = []
    if delta and len(nodes) == 3:
        # A three-phase delta load is one object across all three phases.
        return [
            f"New {dss_object('Load', load.id)} "
            f"bus1={dss_quote(load.bus + '.1.2.3')} phases=3 conn=delta "
            f"kV={bus_kv_ll} kW={sum(kw_split)} kvar={sum(kvar_split)} "
            f"model={model}{load_options}{disabled}"
        ]
    for index, node in enumerate(nodes):
        kw_value, kvar_value = kw_split[index], kvar_split[index]
        if kw_value == 0.0 and kvar_value == 0.0:
            # A phase the source left empty is not a load; creating a zero one
            # would still add an element to the comparator's inventory.
            continue
        if delta:
            other = nodes[(index + 1) % len(nodes)]
            reference = f"{load.bus}.{node}.{other}"
            kv_value = bus_kv_ll
        else:
            reference = f"{load.bus}.{node}"
            kv_value = bus_kv_ll / math.sqrt(3)
        name = load.id if len(nodes) == 1 else f"{load.id}_{node}"
        commands.append(
            f"New {dss_object('Load', name)} bus1={dss_quote(reference)} phases=1 "
            f"conn={'delta' if delta else 'wye'} kV={kv_value} "
            f"kW={kw_value} kvar={kvar_value} model={model}{load_options}{disabled}"
        )
    return commands


def transformer_tap_winding(tr) -> int:
    """Which OpenDSS winding carries the Case ratio: 1 (HV) or 2 (LV).

    PowerFactory's ``TypTr2.tap_side`` names the winding the changer acts on:
    0 is the HV winding, 1 the LV winding.  CEPT builds the HV terminal as
    winding 1, so the mapping is direct -- and it used to be inverted, which
    took IEEE-39's transmission side to about 0.918 pu instead of raising it.

    Measured on a two-bus fixture across step-up/step-down, ``tap_side`` 0/1,
    tap positions -2/0/+2 and both power directions (24 points, WP4 matrix in
    ``workspace/root-cause-holdouts/wp4-tap-characterization/matrix.json``).
    With the tap on the HV winding at nntap=-2 PowerFactory settles the LV
    terminal at 1.0515 pu -- lowering the tapped winding's rated voltage
    lowers the turns ratio and raises the other terminal.  The inverted
    mapping returned 0.9489 pu, an error of 0.103 pu in the wrong direction.

    A Case with no ``powerfactory_tap_side`` did not come from PowerFactory --
    IEEE 13-node's regulators are the worked example -- and keeps the
    OpenDSS-native convention of a boost on the LV winding.  That default is
    unchanged; only the two explicitly declared sides were wrong.
    """
    side = getattr(tr, "powerfactory_tap_side", None)
    if side is None:
        return 2
    return 1 if int(side) == 0 else 2


def transformer_tap_option(tr) -> str:
    """The OpenDSS ``taps=(...)`` clause for a two-winding transformer."""
    if transformer_tap_winding(tr) == 1:
        return f"taps=({tr.tap_pu} 1.0)"
    return f"taps=(1.0 {tr.tap_pu})"


class EngineFidelityError(RuntimeError):
    """The compiled circuit is not the Case that describes it."""

    def __init__(self, rows: list[dict]):
        self.rows = rows
        detail = "\n".join(
            f"  - {r['engine_attribute']}: Case {r['written_value']!r}, "
            f"engine {r['read_back_value']!r} [{r['defect_class']}] {r['reason']}"
            for r in rows
        )
        super().__init__(
            "BLOCKED: the compiled OpenDSS circuit does not match the typed "
            f"Case in {len(rows)} reviewed input(s), so no solver result from "
            "it can be compared to anything:\n" + detail
        )


def enforce_readback(dss, net: NetworkSpec) -> list[dict]:
    """Fail closed when the engine holds something other than the Case.

    This is deliberately not downgradable by ``--allow-warning``: a warning
    tolerance exists for study conditions the engineer accepts, and "the model
    I solved is not the model I described" is not one of them.
    """
    readback = importlib.import_module("cept.adapters.opendss.readback")
    rows = readback.readback_rows(dss, net)
    failures = [r for r in rows if r.get("status") == "fail"]
    if failures:
        raise EngineFidelityError(failures)
    return rows


def load_network(
    dss,
    net: NetworkSpec,
    work_dir: str,
    *,
    regulate_slack_terminal: bool = False,
    user_model=None,
    user_model_initial_states: dict[str, dict[str, float]] | None = None,
) -> None:
    """Load a network into the OpenDSS context.

    ``regulate_slack_terminal`` mirrors PowerFactory load-flow slack semantics:
    an external grid in SL (reference) mode holds its *terminal* bus at the
    voltage setpoint, and its short-circuit strength only enters fault studies.
    OpenDSS models a ``Vsource`` as an EMF behind the short-circuit impedance,
    so a finite ``MVAsc`` makes the terminal sag under load.  For power-flow
    studies we present a stiff source so the terminal equals the setpoint;
    fault/protection/dynamics leave the real impedance intact.
    """
    # Resolve file/builtin masters against the caller's working directory before
    # setting OpenDSS DataPath. DSS C-API may change the process cwd when
    # DataPath is assigned; resolving afterwards silently retargeted relative
    # Case paths into CEPT's temporary work directory.
    master = _master_path(net) if net.kind != "inline" else None
    dss.Basic.DataPath(work_dir)
    if net.kind == "inline":
        compile_inline(
            dss,
            net,
            regulate_slack_terminal=regulate_slack_terminal,
            user_model=user_model,
            user_model_initial_states=user_model_initial_states,
        )
        # One gate, one place: every study path reaches the engine through here,
        # so the compiled circuit is checked against the Case it claims to be
        # before any solver runs.  A number produced from a circuit that is not
        # the Case is worse than no number, because it looks like evidence.
        enforce_readback(dss, net)
        return
    assert master is not None
    if not master.exists():
        raise FileNotFoundError(f"OpenDSS master file not found: {master}")
    dss.Text.Command("Clear")
    dss.Text.Command(f'Redirect "{master}"')
    dss.Text.Command("Set MaxControlIter=100")


def _master_path(net: NetworkSpec) -> Path:
    if net.kind == "dss_file":
        p = Path(net.path)  # type: ignore[arg-type]
        return p if p.is_absolute() else Path.cwd() / p
    if net.kind == "builtin":
        key = (net.name or "").lower()
        if key not in BUILTIN:
            raise ValueError(f"Unknown builtin feeder '{net.name}'. Available: {sorted(BUILTIN)}")
        return BUILTIN[key]
    raise NotImplementedError(f"network.kind='{net.kind}' has no master file.")


def compile_inline(
    dss,
    net: NetworkSpec,
    *,
    regulate_slack_terminal: bool = False,
    user_model=None,
    user_model_initial_states: dict[str, dict[str, float]] | None = None,
) -> None:
    """Build an :class:`InlineNetwork` from ``New`` text commands."""
    from cept.domain.sld.engineering_layout import hierarchical_layout

    inline = net.inline
    assert inline is not None
    # T-038: OpenDSS has no VSC/DC model. A converter silently dropped here
    # would leave the solved network missing generation the Case declares,
    # so the compiler refuses instead of approximating.
    if inline.converters or inline.dc_sources or inline.dc_loads or inline.dc_buses:
        raise ValueError(
            "OpenDSS inline compiler cannot represent converters/DC elements; "
            "use engine='powerfactory' for converter studies."
        )

    dss.Text.Command("Clear")
    dss.Text.Command(f"Set DefaultBaseFreq={net.frequency_hz}")
    bus_kv = {b.name: b.kv for b in inline.buses}

    eg = inline.external_grids[0] if inline.external_grids else None
    slack = next((gen for gen in inline.generators if gen.bus_type == "slack"), None)
    if eg is None and slack is None:
        raise ValueError("OpenDSS inline compiler requires an external_grid or slack generator.")
    if eg is not None:
        if regulate_slack_terminal:
            # PowerFactory holds the SL external-grid terminal at the setpoint in
            # load flow; a stiff source makes OpenDSS's terminal match instead of
            # sagging by S/MVAsc behind the short-circuit impedance.
            dss.Text.Command(
                f"New {dss_object('Circuit', f'inline_{eg.name}')} "
                f"basekv={bus_kv[eg.bus]} bus1={dss_quote(eg.bus)} pu={eg.pu} "
                f"angle={eg.angle_deg} MVAsc3=1e9 MVAsc1=1e9"
            )
        else:
            sk1 = eg.sk1_mva if eg.sk1_mva is not None else eg.sk3_mva
            dss.Text.Command(
                f"New {dss_object('Circuit', f'inline_{eg.name}')} "
                f"basekv={bus_kv[eg.bus]} bus1={dss_quote(eg.bus)} pu={eg.pu} angle={eg.angle_deg} "
                f"MVAsc3={eg.sk3_mva} MVAsc1={sk1} x1r1={eg.x_r_ratio} x0r0={eg.x_r_ratio}"
            )
    else:
        dss.Text.Command(
            f"New {dss_object('Circuit', f'inline_{slack.name}')} basekv={bus_kv[slack.bus]} "
            f"bus1={dss_quote(slack.bus)} pu={slack.pu} angle=0 MVAsc3=1e9 MVAsc1=1e9"
        )
    for extra in inline.external_grids[1:]:
        dss.Text.Command(
            f"New {dss_object('Vsource', extra.name)} bus1={dss_quote(extra.bus)} "
            f"basekv={bus_kv[extra.bus]} "
            f"pu={extra.pu} angle={extra.angle_deg} MVAsc3={extra.sk3_mva} "
            f"MVAsc1={extra.sk1_mva or extra.sk3_mva} "
            f"x1r1={extra.x_r_ratio} x0r0={extra.x_r_ratio}"
        )

    terminal_switches = {sw.element: sw for sw in getattr(inline, "terminal_switches", []) if not sw.closed}
    for line in inline.lines:
        if line.uses_total_parameters:
            raise ValueError(
                f"cannot compile total-parameter line '{line.name}'; OpenDSS requires a physical length basis."
            )
        if line.matrix is not None:
            _emit_matrix_line(dss, line, net, inline)
            continue
        r0 = line.r0_for_adapter_ohm_per_km
        x0 = line.x0_for_adapter_ohm_per_km
        if r0 is None:
            r0 = 3.0 * line.r1_for_adapter_ohm_per_km
        if x0 is None:
            x0 = 3.0 * line.x1_for_adapter_ohm_per_km
        assert r0 is not None and x0 is not None
        code = f"lc_{line.name}"
        c1_nf = (
            line.b1_for_adapter_us_per_km * 1000.0 / (2.0 * math.pi * float(net.frequency_hz))
            if line.b1_for_adapter_us_per_km is not None
            else 0.0
        )
        dss.Text.Command(
            f"New {dss_object('LineCode', code)} nphases=3 baseFreq={net.frequency_hz} "
            f"r1={line.r1_for_adapter_ohm_per_km} x1={line.x1_for_adapter_ohm_per_km} r0={r0} x0={x0} "
            f"c1={c1_nf} c0={c1_nf} units=km"
        )
        normamps = f" normamps={line.normal_amps}" if line.normal_amps else ""
        dss.Text.Command(
            f"New {dss_object('Line', line.name)} bus1={dss_quote(line.from_bus)} "
            f"bus2={dss_quote(line.to_bus)} linecode={dss_quote(code)} "
            f"length={line.adapter_length_km} units=km{normamps}"
            + (" enabled=n" if line.name in set(getattr(inline, "open_elements", [])) else "")
        )
        terminal = terminal_switches.get(line.name)
        if terminal is not None and line.name in set(getattr(inline, "open_elements", [])):
            if terminal.bus not in (line.from_bus, line.to_bus):
                raise ValueError(f"terminal switch '{terminal.name}' is not at line '{line.name}' endpoint")
            floating_bus = f"__open_terminal_{line.name}"
            if terminal.bus == line.from_bus:
                line_bus1, line_bus2 = floating_bus, line.to_bus
            else:
                line_bus1, line_bus2 = line.from_bus, floating_bus
            dss.Text.Command(
                f"Edit {dss_object('Line', line.name)} bus1={dss_quote(line_bus1)} "
                f"bus2={dss_quote(line_bus2)} enabled=y"
            )
            dss.Text.Command(
                f"New {dss_object('Line', 'sw_' + terminal.name)} "
                f"bus1={dss_quote(terminal.bus)} bus2={dss_quote(floating_bus)} "
                f"switch=y r1=0.0001 x1=0.0001 enabled=n"
            )

    transformer_bus_refs: dict[str, str] = {}
    # Buses that are the terminal of a delta-connected winding.  A generator or
    # machine sitting on such a bus (a step-up transformer's delta LV, say) must
    # itself be delta-connected: a wye machine on a delta winding is phase-
    # reference-inconsistent and the OpenDSS power flow will not converge even
    # with controls disabled.
    delta_conn_buses: set[str] = set()
    for tr in inline.transformers:
        r_pct, x_pct = tr.series_r_x_pct
        vg = (getattr(tr, "vector_group", None) or "Ynyn0").strip()
        hv_conn = "delta" if vg.startswith("D") or vg.startswith("d") else "wye"
        lv_conn = "delta" if any(c in vg[1:] for c in ("D", "d")) else "wye"
        if hv_conn == "delta":
            delta_conn_buses.add(tr.hv_bus)
        if lv_conn == "delta":
            delta_conn_buses.add(tr.lv_bus)
        hv_ref, lv_ref, leadlag = _transformer_phase_refs(tr)
        # A step voltage regulator bank is three independent single-phase units.
        # Emitting each as a three-phase transformer would apply one unit's tap
        # to all three phases; IEEE 13-node's VregA/B/C sit at taps 10, 8 and 11.
        nodes = getattr(tr, "phase_nodes", None)
        phase_count = 3
        if nodes and len(nodes) < 3:
            phase_count = len(nodes)
            suffix = "." + ".".join(str(node) for node in nodes)
            hv_ref, lv_ref = tr.hv_bus + suffix, tr.lv_bus + suffix
            leadlag = None
        else:
            transformer_bus_refs[tr.lv_bus] = lv_ref
        phase_option = f" leadlag={leadlag}" if leadlag else ""
        core_option = transformer_core_options(tr)
        tap_option = transformer_tap_option(tr)
        dss.Text.Command(
            f"New {dss_object('Transformer', tr.name)} phases={phase_count} windings=2 "
            f"xhl={x_pct if x_pct > 0 else _IDEAL_TRANSFORMER_XHL_PCT} "
            f"%rs=({r_pct / 2.0} {r_pct / 2.0}) "
            f"buses=({dss_quote(hv_ref)} {dss_quote(lv_ref)}) "
            f"kvs=({tr.hv_kv} {tr.lv_kv}) "
            f"kvas=({tr.mva * tr.parallel_units * 1000} {tr.mva * tr.parallel_units * 1000}) "
            # OpenDSS scales a winding's voltage with its own tap (V2 ~ tap2),
            # so a boost belongs on the LV winding.  Putting the source ratio on
            # winding 1 inverted it: IEEE 13-node's regulators are at +6.25%,
            # +5% and +6.875%, and RG60 came out at 0.9411 -- exactly 1/1.0625 --
            # against a source value of 1.0604.
            f"conns=({hv_conn} {lv_conn}) {tap_option}{phase_option}{core_option}"
            + (" enabled=n" if tr.name in set(getattr(inline, "open_elements", [])) else "")
        )

    for tr in getattr(inline, "three_winding_transformers", []) or []:
        # OpenDSS models a three-winding transformer natively as three coupled
        # windings with per-pair reactances.  The Case keeps PowerFactory's star
        # form, so convert once here rather than storing a second derived copy.
        pair = tr.pair_impedances_pct()
        base_kva = tr.base_mva * 1000.0
        conns = " ".join(
            "delta" if str(conn).upper().startswith("D") else "wye"
            for conn in (tr.hv_connection, tr.mv_connection, tr.lv_connection)
        )
        for bus, conn in (
            (tr.hv_bus, tr.hv_connection),
            (tr.mv_bus, tr.mv_connection),
            (tr.lv_bus, tr.lv_connection),
        ):
            if str(conn).upper().startswith("D"):
                delta_conn_buses.add(bus)
        # Each winding carries its own clock, so each gets its own node
        # permutation.  OpenDSS has a single leadlag for the whole transformer;
        # the windings here must therefore agree on it, and a source that needs
        # two different ones is refused rather than silently given one.
        refs, leadlags = [], set()
        for bus, clock in ((tr.hv_bus, tr.hv_clock), (tr.mv_bus, tr.mv_clock), (tr.lv_bus, tr.lv_clock)):
            ref, leadlag = _winding_ref(bus, int(clock), tr.name)
            refs.append(ref)
            if leadlag:
                leadlags.add(leadlag)
        if len(leadlags) > 1:
            raise ValueError(
                f"OpenDSS three-winding transformer '{tr.name}' needs both a leading and a "
                "lagging winding shift, which one OpenDSS Transformer cannot express; "
                "use a reviewed three-single-phase mapping."
            )
        phase_option = f" leadlag={next(iter(leadlags))}" if leadlags else ""
        # Winding resistances are given per winding, on the shared base.
        percent_rs = " ".join(f"{pair[key]}" for key in ("r_hv_pct", "r_mv_pct", "r_lv_pct"))
        dss.Text.Command(
            f"New {dss_object('Transformer', tr.name)} phases=3 windings=3 "
            f"buses=({dss_quote(refs[0])} {dss_quote(refs[1])} {dss_quote(refs[2])}) "
            f"kvs=({tr.hv_kv} {tr.mv_kv} {tr.lv_kv}) "
            f"kvas=({base_kva} {base_kva} {base_kva}) "
            f"conns=({conns}) %rs=({percent_rs}) "
            f"xhl={pair['xhm_pct']} xht={pair['xhl_pct']} xlt={pair['xml_pct']}"
            + phase_option
            + ("" if tr.in_service else " enabled=n")
        )

    for sw in getattr(inline, "switches", []):
        state = "enabled=y" if sw.closed else "enabled=n"
        dss.Text.Command(
            f"New {dss_object('Line', 'sw_' + sw.name)} bus1={dss_quote(sw.bus1)} "
            f"bus2={dss_quote(sw.bus2)} switch=y r1=0.0001 x1=0.0001 {state}"
        )

    for load in inline.loads:
        kvar = round(
            load.q_kvar if load.q_kvar is not None else load.kw * ((1 - load.pf**2) ** 0.5) / load.pf,
            4,
        )
        model, load_options = inline_load_model_options(inline, load)
        disabled = " enabled=n" if load.id in set(getattr(inline, "open_elements", [])) else ""
        for command in load_commands(load, bus_kv[load.bus], model, load_options, disabled, kvar):
            dss.Text.Command(command)

    for gen in inline.generators:
        if gen.mva_basis != "rated_apparent_power":
            raise ValueError(
                f"OpenDSS cannot compile machine-base generator '{gen.name}' without "
                "turning its source MVA base into a rating; use PowerFactory load_flow."
            )
        # PowerFactory carries ``pgini``/``sgn`` per machine and multiplies by
        # ``ngnum``; OpenDSS has no parallel-machine concept, so the units have
        # to be folded into the emitted rating. Measured on the installed
        # 39_Bus_New_England_System.pfd: G 05 has pgini=254 MW with ngnum=2 and
        # PowerFactory's own solution dispatches it at 508.00 MW, while OpenDSS
        # produced 254.05 MW -- exactly half -- and the slack silently absorbed
        # the missing 254 MW.
        gen_kw, gen_mva = aggregate_parallel_generator(gen)
        if gen.bus_type == "slack":
            if eg is None:
                continue
            sk3 = gen_mva / max(gen.xdpp_pu, 1e-3)
            dss.Text.Command(
                f"New {dss_object('Vsource', gen.name)} bus1={dss_quote(gen.bus)} "
                f"basekv={bus_kv[gen.bus]} "
                f"pu={gen.pu} MVAsc3={round(sk3, 3)} MVAsc1={round(sk3, 3)} "
                f"x1r1=10 x0r0=10"
            )
            continue
        pf = gen.pf if gen.bus_type == "pq" else 1.0
        model = "3" if gen.bus_type == "pv" else "1"
        kv = gen.kv
        q_limit = math.sqrt(max((gen_mva * 1000.0) ** 2 - gen_kw**2, 0.0))
        pv_limits = (
            f" Maxkvar={q_limit:.6g} Minkvar={-q_limit:.6g} Pvfactor=0.1" if gen.bus_type == "pv" else ""
        )
        conn_option = " conn=delta" if gen.bus in delta_conn_buses else ""
        machine = gen.dynamics
        if machine is None:
            dynamic_props = f" Xdp={gen.xdpp_pu} Xdpp={gen.xdpp_pu}"
        else:
            dynamics_mapping = importlib.import_module("cept.adapters.opendss.dynamics_mapping")
            dynamic_props = dynamics_mapping.generator_dynamic_properties(machine)
        # PowerFactory exposes pgini/qgini for PV/slack machines as the
        # initialized operating point.  Preserve that disclosed Q input when
        # OpenDSS supports it; do not invent a fixed-Q value when the source
        # did not expose one.
        q_option = f" kvar={float(gen.q_mvar) * 1000.0:.12g}" if gen.q_mvar is not None else f" PF={pf}"
        user_model_props = ""
        if user_model is not None:
            if machine is None:
                raise ValueError(
                    f"OpenDSS UserModel '{user_model.name}' requires explicit dynamics for generator '{gen.name}'."
                )
            user_models = importlib.import_module("cept.adapters.opendss.user_models")

            user_model_props = user_models.user_model_generator_properties(
                gen,
                machine,
                user_model,
                active_kw=gen_kw,
                active_mva=gen_mva,
                initial_state=(user_model_initial_states or {}).get(gen.name),
            )
            model = "6"
        dss.Text.Command(
            f"New {dss_object('Generator', gen.name)} bus1={dss_quote(transformer_bus_refs.get(gen.bus, gen.bus))} phases=3 "
            f"kV={kv} kW={gen_kw}{q_option} kVA={gen_mva * 1000} "
            f"model={model} Vpu={gen.pu}{pv_limits}{conn_option}{dynamic_props}{user_model_props}"
        )

    for shunt in getattr(inline, "shunts", None) or []:
        kv = shunt.kv if shunt.kv is not None else bus_kv[shunt.bus]
        connected = shunt.steps if shunt.steps_in_service is None else shunt.steps_in_service
        # OpenDSS splits the two devices a PowerFactory ElmShnt covers: a
        # positive rating is a Capacitor, a negative one a Reactor.  Emitting
        # the wrong class would invert the reactive contribution, so the sign
        # picks the class rather than being folded into a magnitude.
        # A bank that does not span all three phases is connected to the phases
        # it actually has, at the phase-to-neutral voltage.  Emitting it as
        # three-phase invents two conductors: IEEE 13-node's single-phase 611
        # capacitor created phantom nodes on a single-phase lateral.
        nodes = getattr(shunt, "phase_nodes", None)
        if nodes and len(nodes) < 3:
            phase_count = len(nodes)
            bus_ref = shunt.bus + "." + ".".join(str(node) for node in nodes)
            kv_value = kv / math.sqrt(3)
        else:
            phase_count = 3
            bus_ref = shunt.bus
            kv_value = kv
        if shunt.q_mvar >= 0:
            dss.Text.Command(
                f"New {dss_object('Capacitor', shunt.name)} bus1={dss_quote(bus_ref)} "
                f"phases={phase_count} "
                f"kv={kv_value} kvar={shunt.q_mvar * shunt.steps * 1000.0} numsteps={shunt.steps} "
                f"states={connected}" + ("" if shunt.in_service else " enabled=n")
            )
        else:
            dss.Text.Command(
                f"New {dss_object('Reactor', shunt.name)} bus1={dss_quote(bus_ref)} "
                f"phases={phase_count} "
                f"kv={kv_value} kvar={abs(shunt.q_mvar) * 1000.0}"
                + ("" if shunt.in_service else " enabled=n")
            )

    dss.Text.Command(
        "Set VoltageBases=["
        + " ".join(sorted({str(b.kv) for b in inline.buses}, key=float, reverse=True))
        + "]"
    )
    dss.Text.Command("CalcVoltageBases")
    dss.Text.Command(f"Set Frequency={net.frequency_hz}")
    dss.Text.Command("Set MaxControlIter=100")
    dss.Text.Command("Set MaxIter=200")

    _SCALE = 100.0
    layout = {
        name: (round(x * _SCALE, 2), round(y * _SCALE, 2))
        for name, (x, y) in hierarchical_layout(inline).items()
    }
    return layout
