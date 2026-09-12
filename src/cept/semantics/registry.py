"""Reviewed semantic registry: the single place where raw names get meaning.

Before this module the same knowledge lived in three unrelated places -- a
channel dict in the source reference runner, a channel-mapping table inside the
OpenDSS adapter, and a synonym dict inside the comparator.  Only the first two
were reviewed; the third was string normalization, which cannot create parity
evidence (``docs/cept/DECISION_LOG.md``).

The rule the comparator depends on: :func:`resolve_channel` returns ``None``
for a raw channel that has no reviewed entry.  ``None`` means "unproven
semantics", which downstream must turn into a blocked row for that channel --
never into a guess.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from cept.schema.result import ChannelSemanticDescriptor

_DATA_DIR = Path(__file__).resolve().parent / "data"
_CHANNEL_FILE = _DATA_DIR / "channel-registry-v1.json"
_INPUT_FILE = _DATA_DIR / "input-registry-v1.json"
_COVERAGE_FILE = _DATA_DIR / "element-coverage-v1.json"
_ATTRIBUTE_FILE = _DATA_DIR / "attribute-registry-v1.json"
_CAPABILITY_FILE = _DATA_DIR / "engine-capability-v1.json"


@dataclass(frozen=True)
class ChannelSemantics:
    """One reviewed binding from a producer's raw channel to canonical meaning."""

    producer: str
    raw_channel: str
    canonical_quantity: str
    unit: str
    raw_unit: str
    phase: str
    sign_convention: str
    time_origin_kind: str
    reference_frame: str
    note: str = ""
    review_status: str = "reviewed"
    mapping_method: str = "reviewed-channel-registry"

    @property
    def sequence(self) -> str:
        """Reviewed sequence identity encoded by the legacy ``phase`` field.

        Older registry data used ``phase`` for values such as
        ``positive-sequence`` and ``three-phase``.  Expose the sequence axis
        explicitly without rewriting evidence data or guessing when the entry
        is genuinely phase-specific/unknown.
        """
        if self.phase.endswith("-sequence"):
            return self.phase.removesuffix("-sequence")
        if self.phase == "three-phase":
            return "three-phase"
        return ""

    @property
    def terminal(self) -> str:
        """Return an explicit terminal identity when the reviewed frame carries one."""
        if self.reference_frame in {"terminal", "machine-terminal", "bus1", "bus2"}:
            return self.reference_frame
        return ""

    @property
    def per_unit_base(self) -> str:
        """Return a proven per-unit base, never an inferred generic base."""
        if self.unit != "pu":
            return ""
        if self.reference_frame == "machine-base":
            return "machine"
        return "unknown"

    def as_row(self) -> dict[str, Any]:
        """Comparator row fields carried verbatim into the report."""
        return {
            "canonical_quantity": self.canonical_quantity,
            "unit": self.unit,
            "phase": self.phase,
            "sequence": self.sequence,
            "terminal": self.terminal,
            "per_unit_base": self.per_unit_base,
            "sign_convention": self.sign_convention,
            "reference_frame": self.reference_frame,
            "mapping_method": self.mapping_method,
            "review_status": self.review_status,
        }


