"""Deterministic cross-check against well-known textbook/benchmark topologies.

An LLM agent can fabricate a plausible-looking but wrong topology for a
famous benchmark system (e.g. IEEE 9-bus) purely from training-data memory,
without ever reading the actual source figure/table. The evidence citations
required by ``cept.cli.topology``/``cept.schema.provenance`` raise the bar,
but a determined-enough fabrication can still include a citation-shaped
string. This module is a fully deterministic backstop: known benchmark
bus-graphs are diffed against whatever the agent actually ingested,
independent of any claimed evidence or status.

This is exactly the check that would have caught the IEEE 9-bus bug where an
agent wired all three generator step-up transformers (T1/T2/T3) onto the same
collector bus instead of three separate ones.

Live cross-engine testing (2026-07-15) found a second real instance of this
same failure class that this module did NOT catch at the time: an ingested
IEEE 9-bus case had all three loads assigned to the wrong buses (Bus 4, 5, 6
instead of the reference's Bus 5, 6, 8 -- Bus 4 should carry no load at all,
it is a pure generator-transformer junction). `cept verify` and a PowerFactory
cross-check both caught the *symptom* (non-convergence / large disagreement)
but only after a full solve -- the mistake itself was extraction-time and
should fail closed at ingest, the same as a wrong line/transformer endpoint
already does. `load_buses` below closes that gap.
"""

from __future__ import annotations

from dataclasses import dataclass

from cept.schema.case import InlineNetwork


def _norm(name: str) -> str:
    return "".join(ch for ch in name.upper() if ch.isalnum())


@dataclass(frozen=True)
class _RefEdge:
    id_hint: str
    bus_a: str
    bus_b: str

    @property
    def pair(self) -> frozenset[str]:
        return frozenset({_norm(self.bus_a), _norm(self.bus_b)})


@dataclass(frozen=True)
class ReferenceTopology:
    name: str
    bus_names: frozenset[str]
    generator_buses: frozenset[str]
    transformers: tuple[_RefEdge, ...]
    lines: tuple[_RefEdge, ...]
    load_buses: frozenset[str] = frozenset()


_IEEE_9_BUS = ReferenceTopology(
    name="IEEE 9-bus (WSCC 3-machine, Anderson & Fouad / Grainger & Stevenson)",
    bus_names=frozenset(_norm(f"Bus{i}") for i in range(1, 10)),
    generator_buses=frozenset(_norm(f"Bus{i}") for i in (1, 2, 3)),
    transformers=(
        _RefEdge("T1", "Bus1", "Bus4"),
        _RefEdge("T2", "Bus2", "Bus7"),
        _RefEdge("T3", "Bus3", "Bus9"),
    ),
    lines=(
        _RefEdge("Line4-5", "Bus4", "Bus5"),
        _RefEdge("Line4-6", "Bus4", "Bus6"),
        _RefEdge("Line5-7", "Bus5", "Bus7"),
        _RefEdge("Line6-9", "Bus6", "Bus9"),
        _RefEdge("Line7-8", "Bus7", "Bus8"),
        _RefEdge("Line8-9", "Bus8", "Bus9"),
    ),
    # Bus 4, 7, 9 are pure generator-transformer/transmission junctions and
    # carry no load in the reference system -- confirmed live (2026-07-15)
    # that a load misplaced onto Bus 4 alone drove OpenDSS's load flow to
    # genuine non-convergence (~18 GW of spurious losses) while the correctly
    # -placed loads converge cleanly with the identical line/transformer data.
    load_buses=frozenset(_norm(f"Bus{i}") for i in (5, 6, 8)),
)

_KNOWN_TOPOLOGIES: tuple[ReferenceTopology, ...] = (_IEEE_9_BUS,)


def _diff_edges(
    kind: str,
    ref_edges: tuple[_RefEdge, ...],
    actual: dict[str, frozenset[str]],
) -> list[str]:
    messages: list[str] = []
    ref_pairs = {edge.pair for edge in ref_edges}
    actual_by_hint = {_norm(name): (name, pair) for name, pair in actual.items()}
    for ref in ref_edges:
        hint = _norm(ref.id_hint)
        if hint in actual_by_hint:
            actual_name, actual_pair = actual_by_hint[hint]
            if actual_pair != ref.pair:
                a, b = sorted(actual_pair)
                messages.append(
                    f"{kind} '{actual_name}' connects {a}-{b}, but the reference "
                    f"topology has '{ref.id_hint}' connecting "
                    f"{ref.bus_a}-{ref.bus_b}"
                )
    actual_pairs = set(actual.values())
    unmatched_by_name = {
        name: pair
        for name, pair in actual.items()
        if _norm(name) not in {_norm(e.id_hint) for e in ref_edges}
    }
    for name, pair in unmatched_by_name.items():
        if pair not in ref_pairs:
            a, b = sorted(pair)
            messages.append(
                f"{kind} '{name}' connects {a}-{b}, which does not match any "
                f"{kind} connection in the reference topology"
            )
    missing_pairs = ref_pairs - actual_pairs
    if missing_pairs:
        for ref in ref_edges:
            if ref.pair in missing_pairs and not any(ref.pair == pair for pair in actual.values()):
                # Already reported above via the id_hint match when the name
                # exists but is wired wrong; only add a standalone note when
                # no actual edge claims this id_hint at all.
                if _norm(ref.id_hint) not in actual_by_hint:
                    messages.append(
                        f"reference topology expects {kind} '{ref.id_hint}' "
                        f"connecting {ref.bus_a}-{ref.bus_b}, which is missing"
                    )
    return messages


def check_known_topology(network: InlineNetwork) -> list[str]:
    """Diff ``network`` against known textbook/benchmark topologies.

    Returns mismatch messages when the network's bus-name set matches a known
    benchmark's signature but its wiring deviates from the reference. Returns
    an empty list when no known signature matches (an unrelated/custom
    system) or when the wiring matches exactly.
    """
    bus_names = frozenset(_norm(b.name) for b in network.buses)
    for ref in _KNOWN_TOPOLOGIES:
        if bus_names != ref.bus_names:
            continue
        messages: list[str] = []
        actual_transformers = {
            tr.name: frozenset({_norm(tr.hv_bus), _norm(tr.lv_bus)}) for tr in network.transformers
        }
        actual_lines = {ln.name: frozenset({_norm(ln.from_bus), _norm(ln.to_bus)}) for ln in network.lines}
        messages.extend(_diff_edges("transformer", ref.transformers, actual_transformers))
        messages.extend(_diff_edges("line", ref.lines, actual_lines))
        actual_gen_buses = frozenset(_norm(g.bus) for g in network.generators)
        if network.generators and actual_gen_buses != ref.generator_buses:
            messages.append(
                f"generators are on buses {sorted(actual_gen_buses)}, but the reference "
                f"{ref.name} topology has generators on {sorted(ref.generator_buses)}"
            )
        actual_load_buses = frozenset(_norm(item.bus) for item in network.loads)
        if ref.load_buses and network.loads and actual_load_buses != ref.load_buses:
            messages.append(
                f"loads are on buses {sorted(actual_load_buses)}, but the reference "
                f"{ref.name} topology has loads on {sorted(ref.load_buses)}"
            )
        if messages:
            messages.insert(
                0,
                f"Network bus names match the known {ref.name} benchmark, but the "
                "wiring deviates from the published reference topology:",
            )
        return messages
    return []
