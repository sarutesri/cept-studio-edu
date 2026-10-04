"""Cross-artifact topology audit for a solved two-engine study.

The existing SLD fidelity gate checks one in-memory ``StudyResult``.  This
module checks the persisted handoff as well: the canonical Case graph, both
engine SLD graphs, the canonical layout routes, the native PowerFactory
readback receipt, and (when present) the identity map.  A raster SLD is kept
as visual evidence, but is never treated as machine-readable proof of an
endpoint; the native readback and the logical graph are the connection gate.
"""

from __future__ import annotations

import re
from typing import Any


SCHEMA = "cept-topology-sld-audit-v1"


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _edge_id(value: Any) -> str:
    """Map engine-native prefixes back to the canonical asset id."""
    value = _norm(value)
    if value.startswith("gridlink."):
        return value
    for prefix in ("line.", "transformer.", "switch.", "reactor.", "capacitor."):
        if value.startswith(prefix):
            return value[len(prefix):]
    return value


def _point_on_layout_busbar(point: Any, node: dict[str, Any], *, tolerance: float = 1e-6) -> bool:
    """Accept a canonical port endpoint inside the persisted busbar envelope."""
    if not isinstance(point, (list, tuple)) or len(point) < 2:
        return False
    center_x_raw = node.get("x")
    center_y_raw = node.get("y")
    half_raw = node.get("canonical_half_length")
    if center_x_raw is None or center_y_raw is None or half_raw is None:
        return False
    try:
        x, y = float(point[0]), float(point[1])
        center_x, center_y = float(center_x_raw), float(center_y_raw)
        half = float(half_raw)
    except (TypeError, ValueError):
        return False
    orientation = str(node.get("bus_orientation") or "").lower()
    if orientation == "horizontal":
        return abs(y - center_y) <= tolerance and abs(x - center_x) <= half + tolerance
    if orientation == "vertical":
        return abs(x - center_x) <= tolerance and abs(y - center_y) <= half + tolerance
    return False


def _route_axis_backtrack_count(points: Any) -> int:
    """Count same-axis direction reversals in a persisted Manhattan route."""
    if not isinstance(points, (list, tuple)):
        return 0
    signs: dict[str, list[int]] = {"x": [], "y": []}
    for left, right in zip(points, points[1:]):
        if not isinstance(left, (list, tuple)) or not isinstance(right, (list, tuple)):
            continue
        try:
            dx = float(right[0]) - float(left[0])
            dy = float(right[1]) - float(left[1])
        except (TypeError, ValueError, IndexError):
            continue
        if abs(dx) > 1e-9:
            signs["x"].append(1 if dx > 0.0 else -1)
        if abs(dy) > 1e-9:
            signs["y"].append(1 if dy > 0.0 else -1)
    return sum(
        sum(previous != current for previous, current in zip(axis_signs, axis_signs[1:]))
        for axis_signs in signs.values()
    )


def _expected(case: dict[str, Any]) -> dict[str, Any]:
    inline = ((case.get("network") or {}).get("inline") or {})
    open_elements = {_norm(value) for value in inline.get("open_elements", []) or []}
    buses = {_norm(item.get("name")) for item in inline.get("buses", [])}
    grids = list(inline.get("external_grids", []) or [])
    nodes = set(buses)
    edges: list[dict[str, str]] = []
    for grid in grids:
        name = _norm(grid.get("name"))
        bus = _norm(grid.get("bus"))
        if name:
            nodes.add(name)
            edges.append({
                "id": f"gridlink.{name}", "src": name, "dst": bus, "kind": "line", "status": "closed",
            })
    for line in inline.get("lines", []) or []:
        edges.append({
            "id": _norm(line.get("name")),
            "src": _norm(line.get("from_bus")),
            "dst": _norm(line.get("to_bus")),
            "kind": "line",
            "status": "open" if _norm(line.get("name")) in open_elements else "closed",
        })
    for transformer in inline.get("transformers", []) or []:
        edges.append({
            "id": _norm(transformer.get("name")),
            "src": _norm(transformer.get("hv_bus")),
            "dst": _norm(transformer.get("lv_bus")),
            "kind": "transformer",
            "status": "open" if _norm(transformer.get("name")) in open_elements else "closed",
        })
    return {
        "nodes": sorted(node for node in nodes if node),
        "edges": sorted((edge for edge in edges if edge["id"]), key=lambda item: item["id"]),
        "generators": sorted(_norm(item.get("name")) for item in inline.get("generators", []) if item.get("name")),
        "loads": sorted(_norm(item.get("id")) for item in inline.get("loads", []) if item.get("id")),
        "assets": sorted(
            {
                *nodes,
                *(_norm(item.get("name")) for item in inline.get("generators", []) if item.get("name")),
                *(_norm(item.get("id")) for item in inline.get("loads", []) if item.get("id")),
                *(_norm(item.get("name")) for item in inline.get("lines", []) if item.get("name")),
                *(_norm(item.get("name")) for item in inline.get("transformers", []) if item.get("name")),
            }
        ),
    }


