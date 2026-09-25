"""Stable machine-readable receipts for Canonical SLD Geometry v2.

A renderer can prove it consumed the same geometry plan as another renderer by
recording this payload/fingerprint beside its own rendering evidence.  The
fingerprint intentionally covers only canonical presentation geometry; zoom,
pan, theme, solver values, and renderer-specific object identifiers are not
part of it.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from cept.domain.sld.geometry import CanonicalSLDGeometry, route_axis_backtrack_count

SCHEMA = "cept-canonical-sld-geometry-receipt-v1"


def geometry_payload(geometry: CanonicalSLDGeometry) -> dict[str, Any]:
    """Return the stable JSON payload used for cross-renderer comparison."""
    buses = [
        {
            "id": bus.bus_id.lower(),
            "center": [bus.center.x, bus.center.y],
            "orientation": bus.orientation,
            "half_length": bus.half_length,
            "ports": [
                {
                    "edge_id": port.edge_id.lower(),
                    "point": [port.point.x, port.point.y],
                    "direction": port.direction,
                }
                for port in sorted(bus.ports, key=lambda item: item.edge_id.lower())
            ],
        }
        for bus in sorted(geometry.buses, key=lambda item: item.bus_id.lower())
    ]
    routes = [
        {
            "edge_id": route.edge_id.lower(),
            "src": route.src.lower(),
            "dst": route.dst.lower(),
            "lane": route.lane,
            "points": [[point.x, point.y] for point in route.points],
            "src_port": {
                "bus_id": route.src_port.bus_id.lower(),
                "point": [route.src_port.point.x, route.src_port.point.y],
                "direction": route.src_port.direction,
            },
            "dst_port": {
                "bus_id": route.dst_port.bus_id.lower(),
                "point": [route.dst_port.point.x, route.dst_port.point.y],
                "direction": route.dst_port.direction,
            },
        }
        for route in sorted(geometry.routes, key=lambda item: item.edge_id.lower())
    ]
    return {
        "schema": SCHEMA,
        "geometry_version": geometry.version,
        "buses": buses,
        "routes": routes,
    }


def geometry_fingerprint(geometry: CanonicalSLDGeometry) -> str:
    """Return SHA-256 over canonical compact JSON with sorted object keys."""
    encoded = json.dumps(
        geometry_payload(geometry),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def geometry_receipt(
    geometry: CanonicalSLDGeometry,
    *,
    consumer: str,
    renderer: str | None = None,
) -> dict[str, Any]:
    """Return a compact receipt suitable for report/PFD evidence metadata."""
    return {
        "schema": SCHEMA,
        "consumer": consumer,
        "renderer": renderer,
        "geometry_version": geometry.version,
        "geometry_sha256": geometry_fingerprint(geometry),
        "bus_count": len(geometry.buses),
        "route_count": len(geometry.routes),
        "axis_backtrack_count": sum(
            route_axis_backtrack_count(route.points) for route in geometry.routes
        ),
    }


__all__ = ["SCHEMA", "geometry_fingerprint", "geometry_payload", "geometry_receipt"]
