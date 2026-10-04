"""Small, renderer-independent SLD geometry checks."""

from __future__ import annotations

from typing import Any

from cept.schema.sld import SLDModel


def _lane_offset(lane: int, spacing: float = 10.0) -> float:
    """Return alternating deterministic offsets for parallel Manhattan lanes."""
    if lane <= 0:
        return 0.0
    magnitude = ((lane + 1) // 2) * spacing
    return magnitude if lane % 2 == 0 else -magnitude


def orthogonal_routes(sld: SLDModel) -> dict[str, list[tuple[float, float]]]:
    """Return stable Manhattan routes for every branch."""
    points = {n.id.lower(): (float(n.x), float(n.y)) for n in sld.nodes}
    routes: dict[str, list[tuple[float, float]]] = {}
    lane_counts: dict[tuple[str, str], int] = {}
    for edge in sld.edges:
        if edge.route_points:
            routes[edge.id] = [(float(x), float(y)) for x, y in edge.route_points]
            continue
        a, b = points.get(edge.src.lower()), points.get(edge.dst.lower())
        pair = tuple(sorted((edge.src.lower(), edge.dst.lower())))
        lane = lane_counts.get(pair, 0)
        lane_counts[pair] = lane + 1
        if a is None or b is None:
            routes[edge.id] = []
        elif lane and (abs(a[0] - b[0]) < 1e-9 or abs(a[1] - b[1]) < 1e-9):
            offset = _lane_offset(lane)
            if abs(a[1] - b[1]) < 1e-9:
                routes[edge.id] = [a, (a[0], a[1] + offset), (b[0], b[1] + offset), b]
            else:
                routes[edge.id] = [a, (a[0] + offset, a[1]), (b[0] + offset, b[1]), b]
        elif abs(a[0] - b[0]) < 1e-9 or abs(a[1] - b[1]) < 1e-9:
            routes[edge.id] = [a, b]
        else:
            # Stable choice; the renderer uses the same source-axis policy.
            routes[edge.id] = [a, (a[0], b[1]), b]
    return routes


def validate_geometry(sld: SLDModel) -> dict[str, Any]:
    """Fail closed on diagonal, overlapping, or crossing SLD geometry."""
    routes = orthogonal_routes(sld)
    diagonal = 0
    segments: list[tuple[str, tuple[float, float], tuple[float, float]]] = []
    for edge_id, route in routes.items():
        for left, right in zip(route, route[1:]):
            if abs(left[0] - right[0]) > 1e-9 and abs(left[1] - right[1]) > 1e-9:
                diagonal += 1
            segments.append((edge_id, left, right))

    def bbox(a, b):
        return min(a[0], b[0]), max(a[0], b[0]), min(a[1], b[1]), max(a[1], b[1])

    overlap = 0
    crossing = 0
    for index, (edge_a, a, b) in enumerate(segments):
        for edge_b, c, d in segments[index + 1 :]:
            if edge_a == edge_b:
                continue
            # Shared electrical terminals are expected, not crossings.
            if {a, b} & {c, d}:
                continue
            ax0, ax1, ay0, ay1 = bbox(a, b)
            cx0, cx1, cy0, cy1 = bbox(c, d)
            if ax1 < cx0 or cx1 < ax0 or ay1 < cy0 or cy1 < ay0:
                continue
            a_h = abs(a[1] - b[1]) < 1e-9
            c_h = abs(c[1] - d[1]) < 1e-9
            if a_h == c_h:
                if a_h and abs(a[1] - c[1]) < 1e-9:
                    overlap += 1
                elif not a_h and abs(a[0] - c[0]) < 1e-9:
                    overlap += 1
            else:
                crossing += 1

    points = [(n.id, float(n.x), float(n.y)) for n in sld.nodes]
    point_overlap = sum(
        1
        for index, (_, x, y) in enumerate(points)
        for _, ox, oy in points[index + 1 :]
        if abs(x - ox) < 1e-9 and abs(y - oy) < 1e-9
    )
    return {
        "verdict": "pass"
        if diagonal == 0 and overlap == 0 and crossing == 0 and point_overlap == 0
        else "blocked",
        "diagonal_segment_count": diagonal,
        "overlap_count": overlap + point_overlap,
        "crossing_count": crossing,
        "route_points": routes,
        "node_overlap_count": point_overlap,
    }


def topology_signature(sld: SLDModel) -> dict[str, Any]:
    return {
        "nodes": sorted(n.id.lower() for n in sld.nodes),
        "edges": sorted((e.id.lower(), e.src.lower(), e.dst.lower(), e.kind, e.status) for e in sld.edges),
    }


def compare_geometry(source: SLDModel, generated: SLDModel) -> dict[str, Any]:
    """Compare topology and relative coordinate orientation, never pixel values."""
    source_sig = topology_signature(source)
    generated_sig = topology_signature(generated)
    common = set(n.id.lower() for n in source.nodes) & set(n.id.lower() for n in generated.nodes)
    source_by_id = {n.id.lower(): n for n in source.nodes}
    generated_by_id = {n.id.lower(): n for n in generated.nodes}
    reflections = 0
    for left in source.nodes:
        for right in source.nodes:
            if (
                left.id.lower() >= right.id.lower()
                or left.id.lower() not in common
                or right.id.lower() not in common
            ):
                continue
            a = generated_by_id[left.id.lower()]
            b = generated_by_id[right.id.lower()]
            if (left.x - right.x) * (a.x - b.x) < 0 or (left.y - right.y) * (a.y - b.y) < 0:
                reflections += 1

    def _aspect(nodes: list[Any]) -> float:
        if len(nodes) < 2:
            return 1.0
        width = max(n.x for n in nodes) - min(n.x for n in nodes)
        height = max(n.y for n in nodes) - min(n.y for n in nodes)
        return width / height if height else float("inf")

    source_aspect = _aspect([source_by_id[name] for name in common])
    generated_aspect = _aspect([generated_by_id[name] for name in common])
    if source_aspect == float("inf") or generated_aspect == float("inf"):
        aspect_ratio_error = 0.0 if source_aspect == generated_aspect else float("inf")
    else:
        aspect_ratio_error = abs(generated_aspect - source_aspect) / max(abs(source_aspect), 1e-9)
    overlap = 0
    for i, left in enumerate(generated.nodes):
        for right in generated.nodes[i + 1 :]:
            if abs(left.x - right.x) < 1e-9 and abs(left.y - right.y) < 1e-9:
                overlap += 1
    aspect_ratio_ok = aspect_ratio_error <= 0.25
    topology_ok = source_sig == generated_sig
    return {
        "verdict": "pass"
        if topology_ok and reflections == 0 and aspect_ratio_ok and overlap == 0
        else "warning",
        "topology_match": topology_ok,
        "shared_nodes": len(common),
        "unexpected_reflections": reflections,
        "aspect_ratio_source": source_aspect,
        "aspect_ratio_generated": generated_aspect,
        "aspect_ratio_error": aspect_ratio_error,
        "aspect_ratio_ok": aspect_ratio_ok,
        "overlap_count": overlap,
        "source": source_sig,
        "generated": generated_sig,
    }


__all__ = ["compare_geometry", "topology_signature", "orthogonal_routes", "validate_geometry"]
