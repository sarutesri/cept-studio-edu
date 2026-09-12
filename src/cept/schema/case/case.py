"""The Case root model and its fingerprint/assumption logic.

Extracted from `cept.schema.case` (mechanical move, no behavior change);
re-exported by the package so the old import paths keep working.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from cept.schema.case.models import (
    Assumption,
    CaseExperiment,
    CaseStudy,
    DER,
    DynamicsSpec,
    EMTSpec,
    Experiment,
    HarmonicsSpec,
    Load,
    NetworkSpec,
    QSTSSpec,
    Scenario,
    StandardsSpec,
    StudySpec,
    _collect_inline_assumptions,
)
from cept.schema.case.network import Meta
from cept.schema.provenance import CaseProvenance


def _required_assumption_paths(case: "Case") -> set[str]:
    """Return defaulted input paths that materially affect this run."""
    study = case.study.type
    options = case.study.options
    required_fields = {"frequency_hz"} if case.network.kind == "inline" else set()

    if study in {
        "load_flow",
        "unbalanced_load_flow",
        "fault",
        "hosting_capacity",
        "qsts",
        "harmonics",
        "protection",
    }:
        required_fields |= {"b1_us_per_km", "x_r_ratio", "pf", "pu", "angle_deg", "kw"}
    if study == "unbalanced_load_flow":
        required_fields |= {"r0_ohm_per_km", "x0_ohm_per_km"}
    if study == "fault":
        required_fields.add("xdpp_pu")
        if options.get("type") in {"slg", "llg"}:
            required_fields |= {"r0_ohm_per_km", "x0_ohm_per_km", "sk1_mva"}
    if study == "protection":
        required_fields.add("xdpp_pu")
        if options.get("type") in {"slg", "llg"}:
            required_fields |= {"r0_ohm_per_km", "x0_ohm_per_km", "sk1_mva"}
    if study == "hosting_capacity" and options.get("criterion") in {"thermal", "both"}:
        required_fields.add("normal_amps")
    if study in {"dynamics", "dynamics_rms"}:
        required_fields |= {
            "h",
            "d",
            "xd",
            "xdp",
            "xdpp",
            "mva",
            "mode",
            "pf",
            "control",
            "p_droop",
            "q_droop",
            "conn",
            "start_time",
            "stepsize",
            "duration",
        }

    return {
        assumption.path
        for assumption in case.assumptions
        if assumption.path.rsplit(".", 1)[-1] in required_fields
    }


# --------------------------------------------------------------------------- #
# Top-level Case
# --------------------------------------------------------------------------- #


class Case(BaseModel):
    """Top-level study description — the LLM contract."""

    meta: Meta
    network: NetworkSpec
    study: StudySpec = Field(default_factory=StudySpec)
    engine: Optional[Literal["opendss", "powerfactory"]] = Field(
        None,
        description="Force a specific engine. If unset, the study router picks "
        "one (currently: opendss for 'dss_file'/'builtin' networks; 'inline' "
        "networks default to opendss unless engine='powerfactory' is set).",
    )
    ders: list[DER] = Field(default_factory=list)
    loads: list[Load] = Field(default_factory=list)
    scenarios: list[Scenario] = Field(default_factory=list)
    experiment: Optional[Experiment] = None
    studies: list[CaseStudy] = Field(
        default_factory=list,
        description="Canonical runnable studies for this physical system.",
    )
    experiments: list[CaseExperiment] = Field(
        default_factory=list,
        description="Declared studies on this physical system; legacy study/scenarios remain supported.",
    )
    dynamics: Optional[DynamicsSpec] = None
    qsts: Optional[QSTSSpec] = None
    harmonics: Optional[HarmonicsSpec] = None
    emt: Optional[EMTSpec] = None
    standards: StandardsSpec = Field(default_factory=StandardsSpec)
    assumptions: list[Assumption] = Field(default_factory=list)
    provenance: CaseProvenance | None = Field(
        None,
        description="Immutable source-manifest/ingest receipt binding for research intake.",
    )

    @field_validator("ders")
    @classmethod
    def _unique_der_ids(cls, v: list[DER]) -> list[DER]:
        ids = [d.id for d in v]
        if len(ids) != len(set(ids)):
            raise ValueError("DER ids must be unique")
        return v

    @field_validator("experiments")
    @classmethod
    def _unique_experiment_ids(cls, value: list[CaseExperiment]) -> list[CaseExperiment]:
        ids = [experiment.id for experiment in value]
        if len(ids) != len(set(ids)):
            raise ValueError("experiment ids must be unique")
        return value

    @field_validator("studies")
    @classmethod
    def _unique_study_ids(cls, value: list[CaseStudy]) -> list[CaseStudy]:
        ids = [study.id for study in value]
        if len(ids) != len(set(ids)):
            raise ValueError("study ids must be unique")
        return value

    @model_validator(mode="after")
    def _check_engine_compat(self) -> "Case":
        if self.engine == "powerfactory" and self.network.kind != "inline":
            raise ValueError(
                "engine='powerfactory' currently requires network.kind='inline' "
                "(dss_file/builtin feeders are OpenDSS-only)."
            )
        if self.network.kind == "inline" and self.network.inline is not None:
            machine_base_generators = [
                generator.name
                for generator in self.network.inline.generators
                if generator.mva_basis == "machine_base"
            ]
            if machine_base_generators:
                if self.engine != "powerfactory":
                    raise ValueError(
                        "machine-base inline generators require engine='powerfactory'; "
                        "OpenDSS needs an actual rated apparent power. Affected generators: "
                        + ", ".join(machine_base_generators)
                    )
                if self.study.type != "load_flow":
                    raise ValueError(
                        "machine-base inline generators support only PowerFactory load_flow; "
                        "they are not a rated-machine, fault, or dynamics representation. "
                        "Affected generators: " + ", ".join(machine_base_generators)
                    )
            total_lines = [line.name for line in self.network.inline.lines if line.uses_total_parameters]
            if total_lines:
                if self.engine != "powerfactory":
                    raise ValueError("total-parameter inline branches require engine='powerfactory'")
                if self.study.type not in {"load_flow", "dynamics", "dynamics_rms"}:
                    raise ValueError(
                        "total-parameter inline branches currently support only balanced "
                        "PowerFactory load_flow or RMS dynamics; no zero-sequence/fault "
                        "or thermal representation was supplied."
                    )
        return self

    @model_validator(mode="after")
    def _check_islanding(self) -> "Case":
        src = self.network.source
        if src.kind == "islanded":
            if not src.grid_forming_der:
                raise ValueError(
                    "Islanded study requires network.source.grid_forming_der "
                    "(id of the grid-forming DER acting as swing)."
                )
            if src.grid_forming_der not in {d.id for d in self.ders}:
                raise ValueError(f"grid_forming_der '{src.grid_forming_der}' not found among declared DERs.")
        return self

    @model_validator(mode="after")
    def _check_research_inputs(self) -> "Case":
        merged = {item.path: item for item in self.assumptions}
        for item in _collect_inline_assumptions(self.network):
            merged.setdefault(item.path, item)

        if self.study.type in {"dynamics", "dynamics_rms"}:
            def add_default(path: str, value: object, *, missing: bool = False) -> None:
                merged.setdefault(
                    path,
                    Assumption(
                        path=path,
                        value=value,
                        source="missing" if missing else "demonstrator-default",
                    ),
                )

            if self.dynamics is not None:
                for field in ("start_time", "stepsize", "duration"):
                    if field not in self.dynamics.model_fields_set:
                        add_default(f"dynamics.{field}", getattr(self.dynamics, field))

            for i, der in enumerate(self.ders):
                if der.machine is not None:
                    base = f"ders[{i}].machine"
                    for field in ("h", "d", "xd", "xdp", "xdpp"):
                        if field not in der.machine.model_fields_set:
                            add_default(f"{base}.{field}", getattr(der.machine, field))
                    if "mva" not in der.machine.model_fields_set:
                        add_default(f"{base}.mva", None, missing=True)

                if der.indmach is not None:
                    base = f"ders[{i}].indmach"
                    for field in ("h", "d", "conn"):
                        if field not in der.indmach.model_fields_set:
                            add_default(f"{base}.{field}", getattr(der.indmach, field))

                if der.inverter is not None:
                    base = f"ders[{i}].inverter"
                    for field in ("mode", "pf", "control"):
                        if field not in der.inverter.model_fields_set:
                            add_default(f"{base}.{field}", getattr(der.inverter, field))
                    if der.inverter.mode == "grid_forming" or der.inverter.control == "grid_forming":
                        for field in ("p_droop", "q_droop"):
                            if field not in der.inverter.model_fields_set or getattr(der.inverter, field) is None:
                                add_default(f"{base}.{field}", None, missing=True)

        self.assumptions = list(merged.values())

        if self.meta.mode == "research":
            required = sorted(_required_assumption_paths(self))
            if required:
                raise ValueError(
                    "research mode requires explicit engineering inputs: "
                    + ", ".join(required)
                    + ". Provide them explicitly or set meta.mode='demonstrator'."
                )
        return self

    @model_validator(mode="after")
    def _check_qsts_contract(self) -> "Case":
        if self.study.type != "qsts":
            return self
        if self.qsts is None:
            raise ValueError("study.type='qsts' requires a qsts specification")
        n_steps = round(self.qsts.duration_s / self.qsts.stepsize_s)
        if n_steps < 1 or abs(n_steps * self.qsts.stepsize_s - self.qsts.duration_s) > 1e-9:
            raise ValueError("qsts duration_s must be an integer number of stepsize_s intervals")
        names = [p.name for p in self.qsts.profiles]
        if len(names) != len(set(names)):
            raise ValueError("qsts profile names must be unique")
        for profile in self.qsts.profiles:
            if abs(profile.interval_s - self.qsts.stepsize_s) > 1e-9:
                raise ValueError(f"qsts profile '{profile.name}' interval_s must equal qsts stepsize_s")
            if len(profile.values) < n_steps:
                raise ValueError(
                    f"qsts profile '{profile.name}' has {len(profile.values)} values; "
                    f"{n_steps} are required for the requested duration"
                )
        return self

    @model_validator(mode="after")
    def _check_harmonics_contract(self) -> "Case":
        if self.study.type == "harmonics" and self.harmonics is None:
            raise ValueError("study.type='harmonics' requires a harmonics specification")
        return self

    @model_validator(mode="after")
    def _check_emt_contract(self) -> "Case":
        if self.study.type != "emt":
            return self
        if self.emt is None:
            raise ValueError("study.type='emt' requires an emt specification")
        if self.engine is not None and self.engine != self.emt.engine:
            raise ValueError(
                f"Case.engine='{self.engine}' conflicts with emt.engine='{self.emt.engine}'; "
                "set both to the same engine or leave Case.engine unset so the EMT "
                "dispatcher does not silently switch engines."
            )
        return self

    def fingerprint(self) -> str:
        """Stable hash of the case for reproducibility in reports.

        The dump is idempotent (a full round-trip reproduces itself), and every
        additive optional field carries a serializer that omits it when unset
        (StudySpec.intent, InlineBus.powerfactory_phase_technology,
        InlineNetwork.*_tolerance), so adding such a field never moves an
        existing study's fingerprint (T-008).
        """
        data = self.model_dump(mode="json")
        selected = self.provenance.selected_study_case if self.provenance else None
        if selected and self.study.type == "fault" and "method c" in selected.name.lower():
            # Historical compatibility: the old post-validation hook mutated the
            # primary StudySpec through its typed FaultOptions round-trip.  Rebuild
            # that same serialized shape for identity only; do not mutate this Case.
            legacy = self.model_copy(deep=True)
            legacy.study.options["powerfactory_method"] = "method_c"
            for entry in legacy.studies:
                if entry.study.type == "fault" and entry.study.options.get("powerfactory_method") is None:
                    entry.study.options["powerfactory_method"] = "method_c"
            data = legacy.model_dump(mode="json")
        payload = json.dumps(data, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:12]
