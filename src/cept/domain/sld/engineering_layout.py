"""Deterministic engineering layout for CEPT single-line diagrams.

Native PowerFactory and Interactive SVG consume the same positions. Small
reference networks keep compact ring/tree layouts; dense industrial networks
use source regions, voltage hierarchy and switchboard-aware fan-out geometry.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import networkx as nx

from cept.schema.case import InlineNetwork

_SCALE = 400.0
_LONG_CHAIN_MAX_COLUMNS = 10
_LONG_CHAIN_ROW_GAP = 2.0
_DENSE_MIN_BUSES = 25
# Small radial feeders benefit from subtree-centred ranks as well. The
# compact layered fallback keeps only global rank order, which can place a
# child subtree back under a neighbouring feeder and force long visual
# dog-legs even when the topology is a simple tree.
_SMALL_TREE_LAYOUT_MIN_BUSES = 10

_DENSE_EXPLICIT_MIN_COVERAGE = 0.75
_DENSE_EXPLICIT_MAX_ASPECT = 6.0
_DENSE_EXPLICIT_MIN_GAP = 0.40 * _SCALE
_SWITCHBOARD_MIN_FANOUT = 8
_SWITCHBOARD_STRONG_FANOUT = 12
_SWITCHBOARD_LOW_KV = 1.5
_SWITCHBOARD_LEAF_RATIO = 0.65
_TREE_LEAF_WIDTH = 0.72
_TREE_SIBLING_GAP = 0.18
_REGION_GAP = 2.5
_SAME_VOLTAGE_GAP = 1.0
_VOLTAGE_CHANGE_GAP = 1.65
# Match the renderer-neutral canonical branch-port pitch. At a high-degree bus
# this makes each direct child land under its own bus port, so the branch can be
# a straight 90-degree drop instead of joining a shared stepped corridor.
ENGINEERING_SWITCHBOARD_PORT_SPACING = 48.0
_SWITCHBOARD_PITCH = ENGINEERING_SWITCHBOARD_PORT_SPACING / _SCALE


@dataclass(frozen=True, slots=True)
class SLDTopologyIntent:
    family: str
    source_roots: tuple[str, ...]
    switchboards: tuple[str, ...]
    voltage_levels_kv: tuple[float, ...]


def _minimum_gap(positions: dict[str, tuple[float, float]]) -> float:
    values = list(positions.values())
    if len(values) < 2:
        return float("inf")
    return min(
        math.hypot(x2 - x1, y2 - y1)
        for index, (x1, y1) in enumerate(values)
        for x2, y2 in values[index + 1 :]
    )


def _explicit_layout_is_readable(net: InlineNetwork, explicit: dict[str, tuple[float, float]]) -> bool:
    if len(net.buses) < _DENSE_MIN_BUSES or len(explicit) < 2:
        return True
    if len(explicit) / max(len(net.buses), 1) < _DENSE_EXPLICIT_MIN_COVERAGE:
        return True
    xs = [point[0] for point in explicit.values()]
    ys = [point[1] for point in explicit.values()]
    span_x, span_y = max(xs) - min(xs), max(ys) - min(ys)
    short = min(span_x, span_y)
    aspect = (
        float("inf") if short <= 1e-9 and max(span_x, span_y) > 0 else max(span_x, span_y) / max(short, 1.0)
    )
    return aspect <= _DENSE_EXPLICIT_MAX_ASPECT and _minimum_gap(explicit) >= _DENSE_EXPLICIT_MIN_GAP


def inline_layout(net: InlineNetwork) -> dict[str, tuple[float, float]]:
    positions = hierarchical_layout(net)
    explicit = {
        name.lower(): (
            round(float(point[0]) * _SCALE, 2),
            round(float(point[1]) * _SCALE, 2),
        )
        for name, point in (net.sld_layout or {}).items()
    }
    if explicit and _explicit_layout_is_readable(net, explicit):
        positions.update(explicit)
    return {name.lower(): (round(x, 2), round(y, 2)) for name, (x, y) in positions.items()}


def _multi_source_distance(graph: nx.Graph, roots: list[str]) -> dict[str, int]:
    distance = {root: 0 for root in roots}
    frontier = list(distance)
    while frontier:
        nxt: list[str] = []
        for node in frontier:
            for neighbour in graph.neighbors(node):
                if neighbour not in distance:
                    distance[neighbour] = distance[node] + 1
                    nxt.append(neighbour)
        frontier = nxt
    return distance


def _path_layout(graph: nx.Graph, roots: list[str]) -> dict[str, tuple[float, float]]:
    if graph.number_of_nodes() == 1:
        node = next(iter(graph.nodes))
        return {node: (0.0, 0.0)}
    endpoints = sorted(node for node, degree in graph.degree if degree <= 1)
    if len(endpoints) != 2:
        endpoints = sorted(graph.nodes)[:2]
    root = roots[0] if roots else endpoints[0]
    start = (
        root
        if root in endpoints
        else min(
            endpoints,
            key=lambda node: (
                nx.shortest_path_length(graph, root, node),
                node,
            ),
        )
    )
    end = endpoints[1] if start == endpoints[0] else endpoints[0]
    order = nx.shortest_path(graph, start, end)
    if len(order) <= _LONG_CHAIN_MAX_COLUMNS:
        return {node: (float(index), 0.0) for index, node in enumerate(order)}
    result: dict[str, tuple[float, float]] = {}
    for index, node in enumerate(order):
        row, raw = divmod(index, _LONG_CHAIN_MAX_COLUMNS)
        column = raw if row % 2 == 0 else _LONG_CHAIN_MAX_COLUMNS - 1 - raw
        result[node] = (
            float(column),
            float(row) * _LONG_CHAIN_ROW_GAP,
        )
    return result


def _ring_layout(
    graph: nx.Graph,
    roots: list[str],
    cycles: list[list[str]],
) -> dict[str, tuple[float, float]]:
    cycle = max(cycles, key=len)
    distance = _multi_source_distance(graph, roots)
    anchor = min(
        range(len(cycle)),
        key=lambda index: (
            distance.get(cycle[index], 0),
            cycle[index],
        ),
    )
    cycle = cycle[anchor:] + cycle[:anchor]
    if len(cycle) > 2 and cycle[-1] < cycle[1]:
        cycle = [cycle[0]] + cycle[:0:-1]
    count = len(cycle)
    side_height = max(1, math.ceil(count / 4))
    top_count = count - 1 - 2 * (side_height - 1)
    while top_count < 1 and side_height > 1:
        side_height -= 1
        top_count = count - 1 - 2 * (side_height - 1)
    half_width = max(1.0, (top_count - 1) / 2.0)
    positions = {cycle[0]: (0.0, 0.0)}
    outward = {cycle[0]: (0.0, -1.0)}
    rest = cycle[1:]
    left = rest[: side_height - 1]
    top = rest[side_height - 1 : side_height - 1 + top_count]
    right = rest[side_height - 1 + top_count :]
    for index, node in enumerate(left):
        positions[node] = (-half_width, float(index + 1))
        outward[node] = (-1.0, 0.0)
    for index, node in enumerate(top):
        x = 0.0 if top_count == 1 else -half_width + index * (2 * half_width) / (top_count - 1)
        positions[node] = (x, float(side_height))
        if index == 0 and top_count > 1:
            outward[node] = (-1.0, 0.0)
        elif index == top_count - 1 and top_count > 1:
            outward[node] = (1.0, 0.0)
        else:
            outward[node] = (0.0, 1.0)
    for index, node in enumerate(right):
        positions[node] = (
            half_width,
            float(len(right) - index),
        )
        outward[node] = (1.0, 0.0)
    placed = set(cycle)
    frontier = list(cycle)
    slots: dict[str, int] = {}
    while frontier:
        nxt: list[str] = []
        for parent in frontier:
            px, py = positions[parent]
            dx, dy = outward[parent]
            for child in sorted(graph.neighbors(parent)):
                if child in placed:
                    continue
                slot = slots.get(parent, 0)
                slots[parent] = slot + 1
                shift = ((slot + 1) // 2) * (1.0 if slot % 2 else -1.0)
                positions[child] = (
                    px + dx * 1.5 - dy * shift,
                    py + dy * 1.5 + dx * shift,
                )
                outward[child] = (dx, dy)
                placed.add(child)
                nxt.append(child)
        frontier = nxt
    return positions


def _source_forest(
    graph: nx.Graph,
    roots: list[str],
    kv: dict[str, float],
) -> tuple[
    dict[str, str | None],
    dict[str, list[str]],
    dict[str, str],
    dict[str, int],
]:
    roots = sorted(dict.fromkeys(roots))
    all_distance = {root: nx.single_source_shortest_path_length(graph, root) for root in roots}
    owner: dict[str, str] = {}
    distance: dict[str, int] = {}
    for node in graph.nodes:
        dist, root = min((all_distance[root].get(node, 10**9), root) for root in roots)
        owner[node], distance[node] = root, int(dist)
    parent: dict[str, str | None] = {root: None for root in roots}
    for node in sorted(
        graph.nodes,
        key=lambda item: (distance[item], item),
    ):
        if node in parent:
            continue
        candidates = [
            other
            for other in graph.neighbors(node)
            if distance.get(other, 10**9) == distance[node] - 1 and owner.get(other) == owner[node]
        ] or [other for other in graph.neighbors(node) if distance.get(other, 10**9) == distance[node] - 1]
        parent[node] = (
            min(
                candidates,
                key=lambda item: (
                    -float(kv.get(item, 0.0)),
                    item,
                ),
            )
            if candidates
            else None
        )
    children = {node: [] for node in graph.nodes}
    for node, upstream in parent.items():
        if upstream is not None:
            children[upstream].append(node)
    for node in children:
        children[node].sort(
            key=lambda child: (
                -float(kv.get(child, 0.0)),
                child,
            )
        )
    return parent, children, owner, distance


def _switchboards(
    children: dict[str, list[str]],
    kv: dict[str, float],
) -> set[str]:
    found: set[str] = set()
    for node, kids in children.items():
        fanout = len(kids)
        if fanout < _SWITCHBOARD_MIN_FANOUT:
            continue
        leaf_ratio = sum(len(children.get(child, [])) <= 1 for child in kids) / max(fanout, 1)
        low_voltage = 0.0 < float(kv.get(node, 0.0)) <= _SWITCHBOARD_LOW_KV
        if fanout >= _SWITCHBOARD_STRONG_FANOUT or low_voltage or leaf_ratio >= _SWITCHBOARD_LEAF_RATIO:
            found.add(node)
    return found


def _edge_level_gap(
    parent: str,
    child: str,
    kv: dict[str, float],
) -> float:
    high = float(kv.get(parent, 0.0))
    low = float(kv.get(child, 0.0))
    if high > 0.0 and low > 0.0 and abs(high - low) > max(0.05, 0.02 * max(high, low)):
        return _VOLTAGE_CHANGE_GAP
    return _SAME_VOLTAGE_GAP


def _engineering_tree(
    graph: nx.Graph,
    roots: list[str],
    kv: dict[str, float],
) -> tuple[dict[str, tuple[float, float]], set[str]]:
    parent, children, owner, distance = _source_forest(graph, roots, kv)
    switchboards = _switchboards(children, kv)
    y = {root: 0.0 for root in roots}
    unresolved = set(graph.nodes) - set(roots)
    for _ in range(max(1, graph.number_of_nodes())):
        progressed = False
        for node in sorted(tuple(unresolved)):
            upstream = parent.get(node)
            if upstream is None:
                y[node] = 0.0
            elif upstream not in y:
                continue
            else:
                y[node] = y[upstream] + _edge_level_gap(upstream, node, kv)
            unresolved.remove(node)
            progressed = True
        if not unresolved or not progressed:
            break
    for node in unresolved:
        y[node] = float(distance.get(node, 0)) * _SAME_VOLTAGE_GAP

    def width(node: str) -> float:
        kids = children.get(node, [])
        if not kids:
            return _TREE_LEAF_WIDTH
        child_widths = [width(child) for child in kids]
        sibling_gap = _TREE_SIBLING_GAP * max(len(kids) - 1, 0)
        if node in switchboards:
            # Children of an engineering switchboard are placed on dedicated
            # fixed-pitch ports, so its reserved subtree envelope must match
            # that actual geometry. Summing every leaf width and generic
            # sibling gap massively over-reserved empty space (31 LV feeders
            # consumed ~11k drawing units) and pushed the next source region
            # off-page even though the feeder ports themselves span only
            # 1,440 units. Preserve room for the widest child subtree while
            # using the canonical switchboard pitch between child centres.
            return max(
                _TREE_LEAF_WIDTH,
                _SWITCHBOARD_PITCH * max(len(kids) - 1, 0) + max(child_widths, default=_TREE_LEAF_WIDTH),
            )
        return max(
            _TREE_LEAF_WIDTH,
            sum(child_widths) + sibling_gap,
        )

    x: dict[str, float] = {}

    def shift_subtree(node: str, delta: float) -> None:
        x[node] += delta
        for child in children.get(node, []):
            shift_subtree(child, delta)

    def place(node: str, left: float) -> None:
        kids = children.get(node, [])
        own_width = width(node)
        if not kids:
            x[node] = left + own_width / 2
            return
        if node in switchboards:
            center = left + own_width / 2
            start = center - (len(kids) - 1) * _SWITCHBOARD_PITCH / 2
            for index, child in enumerate(kids):
                desired = start + index * _SWITCHBOARD_PITCH
                place(child, desired - width(child) / 2)
                shift_subtree(child, desired - x[child])
            x[node] = center
            return
        cursor = left
        centers: list[float] = []
        for child in kids:
            place(child, cursor)
            centers.append(x[child])
            cursor += width(child) + _TREE_SIBLING_GAP
        x[node] = (centers[0] + centers[-1]) / 2

    cursor = 0.0
    for root in sorted(roots):
        if any(owner.get(node) == root for node in graph.nodes):
            place(root, cursor)
            cursor += width(root) + _REGION_GAP
    for node in sorted(graph.nodes):
        if node not in x:
            x[node] = cursor
            cursor += _TREE_LEAF_WIDTH
    return {node: (x[node], y[node]) for node in graph.nodes}, switchboards


def _small_layered(graph: nx.Graph, roots: list[str]) -> dict[str, tuple[float, float]]:
    rank = _multi_source_distance(graph, roots)
    levels: dict[int, list[str]] = {}
    for node, value in rank.items():
        levels.setdefault(value, []).append(node)
    for value in levels:
        levels[value].sort()
    order = {name: index for nodes in levels.values() for index, name in enumerate(nodes)}
    for _pass in range(4):
        for value in sorted(levels):

            def barycenter(
                node: str,
                level: int = value,
            ) -> float:
                upstream = [other for other in graph.neighbors(node) if rank.get(other) == level - 1]
                if upstream:
                    return sum(order[other] for other in upstream) / len(upstream)
                return float(order[node])

            levels[value].sort(key=barycenter)
            for index, node in enumerate(levels[value]):
                order[node] = index
    max_width = max(
        (len(nodes) for nodes in levels.values()),
        default=1,
    )
    result: dict[str, tuple[float, float]] = {}
    for value, nodes in levels.items():
        pad = (max_width - len(nodes)) / 2
        for index, node in enumerate(nodes):
            result[node] = (pad + index, float(value))
    return result


def _pack_dense_levels(graph: nx.Graph, roots: list[str]) -> dict[str, tuple[float, float]]:
    """Finite-page fallback for a homogeneous-voltage generic star."""
    rank = _multi_source_distance(graph, roots)
    levels: dict[int, list[str]] = {}
    for node, value in rank.items():
        levels.setdefault(value, []).append(node)
    result: dict[str, tuple[float, float]] = {}
    y_cursor = 0.0
    for value in sorted(levels):
        nodes = sorted(levels[value])
        bands = max(
            1,
            math.ceil(len(nodes) / _LONG_CHAIN_MAX_COLUMNS),
        )
        for band in range(bands):
            start = band * _LONG_CHAIN_MAX_COLUMNS
            chunk = nodes[start : start + _LONG_CHAIN_MAX_COLUMNS]
            if band % 2:
                chunk = list(reversed(chunk))
            pad = (_LONG_CHAIN_MAX_COLUMNS - len(chunk)) / 2.0
            for column, node in enumerate(chunk):
                result[node] = (
                    pad + float(column),
                    y_cursor + band * 0.85,
                )
        y_cursor += bands * 0.85 + 1.05
    return result


def _component_layout(
    graph: nx.Graph,
    component: set[str],
    roots: list[str],
    kv: dict[str, float],
) -> dict[str, tuple[float, float]]:
    subgraph = graph.subgraph(component)
    roots = roots or [max(sorted(subgraph.degree), key=lambda item: item[1])[0]]
    is_path = (
        subgraph.number_of_edges() == subgraph.number_of_nodes() - 1
        and max(
            (degree for _node, degree in subgraph.degree),
            default=0,
        )
        <= 2
    )
    if is_path:
        return _path_layout(subgraph, roots)
    max_degree = max(
        (degree for _node, degree in subgraph.degree),
        default=0,
    )
    voltage_levels = {
        round(float(kv.get(node, 0.0)), 6) for node in subgraph.nodes if float(kv.get(node, 0.0)) > 0.0
    }
    homogeneous_dense_star = (
        subgraph.number_of_nodes() >= _DENSE_MIN_BUSES
        and len(roots) == 1
        and len(voltage_levels) <= 1
        and max_degree >= _SWITCHBOARD_MIN_FANOUT
    )
    if homogeneous_dense_star:
        return _pack_dense_levels(subgraph, roots)
    tree_with_enough_branches = (
        subgraph.number_of_edges() == subgraph.number_of_nodes() - 1
        and subgraph.number_of_nodes() >= _SMALL_TREE_LAYOUT_MIN_BUSES
    )
    engineering = (
        subgraph.number_of_nodes() >= _DENSE_MIN_BUSES
        or len(roots) > 1
        or max_degree >= _SWITCHBOARD_MIN_FANOUT
        or tree_with_enough_branches
    )
    if engineering:
        return _engineering_tree(subgraph, roots, kv)[0]
    cycles = nx.cycle_basis(subgraph)
    if cycles:
        return _ring_layout(subgraph, roots, cycles)
    return _small_layered(subgraph, roots)


def _layout_graph(
    graph: nx.Graph,
    source_buses: set[str],
    *,
    kv: dict[str, float] | None = None,
) -> dict[str, tuple[float, float]]:
    if graph.number_of_nodes() == 0:
        return {}
    kv = {str(name): float(value) for name, value in (kv or {}).items()}
    result: dict[str, tuple[float, float]] = {}
    cursor = 0.0
    components = sorted(
        nx.connected_components(graph),
        key=lambda item: (-len(item), sorted(item)[0]),
    )
    for component in components:
        subgraph = graph.subgraph(component)
        roots = sorted(source_buses & component) or [
            max(
                sorted(subgraph.degree),
                key=lambda item: item[1],
            )[0]
        ]
        placed = _component_layout(
            graph,
            set(component),
            roots,
            kv,
        )
        xs = [point[0] for point in placed.values()]
        shift = cursor - min(xs)
        for node, (x, y) in placed.items():
            result[node.lower()] = (x + shift, y)
        cursor += max(xs) - min(xs) + _REGION_GAP
    return result


def _inline_graph(
    net: InlineNetwork,
) -> tuple[nx.Graph, dict[str, float], set[str]]:
    graph = nx.Graph()
    kv = {bus.name: float(bus.kv) for bus in net.buses}
    graph.add_nodes_from(bus.name for bus in net.buses)
    graph.add_edges_from((line.from_bus, line.to_bus) for line in net.lines)
    graph.add_edges_from((item.hv_bus, item.lv_bus) for item in net.transformers)
    graph.add_edges_from((item.bus1, item.bus2) for item in net.switches)
    # T-039: the DC island is first-class topology. DC buses are layout nodes
    # and each converter is an edge from its AC bus to its DC pole bus(es),
    # so the island is placed deterministically instead of collapsing to the
    # (0, 0) fallback that fails the collision gate.
    for dc_bus in net.dc_buses or []:
        graph.add_node(dc_bus.name)
        kv[dc_bus.name] = float(dc_bus.kv)
    for conv in net.converters or []:
        graph.add_edge(conv.bus, conv.dc_plus_bus)
        if conv.dc_minus_bus is not None:
            graph.add_edge(conv.bus, conv.dc_minus_bus)
    roots = {grid.bus for grid in net.external_grids if grid.bus in graph}
    if not roots:
        roots = {gen.bus for gen in net.generators if gen.bus_type == "slack" and gen.bus in graph}
    return graph, kv, roots


def classify_inline_topology(
    net: InlineNetwork,
) -> SLDTopologyIntent:
    graph, kv, roots = _inline_graph(net)
    if not roots and graph.number_of_nodes():
        roots = {max(sorted(graph.degree), key=lambda item: item[1])[0]}
    if graph.number_of_nodes():
        _parent, children, _owner, _distance = _source_forest(
            graph,
            sorted(roots),
            kv,
        )
        switchboards = tuple(sorted(_switchboards(children, kv)))
    else:
        switchboards = ()
    if len(roots) > 1:
        family = "multi_source_tree"
    elif switchboards:
        family = "switchboard_fanout"
    elif nx.cycle_basis(graph):
        family = "ring_mesh"
    elif graph.number_of_nodes() >= _DENSE_MIN_BUSES:
        family = "dense_radial"
    else:
        family = "simple_radial"
    return SLDTopologyIntent(
        family=family,
        source_roots=tuple(sorted(roots)),
        switchboards=switchboards,
        voltage_levels_kv=tuple(
            sorted(
                {round(value, 6) for value in kv.values() if value > 0},
                reverse=True,
            )
        ),
    )


def hierarchical_layout(
    net: InlineNetwork,
) -> dict[str, tuple[float, float]]:
    graph, kv, roots = _inline_graph(net)
    positions = _layout_graph(graph, roots, kv=kv)
    return {
        name: (
            round(x * _SCALE, 2),
            round(y * _SCALE, 2),
        )
        for name, (x, y) in positions.items()
    }


def _sld_sources(
    nodes,
    edges,
) -> tuple[set[str], dict[str, str]]:
    node_by_id = {str(node.id): node for node in (nodes or [])}
    physical = {
        str(node.id)
        for node in (nodes or [])
        if any(getattr(gen, "kind", None) == "grid" for gen in getattr(node, "gens", ()))
    }
    synthetic_to_physical: dict[str, str] = {}
    for edge in edges or []:
        if not str(getattr(edge, "id", "")).startswith("GridLink."):
            continue
        a, b = str(edge.src), str(edge.dst)
        if a in physical and b in node_by_id and getattr(node_by_id[b], "kind", None) == "substation":
            synthetic_to_physical[b] = a
        elif b in physical and a in node_by_id and getattr(node_by_id[a], "kind", None) == "substation":
            synthetic_to_physical[a] = b
    return physical, synthetic_to_physical


def sld_topology_layout(
    nodes=None,
    edges=None,
    *,
    node_ids: list[str] | None = None,
    edge_pairs: list[tuple[str, str]] | None = None,
    source_ids: set[str] | None = None,
) -> dict[str, tuple[float, float]]:
    """Layout solved SLD data with physical grid buses as roots."""
    graph = nx.Graph()
    kv: dict[str, float] = {}
    if nodes is not None:
        physical, synthetic_map = _sld_sources(nodes, edges)
        for node in nodes:
            name = str(node.id)
            if name in synthetic_map:
                continue
            graph.add_node(name)
            kv[name] = float(getattr(node, "kv_base", 0.0) or 0.0)
    else:
        physical, synthetic_map = set(), {}
        if node_ids is not None:
            graph.add_nodes_from(str(name) for name in node_ids)
    if edge_pairs is not None:
        graph.add_edges_from((str(a), str(b)) for a, b in edge_pairs)
    elif edges is not None:
        for edge in edges:
            if str(getattr(edge, "id", "")).startswith("GridLink."):
                continue
            a, b = str(edge.src), str(edge.dst)
            if a in graph and b in graph:
                graph.add_edge(a, b)
    if graph.number_of_nodes() == 0:
        return {}
    sources = physical & set(graph)
    if not sources:
        selected = set(source_ids or ())
        if not selected and nodes is not None:
            selected = {
                str(node.id)
                for node in nodes
                if str(node.id) in graph and getattr(node, "kind", None) == "substation"
            }
        sources = selected & set(graph)
    if not sources:
        sources = {max(sorted(graph.degree), key=lambda item: item[1])[0]}
    positions = _layout_graph(graph, sources, kv=kv)
    scaled = {
        name: (
            round(x * _SCALE, 2),
            round(y * _SCALE, 2),
        )
        for name, (x, y) in positions.items()
    }
    # Legacy compatibility until GridLink leaves the SLD transport schema.
    # The browser removes this pseudo node; co-locating it with the physical
    # source prevents it from distorting canonical engineering layout.
    for synthetic, physical_bus in synthetic_map.items():
        point = scaled.get(physical_bus.lower())
        if point is not None:
            scaled[synthetic.lower()] = point
    return scaled


def orient_finite_page_layout(
    positions: dict[str, tuple[float, float]],
    *,
    minimum_nodes: int = 25,
    tall_aspect: float = 1.25,
    preserve_top_down: bool = True,
) -> dict[str, tuple[float, float]]:
    """Keep engineering hierarchy top-down unless rotation is explicit."""
    if preserve_top_down or len(positions) < minimum_nodes or not positions:
        return dict(positions)
    xs = [point[0] for point in positions.values()]
    ys = [point[1] for point in positions.values()]
    if max(ys) - min(ys) <= (max(xs) - min(xs)) * tall_aspect:
        return dict(positions)
    x0, y0 = min(xs), min(ys)
    return {
        name: (
            round(point[1] - y0, 2),
            round(-(point[0] - x0), 2),
        )
        for name, point in positions.items()
    }


def bus_orientations(
    net: InlineNetwork,
    positions: dict[str, tuple[float, float]],
) -> dict[str, str]:
    neighbours: dict[str, list[str]] = {bus.name.lower(): [] for bus in net.buses}
    pairs = [
        *((line.from_bus, line.to_bus) for line in net.lines),
        *((item.hv_bus, item.lv_bus) for item in net.transformers),
        *((item.bus1, item.bus2) for item in net.switches),
    ]
    for source, target in pairs:
        a, b = source.lower(), target.lower()
        neighbours.setdefault(a, []).append(b)
        neighbours.setdefault(b, []).append(a)
    result: dict[str, str] = {}
    for bus, adjacent in neighbours.items():
        if bus not in positions:
            continue
        x, y = positions[bus]
        horizontal = 0
        vertical = 0
        for other in adjacent:
            if other not in positions:
                continue
            ox, oy = positions[other]
            if abs(ox - x) > abs(oy - y):
                horizontal += 1
            else:
                vertical += 1
        result[bus] = "v" if horizontal > vertical else "h"
    return result


__all__ = [
    "ENGINEERING_SWITCHBOARD_PORT_SPACING",
    "SLDTopologyIntent",
    "bus_orientations",
    "classify_inline_topology",
    "hierarchical_layout",
    "inline_layout",
    "orient_finite_page_layout",
    "sld_topology_layout",
]
