# CEPT Public candidate notebooks

The course uses five formal phases after a setup prologue. Each notebook keeps
solver output separate from teaching text and makes the evidence boundary
visible. The lessons are concise, icon-led, and designed for Colab.

Phase 0 establishes scope. Phase 1 examines model integrity. Phase 2 constructs
a typed network. Phase 3 evaluates network conditions. Phase 4 applies studies.
Phase 5 reviews evidence and reproducibility.

| Phase | Notebook | Topic |
| --- | --- | --- |
| Prologue | [`00_environment.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/00_environment.ipynb) | Environment and study scope |
| 1 · Model integrity | [`01_why_solvers_lie.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/01_why_solvers_lie.ipynb) | Convergence, voltage-base handling, and model assumptions |
| 2 · Model construction | [`02_first_circuit_sld.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/02_first_circuit_sld.ipynb) | Typed Case and single-line diagram |
| 3 · Network conditions | [`03_unbalanced_feeder.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/03_unbalanced_feeder.ipynb) | IEEE 13-node phase results |
| 3 · Network conditions | [`04_incomplete_data.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/04_incomplete_data.ipynb) | Input status and approved resolution policies |
| 4 · Applied studies | [`05_solar_hosting_capacity.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/05_solar_hosting_capacity.ipynb) | Solar hosting capacity under one criterion |
| 4 · Applied studies | [`06_fault_study.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/06_fault_study.ipynb) | Declared short-circuit current |
| 5 · Evidence & reproducibility | [`07_digital_evidence.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/07_digital_evidence.ipynb) | Run identity and artifact integrity |


Every notebook uses the same `cept <noun> <verb>` commands as a terminal.
Step icons support scanning: **⚙ Setup · 🧩 Inputs · ▶ Run · 📊 Results · ✓ Verify · ◇ Interpret**.
Direct OpenDSS/Python comparison cells remain optional; the main path stays
focused on the declared Case and saved evidence.

Lesson 01 compares an omitted downstream voltage base with the declared Case.
The recorded outputs illustrate why convergence alone does not establish model
correctness; the CEPT value is not real-project ground truth.

Lesson 04 separates source data, missing inputs, documented defaults, and
AI-selected assumptions. Required unresolved inputs remain blocked, and the
bundled demonstrator is separate from learner intake.

The notebooks are intended for Google Colab and must be reviewed on a real
cold start before hosted acceptance is claimed. Local execution does not
replace that gate.

No notebook embeds a private project path, PowerFactory input, or invented
engineering value. Demonstrator assumptions remain visible in the Case or the
Case-information resolution ledger.
