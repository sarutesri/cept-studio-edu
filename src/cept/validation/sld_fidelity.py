"""SLD structural-fidelity gate (Pillar A of MASTER_PLAN.md).

The rendered single-line diagram must faithfully depict the *solved Case*, not
a hand-placed sketch: every bus, every load/generator/DER, the grid connection,
and the branch endpoints it draws must trace back to Case elements.  This is a
deterministic, solver-artifact-only check (like ``audit_run``) so a small model
can author code but cannot author "correctness".

Checks (all read the Case + the solver's SLDModel; nothing is re-solved):
* **Bus completeness / no unknown buses** (inline cases) — the SLD node set
  must equal the Case's AC + DC bus set (lowercased).  Missing buses FAIL;
  extra buses are recorded as a warning (OpenDSS may synthesize an internal
  source bus).
* **Grid attached (regression for T-012)** — when an inline external grid sits
  on a bus, that bus must be tagged ``substation``; otherwise a grid-tied
  diagram loses its utility connection.
* **Device parity (inline)** — every inline load (+ DC load) appears in some
  SLD node's ``loads`` and every inline generator + DER (+ DC source) appears
  in some node's ``gens``. Every inline converter appears as an SLD edge of
  kind ``converter`` (one edge per DC pole).
* **Branch endpoint existence (inline)** — every inline line/transformer
  endpoints are SLD nodes; every converter pole endpoint is an SLD node.
* **Collision** — reuse the renderer-consistent ``collision_check``; major
  overlap (Blocked) FAILs, minor (Crowded) is a warning.
* **Fingerprint identity** — ``results.case_fingerprint`` must equal the Case
  fingerprint.

On a PFD/``dss_file`` Case (no inline network) the per-element parity checks
are skipped (Kron-eliminated return conductors legitimately drop branch edges);
grid/collision/fingerprint still apply.  Emits a ``cept-sld-fidelity-v1``
record written to ``sld-fidelity.json`` beside ``results.json``.
"""

from __future__ import annotations

from typing import Any

from cept.schema import Case
from cept.schema.result import StudyResult


