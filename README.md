# CEPT Public candidate package

This is the public-edition packaging template for CEPT Studio. It is copied
into a generated staging tree by `tools/public_export.py`; it is not itself a
public repository or a release artifact.

The candidate package contains an OpenDSS-first vertical slice for education:

* typed inline Cases and the bundled IEEE 13-node feeder;
* load flow, unbalanced load flow, hosting-capacity, and fault studies;
* deterministic JSON run artifacts and a fail-closed verification receipt;
* the `cept environment check`, `cept capability show`, and `cept study ...`
  noun+verb CLI with shared human-readable `--format text` and machine
  `--format json` output; and
* the eight cold-start notebooks under `public/notebooks/`.

The course has four stages: start, build, apply, and trust. Open each lesson
directly in Google Colab:

| Stage | Notebook | Link |
| --- | --- | --- |
| Start | `00_environment.ipynb` | [Open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/00_environment.ipynb) |
| Start | `01_why_solvers_lie.ipynb` | [Open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/01_why_solvers_lie.ipynb) |
| Build | `02_first_circuit_sld.ipynb` | [Open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/02_first_circuit_sld.ipynb) |
| Build | `03_unbalanced_feeder.ipynb` | [Open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/03_unbalanced_feeder.ipynb) |
| Build | `04_incomplete_data.ipynb` | [Open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/04_incomplete_data.ipynb) |
| Apply | `05_solar_hosting_capacity.ipynb` | [Open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/05_solar_hosting_capacity.ipynb) |
| Apply | `06_fault_study.ipynb` | [Open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/06_fault_study.ipynb) |
| Trust | `07_digital_evidence.ipynb` | [Open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/07_digital_evidence.ipynb) |

Notebook steps use concise icons: **⚙ Setup · 🧩 Inputs · ▶ Run · 📊 Results · ✓ Verify · ◇ Interpret**.

The desktop/CLI teaching path is qualified on Windows, and the notebooks are
also exercised headlessly in a clean installed-product environment. Google
Colab is the hosted Linux target; its real cold-start status is reported
separately from local qualification. Standalone Linux CLI support is not part
of this candidate. The direct Colab links resolve through the public
`sarutesri/cept-studio-edu` repository.

The solver is the actual OpenDSS runtime. CEPT translates the typed Case,
captures the solver-returned result, and records the solver identity. The
candidate's receipt is bounded to `WORKFLOW_VALIDATED`; it is not a claim of
project validation, field-evidence acceptance, or PowerFactory agreement.

The eight notebooks pin the published teaching wheel by URL and SHA-256. For a
local install, download the wheel from
[`v0.2.0-edu.1`](https://github.com/sarutesri/cept-studio-edu/releases/tag/v0.2.0-edu.1),
verify SHA-256
`c7e609a1d9cc85b322bfb615f0c796c7ea5c43815b197289eb555786964478bc`,
install it, and then run:
```text
cept environment check --format text
cept capability show --format text
cept study demo load-flow --network ieee13 --out runs/ieee13 --force --format text
cept study verify runs/ieee13 --format text
```

The wheel intentionally contains no PowerFactory adapter, private project
paths, internal evidence, or private research assets. The release is licensed
under Apache-2.0; dependency and test-feeder attribution is recorded in
`THIRD_PARTY_NOTICES.md`.

This public repository is a reviewed positive-allowlist export. The canonical
`sarutesri/cept-studio` repository remains the development source of truth.
Every export records its canonical source revision and hashes the final
published bytes in `.cept-public-source.json`.
