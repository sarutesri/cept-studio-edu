# CEPT Public candidate notebooks

The course runs in five phases so a learner meets the failure before the
theory. Every notebook keeps solver output separate from teaching text and makes
the evidence boundary visible. The comparison and receipts are workflow
demonstrations, not project validation.

Phase 0 is a setup prologue. Phase 1 opens with the wrong answer a solver
happily returns. Phase 2 builds the first circuit. Phase 3 meets the real,
messy distribution grid. Phase 4 asks the modern grid questions. Phase 5 proves
the number afterwards.

| Phase | Notebook | Topic |
| --- | --- | --- |
| Prologue | [`00_environment.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/00_environment.ipynb) | Runtime, package boundary, and claim scope |
| 1 · The aha | [`01_why_solvers_lie.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/01_why_solvers_lie.ipynb) | Pure OpenDSS converging to 0.316 pu versus CEPT at 0.948 pu |
| 2 · Build it | [`02_first_circuit_sld.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/02_first_circuit_sld.ipynb) | A typed Case and a zero-config single-line diagram |
| 3 · Real network | [`03_unbalanced_feeder.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/03_unbalanced_feeder.ipynb) | IEEE 13-node unbalanced feeder |
| 3 · Real network | [`04_incomplete_data.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/04_incomplete_data.ipynb) | Guided Case-information review, optional OpenCode help, and one bounded solver run |
| 4 · Active grid | [`05_solar_hosting_capacity.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/05_solar_hosting_capacity.ipynb) | PV hosting-capacity search under one declared criterion |
| 4 · Active grid | [`06_fault_study.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/06_fault_study.ipynb) | Single-line-to-ground fault |
| 5 · Trust | [`07_digital_evidence.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/07_digital_evidence.ipynb) | Fingerprints, artifacts, and verification |


Every lesson is terminal-first: run the setup cell, then use the same
`cept <noun> <verb>` commands you would type in a normal terminal. Each lesson
is split into explicit steps — **The Scenario → The Blueprint → The Simulation
→ The Discovery** — with a short `### Step N` explanation before every code
cell. Direct OpenDSS/Python comparison cells are moved to the optional tail so
they do not interrupt the main path.

Lesson 01 opens the course by letting an AI-written OpenDSS command converge to
the wrong 0.316 pu result after omitting the 4.16 kV voltage base, then shows
CEPT returning 0.948 pu from the declared Case with SLD, plot, fingerprint,
artifacts, and a verification receipt.
The solved SLD stays inline, hovering/focusing a bus shows solver-returned phase
values, and the engineering-result cell reads only persisted result fields.
Time-domain results can use the same view layer for persisted solver traces when
those studies are supported. Full run receipts remain inspectable in the run
directory.
Lesson 04 is a small workspace for the same pattern applied to learner-supplied
Case information: paste what is known or missing, choose `strict`, `assisted`,
or `exploratory` resolution, and optionally use an OpenCode assistant. Missing
engineering inputs never become solver values silently. The final demo remains
solver-backed and reports the exact `WORKFLOW_VALIDATED` receipt boundary.

The notebooks should be executed from a fresh installed wheel, not from the
source checkout. The desktop product is Windows/Python 3.10; the intended web
teaching runtime is Google Colab. Colab runs the notebook in a hosted Linux VM,
so the automated runner below is necessary but does not replace the final
real-Colab cold-start review. The repository runner is:

```text
python tools/run_public_notebooks.py --python <fresh-venv-python> --output-dir <external-output>
```

The links above point at the canonical private development source; the export
pipeline rewrites public release links to `sarutesri/cept-studio-edu`. A passed
link or automated notebook run does not by itself record the authenticated
real-Colab acceptance gate.

No notebook embeds a private project path, PowerFactory input, or an invented
engineering value. Demonstrator assumptions must be visible in the Case or the
Case-information resolution ledger.
