# CEPT Public candidate notebooks

These lessons are deliberately small and solver-visible. Each notebook uses
the direct OpenDSS API for a reference calculation, runs the same typed Case
through CEPT, and compares only quantities that both sides actually expose.
The comparison is a workflow demonstration, not project validation.

| Notebook | Topic |
| --- | --- |
| [`00_environment.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/00_environment.ipynb) | Runtime, package boundary, and claim scope |
| [`01_first_circuit_load_flow.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/01_first_circuit_load_flow.ipynb) | A two-bus circuit and load-flow comparison |
| [`02_ieee13_unbalanced.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/02_ieee13_unbalanced.ipynb) | IEEE 13-node unbalanced feeder |
| [`03_hosting_capacity.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/03_hosting_capacity.ipynb) | PV hosting-capacity search |
| [`04_fault_study.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/04_fault_study.ipynb) | Single-line-to-ground fault |
| [`05_validation_reproducibility.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/05_validation_reproducibility.ipynb) | Fingerprints, artifacts, and verification |

The notebooks are candidate public teaching assets until the package passes
the staged wheel gate and an owner approves the release license. They should
be executed from a fresh installed wheel, not from the source checkout. The
desktop product is Windows/Python 3.10; the intended web teaching runtime is
Google Colab. Colab runs the notebook in a hosted Linux VM, so the automated
runner below is necessary but does not replace the final real-Colab
cold-start review. The repository runner is:

```text
python tools/run_public_notebooks.py --python <fresh-venv-python> --output-dir <external-output>
```

The links above open the `master`-branch notebooks in Google Colab.
Anonymous Colab access follows the public mirror; while the mirror is still
private, anonymous requests return GitHub `404 Not Found`. These links do not
by themselves record a passed real-Colab gate.

No notebook embeds a private project path, PowerFactory input, or an invented
engineering value. The explicit demonstrator assumptions are visible in the
Case or in the lesson text.