def _load_json_object(path: Path) -> dict[str, Any]:
    """Load one reviewed registry document and require an object root.

    Registry callers depend on named top-level sections.  Accepting a scalar
    or list here would defer a malformed-data failure into unrelated semantic
    resolution code, so the typed boundary rejects that shape immediately.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"semantic registry root must be a JSON object: {path}")
    return payload


@lru_cache(maxsize=1)
def _channel_data() -> dict[str, Any]:
    return _load_json_object(_CHANNEL_FILE)


@lru_cache(maxsize=1)
def _input_data() -> dict[str, Any]:
    return _load_json_object(_INPUT_FILE)


@lru_cache(maxsize=1)
def _coverage_data() -> dict[str, Any]:
    return _load_json_object(_COVERAGE_FILE)


@lru_cache(maxsize=1)
def _attribute_data() -> dict[str, Any]:
    return _load_json_object(_ATTRIBUTE_FILE)


@lru_cache(maxsize=1)
def _not_needed_patterns() -> tuple[tuple[Any, str], ...]:
    return tuple(
        (re.compile(rule["pattern"]), str(rule.get("reason", "")))
        for rule in _attribute_data().get("not_needed_patterns", [])
    )


@lru_cache(maxsize=1)
def _capability_data() -> dict[str, Any]:
    return _load_json_object(_CAPABILITY_FILE)


@lru_cache(maxsize=1)
def registry_version() -> str:
    """Content hash of the reviewed data, not a hand-bumped version string.

    Every artifact records this so a comparison can prove that all three roles
    were mapped by the same reviewed registry.  Editing a data file changes the
    version automatically; a stale artifact then fails the provenance gate
    instead of silently mixing two vocabularies.
    """
    digest = hashlib.sha256()
    for path in (_CHANNEL_FILE, _INPUT_FILE, _COVERAGE_FILE, _ATTRIBUTE_FILE, _CAPABILITY_FILE):
        digest.update(path.read_bytes())
    return f"cept-semantics-{digest.hexdigest()[:16]}"


def _producer_channels(producer: str) -> dict[str, Any]:
    producers = _channel_data().get("producers", {})
    spec = producers.get(producer)
    if spec is None:
        return {}
    inherits = spec.get("inherits")
    if inherits:
        return _producer_channels(inherits)
    channels = spec.get("channels")
    return channels if isinstance(channels, dict) else {}


def vocabulary(producer: str) -> str:
    """The vocabulary a producer speaks, following ``inherits``.

    ``cept-pf`` reads the same PowerFactory variable names as the source lane,
    so an unreviewed channel means the same unproven thing on both and they
    share one namespace.  OpenDSS speaks its own vocabulary and keeps its own.
    """
    spec = _channel_data().get("producers", {}).get(producer)
    if isinstance(spec, dict) and spec.get("inherits"):
        return vocabulary(str(spec["inherits"]))
    return producer


def known_producers() -> tuple[str, ...]:
    return tuple(sorted(_channel_data().get("producers", {})))


def resolve_channel(producer: str, raw_channel: str) -> ChannelSemantics | None:
    """Return the reviewed semantics for a raw channel, or ``None`` if unproven.

    Lookup is exact.  There is deliberately no case folding, punctuation
    stripping, or synonym expansion: those are the heuristics this registry
    replaces, and any one of them can pair two channels that merely look alike.
    """
    if not producer or not raw_channel:
        return None
    entry = _producer_channels(producer).get(raw_channel)
    if not isinstance(entry, dict):
        return None
    return ChannelSemantics(
        producer=producer,
        raw_channel=raw_channel,
        canonical_quantity=str(entry["canonical_quantity"]),
        unit=str(entry.get("unit", "")),
        raw_unit=str(entry.get("raw_unit", "")),
        phase=str(entry.get("phase", "")),
        sign_convention=str(entry.get("sign_convention", "")),
        time_origin_kind=str(entry.get("time_origin_kind", "")),
        reference_frame=str(entry.get("reference_frame", "")),
        note=str(entry.get("note", "")),
    )


def resolve_any(producers: tuple[str, ...], raw_channel: str) -> ChannelSemantics | None:
    """First reviewed hit across an ordered producer preference list."""
    for producer in producers:
        found = resolve_channel(producer, raw_channel)
        if found is not None:
            return found
    return None


def unreviewed_channel(producer: str, raw_channel: str) -> ChannelSemantics:
    """A placeholder that keeps the raw name visible and blocks comparison.

    The canonical quantity is namespaced with the producer's vocabulary so two
    unreviewed channels from different engines can never collide into a
    comparable pair, while the same unproven channel read by two lanes of the
    same engine stays one row.
    """
    return ChannelSemantics(
        producer=producer,
        raw_channel=raw_channel,
        canonical_quantity=f"unreviewed:{vocabulary(producer)}:{raw_channel}",
        unit="",
        raw_unit="",
        phase="unknown",
        sign_convention="unknown",
        time_origin_kind="unknown",
        reference_frame="unknown",
        review_status="unreviewed",
        mapping_method="unreviewed",
    )


def result_channel_descriptor(
    producer: str,
    raw_channel: str,
    display_name: str = "",
    *,
    unit: str = "",
    per_unit_base: str | None = None,
    reference_frame: str | None = None,
    sign_convention: str | None = None,
) -> ChannelSemanticDescriptor:
    """Bind reviewed channel meaning into the typed result artifact.

    Lookup remains exact and reviewed: the raw solver channel is attempted
    first, then the explicit display name used by the adapter.  If neither is
    reviewed, an engine-namespaced unreviewed descriptor is returned so the
    artifact is inspectable without creating false cross-engine comparability.

    Runtime overrides are allowed only for facts the adapter has actually
    established while translating a result, such as a disclosed p.u. base or
    a selected reference-frame contract.  They never change the canonical
    quantity chosen by the reviewed registry.
    """
    found = resolve_channel(producer, raw_channel)
    if found is None and display_name:
        found = resolve_channel(producer, display_name)
    semantics = found or unreviewed_channel(producer, raw_channel or display_name)
    return ChannelSemanticDescriptor(
        canonical_quantity=semantics.canonical_quantity,
        unit=unit or semantics.unit,
        raw_unit=semantics.raw_unit,
        phase=semantics.phase,
        sequence=semantics.sequence,
        terminal=semantics.terminal,
        per_unit_base=semantics.per_unit_base if per_unit_base is None else per_unit_base,
        sign_convention=(
            semantics.sign_convention if sign_convention is None else sign_convention
        ),
        reference_frame=(
            semantics.reference_frame if reference_frame is None else reference_frame
        ),
        time_origin_kind=semantics.time_origin_kind,
        review_status=("reviewed" if semantics.review_status == "reviewed" else "unreviewed"),
        mapping_method=semantics.mapping_method,
    )


# --------------------------------------------------------------------------
# Input completeness
# --------------------------------------------------------------------------


def input_registry() -> dict[str, Any]:
    """Raw reviewed input registry data (required fields per study/class)."""
    return _input_data()


def study_input_spec(study_type: str) -> dict[str, Any]:
    """Reviewed input requirements for one study type; empty when unreviewed."""
    spec = _input_data().get("study_types", {}).get(study_type)
    return spec if isinstance(spec, dict) else {}


def baseline_required_fields() -> list[str]:
    return list(_input_data().get("baseline_required", []))


def event_target_attributes(pf_class: str) -> list[dict[str, str]]:
    """Attributes a reviewed event target class must expose, with reasons."""
    spec = _input_data().get("event_target_classes", {}).get(pf_class)
    if not isinstance(spec, dict):
        return []
    return [dict(item) for item in spec.get("required_attributes", [])]


# --------------------------------------------------------------------------
# Element coverage
# --------------------------------------------------------------------------


def element_disposition(pf_class: str) -> tuple[str, str]:
    """What CEPT does with a PowerFactory class: ``(disposition, reason)``.

    ``represented`` means the typed Case carries it, ``out-of-scope`` means it
    is not a network element CEPT needs, ``blocking`` means it is a real
    element with no reviewed representation, and ``unreviewed`` means nobody
    has decided -- which is treated exactly like ``blocking``, because the
    alternative is a typed Case that silently describes a different network.
    """
    data = _coverage_data()
    if pf_class in data.get("represented", {}):
        return "represented", str(data["represented"][pf_class].get("note", ""))
    if pf_class in data.get("out_of_scope", {}):
        return "out-of-scope", str(data["out_of_scope"][pf_class])
    if pf_class in data.get("blocking", {}):
        return "blocking", str(data["blocking"][pf_class])
    return "unreviewed", "class is not in the reviewed element-coverage registry"


def _has_independent_reference(inventory: dict[str, Any]) -> bool:
    """Does the source designate a voltage reference without the controller?

    A network whose machines are all PV with ``ip_ctrl=0`` and which has no
    external grid has no reference of its own -- the secondary controller
    supplies it.  Calling that controller inert would leave the reconstructed
    Case with no slack at all, which is the opposite of harmless.
    """
    if not isinstance(inventory, dict):
        return False
    if inventory.get("external_grids"):
        return True
    for key in ("sync_generators", "static_generators"):
        for row in inventory.get(key) or []:
            if not isinstance(row, dict):
                continue
            if row.get("ip_ctrl"):
                return True
            if str(row.get("bustp", "")).lower() in {"sl", "slack"}:
                return True
    return False


def _conditional_disposition(
    pf_class: str,
    study_type: str,
    inventory: dict[str, Any] | None,
) -> tuple[str, str] | None:
    """Disposition a class against captured evidence rather than by name.

    Some classes only change a result when a solver option makes them do so.
    A secondary controller does not alter a load flow that balances active
    power at the slack machine, and does alter one that distributes it by area
    control.  Reading the option is what turns "we cannot tell" into an answer;
    a rule with no captured evidence to read stays blocking.
    """
    spec = (_coverage_data().get("conditional", {}) or {}).get(pf_class)
    if not isinstance(spec, dict):
        return None
    rule = spec.get(study_type)
    if not isinstance(rule, dict):
        return None
    condition = rule.get("inert_when")
    if not isinstance(condition, dict):
        return "blocking", str(rule.get("active_reason") or "not represented")
    if inventory is None:
        return "blocking", (
            f"{rule.get('active_reason', 'not represented')} "
            "(no inventory was available to check the condition)"
        )
    requires = rule.get("inert_also_requires")
    if requires == "independent_voltage_reference" and not _has_independent_reference(inventory):
        return "blocking", str(rule.get("active_reason") or "not represented")
    for path, expected in condition.items():
        value: Any = inventory
        for part in str(path).split("."):
            value = (value or {}).get(part) if isinstance(value, dict) else None
        if value != expected:
            return "blocking", str(rule.get("active_reason") or "not represented")
    return "out-of-scope", str(rule.get("inert_reason") or "reviewed as inert for this study")


def coverage_gaps(
    class_census: dict[str, Any],
    study_type: str = "",
    inventory: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Classes present in the source that the typed Case cannot represent.

    Driven by the census the dump takes of the project itself, so a class this
    codebase has never heard of is reported rather than skipped.  An empty list
    means every class the source actually contains is either represented or
    reviewed as out of scope.
    """
    classes = (class_census or {}).get("classes") or {}
    gaps: list[dict[str, Any]] = []
    for pf_class, spec in sorted(classes.items()):
        count = int((spec or {}).get("count", 0) or 0)
        if count <= 0:
            continue
        conditional = _conditional_disposition(pf_class, study_type, inventory)
        if conditional is not None:
            disposition, reason = conditional
        else:
            disposition, reason = element_disposition(pf_class)
        if disposition in {"represented", "out-of-scope"}:
            continue
        gaps.append(
            {
                "pf_class": pf_class,
                "count": count,
                "disposition": disposition,
                "reason": reason,
                "names": list((spec or {}).get("names") or [])[:10],
            }
        )
    return gaps


