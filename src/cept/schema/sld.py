"""Single-line-diagram (SLD) data model.

This is a *view model*: an engine-agnostic, fully-resolved description of what
the diagram should show after a study has been solved. The OpenDSS adapter
populates it; the reporting layer turns it into an interactive ECharts graph.
Keeping it separate from the raw result means any engine (or a hand-built
network) can feed the same renderer.

Conventions
-----------
* Power sign: ``p_kw`` / ``q_kvar`` on an edge are the flow measured at the
  *source* terminal, positive in the src -> dst direction. The renderer draws
  the arrow accordingly (and reverses it when the value is negative).
* Phases: 1 = A, 2 = B, 3 = C.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# Generator / DER kinds get distinct symbols + colors in the diagram.
GenKind = Literal["pv", "wind", "hydro", "syncgen", "battery", "generator", "indmach", "grid"]
ShuntKind = Literal["capacitor", "reactor", "statcom", "svc"]
EdgeKind = Literal["line", "transformer", "switch", "regulator", "converter"]
NodeKind = Literal["substation", "bus"]
EventKind = Literal["fault", "open", "close", "trip_gen", "shed_load", "set_tap", "info"]


class SLDLoad(BaseModel):
    name: str
    kw: float = 0.0
    kvar: float = 0.0
    phases: list[int] = Field(default_factory=list)
    shed: bool = False


class SLDGen(BaseModel):
    name: str
    kind: GenKind = "generator"
    kw: float = 0.0
    kvar: float = 0.0
    phases: list[int] = Field(default_factory=list)
    tripped: bool = False


class SLDShunt(BaseModel):
    name: str
    kind: ShuntKind = "capacitor"
    kvar: float = 0.0
    phases: list[int] = Field(default_factory=list)


class SLDEvent(BaseModel):
    """An experiment annotation attached to a node or edge."""

    kind: EventKind
    label: str = ""


class SLDNode(BaseModel):
    id: str
    x: float
    y: float
    kind: NodeKind = "bus"
    kv_base: float = 0.0
    phases: list[int] = Field(default_factory=list)
    v_pu: dict[int, float] = Field(default_factory=dict)
    angle_deg: dict[int, float] = Field(default_factory=dict)
    loads: list[SLDLoad] = Field(default_factory=list)
    gens: list[SLDGen] = Field(default_factory=list)
    shunts: list[SLDShunt] = Field(default_factory=list)
    event: Optional[SLDEvent] = None

    @property
    def v_min(self) -> Optional[float]:
        return min(self.v_pu.values()) if self.v_pu else None

    @property
    def v_mean(self) -> Optional[float]:
        return sum(self.v_pu.values()) / len(self.v_pu) if self.v_pu else None


class SLDEdge(BaseModel):
    id: str
    src: str
    dst: str
    kind: EdgeKind = "line"
    phases: list[int] = Field(default_factory=list)
    p_kw: float = 0.0
    q_kvar: float = 0.0
    losses_kw: float = 0.0
    length: Optional[float] = None
    length_unit: str = ""
    tap: Optional[float] = None
    status: Literal["closed", "open"] = "closed"
    event: Optional[SLDEvent] = None
    # Optional canonical Manhattan route.  The solver owns values; this is
    # presentation geometry only and is therefore safe to persist alongside
    # the value-bearing edge.
    route_points: list[tuple[float, float]] = Field(default_factory=list)


class SLDModel(BaseModel):
    title: str = "Single-Line Diagram"
    nodes: list[SLDNode] = Field(default_factory=list)
    edges: list[SLDEdge] = Field(default_factory=list)
    # convenience aggregates for captions
    total_loss_kw: Optional[float] = None
    v_min_pu: float = 0.95
    v_max_pu: float = 1.05
