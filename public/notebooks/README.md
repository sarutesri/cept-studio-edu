# CEPT Public candidate notebooks

These lessons are deliberately small and solver-visible. Each notebook keeps
solver output separate from teaching text and makes the evidence boundary
visible. The comparison and receipts are workflow demonstrations, not project
validation.

| Notebook | Topic |
| --- | --- |
| [`00_environment.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/00_environment.ipynb) | Runtime, package boundary, and claim scope |
| [`01_first_circuit_load_flow.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/01_first_circuit_load_flow.ipynb) | A two-bus circuit and load-flow comparison |
| [`02_ieee13_unbalanced.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/02_ieee13_unbalanced.ipynb) | IEEE 13-node unbalanced feeder |
| [`03_hosting_capacity.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/03_hosting_capacity.ipynb) | PV hosting-capacity search |
| [`04_fault_study.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/04_fault_study.ipynb) | Single-line-to-ground fault |
| [`05_validation_reproducibility.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/05_validation_reproducibility.ipynb) | Fingerprints, artifacts, and verification |
| [`06_colab_tui.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/06_colab_tui.ipynb) | Guided Case-information review, optional OpenCode help, and one bounded solver run |

Every lesson is designed result-first: run the setup cell, then read the
headline result cards and comparison tables. Supporting code cells stay short
and quiet; full run receipts remain inspectable in the run directory.
Lesson 06 is a small workspace for the same pattern applied to learner-supplied
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