def element_coverage() -> dict[str, Any]:
    return _coverage_data()


# --------------------------------------------------------------------------
# Attribute coverage
# --------------------------------------------------------------------------


def attribute_disposition(pf_class: str, name: str) -> tuple[str, str]:
    """What a single source attribute means to CEPT: ``(disposition, reason)``.

    ``required`` must be captured, ``optional`` is captured when disclosed,
    ``not-needed`` is reviewed as irrelevant to any study CEPT runs, and
    ``unreviewed`` means nobody has decided.  Unreviewed attributes are
    reported rather than blocking: PowerFactory exposes 150-320 attributes per
    class, so blocking on every one would drown the real findings -- but they
    must stay visible, because that is how a meaningful input that nobody
    thought to collect stops being invisible.
    """
    spec = _attribute_data().get("classes", {}).get(pf_class, {})
    if name in (spec.get("required") or []):
        return "required", "reviewed as required for this class"
    if name in (spec.get("optional") or []):
        return "optional", "reviewed; captured when the source discloses it"
    not_needed = spec.get("not_needed") or {}
    if name in not_needed:
        return "not-needed", str(not_needed[name])
    for pattern, reason in _not_needed_patterns():
        if pattern.search(name):
            return "not-needed", reason
    return "unreviewed", "attribute is not in the reviewed attribute registry"


