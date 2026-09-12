# CEPT Public candidate package

This is the public-edition packaging template for CEPT Studio. It is copied
into a generated staging tree by `tools/public_export.py`; it is not itself a
public repository or a release artifact.

The candidate package contains an OpenDSS-first vertical slice for education:

* typed inline Cases and the bundled IEEE 13-node feeder;
* load flow, unbalanced load flow, hosting-capacity, and fault studies;
* deterministic JSON run artifacts and a fail-closed verification receipt;
* the `cept system doctor`, `cept capability show`, and `cept study ...`
  noun+verb CLI; and
* the six cold-start notebooks under `public/notebooks/`.

Open each lesson directly in Google Colab:

| Notebook | Link |
| --- | --- |
| `00_environment.ipynb` | [Open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/master/public/notebooks/00_environment.ipynb) |
| `01_first_circuit_load_flow.ipynb` | [Open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/master/public/notebooks/01_first_circuit_load_flow.ipynb) |
| `02_ieee13_unbalanced.ipynb` | [Open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/master/public/notebooks/02_ieee13_unbalanced.ipynb) |
| `03_hosting_capacity.ipynb` | [Open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/master/public/notebooks/03_hosting_capacity.ipynb) |
| `04_fault_study.ipynb` | [Open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/master/public/notebooks/04_fault_study.ipynb) |
| `05_validation_reproducibility.ipynb` | [Open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/master/public/notebooks/05_validation_reproducibility.ipynb) |

The current support matrix is intentionally narrow: the desktop/CLI preview
is supported on Windows with Python 3.10, while the teaching notebooks target
Google Colab. Colab's hosted runtime is Linux; the real Colab cold-start is a
separate notebook acceptance gate. Standalone Linux CLI support is not part of
this candidate. The direct Colab links require the canonical repository to be
public or the viewer to have authenticated access to it.

The solver is the actual OpenDSS runtime. CEPT translates the typed Case,
captures the solver-returned result, and records the solver identity. The
candidate's receipt is bounded to `WORKFLOW_VALIDATED`; it is not a claim of
project validation, field-evidence acceptance, or PowerFactory agreement.

For a local staged install, use the generated wheel and then run:

```text
cept system doctor
cept capability show
cept study demo load-flow --network ieee13 --out runs/ieee13 --force
cept study verify runs/ieee13
```

The wheel intentionally contains no PowerFactory adapter, private project
paths, internal evidence, or private research assets. Public release remains
blocked until the license is approved, the real Google Colab notebook gate is
completed, and the external public-repository gate is reviewed.

The canonical private repository remains the source of truth. Do not publish
the staging tree until `tools/run_public_gate.py` reports a release-ready
result and an approved public license is recorded in `public-release.yaml`.