def _node_devices(sld: dict[str, Any], key: str) -> set[str]:
    values: set[str] = set()
    for node in sld.get("nodes", []) or []:
        for item in node.get(key, []) or []:
            if isinstance(item, dict):
                value = item.get("name") or item.get("id")
            else:
                value = item
            if value:
                values.add(_norm(value))
    return values


def _base_sld(result: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Use the explicit initial snapshot when a result also has a final SLD.

    PowerFactory keeps ``result.sld`` at the pre-dynamics load-flow snapshot,
    while OpenDSS keeps it at the final event state.  Comparing those two root
    fields would turn an event-state difference into a false base-topology
    result.  The persisted ``initial`` snapshot is the common base contract.
    """
    for snapshot in result.get("sld_snapshots", []) or []:
        if not isinstance(snapshot, dict) or snapshot.get("id") != "initial":
            continue
        sld = snapshot.get("sld")
        if isinstance(sld, dict):
            return sld, "sld_snapshots.initial"
    sld = result.get("sld")
    return (sld if isinstance(sld, dict) else {}), "sld"


def _graph_check(result: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    sld, sld_source = _base_sld(result)
    actual_nodes = {_norm(node.get("id")) for node in sld.get("nodes", []) or [] if node.get("id")}
    actual_edges: dict[str, dict[str, str]] = {}
    for edge in sld.get("edges", []) or []:
        identifier = _edge_id(edge.get("id"))
        if identifier:
            actual_edges[identifier] = {
                "id": identifier,
                "src": _norm(edge.get("src")),
                "dst": _norm(edge.get("dst")),
                "kind": _norm(edge.get("kind")),
                "status": _norm(edge.get("status")),
            }
    expected_edges = {edge["id"]: edge for edge in expected["edges"]}
    missing_edges = sorted(set(expected_edges) - set(actual_edges))
    extra_edges = sorted(set(actual_edges) - set(expected_edges))
    endpoint_mismatches = []
    kind_mismatches = []
    status_available = any(_norm(edge.get("status")) for edge in sld.get("edges", []) or [])
    status_mismatches = []
    for identifier in sorted(set(expected_edges) & set(actual_edges)):
        want, got = expected_edges[identifier], actual_edges[identifier]
        if (want["src"], want["dst"]) != (got["src"], got["dst"]):
            endpoint_mismatches.append({"id": identifier, "expected": want, "actual": got})
        if want["kind"] != got["kind"]:
            kind_mismatches.append({"id": identifier, "expected": want["kind"], "actual": got["kind"]})
        if status_available and want.get("status") != got["status"]:
            status_mismatches.append({"id": identifier, "expected": want.get("status"), "actual": got["status"]})

    missing_nodes = sorted(set(expected["nodes"]) - actual_nodes)
    extra_nodes = sorted(actual_nodes - set(expected["nodes"]))

    adjacency: dict[str, set[str]] = {node: set() for node in actual_nodes}
    for edge in actual_edges.values():
        if edge["src"] == edge["dst"]:
            continue
        adjacency.setdefault(edge["src"], set()).add(edge["dst"])
        adjacency.setdefault(edge["dst"], set()).add(edge["src"])
    source_nodes = {
        _norm(item.get("id") or item.get("name"))
        for item in ((result.get("sld") or {}).get("nodes", []) or [])
        if _norm(item.get("kind")) == "substation"
    }
    # A generator-only case has no substation graphic; a slack generator bus
    # is still an energising source.  The canonical audit remains generic.
    if not source_nodes:
        source_nodes = {
            _norm(item.get("bus"))
            for item in ((result.get("case") or {}).get("network", {}).get("inline", {}).get("generators", []) or [])
            if _norm(item.get("bus_type")) == "slack"
        }
    reachable = set(node for node in source_nodes if node in actual_nodes)
    frontier = list(reachable)
    while frontier:
        current = frontier.pop()
        for neighbour in adjacency.get(current, set()):
            if neighbour not in reachable:
                reachable.add(neighbour)
                frontier.append(neighbour)
    unreachable = sorted(actual_nodes - reachable)

    actual_gens = _node_devices(sld, "gens")
    actual_loads = _node_devices(sld, "loads")
    missing_gens = sorted(set(expected["generators"]) - actual_gens)
    missing_loads = sorted(set(expected["loads"]) - actual_loads)
    passed = not any((missing_edges, extra_edges, endpoint_mismatches, kind_mismatches, status_mismatches,
                      missing_nodes, extra_nodes, missing_gens, missing_loads, unreachable))
    return {
        "passed": passed,
        "sld_source": sld_source,
        "nodes": {
            "expected": expected["nodes"],
            "actual": sorted(actual_nodes),
            "missing": missing_nodes,
            "extra": extra_nodes,
        },
        "edges": {
            "expected": expected["edges"],
            "actual": [actual_edges[key] for key in sorted(actual_edges)],
            "missing": missing_edges,
            "extra": extra_edges,
            "endpoint_mismatches": endpoint_mismatches,
            "kind_mismatches": kind_mismatches,
            "status_mismatches": status_mismatches,
            "status_available": status_available,
        },
        "devices": {
            "generators_expected": expected["generators"],
            "generators_actual": sorted(actual_gens),
            "generators_missing": missing_gens,
            "loads_expected": expected["loads"],
            "loads_actual": sorted(actual_loads),
            "loads_missing": missing_loads,
        },
        "connectivity": {
            "sources": sorted(source_nodes),
            "unreachable": unreachable,
        },
    }


def _event_state_statuses(expected: dict[str, Any], events: list[dict[str, Any]], count: int) -> dict[str, str]:
    statuses = {edge["id"]: edge["status"] for edge in expected["edges"]}
    for event in events[:count]:
        kind = _norm(event.get("kind"))
        target = _norm(event.get("target"))
        if kind in {"open", "close"} and target in statuses:
            statuses[target] = "open" if kind == "open" else "closed"
    return statuses


def _snapshot_index(snapshot_id: str, event_count: int) -> int | None:
    match = re.fullmatch(r"after-event-(\d+)", snapshot_id)
    if match:
        return int(match.group(1))
    if snapshot_id == "final":
        return event_count
    return None


def _dynamic_event_sld_check(
    result: dict[str, Any], expected: dict[str, Any], events: list[dict[str, Any]],
) -> dict[str, Any]:
    snapshots = [item for item in result.get("sld_snapshots", []) or [] if isinstance(item, dict)]
    if not snapshots:
        return {"available": False, "passed": True, "status": "not_available", "reason": "no dynamic SLD snapshots"}
    findings: list[dict[str, Any]] = []
    for snapshot in snapshots:
        snapshot_id = str(snapshot.get("id") or "")
        event_count = _snapshot_index(snapshot_id, len(events))
        if event_count is None or snapshot_id == "initial":
            continue
        sld = snapshot.get("sld")
        if snapshot.get("status") != "available" or not isinstance(sld, dict):
            findings.append({
                "snapshot": snapshot_id,
                "status": "blocked",
                "reason": (snapshot.get("solver_provenance") or {}).get("reason")
                or "value-bearing SLD snapshot is missing",
            })
            continue
        actual = {
            _edge_id(edge.get("id")): _norm(edge.get("status"))
            for edge in sld.get("edges", []) or []
            if _edge_id(edge.get("id"))
        }
        snapshot_time = snapshot.get("t_s")
        effective_count = event_count
        if isinstance(snapshot_time, (int, float)):
            effective_count = max(
                effective_count,
                sum(
                    1 for event in events
                    if isinstance(event.get("t"), (int, float))
                    and float(event["t"]) <= float(snapshot_time) + 1e-9
                ),
            )
        # OpenDSS solves one shared post-boundary state for simultaneous
        # clear/open events, so every snapshot at that timestamp sees both
        # operations even when it carries two event ids.
        expected_statuses = _event_state_statuses(expected, events, effective_count)
        mismatches = [
            {"id": identifier, "expected": status, "actual": actual.get(identifier, "")}
            for identifier, status in sorted(expected_statuses.items())
            if actual.get(identifier) != status
        ]
        findings.append({"snapshot": snapshot_id, "status": "pass" if not mismatches else "fail", "mismatches": mismatches})
    blocked = [item for item in findings if item["status"] == "blocked"]
    failed = [item for item in findings if item["status"] == "fail"]
    return {
        "available": True,
        "passed": not blocked and not failed,
        "status": "blocked" if blocked else ("fail" if failed else "pass"),
        "findings": findings,
        "claim": "event-state edge status only; fault electrical response remains a separate physics audit",
    }


def _layout_check(layout: dict[str, Any] | None, expected: dict[str, Any]) -> dict[str, Any]:
    if layout is None:
        return {"available": False, "passed": False, "reason": "sld-layout artifact missing"}
    nodes = layout.get("nodes") or {}
    actual_nodes = {_norm(key) for key in nodes}
    expected_nodes = set(expected["nodes"])
    route_points = layout.get("route_points") or (layout.get("geometry") or {}).get("route_points") or {}
    actual_routes = {_edge_id(key) for key in route_points}
    expected_routes = {edge["id"] for edge in expected["edges"]}
    endpoint_mismatches = []
    for edge in expected["edges"]:
        points = route_points.get(edge["id"])
        if points is None:
            points = next((value for key, value in route_points.items() if _edge_id(key) == edge["id"]), None)
        src = nodes.get(next((key for key in nodes if _norm(key) == edge["src"]), ""), {})
        dst = nodes.get(next((key for key in nodes if _norm(key) == edge["dst"]), ""), {})
        if not points or not src or not dst:
            continue
        start = tuple(points[0][:2])
        end = tuple(points[-1][:2])
        expected_start = (src.get("x"), src.get("y"))
        expected_end = (dst.get("x"), dst.get("y"))
        start_ok = start == expected_start or _point_on_layout_busbar(start, src)
        end_ok = end == expected_end or _point_on_layout_busbar(end, dst)
        if not start_ok or not end_ok:
            endpoint_mismatches.append({
                "id": edge["id"],
                "expected": [expected_start, expected_end],
                "actual": [start, end],
            })
    geometry = layout.get("geometry") or {}
    axis_backtracks = sum(_route_axis_backtrack_count(points) for points in route_points.values())
    axis_backtrack_failure = (
        str(geometry.get("route_safety_policy") or "") == "zero-for-radial-forest"
        and axis_backtracks > 0
    )
    passed = not any((expected_nodes - actual_nodes, actual_nodes - expected_nodes,
                      expected_routes - actual_routes, actual_routes - expected_routes,
                      endpoint_mismatches, geometry.get("crossing_count", 0),
                      geometry.get("overlap_count", 0), geometry.get("node_overlap_count", 0),
                      axis_backtrack_failure))
    return {
        "available": True,
        "passed": passed,
        "nodes": {
            "missing": sorted(expected_nodes - actual_nodes),
            "extra": sorted(actual_nodes - expected_nodes),
        },
        "routes": {
            "missing": sorted(expected_routes - actual_routes),
            "extra": sorted(actual_routes - expected_routes),
            "endpoint_mismatches": endpoint_mismatches,
        },
        "geometry": {
            "crossing_count": geometry.get("crossing_count", 0),
            "overlap_count": geometry.get("overlap_count", 0),
            "node_overlap_count": geometry.get("node_overlap_count", 0),
            "axis_backtrack_count": axis_backtracks,
        },
    }


def _native_readback_check(provenance: dict[str, Any] | None) -> dict[str, Any]:
    if provenance is None:
        return {"available": False, "passed": False, "reason": "native SLD provenance artifact missing"}
    readback = provenance.get("post_replay_readback") or {}
    passed = bool(
        provenance.get("renderer") == "powerfactory-native"
        and provenance.get("native_sld_mode") == "command-layout+canonical-replay"
        and readback.get("passed") is True
        and readback.get("finding_count") == 0
        and readback.get("coverage") == "all-finite-intgrfcon-segments"
    )
    return {
        "available": True,
        "passed": passed,
        "renderer": provenance.get("renderer"),
        "native_sld_mode": provenance.get("native_sld_mode"),
        "page_name": provenance.get("page_name"),
        "readback": {
            "passed": readback.get("passed"),
            "finding_count": readback.get("finding_count"),
            "coverage": readback.get("coverage"),
            "graphic_count": readback.get("graphic_count"),
            "connection_count": readback.get("connection_count"),
        },
    }


def _identity_check(identity_map: dict[str, Any] | None, expected: dict[str, Any]) -> dict[str, Any]:
    if identity_map is None:
        return {"available": False, "passed": False, "reason": "identity-map artifact missing"}
    actual = {_norm(item.get("canonical_id")) for item in identity_map.get("assets", []) or []}
    missing = sorted(set(expected["assets"]) - actual)
    return {"available": True, "passed": not missing, "missing": missing, "actual_count": len(actual)}


def audit_topology_artifacts(
    case: dict[str, Any],
    powerfactory_results: dict[str, Any],
    opendss_results: dict[str, Any],
    *,
    powerfactory_layout: dict[str, Any] | None = None,
    powerfactory_provenance: dict[str, Any] | None = None,
    powerfactory_identity_map: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a deterministic topology audit for persisted study artifacts."""
    expected = _expected(case)
    pf = _graph_check(powerfactory_results, expected)
    dss = _graph_check(opendss_results, expected)
    layout = _layout_check(powerfactory_layout, expected)
    native = _native_readback_check(powerfactory_provenance)
    identity = _identity_check(powerfactory_identity_map, expected)
    events = [
        item for item in ((case.get("dynamics") or {}).get("events", []) or [])
        if isinstance(item, dict)
    ]
    dynamic: dict[str, Any] = {
        "powerfactory": _dynamic_event_sld_check(powerfactory_results, expected, events),
        "opendss": _dynamic_event_sld_check(opendss_results, expected, events),
    }
    dynamic["passed"] = bool(dynamic["powerfactory"].get("passed")) and bool(dynamic["opendss"].get("passed"))
    checks = {
        "powerfactory_logical_sld": pf,
        "opendss_logical_sld": dss,
        "powerfactory_canonical_layout": layout,
        "powerfactory_native_readback": native,
        "powerfactory_identity_map": identity,
        "dynamic_event_sld": dynamic,
    }
    passed = all(bool(item.get("passed")) for item in checks.values())
    return {
        "schema": SCHEMA,
        "passed": passed,
        "verdict": "pass_with_visual_review" if passed else "fail",
        "visual_review": {
            "status": "manual_review_required",
            "reason": (
                "The native PNG is raster evidence; connector readback proves geometry, "
                "but label/glyph legibility and crop completeness still require a human view."
            ),
        },
        "expected": expected,
        "checks": checks,
        "reasons": [
            f"{name}: {item.get('reason', 'failed')}"
            for name, item in checks.items()
            if not item.get("passed") and item.get("reason")
        ],
    }