def attribute_registry() -> dict[str, Any]:
    return _attribute_data()


# --------------------------------------------------------------------------
# Mapping contract
# --------------------------------------------------------------------------

CONTRACT_KINDS = frozenset({"primitive", "derived", "identity", "diagnostic"})
CONTRACT_DISPOSITIONS = frozenset({"case_field", "adapter_only", "diagnostic", "not_represented"})


def attribute_contract(pf_class: str, name: str) -> dict[str, Any]:
    """How a single attribute may be carried: kind/basis/orientation/disposition.

    Four separate defects reached a solver comparison before this existed: a
    conversion run against the network frequency instead of the line type's own
    (``TypLne.frnom``), a write of a value PowerFactory recomputes and discards
    (``ElmShnt.qcapn`` in ``LAY`` mode), a tap placed on the winding the source
    did not name (``TypTr2.tap_side``), and dropped parallel counts.  Each is
    invisible in the built model and looks like a physics disagreement.  The
    contract makes the question mechanical: an adapter asks before it writes.
    """
    data = _attribute_data()
    defaults = dict(data.get("contract_defaults") or {})
    entry = ((data.get("contracts") or {}).get(pf_class) or {}).get(name)
    if entry:
        defaults.update(entry)
    defaults.setdefault("reason", "")
    return defaults


