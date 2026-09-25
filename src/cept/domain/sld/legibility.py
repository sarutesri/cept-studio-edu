"""Renderer-neutral SLD Presentation / Legibility Gate v2.

The browser or native renderer owns *measurement* of actual symbol/text boxes.
This module owns the deterministic policy applied to those measurements.  It
therefore works for estimated pre-render geometry today and for Playwright or
PowerFactory measured geometry during the final local qualification pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal, Mapping, Sequence

from cept.domain.sld.geometry import Point

FindingKind = Literal[
    "symbol_symbol",
    "symbol_text",
    "text_text",
    "line_text",
    "line_device",
    "branch_crossing",
    "clipped_label",
]

_EPS = 1e-9


@dataclass(frozen=True, slots=True)
class Rect:
    left: float
    top: float
    right: float
    bottom: float

    def __post_init__(self) -> None:
        if self.right < self.left or self.bottom < self.top:
            raise ValueError("rectangle bounds are inverted")

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top

    def intersects(self, other: "Rect", *, clearance: float = 0.0) -> bool:
        return not (
            self.right + clearance <= other.left
            or other.right + clearance <= self.left
            or self.bottom + clearance <= other.top
            or other.bottom + clearance <= self.top
        )

    def contains_rect(self, other: "Rect", *, clearance: float = 0.0) -> bool:
        return (
            other.left >= self.left + clearance
            and other.right <= self.right - clearance
            and other.top >= self.top + clearance
            and other.bottom <= self.bottom - clearance
        )


@dataclass(frozen=True, slots=True)
class RoutedLine:
    line_id: str
    points: tuple[Point, ...]
    src: str | None = None
    dst: str | None = None


@dataclass(frozen=True, slots=True)
class LegibilityFinding:
    kind: FindingKind
    first: str
    second: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class LegibilityReport:
    findings: tuple[LegibilityFinding, ...]
    schema: str = "cept-sld-legibility-v2"

    @property
    def passed(self) -> bool:
        return not self.findings

    @property
    def verdict(self) -> str:
        return "pass" if self.passed else "blocked"

    @property
    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for finding in self.findings:
            result[finding.kind] = result.get(finding.kind, 0) + 1
        return result

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "passed": self.passed,
            "verdict": self.verdict,
            "counts": self.counts,
            "findings": [
                {
                    "kind": item.kind,
                    "first": item.first,
                    "second": item.second,
                    "detail": item.detail,
                }
                for item in self.findings
            ],
        }


def _pairs(mapping: Mapping[str, Rect]) -> Iterable[tuple[str, Rect, str, Rect]]:
    names = sorted(mapping)
    for index, first in enumerate(names):
        for second in names[index + 1 :]:
            yield first, mapping[first], second, mapping[second]


def _orientation(a: Point, b: Point, c: Point) -> float:
    return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)


def _between(value: float, left: float, right: float) -> bool:
    return min(left, right) - _EPS <= value <= max(left, right) + _EPS


def _point_on_segment(point: Point, left: Point, right: Point) -> bool:
    return (
        abs(_orientation(left, right, point)) <= _EPS
        and _between(point.x, left.x, right.x)
        and _between(point.y, left.y, right.y)
    )


def _proper_segment_intersection(a: Point, b: Point, c: Point, d: Point) -> bool:
    """True for a crossing/overlap away from a shared electrical endpoint."""
    shared = {a, b}.intersection({c, d})
    if shared:
        return False
    o1 = _orientation(a, b, c)
    o2 = _orientation(a, b, d)
    o3 = _orientation(c, d, a)
    o4 = _orientation(c, d, b)
    if ((o1 > _EPS and o2 < -_EPS) or (o1 < -_EPS and o2 > _EPS)) and (
        (o3 > _EPS and o4 < -_EPS) or (o3 < -_EPS and o4 > _EPS)
    ):
        return True
    # Collinear or T-junction contact away from shared route endpoints is also
    # visually ambiguous and is therefore a crossing for the presentation gate.
    return (
        (abs(o1) <= _EPS and _point_on_segment(c, a, b))
        or (abs(o2) <= _EPS and _point_on_segment(d, a, b))
        or (abs(o3) <= _EPS and _point_on_segment(a, c, d))
        or (abs(o4) <= _EPS and _point_on_segment(b, c, d))
    )


def _segment_hits_rect(left: Point, right: Point, rect: Rect, *, clearance: float = 0.0) -> bool:
    expanded = Rect(
        rect.left - clearance,
        rect.top - clearance,
        rect.right + clearance,
        rect.bottom + clearance,
    )
    if expanded.left <= left.x <= expanded.right and expanded.top <= left.y <= expanded.bottom:
        return True
    if expanded.left <= right.x <= expanded.right and expanded.top <= right.y <= expanded.bottom:
        return True
    corners = (
        Point(expanded.left, expanded.top),
        Point(expanded.right, expanded.top),
        Point(expanded.right, expanded.bottom),
        Point(expanded.left, expanded.bottom),
    )
    borders = tuple(zip(corners, corners[1:] + corners[:1]))
    return any(_proper_segment_intersection(left, right, a, b) for a, b in borders)


def _line_hits_rect(line: RoutedLine, rect: Rect, *, clearance: float) -> bool:
    return any(
        _segment_hits_rect(left, right, rect, clearance=clearance)
        for left, right in zip(line.points, line.points[1:])
    )


def evaluate_legibility(
    *,
    symbols: Mapping[str, Rect],
    texts: Mapping[str, Rect],
    lines: Sequence[RoutedLine],
    devices: Mapping[str, Rect] | None = None,
    viewport: Rect | None = None,
    symbol_clearance: float = 2.0,
    text_clearance: float = 2.0,
    line_clearance: float = 1.0,
    ignore_line_symbol_pairs: Iterable[tuple[str, str]] = (),
) -> LegibilityReport:
    """Evaluate measured SLD geometry against the v2 presentation contract.

    ``ignore_line_symbol_pairs`` is for intended electrical contacts such as a
    branch touching its own transformer symbol.  Nothing is ignored by default.
    """
    devices = devices or {}
    ignored = {(line_id, symbol_id) for line_id, symbol_id in ignore_line_symbol_pairs}
    findings: list[LegibilityFinding] = []

    for first, first_box, second, second_box in _pairs(symbols):
        if first_box.intersects(second_box, clearance=symbol_clearance):
            findings.append(
                LegibilityFinding("symbol_symbol", first, second, "symbol bounds overlap or violate clearance")
            )

    for symbol_id, symbol_box in sorted(symbols.items()):
        for text_id, text_box in sorted(texts.items()):
            if symbol_box.intersects(text_box, clearance=text_clearance):
                findings.append(
                    LegibilityFinding("symbol_text", symbol_id, text_id, "symbol and text bounds overlap")
                )

    for first, first_box, second, second_box in _pairs(texts):
        if first_box.intersects(second_box, clearance=text_clearance):
            findings.append(LegibilityFinding("text_text", first, second, "text bounds overlap"))

    for line in sorted(lines, key=lambda item: item.line_id):
        for text_id, text_box in sorted(texts.items()):
            if _line_hits_rect(line, text_box, clearance=line_clearance):
                findings.append(LegibilityFinding("line_text", line.line_id, text_id, "route intersects text bounds"))
        for device_id, device_box in sorted(devices.items()):
            if (line.line_id, device_id) in ignored:
                continue
            if _line_hits_rect(line, device_box, clearance=line_clearance):
                findings.append(
                    LegibilityFinding("line_device", line.line_id, device_id, "route intersects device bounds")
                )

    for index, first_line in enumerate(sorted(lines, key=lambda item: item.line_id)):
        for second_line in sorted(lines, key=lambda item: item.line_id)[index + 1 :]:
            # Electrical branches sharing a source/destination bus naturally
            # meet at that bus. Shared point intersections are filtered by the
            # segment routine; any other crossing is presentation debt.
            crossing = any(
                _proper_segment_intersection(a, b, c, d)
                for a, b in zip(first_line.points, first_line.points[1:])
                for c, d in zip(second_line.points, second_line.points[1:])
            )
            if crossing:
                findings.append(
                    LegibilityFinding(
                        "branch_crossing",
                        first_line.line_id,
                        second_line.line_id,
                        "routes cross away from endpoint",
                    )
                )

    if viewport is not None:
        for text_id, text_box in sorted(texts.items()):
            if not viewport.contains_rect(text_box):
                findings.append(
                    LegibilityFinding("clipped_label", text_id, None, "text bounds extend outside viewport")
                )

    findings.sort(key=lambda item: (item.kind, item.first, item.second or "", item.detail))
    return LegibilityReport(findings=tuple(findings))


__all__ = [
    "FindingKind",
    "LegibilityFinding",
    "LegibilityReport",
    "Rect",
    "RoutedLine",
    "evaluate_legibility",
]