def sld_fidelity(case: Case, result: StudyResult) -> dict[str, Any]:
    """Return a machine-readable SLD-fidelity verdict for a solved result."""
    schema = "cept-sld-fidelity-v1"
    reasons: list[str] = []
    warnings: list[str] = []
    checks: dict[str, Any] = {}

    sld = result.sld
    if sld is None or not sld.nodes:
        return {
            "schema": schema,
            "verdict": "fail",
            "passed": False,
            "n_nodes": 0,
            "n_edges": 0,
            "reasons": ["no SLD nodes present"],
            "checks": checks,
            "case_fingerprint": case.fingerprint(),
        }

    sld_nodes = {n.id.lower(): n for n in sld.nodes}
    checks["n_nodes"] = len(sld_nodes)
    checks["n_edges"] = len(sld.edges)

    inline = case.network.inline if case.network.kind == "inline" else None

    # --- fingerprint identity -------------------------------------------------
    expected = case.fingerprint()
    checks["fingerprint"] = {"expected": expected, "actual": result.case_fingerprint}
    if result.case_fingerprint != expected:
        reasons.append("results.case_fingerprint does not match the Case fingerprint")

    # --- bus completeness / unknown buses (inline only) -----------------------
    if inline is not None:
        case_buses = {b.name.lower() for b in inline.buses}
        case_buses |= {b.name.lower() for b in inline.dc_buses or []}
        missing = case_buses - set(sld_nodes)
        extra = set(sld_nodes) - case_buses
        checks["bus_parity"] = {
            "expected": sorted(case_buses),
            "missing": sorted(missing),
            "extra": sorted(extra),
        }
        if missing:
            reasons.append(f"SLD missing declared case buses: {sorted(missing)}")
        if extra:
            warnings.append(f"SLD shows undeclared bus(es): {sorted(extra)}")

        # --- grid attached / visible (T-012) ----------------------------------
        # The utility connection must be *drawn*: a grid-tied case needs a
        # substation source node connected to the attachment bus by a grid
        # edge.  Tagging the attachment bus alone used to pass while the SLD
        # showed a floating bus-bar with no line (single-bus OpenDER cases) —
        # the T-012 fuller fix makes that a FAIL.
        def bus_id(bus_ref: str) -> str:
            canonical = bus_ref.lower()
            return canonical if canonical in sld_nodes else canonical.split(".", 1)[0]

        ext_buses = {bus_id(g.bus) for g in (inline.external_grids or [])}
        substation_ids = {nid for nid, n in sld_nodes.items() if n.kind == "substation"}
        linked_to_sub: set[str] = set()
        for edge in sld.edges:
            s, d = edge.src.lower(), edge.dst.lower()
            if s in substation_ids and d not in substation_ids:
                linked_to_sub.add(d)
            if d in substation_ids and s not in substation_ids:
                linked_to_sub.add(s)
        checks["grid"] = {
            "external_grid_buses": sorted(ext_buses),
            "substation_buses": sorted(substation_ids),
            "linked_to_substation": sorted(linked_to_sub),
        }
        if ext_buses:
            missing_sub = sorted(ext_buses - (substation_ids | linked_to_sub))
            if missing_sub:
                reasons.append(
                    f"external-grid bus(es) not visibly connected to a substation source: {missing_sub}"
                )

        # --- device parity -----------------------------------------------------
        loads_seen = {ld.name.lower() for n in sld_nodes.values() for ld in n.loads}
        gens_seen = {g.name.lower() for n in sld_nodes.values() for g in n.gens}
        case_loads = {ld.id.lower() for ld in inline.loads}
        case_loads |= {ld.name.lower() for ld in inline.dc_loads or []}
        case_gens = {g.name.lower() for g in inline.generators}
        case_gens |= {d.id.lower() for d in case.ders}
        case_gens |= {s.name.lower() for s in inline.dc_sources or []}
        conv_edges = {
            e.id.lower() for e in sld.edges if str(e.kind) == "converter"
        }
        case_convs: set[str] = set()
        for conv in inline.converters or []:
            case_convs.add(conv.name.lower())
            if conv.dc_minus_bus is not None:
                case_convs.add(f"{conv.name}-minus".lower())
        load_missing = sorted(case_loads - loads_seen)
        gen_missing = sorted(case_gens - gens_seen)
        conv_missing = sorted(case_convs - conv_edges)
        checks["device_parity"] = {
            "loads_expected": len(case_loads),
            "gens_expected": len(case_gens),
            "converters_expected": len(case_convs),
        }
        if load_missing:
            reasons.append(f"SLD missing loads: {load_missing}")
        if gen_missing:
            reasons.append(f"SLD missing generators/DERs: {gen_missing}")
        if conv_missing:
            reasons.append(f"SLD missing converters: {conv_missing}")

        # --- branch endpoint existence ----------------------------------------
        branch_endpoint_missing: list[str] = []
        for line in inline.lines:
            for b in (line.from_bus, line.to_bus):
                if b.lower() not in sld_nodes and f"{line.name}:{b}" not in branch_endpoint_missing:
                    branch_endpoint_missing.append(f"{line.name}:{b}")
        for tr in inline.transformers:
            for b in (tr.hv_bus, tr.lv_bus):
                if b.lower() not in sld_nodes and f"{tr.name}:{b}" not in branch_endpoint_missing:
                    branch_endpoint_missing.append(f"{tr.name}:{b}")
        for conv in inline.converters or []:
            poles = [conv.dc_plus_bus]
            if conv.dc_minus_bus is not None:
                poles.append(conv.dc_minus_bus)
            for b in [conv.bus, *poles]:
                if b.lower() not in sld_nodes and f"{conv.name}:{b}" not in branch_endpoint_missing:
                    branch_endpoint_missing.append(f"{conv.name}:{b}")
        checks["branch_endpoints"] = {"missing": sorted(branch_endpoint_missing)}
        if branch_endpoint_missing:
            reasons.append(f"inline branch endpoint(s) missing from SLD: {sorted(branch_endpoint_missing)}")

    # --- connectivity / energisation (mirror the Level-0 structural gate) ----
    # The SLD can faithfully mirror the Case yet still depict a disconnected
    # network: the comparison gate's connectivity checks ask the *Case* the
    # questions, while the SLD derives from the same resolution and would agree
    # with it (the DistanceProtection failure class — a resolution error makes
    # both sides agree).  Here we ask the SLD itself: every node reachable from
    # a source (substation), and no self-loop branches.
    adj: dict[str, set[str]] = {nid: set() for nid in sld_nodes}
    for edge in sld.edges:
        s, d = edge.src.lower(), edge.dst.lower()
        if s == d:
            reasons.append(f"SLD self-loop edge '{edge.id}' (src==dst '{s}')")
            continue
        adj.setdefault(s, set()).add(d)
        adj.setdefault(d, set()).add(s)
    sources = {nid for nid, n in sld_nodes.items() if n.kind == "substation"}
    if inline is not None:
        # A slack generator is a voltage source even when it is not tagged as a
        # substation (no external grid) — its bus must still energise the rest.
        sources |= {g.bus.lower() for g in inline.generators if g.bus_type == "slack"}
    source_ids = sorted(nid for nid in sources if nid in sld_nodes)
    reachable: set[str] = set(source_ids)
    frontier = list(source_ids)
    while frontier:
        cur = frontier.pop()
        for neighbour in adj.get(cur, ()):
            if neighbour not in reachable:
                reachable.add(neighbour)
                frontier.append(neighbour)
    unreachable = sorted(set(sld_nodes) - reachable)
    checks["connectivity"] = {"sources": source_ids, "unreachable": unreachable}
    if unreachable:
        reasons.append(f"SLD bus(es) not connected to a source: {unreachable}")

    # --- collision (renderer-consistent) --------------------------------------
    # Guard so an internally-inconsistent SLD (e.g. an edge referencing a node
    # that was dropped) fails the gate cleanly instead of crashing the audit.
    from cept.reporting.collisions import rendered_collision_check

    collision: dict[str, Any] | None = None
    try:
        collision = rendered_collision_check(sld)
    except Exception as exc:  # malformed SLD -> fail-closed
        reasons.append(f"SLD collision check errored (internally inconsistent): {type(exc).__name__}")
    if collision is not None:
        checks["collision"] = {
            "verdict": collision["verdict"],
            "overlap_count": collision.get("overlap_count", 0),
            "major_count": collision.get("major_count", 0),
            "minor_count": collision.get("minor_count", 0),
        }
        if collision["verdict"] == "pass":
            pass
        elif collision["verdict"] == "warning":
            warnings.append(f"SLD has minor (crowded) overlaps: {collision.get('minor_count', 0)}")
        else:
            reasons.append(f"SLD has major overlap(s): {collision.get('major_count', 0)}")

    passed = not reasons
    return {
        "schema": schema,
        "verdict": "pass" if passed else "fail",
        "passed": passed,
        "n_nodes": checks["n_nodes"],
        "n_edges": checks["n_edges"],
        "reasons": reasons,
        "warnings": warnings,
        "checks": checks,
        "case_fingerprint": expected,
    }