def is_derived_attribute(pf_class: str, name: str) -> bool:
    """True when PowerFactory owns the value and any write is discarded."""
    return attribute_contract(pf_class, name).get("kind") == "derived"


def contract_violations() -> list[str]:
    """Registry self-check.  Empty means the contract file is internally sound.

    This is the lint the plan's WP2 requires.  It does not look at a network;
    it checks that every claim the registry makes is well-formed and refers to
    attributes that exist, so a typo cannot silently disable a guard.
    """
    data = _attribute_data()
    classes = data.get("classes") or {}
    contracts = data.get("contracts") or {}
    problems: list[str] = []

    defaults = data.get("contract_defaults") or {}
    for key in ("kind", "basis_owner", "orientation", "disposition"):
        if key not in defaults:
            problems.append(f"contract_defaults is missing '{key}'")

    for pf_class, entries in sorted(contracts.items()):
        for name, entry in sorted(entries.items()):
            where = f"{pf_class}.{name}"
            missing = [
                key for key in ("kind", "basis_owner", "orientation", "disposition") if key not in entry
            ]
            if missing:
                problems.append(f"{where} contract is missing {sorted(missing)}")
                continue
            if entry["kind"] not in CONTRACT_KINDS:
                problems.append(f"{where} has unknown kind {entry['kind']!r}")
            if entry["disposition"] not in CONTRACT_DISPOSITIONS:
                problems.append(f"{where} has unknown disposition {entry['disposition']!r}")
            if not str(entry.get("reason") or "").strip():
                problems.append(f"{where} declares no reason")
            # A derived value must never be presented as a Case input: that is
            # exactly the write PowerFactory discards.
            if entry["kind"] == "derived" and entry["disposition"] == "case_field":
                problems.append(
                    f"{where} is derived but dispositioned as a Case field; "
                    "carry the primitive input that produces it instead"
                )
            basis = entry.get("basis_owner")
            if basis is not None:
                if "." not in str(basis):
                    problems.append(f"{where} basis_owner {basis!r} is not 'Class.attribute'")
                else:
                    owner_class, owner_attr = str(basis).split(".", 1)
                    spec = classes.get(owner_class)
                    if spec is None:
                        problems.append(f"{where} basis_owner names unknown class {owner_class!r}")
                    elif owner_attr not in set(spec.get("required") or []) | set(spec.get("optional") or []):
                        problems.append(
                            f"{where} basis_owner {basis!r} is not a captured "
                            f"attribute of {owner_class}; a basis nobody reads "
                            "cannot be applied"
                        )
    return problems


def unreviewed_attributes(pf_class: str, present: list[str], captured: list[str]) -> list[str]:
    """Attributes the source exposes that are neither captured nor reviewed."""
    seen = set(captured or [])
    return sorted(
        name
        for name in (present or [])
        if name not in seen and attribute_disposition(pf_class, name)[0] == "unreviewed"
    )


def machine_channels(producer: str) -> list[tuple[str, ChannelSemantics]]:
    """Reviewed channels that describe a synchronous machine's own state.

    Used to give a reconstructed Case a complete monitor list without reading
    the source's results: the registry already says which channels CEPT can
    interpret, and every one of them is worth recording for a machine.
    Terminal/bus quantities are excluded because they attach to a bus, not to
    the machine, and would be requested against the wrong object.
    """
    machine_frames = {
        "machine-own",
        "machine-base",
        "machine-terminal",
        "reference-machine",
        "machine-internal",
        "machine-external",
        "reference-bus-voltage",
    }
    # PowerFactory exposes the same quantity under several prefixes (``s:`` is
    # the machine state, ``m:`` the measured value, ``c:`` a calculated one).
    # Requesting all of them would put two channels on one canonical row and
    # silently drop whichever lost, so exactly one channel per quantity is
    # requested, by a fixed preference.
    priority = {"s:": 0, "m:": 1, "c:": 2}
    best: dict[str, tuple[int, str, ChannelSemantics]] = {}
    for raw_channel in sorted(_producer_channels(producer)):
        semantics = resolve_channel(producer, raw_channel)
        if semantics is None or semantics.reference_frame not in machine_frames:
            continue
        rank = priority.get(raw_channel[:2], 9)
        current = best.get(semantics.canonical_quantity)
        if current is None or rank < current[0]:
            best[semantics.canonical_quantity] = (rank, raw_channel, semantics)
    return [
        (channel, semantics) for _rank, channel, semantics in sorted(best.values(), key=lambda item: item[1])
    ]


# --------------------------------------------------------------------------
# Engine capability
# --------------------------------------------------------------------------


def engine_incapacity(
    engine: str,
    canonical_quantity: str,
    engine_native_name: str = "",
) -> str | None:
    """Why this engine cannot produce this quantity, or ``None`` if it can.

    A source quantity the candidate engine has no state for is not a missing
    counterpart: there is nothing it could have reported.  Saying so keeps it
    out of that engine's parity denominator without pretending the comparison
    happened.  A quantity the engine *can* produce but did not is deliberately
    not covered here -- that stays a blocker, because it is a real gap.
    """
    spec = _capability_data().get("engines", {}).get(_capability_engine(engine))
    if not isinstance(spec, dict):
        return None
    base = str(canonical_quantity or "").split(".", 1)[0]
    for rule in spec.get("asset_rules", []) or []:
        prefix = str(rule.get("when_engine_native_name_starts_with") or "")
        if not prefix or not str(engine_native_name or "").startswith(prefix):
            continue
        kinds = rule.get("cannot_produce_kinds") or []
        if "machine" in kinds and base in set(_capability_data().get("machine_quantities", [])):
            return str(rule.get("reason") or "engine cannot produce machine states for this object")
    cannot = spec.get("cannot_produce") or {}
    if base in cannot:
        return str(cannot[base])
    produces = spec.get("produces")
    if isinstance(produces, list) and produces and base not in set(produces):
        return f"{engine} has no declared output for {base}"
    return None


def _capability_engine(engine: str) -> str:
    key = str(engine or "").lower()
    if "opendss" in key:
        return "opendss"
    if "pf" in key or "powerfactory" in key or key == "source":
        return "powerfactory"
    return key


def engine_capability() -> dict[str, Any]:
    return _capability_data()


__all__ = [
    "ChannelSemantics",
    "attribute_contract",
    "attribute_disposition",
    "attribute_registry",
    "contract_violations",
    "is_derived_attribute",
    "baseline_required_fields",
    "coverage_gaps",
    "element_coverage",
    "engine_capability",
    "engine_incapacity",
    "element_disposition",
    "event_target_attributes",
    "input_registry",
    "known_producers",
    "machine_channels",
    "registry_version",
    "resolve_any",
    "resolve_channel",
    "study_input_spec",
    "unreviewed_attributes",
    "unreviewed_channel",
]
